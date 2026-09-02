# Security

Treated as a financial system: the interesting question is not "can it be
attacked" but "what is the worst thing it can do when it is".

## Credentials

Every secret comes from the environment, read at construction time rather than at
import (a `.env` file or a test fixture must be able to change it — that was a
real bug). `.env` is git-ignored; `.env.example` documents each variable and what
it unlocks. Nothing is committed. `git log -p` contains no keys.

The demo runs completely with all of them unset.

## Authentication and authorization

- Read routes are open. This is a merchant dashboard, and the read surface
  contains no credentials.
- **Every mutating route requires a bearer token**, compared with
  `hmac.compare_digest`. A token check that leaks timing is not a check.
- The webhook route authenticates by HMAC instead, not by bearer token.
- Rate limiting is a per-client sliding window, default 120 requests/minute,
  applied to mutating routes.
- The token is generated per process if `MUNSHI_API_TOKEN` is unset, so a
  default-deploy has no default credential.

## Webhook handling

Three properties, in order:

1. **Verify before parsing.** The signature covers the raw request bytes.
   Parsing the JSON and re-serialising it changes the bytes and the signature can
   never match again, so verification runs on `await request.body()` before
   anything touches it. Compared in constant time.
2. **Reject unsigned traffic outright.** With no `RAZORPAY_WEBHOOK_SECRET`
   configured the endpoint refuses everything. An unauthenticated write path into
   a system that moves money is worse than no webhook at all.
3. **Be idempotent, and return 200 on a duplicate.** Razorpay retries until it
   gets a 2xx, so returning an error on a redelivery would make it send the event
   again. A verified duplicate is recorded and acknowledged.

## Idempotency

At-least-once delivery must not become at-least-once charging.

- Ingest is keyed on the provider event id. The batch generator replays ~3% of
  events verbatim so this path is exercised by the batch itself, not only by a
  unit test.
- Executable actions carry an idempotency key of
  `case | action | attempt-state`, so a redelivered event cannot produce a second
  charge. Refused actions key on the instant, so the refusal is still recorded.
- The `actions` table has a `UNIQUE` constraint on the key.
- `ledger` has `UNIQUE (case_id, provider_ref)`, so a double-settled outcome
  cannot double-count money.
- The Razorpay adapter passes the key as `reference_id`, which Razorpay itself
  enforces as unique per payment link — a redelivery cannot mint a second link.
- `no_duplicate_successful_action` refuses re-running an action that already
  succeeded on a case.

## Financial bounds

The controls that matter, in the order they bite:

1. `write_off` is tier L4 — outside the agent's reach entirely.
2. Commercial concessions and collections escalation are tier L3 — a human
   decides, regardless of amount.
3. Re-presentments above ₹2,00,000 require approval.
4. E-mandate debits above the ₹15,000 AFA-free ceiling cannot be presented at
   all — only the customer can re-authenticate.
5. A per-run circuit breaker caps distinct value in flight at ₹2Cr.
6. 3 retries and 3 messages per case, with cooldowns.
7. Risk-declined payments are hard-stopped and escalated.

A single bug or a fully-compromised model cannot exceed these, because none of
them is reachable from the reasoning layer.

## Prompt injection

Customer-controlled text (names, VPAs, entity ids) reaches the model's context.
The defence is that a successful injection buys nothing:

- The output schema is a closed enum. There is no free-text action.
- Tiers come from a static table, not from the response.
- Retryability, budgets, windows and ceilings are all applied after the model
  returns.
- Everything attempted is in the hash-chained audit trail.

At worst an attacker gets a differently-timed *legitimate* action within the
merchant's own limits.

## Audit integrity

`hash_n = sha256(prev_hash ‖ canonical_json(row_n))`. Editing or deleting any row
invalidates every hash after it; `verify()` reports the first break and the
dashboard recomputes it on load. Four tests tamper with the log and assert
verification fails.

Raw model chain-of-thought is not persisted. What is stored is the short
structured rationale the model was asked to emit.

## Data handling

- `cases.latent` is simulator ground truth. It is stripped from every API
  response, asserted by a test that greps the serialised payload for each field
  name.
- The API serves customer names, emails and phone numbers because a merchant
  dashboard needs them. They are synthetic in this build (`@example.com`).
- Money is `INTEGER` paise end to end. No floats in the money path.
- SQLite runs in WAL mode with foreign keys on and a busy timeout, so the
  background batch and the dashboard can run concurrently without surfacing
  `database is locked`.

## Refusing to fake execution

`UnsupportedInTestMode` is a deliberate, loud failure. When Razorpay test mode
cannot genuinely perform an action — re-presenting a charge needs a
customer-authorised mandate token test mode cannot mint — the adapter raises, and
the executor records the action as `not_executed` with that reason.

The alternative, quietly simulating the call and reporting it as if Razorpay had
run it, is the exact failure mode this project exists to avoid.

The live adapter also refuses to construct against any key id that is not
`rzp_test_*`, and `MUNSHI_ADAPTER=razorpay_test` requires both an explicit
setting and credentials — it can never be selected by accident.

## Not implemented

Honest about scope:

- **No multi-tenancy.** One merchant per deployment. A production version needs a
  merchant id on every table and row-level scoping.
- **No user accounts or roles.** A single shared bearer token; approvals are
  attributed to `"merchant"`, not to a person. Real approval workflow needs
  identity.
- **No secrets manager.** Environment variables only.
- **No TLS termination or CORS policy.** Expected to sit behind a reverse proxy.
- **Rate limiting is in-process**, so it does not survive a restart or span
  replicas.
