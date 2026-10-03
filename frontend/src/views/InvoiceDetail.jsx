import { api } from '../api.js'
import { fmtDate, inr2 } from '../format.js'
import { BackLink, PriorityBadge, State, TypeTag, useLoad } from '../components.jsx'
import AiBrief from './AiBrief.jsx'

const show = (f, v) => (v === null || v === undefined ? null : f.kind === 'money' ? inr2(v) : f.field === 'Invoice date' ? fmtDate(v) : v)

export default function InvoiceDetail({ id, go, back }) {
  const { loading, error, data: i } = useLoad(() => api.invoice(id), [id])
  return (
    <>
      <BackLink onBack={back} />
      <State loading={loading} error={error}>
        {i && (
          <>
            <div className="page-head">
              <div>
                <h1 className="h1-sm">Invoice {i.invoice_no}</h1>
                <p className="muted">
                  <button className="link inline" onClick={() => go({ name: 'supplier', id: i.supplier_id })}>
                    {i.supplier_name}
                  </button> · {fmtDate(i.invoice_date)} · {i.invoice_id}
                </p>
              </div>
              <div className="badges"><TypeTag label={i.issue_label} matched={i.status === 'matched'} />
                <PriorityBadge level={i.investigation?.level || i.priority} /></div>
            </div>

            <dl className="statrow statrow-3">
              <div><dt>Potential ITC Exposure</dt><dd className="risk">{i.exposure ? inr2(i.exposure) : '₹0'}</dd></div>
              <div><dt>ITC claimed in books</dt><dd>{inr2(i.itc_claimed)}</dd></div>
              <div><dt>Tax on portal</dt><dd>{i.portal_tax === null ? <span className="muted na">Not found</span> : inr2(i.portal_tax)}</dd></div>
              <div><dt>Reconciliation status</dt><dd className="dd-text">{i.recon_status_label}</dd></div>
              <div><dt>Matching confidence</dt>
                <dd className="dd-text">{i.match_confidence === null || i.match_confidence === undefined
                  ? <span className="muted na">{i.status === 'matched' ? 'Exact match' : 'No portal record linked'}</span>
                  : <>{Math.round(i.match_confidence)}%<span className="muted dd-of"> {i.match_confidence_label}</span></>}</dd></div>
              <div><dt>Investigation Priority</dt>
                <dd>{i.investigation ? <><PriorityBadge level={i.investigation.level} /> <span className="score">{i.investigation.score}<span className="muted"> / 100</span></span></> : <span className="muted na">{i.status === 'matched' ? 'None (matched)' : 'None (no exposure)'}</span>}</dd>
                {i.investigation && <p className="stat-sub">Rank {i.investigation.rank} among invoices with exposure</p>}</div>
            </dl>

            <section className="panel">
              <h2>What we found</h2>
              <p className="explain">{i.explanation}</p>
              {i.reason && (
                <>
                  <h3 className="why-h">{i.exposure > 0 ? 'Why this invoice is counted' : 'Why this invoice is flagged'}</h3>
                  <p className="explain">{i.reason}</p>
                </>
              )}
              {i.high_value && <p className="fine">High-value invoice: worth a closer look when following up.</p>}
              {i.related_invoice_id && (
                <p><button className="link inline" onClick={() => go({ name: 'invoice', id: i.related_invoice_id })}>
                  View original entry {i.related_invoice_id}</button></p>
              )}
            </section>

            <section className="panel">
              <h2>Purchase register vs portal</h2>
              <div className="scroll">
                <table className="tbl cmp">
                  <thead><tr><th>Field</th><th>Purchase register</th><th>Portal</th><th className="num">Difference</th></tr></thead>
                  <tbody>
                    {i.fields.map((f) => {
                      const p = show(f, f.portal)
                      const unmatched = !i.portal_found && f.field === 'Total tax (ITC)'
                      return (
                        <tr key={f.field} className={f.flag || unmatched ? 'row-flag' : ''}>
                          <td>{f.field}</td>
                          <td>{show(f, f.books)}</td>
                          <td>{p === null ? <span className="muted">Not found</span> : p}</td>
                          <td className="num">{unmatched ? 'No portal record' : f.flag && f.difference !== null ? (f.difference > 0 ? '+' : '') + inr2(f.difference).replace('₹-', '-₹') : f.flag ? 'Differs' : ''}</td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </section>

            {i.suggested_action && (
              <section className="panel">
                <h2>Suggested follow-up</h2>
                <p className="explain">{i.suggested_action}</p>
                <p className="fine">This is a prompt for review, not a GST eligibility decision.</p>
              </section>
            )}
            {i.status !== 'matched' && <AiBrief target={i} kind="invoice" title="AI explanation" />}
          </>
        )}
      </State>
    </>
  )
}
