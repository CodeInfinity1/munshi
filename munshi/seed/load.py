"""Load a generated batch into SQLite, going through the real ingest path."""

from __future__ import annotations

import sqlite3

from .. import db
from ..db import jdump
from ..models import CaseState
from .generate import BATCH_START, build


def load(conn: sqlite3.Connection, n: int = 320, seed: int = 20260824) -> dict:
    batch = build(n=n, seed=seed)
    with db.transaction(conn):
        conn.executemany(
            "INSERT INTO customers (id,name,email,phone,segment,tenure_days,lifetime_paise,"
            "successful_payments,failed_payments,prior_recoveries,contact_opt_out,"
            "preferred_channel,typical_success_hour) VALUES "
            "(:id,:name,:email,:phone,:segment,:tenure_days,:lifetime_paise,"
            ":successful_payments,:failed_payments,:prior_recoveries,:contact_opt_out,"
            ":preferred_channel,:typical_success_hour)",
            batch["customers"],
        )
        conn.executemany(
            "INSERT INTO downtimes (id,method,instrument,begin_at,end_at,status,severity,"
            "scheduled) VALUES (?,?,?,?,?,?,?,?)",
            [(d["id"], d["method"], jdump(d["instrument"]), d["begin_at"], d["end_at"],
              d["status"], d["severity"], d["scheduled"]) for d in batch["downtimes"]],
        )
        for c in batch["cases"]:
            conn.execute(
                "INSERT INTO cases (id,kind,entity_id,customer_id,amount_paise,currency,"
                "opened_at,updated_at,state,method,instrument,error_source,error_step,"
                "error_reason,prior_attempts,days_overdue,mrr_paise,latent) VALUES "
                "(:id,:kind,:entity_id,:customer_id,:amount_paise,:currency,:opened_at,"
                ":opened_at,'open',:method,:instrument,:error_source,:error_step,"
                ":error_reason,:prior_attempts,:days_overdue,:mrr_paise,:latent)",
                c,
            )
    return batch


def seed_database(path=None, n: int = 320, seed: int = 20260824) -> dict:
    conn = db.reset(path)
    batch = load(conn, n=n, seed=seed)
    from ..ingest import ingest_batch

    stats = ingest_batch(conn, batch["events"], now=BATCH_START)
    conn.close()
    return {**batch["meta"], **stats}


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Seed the Munshi demo database.")
    ap.add_argument("-n", type=int, default=320)
    ap.add_argument("--seed", type=int, default=20260824)
    ap.add_argument("--db", default=None)
    a = ap.parse_args()
    out = seed_database(a.db, n=a.n, seed=a.seed)
    print(f"seeded {out['n']} cases  |  events {out['ingested']} accepted, "
          f"{out['duplicates']} duplicate replays rejected  |  seed {out['seed']}")
    _ = CaseState  # state vocabulary is authoritative; imported for the reader
