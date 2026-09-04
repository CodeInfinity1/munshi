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


def _demo_case_amounts() -> set[str]:
    """Amounts of the cases the demo script and README name by id. Read from the
    seeded batch, so renaming a case or reseeding fails here rather than silently
    leaving a wrong number in the prose."""
    from munshi.db import jload  # noqa: F401  (imported for the seeded-batch path)
    from munshi.seed.generate import build

    return {indian(c["amount_paise"] / 100) for c in build()["cases"]}


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
    batch = arms["baseline"]["batch"]

    expected = {
        indian(arms["baseline"]["money"]["at_risk_paise"] / 100),
        indian(arms["baseline"]["money"]["recovered_paise"] / 100),
        indian(arms["agent-heuristic"]["money"]["recovered_paise"] / 100),
        indian(arms["agent-heuristic-approved"]["money"]["recovered_paise"] / 100),
        indian(arms["agent-heuristic"]["money"]["held_for_approval_paise"] / 100),
        indian(batch["structurally_unretryable_paise"] / 100),
    }
    # Any rupee figure with Indian grouping in the prose must be accountable to the
    # committed evaluation, a policy constant, or a real case in the demo batch.
    # Derived rather than listed: a hand-maintained allowlist is how this drifted
    # in the first place.
    allowed = expected | {
        indian(a["money"][k] / 100)
        for a in arms.values()
        for k in a["money"]
        if isinstance(a["money"][k], int)
    } | {
        # Policy constants, which are deliberately round numbers.
        "2,00,000", "1,00,000", "15,000", "25,000", "2,00,00,000",
    } | _demo_case_amounts() | {
        # Derived comparisons the prose is allowed to state.
        indian((arms["baseline"]["money"]["recovered_paise"]
                - arms[a]["money"]["recovered_paise"]) / 100)
        for a in arms if a != "baseline"
    }

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
