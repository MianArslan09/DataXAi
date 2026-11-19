import { useState } from 'react'
import { useWork } from '../components/Workspace'
import { Badge, Card, Empty, Field, Table } from '../components/ui'
import { money } from '../services/contracts'
import {
  assessmentComparison,
  assessmentCondition,
  countDetectionAlerts,
  detectionMethods,
  detectionOutcome,
  detectionOutcomes,
  type DetectionResult,
} from '../services/detection'

function EvidenceSummary({
  result,
  onViewEvidence,
}: {
  result: DetectionResult
  onViewEvidence: (sourceRow: string) => void
}) {
  const { assessment, order } = result
  const condition = assessmentCondition(assessment)
  return (
    <Card
      title={'Selected result · ' + result.id}
      action={<Badge>{detectionOutcome(assessment)}</Badge>}
    >
      <div className="padded stack">
        <div>
          <strong>
            {result.groupId} · {result.sourceRowReference}
          </strong>
          <p>{result.reason}</p>
        </div>
        <div>
          <strong>
            {assessment.method === 'Business Rules'
              ? 'Fixture screening condition'
              : assessment.method}
          </strong>
          <p>{condition}</p>
          {assessment.method === 'Z-score' && 'thresholdVersion' in assessment && (
            <small>Illustrative threshold from {assessment.thresholdVersion}</small>
          )}
          {assessment.method === 'Isolation Forest' && (
            <small>Scikit-learn decision score convention</small>
          )}
        </div>
        {order && (
          <div>
            <strong>
              {result.invoice} · {order.product}
            </strong>
            <p>
              {order.reference} · {order.quantity} units
            </p>
            <p>
              Submitted {money(order.sourceUnit)} each · {money(order.quantity * order.sourceUnit)}{' '}
              total
            </p>
            <p>
              Order supports {money(order.supportedUnit)} each ·{' '}
              {money(order.quantity * order.supportedUnit)} total
            </p>
            <p>
              Historical difference (supported less submitted):{' '}
              {money(order.quantity * (order.supportedUnit - order.sourceUnit))}
            </p>
          </div>
        )}
        <small>
          Alerts identify records for review. Any correction requires separate authorisation.
        </small>
        <div className="action-row">
          <button
            className="btn secondary"
            onClick={() => onViewEvidence(result.sourceRowReference)}
          >
            View Evidence
          </button>
        </div>
      </div>
    </Card>
  )
}

export function AnomalyDetection({
  submissionId,
  onViewEvidence,
}: {
  submissionId: string
  onViewEvidence: (sourceRow: string) => void
}) {
  const { user, data } = useWork()
  const [method, setMethod] = useState('')
  const [status, setStatus] = useState('')
  const [selection, setSelection] = useState('')
  const submission = data.submissions.find((item) => item.id === submissionId)
  const results = data.detectionEvidence.filter((result) => result.submissionId === submissionId)
  if (!submission || !results.length) {
    return (
      <Card title="Anomaly Detection" action={<Badge>Unavailable</Badge>}>
        <Empty>
          {user.businessName}
          {submission && ' · ' + submission.id} · No demonstration detection evidence available.
        </Empty>
      </Card>
    )
  }
  const visible = results.filter(
    (result) =>
      (!method || result.assessment.method === method) &&
      (!status || detectionOutcome(result.assessment) === status),
  )
  const counts = countDetectionAlerts(visible)
  const selected = visible.find((result) => result.id === selection) ?? visible[0]
  const summary = detectionMethods.map((name) => {
    const matches = visible.filter((result) => result.assessment.method === name)
    return [
      name,
      ...detectionOutcomes.map(
        (outcome) =>
          matches.filter((result) => detectionOutcome(result.assessment) === outcome).length,
      ),
    ]
  })
  return (
    <div className="stack detection">
      <Card title={'Anomaly Detection · ' + submission.id}>
        <div className="padded">
          <strong>
            {user.businessName} · {submission.filename}
          </strong>
          <p>
            Demonstration evidence. Scores and observations are supplied by fixtures. No detector or
            model ran.
          </p>
        </div>
        <div className="filters">
          <Field label="Detection method">
            <select value={method} onChange={(event) => setMethod(event.target.value)}>
              <option value="">All methods</option>
              {detectionMethods.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
          </Field>
          <Field label="Evaluation status">
            <select value={status} onChange={(event) => setStatus(event.target.value)}>
              <option value="">All statuses</option>
              {detectionOutcomes.map((name) => (
                <option key={name}>{name}</option>
              ))}
            </select>
          </Field>
        </div>
        <div className="padded" role="status">
          <strong>{method || status ? 'Filtered totals' : 'All-method totals'}</strong>
          {' · '}
          {counts.alerts} method alerts · {counts.rows} distinct affected rows
        </div>
        <div className="detection-table">
          <Table headers={['Method', ...detectionOutcomes]} rows={summary} />
        </div>
      </Card>
      <div className="wide-grid">
        <Card title="Method results">
          <div className="detection-table">
            <Table
              headers={[
                'Finding / illustrative row',
                'Method',
                'Observation / score',
                'Evaluation',
              ]}
              rows={visible.map((result) => [
                <button
                  className="table-link"
                  aria-label={'Inspect ' + result.groupId + ', ' + result.assessment.method}
                  aria-pressed={selected?.id === result.id}
                  onClick={() => setSelection(result.id)}
                >
                  {result.groupId}
                  <small>{result.sourceRowReference}</small>
                </button>,
                result.assessment.method,
                <span>
                  {result.field} ·{' '}
                  {result.field === 'Price' ? money(result.observed) : result.observed}
                  <small>{assessmentComparison(result)}</small>
                </span>,
                <Badge>{detectionOutcome(result.assessment)}</Badge>,
              ])}
              empty="No method results match these filters."
            />
          </div>
        </Card>
        {selected && <EvidenceSummary result={selected} onViewEvidence={onViewEvidence} />}
      </div>
    </div>
  )
}
