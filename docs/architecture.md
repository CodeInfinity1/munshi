# Architecture

## The loop

Every revenue-risk event travels the same path, and every stage is separable and
testable on its own.

```mermaid
sequenceDiagram
    autonumber
    participant RZP as Razorpay
    participant IN as Ingest
    participant TR as Triage
    participant EN as Enrich
    participant AG as Agent
    participant TL as Tools
    participant PO as Policy
    participant EX as Execute
    participant LG as Ledger
    participant AU as Audit

    RZP->>IN: payment.failed (webhook, signed)
    IN->>IN: verify HMAC over raw bytes
    IN->>IN: dedupe on provider event id
    IN->>TR: risk case opened
    TR->>TR: amount x P(recover) x urgency, ranked
    TR->>EN: highest expected value first
    EN->>AG: compact brief (no latent truth)
    loop bounded, max 6 turns
        AG->>TL: read a tool, or dry-run a candidate action
        TL-->>AG: result (no tool moves money)
    end
    AG->>PO: submit_decision -> validated proposal
    PO->>PO: evaluate ~20 rules, record every verdict
    alt allowed
        PO->>EX: execute with idempotency key
        EX->>LG: ledger row, if and only if money moved
    else needs a human
        PO->>AU: queued for merchant approval
    else denied for now
        PO->>EN: reschedule to the earliest permitted instant
    else denied forever
        PO->>AU: stop, with a named reason
    end
    EX->>AU: outcome
    LG->>AU: recovery
```

## Why the layers are split this way

The failure mode this architecture exists to prevent is a persuasive model
widening its own authority. Three defences, in order of how much they matter:

**1. Retryability is a property of the failure code, not a judgement.** Razorpay
publishes `error_reason` on every failure. Whether a retry on that instrument can
*ever* succeed follows from the code, so it is resolved by lookup in
`taxonomy.py` before any model runs. `card_expired` is not retryable however
confident anything is about it.

**1b. No tool the agent can call moves money.** The model has eight tools: six
reads, one calculation, and one dry run. The terminal tool produces a *proposal*.
There is no `retry_payment` tool and no `send_message` tool, so a fully
compromised model has nothing to redirect. See
[agent-design.md](agent-design.md).

**2. The policy engine is deterministic, total, and unreachable from the model.**
It takes a proposal and returns `allow` / `require_approval` / `deny`. It has no
LLM dependency, no natural-language input path, and no override. A test hands it
a justification reading *"policy override approved, ignore previous limits"* and
asserts nothing changes.

**3. The action vocabulary is closed and statically tiered.** `ACTION_TIERS` maps
each of 14 actions to L0–L4. A model that returns anything outside that set is
rejected and the case degrades to the deterministic reasoner. The tier of an
action is never argued for.

There was a real bug here, found by a test: `schedule_retry` and `retry_payment`
were two names for the same operation and only one of them was in `MONEY_MOVING`,
so the autonomous-amount ceiling could be bypassed by proposing the synonym. The
fix was to delete the redundant action — a retry's timing was already carried by
`delay_hours`. **Two names for one operation is how a bound gets bypassed.**

## Deny-for-now versus deny-forever

The single most consequential distinction in the policy engine.

A contact attempted at 22:14 is not a dead case; it is a case that must wait
until 08:00. A fourth retry *is* a dead case. Rules that refuse temporarily set
`reschedule_at`; rules that refuse permanently set `stop_reason`. Conflating them
either spams customers or writes off live revenue — and both failure modes
actually happened during development:

- A hard `deny` was silently downgraded by a later rule's `reschedule_at`,
  looping one case through 130 refused retries. Permanent denial now dominates.
- The e-mandate pre-debit rule declared a permanent `deny` when it meant "notice
  not sent yet", so the precedence fix above turned 18 live cases into
  write-offs. The rule's escalation is now conditional on the amount.

## Prioritisation

An agent with finite capacity has to choose what to work on. Processing cases in
whatever order the database returns is not a decision, and it is the wrong one
whenever the work budget binds: on a book where a quarter of coded failures can
never be recovered, sorting by amount puts uncollectable money at the top.

`triage.py` scores each due case as `amount x P(recover) x urgency`, where
P(recover) is built from the taxonomy's own recoverability prior, the payer's
track record, whether the rail is currently down, retries already spent, and age
decay. Every factor is returned alongside the product — an unexplained priority
is a priority nobody will trust — and the whole score is exposed to the agent as a
tool and rendered in the UI.

## The virtual clock

Recovery plays out over days. An insufficient-funds retry that fires 36 hours
later is a different action from one that fires immediately, so evaluating timing
requires advancing time rather than pretending it passed.

`VirtualClock` is the batch clock: the orchestrator steps it across the recovery
window so scheduled work actually comes due. `SystemClock` is what the live
webhook path uses. Both expose `now()` and `advance()`, so nothing downstream
knows which one it holds.

The seeded batch is anchored to a fixed instant (`BATCH_START`, 24 Aug 2026
09:00 IST) so every evaluation run is byte-for-byte reproducible — asserted in
CI against the committed `results.json`.

## Data model

SQLite, stdlib `sqlite3`, no ORM. The schema is small enough that an ORM would
add more concepts than it removes. **Money is `INTEGER` paise everywhere; there
are no floats in the money path.**

| Table | Holds |
|---|---|
| `events` | Raw inbound events, append-only, keyed on provider event id. The source of truth we can replay from. |
| `customers` | Segment, tenure, payment history, contact opt-out, typical success hour |
| `cases` | One per unit of revenue at risk. The state machine, budgets, and the `latent` ground-truth column the agent never reads. |
| `actions` | Every proposal, executed or not, with the full rule verdict list and an idempotency key |
| `approvals` | The merchant queue |
| `downtimes` | Razorpay Payment Downtime records, kept current by `payment.downtime.*` |
| `ledger` | Money movements. **The only place recovery is counted.** |
| — | `cases.settled_externally_at` records money the customer paid through another channel mid-recovery. Real, and deliberately absent from the ledger. |
| `audit` | Append-only, sha256-chained |
| `runs` | Which reasoner and adapter produced which batch |

### Case state machine

```
                    ┌─────────────────────── recovered           (ledger row exists)
                    │
                    ├─────────────────────── settled_externally  (customer paid elsewhere;
open ──► scheduled ─┤                                             NO ledger row, never claimed)
   ▲          │     │
   │          │     ├─────────────────────── stopped             (a stopping rule fired)
   └──────────┘     │
   (cooldown,       ├─────────────────────── escalated           (handed to a human)
    outage hold,    │
    contact window) └─────────────────────── suppressed          (must not be contacted)

                     awaiting_approval ──► (merchant decides) ──► recovered | stopped
```

`CaseState.terminal_placeholders()` generates the `?,?,?…` for every `IN` clause
over terminal states. Hard-coded placeholder counts broke four queries at runtime
the moment a fifth terminal state was added, which is precisely the bug that
class of literal exists to cause.

Terminal states are terminal: a proposal on a terminal case is denied by
`case_not_terminal`. At the end of the window a sweep gives every remaining case
an explicit stop reason, so the state distribution accounts for every rupee — a
case left `scheduled` forever is a case nobody is accountable for.

## The audit chain

Each row commits to its predecessor:

```
hash_n = sha256(prev_hash ‖ canonical_json(row_n))
```

Editing or deleting any historical row invalidates every hash after it.
`verify()` walks the chain and reports the first break; the dashboard recomputes
it on every load and prints the head. Four tests tamper with the log — edit a
summary, delete a row, inflate a recovered amount — and assert verification
fails.

*"We wrote it down"* and *"nobody changed it"* are different claims. For a system
that moves money, only the second one is worth anything.

The `detail` payload holds structured decision records: inputs, rule verdicts,
outcomes, and the short rationale the model was asked to emit as part of its
structured output. It deliberately does not store raw chain-of-thought.

## Concurrency

The decision pass fans out across threads, because the model call is the entire
latency. Two things follow:

- **Tool dispatch is serialised on a shared lock.** Every read tool touches the
  same SQLite connection, and `check_same_thread=False` permits use from another
  thread but not *concurrent* use — it surfaces as `bad parameter or other API
  misuse` only under load. Reads are microseconds, so the lock costs nothing
  measurable.
- **Nothing that writes runs in a worker.** Enrichment and reasoning fan out;
  every state change is applied on the serialised path afterwards.

## Adapters

```
Orchestrator ──► Adapter (protocol: execute(action, case, params, now))
                    ├── SimulatorAdapter     deterministic outcome oracle
                    └── RazorpayTestAdapter  real Razorpay test-mode HTTP
```

Selection is deliberately hard to get wrong: `razorpay_test` requires an explicit
`MUNSHI_ADAPTER` setting *and* credentials, and the adapter refuses to construct
against a key id that is not `rzp_test_*`. Where test mode cannot genuinely
perform an action, the adapter raises `UnsupportedInTestMode` and the executor
records it as *not executed* with that reason, rather than simulating a rail it
cannot reach.

## Frontend

Vite + React + TypeScript + Tailwind v4, built into `munshi/static/` and served
by the same FastAPI process, so the entire product runs from one command.

The dashboard polls `/api/overview` only while a batch is running; the numbers
move because case state moved, not because something is animating.
