import { useState } from "react";
import { api, type Approval } from "../api";
import { Button, Empty, ErrorNote, Pill } from "./primitives";
import { humanise, rupees } from "../format";

/** The queue is the product's honest edge.
 *
 *  Everything above the merchant's autonomous ceiling stops here rather than
 *  executing, which is why the unattended recovery figure is lower than a
 *  ladder's. Showing the held value plainly, next to the button that releases
 *  it, is the point -- not a footnote. */

export function ApprovalQueue({
  approvals, heldPaise, onChange,
}: { approvals: Approval[]; heldPaise: number; onChange: () => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const pending = approvals.filter((a) => !a.decided_at);

  async function decide(id: string, decision: "approve" | "reject") {
    setError(null);
    setBusy(id);
    try {
      await api.decide(id, decision);
      onChange();
    } catch (e) {
      setError(e instanceof Error ? e.message : "failed");
    } finally {
      setBusy(null);
    }
  }

  if (!pending.length) {
    return (
      <Empty
        title="Nothing is waiting on you"
        hint="Actions above the merchant's autonomous ceiling, and anything that changes
              commercial terms, queue here instead of executing. An empty queue means the
              agent stayed inside its bounds."
      />
    );
  }

  return (
    <>
      <p className="border-b bg-[var(--held-soft)] px-4 py-2 text-[length:var(--text-xs)]"
         style={{ color: "var(--held-ink)" }}>
        <b className="tnum">{rupees(heldPaise)}</b> is held for your decision across{" "}
        {pending.length} actions. The agent will not move it alone.
      </p>
      {error && <div className="px-4 pt-3"><ErrorNote>{error}</ErrorNote></div>}
      <ul className="divide-y">
        {pending.map((a) => {
          const blocked = a.policy_rules.find((r) => !r.passed);
          return (
            <li key={a.action_id} className="px-4 py-3">
              <div className="flex items-start gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-[length:var(--text-xs)] font-medium">
                      {a.customer_name}
                    </span>
                    <Pill tone="held">L{a.tier}</Pill>
                  </div>
                  <p className="mono mt-0.5 text-[length:var(--text-2xs)] text-[var(--ink-2)]">
                    {a.action_type}
                    {a.error_reason ? ` · ${a.error_reason}` : ""}
                  </p>
                  {blocked && (
                    <p className="mt-1 text-[length:var(--text-2xs)] leading-snug
                                  text-[var(--ink-3)]">
                      {blocked.detail}
                    </p>
                  )}
                </div>
                <div className="text-right">
                  <div className="tnum text-[length:var(--text-sm)] font-semibold">
                    {rupees(a.amount_paise)}
                  </div>
                  <div className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                    {humanise(a.kind)}
                  </div>
                </div>
              </div>
              <div className="mt-2 flex gap-2">
                <Button variant="primary" loading={busy === a.action_id}
                        onClick={() => decide(a.action_id, "approve")}>
                  Approve
                </Button>
                <Button variant="danger" disabled={busy === a.action_id}
                        onClick={() => decide(a.action_id, "reject")}>
                  Reject
                </Button>
              </div>
            </li>
          );
        })}
      </ul>
    </>
  );
}
