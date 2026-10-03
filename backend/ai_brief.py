"""
Phase 6 - AI Investigation Brief (explanation + action layer).

The LLM is ONLY an explanation/communication layer. The backend stays the source of truth for reconciliation, exposure,
supplier risk and investigation priority: this module never calculates any of them. It

  1. builds a structured EVIDENCE packet from existing backend results (`build_supplier_evidence`, `build_invoice_evidence`),
  2. produces a deterministic, evidence-based brief with no LLM (`fallback_brief`), and
  3. optionally asks an LLM to write the brief from that evidence (`generate_brief`), accepting the answer only if it is valid
     JSON with the expected keys, contains no rupee amount / count / percentage that is absent from the evidence, and uses
     none of the forbidden wording (fraud, violation, non-compliance, guaranteed loss ...). Anything else -> fallback.

Configuration is read from environment variables only (see ../.env.example); nothing is ever sent to the browser.
  LLM_PROVIDER = groq | anthropic | openai | none   (default: auto - groq if GROQ_API_KEY is set, else anthropic, else openai)
  GROQ_API_KEY (preferred) / ANTHROPIC_API_KEY / OPENAI_API_KEY   (server-side only)
  LLM_MODEL, LLM_BASE_URL (OpenAI-compatible endpoints; Groq default https://api.groq.com/openai/v1), LLM_TIMEOUT (seconds, default 25)
Groq is used through its OpenAI-compatible chat-completions interface; whether usage is free or billed depends on the Groq account.
Tests never call a live provider: `generate_brief(..., llm_call=...)` takes an injectable callable.
"""
from __future__ import annotations

import json
import os
import re
from typing import Callable

from reconcile import ISSUE_LABELS, STATUS_LABELS, _inr

DISCLAIMER = ("Generated from ITC Shield's reconciliation evidence. Potential ITC Exposure, Supplier Risk and Investigation "
              "Priority are estimates and ranking aids, not a determination of GST eligibility, a confirmed loss, or a legal position.")

# Wording the brief must never use (the same scan the earlier phases apply to backend text).
FORBIDDEN_WORDS = ("fraud", "fraudulent", "violation", "violates", "non-compliant", "non-compliance", "noncompliant",
                   "guaranteed loss", "guaranteed", "evasion", "illegal", "penalty", "penalties", "prosecution", "offence", "offense")

MAX_EVIDENCE_INVOICES = 8          # invoices listed in a supplier evidence packet (highest exposure first)
DEFAULT_TIMEOUT = 25.0
DEFAULT_MODELS = {"groq": "llama-3.3-70b-versatile", "anthropic": "claude-sonnet-5-5", "openai": "gpt-4o-mini"}
DEFAULT_BASE_URLS = {"groq": "https://api.groq.com/openai/v1", "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
KEY_VARS = {"groq": "GROQ_API_KEY", "anthropic": "ANTHROPIC_API_KEY", "openai": "OPENAI_API_KEY"}


# ----------------------------------------------------------------------------------------------------------------- evidence
def _label(code):
    return None if code is None else (STATUS_LABELS.get(code) or ISSUE_LABELS.get(code) or code)


def _invoice_evidence(i: dict) -> dict:
    inv = i.get("investigation") or {}
    return {
        "invoice_id": i["invoice_id"], "invoice_no": i["invoice_no"], "invoice_date": i["invoice_date"],
        "supplier_id": i["supplier_id"], "supplier_name": i["supplier_name"],
        "purchase_taxable_value": i["taxable_value"], "purchase_gst": i["itc_claimed"],
        "portal_taxable_value": i.get("portal_taxable_value"), "portal_gst": i.get("portal_tax"),
        "portal_invoice_no": i.get("portal_invoice_no"), "portal_supplier_gstin": i.get("portal_supplier_gstin"),
        "portal_record_found": bool(i.get("portal_found")),
        "reconciliation_status": i["recon_status"], "reconciliation_status_label": i["recon_status_label"],
        "issue_type": i["issue_type"], "issue_label": i["issue_label"],
        "reason_code": i.get("reason_code"), "reason": i.get("reason"),
        "match_method": i.get("match_method"), "match_confidence": i.get("match_confidence"),
        "match_confidence_label": i.get("match_confidence_label"),
        "potential_exposure": i["exposure"], "high_value": bool(i.get("high_value")),
        "related_invoice_id": i.get("related_invoice_id"),
        "investigation_priority": inv.get("score"), "investigation_level": inv.get("level"), "investigation_rank": inv.get("rank"),
    }


def _risk_evidence(s: dict) -> dict:
    score = s.get("risk_score")
    return {
        "available": score is not None, "status": s.get("risk_status"),
        "score": score, "level": s.get("predicted_risk_level"),
        "history_invoice_count": s.get("history_invoice_count"), "low_history": bool(s.get("low_history")),
        "signals": [f.get("text") for f in (s.get("key_risk_factors") or []) if f.get("text")],
        "note": None if score is not None else "Supplier Risk is not available for this dataset (needs at least 5 months of invoice history).",
    }


def build_supplier_evidence(result: dict, supplier_id: str) -> dict | None:
    """Structured evidence for one supplier, taken verbatim from existing backend outputs. None if unknown."""
    s = next((x for x in result["suppliers"] if x["supplier_id"] == supplier_id), None)
    if s is None:
        return None
    flagged = sorted((i for i in result["invoices"] if i["supplier_id"] == supplier_id and i["status"] == "issue"),
                     key=lambda i: (-i["exposure"], i["invoice_id"]))
    inv = s.get("investigation") or {}
    by_code: dict = {}
    for i in flagged:
        code = i.get("reason_code") or i["issue_type"]
        c = by_code.setdefault(code, {"code": code, "label": _label(code), "count": 0, "exposure": 0.0})
        c["count"] += 1
        c["exposure"] = round(c["exposure"] + i["exposure"], 2)
    issues = sorted(by_code.values(), key=lambda c: (-c["exposure"], c["code"]))
    months = sorted({i["invoice_date"][:7] for i in result["invoices"] if i["supplier_id"] == supplier_id})
    return {
        "kind": "supplier",
        "supplier": {"supplier_id": s["supplier_id"], "supplier_name": s["supplier_name"], "gstin": s["gstin"],
                     "category": s.get("category") or None, "name_variants": s.get("name_variants") or []},
        "period": {"first_month": months[0] if months else None, "last_month": months[-1] if months else None,
                   "months_with_invoices": len(months)},
        "invoice_count": s["invoice_count"], "affected_invoice_count": len(flagged),
        "itc_claimed": s["itc_claimed"], "potential_exposure": s["exposure"], "exposure_share_pct": s["exposure_share"],
        "issue_types": issues,
        "high_value_affected_count": sum(1 for i in flagged if i.get("high_value")),
        "supplier_risk": _risk_evidence(s),
        "investigation": {"available": bool(inv), "score": inv.get("score"), "level": inv.get("level"), "rank": inv.get("rank"),
                          "signals": [x["text"] for x in inv.get("signals", []) if x.get("source") != "note"],
                          "components": inv.get("components", [])},
        "invoices": [_invoice_evidence(i) for i in flagged[:MAX_EVIDENCE_INVOICES]],
        "invoices_listed": min(len(flagged), MAX_EVIDENCE_INVOICES),
    }


def build_invoice_evidence(result: dict, invoice_id: str) -> dict | None:
    i = next((x for x in result["invoices"] if x["invoice_id"] == invoice_id), None)
    if i is None:
        return None
    s = next((x for x in result["suppliers"] if x["supplier_id"] == i["supplier_id"]), None) or {}
    sinv = s.get("investigation") or {}
    return {
        "kind": "invoice",
        "invoice": _invoice_evidence(i),
        "supplier": {"supplier_id": i["supplier_id"], "supplier_name": i["supplier_name"], "gstin": s.get("gstin"),
                     "affected_invoice_count": s.get("affected_invoice_count"), "invoice_count": s.get("invoice_count"),
                     "potential_exposure": s.get("exposure"),
                     "investigation_level": sinv.get("level"), "investigation_rank": sinv.get("rank")},
        "supplier_risk": _risk_evidence(s) if s else {"available": False, "note": "No supplier record."},
    }


# --------------------------------------------------------------------------------------------------- deterministic fallback
_VERIFY = {
    "MISSING": "Check the supplier's filed return for the period and confirm whether the invoice has been reported (it may also be reported late or under a different period).",
    "DUPLICATE": "Compare the repeated entry with the original purchase-register entry and confirm whether it is the same invoice keyed twice.",
    "TAX_MISMATCH": "Compare the tax on the physical invoice copy with the purchase-register entry and with the portal figure to see which side is correct.",
    "AMOUNT_MISMATCH": "Compare the taxable value and tax on the invoice copy with both records; confirm whether a credit/debit note or partial entry explains the gap.",
    "INVOICE_NO_MISMATCH": "Check the invoice number printed on the original invoice against the purchase-register entry and the portal record.",
    "GSTIN_MISMATCH": "Verify the supplier GSTIN printed on the invoice; confirm which GSTIN the supplier filed under.",
    "FUZZY_REVIEW": "Open the similar portal record and confirm whether it is the same invoice (number, date, value).",
}
_ASK = {
    "MISSING": "confirm whether it has been reported in your GST return and, if so, in which period",
    "DUPLICATE": "confirm whether this invoice was issued once (we have two entries for it)",
    "TAX_MISMATCH": "confirm the tax amount on the invoice as filed, since the portal figure differs from our records",
    "AMOUNT_MISMATCH": "confirm the taxable value and tax as filed, since the portal figures differ from our records",
    "INVOICE_NO_MISMATCH": "confirm the invoice number as filed, since the portal shows a different number",
    "GSTIN_MISMATCH": "confirm the GSTIN this invoice was filed under",
    "FUZZY_REVIEW": "confirm whether this corresponds to the similar record on the portal",
}


def _plural(n, one, many):
    return one if n == 1 else many


def _inv_line(i: dict) -> str:
    return (f"{i['invoice_no']} ({i['invoice_date']}, {i['issue_label']}): purchase GST {_inr(i['purchase_gst'])}"
            + (f" vs portal GST {_inr(i['portal_gst'])}" if i.get("portal_gst") is not None else ", no portal record")
            + f", exposure {_inr(i['potential_exposure'])}")


def _followup_for(supplier_name: str, invoices: list[dict], total_count: int) -> str:
    lines = [f"Subject: Request to verify {total_count} {_plural(total_count, 'invoice', 'invoices')} for GST reconciliation",
             "", f"Dear {supplier_name} team,", "",
             "While reconciling our purchase register with the GST portal records, the following "
             f"{_plural(total_count, 'invoice shows', 'invoices show')} a difference that we would like to resolve with you:", ""]
    for i in invoices:
        code = i.get("reason_code") or "FUZZY_REVIEW"
        ask = _ASK.get(code, "confirm the invoice details as filed")
        portal = f"portal GST {_inr(i['portal_gst'])}" if i.get("portal_gst") is not None else "no matching portal record"
        lines.append(f"- {i['invoice_no']} (dated {i['invoice_date']}, taxable value {_inr(i['purchase_taxable_value'])}, "
                     f"GST {_inr(i['purchase_gst'])}; {portal}): please {ask}.")
    if total_count > len(invoices):
        lines.append(f"- ... and {total_count - len(invoices)} more {_plural(total_count - len(invoices), 'invoice', 'invoices')} (list attached).")
    lines += ["", "Could you please confirm the details above, or share the filed invoice copies / amendment reference where applicable? "
              "This is a routine reconciliation query to make sure both sets of records agree before we finalise our return.",
              "", "Thank you,", "Accounts Payable team"]
    return "\n".join(lines)


def fallback_brief(ev: dict) -> dict:
    """Deterministic brief built only from the evidence packet. Used when no LLM key is set, the call fails, or its output is rejected."""
    if ev["kind"] == "invoice":
        i, s, r = ev["invoice"], ev["supplier"], ev["supplier_risk"]
        why = [f"Reconciliation status: {i['reconciliation_status_label']} ({i['issue_label']}).", i["reason"] or ""]
        if i["match_confidence"] is not None:
            why.append(f"Matching confidence {i['match_confidence']:.0f}% ({i['match_confidence_label']}).")
        else:
            why.append("No portal record is linked to this entry, so no matching confidence is available.")
        why.append(f"Potential ITC Exposure {_inr(i['potential_exposure'])}" + (" - high-value invoice." if i["high_value"] else "."))
        if i["investigation_level"]:
            why.append(f"Investigation Priority {i['investigation_level']} (score {i['investigation_priority']} of 100, rank {i['investigation_rank']}).")
        if s.get("investigation_level"):
            why.append(f"Supplier {s['supplier_name']} has investigation priority {s['investigation_level']} with "
                       f"{s['affected_invoice_count']} affected {_plural(s['affected_invoice_count'], 'invoice', 'invoices')} out of {s['invoice_count']}.")
        if r.get("available"):
            why.append(f"Supplier Risk {round(r['score'] * 100)}% ({r['level']}).")
        verify = [_VERIFY.get(i["reason_code"] or "", "Compare the purchase-register entry with the invoice copy and the portal record.")]
        if i["purchase_gst"] is not None and i["portal_gst"] is not None:
            verify.append(f"Purchase register shows GST {_inr(i['purchase_gst'])}; portal shows {_inr(i['portal_gst'])}. "
                          "Confirm which figure matches the invoice copy.")
        if i["related_invoice_id"]:
            verify.append(f"Review the related entry {i['related_invoice_id']}.")
        verify.append("If the records cannot be reconciled from the documents on file, verification with the supplier is needed before any ITC decision.")
        followup = _followup_for(i["supplier_name"], [i], 1)
        return {"why_flagged": [w for w in why if w], "what_to_verify": verify, "draft_followup": followup}

    s, inv, r, it = ev["supplier"], ev["investigation"], ev["supplier_risk"], ev["issue_types"]
    n = ev["affected_invoice_count"]
    why = [f"{_inr(ev['potential_exposure'])} potential ITC exposure ({ev['exposure_share_pct']}% of the total), across "
           f"{n} affected {_plural(n, 'invoice', 'invoices')} out of {ev['invoice_count']}."]
    if it:
        why.append("Issue types: " + "; ".join(f"{c['label']} ({c['count']} {_plural(c['count'], 'invoice', 'invoices')}, {_inr(c['exposure'])})" for c in it) + ".")
    if ev["high_value_affected_count"]:
        k = ev["high_value_affected_count"]
        why.append(f"{k} of the affected {_plural(k, 'invoice is', 'invoices are')} high-value (claimed ITC of ₹50,000 or more).")
    if inv.get("available") and inv.get("score") is not None:
        why.append(f"Investigation Priority {inv['level']} (score {inv['score']} of 100, rank {inv['rank']}).")
    if r.get("available"):
        why.append(f"Supplier Risk {round(r['score'] * 100)}% ({r['level']})" + (": " + "; ".join(r["signals"]) if r["signals"] else "") + ".")
    else:
        why.append(r.get("note") or "Supplier Risk is not available.")
    verify = []
    seen = set()
    for c in it:
        v = _VERIFY.get(c["code"])
        if v and v not in seen:
            seen.add(v)
            verify.append(f"{c['label']} ({c['count']}): {v}")
    if ev["invoices"]:
        verify.append("Start with the highest-exposure invoices: " + "; ".join(_inv_line(i) for i in ev["invoices"][:3]) + ".")
    else:
        verify.append("No affected invoices are listed for this supplier.")
    verify.append("If the invoice copies and the portal records cannot be reconciled, verification with the supplier is needed before any ITC decision.")
    followup = _followup_for(s["supplier_name"], ev["invoices"], n) if n else "No follow-up is needed: no invoices from this supplier are currently flagged."
    return {"why_flagged": why, "what_to_verify": verify, "draft_followup": followup}


# ---------------------------------------------------------------------------------------------------------------- validation
_NUM_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_SMALL_INT_OK = 31          # counts, day numbers and ordinal-sized integers are allowed freely; everything larger must be in the evidence


def _walk_numbers(obj, out: set, texts: set):
    """Collect every numeric value in the evidence (plus formatted variants) and every string (invoice numbers, dates, GSTINs)."""
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        v = float(obj)
        out.update({round(v, 2), float(round(v)), round(v / 1e5, 1), round(v / 1e5, 2), round(v / 1e3, 1), round(v / 1e7, 2)})
        if 0 <= v <= 1:                                   # probabilities may be quoted as percentages
            out.update({float(round(v * 100)), round(v * 100, 1)})
        return
    if isinstance(obj, str):
        texts.add(obj)
        for m in _NUM_RE.findall(obj):                    # numbers embedded in backend text (reasons, signals) are evidence too
            try:
                out.add(round(float(m.replace(",", "")), 2))
            except ValueError:
                pass
        return
    if isinstance(obj, dict):
        for v in obj.values():
            _walk_numbers(v, out, texts)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_numbers(v, out, texts)


_PLAIN_NUM_RE = re.compile(r"[\d,]+(?:\.\d+)?")
_DATE_RE = re.compile(r"\b\d{4}-\d{2}(?:-\d{2})?\b|\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
# tokens mixing digits with letters, slashes or dashes are identifiers (invoice numbers, GSTINs, "GSTR-2B", "2026-27"),
# except plain amounts with a lakh/thousand/crore suffix ("4.2L", "90K", "1.25Cr"), which are values and must be checked
_CODE_RE = re.compile(r"\b(?![\d,]+(?:\.\d+)?(?:L|K|Cr)\b)(?=[\w/\-]*\d)(?=[\w/\-]*[A-Za-z/\-])[\w/\-]+\b")


def find_unsupported_numbers(text: str, evidence: dict) -> list[str]:
    """Numbers in `text` that do not appear in the evidence in any formatted form (and are not small counts)."""
    allowed: set = set()
    texts: set = set()
    _walk_numbers(evidence, allowed, texts)
    cleaned = text
    ids = [t for t in texts if len(t) <= 40 and any(ch.isdigit() for ch in t) and not _PLAIN_NUM_RE.fullmatch(t)]
    for t in sorted(ids, key=len, reverse=True):          # literal identifiers from the evidence: invoice numbers, GSTINs, dates, ids
        cleaned = cleaned.replace(t, " ")
    cleaned = _DATE_RE.sub(" ", cleaned)
    cleaned = _CODE_RE.sub(" ", cleaned)
    bad = []
    for m in _NUM_RE.findall(cleaned):
        raw = m.replace(",", "").rstrip(".")
        if not raw:
            continue
        try:
            v = float(raw)
        except ValueError:
            continue
        if v <= _SMALL_INT_OK and v == int(v):
            continue
        if any(abs(v - a) < 0.006 for a in allowed):
            continue
        bad.append(m)
    return bad


def forbidden_words(text: str) -> list[str]:
    low = text.lower()
    return [w for w in FORBIDDEN_WORDS if re.search(r"\b" + re.escape(w) + r"\b", low)]


def validate_brief(brief, evidence: dict) -> tuple[dict | None, str | None]:
    """Returns (clean brief, None) or (None, reason). Strict: anything unexpected is rejected so the fallback is used."""
    if not isinstance(brief, dict):
        return None, "LLM output is not a JSON object"
    out = {}
    for key in ("why_flagged", "what_to_verify"):
        v = brief.get(key)
        if isinstance(v, str):
            v = [v]
        if not isinstance(v, list) or not v or not all(isinstance(x, str) and x.strip() for x in v):
            return None, f"LLM output field '{key}' is missing or malformed"
        out[key] = [x.strip() for x in v][:8]
    d = brief.get("draft_followup")
    if isinstance(d, list) and all(isinstance(x, str) for x in d):
        d = "\n".join(d)
    if not isinstance(d, str) or len(d.strip()) < 40:
        return None, "LLM output field 'draft_followup' is missing or malformed"
    out["draft_followup"] = d.strip()
    text = "\n".join(out["why_flagged"] + out["what_to_verify"] + [out["draft_followup"]])
    bad_words = forbidden_words(text)
    if bad_words:
        return None, "LLM output used disallowed wording: " + ", ".join(bad_words)
    bad_nums = find_unsupported_numbers(text, evidence)
    if bad_nums:
        return None, "LLM output contained values not present in the evidence: " + ", ".join(bad_nums[:5])
    return out, None


def parse_llm_json(text: str):
    """Accept a bare JSON object or one wrapped in ```json fences / prose. Raises ValueError otherwise."""
    if not isinstance(text, str):
        raise ValueError("empty LLM response")
    t = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", t, re.S)
    if fence:
        t = fence.group(1)
    if not t.startswith("{"):
        start, end = t.find("{"), t.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise ValueError("no JSON object in LLM response")
        t = t[start:end + 1]
    return json.loads(t)


# ------------------------------------------------------------------------------------------------------------------- the LLM
SYSTEM_PROMPT = """You are the explanation layer of ITC Shield, a GST input-tax-credit reconciliation tool used by an Indian finance team.
You receive STRUCTURED EVIDENCE computed by the backend (reconciliation status, purchase-register vs portal values, Potential ITC Exposure,
Supplier Risk, Investigation Priority). Your job is to explain that evidence clearly and draft a polite supplier follow-up.

Hard rules:
1. Never calculate, re-estimate or adjust GST, ITC, exposure, risk, priority, eligibility or tax liability. Quote figures exactly as given.
2. Never invent facts, amounts, dates, invoice numbers, percentages, legal provisions or deadlines. Only use what is in the evidence.
   If the evidence is insufficient for a conclusion, say that verification is needed.
3. Never claim or imply fraud, a violation, non-compliance, wrongdoing or a guaranteed loss. The differences may be keying errors,
   timing, or late filing. Use the terms "Potential ITC Exposure", "Supplier Risk" and "Investigation Priority".
4. Write for a busy accounts-payable professional: concrete, short sentences, plain English, Indian rupee formatting as in the evidence.
5. Respond with ONLY a JSON object, no prose, with exactly these keys:
   {"why_flagged": [3-6 short bullet strings], "what_to_verify": [3-6 concrete check strings], "draft_followup": "a complete, polite email to the supplier (subject line first) listing the invoice numbers, dates and figures from the evidence and asking them to confirm or share the filed details"}"""


def _env(name, default=None):
    v = os.environ.get(name)
    return v.strip() if isinstance(v, str) and v.strip() else default


def llm_config() -> dict:
    """Which provider (if any) is configured. Never includes the key itself."""
    provider = (_env("LLM_PROVIDER") or "auto").lower()
    keys = {p: _env(v) for p, v in KEY_VARS.items()}
    if provider == "auto":
        provider = next((p for p in ("groq", "anthropic", "openai") if keys[p]), "none")
    if provider not in KEY_VARS:
        return {"configured": False, "provider": "none", "model": None,
                "reason": ("No LLM API key is set on the server, so briefs use the evidence-based fallback." if provider == "none"
                           else f"Unknown LLM_PROVIDER '{provider}' (use groq, anthropic, openai or none); briefs use the evidence-based fallback.")}
    key = keys[provider]
    if not key:
        return {"configured": False, "provider": "none", "model": None,
                "reason": f"LLM_PROVIDER is '{provider}' but {KEY_VARS[provider]} is not set, so briefs use the evidence-based fallback."}
    model = _env("LLM_MODEL") or DEFAULT_MODELS[provider]
    return {"configured": True, "provider": provider, "model": model, "base_url": _env("LLM_BASE_URL") or DEFAULT_BASE_URLS[provider],
            "timeout": float(_env("LLM_TIMEOUT") or DEFAULT_TIMEOUT), "_key": key}


def _call_llm(system: str, user: str, cfg: dict) -> str:
    """Live provider call (httpx). Only reached when a key is configured; tests inject a fake instead."""
    import httpx
    timeout = cfg.get("timeout", DEFAULT_TIMEOUT)
    if cfg["provider"] == "anthropic":
        url = (cfg.get("base_url") or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        r = httpx.post(url, timeout=timeout,
                       headers={"x-api-key": cfg["_key"], "anthropic-version": "2023-06-01", "content-type": "application/json"},
                       json={"model": cfg["model"], "max_tokens": 1500, "temperature": 0.2, "system": system,
                             "messages": [{"role": "user", "content": user}]})
        r.raise_for_status()
        return "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text")
    # groq and openai (and any other OpenAI-compatible server) share the chat-completions interface
    url = (cfg.get("base_url") or DEFAULT_BASE_URLS["openai"]).rstrip("/") + "/chat/completions"
    r = httpx.post(url, timeout=timeout, headers={"Authorization": f"Bearer {cfg['_key']}", "content-type": "application/json"},
                   json={"model": cfg["model"], "temperature": 0.2, "response_format": {"type": "json_object"},
                         "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]})
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


def _describe_failure(e: Exception) -> str:
    """Short, non-technical reason for the UI. Never includes response bodies or keys."""
    name = type(e).__name__
    status = getattr(getattr(e, "response", None), "status_code", None)
    if status == 429:
        return "LLM rate limit reached (HTTP 429)"
    if status is not None:
        return f"LLM request failed (HTTP {status})"
    if "Timeout" in name or "timed out" in str(e).lower():
        return "LLM timed out"
    if isinstance(e, (ValueError, json.JSONDecodeError, KeyError, IndexError, TypeError)):
        return f"LLM returned an unreadable response ({name})"
    return f"LLM unavailable ({name})"


def _user_prompt(evidence: dict) -> str:
    what = "supplier" if evidence["kind"] == "supplier" else "invoice"
    return (f"Write the investigation brief for this {what} from the evidence below.\n\nEVIDENCE (JSON, computed by the backend):\n"
            + json.dumps(evidence, ensure_ascii=False, indent=1)
            + "\n\nReturn only the JSON object described in the rules.")


def generate_brief(evidence: dict, llm_call: Callable[[str, str], str] | None = None, cfg: dict | None = None) -> dict:
    """
    Produces {"source": "llm"|"fallback", "provider", "model", "fallback_reason", "brief": {why_flagged, what_to_verify, draft_followup},
    "evidence", "disclaimer"}. `llm_call(system, user) -> str` can be injected (tests); otherwise the configured provider is used.
    Any failure or rejected output -> deterministic fallback, never an error.
    """
    cfg = cfg if cfg is not None else llm_config()
    base = {"evidence": evidence, "disclaimer": DISCLAIMER, "provider": cfg.get("provider", "none"), "model": cfg.get("model")}
    if llm_call is None:
        if not cfg.get("configured"):
            return {**base, "source": "fallback", "fallback_reason": cfg.get("reason"), "brief": fallback_brief(evidence)}
        llm_call = lambda s, u: _call_llm(s, u, cfg)  # noqa: E731
    try:
        raw = llm_call(SYSTEM_PROMPT, _user_prompt(evidence))
        parsed = parse_llm_json(raw)
    except Exception as e:  # network error, HTTP error, rate limit, timeout, bad JSON ...
        return {**base, "source": "fallback", "fallback_reason": f"{_describe_failure(e)}; showing the evidence-based brief instead.",
                "brief": fallback_brief(evidence)}
    clean, why = validate_brief(parsed, evidence)
    if clean is None:
        return {**base, "source": "fallback", "fallback_reason": f"{why}; showing the evidence-based brief instead.",
                "brief": fallback_brief(evidence)}
    return {**base, "source": "llm", "fallback_reason": None, "brief": clean}
