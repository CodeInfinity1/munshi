import { useEffect, useState } from "react";
import { api, type PriorityScore } from "../api";
import { Skeleton } from "./primitives";
import { humanise, rupees, rupeesShort } from "../format";

/** The queue, and why it is in that order.
 *
 *  Sorting by amount is the obvious thing and the wrong one: on a book where a
 *  third of failed value can never be recovered, it puts uncollectable money at
 *  the top. This ranks by amount x P(recover) x urgency, and shows the factors
 *  that produced each score so the ordering is arguable rather than asserted. */
export function PriorityQueue({ onSelect }: { onSelect: (id: string) => void }) {
  const [data, setData] = useState<{ queue: PriorityScore[]; scored: number;
                                     note: string } | null>(null);
  useEffect(() => { api.triage(20).then(setData).catch(() => {}); }, []);
  if (!data) return <div className="p-4"><Skeleton rows={8} /></div>;

  const max = Math.max(1, ...data.queue.map((q) => q.expected_recoverable_paise));

  return (
    <>
      <p className="border-b px-4 py-2 text-[length:var(--text-2xs)] text-[var(--ink-2)]">
        {data.note} Scored {data.scored} cases.
      </p>
      <ol className="divide-y">
        {data.queue.map((q, i) => (
          <li key={q.case_id}
              onClick={() => onSelect(q.case_id)}
              className="cursor-pointer px-4 py-2.5 hover:bg-[var(--surface)]">
            <div className="flex items-baseline gap-2">
              <span className="tnum w-5 text-right text-[length:var(--text-2xs)]
                               text-[var(--ink-3)]">{i + 1}</span>
              <span className="mono text-[length:var(--text-2xs)]">{q.case_id}</span>
              {q.error_reason && (
                <code className="mono text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                  {q.error_reason}
                </code>
              )}
              <span className="tnum ml-auto text-[length:var(--text-2xs)]
                               text-[var(--ink-3)]">
                {rupees(q.amount_paise)} × {(q.probability * 100).toFixed(0)}%
              </span>
              <span className="tnum w-20 text-right text-[length:var(--text-xs)]
                               font-semibold">
                {rupeesShort(q.expected_recoverable_paise)}
              </span>
            </div>
            <div className="mt-1 ml-7 flex items-center gap-2">
              <div className="h-1 flex-1 overflow-hidden rounded-full bg-[var(--surface-2)]">
                <div className="h-full rounded-full transition-[width] duration-500
                                ease-[var(--ease-out-quint)]"
                     style={{ width: `${(q.expected_recoverable_paise / max) * 100}%`,
                              background: "var(--brand)" }} />
              </div>
              <span className="tnum shrink-0 text-[length:var(--text-2xs)]
                               text-[var(--ink-3)]">
                {q.remaining_window_days.toFixed(1)}d left
              </span>
            </div>
            <p className="mt-1 ml-7 text-[length:var(--text-2xs)] leading-snug
                          text-[var(--ink-3)]">
              {Object.entries(q.factors)
                .filter(([k]) => k !== "family_prior")
                .filter(([, v]) => Math.abs(v) >= 0.01)
                .map(([k, v]) => `${humanise(k)} ${v > 0 ? "+" : ""}${(v * 100).toFixed(0)}%`)
                .join(" · ") || "no adjustments to the family prior"}
            </p>
          </li>
        ))}
      </ol>
    </>
  );
}
