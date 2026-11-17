import { useState, type FormEvent } from 'react'
import {
  ArrowLeft,
  Building2,
  CheckCheck,
  ChevronRight,
  ListFilter,
  Settings2,
  ShieldCheck,
  UserRoundPlus,
  Users,
} from 'lucide-react'
import { mapBackendRole, ROLE_MAP } from '../auth/roleAdapter'
import { useWork } from '../components/Workspace'
import {
  Badge,
  Busy,
  Card,
  Field,
  Heading,
  LinkButton,
  Metric,
  MoreLink,
  Notice,
  SearchBox,
  Table,
} from '../components/ui'
import { href } from '../routes'
import type { AccountInput } from '../services/contracts'

const adminLink = (slug: string) => href('Admin', slug)
function UserList({ compact = false }: { compact?: boolean }) {
  const { data } = useWork()
  const [query, setQuery] = useState(''),
    [role, setRole] = useState('')
  const found = data.accounts.filter(
    (a) =>
      (!role || a.backendRole === role) &&
      [a.fullName, a.username, a.email].join(' ').toLowerCase().includes(query.toLowerCase()),
  )
  const rows = (compact ? found.slice(0, 3) : found).map((a) => [
    <span className="person">
      <span className={'mini-avatar c' + (a.fullName.length % 3)}>
        {a.fullName
          .split(' ')
          .map((n) => n[0])
          .slice(0, 2)
          .join('')}
      </span>
      <span>
        <b>{a.fullName}</b>
        <small>@{a.username}</small>
      </span>
    </span>,
    <span className="role-tag">{mapBackendRole(a.backendRole)}</span>,
    data.businesses.find((b) => b.businessId === a.businessId)?.businessName ?? 'Organisation',
    <Badge>Active</Badge>,
  ])
  return (
    <Card
      title="User directory"
      icon={Users}
      action={compact ? <MoreLink to={adminLink('users')}>View all users</MoreLink> : undefined}
    >
      {!compact && (
        <div className="filters">
          <SearchBox value={query} onChange={setQuery} placeholder="Search users" />
          <label>
            <ListFilter size={15} />
            <select
              aria-label="Filter by role"
              value={role}
              onChange={(e) => setRole(e.target.value)}
            >
              <option value="">All roles</option>
              {Object.entries(ROLE_MAP).map(([k, v]) => (
                <option key={k} value={k}>
                  {v}
                </option>
              ))}
            </select>
          </label>
        </div>
      )}
      <Table headers={['User', 'Role', 'Business', 'Status']} rows={rows} />
    </Card>
  )
}
function CreateAccount() {
  const { user, service, data, reload } = useWork()
  const [form, setForm] = useState({
      fullName: '',
      username: '',
      email: '',
      role: '',
      businessId: '',
    }),
    [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [done, setDone] = useState('')
  function update(key: keyof typeof form, value: string) {
    setForm((prev) => ({ ...prev, [key]: value }))
    setDone('')
    setError('')
  }
  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setDone('')
    if (
      !form.fullName.trim() ||
      !form.username.trim() ||
      !form.email.trim() ||
      !form.role ||
      (form.role !== 'admin' && !form.businessId)
    ) {
      setError('Complete all required fields and select the account scope.')
      return
    }
    setBusy(true)
    try {
      await service.createAccount(user, form as AccountInput)
      reload()
      setDone(
        form.username + ' was added to the demonstration directory. No Django account was created.',
      )
      setForm({ fullName: '', username: '', email: '', role: '', businessId: '' })
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Heading
        title="Create account"
        sub="Set up a user identity, workspace role and business assignment."
        action={
          <LinkButton to={adminLink('users')}>
            <ArrowLeft size={15} /> User directory
          </LinkButton>
        }
      />
      <div className="wide-grid">
        <Card title="Account details" icon={UserRoundPlus}>
          <form className="padded form-layout" onSubmit={submit} noValidate>
            <Field label="Full name">
              <input
                value={form.fullName}
                onChange={(e) => update('fullName', e.target.value)}
                placeholder="e.g. Hira Ahmed"
                required
              />
            </Field>
            <div className="two-fields">
              <Field label="Username">
                <input
                  value={form.username}
                  onChange={(e) => update('username', e.target.value)}
                  placeholder="hira.ahmed"
                  required
                />
              </Field>
              <Field label="Email address">
                <input
                  type="email"
                  value={form.email}
                  onChange={(e) => update('email', e.target.value)}
                  placeholder="name@example.test"
                  required
                />
              </Field>
            </div>
            <div className="form-divider">
              <ShieldCheck size={16} />
              Workspace access
            </div>
            <Field label="Account role">
              <select value={form.role} onChange={(e) => update('role', e.target.value)} required>
                <option value="">Choose a role</option>
                {Object.entries(ROLE_MAP).map(([k, v]) => (
                  <option key={k} value={k}>
                    {v}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Business assignment">
              <select
                disabled={form.role === 'admin'}
                value={form.role === 'admin' ? '' : form.businessId}
                onChange={(e) => update('businessId', e.target.value)}
              >
                <option value="">
                  {form.role === 'admin' ? 'Organisation-wide access' : 'Choose a business'}
                </option>
                {data.businesses.map((b) => (
                  <option key={b.businessId} value={b.businessId}>
                    {b.businessName}
                  </option>
                ))}
              </select>
            </Field>
            {error && <Notice error>{error}</Notice>}
            {done && <Notice>{done}</Notice>}
            <div className="form-actions">
              <LinkButton to={adminLink('dashboard')}>Cancel</LinkButton>
              <button className="btn primary" disabled={busy}>
                {busy ? (
                  <Busy>Creating…</Busy>
                ) : (
                  <>
                    <UserRoundPlus size={16} />
                    Create account
                  </>
                )}
              </button>
            </div>
          </form>
        </Card>
        <Card title="Access guide" icon={ShieldCheck}>
          <div className="padded guide">
            <h3>Admin</h3>
            <p>Manages users, business assignments and system settings.</p>
            <h3>Manager</h3>
            <p>Runs batch simulations and reviews quarantined records for one business.</p>
            <h3>Seller</h3>
            <p>Submits data and views repairs and analytics for one business.</p>
            <div className="soft-note">
              Local accounts use the shared demonstration password shown by the login presets.
            </div>
          </div>
        </Card>
      </div>
    </>
  )
}
function Assignments() {
  const { data, user, service, reload } = useWork()
  const [message, setMessage] = useState('')
  return (
    <>
      <Heading
        title="Business assignments"
        sub="Assign each manager and seller to a demonstration business."
      />
      {message && <Notice>{message}</Notice>}
      <Card title="Account assignments" icon={Building2}>
        <Table
          headers={['Account', 'Role', 'Assigned business']}
          rows={data.accounts
            .filter((a) => a.backendRole !== 'admin')
            .map((a) => [
              a.fullName,
              mapBackendRole(a.backendRole),
              <select
                aria-label={'Business for ' + a.fullName}
                value={a.businessId ?? ''}
                onChange={(e) => {
                  service.assignBusiness(user, a.userId, e.target.value)
                  reload()
                  setMessage(a.fullName + '’s demonstration assignment was updated.')
                }}
              >
                {data.businesses.map((b) => (
                  <option key={b.businessId} value={b.businessId}>
                    {b.businessName}
                  </option>
                ))}
              </select>,
            ])}
        />
      </Card>
    </>
  )
}
export function Admin({ slug }: { slug: string }) {
  const { data, user, service, reload } = useWork()
  if (slug === 'create-account') return <CreateAccount />
  if (slug === 'assignments') return <Assignments />
  if (slug === 'users')
    return (
      <>
        <Heading
          title="Users"
          sub="Browse accounts, roles and assigned businesses."
          action={
            <LinkButton primary to={adminLink('create-account')}>
              <UserRoundPlus size={16} />
              Create account
            </LinkButton>
          }
        />
        <UserList />
      </>
    )
  if (slug === 'configuration')
    return (
      <>
        <Heading
          title="System configuration"
          sub="Manage settings for the current demonstration."
        />
        <Card title="Review preferences" icon={Settings2}>
          <div className="padded">
            <label className="setting">
              <input
                type="checkbox"
                checked={data.reviewConfirmation}
                onChange={(e) => {
                  service.setReviewReminder(user, e.target.checked)
                  reload()
                }}
              />
              Show an evidence reminder in manager decision confirmations
            </label>
            <p className="muted">Manager confirmation is required for every decision.</p>
          </div>
        </Card>
      </>
    )
  const counts = Object.keys(ROLE_MAP).map(
    (r) => data.accounts.filter((a) => a.backendRole === r).length,
  )
  return (
    <>
      <Heading
        title="Admin dashboard"
        sub="An overview of your organisation’s users and workspace access."
        action={
          <LinkButton primary to={adminLink('create-account')}>
            <UserRoundPlus size={16} />
            Create account
          </LinkButton>
        }
      />
      <div className="metrics">
        <Metric
          label="Active users"
          value={data.accounts.length}
          note="Across all workspaces"
          icon={Users}
        />
        <Metric
          label="Businesses"
          value={data.businesses.length}
          note="Registered in this directory"
          icon={Building2}
          tone="blue"
        />
        <Metric
          label="Assigned accounts"
          value={data.accounts.filter((a) => a.businessId).length}
          note="Business-scoped access"
          icon={CheckCheck}
          tone="violet"
        />
        <Metric
          label="Administrators"
          value={counts[0]}
          note="Organisation-wide access"
          icon={ShieldCheck}
          tone="sand"
        />
      </div>
      <div className="dashboard-grid">
        <Card
          title="Business assignments"
          icon={Building2}
          action={<MoreLink to={adminLink('assignments')}>Manage</MoreLink>}
        >
          <div className="business-rows">
            {data.businesses.map((b, i) => (
              <div key={b.businessId}>
                <span className={'tile-icon c' + i}>
                  <Building2 size={18} />
                </span>
                <b>{b.businessName}</b>
                <span>
                  {data.accounts.filter((a) => a.businessId === b.businessId).length} accounts
                </span>
              </div>
            ))}
          </div>
        </Card>
        <Card title="Role distribution" icon={Users}>
          <div className="padded">
            <div className="distribution">
              {counts.map((n, i) => (
                <span key={i} className={'seg s' + i} style={{ flex: n }} />
              ))}
            </div>
            {Object.values(ROLE_MAP).map((r, i) => (
              <div key={r} className="legend-row">
                <i className={'s' + i} />
                {r}
                <b>{counts[i]}</b>
              </div>
            ))}
          </div>
        </Card>
      </div>
      <UserList compact />
      <a className="utility-link" href={adminLink('configuration')}>
        <Settings2 size={16} /> System configuration <ChevronRight size={15} />
      </a>
    </>
  )
}
