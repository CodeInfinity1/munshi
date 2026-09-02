# Agent design

## What makes this an agent rather than a prompt

The word "agent" is doing real work here only if the system decides *and acts*
under constraint, and can be held to account for both. Munshi's loop is
`detect → quantify → enrich → diagnose → plan → policy → execute → verify →
stop → measure`, run over a virtual clock so timing decisions are real. A model
appears at exactly one stage of that loop.

## Where the model runs

One call per decision, structured output against a closed JSON schema
(`output_config.format`), `claude-opus-5` by default.

It is given a fully-resolved context pack and asked for six things:

| Field | Why a model |
|---|---|
| `root_cause` | The failure code is often ambiguous. Razorpay documents `payment_failed` as *"no specific error code received from gateway"*; `card_declined` and `payment_declined` are similarly opaque. Whether a given decline is an outage, a balance problem or a dying card is a weighing of downtime state, customer history, amount and age. |
| `recoverability` | An estimate over heterogeneous evidence. Answering "this is not worth chasing" is a valuable answer, and the agent is told so explicitly. |
| `action_type` | Choosing between defensible interventions. |
| `delay_hours` | The highest-leverage field. For balance failures the whole game is *when*, not *whether*. |
| `channel` | Cheap, but genuinely context-dependent. |
| `message` | Contextual copy citing the real reason and amount, in Indian-English business register, without a template's tell. |

Plus a one-or-two-sentence `diagnosis_rationale` and `justification`, which are
written into the merchant-visible audit trail.

## What the model is explicitly told has already been decided

The system prompt states this, and the architecture enforces it:

- `failure.retry_on_same_instrument_is_futile` comes from the taxonomy. When
  true, a retry cannot succeed; propose the action that changes the precondition.
- `failure.who_must_act` says whose problem it is. When it is `merchant` or
  `engineering`, contacting the customer is an *actively wrong* action — the
  customer cannot enable a disabled payment method.
- `downtime` is Razorpay's live feed for that exact instrument.
- `compliance` reports whether the RBI contact window and NPCI debit windows are
  open right now.
- Caps, cooldowns, exposure limits and stopping rules are enforced downstream by
  a policy engine it cannot override.

## What the model is not allowed to touch

| Decision | Where it actually lives |
|---|---|
| Can a retry ever succeed? | `taxonomy.py` — a property of the failure code |
| How many retries and messages are left? | `policy.py` — counters on the case |
| Is contact permitted right now? | `compliance.py` — RBI FPC clock arithmetic |
| How much money is at risk? | `quantify.py` — integer arithmetic on paise |
| What tier is this action? | `models.ACTION_TIERS` — a static table |
| Did money move? | `ledger` — a row, or it did not happen |

## Treating the model as an untrusted input

A schema-shaped response is still an untrusted response. Every returned field is
re-validated after the call:

- `action_type` must be in `ACTION_TIERS`; `root_cause` in `ROOT_CAUSES`.
  Anything else raises and the case degrades.
- `confidence` and `recoverability` are clamped to `[0, 1]`; `delay_hours` to
  `[0, 336]`.
- An unrecognised `channel` becomes `none`.
- Any exception at all — network, refusal `stop_reason`, malformed JSON, an
  invented action — is caught, counted, and the case falls through to the
  deterministic reasoner with the rationale prefixed `[LLM unavailable: …]`.

The degraded count is reported in the run summary and in the evaluation output.
A model failure never fails a batch and never silently changes behaviour.

## The deterministic twin

`HeuristicReasoner` is a complete implementation of the same interface, driven
off the taxonomy. It exists for three reasons:

1. **The demo runs with no credentials.** A reviewer with no API key still gets
   the whole product, and the header says which reasoner produced the numbers.
2. **It is the honest control.** It is run as its own evaluation arm, so "is the
   model earning its cost, or is the taxonomy doing all the work?" is a question
   with a number attached. Every figure in this repository's committed
   evaluation is from the *deterministic* arm — the weaker claim, deliberately.
3. **It bounds the blast radius of a bad model day.** Degradation is a designed
   path, not an error path.

What the model adds over the twin, structurally: the twin maps root cause
straight off the taxonomy family and cannot notice when context contradicts the
failure code; its timing comes from a fixed backoff table; and its messages are
templates. Those are exactly the three places the model is scoped to.

## Prompt-injection posture

Customer-controlled text (names, VPAs, entity ids) reaches the context pack. The
defence is not prompt hygiene — it is that a successful injection buys nothing:

- The output schema is a closed enum. There is no free-text action.
- Every action is tier-gated by a static table.
- Retryability, caps, windows and ceilings are enforced after the model returns.
- The per-run exposure circuit breaker caps distinct value in flight regardless
  of how many actions are proposed.

A model fully controlled by an attacker can, at worst, choose a differently-timed
legitimate action within the merchant's own limits — and every attempt is in the
hash-chained audit trail.

## Cost shape

One call per decision, not per tick. The dominant cost control is *not waking
cases that cannot act*: after a failed action the case is scheduled to the
earliest instant policy could permit another, rather than a flat interval. That
single change cut a 320-case batch from 4,397 decisions to 1,516.

The system prompt is byte-stable across every case in a batch, so it caches; only
the per-case context pack is uncached input. Reasoning effort defaults to `low`
(`MUNSHI_LLM_EFFORT`) and the decision pass fans out across
`MUNSHI_LLM_CONCURRENCY` threads.
