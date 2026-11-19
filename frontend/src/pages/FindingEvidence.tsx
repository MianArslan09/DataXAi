import { useWork } from '../components/Workspace'
import { Badge, Card, Empty, Field, Table } from '../components/ui'
import { money, type FindingEvidenceDetail, type RepairEvidence } from '../services/contracts'
import {
  assessmentComparison,
  assessmentCondition,
  detectionOutcome,
  type DetectionResult,
} from '../services/detection'

function observation(result: DetectionResult) {
  return result.field === 'Price' ? money(result.observed) : String(result.observed)
}

function SupportingOrder({ order, price }: { order: Readonly<RepairEvidence>; price: number }) {
  const submitted = order.quantity * price
  const supported = order.quantity * order.supportedUnit
  return (
    <Card title="Supporting order">
      <div className="padded">
        <strong>
          {order.reference} · {order.id}
        </strong>
        <p>
          {order.product} · {order.quantity} units
        </p>
      </div>
      <Table
        headers={['Value', 'Submitted', 'Supporting order']}
        rows={[
          ['Unit price', money(price), money(order.supportedUnit)],
          ['Line total (quantity × price)', money(submitted), money(supported)],
        ]}
      />
      <div className="padded">
        <strong>Supported minus submitted: {money(supported - submitted).replace('-', '−')}</strong>
        <p>The order supplies a comparison for investigation. Its values require review.</p>
      </div>
    </Card>
  )
}

export function FindingEvidence({
  submissionId,
  selectedRow,
  onSelect,
}: {
  submissionId: string
  selectedRow: string | null
  onSelect: (row: string) => void
}) {
  const { data, user, service } = useWork()
  const submission = data.submissions.find(
    (s) => s.id === submissionId && s.businessId === user.businessId,
  )
  const groupsByRow = new Map<string, Set<string>>()
  for (const result of data.detectionEvidence) {
    if (result.businessId !== user.businessId || result.submissionId !== submissionId) continue
    const groups = groupsByRow.get(result.sourceRowReference) ?? new Set<string>()
    groups.add(result.groupId)
    groupsByRow.set(result.sourceRowReference, groups)
  }
  const options = Array.from(groupsByRow.entries())
  const row = selectedRow ?? options[0]?.[0]
  if (!user.businessId || !submission || !row || !groupsByRow.has(row)) {
    return (
      <Card title="Finding evidence" action={<Badge>Unavailable</Badge>}>
        <Empty>No finding evidence is available for this selection in your workspace.</Empty>
      </Card>
    )
  }

  let detail: FindingEvidenceDetail
  try {
    detail = service.readFindingEvidence(user, user.businessId, submissionId, row)
  } catch {
    return (
      <Card title="Finding evidence" action={<Badge>Unavailable</Badge>}>
        <Empty>Evidence unavailable for this workspace.</Empty>
      </Card>
    )
  }

  const { results, relatedChecks } = detail
  const invoices = [
    ...new Set(results.flatMap((result) => (result.invoice ? [result.invoice] : []))),
  ]
  const fields = [...new Set(results.map((result) => result.field + ' · ' + observation(result)))]
  const priceLine = results.find((result) => result.field === 'Price' && result.order)

  return (
    <div className="stack detection">
      <div className="wide-grid">
        <Card title="Finding evidence">
          <div className="padded">
            <Field label="Finding group / illustrative source row">
              <select value={row} onChange={(event) => onSelect(event.target.value)}>
                {options.map(([reference, groups]) => (
                  <option key={reference} value={reference}>
                    {Array.from(groups).join(', ')} · {reference}
                  </option>
                ))}
              </select>
            </Field>
            <dl className="details">
              <dt>Business</dt>
              <dd>
                {user.businessName} ({user.businessId})
              </dd>
              <dt>Submission</dt>
              <dd>
                {submission.id} · {submission.filename}
              </dd>
              <dt>Invoice</dt>
              <dd>{invoices.join(', ') || 'Unavailable'}</dd>
              <dt>Original observation</dt>
              <dd>{fields.join(' / ')}</dd>
            </dl>
          </div>
        </Card>
        {priceLine?.order ? (
          <SupportingOrder price={priceLine.observed} order={priceLine.order} />
        ) : (
          <Card title="Supporting order" action={<Badge>Unavailable</Badge>}>
            <Empty>No supporting-order document is supplied for this row.</Empty>
          </Card>
        )}
      </div>

      <Card title="Method assessments">
        <div className="detection-table">
          <Table
            headers={[
              'Method / result',
              'Source observation',
              'Assessment condition',
              'Outcome and evidence',
            ]}
            rows={results.map((result) => [
              <span>
                {result.assessment.method}
                <small>{result.id}</small>
              </span>,
              <span>
                {result.field}
                <small>{observation(result)}</small>
              </span>,
              <span>
                <strong>{assessmentComparison(result).replace(/-(?=\d)/g, '−')}</strong>
                <small>{assessmentCondition(result.assessment)}</small>
                {'thresholdVersion' in result.assessment && (
                  <small>Illustrative threshold: {result.assessment.thresholdVersion}</small>
                )}
              </span>,
              <span>
                <Badge>{detectionOutcome(result.assessment)}</Badge>
                <small>{result.reason}</small>
              </span>,
            ])}
          />
        </div>
      </Card>

      <Card title="Evidence provenance">
        <div className="padded">
          <p>
            Source observations, scores and row references come from demonstration fixtures.
            Persisted staging lineage and execution-time rule binding are unavailable. Quarantine
            Review handles correction decisions separately. This evidence supplies no quarantine
            decision link.
          </p>
          {relatedChecks.length ? (
            relatedChecks.map(({ sample, check }) => (
              <p key={sample + check.id}>
                <strong>Related validation · {check.id}</strong> · {sample} · {check.outcome}
                <br />
                {check.field}: {check.observed === null ? 'Missing (null)' : check.observed}.{' '}
                {check.reason}
              </p>
            ))
          ) : (
            <p>Related row-level validation evidence: unavailable.</p>
          )}
        </div>
      </Card>
    </div>
  )
}
