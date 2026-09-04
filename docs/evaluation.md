# Evaluation

```bash
make eval    # writes evaluation/results.json and evaluation/report.md
```

CI re-runs this and asserts the recovered totals match the committed file
exactly. The batch is deterministic, so a drift means a behaviour change landed
without the reported figures being updated.

## Method

320 synthetic revenue-risk cases, ₹1,84,96,883 at risk, generated from seed
`20260824`. A 14-day recovery window stepped in 2-hour ticks on a virtual clock.

**Every arm sees identical cases, identical latent ground truth, and identical
per-case seeds, resolved by the same outcome oracle.** Only the choice of action
and its timing differ. That makes the comparison a counterfactual rather than two
draws from a random process.

### Arms

| Arm | Reasoner | Policy |
|---|---|---|
| `baseline` | Fixed retry ladder at +6h / +24h / +72h from the original failure, plus a generic reminder after each rung | Caps only (3 retries, 3 messages, 14-day window) |
| `agent-heuristic` | Taxonomy-driven deterministic reasoner, no model | Full policy engine |
| `agent-heuristic-approved` | Same | Full policy engine, with a merchant approving the queued L3 actions |
| `agent-mock` | The real tool loop, driven by the deterministic `MockProvider` | Full policy engine |
| `agent-groq` | The real tool loop against `openai/gpt-oss-120b` | Full policy engine. Needs `GROQ_API_KEY`. |

**Baseline fairness.** The ladder keeps the retry and contact *caps*, because
almost every real dunning setup has them — the comparison is meant to isolate
judgement, not to win by giving the baseline no limits. It does **not** get the
compliance envelope, because a naive dunning cron genuinely does fire at 02:00.
Those attempts are executed and *counted*, which is how the harness can report
the violations the policy engine prevented.

**Why the committed numbers are from the deterministic arm.** It is the weaker
claim, and it separates "the taxonomy and policy engine work" from "the model
helps". Run `--arms agent-groq` with a key to add the model arm alongside.

**`agent-mock` is a mechanism test, not a quality result.** It exists to prove the
tool loop runs end to end with no network. Read its intervention accuracy as an
artefact: the mock picks its action from the same taxonomy family the metric
scores against, so it is correct by construction.

## Latent ground truth

Every case carries a hidden record the agent never reads:

```json
{
  "true_cause": "payer_balance",
  "family": "balance_dependent",
  "recoverable": true,
  "funds_available_after_h": 38,
  "intent": "lukewarm",
  "responds_to_contact": 0.62,
  "seed": 1483920117
}
```

Plus, per family: `will_replace_instrument`, `outage_clears_after_h`,
`limit_resets_after_h`, `merchant_fixes_after_h`, `will_promise_to_pay`,
`honours_promise`.

`test_the_agent_never_sees_latent_ground_truth` greps the entire serialised
context pack for every latent field name across a 20-case batch, and
`test_latent_ground_truth_is_never_served` does the same to the API responses. If
either failed, every accuracy figure below would be meaningless — so it is
asserted rather than assumed.

## The outcome oracle

`munshi/adapters/simulator.py`. Three properties make its output worth reporting:

1. **It resolves against latent truth and the action's timing, never against the
   agent's reasoning.** It is not passed the diagnosis, the rationale, or which
   arm produced the action.
2. **Luck is seeded per `(case, action, attempt)`.** Both arms draw the same coin
   flip on the same case at the same attempt index.
3. **The conditional probabilities are stated in the module**, derived from each
   taxonomy family's documented resolution condition:

| Family | Retry succeeds, precondition met | …not met |
|---|---:|---:|
| `balance_dependent` | 0.86 | 0.06 |
| `transient_infra` | 0.88 | 0.11 |
| `limit_bound` | 0.82 | 0.04 |
| `merchant_config` | 0.74 | 0.00 |
| `customer_dropout` | 0.05 | 0.05 |
| `instrument_dead`, `mandate_broken`, `risk_flagged`, `integration_bug`, `already_settled` | 0.00 | 0.00 |

Contact conversion is `base(action) × intent × responsiveness × exp(-t/half-life)`,
with half-lives of 14h for an abandoned authentication and 72–120h elsewhere.

**This table is generous to retries once the precondition is met, which flatters
the fixed ladder, not Munshi.** A pessimistic table would have widened the gap in
our favour.

## Results

| Metric | Fixed ladder | Munshi, unattended | Munshi + approvals |
|---|---:|---:|---:|
| Revenue at risk | ₹1,84,96,883 | ₹1,84,96,883 | ₹1,84,96,883 |
| Latently recoverable | ₹1,46,06,071 | ₹1,46,06,071 | ₹1,46,06,071 |
| **Revenue recovered** | **₹72,07,487** | **₹26,41,238** | **₹61,92,376** |
| Recovery rate, of at-risk | 38.97% | 14.28% | 33.48% |
| Recovery rate, of recoverable | 49.35% | 18.08% | 42.4% |
| Cases recovered | 125 / 320 | 113 / 320 | 118 / 320 |
| Held for merchant approval | ₹0 | ₹63,32,969 | ₹0 |
| Paid by the customer elsewhere, **not claimed** | ₹16,61,083 (14) | ₹17,39,778 (15) | ₹16,61,083 (14) |

### What the extra gross revenue cost

| Metric | Fixed ladder | Munshi, unattended | Munshi + approvals |
|---|---:|---:|---:|
| Actions executed | 1,001 | 842 | 890 |
| Retries spent | 441 | 244 | 254 |
| **Retries with zero possible yield** | **153 (34.69%)** | **0** | **0** |
| Customer messages | 560 | 517 | 554 |
| **Customers chased after paying** | **15** | **0** | **0** |
| **Opted-out customers contacted** | **27** | **0** | **0** |
| **RBI contact-window violations** | **183** | **0** | **0** |
| **NPCI debit-window violations** | **13** | **0** | **0** |
| Intervention accuracy | 67.75% | **89.87%** | **89.87%** |
| Diagnosis accuracy | 0.00% | **87.3%** | **87.3%** |

### Bounds

| Check | Ladder | Unattended | + approvals |
|---|---:|---:|---:|
| Cases over the 3-retry cap | 0 | 0 | 0 |
| Cases over the 3-message cap | 0 | 0 | 0 |
| Every case reached a terminal state | yes | yes | yes |
| Audit chain verifies | yes | yes | yes |
| Audit records | 16,588 | 15,015 | 15,451 |

## Metric definitions

- **Revenue recovered** — `SUM(ledger.amount_paise)`. A rupee counts only if a
  ledger row exists pointing at the action that caused it. There is no estimated
  recovery anywhere in the codebase.
- **Retries with zero possible yield** — retries executed on cases whose latent
  family is `instrument_dead`, `mandate_broken`, `risk_flagged`,
  `integration_bug` or `already_settled`. Not a gamble; a certainty of failure.
- **Customers chased after paying** — customer-contacting actions executed either
  on a case Razorpay reported as `order_already_paid`, or *after* the customer
  settled through another channel mid-recovery. The most damaging false positive
  in revenue recovery, and the second form is the harder one because nothing tells
  you.
- **Paid elsewhere, not claimed** — cases where the customer paid out-of-band
  during the recovery window. Real money; it gets no ledger row and is never
  folded into the recovered figure. Worth ₹17L on this batch, which is 66% of the
  unattended recovered total — claiming it would have been a very tempting
  mistake.
- **Intervention accuracy** — the first substantive action per case, scored
  against the set of interventions the case's latent family rewards. Measures
  whether the agent chose the right *kind* of action, independently of whether
  the seeded coin flip landed in its favour.
- **Diagnosis accuracy** — first diagnosis per case where `root_cause` equals
  `latent.true_cause`. The ladder scores 0% because it does not diagnose.
- **Compliance violations** — attempts the ladder executed outside the RBI
  contact window or NPCI debit window, or to an opted-out customer. Counted for
  the ladder, prevented for Munshi.

## Reading the headline honestly

The fixed ladder recovers **₹10,15,111 more gross revenue** than Munshi with
approvals — 14.1%. That is the honest result and it is not tuned away.

It buys those rupees with 153 retries that could never have succeeded (34.7% of
every retry it made), 15 messages to customers who had already paid, 27 to people
who had opted out, and 223 breaches of published RBI and NPCI windows. Munshi
collects 85.9% of the ladder's gross with 42% fewer retries and none of those
costs, and puts ₹63L in front of a human with the reason for each rather than
moving it alone.

Whether that trade is worth making is a merchant's decision. The point of the
harness is that it is a decision with numbers on both sides.

## Threats to validity

Stated because they are the first things a reviewer should ask.

1. **The outcome model is ours.** Success probabilities are derived from
   documented resolution conditions, not from observed Razorpay data — which we
   do not have. They are stated in the source and are generous to retries, so the
   bias runs against our own result. Real merchant data would replace this table
   without changing any other component.
2. **The book is synthetic.** Reason frequencies are weighted toward a plausible
   Indian PG mix rather than a uniform spread, so the headline is not inflated by
   an unrealistically recoverable population — but they are still an estimate.
3. **The baseline is a strawman by construction, though a common one.** A fixed
   ladder is what most merchants actually run; a *sophisticated* dunning vendor
   would do better than this baseline and worse than Munshi on the harm metrics.
4. **Intervention accuracy is scored against a mapping we defined**
   (`CORRECT_INTERVENTION` in `metrics.py`). It is stated in the source and
   follows from each family's resolution condition, but it is our mapping.
5. **The `agent-groq` arm has not been run against the live API here.** No Groq
   credential was available in the build environment. What *is* verified: the
   full tool loop runs end to end (the `agent-mock` arm exercises it over all 320
   cases with zero degradations), the provider's wire format and error
   classification are tested against a stubbed client, and every adversarial path
   — malformed arguments, invented tools, prose instead of a tool call, rate
   limits, timeouts, an agent that never decides — is driven through the real
   loop by scripted providers. No committed figure comes from a live model call.
6. **Reason weights are ours.** The structurally-unretryable share is computed
   from the generator's own weight table, so it is a property of the simulated
   population rather than of Razorpay's real traffic. The population parameter
   (27.69%) is reported alongside the sampled one (24.19%) because a single draw
   is a weaker claim than the distribution it came from — and the *value* share
   moves noticeably between seeds, since it depends on which cases drew large
   amounts.
7. **The compliance comparison is not apples-to-apples on purpose.** The ladder
   is run without the envelope because that is how naive dunning behaves. A
   merchant who already enforces contact windows would see the violation columns
   go to zero on both sides, and the retry and false-positive columns unchanged.
