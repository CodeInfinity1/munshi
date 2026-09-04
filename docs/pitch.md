# Five-minute pitch script

Written to be read aloud over the live product. Every number comes from
`evaluation/results.json` in this repository.

---

### 0:00 — Hook *(20s)*

Merchants don't lose revenue because they can't see that a payment failed.
Razorpay tells them within seconds.

They lose it because nobody closes the loop — and because the loop most of them
run is a fixed retry ladder that treats every failure exactly the same.

That ladder is wrong in two directions at once.

### 0:20 — The problem, precisely *(40s)*

Here's a book of 320 revenue-risk events: ₹1.85 crore at risk. Failed payments,
failed subscription charges, overdue invoices, abandoned checkouts.

Razorpay returns, on every failed payment, an `error_source` and an `error_reason`
from a closed vocabulary — and documents who has to act on each one.

Run that over a realistic failure mix and **27.7% of coded failures are
structurally unretryable**. The card has expired. The mandate is revoked. The
request is malformed. A risk engine declined it. Or the customer has already paid.

No retry ladder can collect a rupee of that. A ladder spends attempts on all of it
anyway — and while it's doing that, it's messaging people who've already paid.

### 1:00 — What Munshi is *(35s)*

Munshi is a bounded revenue-recovery agent. For every at-risk rupee it runs the
whole loop: detect, quantify, prioritise, investigate, decide, act, verify, and
stop — with a reason.

Three layers, deliberately separated. Retryability is a **lookup** over 65
Razorpay reason codes — no model gets a vote. A **tool-using agent** does the part
that's genuinely judgement. And a **deterministic policy engine** decides what's
actually allowed to happen, which the model cannot reach.

That last point is the whole safety argument, and it's structural: the agent has
eight tools, six reads, one calculation, one policy dry-run. **There is no
retry_payment tool.** A fully compromised model cannot execute a payment.

### 1:35 — Live *(30s)*

*[press Run recovery batch]*

Fourteen days of recovery on a virtual clock, so a retry deferred by 36 hours
actually waits 36 hours.

*[the activity stream fills]*

₹24.7 lakh recovered. ₹34.2 lakh held for a human. **Zero retries with zero
possible yield. Zero customers chased after they'd paid.**

### 2:05 — Watch it think *(55s)*

*[case_0014, ₹76,968, bank_technical_error]*

This is one decision end to end. Priority put it in front of the agent. It called
`get_downtime_status`, and Razorpay's feed said there's an active high-severity
outage on this exact instrument. Then it dry-ran `check_policy` on a retry and got
back DENY — `downtime_clear`.

Retries are a bounded resource: three per case. Spending one into a live outage is
a guaranteed-zero action. So it held. Three times, re-checking each time. The
moment Razorpay published the resolution, it retried — ₹76,968 captured.

*[click "Only what policy stopped"]*

This is every decision the policy engine refused, with the rule that refused it.

*[case_0081, ₹88,451, order_already_paid]* — the customer already paid.
Suppressed before a single message went out.

### 3:00 — What it refuses to claim *(35s)*

*[Cases → Paid elsewhere]* ₹17.4 lakh, 15 cases. These customers paid through
another channel *while* Munshi was working them.

*[case_0127]* We sent a reminder at 11:00. At 05:00 next morning the money landed
out-of-band. Munshi stopped — and claimed none of it. No ledger row, its own
column, never folded into the recovered figure.

That ₹17.4 lakh is 66% of the unattended recovered total. Claiming it would have
been very tempting and completely wrong.

### 3:35 — Bounded execution *(30s)*

*[the approval queue]* ₹34.2 lakh waiting on a human.

*[case_0004, ₹11,44,992]* Above the merchant's ₹2 lakh autonomous ceiling. The
agent proposed the retry, the policy engine returned `require_approval`, and it
stopped. That's not revenue it failed to recover — it's revenue it **refused to
move without you**.

### 4:05 — Graceful failure *(25s)*

*[case_0071, ₹46,674, card_expired on an e-mandate]*

The card behind the mandate expired, so re-presenting can never work — **retries
spent: zero**. It sent a re-authorisation link three times, three mornings, all at
09:00: the 20-hour cooldown expires at 05:00, but the RBI Fair Practices window
doesn't open until 08:00, so it waited.

The customer never came back. It stopped, with `all_recovery_avenues_exhausted`.
It does not keep going. That's the point.

### 4:30 — Measured recovery *(20s)*

*[Evaluation]* Same cases, same hidden ground truth, same seeds, same oracle.

**The ladder recovers ₹72.07 lakh. Munshi with approvals recovers ₹61.92 lakh —
the ladder wins on gross by 14%, and I'm showing you that rather than hiding it.**

Here's what those rupees cost: 153 retries with zero possible yield — 34.7% of
every retry it made. 15 customers chased after paying. 27 opted-out customers
contacted. 223 breaches of the RBI contact window.

Munshi collects 86% of that gross with 42% fewer retries and none of those costs.
Intervention accuracy 90% against 68%.

### 4:50 — Close *(10s)*

Every decision, every tool call, every rule verdict is sha256-chained — edit one
row and every hash after it fails. And the money movement is simulated, which the
header says out loud.

Detecting revenue at risk is the easy half. The hard half is knowing which of it
is collectable, taking exactly the actions you're allowed to take, and stopping —
provably — when you should.

That's Munshi.
