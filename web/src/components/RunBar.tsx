import { useState } from "react";
import { api, type Health, type Overview } from "../api";
import { Button, ErrorNote } from "./primitives";

/** Run control plus the honesty strip.
 *
 *  The strip is not decoration: it states which reasoner and which adapter are
 *  actually in use and whether money movement is simulated. A revenue-recovery
 *  demo that lets a viewer assume a real payment rail ran is the one thing this
 *  product must never do, so the claim lives next to the button that produces it. */

export function RunBar({
  health, overview, onChange,
}: { health: Health | null; overview: Overview | null; onChange: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const running = overview?.run_state.status === "running";
  const stats = overview?.run_state.stats ?? {};

  async function start(paced: boolean) {
    setError(null);
    setBusy(true);
    try {
      await api.run({ days: 14, step_hours: 2, tick_delay_ms: paced ? 55 : 0 });
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "run failed");
    } finally {
      setBusy(false);
    }
  }

  async function reseed() {
    setError(null);
    setBusy(true);
    try {
      await api.seed({ n: 320 });
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "reseed failed");
    } finally {
      setBusy(false);
    }
  }

  const simulated = health?.adapter === "simulator";

  return (
    <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
      <div className="flex items-center gap-2">
        <Button variant="primary" onClick={() => start(true)} disabled={running} loading={busy}>
          {running ? "Agent running…" : "Run recovery batch"}
        </Button>
        <Button onClick={() => start(false)} disabled={running || busy} title="Same run, no pacing">
          Run fast
        </Button>
        <Button variant="quiet" onClick={reseed} disabled={running || busy}>
          Reset batch
        </Button>
      </div>

      {running && (
        <span className="tnum text-[length:var(--text-xs)] text-[var(--ink-2)]">
          tick {stats.ticks ?? 0} · {stats.decisions ?? 0} decisions ·{" "}
          {stats.executed ?? 0} executed · {stats.blocked ?? 0} blocked
        </span>
      )}

      <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1
                      text-[length:var(--text-2xs)]">
        <Claim
          label="Money movement"
          value={simulated ? "SIMULATED" : "Razorpay test mode"}
          tone={simulated ? "at-risk" : "recovered"}
        />
        <Claim
          label="Reasoner"
          value={
            health?.reasoner === "agent-groq" ? `agent · ${health.llm_model}`
              : health?.reasoner === "agent-mock" ? "agent · MOCK PROVIDER"
              : "deterministic (no model)"
          }
          tone={health?.reasoner === "agent-groq" ? "held"
                : health?.reasoner === "agent-mock" ? "at-risk" : "stopped"}
        />
        <Claim
          label="Audit chain"
          value={overview?.audit.valid ? `verified · ${overview.audit.checked}` : "BROKEN"}
          tone={overview?.audit.valid ? "recovered" : "escalated"}
        />
      </div>

      {error && <div className="w-full"><ErrorNote>{error}</ErrorNote></div>}
    </div>
  );
}

function Claim({ label, value, tone }: { label: string; value: string; tone: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="text-[var(--ink-3)]">{label}</span>
      <span
        className="rounded-[var(--radius-xs)] px-1.5 py-0.5 font-semibold"
        style={{ background: `var(--${tone}-soft)`, color: `var(--${tone}-ink)` }}
      >
        {value}
      </span>
    </span>
  );
}
