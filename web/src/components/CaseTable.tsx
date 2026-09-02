import { useMemo, useState } from "react";
import type { Case } from "../api";
import { Empty, Pill, Skeleton } from "./primitives";
import { STATE_LABEL, STATE_TOKEN, humanise, rupees } from "../format";

const KIND_SHORT: Record<string, string> = {
  payment_failure: "Payment", subscription_failure: "Subscription",
  invoice_overdue: "Invoice", checkout_abandoned: "Checkout",
};

type SortKey = "amount_paise" | "state" | "attempts";

export function CaseTable({
  cases, loading, selected, onSelect,
}: {
  cases: Case[]; loading: boolean; selected: string | null;
  onSelect: (id: string) => void;
}) {
  const [sort, setSort] = useState<SortKey>("amount_paise");
  const [dir, setDir] = useState<1 | -1>(-1);

  const rows = useMemo(() => {
    const c = [...cases];
    c.sort((a, b) => {
      const x = a[sort], y = b[sort];
      if (typeof x === "number" && typeof y === "number") return (x - y) * dir;
      return String(x).localeCompare(String(y)) * dir;
    });
    return c;
  }, [cases, sort, dir]);

  if (loading) return <div className="p-4"><Skeleton rows={10} /></div>;
  if (!cases.length) {
    return (
      <Empty
        title="No cases match this filter"
        hint="Every revenue-risk event Munshi has ingested becomes a case. Clear the filter,
              or reset the batch to load the 320-case demo book."
      />
    );
  }

  function toggle(key: SortKey) {
    if (sort === key) setDir((d) => (d === 1 ? -1 : 1));
    else { setSort(key); setDir(-1); }
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse text-left">
        <thead className="sticky top-0 z-[var(--z-sticky)] bg-[var(--surface)]">
          <tr className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
            <Th>Customer</Th>
            <Th>Type</Th>
            <Th>Razorpay failure</Th>
            <Th align="right" onClick={() => toggle("amount_paise")}
                active={sort === "amount_paise"} dir={dir}>
              At risk
            </Th>
            <Th align="center" onClick={() => toggle("attempts")}
                active={sort === "attempts"} dir={dir}>
              Try / Msg
            </Th>
            <Th onClick={() => toggle("state")} active={sort === "state"} dir={dir}>
              Outcome
            </Th>
            <Th align="right">Recovered</Th>
          </tr>
        </thead>
        <tbody>
          {rows.map((c) => {
            const isSel = c.id === selected;
            return (
              <tr
                key={c.id}
                onClick={() => onSelect(c.id)}
                tabIndex={0}
                onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") {
                  e.preventDefault(); onSelect(c.id); } }}
                aria-selected={isSel}
                className={`cursor-pointer border-t transition-colors duration-150
                  ${isSel ? "bg-[var(--brand-soft)]" : "hover:bg-[var(--surface)]"}`}
              >
                <td className="px-3 py-2">
                  <div className="max-w-[180px] truncate text-[length:var(--text-xs)]
                                  font-medium">
                    {c.customer_name}
                  </div>
                  <div className="mono text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                    {c.entity_id}
                  </div>
                </td>
                <td className="px-3 py-2 text-[length:var(--text-xs)] text-[var(--ink-2)]">
                  {KIND_SHORT[c.kind] ?? humanise(c.kind)}
                  {c.days_overdue > 0 && (
                    <span className="tnum ml-1 text-[var(--at-risk-ink)]">
                      {c.days_overdue}d
                    </span>
                  )}
                </td>
                <td className="px-3 py-2">
                  {c.error_reason ? (
                    <>
                      <div className="mono text-[length:var(--text-2xs)] text-[var(--ink)]">
                        {c.error_reason}
                      </div>
                      <div className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                        {c.error_source} · {c.method ?? "—"}
                      </div>
                    </>
                  ) : (
                    <span className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                      no failure code
                    </span>
                  )}
                </td>
                <td className="tnum px-3 py-2 text-right text-[length:var(--text-xs)]
                               font-semibold">
                  {rupees(c.amount_paise)}
                </td>
                <td className="tnum px-3 py-2 text-center text-[length:var(--text-2xs)]
                               text-[var(--ink-2)]">
                  {c.attempts}/{c.contacts_sent}
                </td>
                <td className="px-3 py-2">
                  <Pill tone={STATE_TOKEN[c.state]} title={c.stop_reason ?? undefined}>
                    {STATE_LABEL[c.state]}
                  </Pill>
                  {c.stop_reason && c.stop_reason !== "recovered" && (
                    <div className="mono mt-0.5 max-w-[190px] truncate
                                    text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                      {c.stop_reason}
                    </div>
                  )}
                </td>
                <td className="tnum px-3 py-2 text-right text-[length:var(--text-xs)]"
                    style={{ color: c.recovered_paise
                      ? "var(--recovered-ink)" : "var(--ink-3)" }}>
                  {c.recovered_paise ? rupees(c.recovered_paise) : "—"}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function Th({
  children, align = "left", onClick, active, dir,
}: {
  children: React.ReactNode; align?: "left" | "right" | "center";
  onClick?: () => void; active?: boolean; dir?: 1 | -1;
}) {
  return (
    <th
      scope="col"
      className={`whitespace-nowrap border-b px-3 py-2 font-medium text-${align}
        ${onClick ? "cursor-pointer select-none hover:text-[var(--ink)]" : ""}`}
      onClick={onClick}
      aria-sort={active ? (dir === 1 ? "ascending" : "descending") : undefined}
    >
      {children}
      {active && <span className="ml-1">{dir === 1 ? "↑" : "↓"}</span>}
    </th>
  );
}
