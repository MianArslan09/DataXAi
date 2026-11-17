import { useState, type FormEvent } from 'react'
import {
  ArrowRight,
  Building2,
  ChartNoAxesCombined,
  Database,
  Eye,
  EyeOff,
  GitBranch,
  LockKeyhole,
  ShieldCheck,
  Store,
  UserRound,
  Workflow,
} from 'lucide-react'
import { useAuth } from '../auth/AuthProvider'
import { dataMode, loginPresets } from '../services/providers'
import { Brand, Busy, DemoLabel, Field, Notice } from '../components/ui'
import type { AppRole } from '../auth/roleAdapter'

function PipelineDiagram() {
  return (
    <div
      className="pipeline-illustration"
      role="img"
      aria-label="Data workflow from ingestion through evidence review to analytics"
    >
      <div className="diagram-title">
        <GitBranch size={15} /> Data workflow
      </div>
      <div className="diagram-stages">
        {[
          { icon: Database, label: 'Ingest' },
          { icon: ShieldCheck, label: 'Review' },
          { icon: ChartNoAxesCombined, label: 'Analyse' },
        ].map(({ icon: Icon, label }) => (
          <div key={label}>
            <span>
              <Icon size={24} />
            </span>
            <b>{label}</b>
          </div>
        ))}
      </div>
      <div className="diagram-bottom">
        <span className="live-dot" />
        Source records retain their original values
      </div>
    </div>
  )
}
export function Login() {
  const { login, message } = useAuth()
  const [username, setUsername] = useState(''),
    [password, setPassword] = useState('')
  const [show, setShow] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('')
  function preset(role: AppRole) {
    if (loginPresets) {
      setUsername(loginPresets.usernames[role])
      setPassword(loginPresets.password)
      setError('')
    }
  }
  async function submit(e: FormEvent) {
    e.preventDefault()
    setError('')
    if (!username.trim() || !password) {
      setError('Enter your username and password.')
      return
    }
    setBusy(true)
    try {
      await login(username.trim(), password)
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="login">
      <section className="login-cover">
        <Brand variant="login" />
        <div className="login-story">
          <div className="cover-kicker">
            AUTO-HEALING ETL & PREDICTIVE ANALYTICS FOR PAKISTANI E-COMMERCE SMEs
          </div>
          <h1>
            Your data operations
            <br />
            in a <span>single workspace</span>
          </h1>
          <p>
            Manage bulk retail data, review source evidence, automate repairs with self-healing
            pipelines, and explore predictive analytics in a unified workspace.
          </p>
          <PipelineDiagram />
        </div>
        <div className="cover-footer">
          <Building2 size={16} /> Built for Pakistani e-commerce teams and corporations.
        </div>
      </section>
      <section className="login-right">
        <div className="login-mode">
          {dataMode === 'demo' ? <DemoLabel /> : <span>Django authentication</span>}
        </div>
        <div className="login-form">
          <span className="large-icon">
            <LockKeyhole size={24} />
          </span>
          <h2>Sign in</h2>
          <p>Enter your account details to access your workspace.</p>
          <form onSubmit={submit} noValidate>
            <Field label="Username">
              <div className="input-wrap">
                <UserRound size={17} />
                <input
                  autoComplete="username"
                  placeholder="Enter your username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                />
              </div>
            </Field>
            <Field label="Password">
              <div className="input-wrap">
                <LockKeyhole size={17} />
                <input
                  aria-label="Password"
                  autoComplete="current-password"
                  placeholder="Enter your password"
                  type={show ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                />
                <button
                  type="button"
                  className="icon-btn"
                  aria-label={show ? 'Hide password' : 'Show password'}
                  onClick={() => setShow(!show)}
                >
                  {show ? <EyeOff size={16} /> : <Eye size={16} />}
                </button>
              </div>
            </Field>
            {(error || message) && <Notice error>{error || message}</Notice>}
            <button className="btn primary full" disabled={busy}>
              {busy ? (
                <Busy>Signing in…</Busy>
              ) : (
                <>
                  Sign in <ArrowRight size={16} />
                </>
              )}
            </button>
          </form>
          {dataMode === 'demo' && loginPresets && (
            <div className="demo-presets">
              <h3>Account Access</h3>
              <p>Select a role to fill in fictional credentials.</p>
              <div>
                {loginPresets.roles.map((r, i) => {
                  const Icon = [ShieldCheck, Workflow, Store][i]
                  return (
                    <button
                      key={r}
                      type="button"
                      aria-pressed={username === loginPresets?.usernames[r]}
                      onClick={() => preset(r)}
                    >
                      <Icon size={16} />
                      {r}
                    </button>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </section>
    </main>
  )
}
