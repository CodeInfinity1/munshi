/** Typed client for the Munshi API. One place that knows about the wire format. */

export type CaseState =
  | "open" | "scheduled" | "awaiting_approval"
  | "recovered" | "stopped" | "escalated" | "suppressed" | "settled_externally";

export interface ToolCall {
  tool: string;
  arguments: Record<string, unknown>;
  result: Record<string, any>;
}

export interface PriorityScore {
  case_id: string;
  amount_paise: number;
  probability: number;
  urgency: number;
  expected_recoverable_paise: number;
  remaining_window_days: number;
  factors: Record<string, number>;
  explanation: string;
  state?: string;
  error_reason?: string | null;
}

export interface ActivityItem {
  seq: number;
  ts: number;
  case: { id: string; amount_paise: number; state: CaseState; error_reason: string | null;
          kind: string; method: string | null; stop_reason: string | null;
          recovered_paise: number; customer_name: string; segment: string };
  priority: PriorityScore | null;
  diagnosis: { root_cause: string | null; confidence: number | null;
               recoverability: number | null; rationale: string | null;
               evidence: string[] | null; reasoner: string | null; model: string | null };
  agent: { provider: string; model: string; turns: number; outcome: string;
           degrade_reason: string | null; tools: ToolCall[] } | null;
  policy: { action: string; tier: number; decision: "allow" | "deny" | "require_approval";
            stop_reason: string | null; justification: string | null;
            delay_hours: number | null; failed_rules: PolicyRule[];
            rules_evaluated: number } | null;
  execution: { outcome: string | null; summary?: string; channel: string | null;
               message: string | null; simulated: boolean | null;
               adapter: string | null; recovered_paise?: number } | null;
  next: string | null;
}

export interface Case {
  id: string;
  kind: string;
  entity_id: string;
  customer_id: string;
  customer_name: string;
  segment: string;
  amount_paise: number;
  currency: string;
  opened_at: number;
  state: CaseState;
  method: string | null;
  instrument: Record<string, string>;
  error_source: string | null;
  error_reason: string | null;
  attempts: number;
  prior_attempts: number;
  contacts_sent: number;
  days_overdue: number;
  mrr_paise: number;
  stop_reason: string | null;
  recovered_paise: number;
  next_action_at: number | null;
  promise_to_pay_at: number | null;
  contact_opt_out: number;
}

export interface PolicyRule { rule: string; passed: boolean; detail: string }

export interface Action {
  id: string;
  case_id: string;
  proposed_at: number;
  action_type: string;
  tier: number;
  params: { delay_hours?: number; channel?: string; message?: string;
            justification?: string; reasoner?: string };
  policy_decision: "allow" | "deny" | "require_approval";
  policy_rules: PolicyRule[];
  executed_at: number | null;
  outcome: string | null;
  outcome_detail: Record<string, unknown>;
  recovered_paise: number;
}

export interface AuditRecord {
  seq: number; ts: number; stage: string; summary: string;
  case_id: string | null; action_id: string | null;
  detail: Record<string, unknown>; hash: string; prev_hash: string;
}

export interface Overview {
  money: {
    at_risk_paise: number; latently_recoverable_paise: number; recovered_paise: number;
    recovery_rate_of_at_risk: number; recovery_rate_of_recoverable: number;
    held_for_approval_paise: number; escalated_paise: number; unrecovered_paise: number;
    annualised_mrr_at_risk_paise: number; annualised_mrr_protected_paise: number;
    settled_externally_paise: number;
  };
  cases: { total: number; recovered: number; by_state: Record<string, number>;
           money_by_state: Record<string, number>; all_terminal: boolean };
  actions: { proposed: number; executed: number; allowed: number; blocked_by_policy: number;
             required_approval: number; not_executed: number; retries: number;
             contacts: number; by_type: Record<string, number> };
  quality: { wasted_retries: number; wasted_retry_rate: number;
             customers_chased_after_paying: number; opted_out_customers_contacted: number;
             externally_settled_cases: number; chased_after_external_settlement: number;
             intervention_accuracy: number; diagnosis_accuracy: number;
             actions_per_case: number; contacts_per_recovered_case: number };
  stopping: { by_reason: Record<string, number>; cases_over_retry_cap: number;
              cases_over_contact_cap: number };
  approvals: { requested: number; pending: number; value_paise: number };
  attribution: { recovered_by_action: Record<string, number>;
                 recovered_by_failure_family: Record<string, number>;
                 recovered_by_segment: Record<string, number> };
  batch: { cases_with_failure_code: number; structurally_unretryable_cases: number;
           structurally_unretryable_share: number; structurally_unretryable_paise: number;
           share_of_failed_value: number };
  audit: { valid: boolean; checked: number; head?: string; broken_at?: number };
  run_state: { status: "idle" | "running" | "done" | "error";
               stats: Record<string, number>; error: string | null };
  config: { reasoner: string; llm_provider: string | null; llm_model: string | null;
            adapter: string; razorpay_credentials_present: boolean;
            agent_max_turns: number; timezone: string };
}

export interface Health {
  ok: boolean; reasoner: string; llm_provider: string | null; llm_model: string | null;
  adapter: string; money_movement: string; razorpay_credentials_present: boolean;
  agent_max_turns: number; timezone: string; database: string; seeded: boolean;
}

export interface CaseDetail {
  case: Case;
  context: Record<string, any>;
  actions: Action[];
  audit: AuditRecord[];
  ledger: { id: string; ts: number; amount_paise: number; provider_ref: string;
            adapter: string; action_id: string }[];
}

export interface Approval {
  action_id: string; case_id: string; action_type: string; tier: number;
  requested_at: number; reason: string; decided_at: number | null;
  decision: string | null; amount_paise: number; error_reason: string | null;
  kind: string; customer_name: string; params: Action["params"];
  policy_rules: PolicyRule[];
}

/** Set once from the UI; every mutating call carries it. */
let token = localStorage.getItem("munshi_token") ?? "";
export const setToken = (t: string) => {
  token = t.trim();
  localStorage.setItem("munshi_token", token);
};
export const getToken = () => token;

async function req<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(init?.method && init.method !== "GET" ? { Authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }));
    throw new Error(typeof body.detail === "string" ? body.detail : res.statusText);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => req<Health>("/api/health"),
  overview: () => req<Overview>("/api/overview"),
  policy: () => req<any>("/api/policy"),
  evaluation: () => req<any>("/api/evaluation"),
  cases: (p: { state?: string; kind?: string; q?: string } = {}) => {
    const qs = new URLSearchParams(Object.entries(p).filter(([, v]) => v) as [string, string][]);
    return req<{ cases: Case[] }>(`/api/cases?${qs}`);
  },
  caseDetail: (id: string) => req<CaseDetail>(`/api/cases/${id}`),
  audit: (p: { stage?: string; limit?: number } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(p).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]));
    return req<{ verification: Overview["audit"]; records: AuditRecord[] }>(`/api/audit?${qs}`);
  },
  approvals: () => req<{ approvals: Approval[] }>("/api/approvals"),
  activity: (p: { limit?: number; case_id?: string } = {}) => {
    const qs = new URLSearchParams(
      Object.entries(p).filter(([, v]) => v != null).map(([k, v]) => [k, String(v)]));
    return req<{ activity: ActivityItem[] }>(`/api/activity?${qs}`);
  },
  triage: (limit = 12) =>
    req<{ queue: PriorityScore[]; scored: number; note: string }>(`/api/triage?limit=${limit}`),
  run: (body: { days?: number; step_hours?: number; tick_delay_ms?: number;
                auto_approve?: boolean }) =>
    req<{ status: string }>("/api/run", { method: "POST", body: JSON.stringify(body) }),
  seed: (body: { n?: number; seed?: number } = {}) =>
    req<any>("/api/seed", { method: "POST", body: JSON.stringify(body) }),
  decide: (actionId: string, decision: "approve" | "reject") =>
    req<any>(`/api/approvals/${actionId}/${decision}`, { method: "POST" }),
};
