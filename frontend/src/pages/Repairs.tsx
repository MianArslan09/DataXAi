import { useEffect, useState } from 'react'
import {
  BookOpenText,
  Check,
  ChevronRight,
  CircleCheck,
  ClipboardCheck,
  Clock3,
  FileCheck2,
  FileSearch,
  FileText,
  Layers3,
  ListFilter,
  MessageSquareText,
  Scale,
  ShieldAlert,
  Sparkles,
} from 'lucide-react'
import { useWork } from '../components/Workspace'
import {
  Badge,
  Busy,
  Card,
  Dialog,
  Empty,
  EvidenceTable,
  Field,
  Heading,
  Impact,
  Notice,
  SearchBox,
} from '../components/ui'
import {
  date,
  money,
  reviewActions,
  reviewStatus,
  type Decision,
  type QuarantineRecord,
  type RepairEvidence,
} from '../services/contracts'

function EvidenceNote({ e }: { e: RepairEvidence }) {
  return (
    <div className="evidence-note">
      <FileCheck2 size={19} />
      <div>
        <strong>{e.reference}</strong>
        <p>
          Original order evidence supports {e.quantity} × {e.product} at {money(e.supportedUnit)}{' '}
          per unit.
        </p>
      </div>
    </div>
  )
}
function DecisionHistory({ record }: { record: QuarantineRecord }) {
  return (
    <section className="local-review-history" aria-label="Local Review Decision History">
      <h3>Local Review Decision History</h3>
      {!record.decisionHistory.length ? (
        <p>No local review events recorded for {record.id}.</p>
      ) : (
        <ol>
          {record.decisionHistory.map((event) => {
            const e = event.evidence
            const submitted = e.quantity * e.sourceUnit
            const supported = e.quantity * e.supportedUnit
            return (
              <li key={event.sequence} value={event.sequence}>
                <strong>{event.decision}</strong>
                <p>
                  {event.actor} ({event.actorId}) ·{' '}
                  <time dateTime={event.at} title={event.at}>
                    {date(event.at, true)} PKT
                  </time>
                </p>
                <details>
                  <summary>
                    {e.id} · {e.reference}
                  </summary>
                  <p>
                    Snapshot at confirmation · {event.businessId} / {event.recordId}
                  </p>
                  <p>
                    {e.quantity} × {e.product} · Submitted {money(e.sourceUnit)} each (
                    {money(submitted)} total)
                  </p>
                  <p>
                    Order supports {money(e.supportedUnit)} each ({money(supported)} total)
                  </p>
                  <p>Supported minus submitted: {money(supported - submitted).replace('-', '−')}</p>
                </details>
              </li>
            )
          })}
        </ol>
      )}
    </section>
  )
}
const consequences: Record<Decision, string> = {
  'Confirm Original': 'Keep the source unit price in the local review outcome.',
  'Authorise Supported Correction':
    'Authorise the order-supported unit price in this demonstration. The source price stays unchanged.',
  'Retain for Review': 'Keep this record open for further examination.',
}
export function Review() {
  const { data, service, user, reload } = useWork()
  const [selected, setSelected] = useState(data.quarantine[0]?.id ?? ''),
    [query, setQuery] = useState(''),
    [pending, setPending] = useState<{
      recordId: string
      businessId: string
      decision: Decision
    } | null>(null),
    [notice, setNotice] = useState<{ text: string; error?: boolean } | null>(null)
  const filtered = data.quarantine.filter((r) =>
    (r.invoice + ' ' + r.evidence.product).toLowerCase().includes(query.toLowerCase()),
  )
  const r = filtered.find((r) => r.id === selected) ?? filtered[0]
  const editable = r && (!r.resolution || r.resolution.decision === 'Retain for Review')
  const decision =
    pending && pending.recordId === r?.id && pending.businessId === user.businessId
      ? pending.decision
      : null
  function confirm() {
    if (!r || !decision) return
    try {
      service.decide(user, r.id, decision)
      setNotice({
        text: 'Decision recorded in the local simulation. No PostgreSQL resolution was persisted.',
      })
    } catch (err) {
      setNotice({ text: (err as Error).message, error: true })
    }
    setPending(null)
    reload()
  }
  return (
    <>
      <Heading
        title="Quarantine review"
        sub="Compare source values with order evidence before recording a decision."
      />
      <div className="review-layout">
        <div className="stack review-side">
          <Card title="Review queue" icon={ListFilter}>
            <div className="queue-search">
              <SearchBox
                value={query}
                onChange={(value) => {
                  setQuery(value)
                  setNotice(null)
                }}
                placeholder="Find invoice or product"
              />
            </div>
            {filtered.length ? (
              filtered.map((row) => (
                <button
                  key={row.id}
                  className={'queue-item ' + (row.id === r?.id ? 'chosen' : '')}
                  aria-pressed={row.id === r?.id}
                  onClick={() => {
                    setSelected(row.id)
                    setNotice(null)
                  }}
                >
                  <span>
                    <b>{row.invoice}</b>
                    <ChevronRight size={15} />
                  </span>
                  <small>{row.evidence.product}</small>
                  <Badge>{reviewStatus(row)}</Badge>
                </button>
              ))
            ) : (
              <Empty>No matching records.</Empty>
            )}
          </Card>
          {r && (
            <Card title="Proposed impact" icon={Scale}>
              <Impact e={r.evidence} />
            </Card>
          )}
        </div>
        {r && (
          <div className="stack">
            <Card className="evidence-card">
              <div className="record-head">
                <span className="tile-icon sand">
                  <FileSearch size={23} />
                </span>
                <div>
                  <small>QUARANTINED INVOICE</small>
                  <h2>{r.invoice}</h2>
                </div>
                <Badge>{reviewStatus(r)}</Badge>
              </div>
              <div className="record-reason">
                <b>Reason for review</b> {r.reason}
              </div>
              <EvidenceTable e={r.evidence} />
              <EvidenceNote e={r.evidence} />
            </Card>
            <Card title="Manager decision" icon={ClipboardCheck}>
              <div className="decision-area">
                {r.resolution && (
                  <p className="decision-log">
                    {r.resolution.decision} · {r.resolution.actor} · {date(r.resolution.at, true)}{' '}
                    PKT
                  </p>
                )}
                {editable && (
                  <div className="action-row">
                    {reviewActions.map((d, i) => {
                      const Icon = [Check, CircleCheck, Clock3][i]
                      return (
                        <button
                          key={d}
                          className={'btn ' + (i === 1 ? 'primary' : 'secondary')}
                          onClick={() =>
                            setPending({ recordId: r.id, businessId: r.businessId, decision: d })
                          }
                        >
                          <Icon size={15} />
                          {d}
                        </button>
                      )
                    })}
                  </div>
                )}
                {notice ? (
                  <Notice error={notice.error}>{notice.text}</Notice>
                ) : (
                  <small>
                    {r.resolution ? 'Local decision history' : 'Pre-decision scenario'} · No
                    PostgreSQL resolution is persisted.
                  </small>
                )}
                <DecisionHistory record={r} />
              </div>
            </Card>
          </div>
        )}
      </div>
      {decision && r && (
        <Dialog title={decision} onClose={() => setPending(null)}>
          <div className="padded">
            <p>
              {r.invoice} · {consequences[decision]}
            </p>
            {data.reviewConfirmation && (
              <div className="soft-note">
                Source unit price {money(r.evidence.sourceUnit)} · Supported unit price{' '}
                {money(r.evidence.supportedUnit)}. Original evidence is preserved.
              </div>
            )}
            <div className="form-actions">
              <button className="btn secondary" onClick={() => setPending(null)}>
                Cancel
              </button>
              <button className="btn primary" onClick={confirm}>
                Confirm decision
              </button>
            </div>
          </div>
        </Dialog>
      )}
    </>
  )
}
type Scenario = 'Available' | 'Unavailable' | 'Failed'

function NarrativeResponse({
  explanationId,
  scenario,
}: {
  explanationId: string
  scenario: Scenario
}) {
  const { user, service } = useWork()
  const [response, setResponse] = useState<
    { state: 'Available'; text: string } | { state: 'Loading' | 'Unavailable' | 'Failed' }
  >({ state: 'Loading' })
  useEffect(() => {
    let active = true
    service
      .narrate(user, explanationId, scenario)
      .then((result) => {
        if (active) {
          setResponse(
            result === null ? { state: 'Unavailable' } : { state: 'Available', text: result },
          )
        }
      })
      .catch(() => {
        if (active) setResponse({ state: 'Failed' })
      })
    return () => {
      active = false
    }
  }, [user, service, explanationId, scenario])
  return (
    <div aria-live="polite">
      {response.state === 'Loading' ? (
        <Busy>Loading explanation…</Busy>
      ) : response.state === 'Available' ? (
        <>
          <span className="narrative-label">
            <Sparkles size={13} />
            Simulated narrative
          </span>
          <Badge>Available</Badge>
          <p>{response.text}</p>
        </>
      ) : (
        <>
          <Badge>
            {response.state === 'Failed' ? 'Request failed' : 'Explanation Unavailable'}
          </Badge>
          <p>The recorded facts and template explanation remain available.</p>
        </>
      )}
    </div>
  )
}

export function Explanation() {
  const { data, user } = useWork()
  const [selectedId, setSelectedId] = useState('')
  const [scenario, setScenario] = useState<Scenario>('Available')
  const item = data.explanations.find((record) => record.id === selectedId) ?? data.explanations[0]
  return (
    <>
      <Heading
        title="Repair explanations"
        sub="Inspect the evidence, recorded correction and explanation for a retail invoice."
        action={
          data.explanations.length > 1 && (
            <Field label="Recorded repair snapshot">
              <select
                value={item?.id ?? ''}
                onChange={(event) => setSelectedId(event.target.value)}
              >
                {data.explanations.map((snapshot) => (
                  <option key={snapshot.id} value={snapshot.id}>
                    {snapshot.id} · {snapshot.invoice}
                  </option>
                ))}
              </select>
            </Field>
          )
        }
      />
      {!item ? (
        <Card>
          <Empty>No repair explanations for this business.</Empty>
        </Card>
      ) : (
        <>
          <div className="repair-banner">
            <span className="tile-icon">
              <Layers3 size={21} />
            </span>
            <div>
              <b>Post-commit demonstration scenario</b>
              <small>
                {item.id} · {user.businessName} ({item.businessId}) · Independent recorded snapshot
              </small>
            </div>
            <Badge>Completed</Badge>
          </div>
          <div className="repair-columns">
            <div className="stack">
              <Card>
                <div className="record-head">
                  <span className="tile-icon">
                    <FileText size={23} />
                  </span>
                  <div>
                    <small>RECORDED INVOICE</small>
                    <h2>{item.invoice}</h2>
                  </div>
                </div>
                <EvidenceTable
                  e={{ ...item.evidence, supportedUnit: item.processedUnit }}
                  processed
                />
                <EvidenceNote e={item.evidence} />
                <div className="audit-line">
                  <ShieldAlert size={15} />
                  <span>
                    Authorised by {item.actor} · Recorded{' '}
                    <time dateTime={item.at}>
                      {new Date(item.at).toLocaleString('en-GB', {
                        dateStyle: 'medium',
                        timeStyle: 'short',
                        timeZone: 'Asia/Karachi',
                      })}{' '}
                      PKT
                    </time>
                  </span>
                </div>
              </Card>
              <Card title="Analytical impact" icon={Scale}>
                <Impact e={item.evidence} processedUnit={item.processedUnit} />
              </Card>
            </div>
            <div className="stack">
              <Card title="Recorded repair facts" icon={FileCheck2}>
                <div className="fact-list">
                  <div>
                    <span>Action</span>
                    <b>Unit price corrected</b>
                  </div>
                  <div>
                    <span>Reason</span>
                    <b>
                      Order confirmation supports {money(item.evidence.supportedUnit)} per unit.
                    </b>
                  </div>
                  <div>
                    <span>Evidence</span>
                    <b>{item.evidence.id} · original source preserved</b>
                  </div>
                </div>
              </Card>
              <Card title="Template explanation" icon={BookOpenText}>
                <p className="explanation-text">{item.template}</p>
              </Card>
              <Card title="Optional Groq explanation" icon={MessageSquareText}>
                <div className="narrative">
                  <label className="scenario-select">
                    Demo response
                    <select
                      value={scenario}
                      onChange={(e) => setScenario(e.target.value as Scenario)}
                    >
                      <option>Available</option>
                      <option>Unavailable</option>
                      <option>Failed</option>
                    </select>
                  </label>
                  <NarrativeResponse
                    key={JSON.stringify([user.userId, item.businessId, item.id, scenario])}
                    explanationId={item.id}
                    scenario={scenario}
                  />
                </div>
              </Card>
            </div>
          </div>
        </>
      )}
    </>
  )
}
