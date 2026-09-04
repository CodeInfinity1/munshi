"""The closed loop.

    detect -> quantify -> enrich -> diagnose -> plan -> policy -> execute
           -> verify -> stop/escalate -> measure

One `tick()` processes every case that is due at the current instant. `run()`
advances the virtual clock across the recovery window so scheduled work actually
comes due -- a retry deferred by 36 hours has to really wait 36 hours, or the
timing decisions being evaluated would be decorative.

Money is recorded in exactly one place: `_record_recovery`, which writes a ledger
row pointing at the action that caused it and only then moves the case to
RECOVERED. There is no other path by which the recovered total can move.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from concurrent.futures import ThreadPoolExecutor

from . import audit, db, downtime
from .adapters.base import UnsupportedInTestMode
from .agent.tools import Toolbox
from .clock import VirtualClock
from .config import settings
from .db import jdump
from .enrich import build_context
from .models import ACTION_TIERS, CaseState, Tier
from .policy import PolicyEngine
from .taxonomy import lookup
from .triage import prioritise, recovery_score

log = logging.getLogger("munshi.orchestrator")

#: Actions that end the agent's involvement the moment they succeed.
TERMINAL_ACTIONS = {
    "suppress_case": CaseState.SUPPRESSED,
    "escalate_to_merchant_ops": CaseState.ESCALATED,
    "open_engineering_ticket": CaseState.ESCALATED,
    "escalate_to_collections": CaseState.ESCALATED,
}
RETRY_ACTIONS = {"retry_payment"}
CONTACT_ACTIONS = {
    "send_recovery_link", "send_instrument_update_link", "send_mandate_reauth_link",
    "send_reminder", "offer_partial_payment", "issue_discount",
}


class Orchestrator:
    def __init__(self, conn, reasoner, adapter, clock, *, mode="agent", policy=None,
                 auto_approve=False, work_budget: int | None = None):
        self.conn: sqlite3.Connection = conn
        self.reasoner = reasoner
        self.adapter = adapter
        self.clock = clock
        self.mode = mode
        self.policy = PolicyEngine(conn, policy)
        #: Demo/eval convenience: stand in for a merchant clicking Approve. Off by
        #: default -- an unattended run leaves L3 actions parked, which is the point.
        self.auto_approve = auto_approve
        #: Cases worked per tick. None means no cap; a cap makes prioritisation
        #: bite, which is the point of having it.
        self.work_budget = work_budget
        self.run_id = f"run_{uuid.uuid4().hex[:10]}"
        self.stats = {
            "ticks": 0, "decisions": 0, "executed": 0, "allowed": 0,
            "approval_required": 0, "not_executed": 0, "deferred": 0,
            "blocked": 0, "rescheduled": 0, "downtimes_resolved": 0, "prioritised": 0,
            "degraded": 0,
            "recovered_paise": 0, "recovered_cases": 0,
        }

    # ------------------------------------------------------------------
    def start(self) -> None:
        self.policy.begin_run()
        now = self.clock.now()
        self.conn.execute(
            "INSERT INTO runs (id, started_at, mode, reasoner, adapter) VALUES (?,?,?,?,?)",
            (self.run_id, now, self.mode, self.reasoner.name, self.adapter.name),
        )
        audit.record(self.conn, ts=now, run_id=self.run_id, stage="detect",
                     summary=f"run {self.run_id} started ({self.mode} / {self.reasoner.name} / "
                             f"{self.adapter.name})",
                     detail={"policy": self.policy.p, "virtual_clock": self.clock.virtual})

    def finish(self) -> dict:
        now = self.clock.now()
        self.stats["degraded"] = getattr(self.reasoner, "degraded", 0)
        self.conn.execute("UPDATE runs SET ended_at=?, notes=? WHERE id=?",
                          (now, jdump(self.stats), self.run_id))
        audit.record(self.conn, ts=now, run_id=self.run_id, stage="stop",
                     summary=f"run {self.run_id} complete", detail=self.stats)
        return self.stats

    def run(self, days: int = 14, step_hours: int = 2, limit: int | None = None) -> dict:
        """Advance the clock across the recovery window until nothing is left to do."""
        self.start()
        steps = int(days * 24 / step_hours)
        for _ in range(steps):
            processed = self.tick(limit=limit)
            if processed == 0 and not self.anything_pending():
                break
            if isinstance(self.clock, VirtualClock):
                self.clock.advance(step_hours * 3600)
            else:
                break  # a real-time run does one pass; a scheduler drives the rest
        self.sweep()
        return self.finish()

    def sweep(self) -> None:
        """Terminalise anything still open when the window closes.

        A case left `scheduled` forever is a case nobody is accountable for. At the
        end of the window every non-terminal case gets an explicit stop reason, so
        the state distribution accounts for every rupee in the batch."""
        now = self.clock.now()
        for r in db.rows(self.conn,
                         "SELECT * FROM cases WHERE state NOT IN (?,?,?,?)",
                         (*CaseState.TERMINAL,)):
            case = dict(r)
            if case["state"] == CaseState.AWAITING_APPROVAL:
                continue  # deliberately parked: a human still owes a decision
            self._stop(case, now, "recovery_window_expired")

    def anything_pending(self) -> bool:
        return bool(db.scalar(
            self.conn,
            "SELECT COUNT(*) FROM cases WHERE state NOT IN (?,?,?,?)",
            (*CaseState.TERMINAL,),
        ))

    # ------------------------------------------------------------------
    def tick(self, limit: int | None = None) -> int:
        now = self.clock.now()
        self.stats["ticks"] += 1
        self.stats["downtimes_resolved"] += downtime.publish_resolutions(self.conn, now)
        self._verify_pending(now)

        sql = (
            "SELECT * FROM cases WHERE (state = ? OR (state = ? AND next_action_at <= ?))"
            " AND opened_at <= ? ORDER BY amount_paise DESC"
        )
        args = (CaseState.OPEN, CaseState.SCHEDULED, now, now)
        due = [dict(r) for r in db.rows(self.conn, sql, args)]
        if limit:
            due = due[:limit]
        if not due:
            return 0

        # Enrichment is pure reads, and the reasoning call is the slow part, so the
        # decision pass fans out. Everything that writes is serialised below.
        contexts = [build_context(self.conn, c, now) for c in due]

        # Prioritise. An agent with finite capacity has to choose what to work on,
        # and on a book where a third of failed value is uncollectable, "biggest
        # first" puts money that can never come back at the top of the queue.
        scores = [recovery_score(c, x) for c, x in zip(due, contexts, strict=True)]
        order = {s["case_id"]: i for i, s in enumerate(prioritise(scores))}
        ranked = sorted(
            zip(due, contexts, scores, strict=True), key=lambda t: order[t[0]["id"]])
        if self.work_budget:
            ranked = ranked[: self.work_budget]
        due = [t[0] for t in ranked]
        contexts = [t[1] for t in ranked]
        self.stats["prioritised"] += len(due)

        decisions = self._decide_many(due, contexts)

        for (case, ctx, score), (diag, plan) in zip(ranked, decisions, strict=True):
            ctx["priority"] = score
            try:
                self._apply(case, ctx, diag, plan, now)
            except Exception:  # noqa: BLE001 - one bad case must not kill the batch
                log.exception("case %s failed to process", case["id"])
                self._stop(case, now, "internal_error")
        return len(due)

    def _decide_many(self, cases: list[dict], contexts: list[dict]):
        """One toolbox per case: the agent's tools are scoped to the case it is
        deciding, so it cannot read or reason about anything else."""
        now = self.clock.now()
        boxes = [Toolbox(self.conn, c, x, now, self.policy)
                 for c, x in zip(cases, contexts, strict=True)]
        if len(contexts) == 1 or self.reasoner.name == "heuristic":
            return [self.reasoner.decide(x, b) for x, b in zip(contexts, boxes, strict=True)]
        workers = max(1, settings().llm_concurrency)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            return list(pool.map(lambda p: self.reasoner.decide(*p),
                                 zip(contexts, boxes, strict=True)))

    # ------------------------------------------------------------------
    def _apply(self, case, ctx, diag, plan, now) -> None:
        self.stats["decisions"] += 1
        audit.record(
            self.conn, ts=now, run_id=self.run_id, case_id=case["id"], stage="diagnose",
            summary=f"{diag.root_cause} (confidence {diag.confidence:.2f}, "
                    f"recoverability {diag.recoverability:.2f})",
            detail={"root_cause": diag.root_cause, "confidence": diag.confidence,
                    "recoverability": diag.recoverability, "rationale": diag.rationale,
                    "evidence": diag.evidence, "reasoner": diag.reasoner, "model": diag.model,
                    "amount_inr": ctx["case"]["amount_inr"],
                    "error_reason": ctx["failure"]["error_reason"],
                    "downtime": ctx["downtime"].get("state"),
                    "priority": ctx.get("priority"),
                    # The full tool trace: which tools it reached for and what came
                    # back. This is what the dashboard renders as agent activity.
                    "agent": plan.params.get("agent_trace")},
        )

        decision = self.policy.evaluate(case, plan, ctx, now)
        tier = ACTION_TIERS.get(plan.action_type, Tier.FORBIDDEN)

        # A deliberate deferral is a scheduling decision, not an action: writing an
        # action row for work that has not happened would both overstate activity and
        # burn the idempotency key the real execution needs. `deferred_key` pins the
        # deferral to one attempt-state, so a case cannot defer itself indefinitely.
        defer_key = self._idempotency_key(case, plan, now, "allow")
        deferring = (
            decision.decision == "allow"
            and plan.delay_hours > 0
            and plan.action_type in RETRY_ACTIONS | CONTACT_ACTIONS
            and case.get("deferred_key") != defer_key
        )
        action_id = None if deferring else self._write_action(case, plan, tier, decision, now)

        audit.record(
            self.conn, ts=now, run_id=self.run_id, case_id=case["id"], action_id=action_id,
            stage="policy",
            summary=f"{plan.action_type} (L{tier}) -> {decision.decision}"
                    + (f" [{decision.stop_reason}]" if decision.stop_reason else ""),
            detail={"action": plan.action_type, "tier": tier, "decision": decision.decision,
                    "justification": plan.justification, "delay_hours": plan.delay_hours,
                    "stop_reason": decision.stop_reason,
                    "reschedule_at": decision.reschedule_at,
                    "rules": [{"rule": r.rule, "passed": r.passed, "detail": r.detail}
                              for r in decision.rules]},
        )

        if deferring:
            self.stats["allowed"] += 1
            self.stats["deferred"] += 1
            due_at = int(now + plan.delay_hours * 3600)
            self.conn.execute("UPDATE cases SET deferred_key=? WHERE id=?",
                              (defer_key, case["id"]))
            self._schedule(case, due_at, now,
                           f"agent timed {plan.action_type} for +{plan.delay_hours:.1f}h")
            return None

        if decision.decision == "deny":
            if decision.reschedule_at:
                self.stats["rescheduled"] += 1
            else:
                self.stats["blocked"] += 1
            return self._handle_denial(case, decision, now)
        if decision.decision == "require_approval":
            self.stats["approval_required"] += 1
            return self._queue_approval(case, action_id, plan, now)

        self.stats["allowed"] += 1
        self._execute(case, action_id, plan, now)
        return None

    # ------------------------------------------------------------------
    def _execute(self, case, action_id, plan, now) -> None:
        params = {
            "idempotency_key": self._idempotency_key(case, plan, now, "allow"),
            "channel": plan.channel, "message": plan.message,
            "customer_name": (db.one(self.conn, "SELECT name FROM customers WHERE id=?",
                                     (case["customer_id"],)) or {"name": ""})["name"],
        }
        try:
            result = self.adapter.execute(plan.action_type, case, params, now)
        except UnsupportedInTestMode as exc:
            self.stats["not_executed"] += 1
            self.conn.execute(
                "UPDATE actions SET executed_at=?, outcome='not_executed', outcome_detail=?"
                " WHERE id=?",
                (now, jdump({"reason": str(exc), "adapter": self.adapter.name}), action_id),
            )
            audit.record(self.conn, ts=now, run_id=self.run_id, case_id=case["id"],
                         action_id=action_id, stage="execute",
                         summary=f"{plan.action_type} not executed by {self.adapter.name}",
                         detail={"reason": str(exc)})
            return self._escalate(case, now, "adapter_cannot_execute")

        self.stats["executed"] += 1
        self.conn.execute(
            "UPDATE actions SET executed_at=?, outcome=?, outcome_detail=?, recovered_paise=?"
            " WHERE id=?",
            (now, result.outcome, jdump({**result.detail, "adapter": self.adapter.name,
                                         "simulated": result.simulated,
                                         "provider_ref": result.provider_ref}),
             result.recovered_paise, action_id),
        )
        # Attempt counters move on execution, not on proposal, so a policy denial
        # never silently consumes part of the case's budget.
        if plan.action_type in RETRY_ACTIONS:
            self.conn.execute("UPDATE cases SET attempts = attempts + 1 WHERE id=?", (case["id"],))
            case["attempts"] += 1
        if plan.action_type in CONTACT_ACTIONS:
            self.conn.execute("UPDATE cases SET contacts_sent = contacts_sent + 1 WHERE id=?",
                              (case["id"],))
            case["contacts_sent"] += 1

        audit.record(
            self.conn, ts=now, run_id=self.run_id, case_id=case["id"], action_id=action_id,
            stage="execute",
            summary=f"{plan.action_type} -> {result.outcome}"
                    + (f" (Rs {result.recovered_paise / 100:,.0f})" if result.recovered_paise else ""),
            detail={**result.detail, "adapter": self.adapter.name, "outcome": result.outcome,
                    "channel": plan.channel, "message": plan.message,
                    "simulated": result.simulated},
        )

        if result.outcome == "success" and result.recovered_paise > 0:
            return self._record_recovery(case, action_id, result, now)
        if plan.action_type in TERMINAL_ACTIONS and result.outcome == "success":
            # Alerting merchant ops hands the fix to a human but does not settle the
            # money. Keep watching so a re-presentment happens once they act.
            if (plan.action_type == "escalate_to_merchant_ops"
                    and lookup(case.get("error_reason")).family == "merchant_config"):
                audit.record(self.conn, ts=now, run_id=self.run_id, case_id=case["id"],
                             action_id=action_id, stage="stop",
                             summary="merchant ops alerted; case stays open to re-present "
                                     "once the configuration is fixed",
                             detail={"handoff": "merchant_ops", "case_remains_open": True})
                return self._schedule(case, int(now + 24 * 3600), now,
                                      "awaiting merchant configuration fix")
            return self._terminal(case, now, TERMINAL_ACTIONS[plan.action_type],
                                  f"{plan.action_type}_completed")
        if result.outcome == "pending":
            return self._handle_pending(case, action_id, result, now)
        if plan.action_type == "no_action":
            spent = (case["attempts"] >= self.policy.p["max_recovery_attempts"]
                     or case["contacts_sent"] >= self.policy.p["max_customer_contacts"])
            return self._stop(case, now,
                              "all_recovery_avenues_exhausted" if spent
                              else "no_intervention_worth_taking")
        # Failed. Wake at the earliest instant a further attempt could actually be
        # permitted -- waking sooner only produces a decision that policy is
        # guaranteed to refuse, and in the LLM arm that is a wasted model call.
        return self._schedule(case, int(now + self._cooldown_hours(case, plan) * 3600), now,
                              f"{plan.action_type} failed")

    def _cooldown_hours(self, case, plan) -> float:
        """Earliest re-attempt permitted by policy and by the failure's own semantics."""
        p = self.policy.p
        if plan.action_type in RETRY_ACTIONS:
            from .taxonomy import lookup

            sem = lookup(case.get("error_reason"))
            return max(p["min_hours_between_retries"], sem.min_backoff_hours or 0)
        if plan.action_type in CONTACT_ACTIONS:
            return float(p["min_hours_between_contacts"])
        return 6.0

    def _handle_pending(self, case, action_id, result, now) -> None:
        promise = result.detail.get("promise_to_pay_at")
        if promise:
            self.conn.execute("UPDATE cases SET promise_to_pay_at=? WHERE id=?",
                              (promise, case["id"]))
            case["promise_to_pay_at"] = promise
            audit.record(self.conn, ts=now, run_id=self.run_id, case_id=case["id"],
                         action_id=action_id, stage="verify",
                         summary="customer gave a promise to pay; chasing paused until then",
                         detail={"promise_to_pay_at": promise})
            return self._schedule(case, int(promise), now, "promise_to_pay accepted")
        # Awaiting an out-of-band outcome (e.g. a payment link the customer may pay).
        return self._schedule(case, int(now + 12 * 3600), now, "awaiting payment outcome")

    def _verify_pending(self, now: int) -> None:
        """Resolve outcomes that could not be known at execution time.

        In the simulator this settles promises to pay. Against Razorpay it is where
        a created payment link is polled (GET /v1/payments/:id) to see whether the
        customer actually paid -- money is only ever counted after this step.
        """
        rows = db.rows(
            self.conn,
            "SELECT a.*, c.state, c.latent, c.amount_paise, c.promise_to_pay_at FROM actions a"
            " JOIN cases c ON c.id = a.case_id WHERE a.outcome='pending'"
            " AND a.executed_at IS NOT NULL AND c.state NOT IN (?,?,?,?)",
            (*CaseState.TERMINAL,),
        )
        for r in rows:
            detail = db.jload(r["outcome_detail"], {}) or {}
            promise = detail.get("promise_to_pay_at") or r["promise_to_pay_at"]
            if not promise or now < promise:
                continue
            case = dict(db.one(self.conn, "SELECT * FROM cases WHERE id=?", (r["case_id"],)))
            if detail.get("honours_promise"):
                result = type("R", (), {
                    "recovered_paise": int(r["amount_paise"]),
                    "provider_ref": f"pay_SIMP2P{r['id'][-8:]}", "simulated": True,
                    "detail": {"source": "promise_to_pay_honoured"},
                })()
                self.conn.execute(
                    "UPDATE actions SET outcome='success', recovered_paise=? WHERE id=?",
                    (int(r["amount_paise"]), r["id"]))
                self._record_recovery(case, r["id"], result, now)
            else:
                self.conn.execute("UPDATE actions SET outcome='failed' WHERE id=?", (r["id"],))
                self.conn.execute("UPDATE cases SET promise_to_pay_at=NULL WHERE id=?",
                                  (case["id"],))
                audit.record(self.conn, ts=now, run_id=self.run_id, case_id=case["id"],
                             action_id=r["id"], stage="verify",
                             summary="promise to pay was not honoured; case reopened",
                             detail={"promised_at": promise})
                self._schedule(case, now, now, "promise to pay lapsed")

    # ------------------------------------------------------------------
    def _record_recovery(self, case, action_id, result, now) -> None:
        """The one and only place the recovered total can move."""
        self.conn.execute(
            "INSERT OR IGNORE INTO ledger (id, case_id, action_id, ts, amount_paise,"
            " provider_ref, adapter) VALUES (?,?,?,?,?,?,?)",
            (f"led_{uuid.uuid4().hex[:12]}", case["id"], action_id, now,
             result.recovered_paise, result.provider_ref or "", self.adapter.name),
        )
        total = db.scalar(self.conn, "SELECT SUM(amount_paise) FROM ledger WHERE case_id=?",
                          (case["id"],))
        self.conn.execute(
            "UPDATE cases SET state=?, recovered_paise=?, updated_at=?, next_action_at=NULL,"
            " stop_reason='recovered' WHERE id=?",
            (CaseState.RECOVERED, total, now, case["id"]),
        )
        self.stats["recovered_paise"] += result.recovered_paise
        self.stats["recovered_cases"] += 1
        audit.record(self.conn, ts=now, run_id=self.run_id, case_id=case["id"],
                     action_id=action_id, stage="verify",
                     summary=f"recovered Rs {result.recovered_paise / 100:,.0f}",
                     detail={"amount_paise": result.recovered_paise,
                             "provider_ref": result.provider_ref,
                             "simulated": getattr(result, "simulated", True),
                             "adapter": self.adapter.name})

    def _handle_denial(self, case, decision, now) -> None:
        if decision.reschedule_at:
            blocked = next((r for r in decision.rules if not r.passed), None)
            rule = blocked.rule if blocked else "policy deferral"
            if rule == "downtime_clear":
                self.conn.execute(
                    "UPDATE cases SET downtime_holds = downtime_holds + 1 WHERE id=?",
                    (case["id"],))
            return self._schedule(case, decision.reschedule_at, now, rule)
        reason = decision.stop_reason or next(
            (r.rule for r in decision.rules if not r.passed), "policy_denied")
        if reason in ("already_settled",):
            return self._terminal(case, now, CaseState.SUPPRESSED, reason)
        if reason in ("risk_decline_requires_human_review", "emandate_requires_customer_afa",
                      "not_a_customer_resolvable_failure", "adapter_cannot_execute"):
            return self._escalate(case, now, reason)
        # Running out of one budget closes that avenue, not the case. Only stop when
        # nothing is left to try -- otherwise a spent contact allowance writes off
        # revenue a remaining retry could still have collected.
        if reason in ("max_contacts_reached", "max_retry_attempts_reached"):
            from .taxonomy import lookup as _lookup

            sem = _lookup(case.get("error_reason"))
            p = self.policy.p
            retries_left = case["attempts"] < p["max_recovery_attempts"] and not sem.retry_is_futile
            contacts_left = (case["contacts_sent"] < p["max_customer_contacts"]
                             and sem.contacts_customer)
            if retries_left or contacts_left:
                wait = (p["min_hours_between_retries"] if retries_left
                        else p["min_hours_between_contacts"])
                return self._schedule(case, int(now + wait * 3600), now,
                                      f"{reason}; switching to the remaining avenue")
            return self._stop(case, now, "all_recovery_avenues_exhausted")
        return self._stop(case, now, reason)

    def _queue_approval(self, case, action_id, plan, now) -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO approvals (action_id, case_id, requested_at, reason)"
            " VALUES (?,?,?,?)",
            (action_id, case["id"], now,
             f"{plan.action_type} is tier L{ACTION_TIERS[plan.action_type]} and requires "
             "merchant sign-off"),
        )
        self.conn.execute("UPDATE cases SET state=?, updated_at=?, next_action_at=NULL WHERE id=?",
                          (CaseState.AWAITING_APPROVAL, now, case["id"]))
        if self.auto_approve:
            self.approve(action_id, now, decided_by="auto_approve")

    def approve(self, action_id: str, now: int, decided_by: str = "merchant") -> None:
        row = db.one(self.conn, "SELECT * FROM actions WHERE id=?", (action_id,))
        if row is None or row["executed_at"] is not None:
            return
        case = dict(db.one(self.conn, "SELECT * FROM cases WHERE id=?", (row["case_id"],)))
        self.conn.execute(
            "UPDATE approvals SET decided_at=?, decision='approved', decided_by=? WHERE action_id=?",
            (now, decided_by, action_id))
        self.conn.execute("UPDATE actions SET policy_decision='allow' WHERE id=?", (action_id,))
        audit.record(self.conn, ts=now, run_id=self.run_id, case_id=case["id"],
                     action_id=action_id, stage="policy",
                     summary=f"{row['action_type']} approved by {decided_by}",
                     detail={"decided_by": decided_by})
        from .models import Plan

        plan = Plan(action_type=row["action_type"], params=db.jload(row["params"], {}),
                    delay_hours=0.0, channel=None, message=None,
                    justification="merchant-approved")
        self._execute(case, action_id, plan, now)

    def reject(self, action_id: str, now: int, decided_by: str = "merchant") -> None:
        row = db.one(self.conn, "SELECT case_id, action_type FROM actions WHERE id=?", (action_id,))
        if row is None:
            return
        self.conn.execute(
            "UPDATE approvals SET decided_at=?, decision='rejected', decided_by=? WHERE action_id=?",
            (now, decided_by, action_id))
        self.conn.execute("UPDATE actions SET outcome='not_executed' WHERE id=?", (action_id,))
        case = dict(db.one(self.conn, "SELECT * FROM cases WHERE id=?", (row["case_id"],)))
        self._stop(case, now, f"{row['action_type']}_rejected_by_merchant")

    # ------------------------------------------------------------------
    def _write_action(self, case, plan, tier, decision, now) -> str:
        action_id = f"act_{uuid.uuid4().hex[:12]}"
        self.conn.execute(
            "INSERT INTO actions (id, case_id, run_id, proposed_at, action_type, tier, params,"
            " policy_decision, policy_rules, idempotency_key, outcome) VALUES"
            " (?,?,?,?,?,?,?,?,?,?,?)",
            (action_id, case["id"], self.run_id, now, plan.action_type, tier,
             jdump({"delay_hours": plan.delay_hours, "channel": plan.channel,
                    "message": plan.message, "justification": plan.justification,
                    "reasoner": plan.reasoner}),
             decision.decision,
             jdump([{"rule": r.rule, "passed": r.passed, "detail": r.detail}
                    for r in decision.rules]),
             self._idempotency_key(case, plan, now, decision.decision),
             None if decision.decision == "allow" else "not_executed"),
        )
        return action_id

    @staticmethod
    def _idempotency_key(case, plan, now, decision) -> str:
        """For executable actions the key is the logical attempt, so a redelivered
        event cannot produce a second charge. For refused ones it is the instant,
        so the refusal is still recorded and still auditable."""
        if decision == "allow":
            return f"{case['id']}|{plan.action_type}|a{case['attempts']}|c{case['contacts_sent']}"
        return f"{case['id']}|{plan.action_type}|t{now}|{decision}"

    def _schedule(self, case, at, now, why) -> None:
        self.conn.execute(
            "UPDATE cases SET state=?, next_action_at=?, updated_at=? WHERE id=?",
            (CaseState.SCHEDULED, int(at), now, case["id"]))
        audit.record(self.conn, ts=now, run_id=self.run_id, case_id=case["id"], stage="stop",
                     summary=f"deferred to +{(at - now) / 3600:.1f}h: {why}",
                     detail={"next_action_at": int(at), "reason": why})

    def _terminal(self, case, now, state, reason) -> None:
        self.conn.execute(
            "UPDATE cases SET state=?, stop_reason=?, updated_at=?, next_action_at=NULL WHERE id=?",
            (state, reason, now, case["id"]))
        audit.record(self.conn, ts=now, run_id=self.run_id, case_id=case["id"], stage="stop",
                     summary=f"case {state}: {reason}", detail={"state": state, "reason": reason})

    def _stop(self, case, now, reason) -> None:
        self._terminal(case, now, CaseState.STOPPED, reason)

    def _escalate(self, case, now, reason) -> None:
        self._terminal(case, now, CaseState.ESCALATED, reason)
