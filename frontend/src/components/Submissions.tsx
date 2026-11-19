import { useState } from 'react'
import { FileSpreadsheet, Inbox } from 'lucide-react'
import { date, stageOf, type Stage } from '../services/contracts'
import { SubmissionReceipt } from './SubmissionReceipt'
import { href } from '../routes'
import { useWork } from './Workspace'
import { Badge, Card, Dialog, Empty, MoreLink, SearchBox, Table } from './ui'
export function Submissions({
  compact = false,
  limit = 4,
  initialId = null,
}: {
  compact?: boolean
  limit?: number
  initialId?: string | null
}) {
  const { data, user } = useWork()
  const [query, setQuery] = useState(''),
    [filter, setFilter] = useState(''),
    [detail, setDetail] = useState(initialId ?? '')
  const manager = user.appRole === 'Manager'
  const all = data.submissions.filter(
    (s) =>
      (s.filename + ' ' + s.id).toLowerCase().includes(query.toLowerCase()) &&
      (!filter || stageOf(data, s.id) === filter),
  )
  const item = data.submissions.find((s) => s.id === detail)
  const rows = (compact ? all.slice(0, limit) : all).map((s) => [
    <span className="file-cell">
      <span className="file-icon">
        <FileSpreadsheet size={17} />
      </span>
      <span>
        <button className="table-link" onClick={() => setDetail(s.id)}>
          {s.filename}
        </button>
        <small>{s.id}</small>
      </span>
    </span>,
    s.source,
    date(s.submitted),
    <Badge>{stageOf(data, s.id)}</Badge>,
    ...(manager
      ? [<MoreLink to={href('Manager', 'batch') + '?id=' + s.id}>Inspect</MoreLink>]
      : []),
  ])
  return (
    <Card
      title={compact ? 'Recent submissions' : 'Submission register'}
      icon={Inbox}
      action={
        compact ? (
          <MoreLink to={href(user.appRole, manager ? 'submissions' : 'status')}>View all</MoreLink>
        ) : undefined
      }
    >
      {!compact && (
        <div className="filters">
          <SearchBox value={query} onChange={setQuery} placeholder="Search files or references" />
          <select
            aria-label="Filter by execution status"
            value={filter}
            onChange={(e) => setFilter(e.target.value)}
          >
            <option value="">All statuses</option>
            {(
              [
                'Not Requested',
                'Request Received',
                'Queued',
                'Running',
                'Succeeded',
                'Failed',
                'Interrupted',
              ] satisfies Stage[]
            ).map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </div>
      )}
      <Table
        headers={[
          'Submission',
          'Format',
          'Submitted',
          'Execution status',
          ...(manager ? ['Action'] : []),
        ]}
        rows={rows}
        empty="No submissions match this view."
      />
      {detail && !item && <Empty>Submission unavailable for this workspace.</Empty>}
      {item && (
        <Dialog title={item.id} onClose={() => setDetail('')}>
          <SubmissionReceipt item={item} />
        </Dialog>
      )}
    </Card>
  )
}
