import { useState, type FormEvent } from 'react'
import { FileUp } from 'lucide-react'
import { useWork } from '../components/Workspace'
import { SubmissionReceipt } from '../components/SubmissionReceipt'
import { Busy, Card, Dialog, Empty, Field, Heading, Notice } from '../components/ui'
import type { Submission, SubmissionRequest } from '../services/contracts'
import { href } from '../routes'

export function Submit() {
  const { user, data, service, reload } = useWork()
  const [source, setSource] = useState<Submission['source']>('CSV')
  const [sample, setSample] = useState(false),
    [selected, setSelected] = useState<string[]>([])
  const [alias, setAlias] = useState(''),
    [file, setFile] = useState<File | null>(null)
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [id, setId] = useState(''),
    [receipt, setReceipt] = useState('')
  const book = data.workbooks[0]
  const receiptItem = data.submissions.find((s) => s.id === receipt)
  const sampleCsv = data.submissions.find((s) => s.origin === 'Seeded sample' && s.source === 'CSV')
  function clearFeedback() {
    setError('')
    setId('')
  }
  async function send(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = e.currentTarget
    clearFeedback()
    let request: SubmissionRequest
    if (source === 'PostgreSQL') request = { kind: 'postgres', aliasId: alias }
    else if (source === 'Excel' && sample && book)
      request = { kind: 'workbook', workbookId: book.id, selected }
    else if (file) request = file.name
    else {
      setError('Choose a CSV or XLSX file first.')
      return
    }
    setBusy(true)
    try {
      const ref = await service.submit(user, request)
      reload()
      setId(ref)
      setFile(null)
      const input = form.querySelector<HTMLInputElement>('input[type=file]')
      if (input) input.value = ''
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Heading
        title="Submit data"
        sub={'Register a retail source for ' + user.businessName + '.'}
        action={
          sampleCsv && (
            <button className="btn secondary" onClick={() => setReceipt(sampleCsv.id)}>
              View sample CSV receipt
            </button>
          )
        }
      />
      <Card title="New submission" icon={FileUp}>
        <form className="padded form-layout narrow" onSubmit={send}>
          <Field label="Source type">
            <select
              disabled={busy}
              value={source}
              onChange={(e) => {
                setSource(e.target.value as Submission['source'])
                setFile(null)
                setSample(false)
                clearFeedback()
              }}
            >
              <option>CSV</option>
              <option>Excel</option>
              <option>PostgreSQL</option>
            </select>
          </Field>
          {source === 'Excel' && book && (
            <Field label="Workbook source">
              <select
                disabled={busy}
                value={sample ? 'sample' : 'file'}
                onChange={(e) => {
                  setSample(e.target.value === 'sample')
                  setSelected(book.sheets.map((s) => s.name))
                  setFile(null)
                  clearFeedback()
                }}
              >
                <option value="file">Choose a local workbook</option>
                <option value="sample">{book.filename} · demonstration sample</option>
              </select>
            </Field>
          )}
          {source === 'PostgreSQL' ? (
            <>
              <Field label="Configured demonstration source">
                <select
                  disabled={busy}
                  value={alias}
                  onChange={(e) => {
                    setAlias(e.target.value)
                    clearFeedback()
                  }}
                >
                  <option value="">Choose an authorised alias</option>
                  {data.sourceAliases.map((a) => (
                    <option key={a.id} value={a.id}>
                      {a.label} · {a.id}
                    </option>
                  ))}
                </select>
              </Field>
              {!data.sourceAliases.length && (
                <Empty>No configured demo sources for this business.</Empty>
              )}
              <p className="soft-note">
                Admin-configured demonstration sources · Connection status unverified. Registration
                does not open a database connection.
              </p>
            </>
          ) : source === 'Excel' && sample && book ? (
            <fieldset disabled={busy} className="sheet-options">
              <legend>Requested worksheets</legend>
              {book.sheets.map((s) => (
                <label key={s.name}>
                  <input
                    type="checkbox"
                    checked={selected.includes(s.name)}
                    onChange={(e) => {
                      setSelected(
                        e.target.checked
                          ? [...selected, s.name]
                          : selected.filter((n) => n !== s.name),
                      )
                      clearFeedback()
                    }}
                  />
                  {s.name} · {s.rows} sample rows
                </label>
              ))}
            </fieldset>
          ) : (
            <div className="upload-box">
              <FileUp size={26} />
              <Field
                label="Retail transaction file"
                hint="Only the filename is registered. File contents are not inspected."
              >
                <input
                  key={source}
                  disabled={busy}
                  type="file"
                  accept={source === 'CSV' ? '.csv' : '.xlsx'}
                  onChange={(e) => {
                    const chosen = e.target.files?.[0] ?? null
                    clearFeedback()
                    if (chosen && !(source === 'CSV' ? /\.csv$/i : /\.xlsx$/i).test(chosen.name)) {
                      e.target.value = ''
                      setFile(null)
                      setError('Choose a file matching the selected source type.')
                      return
                    }
                    setFile(chosen)
                  }}
                />
              </Field>
            </div>
          )}
          {error && <Notice error>{error}</Notice>}
          {id && (
            <Notice>
              {id} was added to the demonstration queue.{' '}
              <button type="button" className="table-link" onClick={() => setReceipt(id)}>
                View receipt
              </button>{' '}
              · <a href={href('Seller', 'status')}>View submission status</a>
            </Notice>
          )}
          <button className="btn primary" disabled={busy || (source === 'PostgreSQL' && !alias)}>
            {busy ? (
              <Busy>Submitting…</Busy>
            ) : (
              <>
                <FileUp size={16} />
                Submit data
              </>
            )}
          </button>
        </form>
      </Card>
      {receiptItem && (
        <Dialog title={receiptItem.id} onClose={() => setReceipt('')}>
          <SubmissionReceipt item={receiptItem} />
        </Dialog>
      )}
    </>
  )
}
