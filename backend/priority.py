"""
Phase 5 - ITC Risk Radar / Investigation Priority.

A transparent, deterministic ranking of WHICH supplier / invoice the finance team should look at first.
It only combines outputs that already exist (reconciliation, exposure ledger, Phase 4 supplier risk). It trains no model,
uses no LLM and no randomness. The score is a 0-100 ranking aid. It is NOT a probability and not a GST determination.

SUPPLIER investigation priority  (score = 100 x weighted sum of three 0-1 signals)
  exposure  E = supplier Potential ITC Exposure / max(largest supplier exposure in this run, EXPOSURE_FLOOR_SUPPLIER)
  risk      R = Phase 4 supplier risk score (0-1)                                   [only when a model score exists]
  evidence  S = 0.7 x severity + 0.3 x recurrence
                severity   = exposure-weighted average of SEVERITY[reason_code] over the supplier's exposed invoices
                recurrence = min(1, affected invoices / RECURRENCE_FULL)
  weights   with risk    : exposure 0.50, risk 0.30, evidence 0.20
            without risk : the weights of the signals that exist are re-scaled to sum to 1  (exposure 0.714, evidence 0.286)
  A supplier with no exposed invoices has E = S = 0, so its score can only come from risk (max 30) and is always LOW.

INVOICE investigation priority  (invoices with exposure > 0 only)
  score = 100 x (0.70 x E_i + 0.30 x SEVERITY[reason_code]),
  E_i = invoice exposure / max(largest invoice exposure in this run, EXPOSURE_FLOOR_INVOICE)

Levels (both): HIGH >= 60, MEDIUM >= 35, otherwise LOW.
Ties are broken by higher exposure, then by id, so output order is fully reproducible.
"""
from reconcile import ISSUE_LABELS, STATUS_LABELS, _inr

W_EXPOSURE, W_RISK, W_EVIDENCE = 0.50, 0.30, 0.20
W_INV_EXPOSURE, W_INV_SEVERITY = 0.70, 0.30
EXPOSURE_FLOOR_SUPPLIER = 10_000.0     # a run whose biggest supplier exposure is below this is not stretched to a full 1.0
EXPOSURE_FLOOR_INVOICE = 5_000.0
RECURRENCE_FULL = 5                    # this many affected invoices = full recurrence signal
EVIDENCE_SEVERITY_SHARE = 0.70         # evidence = 0.7 severity + 0.3 recurrence
HIGH_THRESHOLD, MEDIUM_THRESHOLD = 60.0, 35.0

# Judgement-based, documented defaults (not learned). Higher = the records show the problem more directly / more completely.
SEVERITY = {
    "MISSING": 1.0,               # full claimed tax has no portal record
    "DUPLICATE": 1.0,             # the same invoice appears more than once in the purchase register
    "TAX_MISMATCH": 0.7,          # both records exist; only the tax difference is counted
    "AMOUNT_MISMATCH": 0.7,
    "INVOICE_NO_MISMATCH": 0.5,   # a portal record was found under a different number: likely a keying difference
    "GSTIN_MISMATCH": 0.5,
    "FUZZY_REVIEW": 0.4,          # probable (not certain) link to a portal record
}

METHODOLOGY = {
    "name": "Investigation Priority",
    "scale": "0-100 ranking score. Not a probability and not a GST determination.",
    "supplier_formula": ("100 x (0.50 x Exposure + 0.30 x Supplier Risk + 0.20 x Evidence). Exposure = supplier exposure / largest "
                         f"supplier exposure (at least ₹{EXPOSURE_FLOOR_SUPPLIER:,.0f}). Supplier Risk = Phase 4 score. "
                         f"Evidence = 0.7 x exposure-weighted issue severity + 0.3 x min(1, affected invoices / {RECURRENCE_FULL}). "
                         "When no Supplier Risk score exists the remaining weights are re-scaled (0.714 / 0.286)."),
    "invoice_formula": (f"100 x (0.70 x invoice exposure / largest invoice exposure (at least ₹{EXPOSURE_FLOOR_INVOICE:,.0f}) "
                        "+ 0.30 x issue severity)."),
    "severity": SEVERITY,
    "thresholds": {"HIGH": HIGH_THRESHOLD, "MEDIUM": MEDIUM_THRESHOLD},
}


def level_for(score: float) -> str:
    return "HIGH" if score >= HIGH_THRESHOLD else "MEDIUM" if score >= MEDIUM_THRESHOLD else "LOW"


def _label(code: str | None) -> str | None:
    return None if code is None else (STATUS_LABELS.get(code) or ISSUE_LABELS.get(code) or code)


def _cents(x) -> int:
    return int(round(float(x) * 100))


def supplier_score(exposure: float, max_exposure: float, risk_score, severity: float, affected: int) -> dict:
    """Pure function: the supplier formula. Returns score (0-100), level and the per-signal breakdown."""
    e = min(1.0, max(0.0, exposure) / max(max_exposure, EXPOSURE_FLOOR_SUPPLIER)) if exposure > 0 else 0.0
    recurrence = min(1.0, affected / RECURRENCE_FULL) if affected > 0 else 0.0
    ev = EVIDENCE_SEVERITY_SHARE * severity + (1 - EVIDENCE_SEVERITY_SHARE) * recurrence if affected > 0 else 0.0
    parts = [("exposure", "Potential ITC Exposure", e, W_EXPOSURE), ("evidence", "Issue evidence", ev, W_EVIDENCE)]
    if risk_score is not None:
        parts.insert(1, ("risk", "Supplier Risk", float(risk_score), W_RISK))
    wsum = sum(p[3] for p in parts)
    comps = [{"key": k, "label": lab, "value": round(v, 4), "weight": round(w / wsum, 4),
              "contribution": round(100 * v * w / wsum, 2)} for k, lab, v, w in parts]
    score = round(sum(100 * v * w / wsum for _, _, v, w in parts), 1)
    return {"score": score, "level": level_for(score), "components": comps, "risk_available": risk_score is not None}


def invoice_score(exposure: float, max_exposure: float, reason_code: str) -> dict:
    """Pure function: the invoice formula."""
    if exposure <= 0:
        return {"score": 0.0, "level": "LOW"}
    e = min(1.0, exposure / max(max_exposure, EXPOSURE_FLOOR_INVOICE))
    s = SEVERITY.get(reason_code, 0.5)
    score = round(100 * (W_INV_EXPOSURE * e + W_INV_SEVERITY * s), 1)
    return {"score": score, "level": level_for(score)}


def _plural(n, one, many):
    return one if n == 1 else many


def _supplier_signals(s, top, share_pct, risk_available):
    sig = []
    if s["exposure"] > 0:
        sig.append({"key": "exposure", "value": s["exposure"],
                    "text": f"{_inr(s['exposure'])} potential ITC exposure ({share_pct}% of the total)"})
        n = s["affected_invoice_count"]
        sig.append({"key": "affected_invoices", "value": n,
                    "text": f"{n} affected {_plural(n, 'invoice', 'invoices')} out of {s['invoice_count']}"})
        sig.append({"key": "top_issue", "value": top["code"],
                    "text": f"Largest source: {top['label']} ({top['count']} {_plural(top['count'], 'invoice', 'invoices')}, {_inr(top['exposure'])})"})
        if s.get("high_value_count"):                       # high-value AMONG THE AFFECTED invoices (not the supplier's whole book)
            k = s["high_value_count"]
            sig.append({"key": "high_value", "value": k,
                        "text": f"{k} of the affected {_plural(k, 'invoice is', 'invoices are')} high-value (claimed ITC of ₹50,000 or more)"})
    else:
        sig.append({"key": "no_exposure", "value": 0, "text": "No invoices currently contribute to potential ITC exposure"})
    if risk_available:
        for f in (s.get("key_risk_factors") or [])[:3]:
            sig.append({"key": f["feature"], "value": f["value"], "text": f["text"][:1].upper() + f["text"][1:], "source": "risk_signal"})
    else:
        sig.append({"key": "risk_unavailable", "value": None, "source": "note",
                    "text": "Supplier Risk is not available for this dataset, so priority uses exposure and issue evidence only"})
    return sig


def attach_priority(result: dict) -> dict:
    """
    Adds `investigation` to every supplier and every invoice in `result` (in place; no existing field is modified) and stores
    `result["priority"]` = {methodology, risk_available, suppliers (ranked, exposure > 0), invoices (ranked, exposure > 0)}.
    Run AFTER attach_supplier_risk (risk fields are used when present; their absence is handled).
    """
    invoices, suppliers = result["invoices"], result["suppliers"]
    exposed = [i for i in invoices if i["exposure"] > 0]
    total_c = sum(_cents(i["exposure"]) for i in exposed)

    by_sup: dict = {}
    for i in exposed:
        by_sup.setdefault(i["supplier_id"], []).append(i)

    max_sup = max((_cents(sum(x["exposure"] for x in v)) / 100 for v in by_sup.values()), default=0.0)
    max_inv = max((i["exposure"] for i in exposed), default=0.0)

    rows = []
    for s in suppliers:
        inv = by_sup.get(s["supplier_id"], [])
        exp = sum(_cents(i["exposure"]) for i in inv) / 100
        sev = (sum(i["exposure"] * SEVERITY.get(i["reason_code"], 0.5) for i in inv) / sum(i["exposure"] for i in inv)) if inv else 0.0
        by_code: dict = {}
        for i in inv:
            c = by_code.setdefault(i["reason_code"], {"code": i["reason_code"], "label": _label(i["reason_code"]), "count": 0, "_c": 0})
            c["count"] += 1
            c["_c"] += _cents(i["exposure"])
        tops = sorted(by_code.values(), key=lambda c: (-c["_c"], c["code"]))
        top = None
        if tops:
            t = tops[0]
            top = {"code": t["code"], "label": t["label"], "count": t["count"], "exposure": t["_c"] / 100}
        calc = supplier_score(exp, max_sup, s.get("risk_score"), sev, len(inv))
        share = round(100 * _cents(exp) / total_c, 1) if total_c else 0.0
        view = {"exposure": exp, "affected_invoice_count": len(inv), "invoice_count": s["invoice_count"],
                "high_value_count": sum(1 for i in inv if i.get("high_value")), "key_risk_factors": s.get("key_risk_factors")}
        rows.append((s, {
            "score": calc["score"], "level": calc["level"], "rank": None,
            "potential_exposure": exp, "exposure_share_pct": share, "affected_invoice_count": len(inv),
            "supplier_risk": s.get("risk_score"), "supplier_risk_level": s.get("predicted_risk_level"),
            "risk_available": calc["risk_available"], "top_issue": top, "components": calc["components"],
            "signals": _supplier_signals(view, top, share, calc["risk_available"]),
        }))
    rows.sort(key=lambda r: (-r[1]["score"], -r[1]["potential_exposure"], r[0]["supplier_id"]))
    for n, (s, inv_) in enumerate(rows, 1):
        inv_["rank"] = n
        s["investigation"] = inv_

    level_of = {s["supplier_id"]: s["investigation"] for s, _ in rows}
    inv_rows = []
    for i in invoices:
        if i["exposure"] <= 0:
            i["investigation"] = None
            continue
        c = invoice_score(i["exposure"], max_inv, i["reason_code"])
        sup = level_of.get(i["supplier_id"])
        signals = [
            {"key": "exposure", "value": i["exposure"], "text": f"{_inr(i['exposure'])} potential ITC exposure"},
            {"key": "issue", "value": i["reason_code"], "text": f"{_label(i['reason_code'])}: {i['reason']}"},
        ]
        if i.get("match_confidence") is not None:
            signals.append({"key": "match_confidence", "value": i["match_confidence"],
                            "text": f"Matching confidence {i['match_confidence']:.0f}% ({i['match_confidence_label']})"})
        if i.get("high_value"):
            signals.append({"key": "high_value", "value": True, "text": "High-value invoice (claimed ITC of ₹50,000 or more)"})
        if sup:
            signals.append({"key": "supplier_priority", "value": sup["level"],
                            "text": f"Supplier investigation priority is {sup['level']} (rank {sup['rank']})"})
        i["investigation"] = {"score": c["score"], "level": c["level"], "rank": None, "signals": signals}
        inv_rows.append(i)
    inv_rows.sort(key=lambda i: (-i["investigation"]["score"], -i["exposure"], i["invoice_id"]))
    for n, i in enumerate(inv_rows, 1):
        i["investigation"]["rank"] = n

    ranked = []
    for s, v in rows:
        if v["potential_exposure"] <= 0:
            continue
        ranked.append({"supplier_id": s["supplier_id"], "supplier_name": s["supplier_name"], "rank": v["rank"],
                       "investigation_priority": v["score"], "priority_level": v["level"],
                       "potential_exposure": v["potential_exposure"], "supplier_risk": v["supplier_risk"],
                       "supplier_risk_level": v["supplier_risk_level"], "risk_available": v["risk_available"],
                       "affected_invoice_count": v["affected_invoice_count"], "top_issue": v["top_issue"],
                       "components": v["components"], "signals": v["signals"]})
    top_invoices = [{"invoice_id": i["invoice_id"], "invoice_no": i["invoice_no"], "supplier_id": i["supplier_id"],
                     "supplier_name": i["supplier_name"], "rank": i["investigation"]["rank"],
                     "investigation_priority": i["investigation"]["score"], "priority_level": i["investigation"]["level"],
                     "potential_exposure": i["exposure"], "issue_label": _label(i["reason_code"]),
                     "signals": i["investigation"]["signals"]} for i in inv_rows]
    result["priority"] = {"methodology": METHODOLOGY, "risk_available": any(s.get("risk_score") is not None for s in suppliers),
                          "suppliers": ranked, "invoices": top_invoices}
    return result["priority"]
