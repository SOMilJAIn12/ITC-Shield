// End-to-end click flow against the REAL running backend (http://127.0.0.1:8000).
// Start the API first:  cd backend && uvicorn main:app --port 8000
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import App from './App.jsx'
import { api } from './api.js'

beforeAll(async () => { await api.reset() })

test('Dashboard -> Analyze -> supplier -> invoice', async () => {
  const user = userEvent.setup()
  render(<App />)

  // before analysis: no numbers, only the call to action
  expect(screen.queryByText('Potential ITC Exposure')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Analyze Data' }))

  // headline numbers
  expect(await screen.findByText('Potential ITC Exposure', {}, { timeout: 8000 })).toBeInTheDocument()
  expect(await screen.findByText('₹4.2L', {}, { timeout: 4000 })).toBeInTheDocument()
  expect(screen.getByText('₹25.0L')).toBeInTheDocument()
  expect(screen.getByText('₹20.8L')).toBeInTheDocument()
  expect(screen.getByText('147')).toBeInTheDocument()

  // top suppliers -> click first
  const list = screen.getByRole('list', { name: 'Top suppliers' })
  const rows = within(list).getAllByRole('button')
  expect(rows.length).toBe(5)
  await user.click(rows[0])

  // affected invoices
  expect(await screen.findByText('Affected invoices')).toBeInTheDocument()
  const invRows = (await screen.findAllByText(/\/26-27\//)).map((n) => n.closest('tr'))
  expect(invRows.length).toBeGreaterThan(5)
  await user.click(invRows[0])

  // invoice discrepancy detail
  expect(await screen.findByText('Purchase register vs portal')).toBeInTheDocument()
  expect(screen.getByText('What we found')).toBeInTheDocument()
  expect(screen.getByText('Suggested follow-up')).toBeInTheDocument()
  expect(screen.getByText('Potential ITC Exposure')).toBeInTheDocument()

  // back twice returns to the dashboard
  await user.click(screen.getByRole('button', { name: /Back/ }))
  expect(await screen.findByText('Affected invoices')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: /Back/ }))
  expect(await screen.findByText('Top Suppliers Driving Exposure')).toBeInTheDocument()
})

test('Reconciliation and Suppliers tabs work after analysis', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: 'Analyze Data' }))
  await screen.findByText('Potential ITC Exposure', {}, { timeout: 8000 })

  await user.click(screen.getByRole('button', { name: 'Reconciliation' }))
  expect(await screen.findByText('147 records')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'All' }))
  expect(await screen.findByText('620 records')).toBeInTheDocument()

  await user.click(screen.getByRole('button', { name: 'Suppliers' }))
  expect(await screen.findByText('Supplier risk')).toBeInTheDocument()
  expect((await screen.findAllByText(/Sundaram Steel Traders/)).length).toBeGreaterThan(0)
})

test('Realistic dataset shows model risk score, level and key risk factors; demo keeps its badges', async () => {
  const user = userEvent.setup()
  await api.reset('realistic')
  try {
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Analyze Data' }))
    await screen.findByText('Potential ITC Exposure', {}, { timeout: 8000 })
    expect(await screen.findByText('₹5.9L', {}, { timeout: 4000 })).toBeInTheDocument()

    const rows = within(screen.getByRole('list', { name: 'Top suppliers' })).getAllByRole('button')
    expect(rows[0].textContent).toMatch(/\d+%(HIGH|MEDIUM|LOW)/)          // score + model level, not "High risk"
    expect(rows[0].textContent).not.toMatch(/ risk/)
    await user.click(rows[0])
    expect(await screen.findByText('Risk signals')).toBeInTheDocument()
    expect(screen.getByRole('list', { name: 'Key risk factors' })).toBeInTheDocument()
  } finally {
    await api.reset()                                                       // leave the backend on the default demo
  }
})

test('ITC Risk Radar: demo has no risk scores and keeps its headline numbers', async () => {
  const user = userEvent.setup()
  render(<App />)
  await user.click(screen.getByRole('button', { name: 'Analyze Data' }))
  await screen.findByText('Potential ITC Exposure', {}, { timeout: 8000 })
  expect(await screen.findByText('ITC Risk Radar')).toBeInTheDocument()
  expect(screen.getByText('Where should you investigate first?')).toBeInTheDocument()
  expect(await screen.findByText('₹4.2L', {}, { timeout: 4000 })).toBeInTheDocument()
  const rows = within(screen.getByRole('list', { name: 'ITC Risk Radar suppliers' })).getAllByRole('button')
  expect(rows.length).toBe(5)
  expect(rows[0].textContent).toMatch(/Sundaram Steel Traders/)
  expect(rows[0].textContent).toMatch(/n\/a/)                            // no invented model score
  expect(rows[0].textContent).toMatch(/HIGH/)
  expect(screen.getByText(/Supplier Risk needs at least 5 months/)).toBeInTheDocument()
  await user.click(rows[0])
  expect(await screen.findByText('Why is this supplier prioritized?')).toBeInTheDocument()
  expect(screen.getByText('Investigation Priority')).toBeInTheDocument()
  expect(screen.getByText('Not available')).toBeInTheDocument()
})

test('ITC Risk Radar on the realistic dataset: exposure + risk + priority, same order as the API', async () => {
  const user = userEvent.setup()
  await api.reset('realistic')
  try {
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Analyze Data' }))
    await screen.findByText('Potential ITC Exposure', {}, { timeout: 8000 })
    await screen.findByText('ITC Risk Radar')
    const api5 = (await api.priority(5)).suppliers
    const rows = within(screen.getByRole('list', { name: 'ITC Risk Radar suppliers' })).getAllByRole('button')
    expect(rows.map((r) => r.querySelector('strong').textContent)).toEqual(api5.map((s) => s.supplier_name))
    expect(rows[0].textContent).toMatch(/Lotus Heavy Machinery/)
    expect(rows[0].textContent).toMatch(/\d+%/)
    expect(rows[0].textContent).toMatch(/HIGH/)
    expect(screen.queryByText(/Supplier Risk needs at least 5 months/)).not.toBeInTheDocument()
    await user.click(rows[0])
    expect(await screen.findByText('Why is this supplier prioritized?')).toBeInTheDocument()
    const why = screen.getByRole('list', { name: 'Why this supplier is prioritized' })
    expect(within(why).getAllByRole('listitem').length).toBeGreaterThanOrEqual(4)
    expect(why.textContent).toMatch(/potential ITC exposure/)
    expect(why.textContent).toMatch(/affected invoices/)
    expect(why.textContent).toMatch(/[Rr]ecent issue rate|flagged/)
  } finally {
    await api.reset()
  }
})

test('Sample data panel switches backend data and the active-source chip reflects it', async () => {
  const user = userEvent.setup()
  await api.reset()
  try {
    render(<App />)
    await screen.findByRole('radio', { name: /Demo Dataset/ })
    expect(screen.getByRole('radio', { name: /Demo Dataset/ })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByText('Synthetic / Sample Data')).toBeInTheDocument()
    await user.click(screen.getByRole('radio', { name: /Realistic Sample Dataset/ }))
    await waitFor(async () => expect((await api.health()).source).toBe('realistic'))
    expect(screen.getByRole('radio', { name: /Realistic Sample Dataset/ })).toHaveAttribute('aria-checked', 'true')
    expect(screen.getByLabelText('Active data source').textContent).toMatch(/Realistic Sample Dataset \(synthetic\)/)
    await user.click(screen.getByRole('button', { name: 'Analyze Data' }))
    expect(await screen.findByText('₹5.9L', {}, { timeout: 8000 })).toBeInTheDocument()
    expect(screen.getByText('Synthetic sample data')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Change data' }))
    await user.click(await screen.findByRole('radio', { name: /Demo Dataset/ }))
    await waitFor(async () => expect((await api.health()).source).toBe('sample'))
    expect(screen.getByRole('button', { name: 'Analyze Data' })).toBeInTheDocument()
    expect(screen.queryByText('ITC Risk Radar')).not.toBeInTheDocument()
  } finally {
    await api.reset()
  }
})

// Phase 6: AI Investigation Brief. The test backend has no LLM key, so the real endpoint returns the evidence-based fallback;
// the "llm" success state is exercised by stubbing the API call (no live LLM in tests).
test('AI Investigation Brief: loading -> evidence-based fallback on supplier detail; explain-this-issue on invoice', async () => {
  const user = userEvent.setup()
  await api.reset()
  render(<App />)
  await user.click(screen.getByRole('button', { name: 'Analyze Data' }))
  await screen.findByText('Potential ITC Exposure', {}, { timeout: 8000 })
  const rows = within(screen.getByRole('list', { name: 'ITC Risk Radar suppliers' })).getAllByRole('button')
  await user.click(rows[0])
  expect(await screen.findByText('AI Investigation Brief')).toBeInTheDocument()
  expect(screen.queryByText('Why flagged')).not.toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Generate AI Brief' }))
  expect(await screen.findByRole('status')).toHaveTextContent(/Preparing the brief|Evidence-based brief/)
  expect(await screen.findByText('Evidence-based brief', {}, { timeout: 8000 })).toBeInTheDocument()
  expect(screen.getByText(/No LLM API key/)).toBeInTheDocument()
  expect(screen.getByRole('list', { name: 'Why flagged' }).textContent).toMatch(/potential ITC exposure/)
  expect(screen.getByRole('list', { name: 'What to verify' }).textContent).toMatch(/Missing in portal/)
  const draft = screen.getByLabelText('Draft supplier follow-up')
  expect(draft.textContent).toMatch(/^Subject:/)
  expect(draft.textContent).toMatch(/Dear Sundaram Steel Traders team/)
  expect(draft.textContent).toMatch(/SST\/26-27\//)
  expect(draft.textContent).not.toMatch(/fraud|violation|non-compliant/i)

  // invoice detail: "Explain this issue"
  const invRows = (await screen.findAllByText(/\/26-27\//)).map((n) => n.closest('tr')).filter(Boolean)
  await user.click(invRows[0])
  expect(await screen.findByText('AI explanation')).toBeInTheDocument()
  await user.click(screen.getByRole('button', { name: 'Explain this issue' }))
  expect(await screen.findByText('Evidence-based brief', {}, { timeout: 8000 })).toBeInTheDocument()
  expect(screen.getByRole('list', { name: 'Why flagged' }).textContent).toMatch(/Reconciliation status/)
})

test('AI Investigation Brief: success state when the backend reports an LLM-written brief; error state on failure', async () => {
  const user = userEvent.setup()
  await api.reset()
  const spy = vi.spyOn(api, 'aiBrief')
  try {
    render(<App />)
    await user.click(screen.getByRole('button', { name: 'Analyze Data' }))
    await screen.findByText('Potential ITC Exposure', {}, { timeout: 8000 })
    await user.click(within(screen.getByRole('list', { name: 'ITC Risk Radar suppliers' })).getAllByRole('button')[0])
    await screen.findByText('AI Investigation Brief')

    spy.mockRejectedValueOnce(new Error('Request failed (503)'))
    await user.click(screen.getByRole('button', { name: 'Generate AI Brief' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(/Could not generate the brief: Request failed \(503\)/)

    spy.mockResolvedValueOnce({
      source: 'llm', provider: 'anthropic', model: 'test-model', fallback_reason: null, disclaimer: 'Test disclaimer.',
      brief: { why_flagged: ['Reason one'], what_to_verify: ['Check one'], draft_followup: 'Subject: Hello\n\nDear team, please confirm.' },
    })
    await user.click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('AI-generated')).toBeInTheDocument()
    expect(screen.getByText(/anthropic · test-model/)).toBeInTheDocument()
    expect(screen.getByText('Reason one')).toBeInTheDocument()
    expect(screen.getByText('Check one')).toBeInTheDocument()
    expect(screen.getByLabelText('Draft supplier follow-up').textContent).toMatch(/^Subject: Hello/)
    expect(screen.getByText('Test disclaimer.')).toBeInTheDocument()
    expect(spy).toHaveBeenCalledWith({ supplier_id: expect.any(String) })
  } finally {
    spy.mockRestore()
  }
})

// Post-Phase-8: user data upload (real backend; the demo_upload_data files are read from disk)
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
const demoFile = (name) => new File([readFileSync(resolve(process.cwd(), '..', 'demo_upload_data', name))], name, { type: 'text/csv' })

// jsdom's FormData is not accepted by Node's fetch (the request never completes), so in this environment api.upload is replaced by a
// hand-built multipart request to the SAME real backend. The browser-native FormData path is covered by the headless Chromium walkthrough.
beforeAll(() => {
  vi.spyOn(api, 'upload').mockImplementation(async (purchaseFile, portalFile) => {
    const boundary = '----itcshield' + Date.now()
    const part = async (name, f) => `--${boundary}\r\nContent-Disposition: form-data; name="${name}"; filename="${f.name}"\r\nContent-Type: text/csv\r\n\r\n${await f.text()}\r\n`
    const body = (await part('purchase_file', purchaseFile)) + (await part('portal_file', portalFile)) + `--${boundary}--\r\n`
    const res = await fetch('http://127.0.0.1:8000/api/upload', { method: 'POST', headers: { 'Content-Type': `multipart/form-data; boundary=${boundary}` }, body })
    const json = await res.json()
    if (!res.ok) throw new Error(json.detail)
    return json
  })
})

test('User data: upload the demo_upload_data files, analyze, and browse the results; sample datasets stay separate', async () => {
  const user = userEvent.setup()
  await api.reset()
  try {
    render(<App />)
    await screen.findByText('User data')
    expect(screen.queryByRole('button', { name: 'Analyze uploaded data' })).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Upload & validate' })).toBeDisabled()
    await user.upload(screen.getByLabelText('Upload purchase register'), demoFile('sample_purchase_register.csv'))
    await user.upload(screen.getByLabelText('Upload portal data'), demoFile('sample_portal_data.csv'))
    expect(screen.getByLabelText('Upload purchase register').closest('label').textContent).toMatch(/sample_purchase_register\.csv/)
    expect(screen.getByLabelText('Upload portal data').closest('label').textContent).toMatch(/sample_portal_data\.csv/)
    await user.click(screen.getByRole('button', { name: 'Upload & validate' }))
    expect(await screen.findByRole('status', {}, { timeout: 8000 })).toHaveTextContent(/Files accepted.*24 purchase rows.*22 portal rows/)
    expect(screen.getByLabelText('Active data source').textContent).toMatch(/Your uploaded data/)
    await user.click(screen.getByRole('button', { name: 'Analyze uploaded data' }))
    expect(await screen.findByText('₹49.7K', {}, { timeout: 8000 })).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Your uploaded data' })).toBeInTheDocument()
    expect(screen.queryByText('Synthetic sample data')).not.toBeInTheDocument()
    const rows = within(screen.getByRole('list', { name: 'ITC Risk Radar suppliers' })).getAllByRole('button')
    expect(rows[0].textContent).toMatch(/Arvind Steel Industries/)
    expect(rows[0].textContent).toMatch(/HIGH/)
    await user.click(rows[0])
    expect(await screen.findByText('Why is this supplier prioritized?')).toBeInTheDocument()
    const invRows = (await screen.findAllByText(/ASI\/26-27\//)).map((n) => n.closest('tr')).filter(Boolean)
    await user.click(invRows[0])
    expect(await screen.findByText('Purchase register vs portal')).toBeInTheDocument()
    expect(screen.getByText('Needs review')).toBeInTheDocument()
    await user.click(screen.getByRole('button', { name: 'Explain this issue' }))
    expect(await screen.findByText('Evidence-based brief', {}, { timeout: 8000 })).toBeInTheDocument()
    // back to the synthetic sample: the demo numbers are untouched
    await user.click(screen.getByRole('button', { name: 'Dashboard' }))
    await user.click(await screen.findByRole('button', { name: 'Change data' }))
    await user.click(await screen.findByRole('radio', { name: /Demo Dataset/ }))
    await waitFor(async () => expect((await api.health()).source).toBe('sample'))
    await user.click(screen.getByRole('button', { name: 'Analyze Data' }))
    expect(await screen.findByText('₹4.2L', {}, { timeout: 8000 })).toBeInTheDocument()
  } finally {
    await api.reset()
  }
})

test('User data: a file with missing columns shows a clear validation error and nothing is analyzed', async () => {
  const user = userEvent.setup()
  await api.reset()
  try {
    render(<App />)
    await screen.findByText('User data')
    const bad = new File(['Supplier GSTIN,Invoice No,Invoice Date,IGST\n27AAACA1111A1Z5,A-1,2026-07-01,18\n'], 'bad.csv', { type: 'text/csv' })
    await user.upload(screen.getByLabelText('Upload purchase register'), bad)
    await user.upload(screen.getByLabelText('Upload portal data'), demoFile('sample_portal_data.csv'))
    await user.click(screen.getByRole('button', { name: 'Upload & validate' }))
    expect(await screen.findByRole('alert', {}, { timeout: 8000 })).toHaveTextContent(/missing columns: taxable_value/)
    expect(screen.queryByRole('button', { name: 'Analyze uploaded data' })).not.toBeInTheDocument()
    expect((await api.health()).source).toBe('sample')
  } finally {
    await api.reset()
  }
})
