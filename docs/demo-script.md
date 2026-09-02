# Five-minute demo

Everything below is live application state. Nothing is a mock, an animation, or
a pre-recorded number, and every case id is deterministic from seed `20260824` —
they will be the same on your machine.

## Setup

```bash
make install && make build && make seed
MUNSHI_API_TOKEN=demo-token uvicorn munshi.api:app --port 8000
```

Open <http://127.0.0.1:8000>, paste `demo-token` into the header field. Leave the
theme on light. Do **not** press Run yet.

---

## 0:00 — 0:35 · The problem

> "Merchants don't lose revenue because they don't know a payment failed. They
> lose it because nobody closes the loop — and because the loop most of them run
> is a fixed retry ladder that treats every failure the same."

Point at the header strip: **Money movement: SIMULATED · Reasoner: deterministic
· Audit chain: verified**.

> "This is a simulated payment rail, and the product says so in its own header.
> I'll be precise all the way through about what's real."

Point at the book: **320 cases · ₹1.83Cr at risk**, one solid amber bar.

## 0:35 — 1:15 · The insight

Point at the line above the bar: *29.91% of failures are structurally
unretryable (₹50.61L)*.

> "Razorpay tells you, on every failed payment, an error_source and an
> error_reason from a closed vocabulary — and documents who has to act on each
> one. Run that over this book and 67 of the 224 coded failures — 36% of the
> failed value — can never be retried successfully. The card has expired, the
> mandate is gone, a risk engine declined it, or the customer has already paid.
> A ladder spends attempts on all of it."

Open the **Policy** tab, scroll to *Recovery families*.

> "Sixty-five reason codes, grouped by what would have to change for a retry to
> work. This is a lookup, not a model — retryability is a property of the failure
> code, so no model gets a vote on it."

## 1:15 — 2:00 · Run it

Back to **Recovery desk**. Press **Run recovery batch**.

> "Fourteen days of recovery on a virtual clock, so a retry deferred by 36 hours
> actually waits 36 hours."

Let the bar fill while you narrate the counter: *tick N · decisions · executed ·
blocked*. It resolves in about fifteen seconds to:

**Recovered ₹30.73L · Held for you ₹78.03L · Retries with zero possible yield: 0
· Customers chased after paying: 0**

## 2:00 — 3:15 · Four decisions, in the case drawer

Search each id and open it. These are the ones worth showing.

**`case_0003` — ₹1,21,838, `payment_failed` on a `@ybl` UPI handle.**
> "Razorpay documents payment_failed as *no specific error code received from
> gateway*. That's genuinely ambiguous. But Razorpay's Payment Downtime feed says
> there's an active high-severity outage on this exact VPA handle right now."

Point at the downtime panel and then at the policy rules: `downtime_clear` failed.
> "Retries are a bounded resource — three per case. Spending one into a live
> outage is a guaranteed-zero action. It held three times, re-checking each time,
> and the moment Razorpay published the resolution it retried. First attempt,
> ₹1.22 lakh captured. Note the three refusals in the trail above it — they cost
> nothing from the retry budget, because the budget is spent on execution, not on
> proposals."

**`case_0134` — ₹76,967, `order_already_paid` → suppressed.**
> "The customer already paid. The single most damaging false positive in this
> business is chasing someone who has settled. Rule `not_already_settled`, hard
> stop, zero messages. The ladder sent 15 of these."

**`case_0262` — ₹3,91,331, `payment_risk_check_failed` → escalated.**
> "Risk declined it. No retry, no message, escalated to a human. An automated
> system must not launder its way past a risk decision, so this is a hard stop —
> not a limit it can spend down."

**`case_0230` — ₹11,37,126, `reqauth_mandate_not_acknowledged` → recovered.**
> "The mandate is gone, so re-presenting the debit isn't a legal path to the
> money — under the RBI e-mandate framework it needs fresh authentication only the
> customer can give. So it sends a re-auth link, not a retry. Retries spent:
> zero. And look at the timestamps: three messages, one per morning, all at
> 09:00 local. The 20-hour contact cooldown expires at 05:00 — but the RBI Fair
> Practices window doesn't open until 08:00, so it waits. Twelve policy rules
> evaluated on the final one, zero failed. ₹11.4 lakh back."

## 3:15 — 3:50 · The bound, and one graceful failure

Point at the **Needs a human** queue: **₹78,02,678 across 18 actions**.

Open `case_0064` (₹12,02,857, `insufficient_funds`).
> "This is above the merchant's ₹2 lakh autonomous ceiling. The agent proposed
> the retry, the policy engine returned require_approval, and it stopped. That
> ₹78 lakh isn't money the agent failed to recover — it's money it refused to
> move without you."

Press **Approve** on one row and watch it execute.

Now open `case_0239` (₹28,211, `card_expired` on an e-mandate, stopped).
> "And here's the failure. The card behind the mandate expired, so re-presenting
> it can never work — retries spent: zero. It sent a re-authorisation link three
> times, the customer never came back, and it stopped with
> `all_recovery_avenues_exhausted`. It does not keep going. That's the point."

## 3:50 — 4:30 · The measurement

Open the **Evaluation** tab.

> "Same 320 cases, same hidden ground truth, same per-case random seeds, same
> outcome oracle. Only the choice of action and its timing differ."

Point at the first table.
> "The fixed ladder recovers ₹81 lakh. Munshi with approvals recovers ₹74.5
> lakh. **The ladder wins on gross revenue, and I'm showing you that rather than
> hiding it.**"

Point at the second table.
> "Here's what those extra rupees cost. 201 retries with zero possible yield —
> 41% of everything it attempted. 15 customers chased after they'd already paid.
> 18 opted-out customers contacted. 238 breaches of the RBI Fair Practices
> contact window, because a dunning cron fires at 2am and the regulator counts an
> automated SMS as contact. Munshi collects 92% of that gross with half the
> retries and none of those. Intervention accuracy 87% against 67%."

## 4:30 — 5:00 · The trail, and what's real

Open the **Audit** tab.

> "Every decision writes a record, chained by sha256 to the one before it. Edit
> any row and every hash after it fails. That's the header line: chain verified,
> 14,638 records. 'We wrote it down' and 'nobody changed it' are different
> claims."

Close on the honesty strip.
> "Money movement here is simulated — a per-case-seeded oracle that resolves
> against hidden ground truth the agent never sees, so it can't talk its way into
> a recovery. The Razorpay adapter makes real test-mode calls for payment links
> and the downtime feed, and it *refuses* to re-present a charge, because test
> mode can't mint the mandate token that needs. It raises instead of simulating
> a rail it can't reach. A revenue-recovery demo that lets you assume a real
> payment ran is the one thing this shouldn't do."

---

## If you have thirty more seconds

Search `case_0285` (₹9,10,019, `payment_method_not_enabled`).
> "Razorpay says `business` — this is the merchant's own misconfiguration.
> Messaging the customer is an actively wrong action: they cannot enable a
> payment method. So it pages merchant ops, keeps the case open rather than
> closing it, and comes back with a re-presentment once someone fixes the config.
> Alert, then probe. At ₹9.1 lakh that probe is above the autonomous ceiling, so
> it lands in the approval queue too."

Search `case_0198` (₹76,026, customer opted out).
> "Zero messages. Ever."
