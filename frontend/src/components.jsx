import { useEffect, useState } from 'react'

export function useLoad(fn, deps) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  useEffect(() => {
    let alive = true
    setState({ loading: true, data: null, error: null })
    fn()
      .then((data) => alive && setState({ loading: false, data, error: null }))
      .catch((e) => alive && setState({ loading: false, data: null, error: e.message }))
    return () => { alive = false }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)
  return state
}

export function State({ loading, error, children }) {
  if (loading) return <p className="muted pad" role="status"><span className="spinner" aria-hidden="true" /> Loading…</p>
  if (error) return <p className="error pad" role="alert">{error}</p>
  return children
}

// HIGH / MEDIUM / LOW badge. Accepts either the Phase 5 level ("HIGH") or the legacy value ("High"); always rendered upper-case.
export const PriorityBadge = ({ level }) =>
  level ? <span className={`badge badge-${level.toLowerCase()}`}>{level.toUpperCase()}</span> : null

export const RiskBadge = ({ level }) => (
  <span className={`badge badge-${level.toLowerCase()}`}>{level.toUpperCase()} risk</span>
)

// Phase 4: model output (score + LOW/MEDIUM/HIGH) when the backend produced one; otherwise the existing rule-of-thumb badge.
export function ModelRisk({ s }) {
  if (s.risk_score === null || s.risk_score === undefined) return s.risk_level ? <RiskBadge level={s.risk_level} /> : null
  return (
    <span className="risk-cell" title="Predicted likelihood of reconciliation follow-up next month">
      <span className="rscore">{Math.round(s.risk_score * 100)}%</span>
      <span className={`badge badge-${s.predicted_risk_level.toLowerCase()}`}>{s.predicted_risk_level}</span>
    </span>
  )
}

export const TypeTag = ({ label, matched }) => (
  <span className={matched ? 'tag tag-ok' : 'tag'}>{label}</span>
)

export function NeedsAnalysis({ onGo }) {
  return (
    <section className="panel empty">
      <h2>No analysis yet</h2>
      <p className="muted">Run the analysis on the dashboard to see reconciled results here.</p>
      <button className="btn" onClick={onGo}>Go to dashboard</button>
    </section>
  )
}

export function BackLink({ onBack, children = 'Back' }) {
  return <button className="back" onClick={onBack}>← {children}</button>
}
