import type { AppRole, BackendRole } from '../auth/roleAdapter'
import type { DetectionResult } from './detection'

export type DataSource = 'demo' | 'api'
export type BusinessContext = { businessId: string; businessName: string }
export type Identity = { userId: string; username: string; fullName: string; backendRole: string }
export type AuthenticatedUser = Identity & {
  appRole: AppRole
  businessId: string | null
  businessName: string | null
  dataSource: DataSource
}
export interface AuthService {
  login(username: string, password: string): Promise<AuthenticatedUser>
  /** Renew a live identity or reject an expired session. */
  renew(): Promise<AuthenticatedUser>
  nextCheck(): number
  logout(): void
}
export type Account = Identity & { email: string; businessId: string | null }
export type AccountInput = {
  fullName: string
  username: string
  email: string
  role: BackendRole
  businessId: string
}
export type Stage =
  | 'Not Requested'
  | 'Request Received'
  | 'Queued'
  | 'Running'
  | 'Succeeded'
  | 'Failed'
  | 'Interrupted'

export type Worksheet = { name: string; rows: number }
export type SampleWorkbook = {
  id: string
  businessId: string
  filename: string
  sheets: Worksheet[]
}
export type SourceAlias = { id: string; businessId: string; label: string }
export type SubmissionRequest =
  | string // File registration by filename
  | { kind: 'workbook'; workbookId: string; selected: string[] }
  | { kind: 'postgres'; aliasId: string }
export type RequestedScope =
  | { kind: 'file' }
  | { kind: 'workbook'; sheets: Worksheet[]; selected: string[] }
  | { kind: 'postgres'; aliasId: string }
export type BatchEvent = {
  batch: number
  sheet: string | null
  state: 'Entered' | 'Completed' | 'Interrupted' | 'Failed'
  at: string
  rows: number | null
}
export type Submission = {
  id: string
  businessId: string
  filename: string
  source: 'CSV' | 'Excel' | 'PostgreSQL'
  origin: 'Seeded sample' | 'New registration'
  scope: RequestedScope
  submitted: string
  submittedBy: string
  rows: number | null
}
export type PipelineExecution = {
  id: string
  receiptId: string
  submissionId: string
  businessId: string
  stage: Exclude<Stage, 'Not Requested'>
  events: { stage: Stage; at: string }[]
  accountedRows: number | null
  batchEvents: BatchEvent[]
  ruleVersion?: string
  scopeOutcome: 'Unverified' | 'Incomplete' | 'Reconciled in simulation'
  error?: string
}
export type Decision = 'Confirm Original' | 'Authorise Supported Correction' | 'Retain for Review'
export const reviewActions = [
  'Confirm Original',
  'Authorise Supported Correction',
  'Retain for Review',
] as const satisfies readonly Decision[]

export type RuleConfiguration = {
  requiredColumns: string[]
  unknownCancellation: 'Preserve unknown' | 'Request review'
  priceAlertSigma: number
  quantityAlertSigma: number
  permittedActions: Decision[]
}

export const initialRuleDraft: RuleConfiguration = {
  requiredColumns: ['InvoiceNo', 'Quantity', 'UnitPrice'],
  unknownCancellation: 'Preserve unknown',
  priceAlertSigma: 3,
  quantityAlertSigma: 3,
  permittedActions: ['Retain for Review'],
}

export type RuleVersion = {
  id: string
  businessId: string
  createdBy: string
  creatorName: string
  createdAt: string
  configuration: RuleConfiguration
}

export type Resolution = { decision: Decision; actor: string; at: string }
export type RepairEvidence = {
  id: string
  reference: string
  sourceUnit: number
  supportedUnit: number
  quantity: number
  product: string
  currency: 'GBP'
}
export type ReviewDecisionEvent = Readonly<
  Resolution & {
    recordId: string
    businessId: string
    sequence: number
    actorId: string
    evidence: Readonly<RepairEvidence>
  }
>
export type QuarantineRecord = {
  id: string
  businessId: string
  invoice: string
  reason: string
  evidence: RepairEvidence
  resolution: Resolution | null
  decisionHistory: readonly ReviewDecisionEvent[]
}
export type RepairExplanation = {
  id: string
  businessId: string
  invoice: string
  evidence: RepairEvidence
  actor: string
  at: string
  processedUnit: number
  template: string
  narrative: string
}
export type ForecastSummary = {
  id: string
  businessId: string
  product: string
  weeks: { start: string; units: number; kind: 'Historical' | 'Predicted' }[]
}
export type CustomerSpendingEstimate = {
  id: string
  businessId: string
  customer: string
  amount: number
  currency: 'GBP'
  horizonDays: 90
  asOf: string
}
export const sourceHeaders = [
  'Invoice',
  'StockCode',
  'Description',
  'Quantity',
  'InvoiceDate',
  'Price',
  'Customer ID',
  'Country',
] as const
export type SourceHeader = (typeof sourceHeaders)[number]
export type ValidationOutcome = 'Passed' | 'Failed' | 'Not evaluated' | 'Unavailable'

export type ValidationCheck = Readonly<{
  id: string
  level: 'Schema' | 'Row'
  field: SourceHeader
  sourceRow?: string
  observed: string | number | null
  outcome: ValidationOutcome
  reason: string
  evaluatedRuleVersion?: string
}>

export type ValidationSample = Readonly<{
  name: string
  observedHeaders: readonly SourceHeader[]
  checks: readonly ValidationCheck[]
}>

export type SubmissionValidation = Readonly<{
  businessId: string
  submissionId: string
  samples: readonly ValidationSample[]
}>

export type FindingEvidenceDetail = Readonly<{
  results: readonly DetectionResult[]
  relatedChecks: readonly Readonly<{ sample: string; check: ValidationCheck }>[]
}>

export type Workspace = {
  dataSource: DataSource
  businesses: BusinessContext[]
  accounts: Account[]
  ruleVersions: RuleVersion[]
  validationEvidence: readonly SubmissionValidation[]
  detectionEvidence: readonly DetectionResult[]
  workbooks: SampleWorkbook[]
  sourceAliases: SourceAlias[]
  submissions: Submission[]
  executions: PipelineExecution[]
  quarantine: QuarantineRecord[]
  explanations: RepairExplanation[]
  forecasts: ForecastSummary[]
  spending: CustomerSpendingEstimate[]
  reviewConfirmation: boolean
}
export interface WorkspaceService {
  snapshot(user: AuthenticatedUser): Workspace
  readFindingEvidence(
    user: AuthenticatedUser,
    businessId: string,
    submissionId: string,
    sourceRowReference: string,
  ): FindingEvidenceDetail
  readDetectionEvidence(
    user: AuthenticatedUser,
    businessId: string,
    submissionId?: string,
  ): readonly DetectionResult[]
  readValidationEvidence(
    user: AuthenticatedUser,
    businessId: string,
  ): readonly SubmissionValidation[]
  saveRuleVersion(
    user: AuthenticatedUser,
    businessId: string,
    baseId: string | null,
    draft: RuleConfiguration,
  ): string
  createAccount(user: AuthenticatedUser, input: AccountInput): Promise<void>
  assignBusiness(user: AuthenticatedUser, accountId: string, businessId: string): void
  setReviewReminder(user: AuthenticatedUser, enabled: boolean): void
  submit(user: AuthenticatedUser, request: SubmissionRequest): Promise<string>
  advance(user: AuthenticatedUser, submissionId: string, fail?: boolean): void
  interrupt(user: AuthenticatedUser, submissionId: string): void
  decide(user: AuthenticatedUser, recordId: string, decision: Decision): void
  narrate(
    user: AuthenticatedUser,
    id: string,
    scenario: 'Available' | 'Unavailable' | 'Failed',
  ): Promise<string | null>
}
export const money = (amount: number, currency = 'GBP') =>
  new Intl.NumberFormat('en-GB', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
    minimumFractionDigits: 0,
  }).format(amount)
export const number = (n: number) => new Intl.NumberFormat('en-GB').format(n)
export const date = (iso: string, time = false) =>
  new Intl.DateTimeFormat('en-GB', {
    day: '2-digit',
    month: 'short',
    ...(time ? { hour: '2-digit', minute: '2-digit' } : {}),
    timeZone: 'Asia/Karachi',
  }).format(new Date(iso))
export const pending = (ms = 240) => new Promise<void>((resolve) => setTimeout(resolve, ms))
export const stageOf = (w: Workspace, id: string): Stage =>
  w.executions.find((e) => e.submissionId === id)?.stage ?? 'Not Requested'
export const reviewStatus = (r: QuarantineRecord) => {
  if (!r.resolution) return 'Awaiting Review'
  return {
    'Confirm Original': 'Original confirmed',
    'Authorise Supported Correction': 'Correction authorised — not applied',
    'Retain for Review': 'Retained for Review',
  }[r.resolution.decision]
}
