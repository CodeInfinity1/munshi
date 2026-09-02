# Five-minute pitch script

Written to be read aloud over the live product. Timings are for a 5:00 ceiling.
Every number here comes from `evaluation/results.json` in this repository.

---

### 0:00 — Hook *(20s)*

Merchants don't lose revenue because they can't see that a payment failed.
Razorpay tells them within seconds.

They lose it because nobody closes the loop — and because the loop most of them
run is a fixed retry ladder that treats every failure exactly the same.

That ladder is wrong in two directions at once.

### 0:20 — The problem, precisely *(40s)*

Here's a book of 320 revenue-risk events. ₹1.83 crore at risk. Failed payments,
failed subscription charges, overdue invoices, abandoned checkouts.

Razorpay returns, on every failed payment, an `error_source` and an `error_reason`
from a closed vocabulary — and documents who has to act on each one.

Run that over this book: **67 of the 224 coded failures — 36% of the failed value,
₹50.6 lakh — are structurally unretryable.** The card has expired. The mandate is
revoked. The request is malformed. A risk engine declined it. Or the customer has
already paid.

No retry ladder can collect a rupee of that. A ladder spends attempts on all of it
anyway — and while it's doing that, it's messaging people who've already paid.

### 1:00 — What Munshi is *(30s)*

Munshi is a bounded revenue-recovery agent. For every at-risk rupee it runs the
whole loop: detect, quantify, diagnose, decide, act, verify, and stop — with a
reason.

Three layers, deliberately separated. Retryability is a **lookup** over 65
Razorpay reason codes — no model gets a vote. A **model** does the part that's
genuinely judgement. And a **deterministic policy engine** decides what's actually
allowed to happen, which the model cannot reach.

### 1:30 — Live *(30s)*

*[press Run]*

Fourteen days of recovery on a virtual clock, so a retry deferred by 36 hours
actually waits 36 hours.

*[the bar fills]*

₹30.7 lakh recovered. ₹78 lakh held for a human. **Zero retries with zero possible
yield. Zero customers chased after they'd paid.**

### 2:00 — Agent reasoning *(45s)*

*[open case_0003]* ₹1,21,838, failure code `payment_failed` — which Razorpay
documents as *"no specific error code received from gateway."* Genuinely
ambiguous.

But Razorpay's **Payment Downtime feed** says there's an active high-severity
outage on this exact UPI handle right now. Retries are a bounded resource — three
per case. Spending one into a live outage is a guaranteed-zero action.

So it held. Three times, re-checking each time. The moment Razorpay published the
resolution, it retried — and captured ₹1.22 lakh on the first attempt.

*[open case_0230]* ₹11.37 lakh. The mandate is revoked. Under the RBI e-mandate
framework, re-presenting the debit isn't a legal path to that money — it needs
fresh authentication only the customer can give. So it sends a re-authorisation
link, not a retry.

Look at the timestamps: three messages, one per morning, all at 09:00. The
20-hour cooldown expires at 05:00 — but the RBI Fair Practices contact window
doesn't open until 08:00. So it waits.

### 2:45 — Bounded execution *(45s)*

*[the approval queue]* ₹78 lakh, 18 actions, waiting on a human.

*[open case_0064]* ₹12 lakh insufficient-funds. Above the merchant's ₹2 lakh
autonomous ceiling. The agent proposed the retry, the policy engine returned
`require_approval`, and it stopped. That money isn't revenue it failed to
recover — it's revenue it **refused to move without you**.

*[open case_0262]* ₹3.9 lakh, risk-declined. No retry, no message, escalated. An
automated system must not launder its way past a risk decision — so this is a hard
stop, not a limit it can spend down.

*[open case_0134]* ₹76,967. The customer already paid. Suppressed before a single
message went out.

### 3:30 — Graceful failure *(25s)*

*[open case_0239]* And here's it failing.

Card behind the mandate expired. Re-presenting can never work, so **retries spent:
zero**. It sent a re-authorisation link three times. The customer never came back.
It stopped, with `all_recovery_avenues_exhausted`.

It does not keep going. That's the whole point.

### 3:55 — Measured recovery *(45s)*

*[Evaluation tab]* Same 320 cases, same hidden ground truth, same per-case random
seeds, same outcome oracle. Only the choice of action and its timing differ.

**The fixed ladder recovers ₹81.2 lakh. Munshi with approvals recovers ₹74.5
lakh. The ladder wins on gross revenue — and I'm showing you that rather than
hiding it.**

Here's what those extra rupees cost. 201 retries with zero possible yield — 41% of
everything it attempted. 15 customers chased after they'd already paid. 18
opted-out customers contacted. 238 breaches of the RBI contact window, because a
dunning cron fires at 2am and the regulator counts an automated SMS as contact.

Munshi collects 92% of that gross with half the retries and none of those costs.
Intervention accuracy 87% against 67%.

### 4:40 — Audit and honesty *(20s)*

*[Audit tab]* Every decision writes a record, sha256-chained to the one before it.
Edit any row and every hash after it fails. 14,638 records, chain verified.

And the header says it: money movement here is **simulated**, against a
per-case-seeded oracle that resolves against ground truth the agent never sees —
so it cannot talk its way into a recovery. The Razorpay adapter makes real
test-mode calls for payment links and the downtime feed, and it **refuses** to
re-present a charge, because test mode can't mint the mandate token that needs.
It raises rather than simulating a rail it can't reach.

### 5:00 — Close

Detecting revenue at risk is the easy half. The hard half is knowing which of it
is actually collectable, taking exactly the actions you're allowed to take, and
stopping — provably — when you should.

That's Munshi.
