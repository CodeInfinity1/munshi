import { rupeesShort } from "../format";
import { STATE_LABEL, STATE_TOKEN } from "../format";

/** The whole book, partitioned by where the money actually ended up.
 *
 *  This is the headline instead of a big number because the interesting fact is
 *  not "we recovered X" -- it is how the book divides: what came back, what a
 *  human still has to decide, what was handed off, and what the agent decided to
 *  stop touching. Every segment is a live SUM over case state, so during a run
 *  it moves because the state moved, not because something is animating. */

const ORDER = ["recovered", "awaiting_approval", "escalated", "stopped", "suppressed",
               "scheduled", "open"];

export function AllocationBar({
  moneyByState, countByState, total,
}: {
  moneyByState: Record<string, number>;
  countByState: Record<string, number>;
  total: number;
}) {
  const segments = ORDER
    .filter((s) => (moneyByState[s] ?? 0) > 0)
    .map((s) => ({
      state: s,
      paise: moneyByState[s] ?? 0,
      count: countByState[s] ?? 0,
      share: total ? (moneyByState[s] / total) * 100 : 0,
    }));

  return (
    <div>
      <div
        className="flex h-11 w-full gap-px overflow-hidden rounded-[var(--radius-sm)]
                   bg-[var(--surface-2)]"
        role="img"
        aria-label={`Revenue at risk ${rupeesShort(total)}, allocated across ${
          segments.map((s) => `${STATE_LABEL[s.state]} ${rupeesShort(s.paise)}`).join(", ")}`}
      >
        {segments.map((s) => (
          <div
            key={s.state}
            title={`${STATE_LABEL[s.state]} · ${rupeesShort(s.paise)} · ${s.count} cases`}
            className="relative flex min-w-0 items-center justify-center transition-[flex-grow]
                       duration-500 ease-[var(--ease-out-quint)]"
            style={{ flex: `${s.share} 1 0%`,
                     background: `var(--${STATE_TOKEN[s.state]})` }}
          >
            {s.share > 7 && (
              <span
                className="tnum truncate px-1.5 text-[length:var(--text-2xs)] font-semibold"
                style={{ color: "oklch(0.16 0 0)" }}
              >
                {rupeesShort(s.paise)}
              </span>
            )}
          </div>
        ))}
        {segments.length === 0 && (
          <div className="flex w-full items-center justify-center
                          text-[length:var(--text-2xs)] text-[var(--ink-3)]">
            no cases loaded
          </div>
        )}
      </div>

      <ul className="mt-3 flex flex-wrap gap-x-5 gap-y-1.5">
        {segments.map((s) => (
          <li key={s.state} className="flex items-baseline gap-1.5">
            <span
              className="size-2 shrink-0 translate-y-px rounded-[2px]"
              style={{ background: `var(--${STATE_TOKEN[s.state]})` }}
            />
            <span className="text-[length:var(--text-xs)] text-[var(--ink-2)]">
              {STATE_LABEL[s.state]}
            </span>
            <span className="tnum text-[length:var(--text-xs)] font-semibold">
              {rupeesShort(s.paise)}
            </span>
            <span className="tnum text-[length:var(--text-2xs)] text-[var(--ink-3)]">
              {s.count}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
