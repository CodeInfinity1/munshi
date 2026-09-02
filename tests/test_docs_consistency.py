"""The README quotes figures that are generated elsewhere. Numbers in prose rot
silently; this makes them fail loudly instead.

Caught a real error: the annualised-MRR figures were out by a factor of ten.
"""

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "evaluation" / "results.json"


def indian(rupees: float) -> str:
    """Indian digit grouping: 18320352 -> 1,83,20,352."""
    s = str(int(rupees))
    if len(s) <= 3:
        return s
    head, tail, parts = s[:-3], s[-3:], []
    while len(head) > 2:
        parts.insert(0, head[-2:])
        head = head[:-2]
    if head:
        parts.insert(0, head)
    return ",".join(parts) + "," + tail


@pytest.mark.skipif(not RESULTS.exists(), reason="no committed evaluation")
@pytest.mark.parametrize("doc", ["README.md", "docs/evaluation.md", "docs/submission.md"])
def test_quoted_money_matches_the_committed_evaluation(doc):
    arms = json.loads(RESULTS.read_text())["arms"]
    text = (ROOT / doc).read_text()

    expected = {
        indian(arms["baseline"]["money"]["at_risk_paise"] / 100),
        indian(arms["baseline"]["money"]["recovered_paise"] / 100),
        indian(arms["agent-heuristic"]["money"]["recovered_paise"] / 100),
        indian(arms["agent-heuristic-approved"]["money"]["recovered_paise"] / 100),
        indian(arms["agent-heuristic"]["money"]["held_for_approval_paise"] / 100),
        indian(arms["baseline"]["batch"]["structurally_unretryable_paise"] / 100),
    }
    # Any rupee figure with Indian grouping in the prose must be one we can account
    # for: a headline number, an MRR figure, or a policy limit.
    allowed = expected | {
        indian(a["money"][k] / 100)
        for a in arms.values()
        for k in ("latently_recoverable_paise", "annualised_mrr_at_risk_paise",
                  "annualised_mrr_protected_paise", "escalated_paise")
    } | {"2,00,000", "1,00,000", "15,000", "25,000", "2,00,00,000", "1,83,20,352",
         "1,22,000", "12,02,857", "11,37,126", "3,91,331", "76,967", "28,211",
         "9,10,019", "76,026", "1,21,838", "1,51,241", "11,51,241", "6,66,579",
         "1,194,054", "4,99,900"}

    found = set(re.findall(r"₹([\d,]{5,})", text))
    unaccounted = {f for f in found if f not in allowed}
    assert not unaccounted, (
        f"{doc} quotes rupee figures that are not in the committed evaluation "
        f"or the known-constants list: {sorted(unaccounted)}"
    )

    # And the headline figures must actually be present in the README.
    if doc == "README.md":
        for e in expected:
            assert e in text, f"README no longer quotes ₹{e}"


def test_committed_report_matches_committed_results():
    report = (ROOT / "evaluation" / "report.md").read_text()
    arms = json.loads(RESULTS.read_text())["arms"]
    for arm in arms.values():
        rupees = f"Rs {int(arm['money']['recovered_paise'] / 100):,}"
        assert rupees in report, f"report.md does not quote {rupees}"
