import { useState, type FormEvent } from 'react'
import { useWork } from '../components/Workspace'
import { Card, Dialog, Empty, Field, Notice } from '../components/ui'
import {
  initialRuleDraft,
  reviewActions,
  type RuleConfiguration,
  type RuleVersion,
} from '../services/contracts'

function RuleEditor({
  base,
  onClose,
  onSave,
}: {
  base?: RuleVersion
  onClose: () => void
  onSave: (id: string) => void
}) {
  const { user, service } = useWork()
  const original = base?.configuration ?? initialRuleDraft
  const [columns, setColumns] = useState(original.requiredColumns.join(', '))
  const [policy, setPolicy] = useState(original.unknownCancellation)
  const [price, setPrice] = useState(String(original.priceAlertSigma))
  const [quantity, setQuantity] = useState(String(original.quantityAlertSigma))
  const [actions, setActions] = useState(original.permittedActions)
  const [error, setError] = useState('')

  function save(event: FormEvent) {
    event.preventDefault()
    if (!user.businessId) return
    try {
      const id = service.saveRuleVersion(user, user.businessId, base?.id ?? null, {
        requiredColumns: columns.split(','),
        unknownCancellation: policy,
        priceAlertSigma: Number(price),
        quantityAlertSigma: Number(quantity),
        permittedActions: actions,
      })
      onSave(id)
    } catch (err) {
      setError((err as Error).message)
    }
  }

  return (
    <Dialog title="Edit processing-rule draft" onClose={onClose}>
      <form className="padded form-layout" onSubmit={save}>
        <p>
          {user.businessName} · Demonstration draft from {base?.id ?? 'initial settings'}
        </p>
        <Field
          label="Required source columns"
          hint="Separate header names with commas. Value nullability is checked separately."
        >
          <input required value={columns} onChange={(e) => setColumns(e.target.value)} />
        </Field>
        <Field
          label="Unknown cancellation status"
          hint="Both policies preserve signed return quantities."
        >
          <select
            value={policy}
            onChange={(e) => setPolicy(e.target.value as RuleConfiguration['unknownCancellation'])}
          >
            <option>Preserve unknown</option>
            <option>Request review</option>
          </select>
        </Field>
        <div className="two-fields">
          <Field label="Unit price alert (σ)">
            <input
              type="number"
              step="any"
              required
              value={price}
              onChange={(e) => setPrice(e.target.value)}
            />
          </Field>
          <Field label="Quantity alert (σ)">
            <input
              type="number"
              step="any"
              required
              value={quantity}
              onChange={(e) => setQuantity(e.target.value)}
            />
          </Field>
        </div>
        <fieldset className="sheet-options">
          <legend>Permitted Manager review actions</legend>
          {reviewActions.map((action) => (
            <label key={action}>
              <input
                type="checkbox"
                checked={actions.includes(action)}
                onChange={(e) =>
                  setActions(
                    e.target.checked ? [...actions, action] : actions.filter((a) => a !== action),
                  )
                }
              />
              {action}
            </label>
          ))}
        </fieldset>
        {error && <Notice error>{error}</Notice>}
        <div className="action-row">
          <button className="btn primary" type="submit">
            Save new version
          </button>
          <button className="btn secondary" type="button" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Dialog>
  )
}

export function ProcessingRules() {
  const { user, data, reload } = useWork()
  const current = data.ruleVersions[0]
  const [selected, setSelected] = useState('')
  const [editor, setEditor] = useState<{ base?: RuleVersion } | null>(null)
  const [message, setMessage] = useState('')
  const version = data.ruleVersions.find((v) => v.id === selected) ?? current
  const config = version?.configuration
  return (
    <div className="stack">
      <Card
        title="Processing Rules"
        action={
          <button className="btn primary" onClick={() => setEditor({ base: current })}>
            {current ? 'Edit current rules' : 'Create first version'}
          </button>
        }
      >
        <div className="padded">
          <p>
            {user.businessName} · Current version: {current?.id ?? 'None saved'}
          </p>
          <p className="soft-note">
            Demonstration only. Saves last until page reload. Execution and ETL jobs are unaffected.
          </p>
          {message && <Notice>{message}</Notice>}
        </div>
      </Card>
      <Card title="Version history">
        <div className="padded">
          {version && config ? (
            <>
              <Field label="Inspect saved version">
                <select value={version.id} onChange={(e) => setSelected(e.target.value)}>
                  {data.ruleVersions.map((v) => (
                    <option key={v.id}>{v.id}</option>
                  ))}
                </select>
              </Field>
              <dl className="details">
                <dt>Business</dt>
                <dd>
                  {user.businessName} ({version.businessId})
                </dd>
                <dt>Created by</dt>
                <dd>
                  {version.creatorName} · {version.createdBy}
                </dd>
                <dt>Created at</dt>
                <dd>
                  <time dateTime={version.createdAt}>{version.createdAt}</time>
                </dd>
                <dt>Required source headers</dt>
                <dd>{config.requiredColumns.join(', ')} · header presence only</dd>
                <dt>Unknown cancellation status</dt>
                <dd>{config.unknownCancellation} · preserve signed quantities</dd>
                <dt>Statistical alert thresholds</dt>
                <dd>
                  Unit price {config.priceAlertSigma}σ · Quantity {config.quantityAlertSigma}σ
                </dd>
                <dt>Permitted review actions</dt>
                <dd>{config.permittedActions.join(' · ') || 'None'}</dd>
              </dl>
              <p className="soft-note">
                Thresholds express distance from the mean in standard deviations. A price correction
                requires supporting evidence and Manager review.
              </p>
            </>
          ) : (
            <Empty>No saved rule versions for this business.</Empty>
          )}
        </div>
      </Card>
      {editor && (
        <RuleEditor
          base={editor.base}
          onClose={() => setEditor(null)}
          onSave={(id) => {
            reload()
            setSelected(id)
            setMessage(id + ' saved in the demonstration.')
            setEditor(null)
          }}
        />
      )}
    </div>
  )
}
