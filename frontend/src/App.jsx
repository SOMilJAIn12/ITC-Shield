import { useEffect, useState } from 'react'
import { api } from './api.js'
import Dashboard from './views/Dashboard.jsx'
import Reconciliation from './views/Reconciliation.jsx'
import Suppliers from './views/Suppliers.jsx'
import SupplierDetail from './views/SupplierDetail.jsx'
import InvoiceDetail from './views/InvoiceDetail.jsx'
import { NeedsAnalysis } from './components.jsx'
import { sourceLabel } from './views/DataSource.jsx'

const TABS = [
  ['dashboard', 'Dashboard'],
  ['reconciliation', 'Reconciliation'],
  ['suppliers', 'Suppliers'],
]

function Shield() {
  return (
    <svg width="22" height="24" viewBox="0 0 22 24" aria-hidden="true">
      <path d="M11 1 2 4.5v6.8c0 5.4 3.7 9.7 9 11.7 5.3-2 9-6.3 9-11.7V4.5L11 1Z" fill="var(--ink)" />
      <path d="m7 12 3 3 5-6" fill="none" stroke="#fff" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function App() {
  const [dash, setDash] = useState(null) // null until "Analyze Data" has been run in this session
  const [stack, setStack] = useState([{ name: 'dashboard' }])
  const [source, setSource] = useState('sample')      // 'sample' | 'realistic' (synthetic samples) | 'upload' (user data)
  const [upload, setUpload] = useState(null)           // upload summary from the backend when source === 'upload'
  const view = stack[stack.length - 1]
  const active = stack[0].name

  const go = (v) => { setStack((s) => [...s, v]); window.scrollTo(0, 0) }
  const back = () => { setStack((s) => (s.length > 1 ? s.slice(0, -1) : s)); window.scrollTo(0, 0) }
  const tab = (name) => setStack([{ name }])

  // Backend health: sets the dataset selector from the server and shows a banner (with retry) when the API is unreachable.
  const [offline, setOffline] = useState(null)
  const checkHealth = () => api.health()
    .then((h) => {
      setOffline(null)
      if (h.source === 'sample' || h.source === 'realistic' || h.source === 'upload') setSource(h.source)
      setUpload(h.source === 'upload' ? h.upload : null)
    })
    .catch((e) => setOffline(e.message))
  useEffect(() => { checkHealth() }, [])    // eslint-disable-line react-hooks/exhaustive-deps
  // Called by the data source panel after the backend has switched its active dataset (sample reset or validated upload).
  const onSourceChange = (src, info, res) => {
    setSource(src); setUpload(src === 'upload' ? { ...info, period: res?.period } : null); setDash(null)
  }
  const changeData = () => { setDash(null); setStack([{ name: 'dashboard' }]); window.scrollTo(0, 0) }

  let body
  if (view.name === 'dashboard') body = <Dashboard dash={dash} setDash={setDash} go={go} source={source} upload={upload} onSourceChange={onSourceChange} onChangeData={changeData} />
  else if (!dash) body = <NeedsAnalysis onGo={() => tab('dashboard')} />
  else if (view.name === 'reconciliation') body = <Reconciliation go={go} />
  else if (view.name === 'suppliers') body = <Suppliers go={go} />
  else if (view.name === 'supplier') body = <SupplierDetail id={view.id} go={go} back={back} />
  else if (view.name === 'invoice') body = <InvoiceDetail id={view.id} go={go} back={back} />

  return (
    <>
      <header className="topbar">
        <div className="topbar-in">
          <button className="brand" onClick={() => tab('dashboard')} aria-label="ITC Shield home">
            <Shield /> <span>ITC Shield</span>
          </button>
          <nav aria-label="Main">
            {TABS.map(([k, label]) => (
              <button key={k} className={'tab' + (active === k ? ' tab-on' : '')}
                aria-current={active === k ? 'page' : undefined} onClick={() => tab(k)}>
                {label}
              </button>
            ))}
          </nav>
          <div className="ds-switch" aria-label="Active data source">
            <span className={'src-chip' + (source === 'upload' ? ' src-chip-user' : '')} title={source === 'upload' ? 'User data (uploaded)' : 'Synthetic sample data (preloaded)'}>
              <i className="dot" aria-hidden="true" /> {sourceLabel(source, upload)}
            </span>
          </div>
        </div>
      </header>
      {offline && (
        <div className="banner" role="alert">
          <span>{offline}</span>
          <button className="btn btn-quiet btn-sm" onClick={checkHealth}>Retry</button>
        </div>
      )}
      <main className="page">{body}</main>
    </>
  )
}
