"""ITC Shield API. Run: uvicorn main:app --reload --port 8000  (environment: see ../.env.example)"""
import os

try:                                          # optional: load backend/.env or ../.env for local development
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))
    load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))
except ImportError:                           # pragma: no cover
    pass

import logging

from fastapi import FastAPI, File, HTTPException, UploadFile, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

import data_source
from reconcile import reconcile, exposure_aggregation
from supplier_risk import attach_supplier_risk
from priority import attach_priority
import ai_brief

VERSION = "1.0.0"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
log = logging.getLogger("itc_shield")

app = FastAPI(title="ITC Shield API", version=VERSION)


def _cors_origins() -> list[str]:
    """CORS_ORIGINS=https://a.app,https://b.app (production). Empty / unset = allow all (local development)."""
    raw = os.environ.get("CORS_ORIGINS", "").strip()
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()] or ["*"]


app.add_middleware(CORSMiddleware, allow_origins=_cors_origins(), allow_methods=["*"], allow_headers=["*"])


@app.exception_handler(Exception)
async def _unhandled(request: Request, exc: Exception):
    """Production error handling: log the traceback on the server, return a short JSON message, never a stack trace."""
    log.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server. Please try again."})


class Store:
    books = None
    portal = None
    meta = {}
    result = None

    @classmethod
    def load_sample(cls):
        cls.load("sample")

    @classmethod
    def load(cls, name: str = "sample"):
        cls.books, cls.portal, cls.meta = data_source.load_dataset(name)
        cls.result = None


Store.load_sample()


def _need_result():
    if Store.result is None:
        raise HTTPException(status_code=409, detail="Run the analysis first (POST /api/analyze).")
    return Store.result


def _dashboard_payload():
    r = _need_result()
    sups = r["suppliers"]
    total = r["summary"]["potential_exposure"] or 1
    top3 = sum(s["exposure"] for s in sups[:3])
    actions = []
    for s in sups[:4]:
        if s["main_issue"]:
            actions.append({
                "supplier_id": s["supplier_id"], "supplier_name": s["supplier_name"],
                "issue_label": s["main_issue_label"], "issue_count": s["main_issue_count"],
                "exposure": s["exposure"], "priority": s["risk_level"],
            })
    return {
        "analyzed": True, "company": Store.meta.get("company"), "period": Store.meta.get("period"),
        "source": Store.meta.get("source"),
        "summary": r["summary"], "breakdown": r["breakdown"],
        "top_suppliers": sups[:5], "top3_share": round(100 * top3 / total, 1),
        "actions": actions,
    }


def _ai_status():
    c = ai_brief.llm_config()                 # never includes the key
    return {"configured": c["configured"], "provider": c["provider"], "model": c["model"], "reason": c.get("reason")}


@app.get("/api/health")
def health():
    src = Store.meta.get("source")
    return {"status": "ok", "version": VERSION, "analyzed": Store.result is not None, "source": src,
            "source_label": "Your uploaded data" if src == "upload" else data_source.DATASET_LABELS.get(src, src),
            "datasets": list(data_source.DATASETS), "dataset_labels": data_source.DATASET_LABELS,
            "upload": Store.meta.get("upload") if src == "upload" else None, "ai": _ai_status()}


@app.get("/api/ai/status")
def ai_status():
    """Whether an LLM is configured on the server (provider + model only; the key never leaves the server)."""
    return _ai_status()


class BriefRequest(BaseModel):
    supplier_id: str | None = None
    invoice_id: str | None = None


@app.post("/api/ai/investigation-brief")
def investigation_brief(req: BriefRequest):
    """
    Phase 6: AI Investigation Brief for one supplier (or one invoice). The backend supplies structured evidence
    (reconciliation, exposure, supplier risk, investigation priority); the LLM only explains it and drafts a follow-up.
    Without an API key, or if the LLM fails or returns anything that is not supported by the evidence, a deterministic
    evidence-based brief is returned instead (`source: "fallback"`). The LLM never computes any figure.
    """
    r = _need_result()
    if bool(req.supplier_id) == bool(req.invoice_id):
        raise HTTPException(status_code=422, detail="Provide exactly one of supplier_id or invoice_id.")
    ev = (ai_brief.build_supplier_evidence(r, req.supplier_id) if req.supplier_id
          else ai_brief.build_invoice_evidence(r, req.invoice_id))
    if ev is None:
        raise HTTPException(status_code=404, detail="Supplier not found" if req.supplier_id else "Invoice not found")
    return ai_brief.generate_brief(ev)


@app.post("/api/upload")
async def upload(purchase_file: UploadFile = File(...), portal_file: UploadFile = File(...)):
    """
    USER DATA: upload a purchase register and a portal / GSTR-2B-style file (CSV, XLSX or JSON each). The files are validated,
    their columns mapped onto the internal schema (see upload_normalize.ALIASES) and the result replaces the active dataset.
    Nothing is analysed until POST /api/analyze; the built-in sample datasets are untouched (POST /api/reset brings them back).
    """
    raw = {}
    for label, f in (("purchase file", purchase_file), ("portal file", portal_file)):
        data = await f.read()
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"The {label} is larger than {MAX_UPLOAD_BYTES // (1024 * 1024)} MB.")
        if not data or not data.strip():
            raise HTTPException(status_code=422, detail=f"The {label} is empty. Upload a CSV, XLSX or JSON file with a header row and at least one invoice.")
        raw[label] = (f.filename, data)
    try:
        books, portal, meta = data_source.load_csvs(raw["purchase file"][1], raw["portal file"][1],
                                                     purchase_name=raw["purchase file"][0], portal_name=raw["portal file"][0])
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        log.exception("Upload failed")
        raise HTTPException(status_code=422, detail="Could not read the files. Please upload valid CSV, XLSX or JSON files with a header row.")
    Store.books, Store.portal, Store.meta, Store.result = books, portal, meta, None
    return {"status": "uploaded", "source": "upload", "purchase_rows": len(books), "portal_rows": len(portal),
            "period": meta["period"], "upload": meta["upload"]}


@app.get("/api/upload/schema")
def upload_schema():
    """What an upload must contain: required fields, optional fields and the accepted header spellings for each."""
    import upload_normalize as un
    return {"formats": ["csv", "xlsx", "json"], "required": un.REQUIRED, "tax": {"components": un.TAX_FIELDS, "or_total": "total_gst"},
            "optional": ["supplier_name", "category"], "aliases": un.ALIASES, "max_mb": MAX_UPLOAD_BYTES // (1024 * 1024)}


@app.post("/api/reset")
def reset(dataset: str = Query("sample", pattern="^(sample|realistic)$")):
    """Load a built-in dataset (default: the stable demo sample) and clear results."""
    Store.load(dataset)
    return {"status": "reset", "dataset": dataset, "purchase_rows": len(Store.books), "portal_rows": len(Store.portal)}


@app.post("/api/analyze")
def analyze():
    Store.result = reconcile(Store.books, Store.portal)
    try:                                    # Phase 4: adds risk fields to suppliers; never blocks the core analysis
        attach_supplier_risk(Store.result)
    except Exception as e:                  # pragma: no cover
        Store.result["risk_model"] = {"status": "error", "reason": f"Supplier risk unavailable: {e}"}
    attach_priority(Store.result)           # Phase 5: deterministic Investigation Priority (works with or without risk scores)
    return {"status": "done", "summary": Store.result["summary"]}


@app.get("/api/dashboard")
def dashboard():
    return _dashboard_payload()


@app.get("/api/exposure")
def exposure(supplier_id: str | None = None):
    """Invoice-level evidence behind Potential ITC Exposure (which invoice, supplier, issue, tax, why)."""
    r = _need_result()
    agg = exposure_aggregation(r, supplier_id)
    ledger = agg["items"]
    return {"potential_exposure": round(sum(x["exposure"] for x in ledger), 2), "count": len(ledger), "items": ledger,
            "total_exposure": agg["total_exposure"], "by_supplier": agg["by_supplier"], "by_issue_type": agg["by_issue_type"]}


@app.get("/api/priority")
def priority(limit: int = Query(10, ge=1, le=100)):
    """ITC Risk Radar: suppliers (and invoices) ranked by Investigation Priority. Computed by the backend; nothing is derived in the UI."""
    p = _need_result()["priority"]
    return {"methodology": p["methodology"], "risk_available": p["risk_available"],
            "supplier_count": len(p["suppliers"]), "suppliers": p["suppliers"][:limit],
            "invoice_count": len(p["invoices"]), "invoices": p["invoices"][:limit]}


@app.get("/api/suppliers")
def suppliers():
    r = _need_result()
    return {"suppliers": r["suppliers"], "risk_model": r.get("risk_model")}


def _list_item(i):
    d = {k: i[k] for k in ("invoice_id", "supplier_id", "supplier_name", "invoice_no", "invoice_date",
                           "itc_claimed", "status", "issue_type", "issue_label", "recon_status",
                           "recon_status_label", "match_confidence", "exposure", "priority")}
    inv = i.get("investigation")
    d["investigation_priority"] = inv["score"] if inv else None
    d["investigation_level"] = inv["level"] if inv else None
    return d


@app.get("/api/suppliers/{supplier_id}")
def supplier(supplier_id: str):
    r = _need_result()
    s = next((x for x in r["suppliers"] if x["supplier_id"] == supplier_id), None)
    if not s:
        raise HTTPException(status_code=404, detail="Supplier not found")
    inv = [i for i in r["invoices"] if i["supplier_id"] == supplier_id and i["status"] == "issue"]
    inv.sort(key=lambda i: i["exposure"], reverse=True)
    return {"supplier": s, "invoices": [_list_item(i) for i in inv]}


@app.get("/api/invoices")
def invoices(status: str = Query("all", pattern="^(all|issue|matched)$"), issue_type: str | None = None,
             supplier_id: str | None = None, q: str | None = None):
    r = _need_result()
    rows = r["invoices"]
    if status != "all":
        rows = [i for i in rows if i["status"] == status]
    if issue_type:
        rows = [i for i in rows if i["issue_type"] == issue_type]
    if supplier_id:
        rows = [i for i in rows if i["supplier_id"] == supplier_id]
    if q:
        ql = q.lower()
        rows = [i for i in rows if ql in i["invoice_no"].lower() or ql in i["supplier_name"].lower()]
    rows = sorted(rows, key=lambda i: (-i["exposure"], i["invoice_date"]))
    return {"count": len(rows), "invoices": [_list_item(i) for i in rows]}


@app.get("/api/invoices/{invoice_id}")
def invoice(invoice_id: str):
    r = _need_result()
    inv = next((i for i in r["invoices"] if i["invoice_id"] == invoice_id), None)
    if not inv:
        raise HTTPException(status_code=404, detail="Invoice not found")
    return inv


# --------------------------------------------------------------------------------------------------- production: serve the UI
# Single-service deployment: when the React app has been built (frontend/dist, or STATIC_DIR), the API also serves it,
# so one Render/Railway/Fly service (or the Windows launcher) is enough and no CORS configuration is needed.
# In development the Vite dev server serves the UI and proxies /api, so this block is simply inactive.
STATIC_DIR = os.environ.get("STATIC_DIR") or os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "frontend", "dist")
if os.path.isfile(os.path.join(STATIC_DIR, "index.html")):
    app.mount("/assets", StaticFiles(directory=os.path.join(STATIC_DIR, "assets")), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """Static files from the build, otherwise index.html (single-page app). Unknown /api routes stay 404 JSON."""
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Not found")
        candidate = os.path.normpath(os.path.join(STATIC_DIR, path))
        if path and candidate.startswith(os.path.abspath(STATIC_DIR)) and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(STATIC_DIR, "index.html"))
