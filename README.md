# ITC Shield

**GST input-tax-credit risk intelligence: Detect → Quantify → Predict → Prioritize → Act.**

Traditional reconciliation tells you *what went wrong*. ITC Shield tells you **how much is exposed, who needs attention, why, and what to do next.**

It compares a purchase register with GST-portal-style records, flags mismatched invoices, puts a rupee figure on the
**Potential ITC Exposure**, predicts **Supplier Risk** from each supplier's history, ranks suppliers and invoices by
**Investigation Priority** (the *ITC Risk Radar*), and writes an **AI Investigation Brief** (why flagged, what to verify,
draft supplier follow-up) from that evidence.

> ITC Shield never decides GST eligibility and never labels anything fraud. Exposure is an estimate from comparing records,
> not a confirmed loss; Supplier Risk and Investigation Priority are ranking aids.

---

## Product overview

| Step | What the product does | Where |
|---|---|---|
| **Detect** | Normalises invoice numbers / supplier names, matches purchase records to portal records in four tiers (exact → fuzzy with a confidence score), and classifies each entry: Matched, Tax mismatch, Amount mismatch, Missing in portal, Duplicate, Needs review. | Reconciliation page, Invoice detail |
| **Quantify** | Computes Potential ITC Exposure per invoice with a plain-English reason, and aggregates it by supplier and issue type. Totals reconcile to the paisa. | Dashboard hero, Exposure by issue type |
| **Predict** | Supplier Risk: a leakage-controlled logistic-regression model trained on each supplier's monthly reconciliation history, with per-supplier explanations. Produces no score when history is too short (nothing is invented). | Suppliers page, Supplier detail |
| **Prioritize** | Investigation Priority (0-100): a transparent, deterministic blend of exposure, supplier risk and issue evidence, with the full score decomposition shown. | ITC Risk Radar on the dashboard |
| **Act** | AI Investigation Brief: an LLM explains the backend's evidence and drafts a supplier e-mail. Every figure it quotes is checked against the evidence; if no key is configured, the LLM fails, or its output is not supported by the evidence, a deterministic evidence-based brief is shown instead. | Supplier detail, Invoice detail |

**Data sources.** The pre-analysis dashboard separates **User data** (upload your own purchase register + portal / GSTR-2B-style file) from
**Sample data** — two preloaded, clearly labelled *synthetic* datasets: the **Demo Dataset** (₹25.0L expected ITC · ₹20.8L reconciled ·
₹4.2L potential exposure · 147 issues; 28 fictional suppliers) and the **Realistic Sample Dataset** (20 fictional suppliers, 6 months,
₹59.0L / ₹5.9L / 85 issues, Supplier Risk available). Neither sample is real GST data; neither comes from the GST portal. ITC Shield has
**no live GSTN connectivity** — it reconciles the records you give it.

## Uploading your own data

In **User data** choose a *Purchase register* and a *Portal / GSTR-2B data* file, click **Upload & validate**, then **Analyze uploaded data**.
The active source is shown in the top bar ("Your uploaded data" vs "… (synthetic)"); **Change data** on the dashboard returns to the panel.

| | Purchase register | Portal / GSTR-2B-style data |
|---|---|---|
| Formats | CSV, XLSX (first sheet) | CSV, XLSX, JSON (array of records, `{"data": [...]}`, or GSTR-2B `b2b → inv → items` nesting) |
| Required | Supplier GSTIN · Invoice No · Invoice Date · Taxable Value · tax as IGST / CGST / SGST **or** one Total GST column | same |
| Optional | Supplier Name, Category | Supplier Name |

Header spellings are matched case- and punctuation-insensitively (e.g. *GSTIN, Supplier GSTIN, Vendor GSTIN, CTIN · Invoice No, Invoice Number,
Bill No, inum · Date, Invoice Date, idt · Taxable Value, Taxable Amount, txval · IGST Amount, Integrated Tax, iamt · Total Tax, GST Amount*;
full list at `GET /api/upload/schema`). Amounts may contain ₹ and commas; dates may be `YYYY-MM-DD`, `DD/MM/YYYY`, `DD-Mon-YYYY` or Excel dates.
Two columns that map to the same field, a missing required column, unreadable dates/numbers, empty or non-tabular files all produce a specific
error message — nothing is guessed. Uploaded data lives only in the server's memory for that session and is replaced when a sample dataset is selected.

**Test files:** `demo_upload_data/sample_purchase_register.csv` + `sample_portal_data.csv` (synthetic; 24 rows, 6 deliberate issues, ₹49,670
exposure — every case is explained in `demo_upload_data/README.md`).

## Architecture

```
User
 ↓
React + Vite frontend (frontend/)             - renders backend values only; computes no analytics
 ↓ HTTPS  (/api/...)
FastAPI backend (backend/main.py)
 ↓
reconcile.py  → exposure ledger / aggregation      (Detect, Quantify)
supplier_risk.py → Supplier Risk (scikit-learn)   (Predict)
priority.py   → Investigation Priority / Radar     (Prioritize)
ai_brief.py   → evidence packet → LLM provider → validation → brief, or deterministic fallback  (Act)
 ↓
LLM provider: Groq (preferred, OpenAI-compatible API); Anthropic / OpenAI also supported. Optional, server-side key only.
```

* One process, no database: state is in memory (the built-in datasets regenerate deterministically on reset).
* **Single-service deployment:** when the React build exists (`frontend/dist`), the FastAPI app also serves it, so one
  container is enough and no CORS set-up is needed. Hosting the UI separately (Vercel/Netlify) is also supported.
* The backend is the only source of truth for every number. The LLM only explains and drafts; it never calculates.

## Local setup

Requirements: Python 3.11+ and Node.js 18+ (LTS recommended).

```bash
# Terminal 1 - API on http://127.0.0.1:8000
cd backend
pip install -r requirements.txt
python -m uvicorn main:app --port 8000

# Terminal 2 - UI with hot reload on http://127.0.0.1:5173 (proxies /api to :8000)
cd frontend
npm install
npm run dev
```

Optional: copy `.env.example` to `backend/.env` and set `GROQ_API_KEY` to enable AI-written briefs. Without it the app works
fully and briefs use the evidence-based fallback.

## One-click startup

* **Windows:** double-click **`Start ITC Shield.bat`**. It checks for Python and Node, installs dependencies (first run only),
  builds the UI once, starts a single server on <http://127.0.0.1:8000> and opens the browser. Close the server window to stop.
  Delete `frontend\dist` to force a rebuild after changing the UI.
* **macOS / Linux:** `./start.sh` does the same; `./start.sh --dev` starts the hot-reload development pair instead.

## Cloud deployment

The simplest reliable setup is **one web service built from the `Dockerfile`** (builds the UI, serves UI + API on `$PORT`):

| Host | How |
|---|---|
| **Render** | "New → Blueprint" on this repo; `render.yaml` defines the service (Docker, health check `/api/health`). Add `GROQ_API_KEY` in the dashboard. |
| **Railway** | "Deploy from GitHub"; `railway.json` selects the Dockerfile and health check. Add the key as a variable. |
| **Fly.io** | `fly launch --copy-config` then `fly secrets set GROQ_API_KEY=...` and `fly deploy` (`fly.toml` included). |

Split hosting (optional): deploy `frontend/` to **Vercel** or **Netlify** (`vercel.json` / `netlify.toml` add the SPA rewrite),
set the build variable `VITE_API_BASE=https://<your-api-host>` there, and set `CORS_ORIGINS=https://<your-ui-host>` on the API.

Production behaviour that is built in: JSON error responses with no stack traces (tracebacks go to the server log only),
useful 413/422 messages for empty, oversized or malformed uploads, a `/api/health` endpoint with version, dataset and AI status,
an "API unreachable" banner with retry in the UI, and automatic fallback when the LLM is unavailable.

> **Deployment status:** the deployment files are included and the single-service production mode (built UI served by the API)
> was verified locally end to end. A live cloud URL is **not** claimed here: no deployment was performed from this repository
> (no hosting account was involved), so follow the table above to deploy.

## Environment variables

All variables are read by the backend only (see `.env.example`). Nothing secret is ever sent to the browser; the health endpoint
reports only *whether* a provider is configured and which model.

| Variable | Purpose | Default |
|---|---|---|
| `LLM_PROVIDER` | `groq` (preferred), `anthropic`, `openai`, `none`, or `auto` | `auto` = groq → anthropic → openai, whichever key is set |
| `GROQ_API_KEY` | Groq key, server-side only. Free or billed usage depends on your Groq account — not claimed here. | unset → evidence-based fallback briefs |
| `ANTHROPIC_API_KEY` / `OPENAI_API_KEY` | Alternative providers | unset |
| `LLM_MODEL` | Model override | `llama-3.3-70b-versatile` (groq) / `claude-sonnet-5-5` / `gpt-4o-mini` |
| `LLM_BASE_URL` | OpenAI-compatible endpoint (Groq default `https://api.groq.com/openai/v1`; also Ollama, OpenRouter…) | provider default |
| `LLM_TIMEOUT` | Seconds before falling back | `25` |
| `CORS_ORIGINS` | Comma-separated allowed browser origins | empty = allow all (local dev / same-origin deploy) |
| `PORT` | Listening port (hosts inject it) | `8000` |
| `STATIC_DIR` | Where the built UI lives | `../frontend/dist` |

Frontend (build-time, `frontend/.env.example`): `VITE_API_BASE` — public API URL when the UI is hosted separately; leave empty otherwise.

## API

`GET /api/health` · `POST /api/reset[?dataset=sample|realistic]` · `POST /api/upload` (two CSVs) · `POST /api/analyze` ·
`GET /api/dashboard` · `GET /api/exposure[?supplier_id=]` · `GET /api/priority[?limit=]` · `GET /api/suppliers` ·
`GET /api/suppliers/{id}` · `GET /api/invoices?status=&issue_type=&supplier_id=&q=` · `GET /api/invoices/{id}` ·
`GET /api/upload/schema` · `GET /api/ai/status` · `POST /api/ai/investigation-brief` `{supplier_id}` or `{invoice_id}`.
Result endpoints return 409 until `/api/analyze` has run. Interactive docs: `/docs`.

`POST /api/upload` takes multipart `purchase_file` + `portal_file` (CSV / XLSX / JSON, ≤ 20 MB each) and returns the row counts, detected
period and the column mapping it applied; `/api/health` reports `source` (`sample` | `realistic` | `upload`) and a human label.

## Tests

```bash
cd backend && python -m pytest -q tests      # 182 tests: engine, exposure, risk model, priority, AI brief, production hardening, upload, Groq
cd frontend && npm test                      # 10 click-flow tests against a running backend on :8000 (resets it to the demo dataset)
cd frontend && npm run build                 # production build
```

No test calls a live LLM: the provider call is injected or `httpx.post` is faked, and the test environment clears every provider key.
The frontend upload test replaces only the multipart transport (jsdom's `FormData` is not accepted by Node's `fetch`); the browser-native
upload path is exercised by the headless Chromium walkthrough.

## Limitations

* The bundled datasets are synthetic and fictional; no database, auth or multi-user state (an upload lives in server memory until replaced).
* No live GST portal / GSTN connection: the portal side is whatever file you upload (GSTR-2B-style CSV/XLSX/JSON). ITC Shield does not check
  whether a vendor paid GST; it reconciles your records with the portal-style data you provide and identifies potential ITC exposure.
* Upload mapping covers common header spellings; unusual headers must be renamed. XLS (old binary Excel) is not accepted; only the first sheet
  of an XLSX is read. Uploaded data with fewer than five months of history gets no Supplier Risk score (as designed).
* Supplier Risk is trained on ~80 rows of synthetic history: it ranks better than chance but does not beat a simple
  "recent issue rate" baseline, scores can saturate near 0/100 %, and the demo dataset (3 months) gets no score at all.
* Investigation Priority weights, severity table and thresholds are explainable hand-set defaults, not validated against real
  investigation outcomes; scores are comparable within a run, not across runs.
* The engine does not model credit notes, reverse charge, Section 16(4) time limits, blocked credits, portal-only records or
  filing periods ("missing" can also mean late filing).
* The AI brief depends on the configured provider (Groq by default) being reachable and within its rate limits; its output is validated
  for schema, figures and wording, not for completeness. Live Groq calls were not made from the development environment (no key there);
  the request/response handling is covered by faked-transport tests. The fallback brief is template-based.
* Frontend bundle is unsplit (~600 kB); fonts come from Google Fonts with a system fallback offline.
* Cloud deployment files are provided but no public deployment is claimed (see above). The Windows launcher was written on Linux
  and could not be executed here; its steps mirror the verified shell launcher.

See `PROJECT_STATE.md` for the full phase-by-phase engineering record and `DEMO_GUIDE.md` for the 90-second demo flow.
