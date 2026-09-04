# Five-minute demo

Everything below is live application state. Nothing is a mock-up, an animation,
or a pre-recorded number, and every case id is deterministic from seed
`20260824` — they will be the same on your machine.

## Setup

```bash
make install && make build
export GROQ_API_KEY=gsk_...        # the agent runs for real
make seed
MUNSHI_API_TOKEN=demo-token uvicorn munshi.api:app --port 8000
```

Without a Groq key, set `MUNSHI_REASONER=mock-agent` to demonstrate the same tool
loop against the deterministic stand-in — the header will say
`agent · MOCK PROVIDER` and you should say so too.

Open <http://127.0.0.1:8000>, paste `demo-token` into the header field. Leave the
theme on light and the **Agent activity** pane selected. Do not press Run yet.

---

## 0:00 — 0:30 · The problem

> "Merchants don't lose revenue because they can't see that a payment failed.
> Razorpay tells them in seconds. They lose it because nobody closes the loop —
> and because the loop most of them run is a fixed retry ladder that treats every
> failure the same."

Point at the header strip: **Money movement: SIMULATED · Reasoner: agent ·
openai/gpt-oss-120b · Audit chain: verified.**

> "This is a simulated payment rail, and the product says so in its own header,
> next to the button that produces the numbers. I'll be precise about that
> throughout."

The book: **320 cases · ₹1.85Cr at risk**, one solid amber bar.

## 0:30 — 1:05 · The insight

> "Razorpay returns, on every failed payment, an error_source and an error_reason
> from a closed vocabulary, and documents who has to act on each. Run that over a
> realistic failure mix and **27.7% of coded failures are structurally
> unretryable** — expired card, revoked mandate, malformed request, risk decline,
> or the customer already paid. No ladder can collect a rupee of it."

Open **Policy** → *Recovery families*.

> "Sixty-five reason codes grouped by what would have to change for a retry to
> work. That's a lookup, not a model — retryability is a property of the failure
> code, so no model gets a vote on it."

Scroll to *Agent tools*.

> "And here's what the model *can* do. Six reads, one calculation, one policy dry
> run, and one tool that submits a proposal. There is no retry_payment tool. A
> fully compromised model cannot execute a payment."

## 1:05 — 1:35 · Run it

Back to **Recovery desk**. Press **Run recovery batch**.

> "Fourteen days of recovery on a virtual clock, so a retry deferred by 36 hours
> actually waits 36 hours."

The activity pane streams. Let it run and narrate what scrolls past — tool calls,
diagnoses, ALLOWED and BLOCKED badges.

It settles at **Recovered ₹24.7L · Held for you ₹34.2L · Retries with zero
possible yield: 0 · Customers chased after paying: 0.**

## 1:35 — 2:35 · Watch it think

Stay in **Agent activity**. Find `case_0014` (₹76,968, `bank_technical_error`).

> "Here's one decision, end to end. Priority put it in front of the agent. It
> called get_downtime_status and Razorpay's feed said there's an active
> high-severity outage on this exact instrument. Then it dry-ran check_policy on a
> retry and got back DENY — downtime_clear."

Point at the BLOCKED badge and the rule detail underneath.

> "Retries are a bounded resource: three per case. Spending one into a live outage
> is a guaranteed-zero action. It held three times, re-checking each time, and the
> moment Razorpay published the resolution it retried. ₹76,968 captured on the
> first real attempt."

Now click **Only what policy stopped**.

> "This is the filter I'd want if I ran a merchant. Every decision the policy
> engine refused, with the rule that refused it."

Find `case_0081` (₹88,451, `order_already_paid`).

> "The customer already paid. Suppressed before a single message went out — the
> most damaging false positive in this business is chasing someone who has
> settled. The ladder sent fifteen of these."

## 2:35 — 3:15 · The race, and the bound

Switch to **Cases** → filter **Paid elsewhere**.

> "₹17.4 lakh across 15 cases. These customers paid through another channel
> *while* Munshi was working them."

Open `case_0127` (₹78,695).

> "We sent a reminder at 11:00. At 05:00 the next morning the money landed
> out-of-band. Munshi stopped, and — this is the part I care about — it claimed
> none of it. No ledger row, its own column, never folded into the recovered
> figure. That ₹17.4 lakh is 66% of the unattended recovered total. Claiming it
> would have been very tempting and completely wrong."

Point at the **Needs a human** queue: **₹34,16,181.**

Open `case_0004` (₹11,44,992, `insufficient_funds`).

> "Above the merchant's ₹2 lakh autonomous ceiling. The agent proposed the retry,
> the policy engine returned require_approval, and it stopped. That money isn't
> revenue it failed to recover — it's revenue it refused to move without you."

Press **Approve** on one row and watch it execute.

## 3:15 — 3:40 · The failure

Search `case_0071` (₹46,674, `card_expired` on an e-mandate).

> "And here it is failing. The card behind the mandate expired, so re-presenting
> it can never work — **retries spent: zero**. It sent a re-authorisation link
> three times. Look at the timestamps: three mornings, all at 09:00. The 20-hour
> contact cooldown expires at 05:00, but the RBI Fair Practices window doesn't
> open until 08:00, so it waited.
>
> The customer never came back. It stopped, with
> `all_recovery_avenues_exhausted`. It does not keep going. That's the point."

## 3:40 — 4:25 · Measured recovery

Open **Evaluation**.

> "Same 320 cases, same hidden ground truth, same per-case seeds, same outcome
> oracle. Only the choice of action and its timing differ."

> "The fixed ladder recovers ₹72.07 lakh. Munshi with approvals recovers ₹61.92
> lakh. **The ladder wins on gross revenue by 14%, and I'm showing you that rather
> than hiding it.**"

Second table.

> "Here's what those extra rupees cost. 153 retries with zero possible yield —
> 34.7% of every retry it made. 15 customers chased after they'd already paid. 27
> opted-out customers contacted. 223 breaches of the RBI contact window, because a
> dunning cron fires at 2am and the regulator counts an automated SMS as contact.
>
> Munshi collects 86% of that gross with 42% fewer retries and none of those
> costs. Intervention accuracy 90% against 68%. The ladder's diagnosis accuracy is
> zero, because it doesn't diagnose."

## 4:25 — 5:00 · The trail, and what's real

Open **Audit**.

> "Every decision — every tool call, every rule verdict — writes a record chained
> by sha256 to the one before it. Edit any row and every hash after it fails.
> That's the header line: chain verified, 15,000-odd records. 'We wrote it down'
> and 'nobody changed it' are different claims."

Close on the honesty strip.

> "Money movement here is simulated, against a per-case-seeded oracle that
> resolves against ground truth the agent never sees — so it can't talk its way
> into a recovery. The Razorpay adapter makes real test-mode calls for payment
> links and the downtime feed, and it **refuses** to re-present a charge, because
> test mode can't mint the mandate token that needs. It raises rather than
> simulating a rail it can't reach.
>
> Detecting revenue at risk is the easy half. The hard half is knowing which of it
> is collectable, taking exactly the actions you're allowed to take, and stopping
> — provably — when you should."

---

## Spares, if you have time

- `case_0189` — ₹10,80,311, the largest case in the book, settled itself before
  Munshi reached it. Claimed: nothing.
- `case_0057` — risk-declined. No retry, no message, escalated. An automated
  system must not launder its way past a risk decision.
- `case_0227` — an opted-out customer that was still *recovered*. Opt-out blocks
  contact, not a re-presentment of a payment they already authorised.
