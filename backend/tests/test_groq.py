"""Post-Phase-8 enhancement 3: Groq as the preferred LLM provider (OpenAI-compatible interface). No live calls: httpx.post is faked."""
import json

import httpx
import pytest
from fastapi.testclient import TestClient

import main
import ai_brief
from ai_brief import llm_config, generate_brief, build_supplier_evidence, fallback_brief

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for k in ("GROQ_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_TIMEOUT"):
        monkeypatch.delenv(k, raising=False)
    yield
    client.post("/api/reset?dataset=sample")


def _evidence():
    client.post("/api/reset?dataset=sample"); client.post("/api/analyze")
    sid = client.get("/api/priority?limit=1").json()["suppliers"][0]["supplier_id"]
    return build_supplier_evidence(main.Store.result, sid), sid


def _good_content(ev):
    inv = ev["invoices"][0]
    return json.dumps({
        "why_flagged": [f"Potential ITC Exposure of {ai_brief._inr(ev['potential_exposure'])} across {ev['affected_invoice_count']} affected invoices."],
        "what_to_verify": [f"Confirm invoice {inv['invoice_no']} (purchase GST {ai_brief._inr(inv['purchase_gst'])}) with the supplier."],
        "draft_followup": f"Subject: Reconciliation query\n\nDear {ev['supplier']['supplier_name']} team, please confirm invoice {inv['invoice_no']} "
                          f"of {ai_brief._inr(inv['purchase_gst'])} GST was reported. Thank you.",
    })


class _Resp:
    def __init__(self, status=200, payload=None, text=""):
        self.status_code, self._payload, self.text = status, payload, text
        self.request = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(f"HTTP {self.status_code}", request=self.request, response=httpx.Response(self.status_code, request=self.request))

    def json(self):
        if self._payload is None:
            raise ValueError("not json")
        return self._payload


def _fake_post(monkeypatch, responder):
    calls = []
    def post(url, **kw):
        calls.append({"url": url, **kw})
        r = responder(url, kw)
        if isinstance(r, Exception):
            raise r
        return r
    monkeypatch.setattr(httpx, "post", post)
    return calls


# ------------------------------------------------------------------------------------ configuration
def test_groq_is_preferred_in_auto_mode(monkeypatch):
    assert llm_config()["configured"] is False
    monkeypatch.setenv("OPENAI_API_KEY", "sk-o"); monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-a"); monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    c = llm_config()
    assert c["provider"] == "groq" and c["model"] == "llama-3.3-70b-versatile" and c["base_url"] == "https://api.groq.com/openai/v1"
    monkeypatch.setenv("LLM_PROVIDER", "groq"); monkeypatch.setenv("LLM_MODEL", "llama-3.1-8b-instant"); monkeypatch.setenv("LLM_BASE_URL", "https://proxy.example/v1/")
    c = llm_config()
    assert c["provider"] == "groq" and c["model"] == "llama-3.1-8b-instant" and c["base_url"] == "https://proxy.example/v1/"
    status = client.get("/api/ai/status").json()
    assert status["configured"] and status["provider"] == "groq" and "gsk_" not in json.dumps(status)


def test_provider_groq_without_key_is_not_configured(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    c = llm_config()
    assert c["configured"] is False and "GROQ_API_KEY is not set" in c["reason"]
    monkeypatch.setenv("LLM_PROVIDER", "bogus")
    assert "Unknown LLM_PROVIDER" in llm_config()["reason"]


def test_missing_key_never_calls_groq(monkeypatch):
    ev, sid = _evidence()
    calls = _fake_post(monkeypatch, lambda u, kw: _Resp(200, {"choices": [{"message": {"content": _good_content(ev)}}]}))
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": sid}).json()
    assert out["source"] == "fallback" and "No LLM API key" in out["fallback_reason"] and calls == []
    assert out["brief"] == fallback_brief(ev)


# ------------------------------------------------------------------------------------ request shape: structured evidence only
def test_structured_evidence_is_sent_to_groq_via_openai_compatible_interface(monkeypatch):
    ev, sid = _evidence()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test_key")
    calls = _fake_post(monkeypatch, lambda u, kw: _Resp(200, {"choices": [{"message": {"content": _good_content(ev)}}]}))
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": sid}).json()
    assert out["source"] == "llm" and out["provider"] == "groq" and out["model"] == "llama-3.3-70b-versatile"
    assert len(calls) == 1
    call = calls[0]
    assert call["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer gsk_test_key"
    body = call["json"]
    assert body["model"] == "llama-3.3-70b-versatile" and body["response_format"] == {"type": "json_object"}
    system, user = body["messages"][0]["content"], body["messages"][1]["content"]
    assert body["messages"][0]["role"] == "system" and "Never calculate" in system and "fraud" in system.lower()
    sent = json.loads(user.split("EVIDENCE (JSON, computed by the backend):\n", 1)[1].rsplit("\n\nReturn only", 1)[0])
    assert sent == ev                                                                  # exactly the backend evidence, nothing else
    for k in ("supplier", "potential_exposure", "affected_invoice_count", "issue_types", "supplier_risk", "investigation", "invoices"):
        assert k in sent
    assert "gsk_test_key" not in json.dumps(out)


# ------------------------------------------------------------------------------------ failure modes -> deterministic fallback
@pytest.mark.parametrize("responder,needle", [
    (lambda u, kw: httpx.ReadTimeout("timed out", request=httpx.Request("POST", u)), "timed out"),
    (lambda u, kw: httpx.ConnectError("dns", request=httpx.Request("POST", u)), "unavailable"),
    (lambda u, kw: _Resp(500, {"error": "boom"}), "HTTP 500"),
    (lambda u, kw: _Resp(429, {"error": "rate"}), "rate limit"),
    (lambda u, kw: _Resp(401, {"error": "bad key"}), "HTTP 401"),
    (lambda u, kw: _Resp(200, {"choices": []}), "unreadable"),
    (lambda u, kw: _Resp(200, {"choices": [{"message": {"content": "Sorry, I cannot do that."}}]}), "unreadable"),
    (lambda u, kw: _Resp(200, {"choices": [{"message": {"content": '{"why_flagged": []}'}}]}), "malformed"),
    (lambda u, kw: _Resp(200, None, text="<html>gateway</html>"), "unreadable"),
])
def test_groq_failures_fall_back_deterministically(monkeypatch, responder, needle):
    ev, sid = _evidence()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _fake_post(monkeypatch, responder)
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": sid}).json()
    assert out["source"] == "fallback", out["fallback_reason"]
    assert needle in out["fallback_reason"], out["fallback_reason"]
    assert out["brief"] == fallback_brief(ev)
    assert "Traceback" not in json.dumps(out) and "gsk_test" not in json.dumps(out)


def test_groq_output_with_invented_values_or_claims_is_rejected(monkeypatch):
    ev, sid = _evidence()
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    bad = json.loads(_good_content(ev))
    bad["why_flagged"].append("Total exposure is ₹8,88,888 and the supplier is likely committing fraud.")
    _fake_post(monkeypatch, lambda u, kw: _Resp(200, {"choices": [{"message": {"content": json.dumps(bad)}}]}))
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": sid}).json()
    assert out["source"] == "fallback" and ("disallowed wording" in out["fallback_reason"] or "not present in the evidence" in out["fallback_reason"])
    only_number = json.loads(_good_content(ev)); only_number["what_to_verify"].append("Expect a credit of ₹7,77,777.")
    _fake_post(monkeypatch, lambda u, kw: _Resp(200, {"choices": [{"message": {"content": json.dumps(only_number)}}]}))
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": sid}).json()
    assert out["source"] == "fallback" and "not present in the evidence" in out["fallback_reason"]


def test_groq_success_on_uploaded_data_and_invoice_brief(monkeypatch):
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    files = {"purchase_file": ("p.csv", open(os.path.join(root, "demo_upload_data", "sample_purchase_register.csv"), "rb"), "text/csv"),
             "portal_file": ("g.csv", open(os.path.join(root, "demo_upload_data", "sample_portal_data.csv"), "rb"), "text/csv")}
    assert client.post("/api/upload", files=files).status_code == 200
    client.post("/api/analyze")
    sid = client.get("/api/priority?limit=1").json()["suppliers"][0]["supplier_id"]
    ev = build_supplier_evidence(main.Store.result, sid)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _fake_post(monkeypatch, lambda u, kw: _Resp(200, {"choices": [{"message": {"content": _good_content(ev)}}]}))
    out = client.post("/api/ai/investigation-brief", json={"supplier_id": sid}).json()
    assert out["source"] == "llm" and out["provider"] == "groq" and "Arvind Steel Industries" in out["brief"]["draft_followup"]


def test_fallback_is_identical_with_and_without_groq_configured(monkeypatch):
    ev, _ = _evidence()
    without = generate_brief(ev)["brief"]
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _fake_post(monkeypatch, lambda u, kw: _Resp(503, {"error": "down"}))
    with_failed = generate_brief(ev)["brief"]
    assert without == with_failed == fallback_brief(ev)
