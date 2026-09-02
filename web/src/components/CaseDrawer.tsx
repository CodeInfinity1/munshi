import { useEffect, useState } from "react";
import { api, type CaseDetail } from "../api";
import { Button, ErrorNote, Field, Pill, Skeleton, Verdict } from "./primitives";
import { STATE_LABEL, STATE_TOKEN, humanise, rupees, when } from "../format";

/** The decision trail for one case.
 *
 *  This is where the product either earns trust or does not, so it shows the
 *  whole chain and not a summary: the evidence the agent was given, what it
 *  concluded, every policy rule that ran (passes included -- a trail that only
 *  records refusals cannot show you what was checked), what actually executed,
 *  and the ledger row behind any rupee claimed as recovered. */

export function CaseDrawer({ caseId, onClose }: { caseId: string; onClose: () => void }) {
  const [data, setData] = useState<CaseDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setData(null);
    setError(null);
    api.caseDetail(caseId).then(setData).catch((e) => setError(String(e.message ?? e)));
  }, [caseId]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    // Standard modal behaviour: the page behind a drawer must not scroll, or the
    // reader loses their place in the table they came from.
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = prev;
    };
  }, [onClose]);

  return (
    <>
      <div
        className="fixed inset-0 z-[var(--z-drawer)] bg-[oklch(0.235_0.014_245_/_0.28)]
                   animate-[fade_180ms_var(--ease-out-quint)]"
        onClick={onClose}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label="Case decision trail"
        className="fixed inset-y-0 right-0 z-[var(--z-drawer)] flex w-full max-w-[640px]
                   flex-col border-l bg-[var(--bg)]
                   animate-[slidein_240ms_var(--ease-out-quint)]"
        style={{ boxShadow: "var(--shadow-drawer)" }}
      >
        {error && <div className="p-4"><ErrorNote>{error}</ErrorNote></div>}
        {!data && !error && <div className="p-4"><Skeleton rows={12} /></div>}
        {data && <Body data={data} onClose={onClose} />}
      </aside>
    </>
  );
}

function Body({ data, onClose }: { data: CaseDetail; onClose: () => void }) {
  const c = data.case;
  const ctx = data.context;

  return (
    <>
      <header className="flex items-start gap-3 border-b px-5 py-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <h2 className="truncate text-[length:var(--text-lg)] font-semibold
                           tracking-[-0.012em]">
              {c.customer_name}
            </h2>
            <Pill tone={STATE_TOKEN[c.state]}>{STATE_LABEL[c.state]}</Pill>
          </div>
          <p className="mono mt-1 text-[length:var(--text-2xs)] text-[var(--ink-3)]">
            {c.id} · {c.entity_id} · {humanise(c.kind)}
          </p>
        </div>
        <div className="text-right">
          <div className="tnum text-[length:var(--text-xl)] font-semibold tracking-[-0.02em]">
            {rupees(c.amount_paise)}
          </div>
          {c.recovered_paise > 0 && (
            <div className="tnum text-[length:var(--text-xs)] font-medium"
                 style={{ color: "var(--recovered-ink)" }}>
              {rupees(c.recovered_paise)} recovered
            </div>
          )}
        </div>
        <Button variant="quiet" onClick={onClose} title="Close (Esc)">✕</Button>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto">
        <Section title="What Razorpay told us">
          {ctx.failure?.error_reason ? (
            <>
              <dl className="grid grid-cols-3 gap-3">
                <Field label="error_reason" mono>{ctx.failure.error_reason}</Field>
                <Field label="error_source" mono>{ctx.failure.error_source}</Field>
                <Field label="error_step" mono>{ctx.failure.error_step}</Field>
              </dl>
              <p className="mt-3 text-[length:var(--text-xs)] leading-relaxed
                            text-[var(--ink-2)]">
                {ctx.failure.razorpay_description}
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Pill tone={ctx.failure.retry_on_same_instrument_is_futile
                  ? "escalated" : "recovered"}>
                  {ctx.failure.retry_on_same_instrument_is_futile
                    ? "Retry cannot succeed" : "Retryable"}
                </Pill>
                <Pill tone="stopped">{ctx.failure.family_label}</Pill>
                <Pill tone="held">{humanise(ctx.failure.who_must_act)} must act</Pill>
              </div>
              <p className="mt-2 text-[length:var(--text-xs)] text-[var(--ink-2)]">
                <span className="text-[var(--ink-3)]">Resolves only when:</span>{" "}
                {ctx.failure.resolution_requires}
              </p>
            </>
          ) : (
            <p className="text-[length:var(--text-xs)] text-[var(--ink-2)]">
              No Razorpay failure code on this entity — it is an{" "}
              {humanise(c.kind).toLowerCase()}, not a declined payment.
            </p>
          )}
        </Section>

        {ctx.downtime?.state && ctx.downtime.state !== "clear" &&
         ctx.downtime.state !== "not_applicable" && (
          <Section title="Razorpay Payment Downtime feed">
            <div className="flex flex-wrap items-center gap-2">
              <Pill tone={ctx.downtime.state === "active" ? "escalated" : "at-risk"}>
                {humanise(ctx.downtime.state)}
                {ctx.downtime.severity ? ` · ${ctx.downtime.severity}` : ""}
              </Pill>
              {ctx.downtime.downtime && (
                <span className="mono text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                  {ctx.downtime.downtime.id} · {JSON.stringify(ctx.downtime.downtime.instrument)}
                </span>
              )}
            </div>
            <p className="mt-2 text-[length:var(--text-xs)] leading-relaxed
                          text-[var(--ink-2)]">
              {ctx.downtime.note}
            </p>
          </Section>
        )}

        <Section title="Context the agent was given">
          <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <Field label="Segment">{humanise(ctx.customer?.segment)}</Field>
            <Field label="Past successes">{ctx.customer?.successful_payments}</Field>
            <Field label="Lifetime value">
              ₹{Math.round(ctx.customer?.lifetime_value_inr ?? 0).toLocaleString("en-IN")}
            </Field>
            <Field label="Opted out">{ctx.customer?.contact_opt_out ? "Yes" : "No"}</Field>
            <Field label="Retries left">{ctx.case?.retries_remaining}</Field>
            <Field label="Messages left">{ctx.case?.contacts_remaining}</Field>
            <Field label="Attempts before Munshi">{ctx.case?.attempts_before_munshi}</Field>
            <Field label="Age">{ctx.case?.age_hours}h</Field>
          </dl>
          <div className="mt-3 flex flex-wrap gap-2">
            <Pill tone={ctx.compliance?.contact_allowed_now ? "recovered" : "at-risk"}>
              RBI contact window {ctx.compliance?.contact_allowed_now ? "open" : "closed"}
            </Pill>
            {c.method === "emandate" && (
              <Pill tone={ctx.compliance?.npci_debit_window_open ? "recovered" : "at-risk"}>
                NPCI debit window {ctx.compliance?.npci_debit_window_open ? "open" : "peak"}
              </Pill>
            )}
            {c.method === "emandate" && (
              <Pill tone={ctx.compliance?.pre_debit_notification_sent ? "recovered" : "at-risk"}>
                Pre-debit notice {ctx.compliance?.pre_debit_notification_sent ? "sent" : "not sent"}
              </Pill>
            )}
          </div>
        </Section>

        <Section title={`Decision trail · ${data.actions.length} decisions`} flush>
          {data.actions.length === 0 && (
            <p className="px-5 py-4 text-[length:var(--text-xs)] text-[var(--ink-2)]">
              No decision has been taken on this case yet. Run the recovery batch.
            </p>
          )}
          <ol>
            {data.actions.map((a) => {
              const diag = data.audit.find(
                (r) => r.stage === "diagnose" && Math.abs(r.ts - a.proposed_at) < 2);
              return (
                <li key={a.id} className="border-t px-5 py-3.5 first:border-t-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="mono text-[length:var(--text-xs)] font-semibold">
                      {a.action_type}
                    </span>
                    <span className="rounded-[var(--radius-xs)] border px-1.5
                                     text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                      L{a.tier}
                    </span>
                    <Pill tone={{ allow: "recovered", deny: "escalated",
                                  require_approval: "held" }[a.policy_decision]}>
                      {humanise(a.policy_decision)}
                    </Pill>
                    {a.outcome && (
                      <span className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                        → {a.outcome}
                        {a.recovered_paise > 0 && (
                          <b className="tnum ml-1" style={{ color: "var(--recovered-ink)" }}>
                            {rupees(a.recovered_paise)}
                          </b>
                        )}
                      </span>
                    )}
                    <span className="tnum ml-auto text-[length:var(--text-2xs)]
                                     text-[var(--ink-3)]">
                      {when(a.proposed_at)}
                    </span>
                  </div>

                  {diag && (
                    <p className="mt-2 border-l-0 text-[length:var(--text-xs)] leading-relaxed
                                  text-[var(--ink-2)]">
                      <span className="mono text-[var(--brand-ink)]">
                        {String((diag.detail as any).root_cause)}
                      </span>{" "}
                      <span className="text-[var(--ink-3)]">
                        (confidence {Number((diag.detail as any).confidence).toFixed(2)},
                        recoverability {Number((diag.detail as any).recoverability).toFixed(2)},
                        via {String((diag.detail as any).reasoner)})
                      </span>
                      <br />
                      {String((diag.detail as any).rationale)}
                    </p>
                  )}

                  {a.params.justification && (
                    <p className="mt-1.5 text-[length:var(--text-xs)] text-[var(--ink-2)]">
                      {a.params.justification}
                      {a.params.delay_hours ? ` Timed for +${a.params.delay_hours}h.` : ""}
                    </p>
                  )}

                  {a.params.message && (
                    <blockquote className="mt-2 rounded-[var(--radius-sm)] bg-[var(--surface)]
                                           px-3 py-2 text-[length:var(--text-xs)]
                                           leading-relaxed text-[var(--ink-2)]">
                      <span className="text-[var(--ink-3)]">
                        {a.params.channel} to customer:
                      </span>{" "}
                      {a.params.message}
                    </blockquote>
                  )}

                  <details className="group mt-2">
                    <summary className="cursor-pointer list-none text-[length:var(--text-2xs)]
                                        text-[var(--ink-3)] hover:text-[var(--ink)]">
                      <span className="inline-block transition-transform duration-150
                                       group-open:rotate-90">▸</span>{" "}
                      {a.policy_rules.length} policy rules evaluated ·{" "}
                      {a.policy_rules.filter((r) => !r.passed).length} failed
                    </summary>
                    <ul className="mt-2 space-y-1">
                      {a.policy_rules.map((r) => (
                        <li key={r.rule} className="flex gap-2">
                          <Verdict passed={r.passed} />
                          <div className="min-w-0">
                            <span className="mono text-[length:var(--text-2xs)]
                                             text-[var(--ink)]">
                              {r.rule}
                            </span>
                            <p className="text-[length:var(--text-2xs)] leading-snug
                                          text-[var(--ink-3)]">
                              {r.detail}
                            </p>
                          </div>
                        </li>
                      ))}
                    </ul>
                  </details>
                </li>
              );
            })}
          </ol>
        </Section>

        {data.ledger.length > 0 && (
          <Section title="Ledger">
            <ul className="space-y-1.5">
              {data.ledger.map((l) => (
                <li key={l.id} className="flex items-baseline gap-2
                                          text-[length:var(--text-xs)]">
                  <b className="tnum" style={{ color: "var(--recovered-ink)" }}>
                    {rupees(l.amount_paise)}
                  </b>
                  <span className="mono text-[length:var(--text-2xs)] text-[var(--ink-3)]">
                    {l.provider_ref} · {l.adapter} · {when(l.ts)}
                  </span>
                </li>
              ))}
            </ul>
            <p className="mt-2 text-[length:var(--text-2xs)] text-[var(--ink-3)]">
              Every rupee shown as recovered anywhere in this product is one of these rows.
            </p>
          </Section>
        )}

        {c.stop_reason && (
          <Section title="Why Munshi stopped">
            <p className="mono text-[length:var(--text-xs)]">{c.stop_reason}</p>
          </Section>
        )}
      </div>
    </>
  );
}

function Section({
  title, children, flush = false,
}: { title: string; children: React.ReactNode; flush?: boolean }) {
  return (
    <section className="border-b last:border-b-0">
      <h3 className="bg-[var(--surface)] px-5 py-1.5 text-[length:var(--text-2xs)]
                     font-semibold text-[var(--ink-2)]">
        {title}
      </h3>
      <div className={flush ? "" : "px-5 py-3.5"}>{children}</div>
    </section>
  );
}
