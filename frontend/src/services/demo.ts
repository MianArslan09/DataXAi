import { mapBackendRole, ROLE_MAP } from '../auth/roleAdapter'
import { seed } from '../fixtures/seed'
import {
  pending,
  type AccountInput,
  type AuthenticatedUser,
  type AuthService,
  type Decision,
  type FindingEvidenceDetail,
  reviewActions,
  type RuleConfiguration,
  type Stage,
  type Submission,
  type SubmissionRequest,
  type Workspace,
  type WorkspaceService,
} from './contracts'
import { makeUser } from '../auth/identity'

const DEMO_PASSWORD = 'DataXAi-demo'
const allowed = (ok: unknown, message = 'Access denied for this workspace.') => {
  if (!ok) throw new Error(message)
}

export class DemoProvider implements WorkspaceService, AuthService {
  private data = seed()
  private userId: string | null = null
  private expires = 0
  private tick = 0
  private timestamp() {
    return new Date(Date.UTC(2026, 10, 15, 5, 0) + this.tick++ * 60000).toISOString()
  }
  private identity() {
    const account = this.data.accounts.find((a) => a.userId === this.userId)
    if (!account || Date.now() >= this.expires) throw new Error('Session expired. Sign in again.')
    const business = this.data.businesses.find((b) => b.businessId === account.businessId)
    return makeUser(account, 'demo', business)
  }
  async login(name: string, password: string) {
    await pending()
    const account = this.data.accounts.find(
      (a) => a.username.toLowerCase() === name.trim().toLowerCase(),
    )
    if (!account || password !== DEMO_PASSWORD)
      throw new Error('Username or password is incorrect.')
    this.userId = account.userId
    this.expires = Date.now() + 30 * 60_000
    return this.identity()
  }
  async renew() {
    return this.identity()
  }
  nextCheck() {
    return Math.max(0, this.expires - Date.now())
  }
  logout() {
    this.userId = null
    this.expires = 0
  }
  private check(user: AuthenticatedUser, role?: string) {
    const current = this.identity()
    allowed(
      user.dataSource === 'demo' &&
        user.userId === current.userId &&
        user.appRole === current.appRole &&
        user.businessId === current.businessId &&
        (!role || current.appRole === role),
    )
    return current
  }
  readDetectionEvidence(
    user: AuthenticatedUser,
    businessId: string,
    submissionId?: string,
  ): Workspace['detectionEvidence'] {
    const actor = this.check(user, 'Manager')
    allowed(businessId && actor.businessId === businessId)
    if (submissionId !== undefined) {
      allowed(
        this.data.submissions.some((s) => s.businessId === businessId && s.id === submissionId),
      )
    }
    return structuredClone(
      this.data.detectionEvidence.filter(
        (e) =>
          e.businessId === businessId &&
          (submissionId === undefined || e.submissionId === submissionId),
      ),
    )
  }
  readFindingEvidence(
    user: AuthenticatedUser,
    businessId: string,
    submissionId: string,
    sourceRowReference: string,
  ): FindingEvidenceDetail {
    const results = this.readDetectionEvidence(user, businessId, submissionId).filter(
      (result) => result.sourceRowReference === sourceRowReference,
    )
    allowed(results.length, 'Finding evidence unavailable for this workspace.')
    const relatedChecks = this.data.validationEvidence
      .filter((report) => report.businessId === businessId && report.submissionId === submissionId)
      .flatMap((report) => report.samples)
      .flatMap((sample) =>
        sample.checks
          .filter(
            (check) =>
              check.level === 'Row' &&
              check.sourceRow === sourceRowReference &&
              results.some(
                (result) =>
                  result.validationRef?.sample === sample.name &&
                  result.validationRef.checkId === check.id &&
                  result.field === check.field &&
                  result.observed === check.observed,
              ),
          )
          .map((check) => ({ sample: sample.name, check })),
      )
    return { results, relatedChecks: structuredClone(relatedChecks) }
  }
  readValidationEvidence(
    user: AuthenticatedUser,
    businessId: string,
  ): Workspace['validationEvidence'] {
    const actor = this.check(user, 'Manager')
    allowed(businessId && actor.businessId === businessId)
    return structuredClone(this.data.validationEvidence.filter((e) => e.businessId === businessId))
  }
  snapshot(user: AuthenticatedUser): Workspace {
    this.check(user)
    const d = this.data
    const admin = user.appRole === 'Admin'
    const manager = user.appRole === 'Manager'
    const own = (item: { businessId: string }) => !admin && item.businessId === user.businessId
    return structuredClone({
      dataSource: 'demo',
      ruleVersions: manager ? d.ruleVersions.filter(own) : [],
      detectionEvidence: manager ? d.detectionEvidence.filter(own) : [],
      validationEvidence:
        manager && user.businessId ? this.readValidationEvidence(user, user.businessId) : [],
      workbooks: d.workbooks.filter(own),
      sourceAliases: d.sourceAliases.filter(own),
      businesses: d.businesses.filter((b) => admin || own(b)),
      accounts: admin ? d.accounts : [],
      submissions: d.submissions.filter(own),
      executions: d.executions.filter(own),
      quarantine: manager ? d.quarantine.filter(own) : [],
      explanations: user.appRole === 'Seller' ? d.explanations.filter(own) : [],
      forecasts: user.appRole === 'Seller' ? d.forecasts.filter(own) : [],
      spending: user.appRole === 'Seller' ? d.spending.filter(own) : [],
      reviewConfirmation: d.reviewConfirmation,
    })
  }
  async createAccount(user: AuthenticatedUser, input: AccountInput) {
    await pending()
    this.check(user, 'Admin')
    const { role, businessId } = input
    const username = input.username.trim().toLowerCase()
    const email = input.email.trim().toLowerCase()
    allowed(
      input.fullName.trim() &&
        /^[a-z0-9._-]{3,32}$/.test(username) &&
        /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email),
      'Enter a name, valid email and a 3–32 character username.',
    )
    allowed(
      mapBackendRole(role) &&
        (role === 'admin' || this.data.businesses.some((b) => b.businessId === businessId)),
      'Choose a role and an assigned business.',
    )
    allowed(
      !this.data.accounts.some((a) => a.username === username || a.email === email),
      'That username or email already exists.',
    )
    this.data.accounts.push({
      userId: 'U-' + (this.data.accounts.length + 1),
      username,
      email,
      fullName: input.fullName.trim(),
      backendRole: role,
      businessId: role === 'admin' ? null : businessId,
    })
  }
  assignBusiness(user: AuthenticatedUser, accountId: string, businessId: string) {
    this.check(user, 'Admin')
    const account = this.data.accounts.find(
      (a) => a.userId === accountId && a.backendRole !== 'admin',
    )
    if (!account || !this.data.businesses.some((b) => b.businessId === businessId))
      throw new Error('Choose an eligible account and business.')
    account.businessId = businessId
  }
  saveRuleVersion(
    user: AuthenticatedUser,
    businessId: string,
    baseId: string | null,
    draft: RuleConfiguration,
  ) {
    const actor = this.check(user, 'Manager')
    allowed(businessId && actor.businessId === businessId)
    const versions = this.data.ruleVersions.filter((v) => v.businessId === businessId)
    allowed((versions[0]?.id ?? null) === baseId, 'Open a draft from the current version.')
    const columns = draft.requiredColumns.map((column) => column.trim())
    allowed(
      columns.length > 0 &&
        columns.every(Boolean) &&
        new Set(columns.map((column) => column.toLowerCase())).size === columns.length,
      'Enter unique, non-empty source column names.',
    )
    allowed(
      draft.unknownCancellation === 'Preserve unknown' ||
        draft.unknownCancellation === 'Request review',
      'Choose a supported cancellation policy.',
    )
    allowed(
      [draft.priceAlertSigma, draft.quantityAlertSigma].every((n) => Number.isFinite(n) && n > 0),
      'Both statistical thresholds must be positive finite numbers.',
    )
    allowed(
      Array.isArray(draft.permittedActions) &&
        draft.permittedActions.every((action) => reviewActions.includes(action)) &&
        new Set(draft.permittedActions).size === draft.permittedActions.length,
      'Choose only supported Manager review actions.',
    )
    const sequence = String(versions.length + 1).padStart(3, '0')
    const id = 'RULE-' + businessId.toUpperCase() + '-' + sequence
    this.data.ruleVersions.unshift({
      id,
      businessId,
      createdBy: actor.userId,
      creatorName: actor.fullName,
      createdAt: this.timestamp(),
      configuration: {
        requiredColumns: columns,
        unknownCancellation: draft.unknownCancellation,
        priceAlertSigma: draft.priceAlertSigma,
        quantityAlertSigma: draft.quantityAlertSigma,
        permittedActions: [...draft.permittedActions],
      },
    })
    return id
  }
  setReviewReminder(user: AuthenticatedUser, enabled: boolean) {
    this.check(user, 'Admin')
    this.data.reviewConfirmation = enabled
  }
  private sourceDetails(
    businessId: string,
    request: SubmissionRequest,
  ): Pick<Submission, 'source' | 'scope' | 'filename' | 'rows'> {
    if (typeof request === 'string') {
      if (!/\.(csv|xlsx)$/i.test(request)) throw new Error('Choose a CSV or XLSX file.')
      return {
        filename: request,
        source: /\.csv$/i.test(request) ? 'CSV' : 'Excel',
        scope: { kind: 'file' },
        rows: null,
      }
    }
    if (request.kind === 'postgres') {
      const alias = this.data.sourceAliases.find(
        (a) => a.businessId === businessId && a.id === request.aliasId,
      )
      if (!alias) throw new Error('Configured source unavailable.')
      return {
        filename: alias.label,
        source: 'PostgreSQL',
        scope: { kind: 'postgres', aliasId: alias.id },
        rows: null,
      }
    }
    const book = this.data.workbooks.find(
      (b) => b.businessId === businessId && b.id === request.workbookId,
    )
    if (!book) throw new Error('Sample workbook unavailable.')
    const sheets = book.sheets.filter((s) => request.selected.includes(s.name))
    if (!sheets.length || sheets.length !== request.selected.length)
      throw new Error('Select valid worksheets from the sample workbook.')
    return {
      filename: book.filename,
      source: 'Excel',
      rows: sheets.reduce((n, s) => n + s.rows, 0),
      scope: {
        kind: 'workbook',
        sheets: structuredClone(book.sheets),
        selected: sheets.map((s) => s.name),
      },
    }
  }
  async submit(user: AuthenticatedUser, request: SubmissionRequest) {
    await pending()
    this.check(user, 'Seller')
    if (!user.businessId) throw new Error('Business unavailable.')
    const details = this.sourceDetails(user.businessId, request)
    const seq = 1 + Math.max(...this.data.submissions.map((s) => Number(s.id.slice(4))))
    const id = 'SUB-' + seq
    this.data.submissions.unshift({
      id,
      businessId: user.businessId,
      submittedBy: user.userId,
      submitted: this.timestamp(),
      origin: 'New registration',
      ...details,
    })
    return id
  }
  interrupt(user: AuthenticatedUser, id: string) {
    this.check(user, 'Manager')
    const execution = this.data.executions.find(
      (e) => e.businessId === user.businessId && e.submissionId === id,
    )
    const entered = execution?.batchEvents.at(-1)
    if (
      !execution ||
      execution.stage !== 'Running' ||
      entered?.state !== 'Entered' ||
      !entered.sheet
    )
      throw new Error('No active worksheet batch available to interrupt.')
    const at = this.timestamp()
    execution.batchEvents.push({ ...entered, state: 'Interrupted', at })
    execution.stage = 'Interrupted'
    execution.scopeOutcome = 'Incomplete'
    execution.events.push({ stage: 'Interrupted', at })
  }
  advance(user: AuthenticatedUser, id: string, fail = false) {
    this.check(user, 'Manager')
    const s = this.data.submissions.find((s) => s.id === id && s.businessId === user.businessId)
    if (!s) throw new Error('Submission unavailable.')
    const e = this.data.executions.find((e) => e.submissionId === id)
    if (!e) {
      const stage = 'Request Received'
      this.data.executions.unshift({
        id: 'EX-' + id.slice(4),
        receiptId: 'ACK-' + id.slice(4),
        submissionId: id,
        businessId: s.businessId,
        stage,
        accountedRows: null,
        batchEvents: [],
        scopeOutcome: 'Unverified',
        events: [{ stage, at: this.timestamp() }],
      })
      return
    }
    const at = this.timestamp()
    const scope = s.scope
    if (scope.kind === 'workbook' && e.stage === 'Running' && !fail) {
      const entered = e.batchEvents.at(-1)
      if (!entered || entered.state !== 'Entered')
        throw new Error('No worksheet batch in progress.')
      const sheet = scope.sheets.find((s) => s.name === entered.sheet)
      if (!sheet) throw new Error('Worksheet evidence unavailable.')
      e.batchEvents.push({ ...entered, state: 'Completed', rows: sheet.rows, at })
      e.accountedRows = (e.accountedRows ?? 0) + sheet.rows
      const nextSheet = scope.selected[entered.batch]
      if (nextSheet) {
        e.batchEvents.push({
          batch: entered.batch + 1,
          sheet: nextSheet,
          state: 'Entered',
          rows: null,
          at,
        })
        return
      }
      // Every selected worksheet has an explicit completion event.
      e.scopeOutcome = 'Reconciled in simulation'
    }
    const next: Partial<Record<Stage, Stage>> = {
      'Request Received': 'Queued',
      Queued: 'Running',
      Running: fail ? 'Failed' : 'Succeeded',
    }
    const stage = next[e.stage]
    if (!stage || stage === 'Not Requested') throw new Error('Execution already finished.')
    e.stage = stage
    e.events.push({ stage, at })
    if (stage === 'Running')
      e.batchEvents.push({
        batch: 1,
        sheet: scope.kind === 'workbook' ? scope.selected[0] : null,
        state: 'Entered',
        rows: null,
        at,
      })
    if (stage === 'Succeeded' && scope.kind !== 'workbook') {
      e.accountedRows = s.rows
      e.batchEvents.push({ batch: 1, sheet: null, state: 'Completed', rows: s.rows, at })
    }
    if (stage === 'Failed') {
      const entered = e.batchEvents.at(-1)
      if (entered?.state === 'Entered') e.batchEvents.push({ ...entered, state: 'Failed', at })
      e.scopeOutcome = 'Incomplete'
      e.error = 'The simulated worker stopped before completion.'
    }
  }
  decide(user: AuthenticatedUser, id: string, decision: Decision) {
    const manager = this.check(user, 'Manager')
    const record = this.data.quarantine.find(
      (r) => r.id === id && r.businessId === manager.businessId,
    )
    if (!record || (record.resolution && record.resolution.decision !== 'Retain for Review'))
      throw new Error('This record is unavailable for review.')
    allowed(reviewActions.includes(decision), 'Unsupported review decision.')

    const resolution = { decision, actor: manager.fullName, at: this.timestamp() }
    const event = {
      ...resolution,
      recordId: record.id,
      businessId: record.businessId,
      sequence: record.decisionHistory.length + 1,
      actorId: manager.userId,
      evidence: structuredClone(record.evidence),
    }
    record.decisionHistory = [...record.decisionHistory, event]
    record.resolution = resolution
  }
  async narrate(
    user: AuthenticatedUser,
    id: string,
    scenario: 'Available' | 'Unavailable' | 'Failed',
  ) {
    await pending(400)
    this.check(user, 'Seller')
    const item = this.data.explanations.find((e) => e.id === id && e.businessId === user.businessId)
    if (!item || scenario === 'Failed') throw new Error('The simulated explanation request failed.')
    return scenario === 'Available' ? item.narrative : null
  }
}
export const demoRoles = Object.values(ROLE_MAP)
export const demoCredential = {
  password: DEMO_PASSWORD,
  usernames: { Admin: 'admin', Manager: 'manager', Seller: 'seller' },
} as const
