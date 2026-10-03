"""
Reconciliation + exposure quantification for ITC Shield.

reconcile(books_df, portal_df) -> dict with:
    invoices  : one row per purchase-register entry (matched or flagged)
    suppliers : per-supplier aggregates
    summary   : headline numbers

This compares two record sets and estimates *Potential ITC Exposure*.
It does NOT decide GST eligibility - it flags what needs a human to look at.
"""
import re
import pandas as pd

try:                                   # RapidFuzz is preferred; difflib keeps the engine working without it
    from rapidfuzz import fuzz as _fuzz
    def _ratio(a: str, b: str) -> float:
        return float(_fuzz.ratio(a, b))
    def _token_ratio(a: str, b: str) -> float:
        return float(_fuzz.token_sort_ratio(a, b))
except ImportError:                    # pragma: no cover
    from difflib import SequenceMatcher
    def _ratio(a: str, b: str) -> float:
        return 100.0 * SequenceMatcher(None, a, b).ratio()
    def _token_ratio(a: str, b: str) -> float:
        return _ratio(" ".join(sorted(a.split())), " ".join(sorted(b.split())))

TAX_TOL = 5.0      # Rs tolerance on tax amounts (rounding differences are not issues)
VALUE_TOL = 1.0    # Rs tolerance on taxable value when comparing fields
HIGH_VALUE_TAX = 50_000.0   # claimed ITC at/above this is flagged "high value" (a review prompt only)

# fuzzy matching (tier 4) - a candidate must clear ALL of these gates before it is linked
FUZZY_MIN_INV_SIM = 75.0     # invoice-number similarity (0-100)
FUZZY_MIN_VALUE_SIM = 90.0   # taxable-value similarity (0-100)
FUZZY_MAX_DAYS = 7           # invoice-date distance in days
FUZZY_MIN_CONFIDENCE = 70.0  # overall confidence floor
CONF_STRONG, CONF_PROBABLE = 95.0, 85.0   # >= strong | >= probable | below = requires review

ISSUE_LABELS = {
    "MISSING_IN_PORTAL": "Not found in portal",
    "AMOUNT_MISMATCH": "Amount mismatch",
    "INVOICE_NO_MISMATCH": "Invoice no. mismatch",
    "GSTIN_MISMATCH": "Supplier GSTIN mismatch",
    "DUPLICATE_ENTRY": "Duplicate entry",
    "FUZZY_REVIEW": "Probable match - review",
}

# Phase 2 status vocabulary (issue_type above is kept unchanged for backward compatibility)
STATUS_LABELS = {
    "MATCHED": "Matched", "TAX_MISMATCH": "Tax mismatch", "AMOUNT_MISMATCH": "Amount mismatch",
    "MISSING": "Missing in portal", "DUPLICATE": "Duplicate", "REVIEW": "Needs review",
}
STATUS_ORDER = ["MISSING", "AMOUNT_MISMATCH", "TAX_MISMATCH", "DUPLICATE", "REVIEW"]

EXPLAIN = {
    "MISSING_IN_PORTAL": ("No record of this invoice was found in the portal data. "
                          "ITC claimed on it may be at risk until the supplier's return reflects it."),
    "AMOUNT_MISMATCH": ("The invoice is in both records, but the tax reported on the portal is lower than "
                        "the ITC claimed in the purchase register."),
    "INVOICE_NO_MISMATCH": ("A portal record with the same supplier, date and taxable value exists under a "
                            "different invoice number. This is often a keying difference."),
    "GSTIN_MISMATCH": ("A portal record with the same invoice number and taxable value exists under a "
                       "different supplier GSTIN. The GSTIN in the purchase register may be mistyped."),
    "FUZZY_REVIEW": ("No exact match exists, but a portal record from the same supplier is very similar "
                     "(invoice number, date and value). It is a probable match, not a certain one."),
    "DUPLICATE_ENTRY": ("This invoice appears more than once in the purchase register, while the portal "
                        "shows it once. The repeated entry's ITC has no matching portal record."),
}

ACTION = {
    "MISSING_IN_PORTAL": "Ask the supplier to confirm whether this invoice has been reported in their GST return.",
    "AMOUNT_MISMATCH": "Compare the invoice copy with the values the supplier reported; ask the supplier to amend or correct the books entry.",
    "INVOICE_NO_MISMATCH": "Check the invoice number on the original invoice and correct the books entry or ask the supplier to amend.",
    "GSTIN_MISMATCH": "Verify the supplier GSTIN on the original invoice and update the purchase register.",
    "DUPLICATE_ENTRY": "Review the repeated entry and reverse it if it is confirmed as a duplicate.",
    "FUZZY_REVIEW": "Compare the invoice copy with the similar portal record and confirm they are the same invoice.",
}


def _norm(s) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def normalize_invoice_no(s) -> str:
    """Upper-case, drop separators, drop leading zeros inside digit runs: 'INV/2026/0042' == 'inv-2026-42'."""
    t = re.sub(r"\d+", lambda m: str(int(m.group())), str(s).upper())
    return re.sub(r"[^A-Z0-9]", "", t)


_NAME_NOISE = {"PVT", "PRIVATE", "LTD", "LIMITED", "LLP", "CO", "COMPANY", "INC", "THE"}


def normalize_supplier_name(s) -> str:
    """'Kalyani Polymers Pvt. Ltd.' == 'KALYANI POLYMERS PRIVATE LIMITED'."""
    t = re.sub(r"[^A-Z0-9 ]", " ", str(s).upper().replace("&", " AND "))
    return " ".join(w for w in t.split() if w not in _NAME_NOISE)


def match_label(conf) -> str | None:
    if conf is None:
        return None
    return "Strong match" if conf >= CONF_STRONG else "Probable match" if conf >= CONF_PROBABLE else "Requires review"


def _val_sim(a: float, b: float) -> float:
    m = max(abs(a), abs(b))
    return 100.0 if m == 0 else 100.0 * (1 - min(1.0, abs(a - b) / m))


def _date_sim(a, b) -> float:
    try:
        d = abs((pd.Timestamp(a) - pd.Timestamp(b)).days)
    except Exception:
        return 0.0
    return max(0.0, 100.0 - 12.0 * d)


def match_confidence(b: dict, p: dict, use_value: bool = True) -> float:
    """
    0-100 score for "these two records are the same invoice", computed from the fields themselves:
      invoice number similarity 35% | supplier GSTIN similarity 20% | taxable value 20% |
      invoice date 15% | supplier name similarity 10% (if both sides have a name).
    Exact (tier 1) links already share GSTIN + invoice key, so the value term is left out
    (`use_value=False`): a different amount does not make it a different invoice.
    """
    parts = [(0.35, _ratio(str(b["invoice_no"]).upper().strip(), str(p["invoice_no"]).upper().strip())),
             (0.20, 100.0 if b["supplier_gstin"] == p["supplier_gstin"] else _ratio(b["supplier_gstin"], p["supplier_gstin"])),
             (0.15, _date_sim(b["invoice_date"], p["invoice_date"]))]
    if use_value:
        parts.append((0.20, _val_sim(float(b["taxable_value"]), float(p["taxable_value"]))))
    bn, pn = b.get("supplier_name"), p.get("supplier_name")
    if isinstance(bn, str) and isinstance(pn, str) and bn and pn:
        parts.append((0.10, _token_ratio(normalize_supplier_name(bn), normalize_supplier_name(pn))))
    w = sum(x for x, _ in parts)
    return round(sum(x * s for x, s in parts) / w, 1)


def _prep(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy().reset_index(drop=True)
    df["supplier_gstin"] = df["supplier_gstin"].astype(str).str.strip().str.upper()
    df["invoice_no"] = df["invoice_no"].astype(str).str.strip()
    df["inv_key"] = df["invoice_no"].map(normalize_invoice_no)
    df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for c in ("taxable_value", "cgst", "sgst", "igst"):
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0.0).round(2)
    df["total_tax"] = (df["cgst"] + df["sgst"] + df["igst"]).round(2)
    return df


def _match(left: pd.DataFrame, right: pd.DataFrame, keys):
    """One-to-one matching on `keys`; returns DataFrame[bid, pid]."""
    if left.empty or right.empty:
        return pd.DataFrame({"bid": [], "pid": []}, dtype=int)
    rp = right.drop_duplicates(subset=keys)
    m = left[["bid"] + keys].merge(rp[["pid"] + keys], on=keys, how="inner")
    m = m.drop_duplicates("pid").drop_duplicates("bid")
    return m[["bid", "pid"]]


def _priority(exposure: float) -> str:
    if exposure >= 6_000:
        return "High"
    if exposure >= 2_000:
        return "Medium"
    return "Low"


def _fields(b, p):
    spec = [("Invoice number", "invoice_no", "text"), ("Invoice date", "invoice_date", "text"),
            ("Supplier GSTIN", "supplier_gstin", "text"), ("Taxable value", "taxable_value", "money"),
            ("CGST", "cgst", "money"), ("SGST", "sgst", "money"), ("IGST", "igst", "money"),
            ("Total tax (ITC)", "total_tax", "money")]
    out = []
    for label, col, kind in spec:
        bv = b[col]
        pv = None if p is None else p[col]
        if kind == "money":
            bv = float(bv)
            pv = None if pv is None else float(pv)
            diff = None if pv is None else round(bv - pv, 2)
            tol = VALUE_TOL if col == "taxable_value" else TAX_TOL
            flag = diff is not None and abs(diff) > tol
        else:
            bv = str(bv)
            pv = None if pv is None else str(pv)
            diff = None
            flag = pv is not None and (_norm(bv) != _norm(pv))
        out.append({"field": label, "kind": kind, "books": bv, "portal": pv, "difference": diff, "flag": bool(flag)})
    return out


def _tier4_fuzzy(rem: pd.DataFrame, p_rem: pd.DataFrame):
    """Fuzzy tier: leftover books vs leftover portal records of the same supplier. Returns [(conf, bid, pid)]."""
    if rem.empty or p_rem.empty:
        return []
    by_gstin, by_name = {}, {}
    for pr in p_rem.to_dict("records"):
        by_gstin.setdefault(pr["supplier_gstin"], []).append(pr)
        nm = pr.get("supplier_name")
        if isinstance(nm, str) and nm:
            by_name.setdefault(normalize_supplier_name(nm), []).append(pr)
    cands = []
    for br in rem.to_dict("records"):
        pool = {id(x): x for x in by_gstin.get(br["supplier_gstin"], [])}
        bn = br.get("supplier_name")
        if isinstance(bn, str) and bn:
            pool.update({id(x): x for x in by_name.get(normalize_supplier_name(bn), [])})
        for pr in pool.values():
            if _ratio(br["inv_key"], pr["inv_key"]) < FUZZY_MIN_INV_SIM:
                continue
            if _val_sim(br["taxable_value"], pr["taxable_value"]) < FUZZY_MIN_VALUE_SIM:
                continue
            try:
                days = abs((pd.Timestamp(br["invoice_date"]) - pd.Timestamp(pr["invoice_date"])).days)
            except Exception:
                continue
            if days > FUZZY_MAX_DAYS:
                continue
            conf = match_confidence(br, pr)
            if conf >= FUZZY_MIN_CONFIDENCE:
                cands.append((conf, int(br["bid"]), int(pr["pid"])))
    cands.sort(key=lambda c: (-c[0], c[1], c[2]))
    used_b, used_p, out = set(), set(), []
    for conf, bid, pid in cands:           # greedy one-to-one, best confidence first
        if bid in used_b or pid in used_p:
            continue
        used_b.add(bid); used_p.add(pid); out.append((conf, bid, pid))
    return out


def _exposure_reason(issue, rs, r, prow, exposure, conf):
    tax = r["total_tax"]
    if issue is None:
        return None
    if issue == "MISSING_IN_PORTAL":
        return f"No portal record found for this supplier and invoice number, so the full claimed tax of {_inr(round(tax))} is counted."
    if issue == "DUPLICATE_ENTRY":
        return f"Repeated purchase-register entry with no separate portal record, so its claimed tax of {_inr(round(tax))} is counted."
    if issue == "AMOUNT_MISMATCH":
        if exposure <= 0:
            return "Records differ, but the portal tax is not lower than the tax claimed, so nothing is counted."
        return (f"Claimed tax {_inr(round(tax))} is higher than portal tax {_inr(round(prow['total_tax']))}; "
                f"only the {_inr(round(exposure))} difference is counted.")
    cs = f" ({conf:.0f}% match confidence)" if conf is not None else ""
    return (f"The portal lists this invoice under a different invoice number or supplier GSTIN{cs}, so the full claimed "
            f"tax of {_inr(round(tax))} is counted until it is verified.")


def _inr(x) -> str:
    """Rupee amount with Indian digit grouping; paise shown only when present: 1234567 -> ₹12,34,567."""
    x = round(float(x), 2)
    whole, frac = divmod(round(abs(x) * 100), 100)
    s = str(whole)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:]); head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{'-' if x < 0 else ''}₹{s}" + (f".{frac:02d}" if frac else "")


def _reason_code(issue, rs):
    """Deterministic code: the reconciliation status, or the specific link type for REVIEW rows."""
    if issue is None:
        return None
    return issue if rs == "REVIEW" else rs


def _trace_reason(code, r, prow, exposure, related):
    """Human-readable reason built only from the invoice's own values (no fixed amounts)."""
    if code is None:
        return None
    tax, tv = r["total_tax"], r["taxable_value"]
    if code == "MISSING":
        return (f"No portal record was found for invoice {r['invoice_no']} from supplier {r['supplier_gstin']}, "
                f"so the full purchase GST of {_inr(tax)} is exposed.")
    if code == "DUPLICATE":
        return (f"Invoice {r['invoice_no']} is entered again in the purchase register (first entry {related}); "
                f"the repeated purchase GST of {_inr(tax)} has no separate portal record and is exposed.")
    if code == "TAX_MISMATCH":
        tail = (f" Taxable value agrees at {_inr(tv)}; the {_inr(exposure)} difference is exposed."
                if exposure > 0 else " The portal GST is not lower than the purchase GST, so no exposure is counted.")
        return f"Purchase GST is {_inr(tax)} while the portal record reports {_inr(prow['total_tax'])}." + tail
    if code == "AMOUNT_MISMATCH":
        tail = (f" The {_inr(exposure)} GST difference is exposed." if exposure > 0
                else " The portal GST is not lower than the purchase GST, so no exposure is counted.")
        return (f"Purchase taxable value is {_inr(tv)} while the portal record reports {_inr(prow['taxable_value'])}; "
                f"purchase GST is {_inr(tax)} against {_inr(prow['total_tax'])} on the portal." + tail)
    if code == "INVOICE_NO_MISMATCH":
        return (f"The portal lists this invoice as {prow['invoice_no']} instead of {r['invoice_no']} "
                f"(same supplier, date and taxable value of {_inr(tv)}); the full purchase GST of {_inr(tax)} is exposed until verified.")
    if code == "GSTIN_MISMATCH":
        return (f"The portal record for invoice {r['invoice_no']} sits under supplier GSTIN {prow['supplier_gstin']} "
                f"instead of {r['supplier_gstin']}; the full purchase GST of {_inr(tax)} is exposed until verified.")
    return (f"The portal has a similar record, invoice {prow['invoice_no']} dated {prow['invoice_date']} "
            f"(taxable value {_inr(prow['taxable_value'])}), but it is not an exact match; "
            f"the full purchase GST of {_inr(tax)} is exposed until verified.")


def reconcile(books_raw: pd.DataFrame, portal_raw: pd.DataFrame) -> dict:
    b = _prep(books_raw)
    p = _prep(portal_raw)
    b["bid"] = range(len(b))
    p["pid"] = range(len(p))
    if "invoice_id" not in b.columns:
        b["invoice_id"] = [f"PI-{i + 1:05d}" for i in range(len(b))]
    if "supplier_name" not in b.columns:
        b["supplier_name"] = b["supplier_gstin"]
    if "category" not in b.columns:
        b["category"] = ""

    b["_dup"] = b.groupby(["supplier_gstin", "inv_key"]).cumcount() > 0
    primary = b[~b["_dup"]]

    # tier 1: same supplier GSTIN + normalised invoice number
    t1 = _match(primary, p, ["supplier_gstin", "inv_key"])
    link = {int(r.bid): (int(r.pid), "exact") for r in t1.itertuples()}

    # tier 2: same GSTIN + taxable value + date, different invoice number
    rem = primary[~primary["bid"].isin(t1["bid"])]
    p_rem = p[~p["pid"].isin(t1["pid"])]
    t2 = _match(rem, p_rem, ["supplier_gstin", "taxable_value", "invoice_date"])
    link.update({int(r.bid): (int(r.pid), "invoice_no") for r in t2.itertuples()})

    # tier 3: same invoice number + taxable value, different GSTIN
    rem = rem[~rem["bid"].isin(t2["bid"])]
    p_rem = p_rem[~p_rem["pid"].isin(t2["pid"])]
    t3 = _match(rem, p_rem, ["inv_key", "taxable_value"])
    link.update({int(r.bid): (int(r.pid), "gstin") for r in t3.itertuples()})

    # tier 4: fuzzy - similar invoice number / value / date for the same supplier (confidence is computed)
    rem = rem[~rem["bid"].isin(t3["bid"])]
    p_rem = p_rem[~p_rem["pid"].isin(t3["pid"])]
    for conf, bid, pid in _tier4_fuzzy(rem, p_rem):
        link[bid] = (pid, "fuzzy")

    first_by_key = {(r.supplier_gstin, r.inv_key): int(r.bid) for r in primary.itertuples()}
    rows = []
    for r in b.to_dict("records"):
        bid = int(r["bid"])
        prow, issue, related, exposure, conf, method = None, None, None, 0.0, None, None
        if r["_dup"]:
            issue = "DUPLICATE_ENTRY"
            first = first_by_key[(r["supplier_gstin"], r["inv_key"])]
            related = b.loc[first, "invoice_id"]
            if first in link:
                prow = p.loc[link[first][0]].to_dict()
            exposure = r["total_tax"]
        elif bid in link:
            pid, how = link[bid]
            prow = p.loc[pid].to_dict()
            conf = match_confidence(r, prow, use_value=(how != "exact"))
            if how == "invoice_no":
                issue, exposure, method = "INVOICE_NO_MISMATCH", r["total_tax"], "date_value"
            elif how == "gstin":
                issue, exposure, method = "GSTIN_MISMATCH", r["total_tax"], "gstin_value"
            elif how == "fuzzy":
                issue, exposure, method = "FUZZY_REVIEW", r["total_tax"], "fuzzy"
            else:
                method = "exact" if str(r["invoice_no"]).upper() == str(prow["invoice_no"]).upper() else "exact_normalized"
                gap = round(r["total_tax"] - prow["total_tax"], 2)
                if abs(gap) > TAX_TOL or abs(r["taxable_value"] - prow["taxable_value"]) > VALUE_TOL:
                    issue = "AMOUNT_MISMATCH"
                    exposure = max(gap, 0.0)
        else:
            issue, exposure = "MISSING_IN_PORTAL", r["total_tax"]

        if issue is None:
            rs = "MATCHED"
        elif issue == "MISSING_IN_PORTAL":
            rs = "MISSING"
        elif issue == "DUPLICATE_ENTRY":
            rs = "DUPLICATE"
        elif issue == "AMOUNT_MISMATCH":
            rs = "AMOUNT_MISMATCH" if abs(r["taxable_value"] - prow["taxable_value"]) > VALUE_TOL else "TAX_MISMATCH"
        else:
            rs = "REVIEW"

        canon_gstin = prow["supplier_gstin"] if (issue == "GSTIN_MISMATCH" and prow) else r["supplier_gstin"]
        explanation = EXPLAIN.get(issue, "Purchase register and portal records agree within tolerance.")
        if issue == "AMOUNT_MISMATCH":
            explanation = (f"The invoice is in both records, but the tax on the portal is "
                           f"{_inr(round(exposure))} lower than the ITC claimed in the purchase register.")
        if issue in ("INVOICE_NO_MISMATCH", "FUZZY_REVIEW") and prow:
            explanation += f" Portal shows it as {prow['invoice_no']}."
        if issue == "DUPLICATE_ENTRY":
            explanation += f" Original entry: {related}."
        high_value = bool(r["total_tax"] >= HIGH_VALUE_TAX)
        rows.append({
            "invoice_id": r["invoice_id"], "supplier_name": r["supplier_name"],
            "supplier_id": canon_gstin, "supplier_gstin": r["supplier_gstin"],
            "category": r["category"], "invoice_no": r["invoice_no"], "invoice_date": r["invoice_date"],
            "taxable_value": float(r["taxable_value"]), "itc_claimed": float(r["total_tax"]),
            "portal_tax": None if prow is None else float(prow["total_tax"]),
            "status": "issue" if issue else "matched",
            "issue_type": issue, "issue_label": ISSUE_LABELS.get(issue, "Matched"),
            "recon_status": rs, "recon_status_label": STATUS_LABELS[rs],
            "match_method": method,
            "match_confidence": conf, "match_confidence_label": match_label(conf),
            "portal_invoice_no": None if prow is None else prow["invoice_no"],
            "portal_supplier_gstin": None if prow is None else prow["supplier_gstin"],
            "high_value": high_value,
            "exposure": round(float(exposure), 2),
            "exposure_reason": _exposure_reason(issue, rs, r, prow, float(exposure), conf),
            "portal_taxable_value": None if prow is None else float(prow["taxable_value"]),
            "reason_code": _reason_code(issue, rs),
            "reason": _trace_reason(_reason_code(issue, rs), r, prow, float(exposure), related),
            "priority": _priority(exposure) if issue else None,
            "explanation": explanation,
            "suggested_action": ACTION.get(issue),
            "related_invoice_id": related,
            "portal_found": prow is not None,
            "fields": _fields(r, prow),
        })

    inv = pd.DataFrame(rows)
    expected = round(float(inv["itc_claimed"].sum()), 2)
    exposure_total = round(float(inv["exposure"].sum()), 2)
    issues = inv[inv["status"] == "issue"]
    summary = {
        "expected_itc": expected,
        "reconciled_itc": round(expected - exposure_total, 2),
        "potential_exposure": exposure_total,
        "exposure_pct": round(100 * exposure_total / expected, 1) if expected else 0.0,
        "issue_count": int(len(issues)),
        "invoice_count": int(len(inv)),
        "matched_count": int((inv["status"] == "matched").sum()),
        "high_priority_count": int((issues["priority"] == "High").sum()),
        "high_value_count": int(inv["high_value"].sum()),
    }

    def status_counts(g):
        return {s: int((g["recon_status"] == s).sum()) for s in STATUS_ORDER}

    # ---- suppliers -------------------------------------------------------------------
    suppliers = []
    for sid, g in inv.groupby("supplier_id"):
        iss = g[g["status"] == "issue"]
        exp = round(float(g["exposure"].sum()), 2)
        by_type = (iss.groupby("issue_type").agg(count=("invoice_id", "count"), exposure=("exposure", "sum"))
                   .reset_index().sort_values("exposure", ascending=False))
        main = by_type.iloc[0]["issue_type"] if len(by_type) else None
        share = exp / exposure_total if exposure_total else 0.0
        rate = len(iss) / len(g)
        level = "High" if (share >= 0.12 or rate >= 0.35) else "Medium" if (share >= 0.04 or rate >= 0.15) else "Low"
        # one display name per supplier; spelling variants are kept for transparency
        vc = g["supplier_name"].value_counts()
        name = sorted(vc[vc == vc.max()].index)[0] if len(vc) else sid
        sc = status_counts(g)
        months = []
        for m, mg in g.assign(_m=g["invoice_date"].str[:7]).groupby("_m"):
            mi = mg[mg["status"] == "issue"]
            months.append({"month": m, "invoice_count": int(len(mg)), "issue_count": int(len(mi)),
                           "issue_rate": round(len(mi) / len(mg), 3), "exposure": round(float(mg["exposure"].sum()), 2)})
        suppliers.append({
            "supplier_id": sid, "supplier_name": name,
            "name_variants": sorted(vc.index.tolist()) if len(vc) > 1 else [],
            "category": g["category"].iloc[0], "gstin": sid,
            "invoice_count": int(len(g)), "issue_count": int(len(iss)),
            "affected_invoice_count": int(len(iss)),
            "issue_rate": round(rate, 3), "mismatch_rate": round(rate, 3),
            "itc_claimed": round(float(g["itc_claimed"].sum()), 2),
            "exposure": exp, "total_potential_exposure": exp, "exposure_share": round(100 * share, 1),
            "missing_count": sc["MISSING"], "duplicate_count": sc["DUPLICATE"],
            "tax_mismatch_count": sc["TAX_MISMATCH"], "amount_mismatch_count": sc["AMOUNT_MISMATCH"],
            "review_count": sc["REVIEW"], "high_value_count": int(g["high_value"].sum()),
            "main_issue": main, "main_issue_label": ISSUE_LABELS.get(main),
            "main_issue_count": int(by_type.iloc[0]["count"]) if len(by_type) else 0,
            "risk_level": level,
            "by_type": [{"type": t.issue_type, "label": ISSUE_LABELS[t.issue_type],
                         "count": int(t.count), "exposure": round(float(t.exposure), 2)}
                        for t in by_type.itertuples()],
            "status_breakdown": [{"status": s, "label": STATUS_LABELS[s], "count": sc[s],
                                  "exposure": round(float(g.loc[g["recon_status"] == s, "exposure"].sum()), 2)}
                                 for s in STATUS_ORDER if sc[s]],
            "monthly": months,
        })
    suppliers.sort(key=lambda s: s["exposure"], reverse=True)

    by_type_all = (issues.groupby("issue_type").agg(count=("invoice_id", "count"), exposure=("exposure", "sum"))
                   .reset_index().sort_values("exposure", ascending=False))
    breakdown = [{"type": t.issue_type, "label": ISSUE_LABELS[t.issue_type], "count": int(t.count),
                  "exposure": round(float(t.exposure), 2)} for t in by_type_all.itertuples()]
    status_breakdown = [{"status": s, "label": STATUS_LABELS[s], "count": int((inv["recon_status"] == s).sum()),
                         "exposure": round(float(inv.loc[inv["recon_status"] == s, "exposure"].sum()), 2)}
                        for s in ["MATCHED"] + STATUS_ORDER]
    return {"invoices": rows, "suppliers": suppliers, "summary": summary, "breakdown": breakdown,
            "status_breakdown": status_breakdown}


def exposure_ledger(result: dict, supplier_id: str | None = None) -> list[dict]:
    """Invoice-level evidence behind Potential ITC Exposure: one row per invoice that contributes to it."""
    rows = [i for i in result["invoices"] if i["exposure"] > 0 and (supplier_id is None or i["supplier_id"] == supplier_id)]
    rows.sort(key=lambda i: (-i["exposure"], i["invoice_id"]))
    return [{"invoice_id": i["invoice_id"], "invoice_no": i["invoice_no"], "supplier_id": i["supplier_id"],
             "supplier": i["supplier_name"], "supplier_name": i["supplier_name"],
             "issue_type": i["issue_type"], "issue_label": i["issue_label"],
             "purchase_taxable_value": i["taxable_value"], "purchase_gst": i["itc_claimed"],
             "portal_taxable_value": i["portal_taxable_value"], "portal_gst": i["portal_tax"],
             "reconciliation_status": i["recon_status"], "recon_status": i["recon_status"],
             "matching_confidence": i["match_confidence"], "match_confidence": i["match_confidence"],
             "exposure": i["exposure"], "reason_code": i["reason_code"], "reason": i["reason"],
             "itc_claimed": i["itc_claimed"], "portal_tax": i["portal_tax"]} for i in rows]


# Exposure-bearing reason codes, in display order. The first four are the headline issue types; the other three are the
# "needs review" link types, which also carry full-tax exposure and so must appear for the totals to reconcile.
CORE_EXPOSURE_CODES = ["TAX_MISMATCH", "AMOUNT_MISMATCH", "MISSING", "DUPLICATE"]
REVIEW_EXPOSURE_CODES = ["INVOICE_NO_MISMATCH", "GSTIN_MISMATCH", "FUZZY_REVIEW"]


def _cents(x) -> int:
    return int(round(float(x) * 100))


def exposure_aggregation(result: dict, supplier_id: str | None = None) -> dict:
    """
    Aggregates built ONLY from the invoice-level exposure ledger (positive-exposure invoices; zero-exposure invoices are excluded).
    Rounding rule: every invoice exposure is already rounded to 2 decimals (paise); sums are done in integer paise and
    converted once, so total == sum(invoices) == sum(suppliers) == sum(issue types) exactly, with no float drift.
    (The dashboard summary total is a float sum rounded once; it can differ from this by at most 1 paisa.)
    """
    ledger = exposure_ledger(result, supplier_id)
    names = {s["supplier_id"]: s["supplier_name"] for s in result["suppliers"]}
    total_c = sum(_cents(x["exposure"]) for x in ledger)

    sup = {}
    for x in ledger:
        s = sup.setdefault(x["supplier_id"], {"supplier_id": x["supplier_id"],
                                              "supplier_name": names.get(x["supplier_id"], x["supplier_name"]),
                                              "affected_invoice_count": 0, "_c": 0,
                                              **{f"{c.lower()}_count": 0 for c in CORE_EXPOSURE_CODES + REVIEW_EXPOSURE_CODES}})
        s["affected_invoice_count"] += 1
        s["_c"] += _cents(x["exposure"])
        s[f"{x['reason_code'].lower()}_count"] += 1
    by_supplier = []
    for s in sup.values():
        c = s.pop("_c")
        s["total_potential_exposure"] = c / 100
        s["exposure_share_pct"] = round(100 * c / total_c, 1) if total_c else 0.0
        by_supplier.append(s)
    by_supplier.sort(key=lambda s: (-s["total_potential_exposure"], s["supplier_id"]))

    typ = {c: {"reason_code": c, "label": STATUS_LABELS.get(c) or ISSUE_LABELS.get(c), "count": 0, "_c": 0}
           for c in CORE_EXPOSURE_CODES + REVIEW_EXPOSURE_CODES}
    for x in ledger:
        typ[x["reason_code"]]["count"] += 1
        typ[x["reason_code"]]["_c"] += _cents(x["exposure"])
    by_type = []
    for c, t in typ.items():
        if c in REVIEW_EXPOSURE_CODES and not t["count"]:
            continue                         # the four core types are always listed (even at 0); review types only when present
        cc = t.pop("_c")
        t["exposure"] = cc / 100
        t["exposure_share_pct"] = round(100 * cc / total_c, 1) if total_c else 0.0
        by_type.append(t)
    return {"total_exposure": total_c / 100, "invoice_count": len(ledger), "items": ledger,
            "by_supplier": by_supplier, "by_issue_type": by_type}
