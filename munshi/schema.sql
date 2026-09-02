-- Munshi storage. SQLite, stdlib driver, no ORM.
-- Money is stored in PAISE as INTEGER everywhere. There are no floats in the
-- money path.

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

-- Raw inbound events, exactly as received. Append-only; the source of truth we
-- can always replay from.
CREATE TABLE IF NOT EXISTS events (
    id              TEXT PRIMARY KEY,          -- provider event id (idempotency key)
    kind            TEXT NOT NULL,             -- payment.failed, subscription.charged.failed, ...
    entity_id       TEXT NOT NULL,             -- pay_xxx / sub_xxx / inv_xxx / cart_xxx
    customer_id     TEXT NOT NULL,
    occurred_at     INTEGER NOT NULL,          -- unix seconds
    received_at     INTEGER NOT NULL,
    payload         TEXT NOT NULL,             -- json
    duplicate_of    TEXT REFERENCES events(id) -- set when we detect a replay
);
CREATE INDEX IF NOT EXISTS idx_events_entity ON events(entity_id);

CREATE TABLE IF NOT EXISTS customers (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    email                   TEXT,
    phone                   TEXT,
    segment                 TEXT NOT NULL,     -- consumer | smb | enterprise
    tenure_days             INTEGER NOT NULL,
    lifetime_paise          INTEGER NOT NULL,
    successful_payments     INTEGER NOT NULL,
    failed_payments         INTEGER NOT NULL,
    prior_recoveries        INTEGER NOT NULL,
    contact_opt_out         INTEGER NOT NULL DEFAULT 0,
    preferred_channel       TEXT NOT NULL DEFAULT 'email',
    typical_success_hour    INTEGER            -- local hour of past successful payments
);

-- One case per unit of revenue at risk. The state machine lives here.
CREATE TABLE IF NOT EXISTS cases (
    id                  TEXT PRIMARY KEY,
    kind                TEXT NOT NULL,         -- payment_failure | subscription_failure | invoice_overdue | checkout_abandoned
    entity_id           TEXT NOT NULL,
    customer_id         TEXT NOT NULL REFERENCES customers(id),
    amount_paise        INTEGER NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'INR',
    opened_at           INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL,
    state               TEXT NOT NULL,         -- see munshi.models.CaseState
    method              TEXT,                  -- card | upi | netbanking | emandate
    instrument          TEXT,                  -- json: issuer / bank / vpa_handle / network / last4
    error_source        TEXT,                  -- customer | business | gateway | razorpay
    error_step          TEXT,
    error_reason        TEXT,                  -- Razorpay reason code -> taxonomy key
    attempts            INTEGER NOT NULL DEFAULT 0,   -- recovery attempts WE made
    prior_attempts      INTEGER NOT NULL DEFAULT 0,   -- attempts before Munshi saw it
    contacts_sent       INTEGER NOT NULL DEFAULT 0,
    days_overdue        INTEGER NOT NULL DEFAULT 0,
    mrr_paise           INTEGER NOT NULL DEFAULT 0,   -- subscriptions: recurring value at risk
    next_action_at      INTEGER,               -- scheduled wake-up
    stop_reason         TEXT,
    recovered_paise     INTEGER NOT NULL DEFAULT 0,
    latent              TEXT,                  -- json: simulator ground truth. NEVER read by the agent.
    UNIQUE (kind, entity_id)                   -- one open case per at-risk entity
);
CREATE INDEX IF NOT EXISTS idx_cases_state ON cases(state);
CREATE INDEX IF NOT EXISTS idx_cases_next ON cases(next_action_at);

-- Every action the agent proposes, whether or not it was allowed to run.
CREATE TABLE IF NOT EXISTS actions (
    id                  TEXT PRIMARY KEY,
    case_id             TEXT NOT NULL REFERENCES cases(id),
    run_id              TEXT NOT NULL,
    proposed_at         INTEGER NOT NULL,
    action_type         TEXT NOT NULL,
    tier                INTEGER NOT NULL,      -- L0..L4 autonomy tier
    params              TEXT NOT NULL,         -- json
    policy_decision     TEXT NOT NULL,         -- allow | require_approval | deny
    policy_rules        TEXT NOT NULL,         -- json: every rule evaluated, with verdicts
    idempotency_key     TEXT NOT NULL UNIQUE,
    executed_at         INTEGER,
    outcome             TEXT,                  -- success | failed | pending | not_executed
    outcome_detail      TEXT,                  -- json
    recovered_paise     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_actions_case ON actions(case_id);

CREATE TABLE IF NOT EXISTS approvals (
    action_id       TEXT PRIMARY KEY REFERENCES actions(id),
    case_id         TEXT NOT NULL REFERENCES cases(id),
    requested_at    INTEGER NOT NULL,
    reason          TEXT NOT NULL,
    decided_at      INTEGER,
    decision        TEXT,                      -- approved | rejected
    decided_by      TEXT
);

-- Razorpay Payment Downtime records (payment.downtime.* webhooks / fetch API).
CREATE TABLE IF NOT EXISTS downtimes (
    id          TEXT PRIMARY KEY,
    method      TEXT NOT NULL,                 -- card | netbanking | upi
    instrument  TEXT NOT NULL,                 -- json: {issuer|bank|vpa_handle|psp|network}
    begin_at    INTEGER NOT NULL,
    end_at      INTEGER,
    status      TEXT NOT NULL,                 -- scheduled | started | updated | resolved
    severity    TEXT NOT NULL,                 -- high | medium | low
    scheduled   INTEGER NOT NULL DEFAULT 0
);

-- Append-only, hash-chained. Each row commits to the one before it, so any
-- edit or deletion breaks verification for every subsequent row.
CREATE TABLE IF NOT EXISTS audit (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          INTEGER NOT NULL,
    run_id      TEXT,
    case_id     TEXT,
    action_id   TEXT,
    stage       TEXT NOT NULL,                 -- detect | quantify | enrich | diagnose | plan | policy | execute | verify | stop
    summary     TEXT NOT NULL,
    detail      TEXT NOT NULL,                 -- json
    prev_hash   TEXT NOT NULL,
    hash        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_case ON audit(case_id);

-- Money movements. A rupee only counts as recovered if it has a row here that
-- points at the action that caused it.
CREATE TABLE IF NOT EXISTS ledger (
    id              TEXT PRIMARY KEY,
    case_id         TEXT NOT NULL REFERENCES cases(id),
    action_id       TEXT NOT NULL REFERENCES actions(id),
    ts              INTEGER NOT NULL,
    amount_paise    INTEGER NOT NULL,
    provider_ref    TEXT NOT NULL,             -- pay_xxx returned by the adapter
    adapter         TEXT NOT NULL,             -- simulator | razorpay_test
    UNIQUE (case_id, provider_ref)
);

CREATE TABLE IF NOT EXISTS runs (
    id          TEXT PRIMARY KEY,
    started_at  INTEGER NOT NULL,
    ended_at    INTEGER,
    mode        TEXT NOT NULL,                 -- agent | baseline
    reasoner    TEXT NOT NULL,                 -- llm | heuristic
    adapter     TEXT NOT NULL,
    notes       TEXT
);
