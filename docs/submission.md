# Buildathon submission

**Razorpay AI Buildathon 2026 · Track 03: AI Revenue Recovery**

---

## 1 · Project name

**Munshi** — a *munshi* is the clerk who keeps the ledger and knows which dues
are actually collectable. That is exactly the product: not a tool that chases
everything, but one that knows what is worth chasing.

Tagline: *Bounded revenue recovery for Razorpay merchants.*

## 2 · Project objectives — what it solves

**The problem.** Merchants don't lose revenue because they can't see that a
payment failed. They lose it because nobody closes the loop, and because the loop
most of them run is a fixed retry ladder that treats every failure the same.

That ladder is wrong in both directions at once. It spends attempts on failures
that can never succeed — an expired card, a revoked mandate, a malformed request,
a risk decline — and it chases customers it should not be touching, including
ones who have already paid. On a 320-case book, **36% of failed value is
structurally unrecoverable by retry**, and the ladder spends 41% of its retries
on it.

**What Munshi does.** It closes the loop end to end for one at-risk rupee at a
time: detect → quantify → diagnose → decide → act → verify → stop, with the money
either recovered, put in front of a human, handed off, or deliberately released,
and a reason recorded for each.

The thing that makes it work is a signal Razorpay already emits and almost nobody
uses as a routing input. Every failed payment carries an `error_source`
(customer / business / gateway / razorpay) and an `error_reason` from a closed
vocabulary, and Razorpay documents who has to act on each. Munshi encodes 65 of
those codes into ten recovery families with explicit retryability, and correlates
each failure against Razorpay's live **Payment Downtime** feed for that exact
instrument — so "retry in six hours" becomes a decision instead of a default.

**What makes it safe enough to actually run.** Three layers, deliberately
separated:

- **Retryability is a lookup**, not a judgement. `card_expired` is not retryable
  however confident anything is about it.
- **A deterministic policy engine** the model cannot reach evaluates ~20 rules on
  every proposal and returns allow / require-approval / deny. Autonomy tiers L0–L4
  are a static table. `write_off` is L4: no tier makes a tax-relevant accounting
  decision an agent's call.
- **A regulatory envelope** encoded as hard bounds: the RBI Fair Practices Code
  contact window (08:00–19:00, automated messages included), the RBI e-mandate
  framework's 24-hour pre-debit notification and ₹15,000 AFA-free ceiling, and
  NPCI's non-peak auto-debit windows.

**Measured, on a 320-case / ₹1.83Cr batch** against a fixed retry ladder over
identical cases with identical hidden ground truth and identical per-case seeds:

| | Fixed ladder | Munshi + approvals |
|---|---:|---:|
| Recovered | ₹81,21,139 | ₹74,54,560 |
| Retries with zero possible yield | **201 (41%)** | **0** |
| Customers chased after paying | **15** | **0** |
| Opted-out customers contacted | **18** | **0** |
| RBI / NPCI window violations | **238** | **0** |
| Intervention accuracy | 66.6% | **87.2%** |

The ladder wins on gross revenue by about 9%, and that is reported rather than
tuned away. It buys those rupees with 201 impossible retries, 15 messages to
people who had already paid, and 238 regulatory breaches. Munshi collects 92% of
the gross with half the retries and none of that — and puts ₹78L in front of the
merchant with a reason attached rather than moving it alone.

Every rupee counted as recovered has a ledger row pointing at the action that
caused it. Every decision is in an append-only, sha256-chained audit trail.

## 3 · GitHub repository

**https://github.com/CodeInfinity1/munshi** — public.

## 4 · Five-minute pitch video

**`VIDEO_LINK_PLACEHOLDER`**

> ⚠️ **This is the one field still to fill in.** Record the run using
> [demo-script.md](demo-script.md), upload to YouTube as **Unlisted**, then
> replace the placeholder above *and* paste the same link into the submission
> form. Nothing else in this repository is a placeholder.

Script: [demo-script.md](demo-script.md).

## 5 · Build challenges and technical obstacles

Six problems that were genuinely hard, and what actually broke.

### Turning noisy payment events into a state machine that always terminates

A failed payment is an event; revenue at risk is a *state* with a budget and a
clock. The hard part was termination. Early runs left cases parked in `scheduled`
indefinitely, which meant rupees nobody was accountable for. The fix was an
explicit end-of-window sweep that gives every remaining case a named stop reason,
plus a test asserting that after any batch no case is left non-terminal.

### Deny-for-now versus deny-forever

The single most consequential distinction in the policy engine, and the source of
two real bugs.

A contact attempted at 22:14 is not a dead case — it must wait until 08:00. A
fourth retry *is* a dead case. Conflating them either spams customers or writes
off live revenue, and both happened:

1. A hard `deny` was silently downgraded by a later rule's `reschedule_at`,
   looping one case through **130 refused retries** across 130 ticks. Fixed by
   making a permanent denial dominate a temporary one.
2. That fix immediately broke something else: the e-mandate pre-debit rule
   declared a permanent `deny` when it actually meant *"the notice hasn't been
   sent yet"* — so the new precedence turned **18 live cases into write-offs**.
   Its escalation is now conditional on the amount: above the AFA ceiling it is
   permanent, below it, a deferral.

### A bound that a synonym could sidestep

`schedule_retry` and `retry_payment` were two names for the same operation, and
only one of them was in the `MONEY_MOVING` set — so the autonomous-amount ceiling
could be bypassed entirely by proposing the synonym. A safety test caught it.

The fix was to delete the redundant action rather than patch the list: a retry's
timing was already fully carried by `delay_hours`. **Two names for one operation
is how a bound gets bypassed.**

### Measuring recovery in a way that survives being questioned

The hardest design problem in the project. "We recovered ₹X" is worthless if the
system grading the outcome can see what the agent decided.

The answer was latent ground truth: every case carries a hidden record — was this
ever recoverable, when does the payer's balance really top up, would the customer
really replace a dead card, when does the outage really clear. The agent never
reads it (asserted by a test that greps the entire context pack *and* the entire
API response for every latent field name). The oracle resolves actions against
that truth and the action's timing, and is never shown the agent's reasoning. Luck
is seeded per `(case, action, attempt)`, so both arms draw identical luck on
identical cases and only the choice and timing differ.

The oracle's probability table is stated in the source and is deliberately
*generous* to retries once the precondition is met — which flatters the baseline,
not us.

### Getting the agent to stop being wrong in ways that cost real money

Four product bugs the batch found that reading the code did not:

- **`merchant_config` was classified as never-retryable.** It is unretryable
  *until the merchant enables the method*, which is a different thing. The wrong
  label wrote off ₹13L of collectable revenue.
- **Alerting merchant ops was terminal**, so the case was abandoned before the
  human could act on the alert. Now: alert, keep the case open, probe with a
  re-presentment once it's fixed.
- **Exhausting the contact budget closed the whole case** even when retries
  remained. A budget bounds an *avenue*, not a case.
- **Backoffs were measured from now rather than from the failure**, so a six-day-
  old insufficient-funds case waited another 24 hours for a precondition that had
  been satisfied for five days.

Recovered cases went 72 → 121 across those four fixes.

### Downtime that never ends, and a loop that never stops

An unscheduled Razorpay downtime has no published `end` — `null` until resolution
is announced. The first implementation therefore treated a live outage as
permanent, and cases waited on it forever: **3,024 of 3,369 reschedules** were one
outage.

Two fixes. Resolution is now published the way Razorpay does it — a
`payment.downtime.resolved` transition driven off ground truth the agent cannot
read, so an outage genuinely has no known end until it is announced. And the wait
is capped: after three holds the agent stops waiting on a broken rail and offers
the customer a working one. Decisions per batch fell from 4,397 to 1,516, which
in the LLM arm is a 3× cost reduction on top of the correctness fix.

### Not faking the payment rail

Re-presenting a failed charge needs a customer-authorised mandate token that
Razorpay test mode cannot mint. The tempting move is to simulate the call and
report it as though Razorpay ran it. The adapter raises `UnsupportedInTestMode`
instead, and the executor records the action as *not executed* with that reason.
The header of the dashboard states which adapter is live and whether money
movement is simulated, next to the button that produces it.

---

## What I would build next

1. **Replace the outcome oracle with observed data.** Everything else stays; the
   probability table is the one component that should come from a merchant's own
   history rather than from documented resolution conditions.
2. **Learn the timing.** `funds_available_after_h` is currently a prior. Per-payer
   settlement-hour and salary-cycle inference is the highest-value model in the
   system and is not built yet.
3. **Multi-tenancy and real approval identity.** Today it is one merchant per
   deployment and approvals are attributed to `"merchant"`, not a person.
4. **The reply loop.** Promise-to-pay is detected from a simulated customer reply;
   parsing real inbound WhatsApp and email replies is the obvious next agentic
   surface.

## Repository checklist

- 94 tests, including a suite written specifically to catch dangerous autonomous
  behaviour
- CI runs lint, tests, and re-runs the evaluation asserting it reproduces the
  committed figures exactly
- `make demo` builds, seeds and serves in one command, with no credentials
- No secrets in the repository or its history
