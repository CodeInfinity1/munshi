"""Append-only, hash-chained audit log.

Every audit row commits to its predecessor:

    hash_n = sha256(prev_hash || canonical_json(row_n))

so editing or deleting any historical row invalidates every hash after it.
`verify()` walks the chain and reports the first break. This is the difference
between a log you can read and a log you can trust: for a system that moves
money, "we wrote it down" is not the same claim as "nobody changed it".

The `detail` payload holds structured decision records -- inputs, rule verdicts,
outcomes. It deliberately does NOT store raw model chain-of-thought; what gets
persisted is the short rationale the model was asked to emit as part of its
structured output.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from .db import jdump

GENESIS = "0" * 64


def _digest(prev_hash: str, payload: dict) -> str:
    return hashlib.sha256((prev_hash + jdump(payload)).encode()).hexdigest()


def record(
    conn: sqlite3.Connection,
    *,
    ts: int,
    stage: str,
    summary: str,
    detail: dict,
    run_id: str | None = None,
    case_id: str | None = None,
    action_id: str | None = None,
) -> str:
    prev = conn.execute("SELECT hash FROM audit ORDER BY seq DESC LIMIT 1").fetchone()
    prev_hash = prev["hash"] if prev else GENESIS
    payload = {
        "ts": ts,
        "run_id": run_id,
        "case_id": case_id,
        "action_id": action_id,
        "stage": stage,
        "summary": summary,
        "detail": detail,
    }
    h = _digest(prev_hash, payload)
    conn.execute(
        "INSERT INTO audit (ts, run_id, case_id, action_id, stage, summary, detail,"
        " prev_hash, hash) VALUES (?,?,?,?,?,?,?,?,?)",
        (ts, run_id, case_id, action_id, stage, summary, jdump(detail), prev_hash, h),
    )
    return h


def verify(conn: sqlite3.Connection) -> dict:
    """Recompute the whole chain. Returns where it breaks, if it does."""
    prev_hash = GENESIS
    checked = 0
    for row in conn.execute("SELECT * FROM audit ORDER BY seq ASC"):
        payload = {
            "ts": row["ts"],
            "run_id": row["run_id"],
            "case_id": row["case_id"],
            "action_id": row["action_id"],
            "stage": row["stage"],
            "summary": row["summary"],
            "detail": json.loads(row["detail"]),
        }
        if row["prev_hash"] != prev_hash:
            return {"valid": False, "checked": checked, "broken_at": row["seq"],
                    "error": "prev_hash does not match the preceding row's hash"}
        expected = _digest(prev_hash, payload)
        if expected != row["hash"]:
            return {"valid": False, "checked": checked, "broken_at": row["seq"],
                    "error": "row content does not match its recorded hash"}
        prev_hash = row["hash"]
        checked += 1
    return {"valid": True, "checked": checked, "head": prev_hash}
