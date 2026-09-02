"""HTTP API and static host for the control centre.

Read routes are open (this is a demo dashboard). Every route that changes state
-- running the agent, reseeding, approving an action -- requires a bearer token
and is rate limited. The webhook route authenticates by HMAC instead.

`GET /api/health` reports which reasoner and which adapter are actually in use,
so the UI can state plainly whether it is looking at Claude or the deterministic
fallback, and whether money movement is simulated. Nothing in this system is
allowed to imply a payment rail ran when it did not.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from pathlib import Path

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import audit, db
from .adapters.razorpay_test import build_adapter
from .clock import VirtualClock
from .config import settings
from .db import jload
from .enrich import build_context
from .evaluation.metrics import compute, unretryable_share
from .models import ACTION_TIERS, CaseState
from .orchestrator import Orchestrator
from .policy import POLICY
from .reason import build_reasoner
from .seed.generate import BATCH_START
from .seed.load import seed_database
from .taxonomy import families, source_semantics

app = FastAPI(title="Munshi", version="0.1.0",
              description="Bounded revenue-recovery agent for Razorpay merchants")

STATIC = Path(__file__).parent / "static"
EVAL = Path("evaluation/results.json")

#: Progress of the currently running batch, polled by the dashboard.
_run_state: dict = {"status": "idle", "started_at": None, "stats": {}, "error": None}
_run_lock = threading.Lock()
_hits: dict[str, deque] = {}


# ---------------------------------------------------------------------------
# auth + rate limiting
# ---------------------------------------------------------------------------
def require_token(authorization: str = Header(default="")) -> None:
    expected = settings().api_token
    supplied = authorization.removeprefix("Bearer ").strip()
    # Constant-time comparison: a token check that leaks timing is not a check.
    import hmac

    if not supplied or not hmac.compare_digest(supplied, expected):
        raise HTTPException(401, "missing or invalid bearer token")


def rate_limit(request: Request) -> None:
    key = request.client.host if request.client else "unknown"
    now = time.time()
    window = _hits.setdefault(key, deque())
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= settings().rate_limit_per_minute:
        raise HTTPException(429, "rate limit exceeded")
    window.append(now)


GUARD = [Depends(require_token), Depends(rate_limit)]


def conn():
    c = db.connect()
    try:
        yield c
    finally:
        c.close()


# ---------------------------------------------------------------------------
# read routes
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    s = settings()
    return {
        "ok": True,
        **s.describe(),
        # The single most important honesty flag in the product.
        "money_movement": ("simulated" if s.effective_adapter == "simulator"
                           else "razorpay test mode"),
        "database": str(s.db_path),
        "seeded": s.db_path.exists(),
    }


@app.get("/api/policy")
def policy():
    """The bounds, verbatim, so a merchant can read what the agent may do."""
    return {
        "limits": POLICY,
        "tiers": {
            "L0": {"name": "Observe", "description": "Records only; never reaches a customer "
                                                     "or moves money.",
                   "actions": _at_tier(0)},
            "L1": {"name": "Recommend", "description": "Surfaced to the merchant, never "
                                                       "auto-executed.", "actions": _at_tier(1)},
            "L2": {"name": "Autonomous", "description": "Executed by the agent inside every "
                                                        "limit below.", "actions": _at_tier(2)},
            "L3": {"name": "Approval required", "description": "Queued for explicit merchant "
                                                               "sign-off.", "actions": _at_tier(3)},
            "L4": {"name": "Forbidden", "description": "The agent may never execute this, with "
                                                       "or without approval.",
                   "actions": _at_tier(4)},
        },
        "failure_families": families(),
        "razorpay_error_sources": source_semantics(),
        "regulatory": {
            "rbi_fair_practices_contact_window": "08:00-19:00 local time, all channels "
                                                 "including automated SMS, WhatsApp and email",
            "rbi_emandate_pre_debit_notice_hours": 24,
            "rbi_emandate_afa_free_ceiling_inr": 15000,
            "rbi_emandate_afa_free_ceiling_elevated_inr": 100000,
            "npci_non_peak_debit_windows": "before 10:00, 13:00-17:00, after 21:30",
        },
    }


def _at_tier(t: int) -> list[str]:
    return sorted(a for a, tier in ACTION_TIERS.items() if tier == t)


@app.get("/api/overview")
def overview(c=Depends(conn)):
    m = compute(c)
    run = db.one(c, "SELECT * FROM runs ORDER BY started_at DESC LIMIT 1")
    return {
        **m,
        "batch": unretryable_share(c),
        "run": dict(run) if run else None,
        "run_state": _run_state,
        "audit": audit.verify(c),
        "config": settings().describe(),
    }


@app.get("/api/cases")
def cases(state: str | None = None, kind: str | None = None, q: str | None = None,
          limit: int = Query(200, le=1000), c=Depends(conn)):
    sql = ("SELECT c.*, cu.name AS customer_name, cu.segment, cu.contact_opt_out"
           " FROM cases c JOIN customers cu ON cu.id = c.customer_id WHERE 1=1")
    args: list = []
    if state:
        sql += " AND c.state = ?"
        args.append(state)
    if kind:
        sql += " AND c.kind = ?"
        args.append(kind)
    if q:
        sql += " AND (c.id LIKE ? OR c.entity_id LIKE ? OR cu.name LIKE ? OR c.error_reason LIKE ?)"
        args += [f"%{q}%"] * 4
    sql += " ORDER BY c.amount_paise DESC LIMIT ?"
    args.append(limit)
    return {"cases": [_case_row(r) for r in db.rows(c, sql, tuple(args))]}


def _case_row(r) -> dict:
    d = dict(r)
    d["instrument"] = jload(d.get("instrument"), {})
    d.pop("latent", None)  # ground truth is never served to a client
    return d


@app.get("/api/cases/{case_id}")
def case_detail(case_id: str, c=Depends(conn)):
    row = db.one(c, "SELECT c.*, cu.name AS customer_name, cu.segment, cu.contact_opt_out"
                    " FROM cases c JOIN customers cu ON cu.id = c.customer_id"
                    " WHERE c.id = ?", (case_id,))
    if row is None:
        raise HTTPException(404, "case not found")
    now = _reference_now(c)
    actions = [
        {**dict(a), "params": jload(a["params"], {}),
         "policy_rules": jload(a["policy_rules"], []),
         "outcome_detail": jload(a["outcome_detail"], {})}
        for a in db.rows(c, "SELECT * FROM actions WHERE case_id=? ORDER BY proposed_at", (case_id,))
    ]
    trail = [
        {**dict(t), "detail": jload(t["detail"], {})}
        for t in db.rows(c, "SELECT * FROM audit WHERE case_id=? ORDER BY seq", (case_id,))
    ]
    return {
        "case": _case_row(row),
        "context": build_context(c, row, now),
        "actions": actions,
        "audit": trail,
        "ledger": [dict(x) for x in db.rows(
            c, "SELECT * FROM ledger WHERE case_id=? ORDER BY ts", (case_id,))],
    }


@app.get("/api/audit")
def audit_stream(case_id: str | None = None, stage: str | None = None,
                 limit: int = Query(300, le=2000), c=Depends(conn)):
    sql, args = "SELECT * FROM audit WHERE 1=1", []
    if case_id:
        sql += " AND case_id = ?"
        args.append(case_id)
    if stage:
        sql += " AND stage = ?"
        args.append(stage)
    sql += " ORDER BY seq DESC LIMIT ?"
    args.append(limit)
    return {
        "verification": audit.verify(c),
        "records": [{**dict(r), "detail": jload(r["detail"], {})}
                    for r in db.rows(c, sql, tuple(args))],
    }


@app.get("/api/approvals")
def approvals(c=Depends(conn)):
    rows = db.rows(c, "SELECT ap.*, a.action_type, a.tier, a.params, a.policy_rules,"
                      " cs.amount_paise, cs.error_reason, cs.kind, cu.name AS customer_name"
                      " FROM approvals ap JOIN actions a ON a.id = ap.action_id"
                      " JOIN cases cs ON cs.id = ap.case_id"
                      " JOIN customers cu ON cu.id = cs.customer_id"
                      " ORDER BY cs.amount_paise DESC")
    return {"approvals": [
        {**dict(r), "params": jload(r["params"], {}), "policy_rules": jload(r["policy_rules"], [])}
        for r in rows
    ]}


@app.get("/api/evaluation")
def evaluation():
    """The committed batch evaluation, so a reviewer sees the numbers without
    running anything."""
    if not EVAL.exists():
        raise HTTPException(404, "no evaluation results; run python -m munshi.evaluation.harness")
    return json.loads(EVAL.read_text())


# ---------------------------------------------------------------------------
# write routes
# ---------------------------------------------------------------------------
@app.post("/api/seed", dependencies=GUARD)
def seed(n: int = Body(320, embed=True), seed: int = Body(20260824, embed=True)):
    out = seed_database(n=n, seed=seed)
    _run_state.update(status="idle", stats={}, error=None, started_at=None)
    return out


@app.post("/api/run", dependencies=GUARD)
def run(days: int = Body(14, embed=True), step_hours: int = Body(2, embed=True),
        tick_delay_ms: int = Body(0, embed=True),
        auto_approve: bool = Body(False, embed=True)):
    """Start a batch in the background. The dashboard polls /api/overview.

    `tick_delay_ms` slows the virtual clock's wall-clock pace so a live demo can
    watch the state change. It affects pacing only, never outcomes.
    """
    with _run_lock:
        if _run_state["status"] == "running":
            raise HTTPException(409, "a run is already in progress")
        _run_state.update(status="running", started_at=int(time.time()), stats={}, error=None)

    def _work():
        c = db.connect()
        try:
            orch = Orchestrator(c, build_reasoner(), build_adapter(),
                                VirtualClock(_reference_now(c)), auto_approve=auto_approve)
            orch.start()
            steps = int(days * 24 / step_hours)
            for _ in range(steps):
                processed = orch.tick()
                _run_state["stats"] = dict(orch.stats)
                if processed == 0 and not orch.anything_pending():
                    break
                orch.clock.advance(step_hours * 3600)
                if tick_delay_ms:
                    time.sleep(tick_delay_ms / 1000)
            orch.sweep()
            _run_state["stats"] = orch.finish()
            _run_state["status"] = "done"
        except Exception as exc:  # noqa: BLE001 - surfaced to the UI, never swallowed
            _run_state.update(status="error", error=f"{type(exc).__name__}: {exc}")
        finally:
            c.close()

    threading.Thread(target=_work, daemon=True).start()
    return {"status": "started", "config": settings().describe()}


@app.post("/api/approvals/{action_id}/{decision}", dependencies=GUARD)
def decide(action_id: str, decision: str, c=Depends(conn)):
    if decision not in ("approve", "reject"):
        raise HTTPException(400, "decision must be approve or reject")
    row = db.one(c, "SELECT * FROM approvals WHERE action_id = ?", (action_id,))
    if row is None:
        raise HTTPException(404, "no pending approval for that action")
    if row["decided_at"]:
        raise HTTPException(409, f"already {row['decision']}")

    orch = Orchestrator(c, build_reasoner(), build_adapter(), VirtualClock(_reference_now(c)))
    # Attribute the decision to the run that proposed the action, so the audit
    # trail keeps one coherent run id rather than inventing a new one per click.
    action = db.one(c, "SELECT run_id FROM actions WHERE id = ?", (action_id,))
    if action and action["run_id"]:
        orch.run_id = action["run_id"]
    now = _reference_now(c)
    if decision == "approve":
        orch.approve(action_id, now, decided_by="merchant")
    else:
        orch.reject(action_id, now, decided_by="merchant")
    return {"status": decision, "case": _case_row(
        db.one(c, "SELECT * FROM cases WHERE id=?", (row["case_id"],)))}


@app.post("/webhooks/razorpay")
async def razorpay_webhook(request: Request,
                           x_razorpay_signature: str = Header(default=""),
                           c=Depends(conn)):
    from . import webhooks

    raw = await request.body()  # verify the bytes, before anything parses them
    try:
        payload = json.loads(raw or b"{}")
        result = webhooks.handle(c, raw, x_razorpay_signature, payload)
    except PermissionError as exc:
        raise HTTPException(401, str(exc)) from exc
    except json.JSONDecodeError as exc:
        raise HTTPException(400, "malformed JSON body") from exc
    # Always 2xx on a verified event, including duplicates: a non-2xx makes
    # Razorpay redeliver something we have already handled.
    return JSONResponse(result, status_code=200)


# ---------------------------------------------------------------------------
def _reference_now(c) -> int:
    """The clock the demo runs on.

    The seeded batch is anchored to a fixed instant so evaluation is reproducible.
    Using wall time here would place every case outside its own recovery window.
    """
    last = db.scalar(c, "SELECT MAX(ts) FROM audit", default=0)
    return max(BATCH_START, last or 0)


_ = CaseState  # state vocabulary is authoritative for the client

if STATIC.exists():
    app.mount("/assets", StaticFiles(directory=STATIC / "assets"), name="assets")

    @app.get("/{path:path}")
    def spa(path: str):
        target = STATIC / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(STATIC / "index.html")
