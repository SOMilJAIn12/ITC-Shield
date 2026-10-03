import { useEffect, useState } from 'react'
import { api } from '../api.js'

// Phase 6: AI Investigation Brief. The backend owns the evidence and the numbers; this component only renders what it returns.
// States: idle (button) -> loading -> success (source "llm") | fallback (source "fallback", evidence-based) | error (request failed).
export default function AiBrief({ target, kind = 'supplier', title = 'AI Investigation Brief' }) {
  const [state, setState] = useState({ status: 'idle', data: null, error: null })
  const [copied, setCopied] = useState(false)
  const key = kind === 'supplier' ? target.supplier_id : target.invoice_id
  useEffect(() => { setState({ status: 'idle', data: null, error: null }); setCopied(false) }, [key])

  const run = async () => {
    setState({ status: 'loading', data: null, error: null })
    try {
      const data = await api.aiBrief(kind === 'supplier' ? { supplier_id: target.supplier_id } : { invoice_id: target.invoice_id })
      setState({ status: data.source === 'llm' ? 'success' : 'fallback', data, error: null })
    } catch (e) {
      setState({ status: 'error', data: null, error: e.message })
    }
  }

  const copy = async () => {
    try { await navigator.clipboard.writeText(state.data.brief.draft_followup); setCopied(true); setTimeout(() => setCopied(false), 1800) } catch { /* clipboard unavailable */ }
  }

  const { status, data, error } = state
  const brief = data?.brief
  const busy = status === 'loading'
  const label = kind === 'supplier' ? 'Generate AI Brief' : 'Explain this issue'
  return (
    <section className="panel ai" aria-labelledby={`ai-h-${kind}`}>
      <div className="panel-head">
        <h2 id={`ai-h-${kind}`}>{title}</h2>
        <span className="muted small">Act</span>
      </div>
      <p className="muted small tight">
        {kind === 'supplier'
          ? 'Why this supplier was flagged, what to verify, and a draft follow-up - written from the evidence above.'
          : 'A plain-English explanation of this invoice and a draft note to the supplier - written from the evidence above.'}
      </p>

      {status === 'idle' && (
        <button className="btn" onClick={run}>{label}</button>
      )}
      {status === 'loading' && (
        <p className="muted status" role="status" aria-live="polite"><span className="spinner" aria-hidden="true" /> Preparing the brief from the reconciliation evidence…</p>
      )}
      {status === 'error' && (
        <>
          <p className="error" role="alert">Could not generate the brief: {error}</p>
          <button className="btn btn-quiet" onClick={run}>Try again</button>
        </>
      )}
      {brief && (
        <>
          <div className={`ai-src ${status === 'success' ? 'ai-src-llm' : 'ai-src-fallback'}`} role="status">
            {status === 'success'
              ? <><strong>AI-generated</strong> ({data.provider}{data.model ? ` · ${data.model}` : ''}), checked against the backend evidence: every figure quoted exists in the evidence.</>
              : <><strong>Evidence-based brief</strong> (no AI). {data.fallback_reason}</>}
          </div>
          <div className="ai-grid">
            <div>
              <h3 className="why-h">Why flagged</h3>
              <ul className="signals" aria-label="Why flagged">{brief.why_flagged.map((t, i) => <li key={i}>{t}</li>)}</ul>
            </div>
            <div>
              <h3 className="why-h">What to verify</h3>
              <ul className="signals" aria-label="What to verify">{brief.what_to_verify.map((t, i) => <li key={i}>{t}</li>)}</ul>
            </div>
          </div>
          <h3 className="why-h">Draft supplier follow-up</h3>
          <pre className="draft" aria-label="Draft supplier follow-up">{brief.draft_followup}</pre>
          <div className="ai-actions">
            <button className="btn btn-quiet" onClick={copy}>{copied ? 'Copied' : 'Copy draft'}</button>
            <button className="btn btn-quiet" onClick={run} disabled={busy}>Regenerate</button>
          </div>
          <p className="fine">{data.disclaimer}</p>
        </>
      )}
    </section>
  )
}
