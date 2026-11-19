import { useState } from 'react'
import {
  Activity,
  CheckCheck,
  CirclePlay,
  CircleAlert,
  Files,
  Inbox,
  ListChecks,
  ShieldAlert,
  Workflow,
} from 'lucide-react'
import { useWork } from '../components/Workspace'
import { Submissions } from '../components/Submissions'
import {
  Badge,
  Card,
  Empty,
  Field,
  Heading,
  LinkButton,
  Metric,
  MoreLink,
  Notice,
  Table,
} from '../components/ui'
import { date, number, reviewStatus, stageOf, type Stage } from '../services/contracts'
import { href } from '../routes'
import { Review } from './Repairs'
import { ProcessingRules } from './ProcessingRules'
import { ValidationFindings } from './ValidationFindings'
import { AnomalyDetection } from './AnomalyDetection'
import { FindingEvidence } from './FindingEvidence'

const batchTabs = [
  'Execution',
  'Processing Rules',
  'Validation Findings',
  'Anomaly Detection',
  'Finding Evidence',
] as const
const path = (slug: string) => href('Manager', slug)
function Batch({ selected }: { selected: string | null }) {
  const { user, data, service, reload } = useWork()
  const [tab, setTab] = useState(0)
  const [evidenceRow, setEvidenceRow] = useState<string | null>(null)
  const [id, setId] = useState(
      data.executions.find((e) => e.id === selected)?.submissionId ??
        selected ??
        data.submissions[0]?.id ??
        '',
    ),
    [error, setError] = useState('')
  const item = data.submissions.find((s) => s.id === id),
    execution = data.executions.find((e) => e.submissionId === id)
  const stage = execution?.stage ?? 'Not Requested'
  const entered = execution?.batchEvents.at(-1)
  const lastCompleted = execution?.batchEvents.filter((event) => event.state === 'Completed').at(-1)
  const workbook = item?.scope.kind === 'workbook'
  const caption: Record<Stage, string> = {
    Interrupted: 'Simulated traversal stopped during the remaining worksheet scope.',
    'Not Requested': 'No request has been sent.',
    'Request Received': 'Request acknowledged. Execution has not started.',
    Queued: 'The simulated worker is waiting to start.',
    Running: 'The simulated worker is processing the submission.',
    Succeeded: 'The simulated execution has finished.',
    Failed: execution?.error ?? 'The simulation failed.',
  }
  function advance(action: 'advance' | 'fail' | 'interrupt' = 'advance') {
    try {
      if (action === 'interrupt') service.interrupt(user, id)
      else service.advance(user, id, action === 'fail')
      reload()
      setError('')
    } catch (err) {
      setError((err as Error).message)
    }
  }
  const button = {
    'Not Requested': 'Request processing',
    'Request Received': 'Queue execution',
    Queued: 'Start simulation',
    Running: workbook ? 'Complete worksheet batch' : 'Complete simulation',
    Interrupted: '',
    Succeeded: '',
    Failed: '',
  }[stage]
  const steps: Stage[] = [
    'Request Received',
    'Queued',
    'Running',
    stage === 'Failed' || stage === 'Interrupted' ? stage : 'Succeeded',
  ]
  return (
    <>
      <Heading
        title="Run batch processing"
        sub={
          [
            'Select a submission and follow its execution from receipt to completion.',
            'Configure demonstration rules for ' + user.businessName + '.',
            'Inspect illustrative source and row checks for ' + user.businessName + '.',
            'Compare demonstration method results for ' + user.businessName + '.',
            'Inspect the source observations and assessments for ' + user.businessName + '.',
          ][tab]
        }
        action={
          <div className="action-row" role="tablist" aria-label="Batch workspace">
            {batchTabs.map((name, i) => {
              const active = tab === i
              return (
                <button
                  key={name}
                  id={'batch-tab-' + i}
                  role="tab"
                  aria-selected={active}
                  aria-controls={'batch-panel-' + i}
                  tabIndex={active ? 0 : -1}
                  className={'btn ' + (active ? 'primary' : 'secondary')}
                  onClick={() => setTab(i)}
                  onKeyDown={(event) => {
                    if (!['Home', 'End', 'ArrowLeft', 'ArrowRight'].includes(event.key)) return
                    event.preventDefault()
                    const count = batchTabs.length
                    const next =
                      event.key === 'Home'
                        ? 0
                        : event.key === 'End'
                          ? count - 1
                          : (i + count + (event.key === 'ArrowLeft' ? -1 : 1)) % count
                    setTab(next)
                    document.getElementById('batch-tab-' + next)?.focus()
                  }}
                >
                  {name}
                </button>
              )
            })}
          </div>
        }
      />
      <div role="tabpanel" id="batch-panel-1" aria-labelledby="batch-tab-1" hidden={tab !== 1}>
        {tab === 1 && <ProcessingRules key={user.businessId} />}
      </div>
      <div role="tabpanel" id="batch-panel-2" aria-labelledby="batch-tab-2" hidden={tab !== 2}>
        {tab === 2 && <ValidationFindings key={id} submissionId={id} />}
      </div>
      <div role="tabpanel" id="batch-panel-3" aria-labelledby="batch-tab-3" hidden={tab !== 3}>
        {tab === 3 && (
          <AnomalyDetection
            key={id}
            submissionId={id}
            onViewEvidence={(row) => {
              setEvidenceRow(row)
              setTab(4)
              document.getElementById('batch-tab-4')?.focus()
            }}
          />
        )}
      </div>
      <div role="tabpanel" id="batch-panel-4" aria-labelledby="batch-tab-4" hidden={tab !== 4}>
        {tab === 4 && (
          <FindingEvidence submissionId={id} selectedRow={evidenceRow} onSelect={setEvidenceRow} />
        )}
      </div>
      <div role="tabpanel" id="batch-panel-0" aria-labelledby="batch-tab-0" hidden={tab !== 0}>
        {!data.submissions.length ? (
          <Card>
            <Empty>No submissions available for this business.</Empty>
          </Card>
        ) : (
          <div className="wide-grid">
            <div className="stack">
              <Card title="Selected submission" icon={Inbox}>
                <div className="padded">
                  <Field label="Submission">
                    <select
                      value={id}
                      onChange={(e) => {
                        setId(e.target.value)
                        setEvidenceRow(null)
                        setError('')
                      }}
                    >
                      {!item && <option value={id}>Submission unavailable</option>}
                      {data.submissions.map((s) => (
                        <option value={s.id} key={s.id}>
                          {s.id} · {s.filename}
                        </option>
                      ))}
                    </select>
                  </Field>
                  {item && (
                    <div className="facts-strip">
                      <div>
                        <small>Source format</small>
                        <b>{item.source}</b>
                      </div>
                      <div>
                        <small>Fixture rows</small>
                        <b>{item.rows === null ? 'Unknown' : number(item.rows)}</b>
                      </div>
                      <div>
                        <small>Submitted</small>
                        <b>{date(item.submitted)}</b>
                      </div>
                    </div>
                  )}
                </div>
              </Card>
              {item && (
                <Card title="Execution control" icon={Workflow} action={<Badge>{stage}</Badge>}>
                  <div className="padded">
                    <div className="execution-id">
                      <small>EXECUTION REFERENCE</small>
                      <h2>{execution?.id ?? 'Awaiting request'}</h2>
                    </div>
                    <p aria-live="polite">{caption[stage]}</p>
                    <dl className="details">
                      <dt>Request receipt</dt>
                      <dd>{execution?.receiptId ?? 'Awaiting request'}</dd>
                      <dt>Applied rule version</dt>
                      <dd>{execution?.ruleVersion ?? 'No version supplied'}</dd>
                      <dt>Simulated rows accounted for</dt>
                      <dd>
                        {execution?.accountedRows != null
                          ? number(execution.accountedRows)
                          : 'Unavailable'}
                      </dd>
                      <dt>PostgreSQL committed rows</dt>
                      <dd>Authoritative count unavailable</dd>
                      <dt>Overall submission completion</dt>
                      <dd>{execution?.scopeOutcome ?? 'Unverified'}</dd>
                      {lastCompleted && (
                        <>
                          <dt>Last completed batch</dt>
                          <dd>
                            Batch {lastCompleted.batch} ·{' '}
                            {lastCompleted.sheet ?? 'Simulated file batch'}
                          </dd>
                        </>
                      )}
                      {workbook && entered?.state === 'Entered' && (
                        <>
                          <dt>Current worksheet</dt>
                          <dd>{entered.sheet}</dd>
                        </>
                      )}
                    </dl>
                    {error && <Notice error>{error}</Notice>}
                    {button && (
                      <div className="action-row">
                        <button className="btn primary" onClick={() => advance()}>
                          <CirclePlay size={16} />
                          {button}
                        </button>
                        {stage === 'Running' && (
                          <button
                            className="btn secondary"
                            onClick={() => advance(workbook ? 'interrupt' : 'fail')}
                          >
                            {workbook ? 'Interrupt simulation' : 'Simulate failure'}
                          </button>
                        )}
                      </div>
                    )}
                  </div>
                </Card>
              )}
            </div>
            <div className="stack">
              {!item ? (
                <Card>
                  <Empty>Submission unavailable for this workspace.</Empty>
                </Card>
              ) : stage === 'Interrupted' && execution ? (
                <Card title="Simulated batch events" icon={ListChecks}>
                  <Table
                    headers={['Batch / worksheet', 'Event', 'Accounted rows']}
                    rows={execution.batchEvents.map((e) => [
                      e.batch + ' · ' + e.sheet,
                      e.state,
                      e.rows === null ? 'Unknown' : number(e.rows),
                    ])}
                  />
                  <div className="padded">
                    <MoreLink to={path('submissions') + '?id=' + id}>
                      Inspect worksheet coverage
                    </MoreLink>
                  </div>
                </Card>
              ) : (
                <Card title="Processing timeline" icon={ListChecks}>
                  <ol className="timeline">
                    {steps.map((step, i) => {
                      const event = execution?.events.find((e) => e.stage === step)
                      return (
                        <li key={step} className={event ? 'reached' : ''}>
                          <i>
                            {event ? (
                              step === 'Failed' ? (
                                <CircleAlert size={14} />
                              ) : (
                                <CheckCheck size={14} />
                              )
                            ) : (
                              i + 1
                            )}
                          </i>
                          <div>
                            <b>{step === 'Succeeded' && !event ? 'Completion outcome' : step}</b>
                            <small>
                              {event ? date(event.at, true) + ' PKT' : 'Awaiting transition'}
                            </small>
                          </div>
                        </li>
                      )
                    })}
                  </ol>
                </Card>
              )}
              <div className="soft-note">
                <Workflow size={18} />
                <span>Manual simulation. No ETL job runs or rows are committed to PostgreSQL.</span>
              </div>
            </div>
          </div>
        )}
      </div>
    </>
  )
}
export function Manager({ slug, selected }: { slug: string; selected: string | null }) {
  const { data, user } = useWork()
  if (slug === 'batch')
    return <Batch key={JSON.stringify([user.businessId, selected])} selected={selected} />
  if (slug === 'quarantine') return <Review />
  if (slug === 'submissions')
    return (
      <>
        <Heading
          title="Submissions"
          sub="Inspect incoming files and open their processing controls."
        />
        <Submissions key={selected} initialId={selected} />
      </>
    )
  const pending = data.submissions.filter((s) => stageOf(data, s.id) === 'Not Requested').length
  const open = data.quarantine.filter(
    (q) => !q.resolution || q.resolution.decision === 'Retain for Review',
  )
  return (
    <>
      <Heading
        title="Manager dashboard"
        sub="Monitor incoming submissions, batch execution and records awaiting review."
        action={
          <LinkButton primary to={path('batch')}>
            <CirclePlay size={16} />
            Run batch processing
          </LinkButton>
        }
      />
      <div className="metrics">
        <Metric
          label="Awaiting processing"
          value={pending}
          note="Submissions ready to request"
          icon={Inbox}
        />
        <Metric
          label="Active executions"
          value={
            data.executions.filter((e) => !['Succeeded', 'Failed', 'Interrupted'].includes(e.stage))
              .length
          }
          note="Received, queued or running"
          icon={Activity}
          tone="blue"
        />
        <Metric
          label="Completed executions"
          value={data.executions.filter((e) => e.stage === 'Succeeded').length}
          note="Successful simulations"
          icon={Files}
          tone="violet"
        />
        <Metric
          label="Awaiting review"
          value={open.length}
          note="Open quarantine records"
          icon={ShieldAlert}
          tone="sand"
        />
      </div>
      <Submissions compact />
      <Card
        title="Quarantine workload"
        icon={ShieldAlert}
        action={<MoreLink to={path('quarantine')}>Open review queue</MoreLink>}
      >
        {open.length ? (
          <div className="queue-rows">
            {open.map((r) => (
              <div key={r.id}>
                <span className="tile-icon sand">
                  <ShieldAlert size={18} />
                </span>
                <span>
                  <b>{r.invoice}</b>
                  <small>{r.evidence.product} · unit price discrepancy</small>
                </span>
                <Badge>{reviewStatus(r)}</Badge>
              </div>
            ))}
          </div>
        ) : (
          <Empty>No open quarantine records.</Empty>
        )}
      </Card>
    </>
  )
}
