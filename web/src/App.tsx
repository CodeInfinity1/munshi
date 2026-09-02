import { useCallback, useEffect, useState } from "react";
import { api, getToken, setToken, type Health, type Overview } from "./api";
import { Audit } from "./views/Audit";
import { Desk } from "./views/Desk";
import { Evaluation } from "./views/Evaluation";
import { Policy } from "./views/Policy";
import { Button, ErrorNote } from "./components/primitives";

type Tab = "desk" | "policy" | "evaluation" | "audit";

const TABS: { id: Tab; label: string; hint: string }[] = [
  { id: "desk", label: "Recovery desk", hint: "Where the money is and what happened to it" },
  { id: "policy", label: "Policy", hint: "What the agent is allowed to do" },
  { id: "evaluation", label: "Evaluation", hint: "Agent vs a fixed retry ladder" },
  { id: "audit", label: "Audit", hint: "Hash-chained decision trail" },
];

export function App() {
  const [tab, setTab] = useState<Tab>("desk");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [theme, setTheme] = useState(document.documentElement.dataset.theme ?? "light");

  const refresh = useCallback(() => {
    api.overview().then(setOverview).catch((e) => setError(String(e.message ?? e)));
    api.health().then(setHealth).catch(() => {});
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // While a batch runs the numbers move because state moves. Poll only then.
  useEffect(() => {
    if (overview?.run_state.status !== "running") return;
    const t = setInterval(refresh, 500);
    return () => clearInterval(t);
  }, [overview?.run_state.status, refresh]);

  function toggleTheme() {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    localStorage.setItem("munshi_theme", next);
    setTheme(next);
  }

  return (
    <div className="flex min-h-full flex-col">
      <header className="sticky top-0 z-[var(--z-sticky)] border-b bg-[var(--surface)]/95
                         backdrop-blur">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center gap-x-6 gap-y-2
                        px-5 py-2.5">
          <div className="flex items-baseline gap-2.5">
            <span className="text-[length:var(--text-base)] font-semibold
                             tracking-[-0.02em]">
              Munshi
            </span>
            <span className="text-[length:var(--text-2xs)] text-[var(--ink-3)]">
              bounded revenue recovery · Razorpay
            </span>
          </div>

          <nav className="flex gap-0.5" aria-label="Sections">
            {TABS.map((t) => (
              <button
                key={t.id}
                onClick={() => setTab(t.id)}
                title={t.hint}
                aria-current={tab === t.id ? "page" : undefined}
                className={`rounded-[var(--radius-sm)] px-2.5 py-1.5
                  text-[length:var(--text-xs)] font-medium transition-colors duration-150
                  ${tab === t.id
                    ? "bg-[var(--brand-soft)] text-[var(--brand-ink)]"
                    : "text-[var(--ink-2)] hover:bg-[var(--surface-2)] hover:text-[var(--ink)]"}`}
              >
                {t.label}
              </button>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-2">
            <TokenField />
            <Button variant="quiet" onClick={toggleTheme}
                    title={`Switch to ${theme === "dark" ? "light" : "dark"} theme`}>
              {theme === "dark" ? "☾" : "☀"}
            </Button>
          </div>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1500px] flex-1 px-5 py-5">
        {error && <div className="mb-4"><ErrorNote>{error}</ErrorNote></div>}
        {tab === "desk" && <Desk overview={overview} health={health} refresh={refresh} />}
        {tab === "policy" && <Policy />}
        {tab === "evaluation" && <Evaluation />}
        {tab === "audit" && <Audit />}
      </main>

      <footer className="border-t px-5 py-3">
        <p className="mx-auto max-w-[1500px] text-[length:var(--text-2xs)]
                      leading-relaxed text-[var(--ink-3)]">
          Money movement in this build is <b>simulated</b> against a deterministic outcome
          oracle seeded per case; no real payment rail is contacted. Recovery is only ever
          counted from a ledger row pointing at the action that caused it. Regulatory
          constants are implemented from published RBI and NPCI guidance and are not legal
          advice.
        </p>
      </footer>
    </div>
  );
}

/** Mutating routes need a bearer token. It is shown once at startup by the
 *  server, so the field is a paste target rather than a login. */
function TokenField() {
  const [value, setValue] = useState(getToken());
  const [saved, setSaved] = useState(false);
  return (
    <label className="flex items-center gap-1.5">
      <span className="sr-only">API token</span>
      <input
        type="password"
        value={value}
        placeholder="API token"
        onChange={(e) => { setValue(e.target.value); setToken(e.target.value);
                           setSaved(true); setTimeout(() => setSaved(false), 1200); }}
        className="w-28 rounded-[var(--radius-sm)] border bg-[var(--bg)] px-2 py-1
                   text-[length:var(--text-2xs)] text-[var(--ink)]
                   placeholder:text-[var(--ink-3)] focus:w-44
                   transition-[width] duration-200 ease-[var(--ease-out-quint)]"
      />
      {saved && (
        <span className="text-[length:var(--text-2xs)]"
              style={{ color: "var(--recovered-ink)" }}>
          saved
        </span>
      )}
    </label>
  );
}
