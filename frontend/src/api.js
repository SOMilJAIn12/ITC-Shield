// All backend calls live here.
// Dev: Vite proxies /api to the FastAPI server on :8000 (BASE = '').
// Production: set VITE_API_BASE at build time (e.g. https://itc-shield-api.onrender.com) when the UI and API are on different
// hosts; leave it empty when the API serves the built UI itself (single-service deployment). No secrets live here.
const BASE = (import.meta.env.VITE_API_BASE || '').replace(/\/$/, '')

const OFFLINE = import.meta.env.DEV
  ? 'Cannot reach the backend. Is the API running on port 8000?'
  : 'The ITC Shield server is not reachable right now. Please try again in a moment.'

async function req(path, opts) {
  let res
  try {
    res = await fetch(BASE + path, opts)
  } catch {
    throw new Error(OFFLINE)
  }
  if (!res.ok) {
    let msg = res.status >= 500 ? 'The server hit an error. Please try again.' : `Request failed (${res.status})`
    let json = false
    try {
      const body = await res.json()
      json = true
      if (typeof body.detail === 'string') msg = body.detail
      else if (Array.isArray(body.detail) && body.detail[0]?.msg) msg = body.detail[0].msg
    } catch { /* non-JSON error body */ }
    // a gateway/proxy answered instead of the API (502/503/504, or a 5xx without JSON): the backend is down, not broken
    if (!json && res.status >= 500) msg = OFFLINE
    throw new Error(msg)
  }
  return res.json()
}

export const api = {
  health: () => req('/api/health'),
  analyze: () => req('/api/analyze', { method: 'POST' }),
  dashboard: () => req('/api/dashboard'),
  exposure: (supplierId) => req('/api/exposure' + (supplierId ? `?supplier_id=${encodeURIComponent(supplierId)}` : '')),
  priority: (limit = 5) => req(`/api/priority?limit=${limit}`),
  suppliers: () => req('/api/suppliers'),
  supplier: (id) => req(`/api/suppliers/${encodeURIComponent(id)}`),
  invoices: (status = 'all') => req(`/api/invoices?status=${status}`),
  invoice: (id) => req(`/api/invoices/${encodeURIComponent(id)}`),
  reset: (dataset = 'sample') => req(`/api/reset?dataset=${dataset}`, { method: 'POST' }),
  // User data: two files (CSV / XLSX / JSON). The backend validates, maps the columns and replaces the active dataset.
  upload: (purchaseFile, portalFile) => {
    const form = new FormData()
    form.append('purchase_file', purchaseFile, purchaseFile.name)
    form.append('portal_file', portalFile, portalFile.name)
    return req('/api/upload', { method: 'POST', body: form })
  },
  uploadSchema: () => req('/api/upload/schema'),
  // Phase 6: the backend builds the evidence and (if a server-side key exists) asks the LLM; otherwise it returns a fallback brief.
  aiBrief: (body) => req('/api/ai/investigation-brief', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  }),
}
