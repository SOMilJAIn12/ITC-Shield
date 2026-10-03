import { useEffect, useState } from 'react'
import { Bar, BarChart, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api } from '../api.js'
import { inr, inrShort } from '../format.js'
import { ModelRisk, PriorityBadge } from '../components.jsx'
import DataSource from './DataSource.jsx'

function useCountUp(target, ms = 800) {
  const [v, setV] = useState(0)
  useEffect(() => {
    const reduce = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches
    if (reduce || typeof requestAnimationFrame !== 'function') { setV(target); return }
    let raf, t0
    const step = (t) => {
      if (!t0) t0 = t
      const p = Math.min(1, (t - t0) / ms)
      setV(target * (1 - Math.pow(1 - p, 3)))
      if (p < 1) raf = requestAnimationFrame(step)
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return v
}

const STEPS = [
  ['Detect', 'Match purchase records against portal records and flag mismatches.'],
  ['Quantify', 'Put a rupee figure on the ITC that is potentially exposed.'],
  ['Predict', 'Score each supplier\'s risk of needing follow-up from its history.'],
  ['Prioritize', 'Rank suppliers and invoices by where to investigate first.'],
  ['Act', 'Get an AI-written brief and a draft supplier follow-up.'],
]

// The product story, shown as a compact strip once results are on screen.
export function Story() {
  return (
    <ol className="story" aria-label="How ITC Shield works">
      {STEPS.map(([t]) => <li key={t}><span>{t}</span></li>)}
    </ol>
  )
}

function Intro({ onRun, busy, error, source, upload, onSourceChange }) {
  return (
    <section className="intro">
      <h1>Find the input tax credit at risk before you file.</h1>
      <p className="lede">
        ITC Shield reconciles your purchase records with available portal / GSTR-2B-style data, identifies potential ITC exposure,
        and shows which suppliers to follow up with first.
      </p>
      <DataSource source={source} upload={upload} onSourceChange={onSourceChange} onAnalyze={onRun} busy={busy} error={error} />
      <ol className="steps">
        {STEPS.map(([t, d], i) => (
          <li key={t}><span className="step-n">{i + 1}</span><div><strong>{t}</strong><p>{d}</p></div></li>
        ))}
      </ol>
    </section>
  )
}

function Hero({ d }) {
  const s = d.summary
  const shown = useCountUp(s.potential_exposure)
  const exposurePct = Math.max(0, Math.min(100, s.exposure_pct))
  return (
    <section className="hero" aria-label="Summary">
      <div className="hero-main">
        <div className="hero-num" aria-label={`Potential ITC Exposure ${inrShort(s.potential_exposure)}`}>
          {inrShort(shown)}
        </div>
        <div className="hero-label">Potential ITC Exposure</div>
        <p className="hero-note">
          {s.exposure_pct}% of expected ITC. An estimate from comparing records, not a confirmed loss.
        </p>
      </div>
      <dl className="hero-stats">
        <div><dt>Expected ITC</dt><dd>{inrShort(s.expected_itc)}</dd></div>
        <div><dt>Reconciled ITC</dt><dd>{inrShort(s.reconciled_itc)}</dd></div>
        <div><dt>Issues</dt><dd>{s.issue_count}</dd></div>
      </dl>
      <div className="split" aria-hidden="true">
        <div className="split-ok" style={{ width: `${100 - exposurePct}%` }} />
        <div className="split-risk" style={{ width: `${exposurePct}%` }} />
      </div>
      <div className="split-legend">
        <span><i className="dot dot-ok" /> Reconciled with portal records</span>
        <span><i className="dot dot-risk" /> Potential exposure</span>
      </div>
    </section>
  )
}

function Radar({ d, go }) {
  // ranking, scores, levels and all figures come from /api/priority (computed by the backend); nothing is calculated here
  const p = d.priority
  if (!p || !p.suppliers.length) return null
  return (
    <section className="panel radar" aria-labelledby="radar-h">
      <div className="panel-head">
        <h2 id="radar-h">ITC Risk Radar</h2>
        <span className="muted small">Prioritize</span>
      </div>
      <p className="radar-q">Where should you investigate first?</p>
      <div className="radar-head" aria-hidden="true">
        <span /><span>Supplier</span><span className="num">Exposure</span><span className="num">Supplier Risk</span>
        <span className="num">Affected invoices</span><span className="num">Priority</span>
      </div>
      <ol className="radar-list" aria-label="ITC Risk Radar suppliers">
        {p.suppliers.map((s) => (
          <li key={s.supplier_id}>
            <button className="radar-row" onClick={() => go({ name: 'supplier', id: s.supplier_id })}>
              <span className="radar-n">{s.rank}</span>
              <span className="rank-name">
                <strong>{s.supplier_name}</strong>
                <span className="muted small">{s.top_issue ? s.top_issue.label : 'No open issues'}</span>
              </span>
              <span className="radar-amt num">{inrShort(s.potential_exposure)}</span>
              <span className="radar-risk num">
                {s.supplier_risk === null || s.supplier_risk === undefined
                  ? <span className="muted">n/a</span>
                  : <><strong>{Math.round(s.supplier_risk * 100)}%</strong> <span className="muted small">{s.supplier_risk_level}</span></>}
              </span>
              <span className="radar-count num">{s.affected_invoice_count}</span>
              <span className="radar-badge"><PriorityBadge level={s.priority_level} /></span>
            </button>
          </li>
        ))}
      </ol>
      {!p.risk_available && (
        <p className="fine">Supplier Risk needs at least 5 months of history, which this dataset does not have.
          Investigation Priority here uses exposure and issue evidence only.</p>
      )}
      <details className="how">
        <summary>How is Investigation Priority calculated?</summary>
        <p className="small">{p.methodology.supplier_formula}</p>
        <p className="small">Levels: HIGH at {p.methodology.thresholds.HIGH} or above, MEDIUM at {p.methodology.thresholds.MEDIUM} or above, otherwise LOW.
          {' '}It is a 0 to 100 ranking aid for deciding what to review first, not a probability and not a GST determination.</p>
      </details>
    </section>
  )
}

function TopSuppliers({ d, go }) {
  // ranking, amounts and invoice counts come from /api/exposure (by_supplier, already sorted by exposure)
  const top = d.exposure.by_supplier.slice(0, 5)
  const risk = new Map(d.top_suppliers.map((s) => [s.supplier_id, s]))
  const max = Math.max(...top.map((s) => s.total_potential_exposure), 1)
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Top Suppliers Driving Exposure</h2>
        <span className="muted small">Quantify</span>
      </div>
      <p className="muted small tight">
        Top 3 suppliers account for {d.top3_share}% of potential exposure.
      </p>
      <ul className="rank" aria-label="Top suppliers">
        {top.map((s) => (
          <li key={s.supplier_id}>
            <button className="rank-row" onClick={() => go({ name: 'supplier', id: s.supplier_id })}>
              <span className="rank-name">
                <strong>{s.supplier_name}</strong>
                <span className="muted small">{s.affected_invoice_count} affected {s.affected_invoice_count === 1 ? 'invoice' : 'invoices'}</span>
              </span>
              <span className="rank-bar"><span style={{ width: `${(s.total_potential_exposure / max) * 100}%` }} /></span>
              <span className="rank-amt">{inrShort(s.total_potential_exposure)}</span>
              {risk.get(s.supplier_id) && <ModelRisk s={risk.get(s.supplier_id)} />}
            </button>
          </li>
        ))}
      </ul>
      <button className="link" onClick={() => go({ name: 'suppliers' })}>View all suppliers</button>
    </section>
  )
}

function Breakdown({ d }) {
  // issue-type totals come from /api/exposure (by_issue_type); nothing is summed here, rows are only ordered largest-first for display
  const data = [...d.exposure.by_issue_type].sort((a, b) => b.exposure - a.exposure).map((b) => ({ ...b, name: `${b.label} (${b.count})` }))
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Exposure by issue type</h2>
        <span className="muted small">Detect</span>
      </div>
      <p className="muted small tight">Potential exposure by issue type, from invoice-level evidence.</p>
      <div className="chart" role="img" aria-label="Potential exposure by issue type">
        <ResponsiveContainer width="100%" height={230}>
          <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, bottom: 4, left: 0 }}>
            <XAxis type="number" hide />
            <YAxis type="category" dataKey="name" width={214} tickLine={false} axisLine={false}
              tick={{ fontSize: 12.5, fill: 'var(--muted)' }} />
            <Tooltip cursor={{ fill: 'rgba(23,32,51,0.05)' }} formatter={(v) => [inr(v), 'Potential exposure']} />
            <Bar dataKey="exposure" radius={[0, 3, 3, 0]} barSize={18}>
              {data.map((b, i) => <Cell key={b.reason_code} fill={i === 0 ? 'var(--risk)' : 'var(--ink-soft)'} />)}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </section>
  )
}

function Actions({ d, go }) {
  return (
    <section className="panel">
      <div className="panel-head">
        <h2>Suggested follow-ups</h2>
        <span className="muted small">Act</span>
      </div>
      <ul className="actions">
        {d.actions.map((a) => (
          <li key={a.supplier_id}>
            <div>
              <strong>{a.supplier_name}</strong>
              <p className="muted">Confirm {a.issue_count} {a.issue_count === 1 ? 'invoice' : 'invoices'} flagged "{a.issue_label}"</p>
            </div>
            <span className="rank-amt">{inrShort(a.exposure)}</span>
            <button className="btn btn-quiet" onClick={() => go({ name: 'supplier', id: a.supplier_id })}>
              Review invoices
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}

export default function Dashboard({ dash, setDash, go, source, upload, onSourceChange, onChangeData }) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)

  const run = async () => {
    setBusy(true); setError(null)
    try {
      // keep the loading state visible for a moment so the step is legible on screen
      const [, data] = await Promise.all([
        new Promise((r) => setTimeout(r, 700)),
        api.analyze().then(() => Promise.all([api.dashboard(), api.exposure(), api.priority(5)]))
          .then(([dash, exposure, priority]) => ({ ...dash, exposure, priority })),
      ])
      setDash(data)
    } catch (e) {
      setError(e.message)
    } finally {
      setBusy(false)
    }
  }

  if (!dash) return <Intro onRun={run} busy={busy} error={error} source={source} upload={upload} onSourceChange={onSourceChange} />

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="h1-sm">{dash.company}</h1>
          <p className="muted">Purchase register vs portal / GSTR-2B-style records · {dash.period}
            {dash.source !== 'upload' && <span className="tag tag-sm">Synthetic sample data</span>}</p>
        </div>
        <div className="badges">
          <button className="btn btn-quiet" onClick={onChangeData} disabled={busy}>Change data</button>
          <button className="btn btn-quiet" onClick={run} disabled={busy}>
            {busy ? 'Analyzing…' : 'Re-run analysis'}
          </button>
        </div>
      </div>
      <Story />
      {error && <p className="error" role="alert">{error}</p>}
      <Hero d={dash} />
      <Radar d={dash} go={go} />
      <div className="grid-2">
        <TopSuppliers d={dash} go={go} />
        <Breakdown d={dash} />
      </div>
      <Actions d={dash} go={go} />
      <p className="fine">
        Potential ITC Exposure is an estimate from comparing the purchase register with portal records.
        It is not a determination of GST eligibility or a confirmed loss.
      </p>
    </>
  )
}
