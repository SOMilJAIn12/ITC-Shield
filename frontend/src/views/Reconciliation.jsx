import { useMemo, useState } from 'react'
import { api } from '../api.js'
import { fmtDate, inr } from '../format.js'
import { PriorityBadge, State, TypeTag, useLoad } from '../components.jsx'

export default function Reconciliation({ go }) {
  const { loading, error, data } = useLoad(() => api.invoices('all'), [])
  const [status, setStatus] = useState('issue')
  const [type, setType] = useState('all')
  const [q, setQ] = useState('')

  const types = useMemo(() => {
    const m = new Map()
    ;(data?.invoices || []).forEach((i) => i.issue_type && m.set(i.issue_type, i.issue_label))
    return [...m.entries()]
  }, [data])

  const rows = useMemo(() => {
    const ql = q.trim().toLowerCase()
    return (data?.invoices || []).filter((i) =>
      (status === 'all' || i.status === status) &&
      (type === 'all' || i.issue_type === type) &&
      (!ql || i.invoice_no.toLowerCase().includes(ql) || i.supplier_name.toLowerCase().includes(ql)))
  }, [data, status, type, q])

  return (
    <>
      <div className="page-head"><div><h1 className="h1-sm">Reconciliation</h1>
        <p className="muted">Every purchase register entry, compared with portal records.</p></div></div>
      <State loading={loading} error={error}>
        <section className="panel">
          <div className="filters">
            <div className="seg" role="group" aria-label="Show">
              {[['issue', 'Issues'], ['matched', 'Matched'], ['all', 'All']].map(([k, l]) => (
                <button key={k} className={status === k ? 'seg-on' : ''} onClick={() => setStatus(k)}>{l}</button>
              ))}
            </div>
            <select value={type} onChange={(e) => setType(e.target.value)} aria-label="Issue type">
              <option value="all">All issue types</option>
              {types.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
            <input type="search" placeholder="Search invoice or supplier" value={q}
              onChange={(e) => setQ(e.target.value)} aria-label="Search" />
            <span className="muted small grow-right">{rows.length} records</span>
          </div>
          <div className="scroll">
            <table className="tbl tbl-click">
              <thead><tr>
                <th>Invoice</th><th>Supplier</th><th>Date</th>
                <th className="num">ITC claimed</th><th>Result</th>
                <th className="num">Potential exposure</th><th>Investigation priority</th>
              </tr></thead>
              <tbody>
                {rows.map((i) => (
                  <tr key={i.invoice_id} tabIndex={0} onClick={() => go({ name: 'invoice', id: i.invoice_id })}
                    onKeyDown={(e) => e.key === 'Enter' && go({ name: 'invoice', id: i.invoice_id })}>
                    <td><strong>{i.invoice_no}</strong></td>
                    <td>{i.supplier_name}</td>
                    <td>{fmtDate(i.invoice_date)}</td>
                    <td className="num">{inr(i.itc_claimed)}</td>
                    <td><TypeTag label={i.issue_label} matched={i.status === 'matched'} /></td>
                    <td className="num">{i.exposure ? inr(i.exposure) : '–'}</td>
                    <td>{i.status === 'matched' ? <span className="muted">–</span> : <PriorityBadge level={i.investigation_level || i.priority} />}</td>
                  </tr>
                ))}
                {!rows.length && <tr><td colSpan="7" className="muted pad">No records match these filters.</td></tr>}
              </tbody>
            </table>
          </div>
        </section>
      </State>
    </>
  )
}
