# ITC Shield – Project State

**Phase 1: COMPLETE. Phase 2: COMPLETE and verified.** **Phases 1, 2, 3A, 3B, 3C: COMPLETE. Phase 4 (supplier risk prediction): COMPLETE and verified.** **Phase 5 (ITC Risk Radar / Investigation Priority): COMPLETE and verified.** **Phase 6 (AI Explanation + Action), Phase 7 (Cloud deployment + production) and Phase 8 (final lock + UI polish): COMPLETE and verified (see the end of this file). The product is feature-locked; no major features or architecture changes after Phase 8.** **Post-Phase-8 enhancement (real user-data upload, demo_upload_data test files, Groq as preferred LLM provider): COMPLETE and verified — see the final section.**

## What ITC Shield does
GST/ITC risk intelligence: **Detect → Quantify → Predict → Prioritize → Act**.
It compares a purchase register with GST portal-style records, flags mismatched invoices, estimates
**Potential ITC Exposure** in ₹, ranks suppliers by exposure, and suggests follow-ups.
It does not make GST eligibility decisions and never labels anything fraud; exposure is an estimate, not a confirmed loss.

## Phase 2 – what changed (strategy B: the demo dataset is preserved)
The calibrated Phase 1 sample stays the **default demo dataset** and produces exactly the same numbers
(₹25.0L / ₹20.8L / ₹4.2L / 147 issues / 620 entries / 28 suppliers; top: Sundaram Steel ₹90.0K, Kalyani Polymers ₹66.2K,
Raghav Logistics ₹61.1K). A second, richer dataset (`realistic`) was added alongside it. The UI is unchanged apart from
three small lines on the invoice detail page (matching confidence, "why it is counted", high-value note).

### Files
Created: `backend/realistic_data.py`, `backend/tests/test_phase2.py`
Modified: `backend/reconcile.py`, `backend/data_source.py`, `backend/main.py`, `backend/requirements.txt` (+rapidfuzz),
`frontend/src/api.js` (reset takes optional dataset), `frontend/src/views/InvoiceDetail.jsx` (3 info lines), `PROJECT_STATE.md`
Untouched: `sample_data.py`, all other frontend files, `start.sh`, `frontend/src/App.test.jsx`

### Dataset structure
| | `sample` (default demo) | `realistic` (Phase 2) |
|---|---|---|
| Source | `sample_data.py`, seed 11 | `realistic_data.py`, seed 2026 |
| Period | Jul–Sep 2026 | Apr–Sep 2026 (6 months) |
| Suppliers / books / portal | 28 / 620 / 564 | 20 / 506 / 467 |
| Expected ITC / exposure | ₹25.0L / ₹4.2L | ₹59.0L / ₹5.9L (9.9%) |
| Issues | 147 | 85 |
| Extras | – | payments (372 rows), per-row ground truth, supplier archetype table |

`realistic_data.build_bundle()` returns `books, portal, payments, truth, suppliers, meta`. Supplier behaviour comes from archetypes
(not random corruption): **reliable** (7 suppliers, ~3% issues), **inconsistent** (4, ~27%, mixed types), **high_value** (3, few invoices,
~4× larger tax each), **deteriorating** (3, issue rate rises ~2% → ~50% across the six months), **late_filer** (3, recent months
often not yet on the portal = delayed reporting). Case types generated: exact, invoice-number format variation, supplier-name
variation, rounding, tax mismatch, amount mismatch, missing, delayed reporting, duplicate (sometimes keyed with different formatting),
GSTIN typo, fuzzy invoice-number slip. `truth` holds the intended case, expected status and expected exposure per row, derived from what
was written to the files and independent of `reconcile.py`. Generation is deterministic (tested) and takes well under a second.

### Reconciliation logic (`reconcile.py`)
1. **Normalise**: invoice numbers (`normalize_invoice_no`: upper-case, separators dropped, leading zeros inside digit runs dropped,
   so `INV/2026/0042` = `inv-2026-42`); supplier names (`normalize_supplier_name`: case, punctuation, `&`/and, Pvt/Private/Ltd/Limited/LLP/Co removed).
2. **Duplicates**: second and later books rows with the same GSTIN + normalised invoice number → DUPLICATE.
3. **Matching tiers** (one-to-one): (1) GSTIN + normalised invoice no. → (2) GSTIN + taxable value + date → (3) invoice no. + taxable value
   (different GSTIN) → **(4) fuzzy** (new).
4. **Statuses** (new field `recon_status`): `MATCHED`, `TAX_MISMATCH` (same taxable value, tax differs), `AMOUNT_MISMATCH` (taxable value differs),
   `MISSING`, `DUPLICATE`, `REVIEW` (tiers 2–4: portal record found under a different invoice no./GSTIN). The Phase 1 `issue_type`/`issue_label`
   fields are kept unchanged for the UI; new `issue_type` value `FUZZY_REVIEW` only appears for tier-4 links.
5. Tolerances unchanged: ₹5 on tax, ₹1 on taxable value.

### Matching logic and confidence
`match_confidence(books_row, portal_row)` → 0–100, weighted: invoice-number similarity 35%, GSTIN similarity 20%, taxable-value similarity 20%,
date proximity 15% (−12 points/day), supplier-name similarity 10% (when both sides have a name). Similarity uses RapidFuzz (`ratio`, `token_sort_ratio`), with a
`difflib` fallback. Exact tier-1 links leave out the value term (a different amount doesn't make it a different invoice). Labels: ≥95 *Strong match*,
85–94.9 *Probable match*, below 85 *Requires review*. Tier-4 gates (all must pass): invoice-no. similarity ≥75, value similarity ≥90, date within 7 days,
confidence ≥70, same GSTIN or same normalised supplier name. Exposed per invoice as `match_method`, `match_confidence`, `match_confidence_label`.
Nothing is hard-coded; the demo sample's tier-4 stage finds nothing, so its numbers don't move.

### Exposure logic (unchanged numerically)
MISSING / DUPLICATE / REVIEW → full claimed tax. TAX_/AMOUNT_MISMATCH → claimed tax minus portal tax, only if claimed is higher.
Reconciled ITC = Expected ITC − exposure. Every invoice now carries `exposure_reason` (plain-English why it is counted), and
`GET /api/exposure[?supplier_id=]` returns the ledger (invoice, supplier, issue, status, claimed tax, portal tax, exposure, reason, confidence);
its total equals the dashboard exposure (tested). `high_value` flags invoices with claimed ITC ≥ ₹50,000 as a prompt to look closer; it changes no status or amount.

### Supplier aggregation
Existing fields kept; added: `affected_invoice_count`, `mismatch_rate`, `total_potential_exposure`, `missing_count`, `duplicate_count`,
`tax_mismatch_count`, `amount_mismatch_count`, `review_count`, `high_value_count`, `status_breakdown`, `name_variants`, and `monthly`
(invoices/issues/rate/exposure per month – the basis for Phase 3 risk prediction). Supplier totals equal invoice-level totals (tested on both datasets).

## API
`GET /api/health` · `POST /api/upload` · `POST /api/reset[?dataset=sample|realistic]` (default `sample`) · `POST /api/analyze` · `GET /api/dashboard` ·
`GET /api/suppliers` · `GET /api/suppliers/{id}` · `GET /api/invoices?status=&issue_type=&supplier_id=&q=` · `GET /api/invoices/{id}` ·
**`GET /api/exposure[?supplier_id=]`** (new). Results endpoints return 409 until `/api/analyze` has run (reset clears results).
There is no dataset switcher in the UI; to demo the realistic dataset call `curl -X POST "localhost:8000/api/reset?dataset=realistic"` and refresh the page.

## Tests performed (all passing)
- `backend/tests`: 33 tests (2 Phase 1 + 31 new): exact match, format variation, rounding tolerance, tax mismatch, amount mismatch, missing, duplicate
  (incl. formatting variants), supplier-name variation, GSTIN typo → review, fuzzy link with computed confidence, confidence monotonicity and label thresholds,
  fuzzy value/date gates, portal-tax-higher adds no exposure, high-value flag, exposure ledger = total, status breakdown = total, supplier aggregation = invoice aggregation
  (both datasets), demo numbers unchanged, realistic dataset deterministic, **engine agrees with ground truth on all 506 invoices (status and exposure)**, archetype behaviour
  (reliable < inconsistent, high-value fewer invoices but ≥3× exposure per issue, deteriorating suppliers' late-period rate > early), forbidden-wording scan, API round trip for both datasets.
- Mutation check: loosening the fuzzy gates or counting negative gaps makes tests fail (so those tests do guard the logic).
- `frontend`: `npm test` click-flow (2 tests) passes against the live backend, unchanged from Phase 1.
- Headless Chromium walk-through on **both** datasets: Analyze Data → Dashboard → top supplier → affected invoices → invoice detail; plus Reconciliation → "Probable match - review" → invoice
  with 88% confidence. No page errors; the only console errors are the blocked Google Fonts request (sandbox offline) and a missing favicon, both pre-existing.

## How to run
```bash
# Terminal 1 – backend (http://127.0.0.1:8000)
cd backend && pip install -r requirements.txt && python3 -m uvicorn main:app --port 8000
# Terminal 2 – frontend (http://127.0.0.1:5173)
cd frontend && npm install && npm run dev
# or: ./start.sh
```
Tests: `cd backend && python3 -m pytest -q tests` · `cd frontend && npm test` (needs backend on :8000, which it resets to the sample dataset).
Reset for a clean demo take: refresh the page, or `curl -X POST localhost:8000/api/reset`.

## Known limitations
- State is in memory only; no database, no auth, single user. Upload endpoint works (CSV round trip) but there is no upload UI.
- Both datasets are synthetic with fictional suppliers. Payments are generated and returned by `build_bundle()` but are **not yet used** by the engine (informational only).
- Invoice normalisation can't recover numbers keyed with separators removed entirely (`AIF/26-27/0123` vs `AIF26270123`); such rows fall to tier 2/4 and surface as REVIEW, not MATCHED.
- Fuzzy matching only searches leftover portal records of the same GSTIN/normalised name; no cross-supplier or many-to-one matching. Weights/thresholds are hand-set, not learned.
- Portal-only records (in portal, not in books) are not reported. No credit notes, reverse charge, Section 16(4) time limits, blocked credits, or multi-period filing logic.
- Priority thresholds (High ≥ ₹6,000 per invoice) are tuned to the demo; the realistic dataset's larger invoices skew toward High.
- "Delayed reporting" is modelled as missing on the portal for recent months; the data has no filing-period field.
- REVIEW and invoice-number/GSTIN issues count the full claimed tax as exposure (conservative estimate, not a legal position).
- Dashboard "breakdown" still uses Phase 1 issue types; the new `status_breakdown` is in the API but not displayed.
- Fonts load from Google Fonts (system fallback offline); frontend bundle unsplit (~590 kB).

## Exact next phase (Phase 3 – not started)
Supplier-risk scoring on the Phase 2 `realistic` dataset, using the existing `monthly` supplier history and archetype ground truth:
1. A transparent, rule-based supplier risk score (issue rate, trend across months, missing/duplicate mix, exposure concentration) with a visible factor breakdown and a backtest against the archetype labels.
2. Only after that works, an optional simple trend/ML model compared against the rule-based baseline.
3. Surface the score on the Suppliers/SupplierDetail pages and add a dataset switcher (sample / realistic) in the UI.
Not included: LLM features, auth, database.

## Phase 3A – COMPLETED (invoice-level traceability of Potential ITC Exposure)
- **Completed:** every invoice contributing to exposure now exposes invoice_id, supplier, purchase taxable value, purchase GST, portal taxable value, portal GST,
  reconciliation status, matching confidence, exposure, deterministic `reason_code` and a human-readable `reason` generated from that invoice's own values (₹, Indian grouping).
  Reason codes: `TAX_MISMATCH`, `AMOUNT_MISMATCH`, `MISSING`, `DUPLICATE`; review links use `INVOICE_NO_MISMATCH`, `GSTIN_MISMATCH`, `FUZZY_REVIEW`.
  Exposure formula, tolerances and demo numbers unchanged (₹25.0L / ₹20.8L / ₹4.2L / 147 issues). `GET /api/exposure` items carry the new fields (old keys kept).
- **Files changed:** `backend/reconcile.py` (new `_inr`, `_reason_code`, `_trace_reason`; new invoice fields `portal_taxable_value`, `reason_code`, `reason`; extended `exposure_ledger`), `PROJECT_STATE.md`.
- **Tests added:** `backend/tests/test_phase3a.py` (6 tests): correct exposure amount (₹18,000 vs ₹16,200 → ₹1,800); correct reason code/text for tax mismatch, amount mismatch, missing, duplicate;
  matched invoice has zero exposure and no reason; portal-higher adds no exposure; exposure and wording derived from input data (varied inputs); demo numbers unchanged and ledger total = dashboard exposure on every invoice.
- **Current status:** `cd backend && python3 -m pytest -q tests` → 39 passed (33 earlier + 6 new). Frontend untouched.
- **Next step:** Phase 3B.

## Phase 3B – COMPLETED (exposure aggregation)
- **Completed:** new `exposure_aggregation(result, supplier_id=None)` builds total, supplier and issue-type aggregates strictly from the Phase 3A invoice-level ledger
  (positive-exposure invoices only; zero-exposure invoices excluded). Reconciliation, matching, exposure formula and datasets untouched.
  - Total business exposure = sum of positive invoice exposures.
  - Supplier: supplier_id/name, `affected_invoice_count`, `total_potential_exposure`, `tax_mismatch_count`, `amount_mismatch_count`, `missing_count`, `duplicate_count`
    (plus `invoice_no_mismatch_count`, `gstin_mismatch_count`, `fuzzy_review_count` so counts add up to `affected_invoice_count`), `exposure_share_pct`.
  - Issue type: TAX_MISMATCH, AMOUNT_MISMATCH, MISSING, DUPLICATE (always listed) plus INVOICE_NO_MISMATCH / GSTIN_MISMATCH / FUZZY_REVIEW when present; count, exposure, share.
  - **Rounding rule (documented in code):** invoice exposures are already 2-decimal; sums use integer paise, so ledger = supplier = issue-type totals exactly. The dashboard
    summary (float sum rounded once) may differ from them by at most ₹0.01.
- **Files changed:** `backend/reconcile.py` (appended `exposure_aggregation`, `CORE/REVIEW_EXPOSURE_CODES`, `_cents`; no existing logic edited), `backend/main.py` (`/api/exposure` only; import changed), `PROJECT_STATE.md`.
- **API changes:** `GET /api/exposure[?supplier_id=]` keeps `potential_exposure`, `count`, `items`; adds `total_exposure`, `by_supplier`, `by_issue_type`. `supplier_id` scopes all aggregates. No new endpoints. Frontend untouched.
- **Tests added:** `backend/tests/test_phase3b.py` (8 tests, sample + realistic datasets where parametrised): total = sum of raw invoice rows and = dashboard; sum(suppliers) = total; issue types reconcile with ledger per code;
  zero-exposure excluded; supplier counts add up and match an independent recompute; hand-computed two-supplier case (₹1,800 + ₹1,800 + ₹9,000 + ₹18,000 = ₹30,600 for one supplier, ₹32,400 total); API payload, backwards-compatible keys, supplier filter; demo numbers unchanged.
- **Test results:** `cd backend && python3 -m pytest -q tests` → 52 passed (39 earlier + 13 from the 8 test functions incl. parametrisation). Demo: ₹25.0L / ₹20.8L / ₹4.2L (₹4,21,027.68) / 147 issues; 147 exposed invoices, 26 suppliers with exposure;
  by type: Missing ₹1,32,936.30, Duplicate ₹82,082.70, Invoice-no mismatch ₹79,809.48, Amount mismatch ₹57,914.76, GSTIN mismatch ₹43,127.10, Tax mismatch ₹25,157.34 — all sum to the total.
- **Next phase:** Phase 3C.

## Phase 3C – COMPLETED (exposure intelligence connected to the UI)
- **Completed:** the existing React UI now reads the Phase 3A/3B backend fields. No redesign, no new dependencies, no backend or calculation changes. React computes no exposure values.
  - **Dashboard:** headline metrics still come from `/api/dashboard` summary. `Analyze Data` now also fetches `/api/exposure` and stores it on the dashboard object.
    "Top Suppliers Driving Exposure" uses `by_supplier` (name, `total_potential_exposure`, `affected_invoice_count`); the risk badge is joined from the existing dashboard `top_suppliers` by supplier id.
    The "What was found" chart became **"Exposure by issue type"** using `by_issue_type` (bars ordered largest-first for display; the largest is highlighted).
  - **Invoice detail:** stat is now labelled "Potential ITC Exposure" and shows the exact amount; added "Reconciliation status", "Matching confidence" (or "not available (no portal record linked)"),
    and **"Why this invoice is counted"** showing the backend `reason` verbatim (zero-exposure issues show "Why this invoice is flagged"). The Phase 2 `exposure_reason` line was replaced by this.
  - **Reconciliation page:** left unchanged (table already has 7 columns and shows result + exposure; the list endpoint does not carry `reason`). Filters verified working.
  - **Suppliers page / Supplier detail:** unchanged.
- **Frontend files changed:** `frontend/src/api.js` (+`api.exposure`), `frontend/src/views/Dashboard.jsx`, `frontend/src/views/InvoiceDetail.jsx`, `frontend/src/styles.css` (+1 rule `.why-h`),
  `frontend/src/App.test.jsx` (one assertion: label "Potential ITC at Risk" -> "Potential ITC Exposure").
- **Backend files changed:** none. **API changes:** none (uses existing `/api/exposure` from Phase 3B).
- **Tests run / results:** backend `python3 -m pytest -q tests` -> **52 passed**; frontend `npm test` (live backend) -> **2 passed**; `npm run build` succeeds.
- **Browser flow (real backend :8000 + real Vite dev server :5173, headless Chromium):** Analyze Data -> Dashboard -> top supplier -> supplier details -> first affected invoice -> exposure explanation -> Reconciliation (Issues -> All) on both datasets.
  No page errors or relevant console errors. UI top-5 suppliers, amounts and invoice counts equal the API's `by_supplier`; chart labels/counts equal `by_issue_type`.
- **Dataset results:**
  - Default demo (`sample`): **₹25.0L Expected / ₹20.8L Reconciled / ₹4.2L Potential ITC Exposure / 147 issues** (unchanged); Reconciliation 147 -> 620 records; top supplier Sundaram Steel Traders ₹90.0K (19 invoices).
  - `realistic` (via `POST /api/reset?dataset=realistic`, then reload + Analyze): **₹59.0L / ₹53.1L / ₹5.9L / 85 issues**; Reconciliation 85 -> 506 records; top supplier Lotus Heavy Machinery ₹1.4L (4 invoices). Values change with the backend dataset as expected.
  - Backend left reset to `sample`.
- **Known limitations:** there is still no dataset switcher in the UI (use the curl reset, then reload). Dashboard top-supplier risk badges are the existing Phase 1/2 rule-of-thumb levels, not Phase 4 supplier risk.
  The Reconciliation table does not show the reason text (open the invoice). Some older explanation text on the invoice page still uses "Rs" while the new backend `reason` uses "₹". Chart bars animate in on load (screenshots taken early show empty bars).
  Bundle size unchanged (~590 kB, unsplit). Fonts load from Google Fonts (system fallback offline).
- **Next phase:** Phase 4 Supplier Risk (not started).

## Phase 4 – COMPLETED (supplier reconciliation-risk prediction)
**What it is / is not:** predicts how likely a supplier is to have invoices needing reconciliation follow-up *next month*, from its history. It is not a fraud detector, GST compliance classifier or ITC-eligibility engine; no output uses those words.

- **Status:** working on the `realistic` dataset (6 months). The default demo (`sample`, 3 months) has too little history, so **no score is produced for it** (status `insufficient_history`; nothing invented). Demo numbers unchanged: ₹25.0L / ₹20.8L / ₹4.2L / 147 issues.
- **Data used:** the existing reconciliation output (`reconcile()["invoices"]`: supplier, date, status). No second dataset; the generator's archetype labels are never an input (a test checks the module does not reference them).
- **Target:** per (supplier, month): `1` if the supplier has at least one invoice dated in the *target month* that the engine flagged (status other than MATCHED: missing, tax/amount mismatch, duplicate, needs review), else `0`. Suppliers with no invoice that month are skipped.
- **Features (12, all from earlier months only):** `hist_issue_rate`, `hist_missing_rate`, `hist_duplicate_rate`, `hist_mismatch_rate` (tax+amount), `hist_review_rate`, `recent_issue_count` and `recent_issue_rate` (last 2 months), `issue_rate_trend` (recent minus earlier rate), `issue_rate_slope` (per-month slope of monthly issue rate), `months_with_issue_share`, `hist_invoice_count`, `avg_invoice_value_lakh`.
  Not separable from the data: *delayed reporting* (no filing-period field; it looks like "missing"), and *unresolved/corrected* status (no resolution data; every flagged invoice is treated as open, review rate = needs-review rows).
- **Temporal design / leakage control:** a sample at origin *m* uses only invoices in `months[:m]` (the function ignores anything else) and is labelled from `months[m]`; at least 2 history months. Evaluation always trains on target months strictly before the tested month. Tests change/append later-month data and show features do not move; a mutation check (removing the history filter) makes them fail.
- **Temporal split:** primary = train on targets Jun, Jul, Aug (60 samples) → test Sep (20); plus rolling-origin (train < t, test t, for Jul, Aug, Sep; 60 pooled predictions).
- **Model:** standardised, L2 logistic regression (scikit-learn, C=0.5, fixed in advance, not tuned on test data). XGBoost/LightGBM are not installed, and with ~80 rows a boosted tree would mostly fit noise; the linear model is also directly explainable. Final model trains on all 80 labelled samples (targets Jun–Sep, 34 positive) and scores every supplier from Apr–Sep history for **Oct 2026**.
- **Actual evaluation (calculated, synthetic data, small samples):**

  | | n (positives, base rate) | precision | recall | F1 | ROC-AUC | AUC of baseline: historical issue rate | AUC of baseline: recent issue rate |
  |---|---|---|---|---|---|---|---|
  | Final-month holdout (Sep) | 20 (8, 0.40) | 0.500 | 1.000 | 0.667 | 0.812 | 0.750 | 0.938 |
  | Rolling origin (Jul–Sep pooled) | 60 (27, 0.45) | 0.591 | 0.481 | 0.531 | 0.659 | 0.687 | 0.813 |

  Precision/recall/F1 use a 0.5 cut-off. **Honest reading:** the model ranks better than chance, but it does **not** beat the simple "recent issue rate" baseline on either test, and is only on par with the historical-rate baseline. With 20 suppliers per month these figures have wide uncertainty (one supplier moves AUC by several points). Diagnostic only (not used to choose C): rolling AUC 0.70 / 0.69 / 0.66 / 0.64 for C = 0.05 / 0.1 / 0.5 / 2. Scores for strongly deteriorating suppliers saturate near 100% (few training rows), so treat the absolute percentages as ordinal.
  A structural effect also limits accuracy: in the generator, late-filer suppliers are mostly "missing" in Aug/Sep only, which their earlier history does not predict.
- **Risk score methodology:** `risk_score` = predicted probability (0–1) from `predict_proba`. Levels are UI categories only: **HIGH ≥ 0.60, MEDIUM ≥ 0.30, LOW < 0.30**. Suppliers with < 5 past invoices get `low_history = true` (UI shows a caution); suppliers with no history get `risk_status = "no_history"` and no score.
- **Explainability:** per supplier, contribution = coefficient × standardised feature value (push on log-odds vs an average supplier). Up to 4 features that push risk up *and* have raw value > 0 are returned as `key_risk_factors` (`feature`, `value`, `contribution`, `text`); the text is generated from that supplier's own counts/rates (e.g. "recent issue rate is 88% (7 of 8 invoices in the last 2 months)"). No LLM.
- **API:** no new endpoints. `POST /api/analyze` now also attaches risk to each supplier (failure never blocks analysis). `GET /api/suppliers` and `GET /api/suppliers/{id}` and the dashboard `top_suppliers` suppliers gain: `risk_score`, `predicted_risk_level` (LOW/MEDIUM/HIGH), `key_risk_factors`, `risk_status`, `history_invoice_count`, `low_history`; `GET /api/suppliers` also returns `risk_model` (status, reason, method, target, features, thresholds, scoring_period, trained_on, evaluation). Existing supplier fields are unchanged.
  **Naming note:** the legacy rule-of-thumb `risk_level` ("High/Medium/Low") is kept as is (dashboard follow-up priority and the demo fallback use it); the model's level is `predicted_risk_level`.
- **Frontend (style unchanged):** new `ModelRisk` component shows "79% HIGH" when a model score exists, otherwise falls back to the existing "High risk" badge (default demo). Used in Dashboard top suppliers, Suppliers table (column becomes "Predicted risk" + one explanatory footnote) and Supplier detail header. Supplier detail gets a "Predicted reconciliation risk" panel listing key risk factors (or a "needs at least 5 months of history" note on the demo).
- **Files changed/created:** created `backend/supplier_risk.py`, `backend/tests/test_phase4.py`; modified `backend/main.py` (analyze + `/api/suppliers`), `backend/requirements.txt`, `frontend/src/components.jsx`, `frontend/src/views/Dashboard.jsx`, `frontend/src/views/Suppliers.jsx`, `frontend/src/views/SupplierDetail.jsx`, `frontend/src/styles.css`, `frontend/src/App.test.jsx` (+1 test), `PROJECT_STATE.md`.
  `reconcile.py`, `sample_data.py`, `realistic_data.py` untouched.
- **Dependencies added:** `scikit-learn>=1.3` (backend/requirements.txt). No frontend dependencies.
- **Tests:** backend 71 passed (52 earlier + 19 in `test_phase4.py`: hand-checked features; no-leakage (later months ignored, target outcome changes label not features, panel rows recomputed from prior months, folds train strictly before test); model trains and responds to feature values on a synthetic panel; single-class labels; prediction fields and 0–1 range, level thresholds, level spread; supplier→prediction mapping recomputed per supplier; determinism and no archetype use; explanations match actual values; forbidden-wording scan; edge cases (no history, one invoice, empty data, single month); demo has no score and unchanged numbers; legacy supplier fields untouched; API on both datasets).
  Frontend `npm test` 3 passed (2 earlier + realistic risk flow); `npm run build` succeeds.
- **Browser flow (real backend + Vite, headless Chromium, both datasets):** Dashboard → top supplier → supplier details → invoice → Suppliers page. Default demo: ₹25.0L / ₹20.8L / ₹4.2L / 147, existing "High/Medium risk" badges, "needs 5 months" note, no page errors. Realistic: ₹59.0L / ₹53.1L / ₹5.9L / 85; top suppliers show e.g. Lotus Heavy Machinery ₹1.4L · 79% · HIGH, Harbor Chemicals ₹58.9K · 38% · MEDIUM; supplier panel lists factors; no page errors. Backend left reset to `sample`.
- **Known limitations:** tiny, synthetic, short history (20 suppliers × 6 months; ~80 training rows); model does not beat the recent-issue-rate baseline; metrics are noisy; scores can saturate near 0/100%; reconciliation outcomes come from a single final snapshot (a real deployment would use status as of each origin date); no delayed-reporting or resolution fields; the default demo and uploads with < 5 months get no prediction; model is retrained on each analyze (fast, deterministic) and not persisted; no calibration; levels are fixed cut-offs; no dataset switcher in the UI (curl reset + reload).
- **Exact next phase:** Phase 5 – not started and not defined here. Suggested (needs your decision): a UI dataset switcher; checking whether a simpler rule/baseline or calibrated model should replace the logistic regression; and richer history (filing-period and resolution fields) before any more model complexity.

## Phase 5 – COMPLETED (ITC Risk Radar / Investigation Priority)
**What it is / is not:** a deterministic ranking of *which supplier or invoice to investigate first*, built only from existing backend outputs (reconciliation, the exposure ledger, and the Phase 4 supplier risk score when one exists). No new ML model, no LLM, no randomness. The score is a 0-100 **ranking aid, not a probability** and not a GST determination. Phase 4 model, default demo dataset and all earlier numbers are untouched.

### Priority formula (code: `backend/priority.py`, documented in its docstring and returned by the API as `methodology`)
**Supplier Investigation Priority** = `100 x (0.50 x Exposure + 0.30 x Supplier Risk + 0.20 x Evidence)`, each signal 0-1:
- **Exposure** = supplier Potential ITC Exposure / max(largest supplier exposure in this run, ₹10,000). Relative to the biggest supplier, so it is a ranking signal, not an absolute rupee judgement. The floor stops a run with only tiny exposures from stretching its top supplier to 1.0.
- **Supplier Risk** = the Phase 4 `risk_score` (0-1), used as is.
- **Evidence** = `0.7 x severity + 0.3 x recurrence`. *Severity* = exposure-weighted average of the severity table over the supplier's exposed invoices. *Recurrence* = `min(1, affected invoices / 5)`.
- **No Supplier Risk score (default demo, short histories, uploads):** nothing is imputed. The weights of the signals that exist are re-scaled to sum to 1 (exposure 0.714, evidence 0.286) and the API/UI say Supplier Risk is not available.
- **No affected invoices:** exposure and evidence are 0, so the score can only come from risk (max 30) and the level is always LOW. Such suppliers are ranked but not listed on the radar.
- **Severity table (judgement-based defaults, not learned):** MISSING 1.0, DUPLICATE 1.0, TAX_MISMATCH 0.7, AMOUNT_MISMATCH 0.7, INVOICE_NO_MISMATCH 0.5, GSTIN_MISMATCH 0.5, FUZZY_REVIEW 0.4. Rationale: higher where the records show the problem more completely (whole claim unsupported or repeated) and lower where a portal record exists and the gap is partial or probably a keying difference.

**Invoice Investigation Priority** (invoices with exposure > 0) = `100 x (0.70 x invoice exposure / max(largest invoice exposure, ₹5,000) + 0.30 x severity)`.

**Levels (both):** HIGH >= 60, MEDIUM >= 35, otherwise LOW. Thresholds were fixed when the engine was written and not adjusted after looking at either dataset. Ties break by higher exposure, then id, so output is fully reproducible.

**Why risk alone cannot win:** risk carries 30% and exposure 50%. A supplier with ₹400 exposure and 99% risk scores far below one with ₹60,000 exposure and 30% risk (tested). Risk with no exposure is capped at 30 (LOW).

### Resulting rankings (calculated)
- Default demo (no risk scores): Sundaram Steel Traders ₹90.0K 99.1 HIGH; Kalyani Polymers ₹66.2K 78.7 HIGH; Raghav Logistics ₹61.1K 72.0 HIGH; then Delta Precision, Mehta Packaging (MEDIUM). Suppliers with exposure: 3 HIGH / 5 MEDIUM / 18 LOW.
- Realistic: Lotus Heavy Machinery ₹1.4L, 79% risk, 4 invoices, 90.9 HIGH; Pinnacle Polymers ₹54.4K, 97%, 64.5 HIGH; Sterling Facility ₹38.3K, 100%, 64.0 HIGH; Radiant Paints ₹34.1K, 99%, 62.0 HIGH; Quest Components ₹31.1K, 100%, 59.7 MEDIUM. Suppliers with exposure: 4 HIGH / 8 MEDIUM / 5 LOW. Harbor Chemicals (₹58.9K, 38% risk) ranks 8th and Meridian Power (₹51.3K, one invoice, 15% risk) lower, so the order is neither pure-exposure nor pure-risk (tested).
- Invoice level: demo 4 HIGH / 44 MEDIUM / 52 LOW; realistic 4 / 26 / 55.

### API / schema changes (all additive; no existing field changed or removed)
- `POST /api/analyze` now also runs `attach_priority` after supplier risk.
- **New `GET /api/priority[?limit=10]`** (1-100, 409 before analysis): `methodology`, `risk_available`, `supplier_count`, `suppliers` (ranked, exposure > 0), `invoice_count`, `invoices` (ranked). Supplier item: `supplier_id, supplier_name, rank, investigation_priority, priority_level, potential_exposure, supplier_risk, supplier_risk_level, risk_available, affected_invoice_count, top_issue{code,label,count,exposure}, components[{key,label,value,weight,contribution}], signals[{key,value,text,source?}]`.
- Each supplier (in `/api/suppliers`, `/api/suppliers/{id}`) gains `investigation` (score, level, rank, exposure, risk, top issue, components, signals). Each invoice gains `investigation` (null when exposure is 0; visible in `/api/invoices/{id}`). Invoice list items gain `investigation_priority` and `investigation_level`.
- Signal text is generated from structured data: exposure and share, affected/total invoices, largest issue source, high-value count **among the affected invoices**, and the Phase 4 `key_risk_factors` text (sentence-cased, reused as is). No LLM.
- Naming note: the older invoice field `priority` (High/Medium/Low from fixed exposure cut-offs) and supplier `risk_level` are kept for backwards compatibility. The new concept is `investigation*`.

### Frontend
- **Dashboard:** new **"ITC Risk Radar"** panel ("Where should you investigate first?") directly under the unchanged headline hero (Potential ITC Exposure / Expected / Reconciled / Issues). Top 5 suppliers: rank, name, affected invoices, top issue, exposure, "79% risk" (or "risk n/a"), HIGH/MEDIUM/LOW badge; row opens Supplier Detail. A collapsible "How is Investigation Priority calculated?" shows the backend's formula text. On the demo a note explains Supplier Risk is unavailable. Existing panels are unchanged.
- **Supplier Detail:** stat row adds **Supplier Risk** ("Not available" on the demo) and **Investigation Priority** (badge, score, rank); new **"Why is this supplier prioritized?"** panel lists the backend signals and the score decomposition. The invoice table column is now "Investigation priority".
- **Invoice Detail:** one line with the invoice's Investigation Priority, score and rank.
- **Dataset selector (added; small, low risk):** a "Data" dropdown in the top bar (Demo Dataset / Realistic Dataset). It calls the existing `POST /api/reset?dataset=`, clears the session and returns to the pre-analysis dashboard. Initial value comes from `/api/health`. Supersedes the earlier "no dataset switcher" notes. If the backend source is an upload, the dropdown shows Demo.
- No priority maths in React; it only renders backend values.

### Files
Created: `backend/priority.py`, `backend/tests/test_phase5.py`.
Modified: `backend/main.py` (import, analyze hook, `/api/priority`, list item fields), `frontend/src/api.js` (+`priority`), `frontend/src/views/Dashboard.jsx` (Radar), `frontend/src/views/SupplierDetail.jsx`, `frontend/src/views/InvoiceDetail.jsx`, `frontend/src/App.jsx` (selector), `frontend/src/styles.css` (appended rules), `frontend/src/App.test.jsx` (+3 tests), `PROJECT_STATE.md`.
Untouched: `reconcile.py`, `supplier_risk.py`, `sample_data.py`, `realistic_data.py`, `data_source.py`, `requirements.txt`. No new dependencies.

### Tests
- **Backend: 98 passed** (71 earlier + 27 new in `test_phase5.py`): supplier and invoice formulas against hand-computed values; component contributions sum to the score; high exposure + high risk = HIGH and rank 1; high risk + tiny exposure does not outrank substantial exposure and cannot be HIGH; risk alone is capped LOW; missing risk score (weights re-scale, no imputation, note in signals, `risk_available` false); supplier with no affected invoices (score, LOW, not on radar, null invoice investigation); level thresholds; severity table complete; determinism across two full runs on both datasets; `attach_priority` idempotent; **rankings recomputed independently from `/api/exposure` + `/api/suppliers` equal the API on both datasets**; top issue and signal text match ledger data; ranking differs from pure-risk and pure-exposure order; demo numbers unchanged and no risk invented; realistic full workflow (Lotus first, Phase 4 factors reused verbatim); invoice priority from the ledger; supplier endpoints carry `investigation`; 409 before analysis and limit validation; high-value signal counts only affected invoices; forbidden-wording scan (fraud, guaranteed, GST violation, non-compliant).
- **Mutation checks (all caught):** ranking by risk only (7 tests fail), risk weight 0.9 (6 fail), imputing 0.5 for a missing risk score (2 fail).
- **Frontend: 6 passed** (3 earlier + radar on demo, radar on realistic in the same order as the API, dataset selector). `npm run build` succeeds.

### Browser verification (real backend + Vite dev server, headless Chromium, both datasets)
Reset to dataset -> Analyze Data -> Dashboard -> Radar -> top supplier -> "Why is this supplier prioritized?" -> first affected invoice -> invoice priority line. **Demo:** ₹4.2L / ₹25.0L / ₹20.8L / 147 unchanged, Radar shows Sundaram Steel Traders ₹90.0K "risk n/a" HIGH, supplier page shows "Supplier Risk: Not available". **Realistic:** ₹5.9L / ₹59.0L / ₹53.1L / 85, Radar shows Lotus Heavy Machinery ₹1.4L 79% risk HIGH, supplier page lists exposure, 4 affected invoices, high-value count, recent issue rate and trend. Dropdown switched both ways in the browser. 390 px mobile layout checked. No page errors; the only failing request is the Google Fonts stylesheet (sandbox offline, pre-existing). Browser testing found one real bug (a supplier-wide high-value count shown as "affected"), fixed and covered by a test. Backend left reset to `sample`.

### Known limitations
- Weights (0.5/0.3/0.2), severity values, thresholds (60/35) and the floors are hand-set and **not validated against real investigation outcomes**; they are explainable defaults, not learned. Changing them changes rankings.
- Exposure is normalised to the largest supplier in the run, so scores are comparable within a run, not across runs or datasets.
- Supplier Risk inherits Phase 4's limits (small synthetic data, does not beat the recent-issue-rate baseline, scores saturate near 100% for deteriorating suppliers). Several realistic suppliers show 97-100% risk, so the risk term separates them less than their raw percentages suggest.
- Severity ranks MISSING above TAX_MISMATCH partly because mismatches already count a smaller exposure. "Missing" can also mean delayed reporting, which the data cannot distinguish.
- Invoice priority uses exposure and severity only (supplier risk is shown as context, not scored), and invoice-level evidence is limited to what reconciliation provides.
- The Dashboard "Top Suppliers Driving Exposure" and "Suggested follow-ups" panels still use the Phase 1-4 exposure ordering and legacy badges, so their order can differ from the Radar by design.
- The Reconciliation and Suppliers tables do not show Investigation Priority yet.
- Uploaded data works through the API only; the dataset selector offers the two built-in datasets.

### Next recommended phase (not started)
Phase 6, to be decided by you. Suggested: (1) an action/workflow layer on top of the Radar (investigation status per supplier or invoice, notes, a follow-up list that can be exported); (2) show Investigation Priority in the Reconciliation and Suppliers tables; (3) before more modelling, add filing-period and resolution fields so delayed reporting and "already fixed" can be separated and the severity and weight choices can be checked against real outcomes. Do not replace the Phase 4 model without a fair comparison against the recent-issue-rate baseline.

## Phase 6 – COMPLETED (AI Explanation + Action)
**What it is / is not:** an explanation and communication layer on top of the existing results. The backend stays the only source of truth for
reconciliation, exposure, supplier risk and investigation priority; the LLM never calculates any of them and is never required for the app to work.

- **Backend (`backend/ai_brief.py`, new):**
  - `build_supplier_evidence(result, supplier_id)` / `build_invoice_evidence(result, invoice_id)` build a structured evidence packet verbatim from existing
    outputs: supplier (name, GSTIN, category, name variants), period covered, invoice/affected counts, ITC claimed, Potential ITC Exposure and share,
    issue types (code, label, count, exposure), high-value count, Supplier Risk (score, level, signals, or an explicit "not available" note),
    Investigation Priority (score, level, rank, signals, components) and up to 8 affected invoices (purchase vs portal taxable value / GST, portal
    invoice no./GSTIN, reconciliation status, reason code + reason text, match method/confidence, exposure, priority).
  - `fallback_brief(evidence)`: deterministic, template-based brief (why flagged / what to verify / draft supplier e-mail) built only from the packet.
    Per-issue-type verification steps; the draft lists invoice numbers, dates, taxable values, GST and the portal figure and asks the supplier to confirm.
    Always ends with "verification with the supplier is needed before any ITC decision".
  - `generate_brief(evidence, llm_call=None)`: if a provider key is configured, sends the system prompt (rules: never calculate, never invent, never claim
    fraud/violation/non-compliance/guaranteed loss, use the three product terms, JSON only) plus the evidence JSON; parses JSON (bare, fenced or wrapped);
    `validate_brief` rejects anything that is not `{why_flagged[], what_to_verify[], draft_followup}`, any **number not present in the evidence**
    (amounts in ₹ Indian grouping, rounded, lakh/K/crore forms, percentages, scores; invoice numbers, GSTINs and dates are treated as identifiers; integers ≤ 31 pass as counts)
    and any **forbidden wording** (fraud, violation, non-compliant, guaranteed loss, evasion, penalty …). Any exception, HTTP error, timeout or rejection → fallback with `fallback_reason`.
  - Providers via `httpx` (already a dependency, no SDKs): Anthropic Messages API or any OpenAI-compatible chat endpoint (`LLM_BASE_URL`). Config from env only
    (`LLM_PROVIDER` auto|anthropic|openai|none, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `LLM_MODEL`, `LLM_BASE_URL`, `LLM_TIMEOUT`); `backend/.env` / `../.env` are loaded
    with python-dotenv if present. `.env.example` added at the repo root. The key never appears in any response.
- **API:** `POST /api/ai/investigation-brief` with `{supplier_id}` **or** `{invoice_id}` → `{source: "llm"|"fallback", provider, model, fallback_reason, brief, evidence, disclaimer}`
  (409 before analysis, 404 unknown id, 422 if both/neither id). `GET /api/ai/status` and a new `ai` field on `/api/health` report only configured/provider/model.
- **Frontend:** `frontend/src/views/AiBrief.jsx` (new) — "AI Investigation Brief" panel on Supplier Detail (**Generate AI Brief** button) and "AI explanation"
  panel with **Explain this issue** on flagged invoices. States: idle → loading (spinner, `role=status`) → success (green "AI-generated · provider · model") |
  fallback (grey "Evidence-based brief (no AI)" with the reason) | error (alert + Try again). Shows Why flagged / What to verify / Draft supplier follow-up
  (with Copy draft and Regenerate) and the backend disclaimer. `api.aiBrief` added. No numbers are computed in React.
- **Tests:** `backend/tests/test_phase6.py` — 31 tests: evidence structure equals backend values (supplier and invoice, both datasets, realistic carries risk signals);
  fallback uses only evidence values (passes the same number/wording checks), deterministic, invoice variant, supplier with nothing flagged; LLM success (injected
  callable; evidence sent as data; rules in the system prompt); missing key → fallback without any provider call (the live call is patched to raise if reached);
  API failure → fallback; 7 malformed-output shapes → fallback; invented values rejected (control case accepted); forbidden claims rejected; number validator accepts
  evidence formats/identifiers and flags foreign numbers; JSON parsing variants; config from env never exposes the key; configured-provider failure and success via the API
  (monkeypatched); endpoint validation; both datasets through the endpoint; existing numbers unchanged; wording scan of module text.
  **No test calls a live LLM.** Frontend `App.test.jsx` +2 tests: real fallback flow on supplier and invoice; stubbed `api.aiBrief` for the success and error states.
- **Browser (headless Chromium, both datasets):** Generate AI Brief and Explain this issue render the fallback brief with the supplier's own invoice numbers; no page errors.
- **Limitations:** the LLM path was verified only with injected/mocked responses in this environment (no key available here); provider model defaults
  (`claude-sonnet-5-5`, `gpt-4o-mini`) are overridable with `LLM_MODEL`. The validator checks figures and wording, not completeness or tone. The fallback is template text.
  The supplier draft lists at most 8 invoices (the rest are summarised as "and N more").

## Phase 7 – COMPLETED (Cloud deployment + production)
- **Architecture kept simple:** one FastAPI process (no database, queues, microservices or Kubernetes). **Single-service mode:** when `frontend/dist` exists
  (or `STATIC_DIR` points at a build) the API also serves the React app with an SPA fallback (`/assets/*` static, unknown `/api/*` stay JSON 404s, path traversal blocked).
  Split hosting (Vercel/Netlify UI + API host) is also supported via `VITE_API_BASE` and `CORS_ORIGINS`.
- **Backend hardening (`backend/main.py`, `backend/data_source.py`):** `CORS_ORIGINS` env (comma list; empty = allow all for local dev); global exception handler →
  `500 {"detail": "Something went wrong on the server. Please try again."}` with the traceback only in the server log; `/api/health` now returns `version`, `datasets`, `ai`;
  upload validation: empty file → 422 with a clear message, header-only / missing columns / non-CSV / non-UTF-8 / no numeric taxable values → 422 messages, > 20 MB → 413;
  blank tax cells still allowed (engine treats them as 0). Version `1.0.0`. `python-dotenv` added to requirements.
- **Frontend:** `api.js` reads `VITE_API_BASE`; production-wording for unreachable backend; proxy/gateway 5xx without JSON treated as "server unreachable";
  FastAPI `detail` messages surfaced as is. `App.jsx` checks `/api/health` on load and shows a red **"server not reachable" banner with Retry** when it fails
  (verified in the browser with the API stopped: banner + inline error, no page errors). `frontend/.env.example`, `vercel.json`, `netlify.toml` (SPA rewrites).
- **Deployment files:** `Dockerfile` (multi-stage: Node build of the UI → python:3.12-slim serving UI + API on `$PORT`, health check), `.dockerignore`, `render.yaml`
  (Docker web service, health path, secret env vars unsynced), `railway.json`, `fly.toml`. **Cloud status: NOT deployed** — no hosting account was used from this
  environment, and the Docker daemon is not available in the sandbox, so the image build itself was not executed. What **was** verified: `npm run build` → `uvicorn` alone on
  one port serves `/`, SPA routes, `/assets/*` and `/api/*`; the full demo flow passes against that server on both datasets (headless Chromium).
- **Windows launcher `Start ITC Shield.bat`:** checks Python/npm, installs backend deps, builds the UI once (skipped when `frontend\dist` exists), starts one server
  window on http://127.0.0.1:8000 and opens the browser. Written with CRLF line endings; **not executed here (Linux)** — its steps mirror the verified `start.sh`,
  which now has the same single-server default and `--dev` for the hot-reload pair.
- **README.md** rewritten: overview, architecture, local setup, one-click startup, cloud deployment (with the honest deployment status), environment variables, API, tests, limitations.
- **Tests:** `backend/tests/test_phase7.py` — 14 tests: health fields without secrets; CORS parsing and headers for configured vs other origins; unhandled error → JSON without
  stack trace; 409 before analysis on all result endpoints; JSON 404 for unknown API routes; upload empty / header-only / missing columns / non-CSV / binary / too large / valid
  with blank cells; static UI served with SPA fallback and traversal check; no build → plain 404; demo numbers unchanged.

## Phase 8 – COMPLETED (final hackathon lock + UI polish)
No new features or architecture; frontend polish only (plus one wording fix in backend text). React still computes no analytics.
- **Dashboard:** story strip **DETECT → QUANTIFY → PREDICT → PRIORITIZE → ACT** under the page head; intro steps now list all five. Hero unchanged (₹4.2L hero,
  Expected / Reconciled / Issues). **ITC Risk Radar is now a table**: Supplier | Exposure | Supplier Risk (score + level or n/a) | Affected invoices | Priority badge,
  question "Where should you investigate first?" as the panel lead; on phones it collapses to name + badge + exposure. Panel tags corrected (Top suppliers = Quantify).
  Chart label column widened so issue-type labels no longer wrap.
- **Supplier Detail:** six stat tiles in a 3-column grid — Potential ITC Exposure (+ share), Supplier Risk (score + level, or "Not available / needs 5+ months"),
  Investigation Priority (badge, score / 100, rank), Invoices flagged, ITC claimed, **Issue types** chips; "Why is this supplier prioritized?" (signals + decomposition);
  **Risk signals** panel (Phase 4 factors) when a score exists; **AI Investigation Brief moved above the Affected invoices table** so the demo reaches it without scrolling
  past 19 rows; empty state when nothing is flagged.
- **Invoice Detail:** header badge now shows the Investigation Priority level; six tiles — exposure, ITC claimed, tax on portal, **reconciliation status, matching confidence,
  investigation priority (score + rank)**; reason text and AI explanation unchanged. Backend "What we found" text for mismatches now uses ₹ with Indian grouping instead of "Rs".
- **Tables:** Reconciliation shows "Investigation priority" (HIGH/MEDIUM/LOW; "–" for matched); Suppliers table gains "Investigation priority" (badge + rank) and labels the
  model column "Supplier Risk". All badges render upper-case (legacy "High risk" → "HIGH risk").
- **Global:** loading states use a spinner with `role=status`; subtle 120–150 ms hover transitions only; badge letter-spacing; page head wraps on narrow screens; topbar on phones
  keeps the icon, scrolls the tabs and narrows the dataset selector; stat grids 3 → 2 → 1 columns; 390 px layout has no horizontal overflow (measured).
- **Verification:** backend **143 passed**; frontend **8 passed** (locators updated for the renamed Radar/Risk-signals text); `npm run build` passes; headless Chromium
  demo flow (dashboard → ₹4.2L → Radar → #1 supplier → evidence → affected invoice → AI brief → draft) passes on the production build for **both datasets**
  with no page errors (demo ₹4.2L / ₹25.0L / ₹20.8L / 147, Sundaram Steel Traders 99.1 HIGH; realistic ₹5.9L / ₹59.0L / ₹53.1L / 85, Lotus Heavy Machinery 90.9 HIGH, 79 % risk);
  dataset switching both ways; backend left reset to `sample`.

## Final state (after Phase 8)
- **Files created:** `backend/ai_brief.py`, `backend/tests/test_phase6.py`, `backend/tests/test_phase7.py`, `frontend/src/views/AiBrief.jsx`, `.env.example`, `frontend/.env.example`,
  `Dockerfile`, `.dockerignore`, `render.yaml`, `railway.json`, `fly.toml`, `frontend/vercel.json`, `frontend/netlify.toml`, `Start ITC Shield.bat`, `README.md`, `DEMO_GUIDE.md`.
- **Files modified:** `backend/main.py`, `backend/data_source.py`, `backend/reconcile.py` (₹ wording in two explanation strings only), `backend/requirements.txt` (+python-dotenv),
  `frontend/src/api.js`, `frontend/src/App.jsx`, `frontend/src/components.jsx`, `frontend/src/styles.css`, `frontend/src/views/Dashboard.jsx`, `SupplierDetail.jsx`,
  `InvoiceDetail.jsx`, `Reconciliation.jsx`, `Suppliers.jsx`, `frontend/src/App.test.jsx`, `start.sh`, `PROJECT_STATE.md`.
- **Untouched:** `supplier_risk.py`, `priority.py`, `sample_data.py`, `realistic_data.py`, all earlier test files, `vite.config.js`, `package.json`.
- **Default dataset:** ₹25.0L / ₹20.8L / ₹4.2L / 147 — unchanged and tested in every phase file.

## Post-Phase-8 enhancement – COMPLETED (user data upload · test files · Groq)
Controlled enhancement, not a phase: the Phase-8 product, its engines, model, calculations, datasets and design are unchanged.
Default demo still ₹25.0L / ₹20.8L / ₹4.2L / 147 (tested in every suite).

### 1. Real user-data upload
- **`backend/upload_normalize.py` (new):** `read_table` (CSV via pandas; XLSX first sheet via openpyxl; JSON = array of records, `{"data": [...]}`/first list value,
  or GSTR-2B-style `b2b → inv → items` flattened with item sums; format sniffed by extension and content, `.xls` refused with advice) → `normalize_table`
  (header → internal field via the documented `ALIASES` table, compared case-/punctuation-insensitively; **two headers mapping to one field = error, never a guess**;
  required `supplier_gstin, invoice_no, invoice_date, taxable_value` + IGST/CGST/SGST columns **or** a single Total GST column (then stored as IGST with a note);
  ₹/commas/Rs stripped from amounts; blank tax cells = 0; blank taxable value = error; dates ISO / day-first slashed-dashed / `DD-Mon-YYYY` / Excel serials
  (`format="mixed"`), unreadable dates = error listing examples; blank GSTIN or invoice no. = error; fully blank rows dropped) → `load_upload` returns the same
  frame shape the built-in datasets produce plus `meta.upload` (filename, format, rows, mapping, notes per file) and a detected period.
- **`data_source.load_csvs`** now delegates to it (signature extended with filenames); `DATASET_LABELS` label both samples "synthetic / sample data".
- **`main.py`:** `/api/upload` accepts CSV/XLSX/JSON (≤ 20 MB each), returns row counts, period and the applied mapping; new `GET /api/upload/schema`;
  `/api/health` adds `source_label`, `dataset_labels`, `upload` (summary when the active source is an upload). Flow is unchanged downstream:
  upload → validation → normalisation → existing `reconcile` → exposure → supplier risk → priority → existing dashboard/AI brief. Uploaded data lives only in
  `Store` and is replaced by `/api/reset`; the generators are never touched.
- **Frontend:** new `views/DataSource.jsx` on the pre-analysis dashboard with two columns — **User data** (Purchase register + Portal / GSTR-2B data file inputs
  showing the chosen filenames, *Upload & validate* → green "Files accepted … rows … period" with a collapsible column mapping and notes, red validation errors
  (`role=alert`), then *Analyze uploaded data*) and **Sample data** (tag "Synthetic / Sample Data", radio cards *Demo Dataset* / *Realistic Sample Dataset*,
  *Analyze Data*, disabled while an upload is active). The active column is highlighted; the top bar shows an active-source chip
  ("Demo Dataset (synthetic)" / "Realistic Sample Dataset (synthetic)" / green "Your uploaded data"); the analysed dashboard shows a "Synthetic sample data" tag for
  samples and a **Change data** button next to *Re-run analysis*. The old top-bar dataset `<select>` was replaced by this. Product copy changed to
  "reconciles your purchase records with available portal / GSTR-2B-style data" (no claim of checking vendor GST payment or GSTN connectivity).
  `api.upload` (FormData) and `api.uploadSchema` added.

### 2. Test data files – `demo_upload_data/`
`sample_purchase_register.csv` (24 rows, 5 fictional suppliers, Jul–Sep 2026, friendly headers *Supplier Name, Supplier GSTIN, Invoice No, …, Category*),
`sample_portal_data.csv` (22 rows, different headers *GSTIN, Invoice Number, …*), `README.md` (labelled SYNTHETIC TEST DATA; columns, every intentional case, how to
upload, expected behaviour). Cases: 2 tax mismatches (₹2,880; ₹1,700), 1 amount mismatch (₹3,240), 1 missing (₹12,330), 1 duplicate purchase-register entry (₹9,720),
1 invoice-number variation → needs review at 97.5 % confidence (₹19,800), plus one separator/leading-zero variation that correctly **matches**; 18 of 24 match.
**Verified through the application (API + browser):** Expected ₹4,22,820 · Reconciled ₹3,73,150 · Potential ITC Exposure ₹49,670 (11.7 %) · 6 issues; Radar: Arvind
Steel Industries ₹22,680 HIGH, Neha Electricals ₹12,330 HIGH, Om Chemicals ₹9,720 MEDIUM, Sunrise Logistics LOW, Prakash Packaging LOW; Supplier Risk not available
(3 months); supplier/invoice detail and the AI brief fallback work on it. The same data as XLSX and as JSON (flat and GSTR-2B-nested) gives identical results (tested).

### 3. Groq
- `ai_brief.py`: providers `groq | anthropic | openai | none`; `auto` prefers **groq** (`GROQ_API_KEY`), then anthropic, then openai. Groq uses the existing
  OpenAI-compatible chat-completions path (`https://api.groq.com/openai/v1`, `response_format: json_object`, default model `llama-3.3-70b-versatile`,
  overridable with `LLM_MODEL` / `LLM_BASE_URL`). `LLM_PROVIDER=groq` without a key → not configured → fallback (reason says which variable is missing).
  Failure reasons shown to the user are short and specific (timed out / rate limit HTTP 429 / HTTP n / unreadable response / unavailable); never bodies or keys.
  Input to Groq is exactly the backend evidence packet; output is validated as before (schema, no figures absent from the evidence, no forbidden wording).
- `.env.example` leads with `LLM_PROVIDER=groq` + `GROQ_API_KEY=` and states that free vs billed usage depends on the Groq account; `render.yaml`/`fly.toml`
  reference `GROQ_API_KEY`. The key never reaches React (`/api/ai/status` and `/api/health` expose only configured/provider/model; tested).
- **Not verified live:** no Groq key was available in the development environment, so real Groq responses were not exercised; the request shape and every failure
  path are covered with a faked `httpx.post`.

### Tests / verification
- Backend **182 passed** (143 + 23 `tests/test_upload.py` + 16 `tests/test_groq.py`; `test_phase6` fixture now also clears `GROQ_API_KEY`, one assertion follows
  the friendlier timeout wording). Upload tests: shipped files end to end (summary, every case's status and exposure, review link, format variation matched, ledger,
  radar order, supplier/invoice detail, supplier and invoice briefs, health); XLSX = CSV; XLSX detected by content; JSON array; GSTR-2B nesting with item sums;
  aliases + ₹/comma amounts + day-first and `DD-Mon-YYYY` dates; Total-GST-only with note; ambiguous columns rejected; missing columns list accepted names;
  no tax columns; bad dates/numbers reported with examples; blank rows/GSTIN; 7 malformed/empty payloads; old `.xls`; **isolation** (upload → 6 issues, reset →
  demo 147 identical summary, realistic 85); health/schema endpoints. Groq tests: auto-preference and explicit config; provider without key; missing key never
  calls the API; request shape (URL, bearer header, model, json_object, system rules, user message == evidence JSON); timeout, connect error, HTTP 500/429/401,
  empty choices, prose, partial JSON, HTML body → identical deterministic fallback; invented values / fraud wording rejected; success on uploaded data; fallback
  identical with and without Groq configured.
- Frontend **10 passed** (8 + sample-panel switching with the chip, + upload-analyze-browse-and-switch-back; dataset-selector test rewritten for the panel).
  jsdom's `FormData` cannot be sent by Node's `fetch`, so the test replaces `api.upload`'s transport with a hand-built multipart request to the same real
  backend; the native path is covered in the browser.
- `npm run build` passes. **Headless Chromium on the production single-service build, three modes:** Demo (₹4.2L / 147, Sundaram Steel Traders HIGH),
  Realistic Sample (₹5.9L / 85, Lotus Heavy Machinery 79 % HIGH), **Uploaded test data via the real file inputs** (₹49.7K / 6, Arvind Steel Industries HIGH,
  invoice ASI/26-27/0130 "Needs review" 98 %) — each through Dashboard → Radar → Supplier → AI brief → Invoice → AI explanation; no page errors; chip reflects the
  source; Change data → Demo Dataset switches back. 390 px: no horizontal overflow, chip visible, file pickers fit.

### Files
Created: `backend/upload_normalize.py`, `backend/tests/test_upload.py`, `backend/tests/test_groq.py`, `frontend/src/views/DataSource.jsx`,
`demo_upload_data/sample_purchase_register.csv`, `demo_upload_data/sample_portal_data.csv`, `demo_upload_data/README.md`.
Modified: `backend/data_source.py`, `backend/main.py`, `backend/ai_brief.py`, `backend/tests/test_phase6.py`, `frontend/src/api.js`, `frontend/src/App.jsx`,
`frontend/src/views/Dashboard.jsx`, `frontend/src/styles.css`, `frontend/src/App.test.jsx`, `.env.example`, `render.yaml`, `fly.toml`, `Start ITC Shield.bat` (one line),
`README.md`, `DEMO_GUIDE.md`, `PROJECT_STATE.md`. Untouched: `reconcile.py`, `supplier_risk.py`, `priority.py`, `sample_data.py`, `realistic_data.py`, all other tests.

### Limitations
Upload mapping covers common header spellings only; one sheet per XLSX; no `.xls`; uploaded data is in-memory and single-user; fewer than five months of history
→ no Supplier Risk; no live GSTN connection (the "portal" side is the file the user provides); Groq not exercised live here.
