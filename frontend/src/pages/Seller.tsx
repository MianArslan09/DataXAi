import { FileCheck2, FileSpreadsheet, FileUp, Files, Inbox, ShieldCheck } from 'lucide-react'
import { useWork } from '../components/Workspace'
import { Empty, Heading, LinkButton, Metric, MoreLink } from '../components/ui'
import { href } from '../routes'
import { Submissions } from '../components/Submissions'
import { Forecast, Spending } from '../components/Analytics'
import { Explanation } from './Repairs'
import { Submit } from './Submit'
const path = (slug: string) => href('Seller', slug)
export function Seller({ slug, selected }: { slug: string; selected: string | null }) {
  const { data, user } = useWork()
  if (slug === 'submit') return <Submit />
  if (slug === 'repairs') return <Explanation key={user.businessId} />
  if (slug === 'status')
    return (
      <>
        <Heading
          title="Submission status"
          sub="Track submitted files and their latest execution status."
        />
        <Submissions key={selected} initialId={selected} />
      </>
    )
  if (slug === 'forecasts')
    return (
      <>
        <Heading
          title="Product forecasts"
          sub="Historical weekly unit sales and illustrative demand predictions."
        />
        <Forecast key={user.businessId} detail />
      </>
    )
  if (slug === 'spending')
    return (
      <>
        <Heading
          title="Customer spending estimates"
          sub="Illustrative customer-level predictions for the next 90 days."
        />
        <Spending detail />
      </>
    )
  return (
    <>
      <Heading
        title="Seller dashboard"
        sub="Track your retail submissions, demand outlook and recorded repairs."
        action={
          <LinkButton primary to={path('submit')}>
            <FileUp size={16} />
            Submit data
          </LinkButton>
        }
      />
      <div className="metrics">
        <Metric
          label="Submissions"
          value={data.submissions.length}
          note="Files in this workspace"
          icon={Files}
        />
        <Metric
          label="Awaiting processing"
          value={
            data.submissions.filter((s) => !data.executions.some((e) => e.submissionId === s.id))
              .length
          }
          note="Pending request"
          icon={Inbox}
          tone="blue"
        />
        <Metric
          label="Completed executions"
          value={data.executions.filter((e) => e.stage === 'Succeeded').length}
          note="Successful simulations"
          icon={FileSpreadsheet}
          tone="violet"
        />
        <Metric
          label="Repair explanations"
          value={data.explanations.length}
          note="Recorded scenario history"
          icon={ShieldCheck}
          tone="sand"
        />
      </div>
      <div className="wide-grid analytics">
        <Forecast />
        <Spending />
      </div>
      <Submissions compact limit={2} />
      <div className="repair-summary">
        <span className="tile-icon">
          <FileCheck2 size={19} />
        </span>
        <div>
          <b>Repair explanation history</b>
          <small>
            {data.explanations[0]?.invoice ?? 'No repairs available'}
            {data.explanations.length > 0 ? ' · recorded unit price correction' : ''}
          </small>
        </div>
        {data.explanations.length > 0 ? (
          <MoreLink to={path('repairs')}>View explanation</MoreLink>
        ) : (
          <Empty>No history</Empty>
        )}
      </div>
    </>
  )
}
