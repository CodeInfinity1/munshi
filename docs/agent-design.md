# Agent design

## What makes this an agent

The word is only doing work if the system *decides what to look at*, decides what
to do, acts under constraint, and can be held to account for all three. An
earlier version of this reasoner was `context → one LLM call → JSON`. That is a
prompt: it had no say in what it saw, so its only contribution was the mapping.

Munshi's loop is `detect → quantify → prioritise → enrich → investigate →
decide → policy → execute → verify → stop → measure`, run over a virtual clock so
timing decisions are real. A model appears at exactly one stage of it, and inside
that stage it runs a bounded tool loop.

## The tool surface

Eight tools. **The safety argument rests on what is not among them.**

| Tool | Kind | |
|---|---|---|
| `get_customer_context` | read | Segment, tenure, lifetime value, prior successes and failures, opt-out, the local hour this payer has historically settled at |
| `get_payment_history` | read | The payer's other cases and how they ended |
| `get_failure_semantics` | read | Razorpay's documented position on *any* reason code, including ones not in the brief |
| `get_downtime_status` | read | The live Payment Downtime feed for this exact instrument, and how many times we have already held |
| `get_recovery_history` | read | What was attempted, what policy said each time, what budget is left |
| `calculate_recovery_score` | compute | Deterministic expected recoverable value, decomposed |
| `check_policy` | dry run | What the real policy engine would say about a candidate action |
| `submit_decision` | terminal | A **proposal** |

There is no `retry_payment` tool. No `create_payment_link`. No `send_message`. No
`write_off`. Every tool is a read, a calculation, or a dry run. The only way the
model affects anything is `submit_decision`, and what that produces is a proposal
that the deterministic policy engine and the executor then handle — neither of
which trusts it.

**A fully compromised model cannot execute a payment.** That is asserted by a
test, not argued.

### `check_policy` is a dry run, not a bypass

The agent can ask what the engine would say before committing. It sees the
verdict, the failing rules, and each rule's own explanation. It consumes
nothing — `dry_run=True` skips exposure accounting, asserted by a test that calls
it five times and checks the counter — and the same engine re-evaluates whatever
is finally submitted.

**Consulting policy costs nothing and grants nothing.** What it buys is that the
agent can act on the *reason*: an `emandate_pre_debit_notice` failure means send
the notification, not keep proposing the debit.

One semantic matters here and is taught explicitly, in the system prompt and on
the tool's own description: a `deny` carrying `would_reschedule_to_hours` is
**temporary** — the case is simply not due yet, and proposing the action anyway is
correct because the engine will schedule it. Only a `deny` with a `stop_reason`
and no reschedule is permanent. Treating the first as the second writes off
revenue that was only waiting on a cooldown.

## The brief is deliberately small

If the opening brief contained everything, the tools would be decorative. It
carries the case, the failure's documented semantics, the compliance state, and
the remaining budgets. The payer's history, the downtime feed, prior attempts,
the recovery score and the policy dry-run are all fetched only when the case
warrants it. A test asserts the brief is strictly smaller than the full context
pack and contains none of the keys the tools return.

## Where the model's judgement actually matters

1. **Ambiguous failure codes.** Razorpay documents `payment_failed` as *"no
   specific error code received from gateway"*; `card_declined` and
   `payment_declined` are similarly opaque. Whether a given decline is an outage,
   a balance problem or a dying instrument is a weighing of downtime state, payer
   history, amount and age — which is exactly what the read tools are for.
2. **Which intervention, and when.** For balance failures the whole game is
   timing. `delay_hours` is measured from now, but preconditions are measured
   from when the failure happened, so the agent is told to check `case.age_hours`
   before waiting.
3. **Whether this is worth chasing at all.** `no_action` on a low-recoverability
   case is a correct and valuable answer, and the prompt says so.
4. **The customer-facing message**, in register, citing the real reason and
   amount.

## What it is not allowed to touch

| Decision | Where it lives |
|---|---|
| Can a retry ever succeed? | `taxonomy.py` — a property of the failure code |
| Which case is worked first? | `triage.py` — deterministic expected value |
| How many retries and messages are left? | `policy.py` — counters on the case |
| Is contact permitted right now? | `compliance.py` — RBI FPC clock arithmetic |
| How much money is at risk? | `quantify.py` — integer arithmetic on paise |
| What tier is this action? | `models.ACTION_TIERS` — a static table |
| Did money move? | `ledger` — a row, or it did not happen |

## Treating the model as an untrusted input

A schema-shaped response is still an untrusted response.

- `action_type` must be in `ACTION_TIERS`; `root_cause` in `ROOT_CAUSES`.
  Anything else raises and the case degrades.
- `confidence` and `recoverability` are clamped to `[0, 1]`; `delay_hours` to
  `[0, 336]`. Clamping is not a failure and is not counted as one.
- An unrecognised `channel` becomes `none`.
- An unknown tool, or a tool called with bad arguments, is answered with an error
  *as a tool result* rather than raised — a bad call is a turn to learn from, not
  a crash.

## Every failure degrades

| Failure | Handling |
|---|---|
| No credential | `LLMUnavailable` at construction; the deterministic reasoner runs and the header says so |
| Rate limit (429) | Exactly one bounded retry, then degrade. Retrying a malformed response or a rejected credential only spends money. |
| Timeout / connection | Degrade |
| Malformed tool arguments | Surfaced as `LLMMalformed`, never guessed at; degrade |
| Invented action or root cause | Rejected by validation; degrade |
| Model answers in prose instead of calling the tool | Salvage path — accepts only something already shaped like a decision, never invents a field. `gpt-oss-120b` does this intermittently. |
| Model will not decide | Nudged exactly once, then abandoned |
| Turn cap reached | Degrade |

Every degradation is counted, categorised, recorded on the case with its reason,
and surfaced in the run summary and the activity stream. **An LLM failure cannot
corrupt financial state because it never touches it.**

## The deterministic twin

`HeuristicReasoner` is a complete implementation of the same interface, driven off
the taxonomy. It exists for three reasons:

1. **The demo runs with no credential**, and the header says which reasoner
   produced the numbers.
2. **It is the honest control.** Run as its own evaluation arm, it answers "is the
   model earning its cost, or is the taxonomy doing all the work?" with a number.
   **Every figure committed in this repository is from the deterministic arm** —
   the weaker claim, deliberately.
3. **It bounds the blast radius of a bad model day.** Degradation is a designed
   path, not an error path.

## Prompt-injection posture

Customer-controlled text (names, VPAs, entity ids) reaches the agent's context,
and tool results contain more of it. The defence is not prompt hygiene — it is
that a successful injection buys nothing:

- The terminal tool's schema is a closed enum. There is no free-text action.
- Tiers come from a static table, not from the response.
- Retryability, budgets, windows and ceilings are all applied after the model
  returns.
- No tool can move money, so there is nothing to redirect.
- The per-run exposure circuit breaker caps distinct value in flight regardless of
  how many actions are proposed.

At worst, an attacker in full control of the model gets a differently-timed
*legitimate* action within the merchant's own limits — and every tool call and
every verdict is in the hash-chained audit trail.

## Cost shape

One loop per decision, not per tick. The dominant control is *not waking cases
that cannot act*: after a failed action the case is scheduled to the earliest
instant policy could permit another, rather than a flat interval. That change
alone cut a 320-case batch from 4,397 decisions to ~1,500.

`GROQ_REASONING_EFFORT` defaults to `medium`; `MUNSHI_AGENT_MAX_TURNS` caps the
loop at 6; the decision pass fans out across `MUNSHI_LLM_CONCURRENCY` threads.
Tool results are truncated before re-entering context — a 25-case payment history
is useful, the same history in full is context pressure.
