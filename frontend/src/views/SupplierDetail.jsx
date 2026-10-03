import { api } from '../api.js'
import { fmtDate, inr, inrShort } from '../format.js'
import { BackLink, ModelRisk, PriorityBadge, State, TypeTag, useLoad } from '../components.jsx'
import AiBrief from './AiBrief.jsx'

export default function SupplierDetail({ id, go, back }) {
  const { loading, error, data } = useLoad(() => api.supplier(id), [id])
  const s = data?.supplier
  const inv = s?.investigation
  const hasRisk = s && s.risk_score !== null && s.risk_score !== undefined
  return (
    <>
      <BackLink onBack={back} />
      <State loading={loading} error={error}>
        {s && (
          <>
            <div className="page-head">
              <div>
                <h1 className="h1-sm">{s.supplier_name}</h1>
                <p className="muted">{s.category} · GSTIN {s.gstin}</p>
              </div>
              {inv && inv.potential_exposure > 0 && <div className="badges"><span className="muted small">Investigation priority</span><PriorityBadge level={inv.level} /></div>}
            </div>

            {/* Detect / Quantify / Predict / Prioritize at a glance - every value comes from the backend */}
            <dl className="statrow statrow-3">
              <div><dt>Potential ITC Exposure</dt><dd className="risk">{inrShort(s.exposure)}</dd><p className="stat-sub">{s.exposure_share}% of total exposure</p></div>
              <div><dt>Supplier Risk</dt><dd>{hasRisk ? <ModelRisk s={s} /> : <span className="muted na">Not available</span>}</dd>
                <p className="stat-sub">{hasRisk ? 'Predicted likelihood of follow-up next period' : 'Needs 5+ months of history'}</p></div>
              <div><dt>Investigation Priority</dt>
                <dd>{inv && inv.potential_exposure > 0 ? <><PriorityBadge level={inv.level} /> <span className="score">{inv.score}<span className="muted"> / 100</span></span></> : <span className="muted na">None (no exposure)</span>}</dd>
                {inv && inv.potential_exposure > 0 && <p className="stat-sub">Rank {inv.rank} of all suppliers</p>}</div>
              <div><dt>Invoices flagged</dt><dd>{s.issue_count}<span className="muted dd-of"> of {s.invoice_count}</span></dd></div>
              <div><dt>ITC claimed</dt><dd>{inrShort(s.itc_claimed)}</dd></div>
              <div><dt>Issue types</dt>
                <dd className="chips">{s.by_type?.length ? s.by_type.map((t) => <TypeTag key={t.type} label={`${t.label} · ${t.count}`} />) : <span className="muted na">None</span>}</dd></div>
            </dl>

            {inv && (
              <section className="panel">
                <div className="panel-head"><h2>Why is this supplier prioritized?</h2>
                  <span className="muted small">Prioritize</span></div>
                <ul className="signals" aria-label="Why this supplier is prioritized">
                  {inv.signals.filter((x) => x.source !== 'note').map((x) => <li key={x.key}>{x.text}</li>)}
                </ul>
                <p className="fine">
                  Score {inv.score} of 100 =
                  {' '}{inv.components.map((c) => `${c.label} ${c.contribution.toFixed(1)}`).join(' + ')}.
                  {!inv.risk_available && ' Supplier Risk is not available for this dataset, so it is not part of this score.'}
                  {' '}A ranking aid for what to review first, not a probability.
                </p>
              </section>
            )}
            {hasRisk ? (
              <section className="panel">
                <div className="panel-head"><h2>Risk signals</h2>
                  <span className="muted small">Predict · {Math.round(s.risk_score * 100)}% {s.predicted_risk_level}</span></div>
                <p className="muted small tight">
                  Estimated likelihood of invoices needing reconciliation follow-up next period, learned from this supplier's history.
                  Not a fraud or GST compliance assessment.
                </p>
                {s.key_risk_factors.length ? (
                  <ul className="signals" aria-label="Key risk factors">
                    {s.key_risk_factors.map((f) => <li key={f.feature}>{f.text}</li>)}
                  </ul>
                ) : <p className="muted">No elevated signals in this supplier's history.</p>}
                {s.low_history && <p className="fine">Based on only {s.history_invoice_count} past {s.history_invoice_count === 1 ? 'invoice' : 'invoices'}; treat this score with caution.</p>}
              </section>
            ) : (
              <p className="fine">Predicted risk needs at least 5 months of invoice history, which this dataset does not have.
                The risk badge shown is a rule of thumb from issue rate and exposure share.</p>
            )}

            <AiBrief target={s} kind="supplier" />

            <section className="panel">
              <div className="panel-head"><h2>Affected invoices</h2>
                <span className="muted small">{data.invoices.length} flagged, highest exposure first</span></div>
              {data.invoices.length ? (
                <div className="scroll">
                  <table className="tbl tbl-click">
                    <thead><tr>
                      <th>Invoice</th><th>Date</th><th>Issue</th>
                      <th className="num">ITC claimed</th><th className="num">Potential exposure</th><th>Investigation priority</th>
                    </tr></thead>
                    <tbody>
                      {data.invoices.map((i) => (
                        <tr key={i.invoice_id} tabIndex={0} onClick={() => go({ name: 'invoice', id: i.invoice_id })}
                          onKeyDown={(e) => e.key === 'Enter' && go({ name: 'invoice', id: i.invoice_id })}>
                          <td><strong>{i.invoice_no}</strong></td>
                          <td>{fmtDate(i.invoice_date)}</td>
                          <td><TypeTag label={i.issue_label} /></td>
                          <td className="num">{inr(i.itc_claimed)}</td>
                          <td className="num">{inr(i.exposure)}</td>
                          <td><PriorityBadge level={i.investigation_level || i.priority} /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              ) : <p className="muted pad-sm">No invoices from this supplier are flagged.</p>}
            </section>
          </>
        )}
      </State>
    </>
  )
}
