# Munshi evaluation report

Batch of **320 revenue-risk cases** (seed `20260824`), 14-day recovery window, 2h ticks.

Every arm runs the **same cases with the same latent ground truth and the same per-case seeds**, through the same outcome oracle. Only the choice of action and its timing differ.

> All money movement in this report is **simulated**. A rupee is counted as recovered only when a ledger row exists pointing at the action that caused it; there is no estimated recovery anywhere in these numbers.

## The claim this rests on

- 52 of 215 cases carrying a Razorpay failure code (**24.19%**, **17.22%** of failed value = Rs 2,142,019) are *structurally unretryable*: the instrument, the mandate or the request itself cannot authorise the amount, whatever the ladder does.

## Headline

| Metric | baseline | agent-heuristic | agent-heuristic-approved | agent-mock |
|---|---|---|---|---|
| Revenue at risk | Rs 18,496,883 | Rs 18,496,883 | Rs 18,496,883 | Rs 18,496,883 |
| **Revenue recovered** | **Rs 7,207,487** | **Rs 2,641,238** | **Rs 6,192,376** | **Rs 2,454,483** |
| Recovery rate (of at-risk) | 38.97% | 14.28% | 33.48% | 13.27% |
| Recovery rate (of recoverable) | 49.35% | 18.08% | 42.4% | 16.8% |
| Cases recovered | 125/320 | 113/320 | 118/320 | 100/320 |
| Held for merchant approval | Rs 0 | Rs 6,332,969 | Rs 0 | Rs 3,416,181 |
| Annualised MRR protected | Rs 41,789,112 | Rs 5,820,816 | Rs 29,545,872 | Rs 5,620,032 |

> **Read the `agent-mock` arm's intervention accuracy as an artefact, not a result.** The mock provider picks its action from the same taxonomy family the accuracy metric scores against, so it is correct by construction. That arm exists to prove the tool loop runs end to end without a network, not to say anything about judgement quality.

## Efficiency and harm

| Metric | baseline | agent-heuristic | agent-heuristic-approved | agent-mock |
|---|---|---|---|---|
| Actions executed | 1001 | 842 | 890 | 614 |
| Retries spent | 441 | 244 | 254 | 117 |
| **Retries with zero possible yield** | **153** (34.69%) | **0** (0.0%) | **0** (0.0%) | **0** (0.0%) |
| Customer messages sent | 560 | 517 | 554 | 482 |
| Messages per recovered case | 4.48 | 4.58 | 4.69 | 4.82 |
| **Customers chased after paying** | **15** | **0** | **0** | **0** |
| Opted-out customers contacted | 27 | 0 | 0 | 0 |
| Intervention accuracy | 67.75% | 89.87% | 89.87% | 100.0% |
| Diagnosis accuracy | 0.0% | 87.3% | 87.3% | 66.78% |

## Compliance

The baseline is run *without* the compliance envelope, because a naive dunning cron genuinely does fire at 02:00. Its breaches are executed and counted; the agent's policy engine prevents them.

| Violation | baseline | agent-heuristic | agent-heuristic-approved | agent-mock |
|---|---|---|---|---|
| RBI contact-window (08:00-19:00) | 183 | 0 | 0 | 0 |
| NPCI non-peak debit window | 13 | 0 | 0 | 0 |
| Contacted an opted-out customer | 27 | 0 | 0 | 0 |

## Bounds held

| Check | baseline | agent-heuristic | agent-heuristic-approved | agent-mock |
|---|---|---|---|---|
| Cases over the 3-retry cap | 0 | 0 | 0 | 0 |
| Cases over the 3-contact cap | 0 | 0 | 0 | 0 |
| Every case reached a terminal state | False | False | False | False |
| Audit chain verifies | True | True | True | True |
| Audit records | 16588 | 15015 | 15451 | 23312 |

## Arm detail: baseline

- reasoner `fixed_ladder`, adapter `simulator`, 5.63s over 168 ticks

Why cases stopped:

- `recovered` - 125
- `all_recovery_avenues_exhausted` - 98
- `recovery_window_expired` - 83
- `customer_paid_through_another_channel` - 14

Recovery attributed by action:

- `retry_payment` - Rs 5,767,295
- `send_reminder` - Rs 1,440,192

## Arm detail: agent-heuristic

- reasoner `heuristic`, adapter `simulator`, 4.64s over 168 ticks

Why cases stopped:

- `recovered` - 113
- `recovery_window_expired` - 89
- `all_recovery_avenues_exhausted` - 69
- `customer_paid_through_another_channel` - 15
- `customer_opted_out` - 8
- `escalate_to_merchant_ops_completed` - 6
- `suppress_case_completed` - 5
- `not_a_customer_resolvable_failure` - 2
- `no_intervention_worth_taking` - 1

Recovery attributed by action:

- `retry_payment` - Rs 1,216,643
- `send_recovery_link` - Rs 1,020,767
- `send_reminder` - Rs 334,531
- `send_mandate_reauth_link` - Rs 67,298
- `send_instrument_update_link` - Rs 1,999

## Arm detail: agent-heuristic-approved

- reasoner `heuristic`, adapter `simulator`, 4.72s over 168 ticks

Why cases stopped:

- `recovered` - 118
- `recovery_window_expired` - 96
- `all_recovery_avenues_exhausted` - 70
- `customer_paid_through_another_channel` - 14
- `customer_opted_out` - 8
- `escalate_to_merchant_ops_completed` - 6
- `suppress_case_completed` - 5
- `not_a_customer_resolvable_failure` - 2
- `no_intervention_worth_taking` - 1

Recovery attributed by action:

- `retry_payment` - Rs 4,689,086
- `send_recovery_link` - Rs 1,020,767
- `send_reminder` - Rs 334,531
- `escalate_to_collections` - Rs 78,695
- `send_mandate_reauth_link` - Rs 67,298
- `send_instrument_update_link` - Rs 1,999

## Arm detail: agent-mock

- reasoner `agent`, adapter `simulator`, 10.75s over 168 ticks

Why cases stopped:

- `recovery_window_expired` - 135
- `recovered` - 100
- `all_recovery_avenues_exhausted` - 34
- `customer_paid_through_another_channel` - 15
- `customer_opted_out` - 9
- `emandate_requires_customer_afa` - 8
- `escalate_to_merchant_ops_completed` - 6
- `suppress_case_completed` - 5
- `no_duplicate_successful_action` - 4

Recovery attributed by action:

- `retry_payment` - Rs 1,030,687
- `send_recovery_link` - Rs 1,019,968
- `send_reminder` - Rs 334,531
- `send_mandate_reauth_link` - Rs 67,298
- `send_instrument_update_link` - Rs 1,999

---

Generated by `python -m munshi.evaluation.harness`. Raw figures in `results.json`.
