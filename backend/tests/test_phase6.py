"""Phase 6 - AI Investigation Brief. No test here calls a live LLM: the provider call is always injected or monkeypatched."""
import json
import copy
import pytest
from fastapi.testclient import TestClient

import main
import ai_brief
from ai_brief import (build_supplier_evidence, build_invoice_evidence, fallback_brief, generate_brief, validate_brief,
                      find_unsupported_numbers, forbidden_words, parse_llm_json, llm_config, FORBIDDEN_WORDS)

client = TestClient(main.app)
DEMO = {"expected": 2_500_000, "reconciled": 2_080_000, "exposure": 420_000, "issues": 147}


@pytest.fixture(autouse=True)
def _no_keys(monkeypatch):
    """Tests run with no provider configured unless they set one explicitly; the demo dataset is restored afterwards."""
    for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ai_brief, "_call_llm", lambda *a, **k: (_ for _ in ()).throw(AssertionError("live LLM call attempted in tests")))
    yield
    client.post("/api/reset?dataset=sample")


def _analyze(dataset="sample"):
    client.post(f"/api/reset?dataset={dataset}")
    assert client.post("/api/analyze").status_code == 200
    return main.Store.result


def _top_supplier():
    return client.get("/api/priority?limit=1").json()["suppliers"][0]["supplier_id"]


def _good_llm_output(ev):
    """A valid LLM answer that only quotes figures from the evidence."""
    s, inv = ev["supplier"], ev["invoices"][0]
    return json.dumps({
        "why_flagged": [f"Potential ITC Exposure of {ai_brief._inr(ev['potential_exposure'])} across {ev['affected_invoice_count']} invoices.",
                        f"Largest issue: {ev['issue_types'][0]['label']} ({ev['issue_types'][0]['count']} invoices)."],
        "what_to_verify": [f"Check invoice {inv['invoice_no']} dated {inv['invoice_date']} (purchase GST {ai_brief._inr(inv['purchase_gst'])})."],
        "draft_followup": f"Subject: Reconciliation query\n\nDear {s['supplier_name']} team, please confirm invoice {inv['invoice_no']} "
                          f"of {ai_brief._inr(inv['purchase_gst'])} GST was reported in your return. Thank you.",
    })


# ---------------------------------------------------------------------------------------------- structured evidence
def test_supplier_evidence_is_structured_and_matches_backend():
    r = _analyze()
    sid = _top_supplier()
    ev = build_supplier_evidence(r, sid)
    s = next(x for x in r["suppliers"] if x["supplier_id"] == sid)
    assert ev["kind"] == "supplier" and ev["supplier"]["supplier_name"] == s["supplier_name"]
    assert ev["potential_exposure"] == s["exposure"] and ev["affected_invoice_count"] == s["issue_count"]
    assert ev["investigation"]["score"] == s["investigation"]["score"] and ev["investigation"]["rank"] == 1
    assert sum(c["count"] for c in ev["issue_types"]) == ev["affected_invoice_count"]
    assert round(sum(c["exposure"] for c in ev["issue_types"]), 2) == pytest.approx(ev["potential_exposure"], abs=0.02)
    assert ev["supplier_risk"]["available"] is False and ev["supplier_risk"]["score"] is None      # demo: nothing invented
    first = ev["invoices"][0]
    for k in ("invoice_no", "purchase_taxable_value", "purchase_gst", "portal_taxable_value", "portal_gst", "reconciliation_status",
              "match_confidence", "potential_exposure", "reason", "reason_code", "investigation_priority"):
        assert k in first
    assert ev["invoices"] == sorted(ev["invoices"], key=lambda i: -i["potential_exposure"])
    assert build_supplier_evidence(r, "NOPE") is None


def test_invoice_evidence_matches_backend_values():
    r = _analyze()
    inv = next(i for i in r["invoices"] if i["exposure"] > 0 and i["portal_tax"] is not None)
    ev = build_invoice_evidence(r, inv["invoice_id"])
    e = ev["invoice"]
    assert e["purchase_gst"] == inv["itc_claimed"] and e["portal_gst"] == inv["portal_tax"]
    assert e["purchase_taxable_value"] == inv["taxable_value"] and e["potential_exposure"] == inv["exposure"]
    assert e["reconciliation_status"] == inv["recon_status"] and e["match_confidence"] == inv["match_confidence"]
    assert e["investigation_priority"] == inv["investigation"]["score"]
    assert ev["supplier"]["supplier_name"] == inv["supplier_name"]
    assert build_invoice_evidence(r, "NOPE") is None


def test_realistic_evidence_carries_supplier_risk_signals():
    r = _analyze("realistic")
    ev = build_supplier_evidence(r, _top_supplier())
    assert ev["supplier_risk"]["available"] and 0 <= ev["supplier_risk"]["score"] <= 1
    assert ev["supplier_risk"]["signals"]


# ---------------------------------------------------------------------------------------------- deterministic fallback
@pytest.mark.parametrize("dataset", ["sample", "realistic"])
def test_fallback_brief_uses_only_evidence_values(dataset):
    r = _analyze(dataset)
    ev = build_supplier_evidence(r, _top_supplier())
    b = fallback_brief(ev)
    assert b["why_flagged"] and b["what_to_verify"] and len(b["draft_followup"]) > 100
    text = "\n".join(b["why_flagged"] + b["what_to_verify"] + [b["draft_followup"]])
    assert find_unsupported_numbers(text, ev) == []           # the fallback passes the same check applied to the LLM
    assert forbidden_words(text) == []
    assert ai_brief._inr(ev["potential_exposure"]) in text
    assert ev["supplier"]["supplier_name"] in b["draft_followup"]
    assert ev["invoices"][0]["invoice_no"] in b["draft_followup"]
    assert "verification with the supplier is needed" in " ".join(b["what_to_verify"])
    if not ev["supplier_risk"]["available"]:
        assert any("not available" in w for w in b["why_flagged"])


def test_fallback_is_deterministic():
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    assert fallback_brief(ev) == fallback_brief(copy.deepcopy(ev))


def test_fallback_invoice_brief():
    r = _analyze()
    ev = build_invoice_evidence(r, next(i["invoice_id"] for i in r["invoices"] if i["reason_code"] == "TAX_MISMATCH"))
    b = fallback_brief(ev)
    text = "\n".join(b["why_flagged"] + b["what_to_verify"] + [b["draft_followup"]])
    assert "Tax mismatch" in text and ai_brief._inr(ev["invoice"]["portal_gst"]) in text
    assert find_unsupported_numbers(text, ev) == [] and forbidden_words(text) == []


def test_fallback_for_supplier_without_flagged_invoices():
    r = _analyze()
    clean = next(s for s in r["suppliers"] if s["issue_count"] == 0)
    b = fallback_brief(build_supplier_evidence(r, clean["supplier_id"]))
    assert "No follow-up is needed" in b["draft_followup"]


# ---------------------------------------------------------------------------------------------- LLM paths (all injected)
def test_llm_success_path():
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    calls = []
    def fake(system, user):
        calls.append((system, user))
        return _good_llm_output(ev)
    out = generate_brief(ev, llm_call=fake)
    assert out["source"] == "llm" and out["fallback_reason"] is None
    assert set(out["brief"]) == {"why_flagged", "what_to_verify", "draft_followup"}
    assert out["evidence"] is ev and "not a determination" in out["disclaimer"]
    system, user = calls[0]
    assert "Never calculate" in system and "fraud" in system.lower()
    assert ev["supplier"]["supplier_name"] in user and json.dumps(ev["potential_exposure"]) in user     # evidence is sent as data


def test_missing_key_uses_fallback_without_calling_provider():
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    cfg = llm_config()
    assert cfg["configured"] is False and cfg["provider"] == "none"
    out = generate_brief(ev)                                   # _call_llm is patched to raise if reached
    assert out["source"] == "fallback" and "No LLM API key" in out["fallback_reason"]
    assert out["brief"] == fallback_brief(ev)


def test_llm_api_failure_falls_back():
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    def boom(system, user):
        raise ConnectionError("socket closed")
    out = generate_brief(ev, llm_call=boom)
    assert out["source"] == "fallback" and "ConnectionError" in out["fallback_reason"]
    assert out["brief"] == fallback_brief(ev)


@pytest.mark.parametrize("raw", ["", "I cannot help with that.", "{not json", '{"why_flagged": "x"}',
                                 '{"why_flagged": [], "what_to_verify": ["a"], "draft_followup": "short"}',
                                 '{"why_flagged": ["a"], "what_to_verify": [1, 2], "draft_followup": "' + "x" * 60 + '"}',
                                 '[1, 2, 3]'])
def test_malformed_llm_output_falls_back(raw):
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    out = generate_brief(ev, llm_call=lambda s, u: raw)
    assert out["source"] == "fallback" and out["brief"] == fallback_brief(ev)


def test_llm_output_with_invented_values_is_rejected():
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    good = json.loads(_good_llm_output(ev))
    bad = copy.deepcopy(good)
    bad["why_flagged"].append("The total exposure is ₹9,87,654 which exceeds the ₹5,00,000 threshold.")   # neither number exists
    out = generate_brief(ev, llm_call=lambda s, u: json.dumps(bad))
    assert out["source"] == "fallback" and "values not present in the evidence" in out["fallback_reason"]
    bad2 = copy.deepcopy(good)
    bad2["draft_followup"] += " Please respond within 45 days or the credit of ₹2.9L will lapse."
    assert generate_brief(ev, llm_call=lambda s, u: json.dumps(bad2))["source"] == "fallback"
    assert generate_brief(ev, llm_call=lambda s, u: json.dumps(good))["source"] == "llm"     # control


def test_llm_output_with_forbidden_claims_is_rejected():
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    for word in ("fraud", "GST violation", "non-compliant", "guaranteed loss", "penalty"):
        bad = json.loads(_good_llm_output(ev))
        bad["why_flagged"].append(f"This supplier shows signs of {word}.")
        out = generate_brief(ev, llm_call=lambda s, u, b=bad: json.dumps(b))
        assert out["source"] == "fallback" and "disallowed wording" in out["fallback_reason"], word


def test_number_validator_allows_evidence_formats_and_identifiers():
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    inv = ev["invoices"][0]
    e = ev["potential_exposure"]
    ok = (f"₹{e:,.2f} and {ai_brief._inr(e)} and ₹{e/1e5:.1f}L and ₹{round(e):,} and {ev['exposure_share_pct']}% "
          f"for invoice {inv['invoice_no']} dated {inv['invoice_date']} from GSTIN {ev['supplier']['gstin']} (GSTR-2B, FY 2026-27), "
          f"rank {ev['investigation']['rank']}, 3 invoices, score {ev['investigation']['score']}")
    assert find_unsupported_numbers(ok, ev) == []
    assert find_unsupported_numbers("a sum of ₹1,23,456.78", ev) == ["1,23,456.78"]
    allowed, _texts = set(), set()
    ai_brief._walk_numbers(ev, allowed, _texts)
    absent = next(x / 10 for x in range(333, 9999) if not any(abs(x / 10 - a) < 0.01 for a in allowed))
    assert find_unsupported_numbers(f"about ₹{absent}L in total", ev) == [str(absent)]
    assert find_unsupported_numbers("within 120 days", ev) == ["120"]


def test_parse_llm_json_accepts_fenced_and_wrapped_json():
    obj = {"why_flagged": ["a"], "what_to_verify": ["b"], "draft_followup": "c"}
    assert parse_llm_json(json.dumps(obj)) == obj
    assert parse_llm_json("```json\n" + json.dumps(obj) + "\n```") == obj
    assert parse_llm_json("Here you go:\n" + json.dumps(obj) + "\nHope it helps") == obj
    with pytest.raises(ValueError):
        parse_llm_json("no braces here")


def test_validate_brief_normalises_string_and_list_shapes():
    r = _analyze()
    ev = build_supplier_evidence(r, _top_supplier())
    b = {"why_flagged": "single string", "what_to_verify": ["check"], "draft_followup": ["Subject: x", "Dear team, please confirm the invoice details listed."]}
    clean, why = validate_brief(b, ev)
    assert why is None and clean["why_flagged"] == ["single string"] and clean["draft_followup"].startswith("Subject: x\n")


# ---------------------------------------------------------------------------------------------- configuration
def test_llm_config_reads_env_and_never_exposes_key(monkeypatch):
    assert llm_config()["configured"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    c = llm_config()
    assert c["configured"] and c["provider"] == "openai" and c["model"] == "gpt-4o-mini"
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("LLM_MODEL", "my-model")
    c = llm_config()
    assert c["provider"] == "anthropic" and c["model"] == "my-model"          # auto prefers anthropic when both are set
    monkeypatch.setenv("LLM_PROVIDER", "none")
    assert llm_config()["configured"] is False
    health = client.get("/api/health").json()["ai"]
    assert "sk-" not in json.dumps(health) and health["configured"] is False
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    status = client.get("/api/ai/status").json()
    assert status == {"configured": True, "provider": "openai", "model": "my-model", "reason": None}
    assert "sk-" not in json.dumps(status)


def test_configured_provider_failure_falls_back_via_api(monkeypatch):
    _analyze()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-123")
    def failing(system, user, cfg):
        raise TimeoutError("timed out")
    monkeypatch.setattr(ai_brief, "_call_llm", failing)
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": _top_supplier()}).json()
    assert out["source"] == "fallback" and "timed out" in out["fallback_reason"] and out["provider"] == "openai"
    assert "sk-test" not in json.dumps(out)


def test_configured_provider_success_via_api(monkeypatch):
    r = _analyze()
    sid = _top_supplier()
    ev = build_supplier_evidence(r, sid)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(ai_brief, "_call_llm", lambda system, user, cfg: _good_llm_output(ev))
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": sid}).json()
    assert out["source"] == "llm" and out["provider"] == "anthropic" and out["model"] == "claude-sonnet-5-5"
    assert "sk-ant" not in json.dumps(out)


# ---------------------------------------------------------------------------------------------- API behaviour
def test_endpoint_requires_analysis_and_validates_input():
    client.post("/api/reset?dataset=sample")
    assert client.post("/api/ai/investigation-brief", json={"supplier_id": "x"}).status_code == 409
    _analyze()
    assert client.post("/api/ai/investigation-brief", json={}).status_code == 422
    assert client.post("/api/ai/investigation-brief", json={"supplier_id": "a", "invoice_id": "b"}).status_code == 422
    assert client.post("/api/ai/investigation-brief", json={"supplier_id": "NOPE"}).status_code == 404
    assert client.post("/api/ai/investigation-brief", json={"invoice_id": "NOPE"}).status_code == 404


@pytest.mark.parametrize("dataset", ["sample", "realistic"])
def test_endpoint_supplier_and_invoice_briefs(dataset):
    r = _analyze(dataset)
    sid = _top_supplier()
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": sid}).json()
    assert out["source"] == "fallback" and out["evidence"]["supplier"]["supplier_id"] == sid
    assert out["brief"]["why_flagged"] and out["brief"]["what_to_verify"] and out["brief"]["draft_followup"]
    iid = out["evidence"]["invoices"][0]["invoice_id"]
    inv = client.post("/api/ai/investigation-brief", json={"invoice_id": iid}).json()
    assert inv["evidence"]["kind"] == "invoice" and inv["evidence"]["invoice"]["invoice_id"] == iid
    assert inv["brief"]["draft_followup"].startswith("Subject:")


def test_existing_functionality_unchanged():
    _analyze()
    s = client.get("/api/dashboard").json()["summary"]
    assert round(s["expected_itc"] / 1e5, 1) == 25.0 and round(s["reconciled_itc"] / 1e5, 1) == 20.8
    assert round(s["potential_exposure"] / 1e5, 1) == 4.2 and s["issue_count"] == 147
    p = client.get("/api/priority?limit=3").json()
    assert p["suppliers"][0]["supplier_name"] == "Sundaram Steel Traders" and p["risk_available"] is False
    assert client.get("/api/suppliers").json()["suppliers"][0]["risk_score"] is None


def test_forbidden_wording_scan_of_module_text():
    """Backend-authored brief text (templates, prompt instructions excluded) never asserts fraud / violation / guaranteed loss."""
    text = " ".join(list(ai_brief._VERIFY.values()) + list(ai_brief._ASK.values()) + [ai_brief.DISCLAIMER])
    assert forbidden_words(text) == []
    assert "fraud" in FORBIDDEN_WORDS
