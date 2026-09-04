import { useEffect, useState } from "react";
import { api, type ActivityItem, type ToolCall } from "../api";
import { Empty, Pill, Skeleton, Verdict } from "./primitives";
import { STATE_LABEL, STATE_TOKEN, humanise, rupees, rupeesShort } from "../format";

/** What the agent actually did, one row per decision.
 *
 *  This is the difference between a dashboard that reports outcomes and one that
 *  shows work. Each row is the whole chain: why this case came up the queue, which
 *  tools the agent reached for and what came back, what it concluded, what the
 *  policy engine did about it, and what happens next.
 *
 *  All of it is read back out of the hash-chained audit trail, so what you are
 *  watching and what is tamper-evident are the same records. */

const DECISION_TONE: Record<string, string> = {
  allow: "recovered", deny: "escalated", require_approval: "held",
};
const DECISION_LABEL: Record<string, string> = {
  allow: "ALLOWED", deny: "BLOCKED", require_approval: "NEEDS APPROVAL",
};

export function ActivityStream({ running, onSelect }:
  { running: boolean; onSelect: (id: string) => void }) {
  const [items, setItems] = useState<ActivityItem[] | null>(null);
  const [onlyBlocked, setOnlyBlocked] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = () => api.activity({ limit: 60 })
      .then((r) => !cancelled && setItems(r.activity)).catch(() => {});
    load();
    if (!running) return () => { cancelled = true; };
    const t = setInterval(load, 700);
    return () => { cancelled = true; clearInterval(t); };
  }, [running]);

  if (!items) return <div className="p-4"><Skeleton rows={8} /></div>;

  const shown = onlyBlocked
    ? items.filter((i) => i.policy && i.policy.decision !== "allow")
    : items;

  if (!items.length) {
    return (
      <Empty
        title="The agent has not decided anything yet"
        hint="Press Run recovery batch. Every decision it takes appears here: the tools it
              used, what it concluded, and what the policy engine allowed or blocked."
      />
    );
  }

  return (
    <>
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <button
          onClick={() => setOnlyBlocked((v) => !v)}
          aria-pressed={onlyBlocked}
          className={`rounded-[var(--radius-sm)] px-2 py-1 text-[length:var(--text-2xs)]
            font-medium transition-colors duration-150
            ${onlyBlocked ? "bg-[var(--escalated-soft)] text-[var(--escalated-ink)]"
                          : "text-[var(--ink-2)] hover:bg-[var(--surface-2)]"}`}
        >
          Only what policy stopped
        </button>
        <span className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
          {shown.length} of {items.length} decisions
        </span>
        {running && (
          <span className="ml-auto flex items-center gap-1.5 text-[length:var(--text-2xs)]
                           text-[var(--ink-3)]">
            <span className="size-1.5 animate-pulse rounded-full bg-[var(--brand)]" />
            live
          </span>
        )}
      </div>
      <ol className="divide-y">
        {shown.map((a) => <Row key={a.seq} item={a} onSelect={onSelect} />)}
      </ol>
    </>
  );
}

function Row({ item, onSelect }: { item: ActivityItem; onSelect: (id: string) => void }) {
  const { case: c, diagnosis: d, agent, policy, execution } = item;
  const recovered = execution?.outcome === "success" && (execution.recovered_paise ?? 0) > 0;

  return (
    <li
      className="cursor-pointer px-4 py-3 transition-colors duration-150
                 hover:bg-[var(--surface)]"
      onClick={() => onSelect(c.id)}
    >
      {/* Header: who, how much, and why it came up the queue */}
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="mono text-[length:var(--text-2xs)] text-[var(--ink-3)]">{c.id}</span>
        <span className="text-[length:var(--text-xs)] font-medium">{c.customer_name}</span>
        <span className="tnum text-[length:var(--text-xs)] font-semibold">
          {rupees(c.amount_paise)}
        </span>
        {c.error_reason && (
          <code className="mono rounded-[var(--radius-xs)] bg-[var(--surface-2)] px-1.5
                           text-[length:var(--text-2xs)]">
            {c.error_reason}
          </code>
        )}
        <span className="ml-auto flex items-center gap-2">
          {item.priority && (
            <span className="tnum text-[length:var(--text-2xs)] text-[var(--ink-3)]"
                  title={item.priority.explanation}>
              priority {rupeesShort(item.priority.expected_recoverable_paise)}
            </span>
          )}
          <Pill tone={STATE_TOKEN[c.state]}>{STATE_LABEL[c.state]}</Pill>
        </span>
      </div>

      {/* What it looked at */}
      {agent && agent.tools.length > 0 && (
        <ul className="mt-1.5 space-y-0.5">
          {agent.tools.filter((t) => t.tool !== "submit_decision").map((t, i) => (
            <li key={i} className="flex items-baseline gap-1.5
                                   text-[length:var(--text-2xs)] leading-snug">
              <span className="text-[var(--ink-3)]">→</span>
              <code className="mono text-[var(--brand-ink)]">{t.tool}</code>
              <span className="min-w-0 truncate text-[var(--ink-2)]">{summarise(t)}</span>
            </li>
          ))}
        </ul>
      )}
      {agent?.outcome === "degraded" && (
        <p className="mt-1.5 text-[length:var(--text-2xs)]"
           style={{ color: "var(--at-risk-ink)" }}>
          model unavailable ({agent.degrade_reason}) — fell back to the deterministic reasoner
        </p>
      )}

      {/* What it concluded */}
      {d.root_cause && (
        <p className="mt-1.5 text-[length:var(--text-xs)] leading-relaxed text-[var(--ink-2)]">
          <code className="mono text-[var(--ink)]">{d.root_cause}</code>
          {d.confidence != null && (
            <span className="tnum text-[var(--ink-3)]"> ({d.confidence.toFixed(2)})</span>
          )}
          {" — "}{d.rationale}
        </p>
      )}

      {/* What policy did about it: the contrast that matters */}
      {policy && (
        <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1">
          <code className="mono text-[length:var(--text-xs)] font-semibold">
            {policy.action}
          </code>
          <span className="rounded-[var(--radius-xs)] border px-1
                           text-[length:var(--text-2xs)] text-[var(--ink-3)]">
            L{policy.tier}
          </span>
          <span className="text-[var(--ink-3)]">→</span>
          <span
            className="rounded-[var(--radius-xs)] px-1.5 py-0.5
                       text-[length:var(--text-2xs)] font-bold tracking-[0.02em]"
            style={{ background: `var(--${DECISION_TONE[policy.decision]}-soft)`,
                     color: `var(--${DECISION_TONE[policy.decision]}-ink)` }}
          >
            {DECISION_LABEL[policy.decision]}
          </span>
          {policy.failed_rules.length > 0 && (
            <span className="flex items-center gap-1">
              <Verdict passed={false} />
              <code className="mono text-[length:var(--text-2xs)]">
                {policy.failed_rules[0].rule}
              </code>
            </span>
          )}
          <span className="tnum text-[length:var(--text-2xs)] text-[var(--ink-3)]">
            {policy.rules_evaluated} rules
          </span>
          {recovered && (
            <span className="tnum ml-auto text-[length:var(--text-xs)] font-semibold"
                  style={{ color: "var(--recovered-ink)" }}>
              + {rupees(execution!.recovered_paise!)}
            </span>
          )}
        </div>
      )}

      {policy?.failed_rules.length ? (
        <p className="mt-1 text-[length:var(--text-2xs)] leading-snug text-[var(--ink-3)]">
          {policy.failed_rules[0].detail}
        </p>
      ) : null}

      {item.next && (
        <p className="mt-1 text-[length:var(--text-2xs)] text-[var(--ink-3)]">
          {item.next}
        </p>
      )}
    </li>
  );
}

/** One line per tool result. The full payload lives in the case drawer. */
function summarise(t: ToolCall): string {
  const r = t.result ?? {};
  switch (t.tool) {
    case "get_downtime_status":
      return r.state === "active"
        ? `${r.severity} severity outage active on this instrument`
        : `no active outage (${humanise(r.state)})`;
    case "check_policy":
      return `${r.action_type} would be ${String(r.decision).toUpperCase()}` +
        (r.failed_rules?.length ? ` — ${r.failed_rules[0].rule}` : "");
    case "get_customer_context":
      return `${humanise(r.segment)}, ${r.successful_payments} prior successes` +
        (r.contact_opt_out ? ", opted out of contact" : "");
    case "calculate_recovery_score":
      return r.explanation ?? "";
    case "get_recovery_history":
      return `${r.attempts_used}/${(r.attempts_used ?? 0) + (r.attempts_remaining ?? 0)} ` +
        `retries and ${r.contacts_used} messages already spent`;
    case "get_payment_history":
      return `${(r.other_cases ?? []).length} other cases for this payer`;
    case "get_failure_semantics":
      return `${r.family} — ${r.retry_on_same_instrument_is_futile
        ? "retry cannot succeed" : "retryable"}`;
    default:
      return r.error ? String(r.error) : "";
  }
}
