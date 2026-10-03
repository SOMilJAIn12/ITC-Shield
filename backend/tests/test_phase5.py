"""Phase 5 - Investigation Priority / ITC Risk Radar."""
import copy
import json
import pytest
from fastapi.testclient import TestClient

import main
from priority import (attach_priority, supplier_score, invoice_score, level_for, SEVERITY, METHODOLOGY,
                      HIGH_THRESHOLD, MEDIUM_THRESHOLD)

client = TestClient(main.app)


def _analyze(dataset):
    client.post(f"/api/reset?dataset={dataset}")
    assert client.post("/api/analyze").status_code == 200


@pytest.fixture(autouse=True)
def _restore_demo():
    yield
    client.post("/api/reset?dataset=sample")


def _inv(iid, sid, exposure, code="MISSING"):
    return {"invoice_id": iid, "invoice_no": iid, "supplier_id": sid, "supplier_name": sid, "exposure": exposure,
            "reason_code": code if exposure else None, "reason": "test reason", "match_confidence": None,
            "match_confidence_label": None, "high_value": False}


def _sup(sid, n_inv, risk=None, level=None):
    return {"supplier_id": sid, "supplier_name": sid, "invoice_count": n_inv, "exposure": 0, "high_value_count": 0,
            "risk_score": risk, "predicted_risk_level": level, "key_risk_factors": []}


def _mini(risk_a=None, risk_b=None):
    """A: large exposure, moderate risk. B: tiny exposure, very high risk. C: no flagged invoices."""
    inv = [_inv(f"A{k}", "A", 15_000) for k in range(4)] + [_inv("B1", "B", 400)] + [_inv("C1", "C", 0)]
    sup = [_sup("A", 10, risk_a), _sup("B", 10, risk_b), _sup("C", 10, 0.9 if risk_a is not None else None)]
    return {"invoices": inv, "suppliers": sup}


# 1. priority calculation (hand-computed) --------------------------------------------------------------
def test_supplier_formula_hand_computed():
    r = supplier_score(exposure=100_000, max_exposure=100_000, risk_score=0.8, severity=1.0, affected=5)
    assert r["score"] == 94.0 and r["level"] == "HIGH"       # 100*(0.5*1 + 0.3*0.8 + 0.2*(0.7*1+0.3*1))
    assert [c["key"] for c in r["components"]] == ["exposure", "risk", "evidence"]
    assert sum(c["contribution"] for c in r["components"]) == pytest.approx(r["score"], abs=0.05)
    r = supplier_score(exposure=50_000, max_exposure=100_000, risk_score=0.5, severity=0.7, affected=2)
    # E=0.5 ; R=0.5 ; S=0.7*0.7+0.3*0.4=0.61  -> 25 + 15 + 12.2
    assert r["score"] == 52.2 and r["level"] == "MEDIUM"


def test_invoice_formula_hand_computed():
    assert invoice_score(50_000, 50_000, "MISSING")["score"] == 100.0
    assert invoice_score(25_000, 50_000, "TAX_MISMATCH")["score"] == 56.0     # 100*(0.7*0.5 + 0.3*0.7)
    assert invoice_score(500, 50_000, "MISSING")["score"] == 30.7             # 100*(0.7*0.01 + 0.3*1.0): tiny exposure -> carried by severity only
    assert invoice_score(500, 50_000, "MISSING")["level"] == "LOW"
    assert invoice_score(0, 50_000, "MISSING")["score"] == 0.0


# 2/3/4. ranking, high+high, high risk with low exposure ----------------------------------------------
def test_high_exposure_and_high_risk_is_high_and_first():
    res = _mini(risk_a=0.85, risk_b=0.99)
    attach_priority(res)
    a, b = (next(s for s in res["suppliers"] if s["supplier_id"] == k)["investigation"] for k in "AB")
    assert a["level"] == "HIGH" and a["rank"] == 1


def test_high_risk_with_tiny_exposure_does_not_outrank_substantial_exposure():
    res = _mini(risk_a=0.30, risk_b=0.99)                    # B has the far higher risk score, A the real money
    attach_priority(res)
    a, b = (next(s for s in res["suppliers"] if s["supplier_id"] == k)["investigation"] for k in "AB")
    assert b["supplier_risk"] > a["supplier_risk"]
    assert a["score"] > b["score"] and a["rank"] < b["rank"]
    assert b["level"] != "HIGH"                              # tiny exposure cannot reach HIGH on risk alone


def test_risk_alone_can_never_reach_high_or_medium():
    for risk in (0.0, 0.5, 1.0):
        r = supplier_score(exposure=0, max_exposure=100_000, risk_score=risk, severity=0.0, affected=0)
        assert r["score"] <= 30.0 and r["level"] == "LOW"


# 5. missing risk score -------------------------------------------------------------------------------
def test_missing_risk_score_uses_remaining_signals():
    r = supplier_score(exposure=100_000, max_exposure=100_000, risk_score=None, severity=1.0, affected=5)
    assert r["risk_available"] is False and r["score"] == 100.0
    assert [c["key"] for c in r["components"]] == ["exposure", "evidence"]
    assert sum(c["weight"] for c in r["components"]) == pytest.approx(1.0, abs=1e-3)
    res = _mini()                                            # no risk anywhere
    attach_priority(res)
    sa = next(s for s in res["suppliers"] if s["supplier_id"] == "A")["investigation"]
    assert sa["supplier_risk"] is None and sa["risk_available"] is False and sa["rank"] == 1
    assert any(x["key"] == "risk_unavailable" for x in sa["signals"])
    assert res["priority"]["risk_available"] is False


# 6. supplier with no affected invoices ----------------------------------------------------------------
def test_supplier_without_affected_invoices():
    res = _mini(risk_a=0.4, risk_b=0.4)
    attach_priority(res)
    c = next(s for s in res["suppliers"] if s["supplier_id"] == "C")["investigation"]
    assert c["potential_exposure"] == 0 and c["affected_invoice_count"] == 0 and c["top_issue"] is None
    assert c["level"] == "LOW" and c["rank"] == 3
    assert [x["key"] for x in c["signals"]][0] == "no_exposure"
    assert "C" not in [s["supplier_id"] for s in res["priority"]["suppliers"]]          # radar lists exposed suppliers only
    res2 = _mini()
    attach_priority(res2)
    assert next(s for s in res2["suppliers"] if s["supplier_id"] == "C")["investigation"]["score"] == 0.0
    assert res["invoices"][-1]["investigation"] is None                                  # zero-exposure invoice


# 7. levels --------------------------------------------------------------------------------------------
def test_level_thresholds():
    assert (HIGH_THRESHOLD, MEDIUM_THRESHOLD) == (60.0, 35.0)
    assert [level_for(x) for x in (100, 60, 59.9, 35, 34.9, 0)] == ["HIGH", "HIGH", "MEDIUM", "MEDIUM", "LOW", "LOW"]


def test_severity_table_is_documented_and_complete():
    assert set(SEVERITY) == {"MISSING", "DUPLICATE", "TAX_MISMATCH", "AMOUNT_MISMATCH", "INVOICE_NO_MISMATCH", "GSTIN_MISMATCH", "FUZZY_REVIEW"}
    assert METHODOLOGY["severity"] == SEVERITY and METHODOLOGY["thresholds"] == {"HIGH": 60.0, "MEDIUM": 35.0}
    assert all(0 < v <= 1 for v in SEVERITY.values())


# 8. deterministic ------------------------------------------------------------------------------------
@pytest.mark.parametrize("dataset", ["sample", "realistic"])
def test_deterministic_across_runs(dataset):
    _analyze(dataset)
    first = json.dumps(client.get("/api/priority?limit=100").json(), sort_keys=True)
    _analyze(dataset)
    assert json.dumps(client.get("/api/priority?limit=100").json(), sort_keys=True) == first


def test_attach_priority_idempotent_and_does_not_mutate_inputs_twice():
    res = _mini(risk_a=0.5, risk_b=0.5)
    attach_priority(res)
    snap = copy.deepcopy(res)
    attach_priority(res)
    assert res == snap


# rankings come from real backend data -----------------------------------------------------------------
@pytest.mark.parametrize("dataset", ["sample", "realistic"])
def test_ranking_recomputed_independently_from_ledger(dataset):
    _analyze(dataset)
    ledger = client.get("/api/exposure").json()
    sup = {s["supplier_id"]: s for s in client.get("/api/suppliers").json()["suppliers"]}
    by_sup = {}
    for x in ledger["items"]:
        by_sup.setdefault(x["supplier_id"], []).append(x)
    max_exp = max(sum(round(i["exposure"] * 100) for i in v) / 100 for v in by_sup.values())
    expect = {}
    for sid, items in by_sup.items():
        exp = sum(round(i["exposure"] * 100) for i in items) / 100
        sev = sum(i["exposure"] * SEVERITY[i["reason_code"]] for i in items) / sum(i["exposure"] for i in items)
        expect[sid] = supplier_score(exp, max_exp, sup[sid]["risk_score"], sev, len(items))["score"]
    api = client.get("/api/priority?limit=100").json()["suppliers"]
    assert {s["supplier_id"]: s["investigation_priority"] for s in api} == expect
    assert [s["investigation_priority"] for s in api] == sorted(expect.values(), reverse=True)
    for s in api:                                              # displayed numbers are the backend's own aggregates
        assert s["potential_exposure"] == sup[s["supplier_id"]]["exposure"] or abs(s["potential_exposure"] - sup[s["supplier_id"]]["exposure"]) < 0.011
        assert s["affected_invoice_count"] == sup[s["supplier_id"]]["affected_invoice_count"]
        assert s["supplier_risk"] == sup[s["supplier_id"]]["risk_score"]
    assert sum(s["potential_exposure"] for s in api) == pytest.approx(ledger["total_exposure"], abs=0.01)


@pytest.mark.parametrize("dataset", ["sample", "realistic"])
def test_top_issue_and_signals_match_supplier_data(dataset):
    _analyze(dataset)
    ledger = client.get("/api/exposure").json()["items"]
    for s in client.get("/api/priority?limit=100").json()["suppliers"]:
        items = [x for x in ledger if x["supplier_id"] == s["supplier_id"]]
        by = {}
        for x in items:
            by[x["reason_code"]] = by.get(x["reason_code"], 0) + round(x["exposure"] * 100)
        top_code = sorted(by, key=lambda c: (-by[c], c))[0]
        assert s["top_issue"]["code"] == top_code and s["top_issue"]["count"] == sum(1 for x in items if x["reason_code"] == top_code)
        text = " ".join(x["text"] for x in s["signals"])
        assert f"{len(items)} affected" in text and s["top_issue"]["label"] in text


def test_supplier_not_ranked_by_risk_alone_on_realistic():
    _analyze("realistic")
    rows = client.get("/api/priority?limit=100").json()["suppliers"]
    by_risk = [s["supplier_id"] for s in sorted(rows, key=lambda s: -s["supplier_risk"])]
    by_prio = [s["supplier_id"] for s in rows]
    by_exp = [s["supplier_id"] for s in sorted(rows, key=lambda s: -s["potential_exposure"])]
    assert by_prio != by_risk and by_prio != by_exp            # a genuine blend of signals
    top = rows[0]
    assert top["supplier_name"].startswith("Lotus Heavy Machinery") and top["priority_level"] == "HIGH"
    assert top["risk_available"] and top["supplier_risk"] > 0.5


# default demo safety / realistic works ---------------------------------------------------------------
def test_default_demo_numbers_unchanged_and_no_invented_risk():
    _analyze("sample")
    s = client.post("/api/analyze").json()["summary"]
    assert f"{s['expected_itc']/1e5:.1f}" == "25.0" and f"{s['reconciled_itc']/1e5:.1f}" == "20.8"
    assert f"{s['potential_exposure']/1e5:.1f}" == "4.2" and s["issue_count"] == 147
    p = client.get("/api/priority?limit=100").json()
    assert p["risk_available"] is False
    assert all(x["supplier_risk"] is None and not x["risk_available"] for x in p["suppliers"])
    assert p["suppliers"][0]["supplier_name"].startswith("Sundaram Steel")
    assert all(sup["risk_score"] is None for sup in client.get("/api/suppliers").json()["suppliers"])
    assert all("Supplier Risk is not available" in " ".join(x["text"] for x in sp["signals"]) for sp in p["suppliers"])


def test_realistic_dataset_full_workflow():
    _analyze("realistic")
    s = client.get("/api/dashboard").json()["summary"]
    assert f"{s['potential_exposure']/1e5:.1f}" == "5.9" and s["issue_count"] == 85
    p = client.get("/api/priority?limit=100").json()
    assert p["risk_available"] and {x["priority_level"] for x in p["suppliers"]} >= {"HIGH", "MEDIUM"}
    top = p["suppliers"][0]
    assert any(c["key"] == "risk" for c in top["components"])
    assert any(x.get("source") == "risk_signal" for x in top["signals"])          # Phase 4 factors reused verbatim


# invoice priority ------------------------------------------------------------------------------------
@pytest.mark.parametrize("dataset", ["sample", "realistic"])
def test_invoice_priority_from_ledger(dataset):
    _analyze(dataset)
    ledger = {x["invoice_id"]: x for x in client.get("/api/exposure").json()["items"]}
    inv = client.get("/api/priority?limit=100").json()["invoices"]
    assert len(inv) == min(100, len(ledger))
    assert [i["investigation_priority"] for i in inv] == sorted((i["investigation_priority"] for i in inv), reverse=True)
    mx = max(x["exposure"] for x in ledger.values())
    for i in inv:
        x = ledger[i["invoice_id"]]
        assert i["potential_exposure"] == x["exposure"]
        assert i["investigation_priority"] == invoice_score(x["exposure"], mx, x["reason_code"])["score"]
    assert inv[0]["potential_exposure"] == mx and inv[0]["investigation_priority"] == 100.0       # largest exposure + severity 1.0


def test_supplier_endpoints_carry_investigation_and_invoice_list_fields():
    _analyze("realistic")
    sid = client.get("/api/priority").json()["suppliers"][0]["supplier_id"]
    d = client.get(f"/api/suppliers/{sid}").json()
    inv = d["supplier"]["investigation"]
    assert inv["rank"] == 1 and inv["level"] == "HIGH" and inv["signals"]
    assert all(i["investigation_level"] in ("HIGH", "MEDIUM", "LOW") for i in d["invoices"])
    assert d["supplier"]["risk_level"] in ("High", "Medium", "Low")                # legacy field untouched


def test_priority_requires_analysis_and_limit_validation():
    client.post("/api/reset")
    assert client.get("/api/priority").status_code == 409
    _analyze("sample")
    assert client.get("/api/priority?limit=0").status_code == 422
    assert len(client.get("/api/priority?limit=3").json()["suppliers"]) == 3


def test_forbidden_wording_absent():
    for ds in ("sample", "realistic"):
        _analyze(ds)
        blob = json.dumps(client.get("/api/priority?limit=100").json()).lower()
        for bad in ("fraud", "guaranteed", "gst violation", "non-compliant", "non compliant"):
            assert bad not in blob


@pytest.mark.parametrize("dataset", ["sample", "realistic"])
def test_high_value_signal_counts_only_affected_invoices(dataset):
    _analyze(dataset)
    inv = client.get("/api/invoices?status=issue").json()["invoices"]
    full = {i["invoice_id"]: client.get(f"/api/invoices/{i['invoice_id']}").json() for i in inv if i["supplier_id"]}
    for s in client.get("/api/priority?limit=100").json()["suppliers"]:
        k = sum(1 for i in full.values() if i["supplier_id"] == s["supplier_id"] and i["exposure"] > 0 and i["high_value"])
        sig = next((x for x in s["signals"] if x["key"] == "high_value"), None)
        assert (sig["value"] if sig else 0) == k
        if sig:
            assert k <= s["affected_invoice_count"]


def test_risk_signal_text_is_sentence_cased_and_comes_from_phase4():
    _analyze("realistic")
    sup = {s["supplier_id"]: s for s in client.get("/api/suppliers").json()["suppliers"]}
    for s in client.get("/api/priority?limit=100").json()["suppliers"]:
        got = [x["text"] for x in s["signals"] if x.get("source") == "risk_signal"]
        want = [f["text"] for f in sup[s["supplier_id"]]["key_risk_factors"]][:3]
        assert got == [w[:1].upper() + w[1:] for w in want]
