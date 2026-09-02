import { useEffect, useState } from "react";
import { api, type AuditRecord } from "../api";
import { Empty, Panel, Skeleton } from "../components/primitives";
import { humanise, when } from "../format";

const STAGES = ["", "diagnose", "policy", "execute", "verify", "stop", "detect"];
const STAGE_TONE: Record<string, string> = {
  diagnose: "held", policy: "at-risk", execute: "brand",
  verify: "recovered", stop: "stopped", detect: "suppressed",
};

/** The audit trail, with its chain verification stated at the top.
 *
 *  "We wrote it down" and "nobody changed it" are different claims. Each record
 *  commits to its predecessor by sha256, so the verification line is the second
 *  claim, recomputed on every load. */
export function Audit() {
  const [records, setRecords] = useState<AuditRecord[] | null>(null);
  const [verification, setVerification] = useState<any>(null);
  const [stage, setStage] = useState("");

  useEffect(() => {
    setRecords(null);
    api.audit({ stage: stage || undefined, limit: 400 }).then((r) => {
      setRecords(r.records);
      setVerification(r.verification);
    });
  }, [stage]);

  return (
    <Panel
      flush
      title="Audit trail"
      meta={verification
        ? verification.valid
          ? `chain verified · ${verification.checked} records · head ${String(
              verification.head).slice(0, 12)}…`
          : `CHAIN BROKEN at record ${verification.broken_at}`
        : undefined}
      actions={
        <div className="flex flex-wrap gap-1">
          {STAGES.map((s) => (
            <button
              key={s || "all"}
              onClick={() => setStage(s)}
              aria-pressed={stage === s}
              className={`rounded-[var(--radius-sm)] px-2 py-1 text-[length:var(--text-2xs)]
                font-medium transition-colors duration-150
                ${stage === s
                  ? "bg-[var(--brand-soft)] text-[var(--brand-ink)]"
                  : "text-[var(--ink-2)] hover:bg-[var(--surface-2)]"}`}
            >
              {s ? humanise(s) : "All"}
            </button>
          ))}
        </div>
      }
    >
      {!records && <div className="p-4"><Skeleton rows={12} /></div>}
      {records?.length === 0 && (
        <Empty title="No audit records yet"
               hint="Every meaningful decision writes one record, chained to the one before it.
                     Run the recovery batch from the desk to populate the trail." />
      )}
      {records && records.length > 0 && (
        <ol className="max-h-[72vh] divide-y overflow-y-auto">
          {records.map((r) => (
            <li key={r.seq} className="flex gap-3 px-4 py-2.5 hover:bg-[var(--surface)]">
              <span className="tnum w-12 shrink-0 pt-0.5 text-right
                               text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                {r.seq}
              </span>
              <span
                className="mt-0.5 h-fit shrink-0 rounded-[var(--radius-xs)] px-1.5 py-0.5
                           text-[length:var(--text-2xs)] font-medium"
                style={{ background: `var(--${STAGE_TONE[r.stage] ?? "stopped"}-soft)`,
                         color: `var(--${STAGE_TONE[r.stage] ?? "stopped"}-ink)` }}
              >
                {r.stage}
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-[length:var(--text-xs)] leading-snug">{r.summary}</p>
                <p className="mono mt-0.5 truncate text-[length:var(--text-2xs)]
                              text-[var(--ink-3)]">
                  {r.case_id ?? "—"} · {when(r.ts)} · {r.hash.slice(0, 16)}…
                </p>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Panel>
  );
}
