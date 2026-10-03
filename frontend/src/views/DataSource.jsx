import { useRef, useState } from 'react'
import { api } from '../api.js'

export const SAMPLES = [
  ['sample', 'Demo Dataset', '28 suppliers · Jul–Sep 2026 · calibrated demo numbers'],
  ['realistic', 'Realistic Sample Dataset', '20 suppliers · Apr–Sep 2026 · six months of history (Supplier Risk available)'],
]
export const sourceLabel = (source, upload) =>
  source === 'upload' ? 'Your uploaded data' : (SAMPLES.find(([k]) => k === source)?.[1] || 'Demo Dataset') + ' (synthetic)'

// Pre-analysis data source panel. USER DATA (two uploads) is separated from SAMPLE DATA (preloaded synthetic datasets).
// All validation and column mapping happen in the backend; this component only shows what it returns.
export default function DataSource({ source, upload, onSourceChange, onAnalyze, busy, error }) {
  const [files, setFiles] = useState({ purchase: null, portal: null })
  const [state, setState] = useState({ status: 'idle', error: null })   // idle | uploading | error
  const purchaseRef = useRef(null)
  const portalRef = useRef(null)
  const uploaded = source === 'upload' && upload

  const pick = (kind) => (e) => {
    setFiles((f) => ({ ...f, [kind]: e.target.files?.[0] || null }))
    setState({ status: 'idle', error: null })
  }

  const doUpload = async () => {
    setState({ status: 'uploading', error: null })
    try {
      const res = await api.upload(files.purchase, files.portal)
      setState({ status: 'idle', error: null })
      onSourceChange('upload', res.upload, res)
    } catch (e) {
      setState({ status: 'error', error: e.message })
    }
  }

  const chooseSample = async (ds) => {
    setState({ status: 'idle', error: null })
    try {
      await api.reset(ds)
      onSourceChange(ds, null)
    } catch (e) {
      setState({ status: 'error', error: e.message })
    }
  }

  const ready = files.purchase && files.portal
  return (
    <section className="panel sources" aria-label="Data source">
      <div className="src-grid">
        <div className={'src-col' + (source === 'upload' ? ' src-active' : '')}>
          <div className="src-head">
            <h2>User data</h2>
            {source === 'upload' && <span className="badge badge-ok">Active</span>}
          </div>
          <p className="muted small">Upload your own purchase register and portal / GSTR-2B-style data (CSV, XLSX or JSON). Columns such as
            <em> GSTIN, Invoice No, Invoice Date, Taxable Value, IGST/CGST/SGST</em> are mapped automatically; ambiguous files are rejected, never guessed.</p>
          <div className="file-row">
            <label className="file-pick">
              <span className="file-label">Purchase register</span>
              <input ref={purchaseRef} type="file" accept=".csv,.xlsx,.json,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={pick('purchase')} aria-label="Upload purchase register" />
              <span className="file-name">{files.purchase ? files.purchase.name : 'Choose file…'}</span>
            </label>
            <label className="file-pick">
              <span className="file-label">Portal / GSTR-2B data</span>
              <input ref={portalRef} type="file" accept=".csv,.xlsx,.json,text/csv,application/json,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={pick('portal')} aria-label="Upload portal data" />
              <span className="file-name">{files.portal ? files.portal.name : 'Choose file…'}</span>
            </label>
          </div>
          <div className="src-actions">
            <button className="btn btn-quiet" onClick={doUpload} disabled={!ready || state.status === 'uploading' || busy}>
              {state.status === 'uploading' ? 'Validating…' : 'Upload & validate'}
            </button>
            {uploaded && (
              <button className="btn" onClick={onAnalyze} disabled={busy}>{busy ? 'Analyzing…' : 'Analyze uploaded data'}</button>
            )}
          </div>
          {state.status === 'error' && <p className="error small" role="alert">{state.error}</p>}
          {uploaded && state.status !== 'error' && (
            <div className="upload-ok" role="status">
              <strong>Files accepted.</strong> {upload.purchase.rows} purchase rows ({upload.purchase.filename}) · {upload.portal.rows} portal rows ({upload.portal.filename})
              {upload.period ? ` · ${upload.period}` : ''}
              <details className="how">
                <summary>Column mapping</summary>
                {['purchase', 'portal'].map((k) => (
                  <p key={k} className="small"><strong>{k === 'purchase' ? 'Purchase register' : 'Portal data'}:</strong>{' '}
                    {Object.entries(upload[k].mapping).map(([from, to]) => `${from} → ${to}`).join(', ')}
                    {upload[k].notes?.length ? ` · ${upload[k].notes.join(' ')}` : ''}</p>
                ))}
              </details>
            </div>
          )}
          <p className="fine">Test files: <code>demo_upload_data/sample_purchase_register.csv</code> + <code>sample_portal_data.csv</code> (synthetic). Nothing is sent anywhere except your ITC Shield server.</p>
        </div>

        <div className={'src-col' + (source !== 'upload' ? ' src-active' : '')}>
          <div className="src-head">
            <h2>Sample data</h2>
            <span className="tag">Synthetic / Sample Data</span>
          </div>
          <p className="muted small">Preloaded, generated datasets with fictional suppliers — not real GST data and not uploaded by you. Use them to explore the product.</p>
          <div className="sample-list" role="radiogroup" aria-label="Sample dataset">
            {SAMPLES.map(([k, label, desc]) => (
              <button key={k} role="radio" aria-checked={source === k} className={'sample-opt' + (source === k ? ' sample-on' : '')}
                onClick={() => chooseSample(k)} disabled={busy}>
                <span className="radio" aria-hidden="true" />
                <span><strong>{label}</strong><span className="muted small">{desc}</span></span>
              </button>
            ))}
          </div>
          <div className="src-actions">
            <button className="btn btn-lg" onClick={onAnalyze} disabled={busy || source === 'upload'}>
              {busy && source !== 'upload' ? 'Analyzing…' : 'Analyze Data'}
            </button>
            <span className="muted small">{source === 'upload' ? 'select a sample dataset to switch back' : 'runs on the selected sample dataset'}</span>
          </div>
        </div>
      </div>
      {busy && <p className="muted status" role="status">Matching purchase records against portal records…</p>}
      {error && <p className="error status" role="alert">{error}</p>}
    </section>
  )
}
