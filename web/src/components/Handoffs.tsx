import { useEffect, useState } from "react";
import { Empty, Skeleton } from "./primitives";
import { rupees, rupeesShort } from "../format";

interface Group {
  reason: string; owner: string; next_action: string; value_paise: number;
  cases: { id: string; customer_name: string; amount_paise: number;
           error_reason: string | null }[];
}

/** Cases the agent handed to a human.
 *
 *  An escalation with no owner and no next action is a dead end dressed up as a
 *  handoff, so each group carries both. */
export function Handoffs({ onSelect, refreshKey }:
  { onSelect: (id: string) => void; refreshKey: unknown }) {
  const [data, setData] = useState<{ groups: Group[]; total_paise: number;
                                     total_cases: number } | null>(null);
  useEffect(() => {
    fetch("/api/escalations").then((r) => r.json()).then(setData).catch(() => {});
  }, [refreshKey]);

  if (!data) return <div className="p-4"><Skeleton rows={4} /></div>;
  if (!data.total_cases) {
    return <Empty title="Nothing handed off"
                  hint="Cases the agent cannot resolve itself — a risk decline, a merchant
                        misconfiguration, an integration defect — are routed here with an
                        owner and a next action." />;
  }

  return (
    <>
      <p className="border-b px-4 py-2 text-[length:var(--text-2xs)] text-[var(--ink-2)]">
        <b className="tnum">{rupees(data.total_paise)}</b> across {data.total_cases} cases,
        each with an owner and a next action.
      </p>
      <ul className="divide-y">
        {data.groups.map((g) => (
          <li key={g.reason} className="px-4 py-2.5">
            <div className="flex items-baseline gap-2">
              <span className="rounded-[var(--radius-xs)] px-1.5 py-0.5
                               text-[length:var(--text-2xs)] font-semibold"
                    style={{ background: "var(--escalated-soft)",
                             color: "var(--escalated-ink)" }}>
                {g.owner}
              </span>
              <span className="tnum ml-auto text-[length:var(--text-xs)] font-semibold">
                {rupeesShort(g.value_paise)}
              </span>
              <span className="tnum text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                {g.cases.length}
              </span>
            </div>
            <code className="mono mt-1 block text-[length:var(--text-2xs)]
                             text-[var(--ink-3)]">
              {g.reason}
            </code>
            <p className="mt-1 text-[length:var(--text-2xs)] leading-snug
                          text-[var(--ink-2)]">
              {g.next_action}
            </p>
            <div className="mt-1.5 flex flex-wrap gap-1">
              {g.cases.slice(0, 4).map((c) => (
                <button key={c.id} onClick={() => onSelect(c.id)}
                        className="mono rounded-[var(--radius-xs)] bg-[var(--surface-2)]
                                   px-1.5 py-0.5 text-[length:var(--text-2xs)]
                                   hover:bg-[var(--line)]">
                  {c.id}
                </button>
              ))}
              {g.cases.length > 4 && (
                <span className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                  +{g.cases.length - 4}
                </span>
              )}
            </div>
          </li>
        ))}
      </ul>
    </>
  );
}
