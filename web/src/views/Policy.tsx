import { useEffect, useState } from "react";
import { api } from "../api";
import { Panel, Pill, Skeleton } from "../components/primitives";
import { humanise } from "../format";

const TIER_TONE: Record<string, string> = {
  L0: "suppressed", L1: "stopped", L2: "recovered", L3: "held", L4: "escalated",
};

const LIMIT_LABEL: Record<string, string> = {
  max_recovery_attempts: "Retries per case",
  max_customer_contacts: "Messages per case",
  min_hours_between_retries: "Minimum hours between retries",
  min_hours_between_contacts: "Minimum hours between messages",
  recovery_window_days: "Recovery window (days)",
  max_autonomous_retry_paise: "Largest autonomous re-presentment",
  max_autonomous_action_paise: "AFA-free e-mandate ceiling",
  max_run_exposure_paise: "Circuit breaker: value in flight per run",
  escalate_to_collections_min_paise: "Collections escalation floor",
  escalate_to_collections_min_days_overdue: "Collections escalation, days overdue",
};
const RUPEE_KEYS = new Set(["max_autonomous_retry_paise", "max_autonomous_action_paise",
  "max_run_exposure_paise", "escalate_to_collections_min_paise"]);

/** The bounds, published. A merchant should be able to read exactly what the
 *  agent may do without reading the source. */
export function Policy() {
  const [p, setP] = useState<any>(null);
  useEffect(() => { api.policy().then(setP); }, []);
  if (!p) return <Skeleton rows={14} />;

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Autonomy tiers"
             meta="The tier of an action is a property of the action, not something a model argues for">
        <ul className="divide-y">
          {Object.entries(p.tiers).map(([tier, v]: [string, any]) => (
            <li key={tier} className="flex flex-wrap items-start gap-x-4 gap-y-1 py-3
                                      first:pt-0 last:pb-0">
              <div className="w-32 shrink-0">
                <Pill tone={TIER_TONE[tier]}>{tier} · {v.name}</Pill>
              </div>
              <p className="min-w-[24ch] flex-1 text-[length:var(--text-xs)]
                            text-[var(--ink-2)]">
                {v.description}
              </p>
              <div className="flex flex-wrap gap-1">
                {v.actions.map((a: string) => (
                  <code key={a} className="mono rounded-[var(--radius-xs)] bg-[var(--surface-2)]
                                           px-1.5 py-0.5 text-[length:var(--text-2xs)]">
                    {a}
                  </code>
                ))}
              </div>
            </li>
          ))}
        </ul>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Merchant limits" meta="enforced deterministically, outside the model's reach">
          <dl className="divide-y">
            {Object.entries(p.limits).map(([k, v]: [string, any]) => (
              <div key={k} className="flex items-baseline justify-between gap-4 py-2
                                      first:pt-0 last:pb-0">
                <dt className="text-[length:var(--text-xs)] text-[var(--ink-2)]">
                  {LIMIT_LABEL[k] ?? humanise(k)}
                </dt>
                <dd className="tnum shrink-0 text-[length:var(--text-xs)] font-semibold">
                  {RUPEE_KEYS.has(k)
                    ? `₹${(v / 100).toLocaleString("en-IN")}`
                    : String(v)}
                </dd>
              </div>
            ))}
          </dl>
        </Panel>

        <Panel title="Regulatory envelope"
               meta="implemented from published guidance; not legal advice">
          <dl className="divide-y">
            {Object.entries(p.regulatory).map(([k, v]: [string, any]) => (
              <div key={k} className="py-2 first:pt-0 last:pb-0">
                <dt className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                  {humanise(k)}
                </dt>
                <dd className="mt-0.5 text-[length:var(--text-xs)]">{String(v)}</dd>
              </div>
            ))}
          </dl>
        </Panel>
      </div>

      <Panel title="Razorpay error_source semantics"
             meta="Razorpay's own guidance on who must act, used as a routing signal">
        <dl className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Object.entries(p.razorpay_error_sources).map(([k, v]: [string, any]) => (
            <div key={k}>
              <dt className="mono text-[length:var(--text-xs)] font-semibold
                             text-[var(--brand-ink)]">
                {k}
              </dt>
              <dd className="mt-1 text-[length:var(--text-2xs)] leading-relaxed
                             text-[var(--ink-2)]">
                {v}
              </dd>
            </div>
          ))}
        </dl>
      </Panel>

      <Panel title="Recovery families"
             meta="65 Razorpay reason codes, grouped by what would have to change for a retry to work">
        <ul className="divide-y">
          {Object.entries(p.failure_families).map(([k, v]: [string, any]) => (
            <li key={k} className="py-3 first:pt-0 last:pb-0">
              <div className="flex flex-wrap items-center gap-2">
                <span className="mono text-[length:var(--text-xs)] font-semibold">{k}</span>
                <Pill tone={["never", "new_instrument_only"].includes(v.retryability)
                  ? "escalated" : "recovered"}>
                  {humanise(v.retryability)}
                </Pill>
                <span className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                  → {v.default_intervention}
                </span>
              </div>
              <p className="mt-1 max-w-[92ch] text-[length:var(--text-xs)] leading-relaxed
                            text-[var(--ink-2)]">
                {v.rationale}
              </p>
            </li>
          ))}
        </ul>
      </Panel>
    </div>
  );
}
