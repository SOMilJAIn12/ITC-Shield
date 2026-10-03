"""Phase 4 tests: supplier risk features, temporal leakage, model, scores, explanations, API."""
import copy
import pathlib
import re
import sys

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import realistic_data  # noqa: E402
import sample_data  # noqa: E402
import supplier_risk as sr  # noqa: E402
from reconcile import reconcile  # noqa: E402

MONTHS = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]


def inv(sid, month, status="MATCHED", day=10, taxable=100_000.0):
    return {"supplier_id": sid, "invoice_date": f"{month}-{day:02d}", "taxable_value": taxable,
            "status": "matched" if status == "MATCHED" else "issue", "recon_status": status}


@pytest.fixture(scope="module")
def realistic():
    b, p, _ = realistic_data.build_realistic()
    r = reconcile(b, p)
    sr.attach_supplier_risk(r)
    return r


# ------------------------------------------------------------------ 1. features
def test_feature_generation_by_hand():
    rows = ([inv("S", "2026-04")] * 4                                         # 4 clean
            + [inv("S", "2026-05")] * 2 + [inv("S", "2026-05", "MISSING")] * 2   # 2 clean, 2 missing
            + [inv("S", "2026-06", "TAX_MISMATCH"), inv("S", "2026-06", "DUPLICATE"),
               inv("S", "2026-06", "REVIEW", taxable=300_000.0), inv("S", "2026-06")])
    f = sr.compute_features(rows, MONTHS[:3])
    x, facts = f["features"], f["facts"]
    assert facts["n_invoices"] == 12 and facts["n_issue"] == 5
    assert x["hist_issue_rate"] == pytest.approx(5 / 12)
    assert x["hist_missing_rate"] == pytest.approx(2 / 12)
    assert x["hist_duplicate_rate"] == pytest.approx(1 / 12) and x["hist_review_rate"] == pytest.approx(1 / 12)
    assert x["hist_mismatch_rate"] == pytest.approx(1 / 12)
    assert x["recent_issue_count"] == 5 and x["recent_issue_rate"] == pytest.approx(5 / 8)   # last 2 months: May + Jun
    assert x["issue_rate_trend"] == pytest.approx(5 / 8 - 0.0)                               # earlier = Apr: 0 flagged
    assert x["issue_rate_slope"] == pytest.approx(0.375)                                     # monthly rates 0, .5, .75 -> slope .375
    assert x["months_with_issue_share"] == pytest.approx(2 / 3)
    assert x["hist_invoice_count"] == 12
    assert x["avg_invoice_value_lakh"] == pytest.approx((11 * 100_000 + 300_000) / 12 / 1e5)


# ------------------------------------------------------------------ 2. no leakage
def test_features_ignore_target_and_later_months():
    rows = [inv("S", m, "MISSING" if i % 2 else "MATCHED") for i, m in enumerate(MONTHS) for _ in range(3)]
    base = sr.compute_features(rows, MONTHS[:3])
    changed = [dict(r, status="issue", recon_status="MISSING") if r["invoice_date"][:7] >= MONTHS[3] else r for r in rows]
    assert sr.compute_features(changed, MONTHS[:3]) == base                     # outcomes from month 4+ cannot move features
    extra = rows + [inv("S", "2027-01", "MISSING", taxable=9e9)]
    assert sr.compute_features(extra, MONTHS[:3]) == base


def test_panel_uses_only_prior_months(realistic):
    invoices = realistic["invoices"]
    panel = sr.build_panel(invoices)
    by = sr._by_supplier(invoices)
    months = sr.dataset_months(invoices)
    for row in panel.sample(15, random_state=0).itertuples():
        hist = months[:row.origin]
        assert months[row.origin] == row.target_month and row.target_month not in hist
        expect = sr.compute_features(by[row.supplier_id], hist)["features"]
        assert all(getattr(row, k) == pytest.approx(v) for k, v in expect.items())
        tgt = [i for i in by[row.supplier_id] if i["invoice_date"][:7] == row.target_month]
        assert row.y == int(any(i["status"] == "issue" for i in tgt))             # label = target-month outcome only


def test_perturbing_target_month_changes_label_not_features():
    rows = [inv("S", m) for m in MONTHS for _ in range(3)]
    flipped = [dict(r, status="issue", recon_status="MISSING") if r["invoice_date"].startswith("2026-09") else r for r in rows]
    p0, p1 = sr.build_panel(rows), sr.build_panel(flipped)
    t0, t1 = p0[p0.target_month == "2026-09"].iloc[0], p1[p1.target_month == "2026-09"].iloc[0]
    assert t0["y"] == 0 and t1["y"] == 1
    assert all(t0[f] == pytest.approx(t1[f]) for f in sr.FEATURES)


def test_evaluation_folds_train_strictly_before_test(realistic):
    ev = realistic["risk_model"]["evaluation"]
    for fold in ev["rolling_origin"]["folds"]:
        months = realistic["risk_model"]["trained_on"]["target_months"]
        assert fold["train_samples"] > 0 and fold["test_month"] in months
    # train sizes grow month by month and the final-month holdout trains on every earlier target month
    sizes = [f["train_samples"] for f in ev["rolling_origin"]["folds"]]
    assert sizes == sorted(sizes) and len(set(sizes)) == len(sizes)
    assert ev["final_month_holdout"]["folds"][0]["test_month"] == max(realistic["risk_model"]["trained_on"]["target_months"])


# ------------------------------------------------------------------ 3. model training
def test_model_trains_and_learns_from_features(realistic):
    panel = sr.build_panel(realistic["invoices"])
    model = sr.fit(panel)
    assert model is not None
    p = model.predict_proba(panel[sr.FEATURES].to_numpy(float))[:, 1]
    assert np.unique(p.round(3)).size > 10                                       # not a constant / type lookup
    from sklearn.metrics import roc_auc_score
    assert roc_auc_score(panel["y"], p) > 0.6                                    # learned something on its training data


def test_model_responds_to_feature_values_not_labels_of_type():
    rng = np.random.default_rng(0)
    rows = []
    for i in range(80):                                                          # synthetic panel: outcome driven by recent rate
        rate = rng.uniform(0, 1)
        row = {f: 0.0 for f in sr.FEATURES}
        row.update(recent_issue_rate=rate, hist_issue_rate=rate / 2, hist_invoice_count=10.0, avg_invoice_value_lakh=1.0)
        rows.append({**row, "y": int(rate + rng.normal(0, 0.1) > 0.5), "supplier_id": f"S{i}"})
    model = sr.fit(pd.DataFrame(rows))
    lo = {f: 0.0 for f in sr.FEATURES}; hi = dict(lo)
    for d, r in ((lo, 0.05), (hi, 0.95)):
        d.update(recent_issue_rate=r, hist_issue_rate=r / 2, hist_invoice_count=10.0, avg_invoice_value_lakh=1.0)
    plo, phi = (model.predict_proba(np.array([[d[f] for f in sr.FEATURES]]))[0, 1] for d in (lo, hi))
    assert phi > 0.8 > 0.2 > plo


def test_single_class_labels_do_not_train():
    rows = [inv(s, m) for s in ("A", "B") for m in MONTHS for _ in range(2)]    # nobody ever flagged
    r = {"invoices": rows, "suppliers": [{"supplier_id": "A"}, {"supplier_id": "B"}]}
    meta = sr.attach_supplier_risk(r)
    assert meta["status"] == "insufficient_labels"
    assert all(s["risk_score"] is None for s in r["suppliers"])


# ------------------------------------------------------------------ 4/5/6. predictions, range, mapping
def test_prediction_output_and_score_range(realistic):
    meta = realistic["risk_model"]
    assert meta["status"] == "ok" and meta["scoring_period"] == "2026-10"
    assert meta["thresholds"] == {"HIGH": 0.6, "MEDIUM": 0.3}
    for s in realistic["suppliers"]:
        assert s["risk_status"] == "scored"
        assert 0.0 <= s["risk_score"] <= 1.0
        assert s["predicted_risk_level"] == ("HIGH" if s["risk_score"] >= 0.6 else "MEDIUM" if s["risk_score"] >= 0.3 else "LOW")
        assert {"supplier_id", "supplier_name", "exposure", "affected_invoice_count", "key_risk_factors"} <= s.keys()
    levels = {s["predicted_risk_level"] for s in realistic["suppliers"]}
    assert levels == {"LOW", "MEDIUM", "HIGH"}                                  # spread, not all one bucket


def test_supplier_to_prediction_mapping(realistic):
    ids = [s["supplier_id"] for s in realistic["suppliers"]]
    assert len(ids) == len(set(ids)) == 20
    panel = sr.build_panel(realistic["invoices"])
    model = sr.fit(panel)
    by = sr._by_supplier(realistic["invoices"])
    months = sr.dataset_months(realistic["invoices"])
    for s in realistic["suppliers"]:                                            # recompute each score from that supplier's own invoices
        f = sr.compute_features(by[s["supplier_id"]], months)
        p = model.predict_proba(np.array([[f["features"][c] for c in sr.FEATURES]]))[0, 1]
        assert s["risk_score"] == pytest.approx(round(float(p), 3))
        assert s["history_invoice_count"] == len(by[s["supplier_id"]])


def test_scores_are_deterministic_and_do_not_use_supplier_type(realistic):
    b, p, _ = realistic_data.build_realistic()
    r2 = reconcile(b, p); sr.attach_supplier_risk(r2)
    assert [s["risk_score"] for s in r2["suppliers"]] == [s["risk_score"] for s in realistic["suppliers"]]
    src = pathlib.Path(sr.__file__).read_text()
    assert "realistic_data" not in src and "archetype" not in src.replace("archetype-", "")  # model never sees the generator's labels
    assert not any("arch" in f for f in sr.FEATURES)


def test_risk_tracks_history_not_labels(realistic):
    # across suppliers, a higher recent issue rate goes with a higher score (rank correlation clearly positive)
    by = sr._by_supplier(realistic["invoices"]); months = sr.dataset_months(realistic["invoices"])
    xs, ys = [], []
    for s in realistic["suppliers"]:
        xs.append(sr.compute_features(by[s["supplier_id"]], months)["features"]["recent_issue_rate"]); ys.append(s["risk_score"])
    assert pd.Series(xs).corr(pd.Series(ys), method="spearman") > 0.5


# ------------------------------------------------------------------ explanations
def test_explanations_come_from_actual_values(realistic):
    by = sr._by_supplier(realistic["invoices"]); months = sr.dataset_months(realistic["invoices"])
    top = max(realistic["suppliers"], key=lambda s: s["risk_score"])
    assert top["key_risk_factors"] and len(top["key_risk_factors"]) <= 4
    f = sr.compute_features(by[top["supplier_id"]], months)
    for kf in top["key_risk_factors"]:
        assert kf["contribution"] > 0 and kf["value"] > 0
        assert kf["value"] == pytest.approx(round(f["features"][kf["feature"]], 4))
        assert kf["text"] == sr._signal_text(kf["feature"], f["features"], f["facts"])
    pcts = {f"{100 * f['features']['hist_issue_rate']:.0f}%", f"{100 * f['features']['recent_issue_rate']:.0f}%"}
    assert any(p in " ".join(k["text"] for k in top["key_risk_factors"]) for p in pcts)


def test_no_forbidden_wording_in_supplier_output(realistic):
    text = " ".join(k["text"] for s in realistic["suppliers"] for k in s["key_risk_factors"]).lower()
    assert not re.search(r"fraud|default|violation|non-?compliant|illegal", text)


# ------------------------------------------------------------------ 7. edge cases
def test_edge_cases():
    assert sr.compute_features([], MONTHS[:3]) is None
    assert sr.compute_features([inv("S", "2026-09")], MONTHS[:3]) is None      # only invoices outside the history
    one = sr.compute_features([inv("S", "2026-04", "MISSING")], MONTHS[:3])    # single invoice, single month: no crash
    assert one["features"]["hist_issue_rate"] == 1.0 and one["features"]["issue_rate_slope"] == 0.0
    assert one["features"]["issue_rate_trend"] == 0.0
    assert sr.build_panel([]).empty
    r = {"invoices": [], "suppliers": []}
    assert sr.attach_supplier_risk(r)["status"] == "insufficient_history"


def test_supplier_without_history_and_low_history_flag():
    rows = []
    for m_i, m in enumerate(MONTHS):                                           # A: flagged every other month; B: flagged often
        for k in range(3):
            rows.append(inv("A", m, "MISSING" if (m_i + k) % 4 == 0 else "MATCHED"))
            rows.append(inv("B", m, "MISSING" if m_i >= 3 else "MATCHED"))
    rows.append(inv("C", "2026-09", "MISSING"))                                # C: single invoice
    r = {"invoices": rows, "suppliers": [{"supplier_id": s} for s in ("A", "B", "C", "D")]}
    meta = sr.attach_supplier_risk(r)
    assert meta["status"] == "ok"
    s = {x["supplier_id"]: x for x in r["suppliers"]}
    assert s["C"]["risk_status"] == "scored" and s["C"]["low_history"] is True and s["C"]["history_invoice_count"] == 1
    assert s["D"]["risk_status"] == "no_history" and s["D"]["risk_score"] is None and s["D"]["key_risk_factors"] == []
    assert s["A"]["low_history"] is False


def test_default_demo_has_no_invented_history():
    b, p, _ = sample_data.build_sample()
    r = reconcile(b, p)
    meta = sr.attach_supplier_risk(r)
    assert len(sr.dataset_months(r["invoices"])) == 3
    assert meta["status"] == "insufficient_history"
    assert all(s["risk_score"] is None and s["predicted_risk_level"] is None for s in r["suppliers"])
    s = r["summary"]
    assert (round(s["expected_itc"] / 1e5, 1), round(s["reconciled_itc"] / 1e5, 1),
            round(s["potential_exposure"] / 1e5, 1), s["issue_count"]) == (25.0, 20.8, 4.2, 147)


def test_existing_supplier_fields_untouched(realistic):
    b, p, _ = realistic_data.build_realistic()
    plain = reconcile(b, p)
    for a, c in zip(plain["suppliers"], realistic["suppliers"]):
        assert all(c[k] == a[k] for k in a)                                    # attach only adds keys; legacy risk_level intact


# ------------------------------------------------------------------ 8. API
def test_api_output_both_datasets():
    import main
    c = TestClient(main.app)
    c.post("/api/reset", params={"dataset": "realistic"}); c.post("/api/analyze")
    d = c.get("/api/suppliers").json()
    assert d["risk_model"]["status"] == "ok" and d["risk_model"]["evaluation"]["final_month_holdout"]["n"] > 0
    for s in d["suppliers"]:
        assert {"supplier_id", "supplier_name", "exposure", "affected_invoice_count", "risk_score",
                "predicted_risk_level", "key_risk_factors", "risk_level"} <= s.keys()
        assert 0 <= s["risk_score"] <= 1
    top = max(d["suppliers"], key=lambda s: s["risk_score"])
    det = c.get(f"/api/suppliers/{top['supplier_id']}").json()["supplier"]
    assert det["risk_score"] == top["risk_score"] and det["key_risk_factors"] == top["key_risk_factors"]
    dash = c.get("/api/dashboard").json()
    assert all("risk_score" in s for s in dash["top_suppliers"])
    assert round(dash["summary"]["potential_exposure"] / 1e5, 1) == 5.9 and dash["summary"]["issue_count"] == 85

    c.post("/api/reset"); c.post("/api/analyze")                               # default demo
    d = c.get("/api/suppliers").json()
    assert d["risk_model"]["status"] == "insufficient_history" and "5 months" in d["risk_model"]["reason"]
    assert all(s["risk_score"] is None for s in d["suppliers"])
    dash = c.get("/api/dashboard").json()["summary"]
    assert (round(dash["expected_itc"] / 1e5, 1), round(dash["reconciled_itc"] / 1e5, 1),
            round(dash["potential_exposure"] / 1e5, 1), dash["issue_count"]) == (25.0, 20.8, 4.2, 147)
