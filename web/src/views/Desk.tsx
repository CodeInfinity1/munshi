import { useEffect, useMemo, useState } from "react";
import { api, type Approval, type Case, type Health, type Overview } from "../api";
import { AllocationBar } from "../components/AllocationBar";
import { ApprovalQueue } from "../components/ApprovalQueue";
import { CaseDrawer } from "../components/CaseDrawer";
import { CaseTable } from "../components/CaseTable";
import { RunBar } from "../components/RunBar";
import { Panel } from "../components/primitives";
import { rupeesShort } from "../format";

const FILTERS = [
  { label: "All", state: "" },
  { label: "Recovered", state: "recovered" },
  { label: "Awaiting approval", state: "awaiting_approval" },
  { label: "Escalated", state: "escalated" },
  { label: "Stopped", state: "stopped" },
  { label: "Suppressed", state: "suppressed" },
];

export function Desk({
  overview, health, refresh,
}: { overview: Overview | null; health: Health | null; refresh: () => void }) {
  const [cases, setCases] = useState<Case[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [filter, setFilter] = useState("");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);

  const running = overview?.run_state.status === "running";

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api.cases({ state: filter, q })
      .then((r) => !cancelled && setCases(r.cases))
      .finally(() => !cancelled && setLoading(false));
    api.approvals().then((r) => !cancelled && setApprovals(r.approvals));
    return () => { cancelled = true; };
  }, [filter, q, overview?.run_state.status, overview?.money.recovered_paise]);

  const m = overview?.money;
  const counts = overview?.cases.by_state ?? {};

  const headline = useMemo(() => {
    if (!m || !overview) return null;
    return {
      atRisk: m.at_risk_paise,
      recovered: m.recovered_paise,
      held: m.held_for_approval_paise,
      cases: overview.cases.total,
    };
  }, [m, overview]);

  return (
    <div className="flex flex-col gap-4">
      <RunBar health={health} overview={overview} onChange={refresh} />

      <Panel
        title="The book"
        meta={headline
          ? `${headline.cases} cases · ${rupeesShort(headline.atRisk)} at risk`
          : undefined}
        actions={
          overview && (
            <span className="tnum text-[length:var(--text-2xs)] text-[var(--ink-3)]">
              {overview.batch.structurally_unretryable_share}% of failures are structurally
              unretryable ({rupeesShort(overview.batch.structurally_unretryable_paise)})
            </span>
          )
        }
      >
        <AllocationBar
          moneyByState={overview?.cases.money_by_state ?? {}}
          countByState={counts}
          total={m?.at_risk_paise ?? 0}
        />
        {overview && (
          <dl className="mt-4 grid grid-cols-2 gap-x-6 gap-y-3 border-t pt-4 sm:grid-cols-4">
            <Stat label="Recovered" value={rupeesShort(m!.recovered_paise)}
                  sub={`${m!.recovery_rate_of_at_risk}% of at-risk`} tone="recovered" />
            <Stat label="Held for you" value={rupeesShort(m!.held_for_approval_paise)}
                  sub={`${counts.awaiting_approval ?? 0} actions`} tone="held" />
            <Stat label="Retries with zero possible yield"
                  value={String(overview.quality.wasted_retries)}
                  sub={`of ${overview.actions.retries} retries spent`} tone="stopped" />
            <Stat label="Customers chased after paying"
                  value={String(overview.quality.customers_chased_after_paying)}
                  sub={`${overview.quality.opted_out_customers_contacted} opted-out contacted`}
                  tone="stopped" />
          </dl>
        )}
      </Panel>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <Panel
          flush
          title="Cases"
          meta={`${cases.length} shown`}
          actions={
            <div className="flex items-center gap-2">
              <input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Search customer, entity, reason"
                aria-label="Search cases"
                className="w-52 rounded-[var(--radius-sm)] border bg-[var(--bg)] px-2 py-1
                           text-[length:var(--text-xs)] text-[var(--ink)]
                           placeholder:text-[var(--ink-3)]"
              />
            </div>
          }
        >
          <div className="flex flex-wrap gap-1 border-b px-3 py-2">
            {FILTERS.map((f) => (
              <button
                key={f.label}
                onClick={() => setFilter(f.state)}
                aria-pressed={filter === f.state}
                className={`rounded-[var(--radius-sm)] px-2 py-1 text-[length:var(--text-2xs)]
                  font-medium transition-colors duration-150
                  ${filter === f.state
                    ? "bg-[var(--brand-soft)] text-[var(--brand-ink)]"
                    : "text-[var(--ink-2)] hover:bg-[var(--surface-2)]"}`}
              >
                {f.label}
                {f.state && counts[f.state] != null && (
                  <span className="tnum ml-1 text-[var(--ink-3)]">{counts[f.state]}</span>
                )}
              </button>
            ))}
          </div>
          <div className="max-h-[62vh] overflow-y-auto">
            <CaseTable cases={cases} loading={loading && !running}
                       selected={selected} onSelect={setSelected} />
          </div>
        </Panel>

        <Panel flush title="Needs a human"
               meta={approvals.filter((a) => !a.decided_at).length
                 ? `${approvals.filter((a) => !a.decided_at).length} pending` : undefined}>
          <div className="max-h-[62vh] overflow-y-auto">
            <ApprovalQueue
              approvals={approvals}
              heldPaise={m?.held_for_approval_paise ?? 0}
              onChange={refresh}
            />
          </div>
        </Panel>
      </div>

      {selected && <CaseDrawer caseId={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}

function Stat({ label, value, sub, tone }: {
  label: string; value: string; sub: string; tone: string;
}) {
  return (
    <div>
      <dt className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">{label}</dt>
      <dd className="tnum mt-0.5 text-[length:var(--text-2xl)] font-semibold
                     tracking-[-0.022em] leading-none"
          style={{ color: `var(--${tone}-ink)` }}>
        {value}
      </dd>
      <dd className="tnum mt-1 text-[length:var(--text-2xs)] text-[var(--ink-3)]">{sub}</dd>
    </div>
  );
}
