import { useState } from 'react'
import { useWork } from '../components/Workspace'
import { Badge, Card, Empty, Field, Table } from '../components/ui'
import { sourceHeaders, type ValidationOutcome } from '../services/contracts'

const outcomes: readonly ValidationOutcome[] = ['Passed', 'Failed', 'Not evaluated', 'Unavailable']

export function ValidationFindings({ submissionId }: { submissionId: string }) {
  const { data } = useWork()
  const evidence = data.validationEvidence.find((entry) => entry.submissionId === submissionId)
  const [variant, setVariant] = useState(0)
  const [filter, setFilter] = useState('')
  const [selected, setSelected] = useState('')
  const sample = evidence?.samples[variant]
  if (!sample || !evidence) {
    return (
      <Card title="Validation findings" action={<Badge>Unavailable</Badge>}>
        <Empty>No demonstration evidence is available for this submission in your workspace.</Empty>
      </Card>
    )
  }
  const visible = sample.checks.filter((check) => !filter || check.outcome === filter)
  const detail = visible.find((check) => check.id === selected) ?? visible[0]
  const version = data.ruleVersions.find((v) => v.id === detail?.evaluatedRuleVersion)
  const schema = sample.checks.find((check) => check.level === 'Schema')
  return (
    <div className="stack">
      <Card
        title={'Source contract · ' + submissionId}
        action={<Badge>{schema?.outcome ?? 'Unavailable'}</Badge>}
      >
        <div className="padded">
          <Field label="Demonstration sample">
            <select
              value={variant}
              onChange={(e) => {
                setVariant(Number(e.target.value))
                setSelected('')
                setFilter('')
              }}
            >
              {evidence.samples.map((entry, i) => (
                <option key={entry.name} value={i}>
                  {entry.name}
                </option>
              ))}
            </select>
          </Field>
          <dl className="details">
            <dt>Required ETL source headers</dt>
            <dd>{sourceHeaders.join(', ')}</dd>
            <dt>Observed sample headers</dt>
            <dd>{sample.observedHeaders.join(', ')}</dd>
          </dl>
          <p>{schema?.reason}</p>
        </div>
      </Card>
      <div className="wide-grid">
        <Card title="Validation findings">
          <div className="padded">
            <Field label="Filter by outcome">
              <select value={filter} onChange={(e) => setFilter(e.target.value)}>
                <option value="">All outcomes</option>
                {outcomes.map((value) => (
                  <option key={value}>{value}</option>
                ))}
              </select>
            </Field>
          </div>
          <Table
            headers={['Check', 'Level', 'Source field', 'Outcome']}
            rows={visible.map((check) => [
              <button
                className="table-link"
                aria-pressed={detail?.id === check.id}
                onClick={() => setSelected(check.id)}
              >
                {check.id}
              </button>,
              check.level,
              check.field,
              <Badge>{check.outcome}</Badge>,
            ])}
            empty="No sample checks match this outcome."
          />
        </Card>
        {detail && (
          <Card title={detail.id}>
            <div className="padded">
              <p>{detail.reason}</p>
              <dl className="details">
                <dt>Source field</dt>
                <dd>{detail.field}</dd>
                <dt>Observed value</dt>
                <dd>{detail.observed === null ? 'Missing (null)' : String(detail.observed)}</dd>
                {detail.sourceRow && (
                  <>
                    <dt>Illustrative row reference</dt>
                    <dd>{detail.sourceRow}</dd>
                  </>
                )}
                <dt>Illustrative evaluation version</dt>
                <dd>{detail.evaluatedRuleVersion ?? 'Unavailable'}</dd>
                {version && (
                  <>
                    <dt>Business rule column labels</dt>
                    <dd>{version.configuration.requiredColumns.join(', ')}</dd>
                  </>
                )}
              </dl>
              <p className="muted">
                Business rules keep their saved column labels. The ETL source header “Customer ID”
                corresponds to the transform field “external_customer_id”.
              </p>
            </div>
          </Card>
        )}
      </div>
      <p className="soft-note">
        Independent demonstration samples. Row references are illustrative. No persisted staged-row
        link, validation run or execution-time rule binding is supplied.
      </p>
    </div>
  )
}
