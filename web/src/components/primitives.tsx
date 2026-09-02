import type { ReactNode } from "react";

/* Panels are bordered regions of the page, not floating cards. A card grid is
   the lazy answer to "show several things"; here the things are a table, a
   queue and a chart, and each gets the shape it needs. */

export function Panel({
  title, meta, actions, children, className = "", flush = false,
}: {
  title?: ReactNode; meta?: ReactNode; actions?: ReactNode;
  children: ReactNode; className?: string; flush?: boolean;
}) {
  return (
    <section
      className={`rounded-[var(--radius-md)] border bg-[var(--bg)] ${className}`}
      style={{ boxShadow: "var(--shadow-panel)" }}
    >
      {(title || actions) && (
        <header className="flex items-center gap-3 border-b px-4 py-2.5">
          <h2 className="text-[length:var(--text-sm)] font-semibold tracking-[-0.006em]">
            {title}
          </h2>
          {meta && (
            <span className="text-[length:var(--text-xs)] text-[var(--ink-3)] tnum">{meta}</span>
          )}
          {actions && <div className="ml-auto flex items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={flush ? "" : "p-4"}>{children}</div>
    </section>
  );
}

export function Pill({
  tone = "stopped", children, title,
}: { tone?: string; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5
                 text-[length:var(--text-2xs)] font-medium whitespace-nowrap"
      style={{ background: `var(--${tone}-soft)`, color: `var(--${tone}-ink)` }}
    >
      <span className="size-1.5 rounded-full" style={{ background: `var(--${tone})` }} />
      {children}
    </span>
  );
}

export function Button({
  children, onClick, variant = "default", disabled, loading, type = "button", title,
}: {
  children: ReactNode; onClick?: () => void;
  variant?: "default" | "primary" | "quiet" | "danger";
  disabled?: boolean; loading?: boolean; type?: "button" | "submit"; title?: string;
}) {
  const base =
    "inline-flex items-center justify-center gap-1.5 rounded-[var(--radius-sm)] px-2.5 py-1.5 " +
    "text-[length:var(--text-xs)] font-medium transition-[background-color,border-color,color] " +
    "duration-150 ease-[var(--ease-out-quint)] disabled:cursor-not-allowed disabled:opacity-45";
  const variants: Record<string, string> = {
    default: "border bg-[var(--bg)] text-[var(--ink)] hover:bg-[var(--surface-2)] " +
             "active:bg-[var(--line)]",
    primary: "border border-transparent text-white hover:brightness-110 active:brightness-95",
    quiet: "text-[var(--ink-2)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]",
    danger: "border text-[var(--escalated-ink)] hover:bg-[var(--escalated-soft)]",
  };
  return (
    <button
      type={type} title={title} onClick={onClick} disabled={disabled || loading}
      className={`${base} ${variants[variant]}`}
      style={variant === "primary" ? { background: "var(--brand)" } : undefined}
    >
      {loading && (
        <span
          className="size-3 animate-spin rounded-full border-[1.5px] border-current
                     border-t-transparent"
          aria-hidden
        />
      )}
      {children}
    </button>
  );
}

/** Label above value. The workhorse of every detail panel. */
export function Field({
  label, children, mono = false, className = "",
}: { label: string; children: ReactNode; mono?: boolean; className?: string }) {
  return (
    <div className={`min-w-0 ${className}`}>
      <dt className="text-[length:var(--text-2xs)] font-medium tracking-[0.01em]
                     text-[var(--ink-3)]">
        {label}
      </dt>
      <dd className={`mt-0.5 text-[length:var(--text-xs)] text-[var(--ink)] ${
        mono ? "mono break-all" : ""}`}>
        {children}
      </dd>
    </div>
  );
}

/** Skeletons, not a spinner parked in the middle of the content. */
export function Skeleton({ rows = 5, className = "" }: { rows?: number; className?: string }) {
  return (
    <div className={`space-y-2 ${className}`} aria-hidden>
      {Array.from({ length: rows }, (_, i) => (
        <div
          key={i}
          className="h-6 animate-pulse rounded-[var(--radius-xs)] bg-[var(--surface-2)]"
          style={{ animationDelay: `${i * 60}ms`, opacity: 1 - i * 0.08 }}
        />
      ))}
    </div>
  );
}

/** Empty states teach the interface rather than announcing absence. */
export function Empty({ title, hint, action }: { title: string; hint: string; action?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-12 text-center">
      <p className="text-[length:var(--text-sm)] font-medium">{title}</p>
      <p className="max-w-[46ch] text-[length:var(--text-xs)] leading-relaxed
                    text-[var(--ink-2)]">
        {hint}
      </p>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function ErrorNote({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      className="rounded-[var(--radius-sm)] border px-3 py-2 text-[length:var(--text-xs)]"
      style={{ background: "var(--escalated-soft)", color: "var(--escalated-ink)",
               borderColor: "var(--escalated)" }}
    >
      {children}
    </p>
  );
}

/** A pass/fail mark for policy rules. Shape as well as colour, so the verdict
 *  survives greyscale and the ~8% of male readers with a colour deficiency. */
export function Verdict({ passed }: { passed: boolean }) {
  return (
    <span
      aria-label={passed ? "passed" : "failed"}
      className="mt-0.5 inline-flex size-3.5 shrink-0 items-center justify-center rounded-full
                 text-[9px] font-bold leading-none"
      style={{
        background: passed ? "var(--recovered-soft)" : "var(--escalated-soft)",
        color: passed ? "var(--recovered-ink)" : "var(--escalated-ink)",
      }}
    >
      {passed ? "✓" : "✕"}
    </span>
  );
}
