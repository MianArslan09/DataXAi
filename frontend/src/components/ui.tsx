import { useEffect, useRef, type ReactNode } from 'react'
import {
  Activity,
  ArrowRight,
  Check,
  Circle,
  CircleAlert,
  CircleCheck,
  Clock3,
  Database,
  FileSpreadsheet,
  LoaderCircle,
  Search,
  X,
  type LucideIcon,
} from 'lucide-react'
import { money, type RepairEvidence } from '../services/contracts'

export function Brand({
  variant = 'sidebar',
  className = '',
}: {
  variant?: 'login' | 'sidebar'
  className?: string
}) {
  return (
    <span className={`brand brand--${variant} ${className}`.trim()}>
      <img
        src="/dataxailogo.png"
        alt="DataXAi"
        className={`brand-logo brand-logo--${variant} logo--${variant}`}
      />
    </span>
  )
}
export function DemoLabel() {
  return (
    <span className="demo-label">
      <Database size={13} />
      Demonstration Data
    </span>
  )
}
export function Heading({
  title,
  sub,
  action,
}: {
  title: string
  sub: string
  action?: ReactNode
}) {
  return (
    <header className="page-heading">
      <div>
        <h1>{title}</h1>
        <p>{sub}</p>
      </div>
      {action}
    </header>
  )
}
export function Card({
  title,
  icon: Icon,
  action,
  children,
  className = '',
}: {
  title?: string
  icon?: LucideIcon
  action?: ReactNode
  children: ReactNode
  className?: string
}) {
  return (
    <section className={'card ' + className}>
      {title && (
        <header className="card-head">
          <h2>
            {Icon && <Icon size={18} />} {title}
          </h2>
          {action}
        </header>
      )}
      {children}
    </section>
  )
}
export function Metric({
  label,
  value,
  note,
  icon: Icon,
  tone = 'teal',
}: {
  label: string
  value: string | number
  note: string
  icon: LucideIcon
  tone?: string
}) {
  return (
    <article className="metric">
      <div>
        <span>{label}</span>
        <span className={'icon-box ' + tone}>
          <Icon size={18} />
        </span>
      </div>
      <strong>{value}</strong>
      <small>{note}</small>
    </article>
  )
}
export function Badge({ children }: { children: string }) {
  const warning = /review|queued|pending|interrupted|^flagged$/i.test(children)
  const ok =
    /succeeded|active|confirmed|authorised|available|completed|^assessed without alert$/i.test(
      children,
    )
  const fail = /failed|unavailable/i.test(children)
  const progress = /running|received/i.test(children)
  const Icon = fail
    ? CircleAlert
    : warning
      ? Clock3
      : ok
        ? CircleCheck
        : progress
          ? Activity
          : Circle
  return (
    <span
      className={
        'badge ' + (fail ? 'bad' : warning ? 'warn' : ok ? 'good' : progress ? 'blue' : 'neutral')
      }
    >
      <Icon size={12} />
      {children}
    </span>
  )
}
export function LinkButton({
  to,
  children,
  primary = false,
}: {
  to: string
  children: ReactNode
  primary?: boolean
}) {
  return (
    <a className={'btn ' + (primary ? 'primary' : 'secondary')} href={to}>
      {children}
    </a>
  )
}
export function MoreLink({ to, children }: { to: string; children: ReactNode }) {
  return (
    <a className="more" href={to}>
      {children}
      <ArrowRight size={14} />
    </a>
  )
}
export function Notice({ children, error = false }: { children: ReactNode; error?: boolean }) {
  const Icon = error ? CircleAlert : Check
  return (
    <div className={'notice ' + (error ? 'error' : '')} role={error ? 'alert' : 'status'}>
      <Icon size={16} />
      <span>{children}</span>
    </div>
  )
}
export function Busy({ children }: { children: ReactNode }) {
  return (
    <span className="busy" role="status">
      <LoaderCircle className="spin" size={15} />
      {children}
    </span>
  )
}
export function Empty({ children }: { children: ReactNode }) {
  return (
    <div className="empty">
      <InboxIcon />
      {children}
    </div>
  )
}
function InboxIcon() {
  return <FileSpreadsheet size={22} aria-hidden="true" />
}
export function Table({
  headers,
  rows,
  empty = 'No matching records.',
}: {
  headers: string[]
  rows: ReactNode[][]
  empty?: string
}) {
  if (!rows.length) return <Empty>{empty}</Empty>
  return (
    <table>
      <thead>
        <tr>
          {headers.map((h) => (
            <th key={h} scope="col">
              {h}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => (
          <tr key={i}>
            {row.map((c, j) => (
              <td key={headers[j]}>{c}</td>
            ))}
          </tr>
        ))}
      </tbody>
    </table>
  )
}
export function SearchBox({
  value,
  onChange,
  placeholder = 'Search records',
}: {
  value: string
  onChange: (v: string) => void
  placeholder?: string
}) {
  return (
    <label className="search">
      <Search size={16} />
      <input
        type="search"
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  )
}
export function Field({
  label,
  hint,
  children,
}: {
  label: string
  hint?: string
  children: ReactNode
}) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  )
}
export function Dialog({
  title,
  onClose,
  children,
}: {
  title: string
  onClose: () => void
  children: ReactNode
}) {
  const ref = useRef<HTMLDialogElement>(null)
  useEffect(() => {
    const el = ref.current
    el?.showModal()
    return () => el?.close()
  }, [])
  return (
    <dialog
      ref={ref}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
      aria-label={title}
    >
      <div className="dialog-head">
        <h2>{title}</h2>
        <button aria-label="Close dialog" className="icon-btn" onClick={onClose}>
          <X size={18} />
        </button>
      </div>
      {children}
    </dialog>
  )
}
export function EvidenceTable({
  e,
  processed = false,
}: {
  e: RepairEvidence
  processed?: boolean
}) {
  return (
    <Table
      headers={[
        'Transaction field',
        'Original source',
        processed ? 'Processed value' : 'Proposed value',
      ]}
      rows={[
        ['Product', e.product, e.product],
        ['Quantity', e.quantity + ' units', e.quantity + ' units'],
        ['Unit price', money(e.sourceUnit), <strong>{money(e.supportedUnit)}</strong>],
        [
          'Line total',
          money(e.quantity * e.sourceUnit),
          <strong>{money(e.quantity * e.supportedUnit)}</strong>,
        ],
      ]}
    />
  )
}
export function Impact({ e, processedUnit }: { e: RepairEvidence; processedUnit?: number }) {
  const difference = e.quantity * ((processedUnit ?? e.supportedUnit) - e.sourceUnit)
  return (
    <div className="impact">
      <div>
        <span>
          {processedUnit === undefined
            ? 'Historical spending difference'
            : 'Transaction difference'}
        </span>
        <strong>
          {processedUnit !== undefined && difference > 0 ? '+' : ''}
          {money(difference).replace('-', '−')}
        </strong>
        {processedUnit !== undefined && <small>Processed total minus original total</small>}
      </div>
      <div>
        <span>{processedUnit === undefined ? 'Weekly units' : 'Invoice quantity'}</span>
        <b>{e.quantity} units · unchanged</b>
        <small>
          {processedUnit === undefined
            ? 'Forecast effect requires model evaluation.'
            : 'Before/after demand comparison not evaluated.'}
        </small>
      </div>
      <div>
        <span>90-Day Customer Spending Prediction</span>
        <b>Not Yet Recomputed</b>
        {processedUnit !== undefined && <small>Before/after spending estimates unavailable.</small>}
      </div>
    </div>
  )
}
