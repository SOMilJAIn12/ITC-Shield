"""
Phase 4 - supplier reconciliation-risk prediction.

Question answered: "How likely is this supplier to have invoices that need reconciliation follow-up in the NEXT month,
judging by its history so far?"  It is NOT a fraud detector, a GST compliance classifier or an ITC-eligibility engine.

Data: the reconciliation output of the existing engine (`reconcile()["invoices"]`) - one row per purchase-register
invoice with its supplier, date and reconciliation status. No second dataset is created.

Temporal design (no leakage)
    months = sorted distinct invoice months in the dataset, e.g. Apr..Sep 2026.
    A sample is (supplier, origin m): features use ONLY invoices dated in months[:m]; the label comes from months[m].
    `compute_features` ignores any invoice outside the history months it is given, so later months cannot leak in.
    Training/evaluation always train on targets that lie strictly before the tested target month.

Target (per sample)
    1 if the supplier has at least one invoice dated in the target month that the reconciliation engine flagged
    (status != MATCHED: missing in portal, tax/amount mismatch, duplicate, or needs review), else 0.
    Suppliers with no invoice in the target month have no label and are skipped.

Model: standardised, L2-regularised logistic regression (scikit-learn). With ~20 suppliers x 6 months a boosted-tree
model would mostly memorise noise; a linear model is also directly explainable. Nothing about supplier "type" is an
input - the model sees only the numeric history features below.

Score = predicted probability (0..1) of the target above, for the month after the last month in the data.
Levels (UI categories only): HIGH >= 0.60, MEDIUM >= 0.30, LOW < 0.30.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

MIN_HISTORY_MONTHS = 2        # a sample needs at least this many months of history before its target month
MIN_DATASET_MONTHS = 5        # fewer distinct months than this -> no time-aware training/evaluation is possible
RECENT_MONTHS = 2             # window for the "recent" features
LOW_HISTORY_INVOICES = 5      # fewer past invoices than this -> score flagged as low-confidence
HIGH_THRESHOLD, MEDIUM_THRESHOLD = 0.60, 0.30
DECISION_THRESHOLD = 0.5      # only used for precision / recall / F1
LOGREG_C = 0.5

FEATURES = [
    "hist_issue_rate", "hist_missing_rate", "hist_duplicate_rate", "hist_mismatch_rate", "hist_review_rate",
    "recent_issue_count", "recent_issue_rate", "issue_rate_trend", "issue_rate_slope",
    "months_with_issue_share", "hist_invoice_count", "avg_invoice_value_lakh",
]
# features whose raw value must be > 0 to be shown as a risk signal (and which are described in plain words)
SIGNAL_FEATURES = [f for f in FEATURES if f not in ("hist_invoice_count", "avg_invoice_value_lakh")]


# ------------------------------------------------------------------ helpers
def risk_level(score: float) -> str:
    return "HIGH" if score >= HIGH_THRESHOLD else "MEDIUM" if score >= MEDIUM_THRESHOLD else "LOW"


def _month(d) -> str:
    return str(d)[:7]


def _next_month(m: str) -> str:
    return str((pd.Period(m, "M") + 1))


def dataset_months(invoices: list[dict]) -> list[str]:
    return sorted({_month(i["invoice_date"]) for i in invoices if i.get("invoice_date")})


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


# ------------------------------------------------------------------ features
def compute_features(rows: list[dict], history_months: list[str]) -> dict | None:
    """
    Features for ONE supplier from invoices dated in `history_months` only (others are ignored).
    Returns None when the supplier has no invoice in that history.
    `rows` are reconcile() invoice dicts (needs invoice_date, status, recon_status, taxable_value).
    """
    hm = list(history_months)
    hs = set(hm)
    hist = [r for r in rows if _month(r["invoice_date"]) in hs]
    if not hist:
        return None
    n = len(hist)
    issue = [r for r in hist if r["status"] == "issue"]
    cnt = lambda s: sum(1 for r in hist if r["recon_status"] == s)           # noqa: E731
    n_missing, n_dup, n_review = cnt("MISSING"), cnt("DUPLICATE"), cnt("REVIEW")
    n_mismatch = cnt("TAX_MISMATCH") + cnt("AMOUNT_MISMATCH")

    per_m = {m: [0, 0] for m in hm}                                           # month -> [invoices, flagged]
    for r in hist:
        per_m[_month(r["invoice_date"])][0] += 1
        per_m[_month(r["invoice_date"])][1] += r["status"] == "issue"
    recent_ms, earlier_ms = hm[-RECENT_MONTHS:], hm[:-RECENT_MONTHS]
    rn, ri = sum(per_m[m][0] for m in recent_ms), sum(per_m[m][1] for m in recent_ms)
    en, ei = sum(per_m[m][0] for m in earlier_ms), sum(per_m[m][1] for m in earlier_ms)
    recent_rate = ri / rn if rn else 0.0
    earlier_rate = ei / en if en else None
    trend = (recent_rate - earlier_rate) if (earlier_rate is not None and rn) else 0.0
    pts = [(k, per_m[m][1] / per_m[m][0]) for k, m in enumerate(hm) if per_m[m][0] > 0]
    slope = float(np.polyfit([p[0] for p in pts], [p[1] for p in pts], 1)[0]) if len(pts) >= 2 else 0.0
    active = [m for m in hm if per_m[m][0] > 0]
    months_with_issue = sum(1 for m in active if per_m[m][1] > 0)

    feats = {
        "hist_issue_rate": len(issue) / n,
        "hist_missing_rate": n_missing / n,
        "hist_duplicate_rate": n_dup / n,
        "hist_mismatch_rate": n_mismatch / n,
        "hist_review_rate": n_review / n,
        "recent_issue_count": float(ri),
        "recent_issue_rate": recent_rate,
        "issue_rate_trend": trend,
        "issue_rate_slope": slope,
        "months_with_issue_share": months_with_issue / len(active),
        "hist_invoice_count": float(n),
        "avg_invoice_value_lakh": float(np.mean([r["taxable_value"] for r in hist])) / 1e5,
    }
    facts = {"n_invoices": n, "n_issue": len(issue), "n_missing": n_missing, "n_duplicate": n_dup,
             "n_mismatch": n_mismatch, "n_review": n_review, "recent_n": rn, "recent_issue": ri,
             "earlier_rate": earlier_rate, "months_active": len(active), "months_with_issue": months_with_issue}
    return {"features": feats, "facts": facts}


def _by_supplier(invoices: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in invoices:
        out.setdefault(r["supplier_id"], []).append(r)
    return out


def build_panel(invoices: list[dict]) -> pd.DataFrame:
    """Supplier x origin training panel. Columns: supplier_id, origin (#history months), target_month, target_idx, y, features..."""
    months = dataset_months(invoices)
    by_sup = _by_supplier(invoices)
    recs = []
    for m in range(MIN_HISTORY_MONTHS, len(months)):
        hist_months, target = months[:m], months[m]
        for sid, rows in by_sup.items():
            tgt = [r for r in rows if _month(r["invoice_date"]) == target]
            if not tgt:
                continue                                                      # no invoices in the target month -> no label
            f = compute_features(rows, hist_months)
            if f is None:
                continue
            recs.append({"supplier_id": sid, "origin": m, "target_month": target, "target_idx": m,
                         "y": int(any(r["status"] == "issue" for r in tgt)), **f["features"]})
    cols = ["supplier_id", "origin", "target_month", "target_idx", "y"] + FEATURES
    return pd.DataFrame(recs, columns=cols)


# ------------------------------------------------------------------ model
def make_model():
    return make_pipeline(StandardScaler(), LogisticRegression(C=LOGREG_C, max_iter=1000))


def fit(panel: pd.DataFrame):
    if panel.empty or panel["y"].nunique() < 2:
        return None
    model = make_model()
    model.fit(panel[FEATURES].to_numpy(float), panel["y"].to_numpy(int))
    return model


def _metrics(y, p, thr=DECISION_THRESHOLD) -> dict:
    y, p = np.asarray(y), np.asarray(p)
    pred = (p >= thr).astype(int)
    out = {"n": int(len(y)), "positives": int(y.sum()), "base_rate": round(float(y.mean()), 3),
           "precision": round(float(precision_score(y, pred, zero_division=0)), 3),
           "recall": round(float(recall_score(y, pred, zero_division=0)), 3),
           "f1": round(float(f1_score(y, pred, zero_division=0)), 3),
           "roc_auc": None}
    if len(set(y.tolist())) == 2:
        out["roc_auc"] = round(float(roc_auc_score(y, p)), 3)
    return out


def _auc(y, s):
    return round(float(roc_auc_score(y, s)), 3) if len(set(np.asarray(y).tolist())) == 2 else None


def evaluate(panel: pd.DataFrame) -> dict:
    """
    Time-aware evaluation, computed from the data (nothing is assumed):
      final_month_holdout : train on targets before the last month, test on the last month.
      rolling_origin      : for every test month t with at least one earlier target month, train on targets < t,
                            predict t; predictions from all test months are pooled.
    Baselines use no model: the supplier's historical issue rate and its recent issue rate as the score.
    """
    if panel.empty:
        return {"status": "no_samples"}
    tmax = int(panel["target_idx"].max())
    tmin = int(panel["target_idx"].min())

    def run(test_idx_list):
        ys, ps, hb, rb, per = [], [], [], [], []
        for t in test_idx_list:
            tr, te = panel[panel["target_idx"] < t], panel[panel["target_idx"] == t]
            if te.empty:
                continue
            m = fit(tr)
            if m is None:
                continue
            p = m.predict_proba(te[FEATURES].to_numpy(float))[:, 1]
            ys += te["y"].tolist(); ps += p.tolist()
            hb += te["hist_issue_rate"].tolist(); rb += te["recent_issue_rate"].tolist()
            per.append({"test_month": te["target_month"].iloc[0], "train_samples": int(len(tr)), "test_samples": int(len(te))})
        if not ys:
            return None
        res = _metrics(ys, ps)
        res["baseline_auc_historical_issue_rate"] = _auc(ys, hb)
        res["baseline_auc_recent_issue_rate"] = _auc(ys, rb)
        res["folds"] = per
        return res

    return {
        "status": "ok",
        "decision_threshold": DECISION_THRESHOLD,
        "final_month_holdout": run([tmax]),
        "rolling_origin": run(list(range(tmin + 1, tmax + 1))),
        "note": ("Small sample (about 20 suppliers per month, 6 months): metrics have wide uncertainty and are shown "
                 "for transparency, not as a performance guarantee. Data is synthetic."),
    }


# ------------------------------------------------------------------ explanations
def _signal_text(feature: str, f: dict, facts: dict) -> str:
    n, k = facts["n_invoices"], facts["n_issue"]
    plural = lambda c, a, b: a if c == 1 else b                              # noqa: E731
    return {
        "hist_issue_rate": f"{_pct(f['hist_issue_rate'])} of past invoices were flagged ({k} of {n})",
        "hist_missing_rate": f"{facts['n_missing']} past {plural(facts['n_missing'], 'invoice', 'invoices')} not found in portal records ({_pct(f['hist_missing_rate'])})",
        "hist_duplicate_rate": f"{facts['n_duplicate']} repeated {plural(facts['n_duplicate'], 'entry', 'entries')} in the purchase register ({_pct(f['hist_duplicate_rate'])})",
        "hist_mismatch_rate": f"{facts['n_mismatch']} tax or amount {plural(facts['n_mismatch'], 'mismatch', 'mismatches')} ({_pct(f['hist_mismatch_rate'])} of invoices)",
        "hist_review_rate": f"{facts['n_review']} {plural(facts['n_review'], 'invoice', 'invoices')} needing review for invoice-number or GSTIN differences",
        "recent_issue_count": f"{int(f['recent_issue_count'])} flagged {plural(int(f['recent_issue_count']), 'invoice', 'invoices')} in the last {RECENT_MONTHS} months",
        "recent_issue_rate": f"recent issue rate is {_pct(f['recent_issue_rate'])} ({facts['recent_issue']} of {facts['recent_n']} invoices in the last {RECENT_MONTHS} months)",
        "issue_rate_trend": (f"issue rate rose from {_pct(facts['earlier_rate'])} in earlier months to {_pct(f['recent_issue_rate'])} recently"
                             if facts["earlier_rate"] is not None else "issue rate is rising"),
        "issue_rate_slope": f"issue rate is rising by about {100 * f['issue_rate_slope']:.0f} percentage points per month",
        "months_with_issue_share": f"issues appeared in {facts['months_with_issue']} of {facts['months_active']} months with invoices",
    }[feature]


def explain(model, feats: dict, facts: dict, top: int = 4) -> list[dict]:
    """
    Per-supplier key signals from the fitted linear model: contribution = coefficient x standardised value
    (the feature's push on the log-odds relative to an average supplier). Only features that push risk UP and whose
    raw value is above zero are shown; wording is generated from that supplier's own values.
    """
    scaler, lr = model.steps[0][1], model.steps[1][1]
    x = np.array([[feats[f] for f in FEATURES]], float)
    z = scaler.transform(x)[0]
    contrib = lr.coef_[0] * z
    out = []
    for name, c in sorted(zip(FEATURES, contrib), key=lambda t: -t[1]):
        if name not in SIGNAL_FEATURES or c <= 0 or feats[name] <= 0:
            continue
        out.append({"feature": name, "value": round(float(feats[name]), 4), "contribution": round(float(c), 3),
                    "text": _signal_text(name, feats, facts)})
        if len(out) == top:
            break
    return out


# ------------------------------------------------------------------ entry point
def attach_supplier_risk(result: dict) -> dict:
    """
    Adds risk fields to every supplier in `result["suppliers"]` (in place) and returns the model metadata
    (also stored as result["risk_model"]). Existing supplier fields - including the legacy rule-of-thumb
    `risk_level` - are not modified.
    New supplier fields: risk_score (0-1 or None), predicted_risk_level (LOW/MEDIUM/HIGH or None),
    key_risk_factors, risk_status, history_invoice_count, low_history.
    """
    invoices = result["invoices"]
    months = dataset_months(invoices)
    meta = {"method": "standardised logistic regression (L2, C=%s)" % LOGREG_C,
            "target": ("supplier has at least one invoice flagged by reconciliation (status other than MATCHED) "
                       "in the next month"),
            "features": FEATURES, "thresholds": {"HIGH": HIGH_THRESHOLD, "MEDIUM": MEDIUM_THRESHOLD},
            "months": months, "min_dataset_months": MIN_DATASET_MONTHS,
            "disclaimer": ("Predicts reconciliation follow-up likelihood only - not fraud, not a GST compliance "
                           "or ITC eligibility decision.")}
    for s in result["suppliers"]:
        s.update({"risk_score": None, "predicted_risk_level": None, "key_risk_factors": [],
                  "risk_status": "insufficient_dataset", "history_invoice_count": 0, "low_history": False})

    def finish(status, reason=None, **extra):
        meta.update({"status": status, "reason": reason, **extra})
        result["risk_model"] = meta
        return meta

    if len(months) < MIN_DATASET_MONTHS:
        return finish("insufficient_history",
                      f"Needs at least {MIN_DATASET_MONTHS} months of invoice history for a time-aware model; "
                      f"this dataset has {len(months)}. No score is shown.")
    panel = build_panel(invoices)
    model = fit(panel)
    if model is None:
        return finish("insufficient_labels", "Training samples do not contain both outcomes, so no model was trained.")

    by_sup = _by_supplier(invoices)
    scoring_months = months                                                   # all history -> predict the next month
    for s in result["suppliers"]:
        f = compute_features(by_sup.get(s["supplier_id"], []), scoring_months)
        if f is None:
            s["risk_status"] = "no_history"
            continue
        p = float(model.predict_proba(np.array([[f["features"][c] for c in FEATURES]], float))[0, 1])
        s.update({"risk_score": round(p, 3), "predicted_risk_level": risk_level(p),
                  "key_risk_factors": explain(model, f["features"], f["facts"]),
                  "risk_status": "scored", "history_invoice_count": f["facts"]["n_invoices"],
                  "low_history": f["facts"]["n_invoices"] < LOW_HISTORY_INVOICES})
    return finish("ok", None, scoring_period=_next_month(months[-1]),
                  history_months=f"{months[0]} to {months[-1]}",
                  trained_on={"samples": int(len(panel)), "positives": int(panel["y"].sum()),
                              "target_months": sorted(panel["target_month"].unique().tolist())},
                  evaluation=evaluate(panel))
