import { api } from '../api.js'
import { inr } from '../format.js'
import { ModelRisk, PriorityBadge, State, useLoad } from '../components.jsx'

export default function Suppliers({ go }) {
  const { loading, error, data } = useLoad(() => api.suppliers(), [])
  return (
    <>
      <div className="page-head"><div><h1 className="h1-sm">Supplier risk</h1>
        <p className="muted">All suppliers, ordered by potential ITC exposure. Open a supplier for its evidence and AI brief.</p></div></div>
      <State loading={loading} error={error}>
        <section className="panel">
          <div className="scroll">
            <table className="tbl tbl-click">
              <thead><tr>
                <th>Supplier</th><th>Main issue</th><th className="num">Invoices</th>
                <th className="num">Flagged</th><th className="num">ITC claimed</th>
                <th className="num">Potential exposure</th><th className="num">Share</th><th>{data?.risk_model?.status === 'ok' ? 'Supplier Risk' : 'Risk'}</th><th>Investigation priority</th>
              </tr></thead>
              <tbody>
                {data?.suppliers.map((s) => (
                  <tr key={s.supplier_id} tabIndex={0} onClick={() => go({ name: 'supplier', id: s.supplier_id })}
                    onKeyDown={(e) => e.key === 'Enter' && go({ name: 'supplier', id: s.supplier_id })}>
                    <td><strong>{s.supplier_name}</strong><div className="muted small">{s.category}</div></td>
                    <td>{s.main_issue_label || '–'}</td>
                    <td className="num">{s.invoice_count}</td>
                    <td className="num">{s.issue_count}</td>
                    <td className="num">{inr(s.itc_claimed)}</td>
                    <td className="num"><strong>{inr(s.exposure)}</strong></td>
                    <td className="num">{s.exposure_share}%</td>
                    <td><ModelRisk s={s} /></td>
                    <td>{s.investigation && s.investigation.potential_exposure > 0 ? <><PriorityBadge level={s.investigation.level} /> <span className="muted small">#{s.investigation.rank}</span></> : <span className="muted">–</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data?.risk_model?.status === 'ok' && (
            <p className="fine pad">
              Predicted risk is the estimated likelihood that a supplier will have invoices needing reconciliation follow-up in
              {' '}{data.risk_model.scoring_period}, based on its {data.risk_model.history_months} history. It is not a fraud or GST compliance assessment.
            </p>
          )}
        </section>
      </State>
    </>
  )
}
