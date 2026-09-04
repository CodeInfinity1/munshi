# Buildathon submission

**Razorpay AI Buildathon 2026 · Track 03: AI Revenue Recovery**

---

## 1 · Project name

**Munshi** — a *munshi* is the clerk who keeps the ledger and knows which dues are
actually collectable. That is exactly the product: not a tool that chases
everything, but one that knows what is worth chasing, what it is allowed to do
about it, and when to stop.

Tagline: *Bounded revenue recovery for Razorpay merchants.*

## 2 · Project objectives — what it solves

**The problem.** Merchants don't lose revenue because they can't see that a
payment failed — Razorpay tells them in seconds. They lose it because nobody
closes the loop, and because the loop most of them run is a fixed retry ladder
that treats every failure identically.

That ladder is wrong in both directions at once. It spends attempts on failures
that can never succeed — an expired card, a revoked mandate, a malformed request,
a risk decline — and it chases customers it should not be touching, including ones
who have already paid. **27.7% of coded payment failures are structurally
unretryable**, and on the demo batch the ladder burned 34.7% of its retries on
exactly that.

**What Munshi does.** It closes the loop end to end, one at-risk rupee at a time:
detect → quantify → prioritise → investigate → decide → policy → execute →
verify → stop, with the money either recovered, put in front of a human, handed
off, or deliberately released — and a reason recorded for each.

The signal that makes it work is one Razorpay already emits and almost nobody uses
as a routing input. Every failed payment carries an `error_source`
(customer / business / gateway / razorpay) and an `error_reason` from a closed
vocabulary, and Razorpay documents who has to act on each. Munshi encodes 65 of
those codes into ten recovery families with explicit retryability, and correlates
each failure against Razorpay's live **Payment Downtime** feed for that exact
instrument — so "retry in six hours" becomes a decision instead of a default.

**It is a tool-using agent, not a prompt.** The model gets a compact brief and
eight tools, and decides for itself what else to look at: the payer's other cases,
the downtime feed, what has already been tried, the deterministic recovery score,
or a dry run of a candidate action against the real policy engine. It ends by
calling `submit_decision`.

**What makes that safe enough to run.** There is no `retry_payment` tool, no
`create_payment_link`, no `send_message`. Every tool is a read, a calculation or a
dry run; the only way the model affects anything is by *proposing* an action that
a deterministic policy engine and an executor then handle. A fully compromised
model cannot execute a payment. Autonomy tiers L0–L4 are a static table;
`write_off` is L4, because no tier makes a tax-relevant accounting decision an
agent's call. And the RBI Fair Practices contact window, the RBI e-mandate
framework's pre-debit notice and AFA ceiling, and NPCI's non-peak debit windows
are all hard bounds the reasoning layer cannot argue past.

**Measured, on a 320-case / ₹1.85Cr batch** against a fixed retry ladder over
identical cases with identical hidden ground truth and identical per-case seeds:

| | Fixed ladder | Munshi + approvals |
|---|---:|---:|
| Recovered | ₹72,07,487 | ₹61,92,376 |
| Retries with zero possible yield | **153 (34.7%)** | **0** |
| Customers chased after paying | **15** | **0** |
| Opted-out customers contacted | **27** | **0** |
| RBI / NPCI window violations | **223** | **0** |
| Intervention accuracy | 67.8% | **89.9%** |
| Diagnosis accuracy | 0% | **87.3%** |

The ladder wins on gross revenue by ₹10.15L — 14% — and that is reported rather
than tuned away. It buys those rupees with 153 impossible retries, 15 messages to
people who had already paid, and 223 regulatory breaches. Munshi collects 86% of
the gross with 42% fewer retries and none of that, and puts ₹63L in front of the
merchant with a reason attached rather than moving it alone.

A further **₹17,39,778 was paid by customers through other channels
mid-recovery.** Real money, and Munshi claims none of it — no ledger row, its own
column.

Every rupee counted as recovered has a ledger row pointing at the action that
caused it. Every decision, every tool call and every policy verdict is in an
append-only, sha256-chained audit trail.

## 3 · GitHub repository

**https://github.com/CodeInfinity1/munshi** — public.

## 4 · Five-minute pitch video

**`VIDEO_LINK_PLACEHOLDER`**

> ⚠️ **This is the one field still to fill in.** Record the run using
> [demo-script.md](demo-script.md), upload to YouTube as **Unlisted**, then
> replace the placeholder above *and* paste the same link into the submission
> form. Nothing else in this repository is a placeholder.

## 5 · Build challenges and technical obstacles

Eight problems that were genuinely hard, and what actually broke.

### Turning noisy payment events into a state machine that always terminates

A failed payment is an event; revenue at risk is a *state* with a budget and a
clock. The hard part was termination. Early runs left cases parked in `scheduled`
indefinitely — rupees nobody was accountable for. Fixed with an explicit
end-of-window sweep that gives every remaining case a named stop reason, plus a
test asserting no case is left non-terminal after any batch.

### Deny-for-now versus deny-forever

The most consequential distinction in the policy engine, and the source of two
real bugs.

A contact attempted at 22:14 is not a dead case — it must wait until 08:00. A
fourth retry *is* a dead case. Conflating them either spams customers or writes
off live revenue, and both happened:

1. A hard `deny` was silently downgraded by a later rule's `reschedule_at`,
   looping one case through **130 refused retries** across 130 ticks. Fixed by
   making a permanent denial dominate a temporary one.
2. That fix immediately broke something else: the e-mandate pre-debit rule
   declared a permanent `deny` when it meant *"the notice hasn't been sent yet"* —
   so the new precedence turned **18 live cases into write-offs**. Its escalation
   is now conditional on the amount.

The same distinction later had to be taught to the *agent*: a `deny` carrying
`would_reschedule_to_hours` means wait, not give up, and a reasoner that treated
it as terminal abandoned cases that were only on cooldown.

### A bound that a synonym could sidestep

`schedule_retry` and `retry_payment` were two names for one operation, and only
one of them was in the `MONEY_MOVING` set — so the autonomous-amount ceiling could
be bypassed entirely by proposing the synonym. A safety test caught it. The fix
was to *delete* the redundant action: a retry's timing was already carried by
`delay_hours`. **Two names for one operation is how a bound gets bypassed.**

### Making the agent an agent without making it dangerous

The first reasoner was `context → one LLM call → JSON`. That is a prompt: it had
no say in what it saw. Turning it into a tool loop raised the obvious question of
what a model should be allowed to call.

The answer was that **no tool may move money or reach a customer.** Eight tools,
all reads, calculations or dry runs; the terminal one produces a *proposal*. The
subtle piece was `check_policy` — genuinely useful, because the agent can see the
failing rule and act on it, but it must not become a bypass. It runs the real
engine with `dry_run=True` so exposure is never consumed, and the same engine
re-checks whatever is finally submitted. A test calls it five times and asserts
the exposure counter has not moved.

### The concurrency bug that only a real agent could have

Giving the model tools that read the database broke a single-threaded assumption
the moment reasoning fanned out across threads. `check_same_thread=False` permits
use from *another* thread; it does not permit *concurrent* use, and it surfaces as
`bad parameter or other API misuse` only under load — never in a unit test.

The related bug was subtler: `MockProvider` tracked its turn index on the
instance, but one provider is shared across concurrently-decided cases, so their
turns interleaved and later cases silently got a truncated loop. Most decisions
were running one turn instead of three, and nothing failed. Deriving the index
from the conversation fixed it.

### Measuring recovery in a way that survives being questioned

The hardest design problem here. "We recovered ₹X" is worthless if the thing
grading the outcome can see what the agent decided.

The answer was latent ground truth: every case carries a hidden record — was this
ever recoverable, when does the payer's balance really top up, would the customer
really replace a dead card, when does the outage really clear. The agent never
reads it (asserted by tests that grep the context pack, the agent's brief *and*
the API response for every latent field name). The oracle resolves actions against
that truth and the timing, and is never shown the reasoning. Luck is seeded per
`(case, action, attempt)` so both arms draw identical luck on identical cases.

The oracle's probability table is stated in the source and is deliberately
*generous* to retries once the precondition is met — which flatters the baseline,
not us.

### Not claiming money we did not recover

The adversarial case that mattered most: a customer paying through another channel
while Munshi is mid-workflow. ₹17.4L of the batch settles this way — 66% of the
unattended recovered total. Folding it in would have been extremely tempting and
completely wrong.

It gets no ledger row, lands in its own terminal state, and is reported in its own
column. And because chasing someone who paid an hour ago is the same false
positive as chasing someone Razorpay already flagged — just harder to notice — the
policy engine's `not_already_settled` rule covers both. The ladder contacted 15
customers after they had paid; Munshi contacted none.

### Getting the agent to stop being wrong in ways that cost real money

Four product bugs the batch found that reading the code did not:

- **`merchant_config` was classified as never-retryable.** It is unretryable
  *until the merchant enables the method*, which is a different thing. The wrong
  label wrote off ₹13L of collectable revenue.
- **Alerting merchant ops was terminal**, abandoning the case before the human
  could act on the alert. Now: alert, keep the case open, probe once it is fixed.
- **Exhausting the contact budget closed the whole case** even when retries
  remained. A budget bounds an *avenue*, not a case.
- **Backoffs were measured from now rather than from the failure**, so a six-day-
  old insufficient-funds case waited another 24 hours for a precondition satisfied
  five days earlier.

### An unscheduled outage that never ends

Razorpay's downtime entity has no published `end` until resolution is announced.
The first implementation therefore treated a live outage as permanent, and cases
waited on it forever: **3,024 of 3,369 reschedules were one outage.** Resolution
is now published the way Razorpay does it, off ground truth the agent cannot read,
and the wait is capped at three holds — after which the agent stops waiting on a
broken rail and offers the customer a working one. Decisions per batch fell from
4,397 to ~1,500.

---

## What I would build next

1. **Replace the outcome oracle with observed data.** Everything else stays; the
   probability table is the one component that should come from a merchant's own
   history rather than from documented resolution conditions.
2. **Learn the timing.** `funds_available_after_h` is currently a prior. Per-payer
   settlement-hour and salary-cycle inference is the highest-value model in the
   system and is not built.
3. **Multi-tenancy and real approval identity.**
4. **The reply loop.** Promise-to-pay is detected from a simulated reply; parsing
   real inbound WhatsApp and email is the obvious next agentic surface.

## Repository checklist

- 141 tests, including an adversarial agent suite and a policy-safety suite
  written specifically to catch dangerous autonomous behaviour
- CI runs lint, tests, and re-runs the evaluation asserting it reproduces the
  committed figures exactly
- A docs-consistency test that fails if any rupee figure in the prose drifts from
  the committed evaluation — it has caught a 10× error and an invented number
- `make demo` builds, seeds and serves in one command, with no credentials
- No secrets in the repository or its history
