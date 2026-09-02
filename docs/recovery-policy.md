# Recovery policy

Everything here is enforced in `munshi/policy.py`: deterministic, total, and
unreachable from the reasoning layer. The engine takes a proposal and returns
`allow`, `require_approval`, or `deny`. Every rule that runs is recorded —
passes included — because a trail that records only refusals cannot show what was
checked.

Live at `GET /api/policy`, and rendered on the dashboard's **Policy** tab.

## Autonomy tiers

The tier of an action is a property of the action, not something a model argues
for.

| Tier | Meaning | Actions |
|---|---|---|
| **L0** Observe | Records only. Never reaches a customer, never moves money. | `no_action`, `suppress_case` |
| **L1** Recommend | Surfaced to the merchant, never auto-executed. | *(reserved; no action currently sits here)* |
| **L2** Autonomous | Executed inside every limit below. | `retry_payment`, `send_recovery_link`, `send_instrument_update_link`, `send_mandate_reauth_link`, `send_reminder`, `escalate_to_merchant_ops`, `open_engineering_ticket` |
| **L3** Approval required | Queued; nothing happens until a human decides. | `offer_partial_payment`, `issue_discount`, `escalate_to_collections` |
| **L4** Forbidden | The agent may never execute this, with or without approval. | `write_off` |

**Why `write_off` is L4.** Writing revenue off is an accounting decision with tax
consequences. There is no autonomy tier at which that becomes an agent's call, so
it is not gated behind approval — it is removed from the agent's reach entirely.

**Why the L3 set is what it is.** `offer_partial_payment` and `issue_discount`
change what the merchant is owed. `escalate_to_collections` puts a third party in
front of a customer, with relationship and regulatory consequences. None of these
scale down to "small enough to be automatic", so they are tier-gated regardless
of amount.

## Limits

| Limit | Value | Nature |
|---|---:|---|
| Retries per case | 3 | merchant policy |
| Customer messages per case | 3 | merchant policy |
| Minimum hours between retries | 6 | merchant policy, floored by the failure's own documented backoff |
| Minimum hours between messages | 20 | merchant policy |
| Recovery window | 14 days | merchant policy |
| Largest autonomous re-presentment | ₹2,00,000 | merchant policy |
| AFA-free e-mandate ceiling | ₹15,000 | **regulation** (₹1,00,000 for MF / insurance / credit-card bills) |
| Per-run circuit breaker on distinct value in flight | ₹2,00,00,000 | merchant policy |
| Collections escalation floor | ₹25,000 and 45 days overdue | merchant policy |
| Consecutive holds on a live outage | 3 | merchant policy |

Two ceilings are deliberately different things, and the UI labels which is which.
₹15,000 is the RBI AFA-free e-mandate limit — above it, *only the customer* can
complete fresh authentication, so the agent cannot present the debit at all.
₹2,00,000 is merchant policy on re-presenting a payment the customer already
authorised: that is low-risk (idempotency prevents a double charge, and the money
is already owed), so the reason to gate it is relationship size, not risk.

An earlier default of ₹50,000 sat *below* the demo book's average case
(~₹57,000) and queued half the book for no safety gain. Calibrate this against
your own distribution.

The circuit breaker counts **distinct value**, not value per action: re-presenting
one case three times is one case of exposure. Counting per action would trip the
breaker during normal operation instead of during a runaway.

## Rules

Evaluated in order. Each records a verdict; a failing rule declares whether it
denies, requires approval, or is informational.

**Vocabulary and tier**
- `action_in_vocabulary` — the action must be one of the 14 known actions.
- `autonomy_tier` — L4 is refused outright.
- `tier_requires_human` — L1 and L3 route to the merchant queue.

**Terminal and settled state**
- `case_not_terminal` — a proposal on a closed case is refused.
- `not_already_settled` — Razorpay reporting `order_already_paid` means the money
  is collected. Chasing it would contact a customer who has paid. Only
  `suppress_case` is permitted.

**Risk**
- `risk_decline_hold` — on `payment_risk_check_failed`, `compliance_violation`,
  `payment_amount_tampered` or `international_transaction_not_allowed`, every
  retry, contact and money-moving action is refused and the case is escalated for
  human review. An automated system must not launder its way past a risk
  decision.

**Retry**
- `retry_can_succeed` — from the taxonomy. `card_expired`, `invalid_vpa`,
  `debit_instrument_blocked` and the rest of the structurally-dead set cannot be
  retried.
- `retry_budget` — 3 per case, then stop.
- `retry_cooldown` — `max(6h, the failure's own documented backoff)` since the
  last attempt, measured from the failure rather than from now. A six-day-old
  insufficient-funds case does not wait another 24 hours for a precondition that
  has been satisfied for five days.
- `downtime_clear` — Razorpay reporting an active high- or medium-severity
  outage on this exact instrument makes a retry near-worthless; the case is held
  and re-checked. Capped at 3 holds, after which the agent stops waiting on a
  broken rail and offers the customer a working one.

**Recurring debits**
- `npci_debit_window` — mandate presentment only in NPCI non-peak windows
  (before 10:00, 13:00–17:00, after 21:30). Outside them, deferred.
- `emandate_pre_debit_notice` — a pre-debit notification at least 24h ahead.
  Below the AFA ceiling this is a deferral (send the notice); above it, only the
  customer can re-authenticate, so the case is escalated with
  `emandate_requires_customer_afa`.

**Customer contact**
- `customer_contactable` — opt-out is a hard stop.
- `contact_targets_the_right_party` — when the failure's `who_must_act` is
  `merchant` or `engineering`, customer contact is refused. The customer cannot
  enable a disabled payment method; messaging them is an actively wrong action.
- `contact_budget` — 3 per case.
- `promise_to_pay_hold` — a customer who has committed to a date is not chased
  before it. Accepting a commitment and then chasing anyway is worse than not
  accepting it.
- `contact_cooldown` — 20h between messages.
- `rbi_contact_window` — 08:00–19:00 local. Outside it, deferred to 08:00.

**Money**
- `autonomous_amount_ceiling` — above the applicable ceiling, approval required.
- `run_exposure_cap` — the circuit breaker.

**Escalation and closure**
- `collections_threshold` — collections is not a first resort.
- `recovery_window` — 14 days from the failure.
- `no_duplicate_successful_action` — the same successful action is not executed
  twice on a case.

## Deny-for-now versus deny-forever

A contact attempted at 22:14 is not a dead case; it is a case that must wait
until 08:00. A fourth retry *is* a dead case.

Rules that refuse temporarily set `reschedule_at`. Rules that refuse permanently
set `stop_reason`. **A permanent denial dominates a temporary one** — without
that precedence, a case whose retry can never succeed gets rescheduled forever by
an unrelated timing rule. That bug existed, and looped one case through 130
refused retries before it was found.

## Budgets bound avenues, not cases

Exhausting the contact budget stops *contacting*, not the case. A customer who
has had three messages may still have a retry left, and closing the case there
writes off collectable revenue. The orchestrator switches to the remaining
avenue and only stops with `all_recovery_avenues_exhausted` when nothing is left
to try.

## Stop reasons

Every terminal case carries one, and they are rendered in the UI:

`recovered` · `max_retry_attempts_reached` · `max_contacts_reached` ·
`all_recovery_avenues_exhausted` · `recovery_window_expired` ·
`customer_opted_out` · `already_settled` ·
`risk_decline_requires_human_review` · `emandate_requires_customer_afa` ·
`not_a_customer_resolvable_failure` · `run_exposure_cap_reached` ·
`no_intervention_worth_taking` · `adapter_cannot_execute` ·
`escalate_to_merchant_ops_completed` · `open_engineering_ticket_completed` ·
`suppress_case_completed` · `<action>_rejected_by_merchant`

## Tuning

Every value above lives in `POLICY` in `munshi/policy.py` and can be overridden
per `PolicyEngine` instance. The regulatory constants in `compliance.py` are
separated from merchant policy on purpose: one of them is yours to change.
