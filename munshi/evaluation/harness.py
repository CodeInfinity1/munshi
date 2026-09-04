"""Batch evaluation harness.

Runs the same 320-case batch through every arm and writes a machine-readable
result plus a human-readable report. Each arm gets a freshly seeded database
built from the same seed, so the arms see identical cases with identical latent
truth and identical per-case luck.

Arms
  baseline         fixed retry ladder + caps only (what most merchants run)
  agent-heuristic  taxonomy-driven deterministic reasoner + full policy engine
  agent-heuristic-approved
                   same, with a merchant approving the queued L3 actions
  agent-mock       the real tool-calling loop, driven by the deterministic mock
                   provider -- proves the loop end to end with no network
  agent-groq       the tool-calling loop against Groq (needs GROQ_API_KEY)
  agent-groq-approved
                   same, with a merchant approving the queued L3 actions

Reporting the heuristic arm separately is deliberate. It is the honest control
for "is the model earning its place, or is the taxonomy doing all the work?" --
a question the submission should answer with numbers rather than assert.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from .. import audit, db
from ..adapters.simulator import SimulatorAdapter
from ..clock import VirtualClock
from ..config import settings
from ..reason import AgentReasoner, HeuristicReasoner
from ..seed.generate import BATCH_START
from ..seed.load import load
from .baseline import FixedLadderReasoner, LadderPolicy
from .metrics import compute, unretryable_share

OUT = Path("evaluation")


def run_arm(arm: str, n: int, seed: int, days: int, step_hours: int,
            db_path: str | None = None) -> dict:
    from ..orchestrator import Orchestrator

    path = db_path or f"/tmp/munshi_eval_{arm}_n{n}_s{seed}.db"
    conn = db.reset(path)
    load(conn, n=n, seed=seed)

    if arm == "baseline":
        reasoner, policy = FixedLadderReasoner(), LadderPolicy(conn)
    elif arm in ("agent-heuristic", "agent-heuristic-approved"):
        reasoner, policy = HeuristicReasoner(), None
    elif arm in ("agent-mock", "agent-mock-approved"):
        from ..llm.mock_provider import MockProvider

        reasoner, policy = AgentReasoner(provider=MockProvider()), None
    elif arm in ("agent-groq", "agent-groq-approved"):
        if not settings().llm_available:
            raise RuntimeError("GROQ_API_KEY is not set; cannot run the agent-groq arm")
        reasoner, policy = AgentReasoner(), None
    else:
        raise ValueError(f"unknown arm {arm}")

    clock = VirtualClock(BATCH_START)
    # `-approved` stands in for a merchant who signs off on the L3 actions the agent
    # queues. The unattended arm is the honest default; this one shows what the full
    # human-in-the-loop cycle collects.
    orch = Orchestrator(conn, reasoner, SimulatorAdapter(), clock, mode=arm,
                        auto_approve=arm.endswith("-approved"))
    if arm == "baseline":
        orch.policy = policy  # caps only; compliance breaches are recorded, not prevented

    t0 = time.time()
    stats = orch.run(days=days, step_hours=step_hours)
    elapsed = time.time() - t0

    violations = getattr(orch.policy, "violations", [])
    m = compute(conn, violations)
    m["run"] = {
        "arm": arm, "reasoner": reasoner.name, "adapter": orch.adapter.name,
        "run_id": orch.run_id, "wall_seconds": round(elapsed, 2), "ticks": stats["ticks"],
        "deferred": stats.get("deferred", 0),
        "prioritised": stats.get("prioritised", 0),
        "degraded_to_heuristic": getattr(reasoner, "degraded", 0),
        "degrade_reasons": dict(getattr(reasoner, "degrade_reasons", {}) or {}),
        "audit": audit.verify(conn), "db": path,
    }
    m["batch"] = {"n": n, "seed": seed, "recovery_window_days": days,
                  "tick_hours": step_hours, **unretryable_share(conn)}
    conn.close()
    return m


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the Munshi batch evaluation.")
    ap.add_argument("--arms", default="baseline,agent-heuristic,agent-heuristic-approved",
                    help="comma-separated; see the module docstring for the full list")
    ap.add_argument("-n", type=int, default=320)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--step-hours", type=int, default=2)
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()

    arms = [x.strip() for x in a.arms.split(",") if x.strip()]
    results = {}
    for arm in arms:
        print(f"running arm: {arm} ...", flush=True)
        results[arm] = run_arm(arm, a.n, a.seed, a.days, a.step_hours)
        r = results[arm]
        print(f"  recovered Rs {r['money']['recovered_paise'] / 100:,.0f} "
              f"({r['money']['recovery_rate_of_at_risk']}% of at-risk) in "
              f"{r['run']['wall_seconds']}s", flush=True)

    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": int(time.time()), "config": vars(a), "arms": results}
    (out / "results.json").write_text(json.dumps(payload, indent=2))
    (out / "report.md").write_text(render(payload))
    print(f"\nwrote {out / 'results.json'} and {out / 'report.md'}")


def rs(paise: int) -> str:
    return f"Rs {paise / 100:,.0f}"


def render(payload: dict) -> str:
    arms = payload["arms"]
    names = list(arms)
    any_arm = arms[names[0]]
    b = any_arm["batch"]

    lines = [
        "# Munshi evaluation report",
        "",
        f"Batch of **{b['n']} revenue-risk cases** (seed `{b['seed']}`), "
        f"{b['recovery_window_days']}-day recovery window, {b['tick_hours']}h ticks.",
        "",
        "Every arm runs the **same cases with the same latent ground truth and the "
        "same per-case seeds**, through the same outcome oracle. Only the choice of "
        "action and its timing differ.",
        "",
        "> All money movement in this report is **simulated**. A rupee is counted as "
        "recovered only when a ledger row exists pointing at the action that caused "
        "it; there is no estimated recovery anywhere in these numbers.",
        "",
        "## The claim this rests on",
        "",
        f"- {b['structurally_unretryable_cases']} of {b['cases_with_failure_code']} "
        f"cases carrying a Razorpay failure code "
        f"(**{b['structurally_unretryable_share']}%**, "
        f"**{b['share_of_failed_value']}%** of failed value = "
        f"{rs(b['structurally_unretryable_paise'])}) are *structurally unretryable*: "
        "the instrument, the mandate or the request itself cannot authorise the "
        "amount, whatever the ladder does.",
        "",
        "## Headline",
        "",
        "| Metric | " + " | ".join(names) + " |",
        "|---|" + "---|" * len(names),
    ]

    def row(label, fn):
        lines.append(f"| {label} | " + " | ".join(str(fn(arms[n])) for n in names) + " |")

    row("Revenue at risk", lambda m: rs(m["money"]["at_risk_paise"]))
    row("**Revenue recovered**", lambda m: f"**{rs(m['money']['recovered_paise'])}**")
    row("Recovery rate (of at-risk)", lambda m: f"{m['money']['recovery_rate_of_at_risk']}%")
    row("Recovery rate (of recoverable)",
        lambda m: f"{m['money']['recovery_rate_of_recoverable']}%")
    row("Cases recovered", lambda m: f"{m['cases']['recovered']}/{m['cases']['total']}")
    row("Held for merchant approval", lambda m: rs(m["money"]["held_for_approval_paise"]))
    row("Annualised MRR protected",
        lambda m: rs(m["money"]["annualised_mrr_protected_paise"]))

    if any(n.startswith("agent-mock") for n in names):
        lines += ["", "> **Read the `agent-mock` arm's intervention accuracy as an artefact, "
                  "not a result.** The mock provider picks its action from the same taxonomy "
                  "family the accuracy metric scores against, so it is correct by "
                  "construction. That arm exists to prove the tool loop runs end to end "
                  "without a network, not to say anything about judgement quality."]

    lines += ["", "## Efficiency and harm", "",
              "| Metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    row("Actions executed", lambda m: m["actions"]["executed"])
    row("Retries spent", lambda m: m["actions"]["retries"])
    row("**Retries with zero possible yield**",
        lambda m: f"**{m['quality']['wasted_retries']}** "
                  f"({m['quality']['wasted_retry_rate']}%)")
    row("Customer messages sent", lambda m: m["actions"]["contacts"])
    row("Messages per recovered case",
        lambda m: m["quality"]["contacts_per_recovered_case"])
    row("**Customers chased after paying**",
        lambda m: f"**{m['quality']['customers_chased_after_paying']}**")
    row("Opted-out customers contacted",
        lambda m: m["quality"]["opted_out_customers_contacted"])
    row("Intervention accuracy", lambda m: f"{m['quality']['intervention_accuracy']}%")
    row("Diagnosis accuracy", lambda m: f"{m['quality']['diagnosis_accuracy']}%")

    lines += ["", "## Compliance", "",
              "The baseline is run *without* the compliance envelope, because a naive "
              "dunning cron genuinely does fire at 02:00. Its breaches are executed and "
              "counted; the agent's policy engine prevents them.",
              "",
              "| Violation | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    row("RBI contact-window (08:00-19:00)",
        lambda m: m["compliance"]["rbi_contact_window_violations"])
    row("NPCI non-peak debit window",
        lambda m: m["compliance"]["npci_debit_window_violations"])
    row("Contacted an opted-out customer",
        lambda m: m["compliance"]["customer_opt_out_violations"])

    lines += ["", "## Bounds held", "",
              "| Check | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    row("Cases over the 3-retry cap", lambda m: m["stopping"]["cases_over_retry_cap"])
    row("Cases over the 3-contact cap", lambda m: m["stopping"]["cases_over_contact_cap"])
    row("Every case reached a terminal state", lambda m: m["cases"]["all_terminal"])
    row("Audit chain verifies", lambda m: m["run"]["audit"]["valid"])
    row("Audit records", lambda m: m["run"]["audit"]["checked"])

    for name in names:
        m = arms[name]
        lines += ["", f"## Arm detail: {name}", "",
                  f"- reasoner `{m['run']['reasoner']}`, adapter `{m['run']['adapter']}`, "
                  f"{m['run']['wall_seconds']}s over {m['run']['ticks']} ticks"]
        if m["run"]["degraded_to_heuristic"]:
            lines.append(f"- **{m['run']['degraded_to_heuristic']} cases degraded to the "
                         "deterministic reasoner** after a model failure")
        lines.append("")
        lines.append("Why cases stopped:")
        lines.append("")
        for reason, count in sorted(m["stopping"]["by_reason"].items(),
                                    key=lambda kv: -kv[1]):
            lines.append(f"- `{reason}` - {count}")
        lines.append("")
        lines.append("Recovery attributed by action:")
        lines.append("")
        for act, amt in sorted(m["attribution"]["recovered_by_action"].items(),
                               key=lambda kv: -kv[1]):
            lines.append(f"- `{act}` - {rs(amt)}")

    lines += ["", "---", "",
              "Generated by `python -m munshi.evaluation.harness`. "
              "Raw figures in `results.json`."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    main()
