import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import sample_data
from reconcile import reconcile


def test_sample_reconciliation():
    b, p, _ = sample_data.build_sample()
    r = reconcile(b, p)
    s = r["summary"]
    assert s["issue_count"] == sum(sample_data.ISSUE_PLAN.values()) == 147
    assert 2_450_000 <= s["expected_itc"] <= 2_550_000
    assert 380_000 <= s["potential_exposure"] <= 460_000
    assert abs(s["expected_itc"] - s["reconciled_itc"] - s["potential_exposure"]) < 1
    # every injected issue type is detected with the exact injected count
    counts = {x["type"]: x["count"] for x in r["breakdown"]}
    assert counts == sample_data.ISSUE_PLAN


def test_rounding_differences_are_not_issues():
    b, p, _ = sample_data.build_sample()
    r = reconcile(b, p)
    matched = [i for i in r["invoices"] if i["status"] == "matched"]
    assert len(matched) > 400 and all(i["exposure"] == 0 for i in matched)
