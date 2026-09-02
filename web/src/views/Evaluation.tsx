import { useEffect, useState } from "react";
import { api } from "../api";
import { Empty, Panel, Skeleton } from "../components/primitives";
import { rupeesShort } from "../format";

const ARM_LABEL: Record<string, string> = {
  baseline: "Fixed retry ladder",
  "agent-heuristic": "Munshi, unattended",
  "agent-heuristic-approved": "Munshi + merchant approvals",
  "agent-llm": "Munshi, Claude reasoner",
};

/** The batch comparison, read from the committed evaluation run.
 *
 *  The ladder recovers more gross revenue than the unattended agent, and that is
 *  shown here rather than hidden, because the interesting number is what it cost
 *  to get it. */
export function Evaluation() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.evaluation().then(setData).catch((e) => setErr(String(e.message ?? e)));
  }, []);

  if (err) {
    return (
      <Empty
        title="No evaluation results committed yet"
        hint="Run `python -m munshi.evaluation.harness --arms baseline,agent-heuristic,agent-heuristic-approved`
              to produce evaluation/results.json, then reload."
      />
    );
  }
  if (!data) return <Skeleton rows={12} />;

  const arms: string[] = Object.keys(data.arms);
  const first = data.arms[arms[0]];
  const b = first.batch;

  const row = (label: string, fn: (m: any) => string, emphasis = false) => (
    <tr key={label} className="border-t">
      <th scope="row" className={`px-3 py-2 text-left text-[length:var(--text-xs)]
        ${emphasis ? "font-semibold" : "font-normal text-[var(--ink-2)]"}`}>
        {label}
      </th>
      {arms.map((a) => (
        <td key={a} className={`tnum px-3 py-2 text-right text-[length:var(--text-xs)]
          ${emphasis ? "font-semibold" : ""}`}>
          {fn(data.arms[a])}
        </td>
      ))}
    </tr>
  );

  return (
    <div className="flex flex-col gap-4">
      <Panel title="Batch">
        <p className="max-w-[86ch] text-[length:var(--text-xs)] leading-relaxed
                      text-[var(--ink-2)]">
          {b.n} revenue-risk cases, seed <code className="mono">{b.seed}</code>,{" "}
          {b.recovery_window_days}-day window. Every arm runs the <b>same cases with the same
          latent ground truth and the same per-case seeds</b> through the same outcome oracle;
          only the choice of action and its timing differ. A rupee counts as recovered only
          when a ledger row exists pointing at the action that caused it.
        </p>
        <p className="mt-3 max-w-[86ch] rounded-[var(--radius-sm)] px-3 py-2
                      text-[length:var(--text-xs)] leading-relaxed"
           style={{ background: "var(--brand-soft)", color: "var(--brand-ink)" }}>
          <b className="tnum">{b.structurally_unretryable_cases}</b> of{" "}
          <b className="tnum">{b.cases_with_failure_code}</b> cases carrying a Razorpay failure
          code ({b.structurally_unretryable_share}%, {b.share_of_failed_value}% of failed value
          = {rupeesShort(b.structurally_unretryable_paise)}) are structurally unretryable. A
          fixed ladder spends attempts on all of it.
        </p>
      </Panel>

      <Panel flush title="What each approach recovered">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse">
            <thead>
              <tr className="bg-[var(--surface)]">
                <th className="px-3 py-2 text-left text-[length:var(--text-2xs)]
                               font-medium text-[var(--ink-3)]">Metric</th>
                {arms.map((a) => (
                  <th key={a} className="px-3 py-2 text-right text-[length:var(--text-xs)]
                                         font-semibold">
                    {ARM_LABEL[a] ?? a}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {row("Revenue recovered", (m) => rupeesShort(m.money.recovered_paise), true)}
              {row("Recovery rate (of at-risk)",
                (m) => `${m.money.recovery_rate_of_at_risk}%`)}
              {row("Cases recovered",
                (m) => `${m.cases.recovered}/${m.cases.total}`)}
              {row("Held for merchant approval",
                (m) => rupeesShort(m.money.held_for_approval_paise))}
              {row("Annualised MRR protected",
                (m) => rupeesShort(m.money.annualised_mrr_protected_paise))}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel flush title="What it cost to get there"
             meta="the ladder's extra gross revenue, itemised">
        <div className="overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse">
            <thead>
              <tr className="bg-[var(--surface)]">
                <th className="px-3 py-2 text-left text-[length:var(--text-2xs)]
                               font-medium text-[var(--ink-3)]">Metric</th>
                {arms.map((a) => (
                  <th key={a} className="px-3 py-2 text-right text-[length:var(--text-2xs)]
                                         font-medium text-[var(--ink-3)]">
                    {ARM_LABEL[a] ?? a}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {row("Actions executed", (m) => String(m.actions.executed))}
              {row("Retries spent", (m) => String(m.actions.retries))}
              {row("Retries with zero possible yield",
                (m) => `${m.quality.wasted_retries} (${m.quality.wasted_retry_rate}%)`, true)}
              {row("Customers chased after paying",
                (m) => String(m.quality.customers_chased_after_paying), true)}
              {row("Opted-out customers contacted",
                (m) => String(m.quality.opted_out_customers_contacted), true)}
              {row("RBI contact-window violations",
                (m) => String(m.compliance.rbi_contact_window_violations), true)}
              {row("NPCI debit-window violations",
                (m) => String(m.compliance.npci_debit_window_violations))}
              {row("Intervention accuracy",
                (m) => `${m.quality.intervention_accuracy}%`)}
              {row("Diagnosis accuracy", (m) => `${m.quality.diagnosis_accuracy}%`)}
            </tbody>
          </table>
        </div>
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        <Panel title="Bounds held">
          <table className="w-full border-collapse">
            <tbody>
              {row("Cases over the 3-retry cap",
                (m) => String(m.stopping.cases_over_retry_cap))}
              {row("Cases over the 3-contact cap",
                (m) => String(m.stopping.cases_over_contact_cap))}
              {row("Every case terminal", (m) => (m.cases.all_terminal ? "yes" : "no"))}
              {row("Audit chain verifies", (m) => (m.run.audit.valid ? "yes" : "NO"))}
              {row("Audit records", (m) => String(m.run.audit.checked))}
            </tbody>
          </table>
        </Panel>

        <Panel title="Why Munshi stopped"
               meta={ARM_LABEL[arms.find((a) => a.startsWith("agent")) ?? arms[0]]}>
          <ul className="space-y-1.5">
            {Object.entries(
              data.arms[arms.find((a) => a.startsWith("agent")) ?? arms[0]].stopping.by_reason,
            )
              .sort((a: any, b: any) => b[1] - a[1])
              .map(([reason, n]: any) => (
                <li key={reason} className="flex items-baseline gap-2">
                  <span className="mono text-[length:var(--text-2xs)]">{reason}</span>
                  <span className="h-px flex-1 bg-[var(--line)]" />
                  <span className="tnum text-[length:var(--text-xs)] font-medium">{n}</span>
                </li>
              ))}
          </ul>
        </Panel>
      </div>
    </div>
  );
}
