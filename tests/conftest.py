import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
# Tests must never reach a real API or a real payment rail. The whole suite,
# including every agent path, has to pass with no credential present.
os.environ.pop("GROQ_API_KEY", None)
os.environ.pop("MUNSHI_REASONER", None)
os.environ["MUNSHI_ADAPTER"] = "simulator"
os.environ["MUNSHI_TIMEZONE"] = "Asia/Kolkata"
os.environ["MUNSHI_API_TOKEN"] = "test-token"

import pytest  # noqa: E402

from munshi import db  # noqa: E402
from munshi.clock import VirtualClock  # noqa: E402
from munshi.seed.generate import BATCH_START  # noqa: E402
from munshi.seed.load import load  # noqa: E402


@pytest.fixture
def conn(tmp_path):
    c = db.reset(tmp_path / "t.db")
    yield c
    c.close()


@pytest.fixture
def seeded(tmp_path):
    c = db.reset(tmp_path / "seeded.db")
    load(c, n=60, seed=7)
    yield c
    c.close()


@pytest.fixture
def clock():
    return VirtualClock(BATCH_START)


def make_case(conn, **over):
    """Insert one case with sane defaults; override any field."""
    row = {
        "id": "case_t1", "kind": "payment_failure", "entity_id": "pay_T1",
        "customer_id": "cust_t1", "amount_paise": 250000, "currency": "INR",
        "opened_at": BATCH_START - 3600, "state": "open", "method": "card",
        "instrument": '{"issuer":"HDFC","network":"VISA"}', "error_source": "customer",
        "error_step": "payment_authentication", "error_reason": "insufficient_funds",
        "prior_attempts": 0, "days_overdue": 0, "mrr_paise": 0, "latent": "{}",
        "attempts": 0, "contacts_sent": 0,
    }
    row.update(over)
    conn.execute(
        "INSERT OR REPLACE INTO customers (id,name,email,phone,segment,tenure_days,"
        "lifetime_paise,successful_payments,failed_payments,prior_recoveries,"
        "contact_opt_out,preferred_channel,typical_success_hour) VALUES"
        " (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (row["customer_id"], "Test Customer", "t@example.com", "+919800000000", "consumer",
         400, 5000000, 12, 1, 0, over.pop("contact_opt_out", 0), "email", 19),
    )
    conn.execute(
        "INSERT OR REPLACE INTO cases (id,kind,entity_id,customer_id,amount_paise,currency,"
        "opened_at,updated_at,state,method,instrument,error_source,error_step,error_reason,"
        "prior_attempts,days_overdue,mrr_paise,latent,attempts,contacts_sent) VALUES"
        " (:id,:kind,:entity_id,:customer_id,:amount_paise,:currency,:opened_at,:opened_at,"
        ":state,:method,:instrument,:error_source,:error_step,:error_reason,:prior_attempts,"
        ":days_overdue,:mrr_paise,:latent,:attempts,:contacts_sent)",
        row,
    )
    return dict(conn.execute("SELECT * FROM cases WHERE id=?", (row["id"],)).fetchone())
