import { useWork } from './Workspace'
import { Badge, Table } from './ui'
import { date, number, type BatchEvent, type Submission } from '../services/contracts'

const traversalLabel: Record<BatchEvent['state'], string> = {
  Entered: 'Entered · in progress',
  Interrupted: 'Entered · interrupted before completion',
  Completed: 'Entered · completed',
  Failed: 'Entered · stopped on failure',
}

export function SubmissionReceipt({ item }: { item: Submission }) {
  const { user, data } = useWork()
  const execution = data.executions.find((e) => e.submissionId === item.id)
  const scope = item.scope
  const requested =
    scope.kind === 'workbook'
      ? scope.selected.join(', ')
      : scope.kind === 'postgres'
        ? 'Configured source ' + scope.aliasId
        : 'Entire submitted file'
  return (
    <div className="submission-receipt">
      <div className="card-head">
        <div>
          <h3>{item.filename}</h3>
          <small>
            {item.origin} · {item.id}
          </small>
        </div>
        <Badge>Registered</Badge>
      </div>
      <div className="padded">
        <dl className="details">
          <dt>Business</dt>
          <dd>{user.businessName}</dd>
          <dt>Source type</dt>
          <dd>{item.source}</dd>
          <dt>Requested scope</dt>
          <dd>{requested}</dd>
          <dt>Submitted</dt>
          <dd>{date(item.submitted, true)} PKT</dd>
          <dt>Declared sample rows</dt>
          <dd>{item.rows === null ? 'Unknown' : number(item.rows)}</dd>
          <dt>Execution status</dt>
          <dd>{execution?.stage ?? 'Not Requested'}</dd>
          <dt>Overall submission completion</dt>
          <dd>{execution?.scopeOutcome ?? 'Unverified'}</dd>
        </dl>
        {scope.kind === 'workbook' ? (
          <>
            <h3>Worksheet coverage</h3>
            <p className="muted">Ordered sample manifest · simulated traversal</p>
            <Table
              headers={['Worksheet', 'Sample rows', 'Selected', 'Traversal evidence']}
              rows={scope.sheets.map((s) => {
                const selected = scope.selected.includes(s.name)
                const events = execution?.batchEvents.filter((e) => e.sheet === s.name) ?? []
                const latest = events.at(-1)
                const state = latest
                  ? traversalLabel[latest.state]
                  : selected
                    ? 'No entry recorded'
                    : 'Outside requested scope'
                return [s.name, number(s.rows), selected ? 'Yes' : 'No', state]
              })}
            />
          </>
        ) : (
          <div className="soft-note">
            {item.source === 'Excel'
              ? 'Worksheet metadata unavailable. This file has not been inspected.'
              : item.source === 'PostgreSQL'
                ? 'Demo alias only. Connection and extraction evidence unavailable.'
                : 'Registration records the request. Extraction evidence is unavailable.'}
          </div>
        )}
      </div>
    </div>
  )
}
