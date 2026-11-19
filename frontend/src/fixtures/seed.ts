import { detectionExamples } from './detection'
import { validationExample } from './validation'
import { initialRuleDraft, reviewActions, type RuleVersion } from '../services/contracts'
import type {
  Account,
  BusinessContext,
  ForecastSummary,
  PipelineExecution,
  QuarantineRecord,
  RepairEvidence,
  RepairExplanation,
  Submission,
  Workspace,
} from '../services/contracts'

const businesses: BusinessContext[] = [
  { businessId: 'loom', businessName: 'Lahore Homewares' },
  { businessId: 'indus', businessName: 'Indus Textiles' },
  { businessId: 'cedar', businessName: 'Cedar Stationery' },
]
const account = (
  userId: string,
  fullName: string,
  username: string,
  backendRole: string,
  businessId: string | null,
): Account => ({
  userId,
  fullName,
  username,
  backendRole,
  businessId,
  email: username + '@example.test',
})
const accounts = [
  account('U-01', 'M Arslan Ahmad', 'admin', 'admin', null),
  account('U-02', 'M Arslan Ahmad', 'manager', 'data_engineer', 'loom'),
  account('U-03', 'M Arslan Ahmad', 'seller', 'business_analyst', 'loom'),
  account('U-04', 'Noor Ahmed', 'noor', 'business_analyst', 'indus'),
  account('U-05', 'Bilal Khan', 'bilal', 'data_engineer', 'indus'),
  account('U-06', 'Zara Iqbal', 'zara', 'business_analyst', 'cedar'),
  account('U-07', 'Omar Raza', 'omar', 'data_engineer', 'cedar'),
  account('U-08', 'Aiman Tariq', 'aiman', 'business_analyst', 'loom'),
]
const sharedEvidence: RepairEvidence = {
  id: 'EV-042',
  reference: 'Order confirmation OC-1042',
  product: 'Ceramic Mug',
  quantity: 3,
  sourceUnit: 40,
  supportedUnit: 4,
  currency: 'GBP',
}
const records: QuarantineRecord[] = [
  {
    id: 'QR-042',
    businessId: 'loom',
    invoice: 'DEMO-INV-042',
    evidence: sharedEvidence,
    reason: 'Submitted unit price differs from the original order confirmation.',
    resolution: null,
    decisionHistory: [],
  },
  {
    id: 'QR-039',
    businessId: 'loom',
    invoice: 'DEMO-INV-039',
    evidence: {
      ...sharedEvidence,
      id: 'EV-039',
      reference: 'Order confirmation OC-1039',
      product: 'Linen Napkin',
      quantity: 4,
      sourceUnit: 16,
      supportedUnit: 8,
    },
    reason: 'Invoice and order confirmation show different unit prices.',
    resolution: null,
    decisionHistory: [],
  },
]
const explanation: RepairExplanation = {
  id: 'REP-042',
  businessId: 'loom',
  invoice: 'DEMO-INV-042',
  evidence: sharedEvidence,
  processedUnit: 4,
  actor: 'M Arslan Ahmad',
  at: '2026-11-15T04:40:00Z',
  template:
    'The manager authorised £4 per Ceramic Mug using order confirmation OC-1042. Three mugs total £12. The original £120 line remains in the source record.',
  narrative:
    'The original invoice priced each mug at £40. The order evidence supports £4 each, reducing recorded historical spending by £108 for the same three mugs.',
}
const retailWorkbook = {
  id: 'WB-LOOM',
  businessId: 'loom',
  filename: 'retail_export.xlsx',
  sheets: [
    { name: 'Store orders', rows: 512 },
    { name: 'Marketplace orders', rows: 344 },
    { name: 'Notes', rows: 4 },
  ],
}

function submission(
  seq: number,
  businessId: string,
  filename: string,
  day: number,
  rows: number,
  submittedBy = 'U-03',
): Submission {
  return {
    id: 'SUB-' + seq,
    businessId,
    filename,
    submitted: `2026-11-${day}T04:20:00Z`,
    rows,
    submittedBy,
    origin: 'Seeded sample',
    scope:
      seq === 1047
        ? {
            kind: 'workbook',
            sheets: retailWorkbook.sheets,
            selected: ['Store orders', 'Marketplace orders'],
          }
        : { kind: 'file' },
    source: filename.endsWith('.csv') ? 'CSV' : 'Excel',
  }
}
const submissions = [
  submission(1048, 'loom', 'november_orders.csv', 15, 1240),
  submission(1047, 'loom', 'retail_export.xlsx', 14, 856),
  submission(1046, 'loom', 'week_46_sales.csv', 13, 2118),
  submission(1045, 'loom', 'marketplace_orders.csv', 12, 1600),
  submission(1044, 'loom', 'store_returns.xlsx', 11, 184),
  submission(1043, 'loom', 'week_45_sales.csv', 10, 940),
  submission(1030, 'indus', 'fabric_orders.csv', 13, 420, 'U-04'),
]
function finished(s: Submission): PipelineExecution {
  const stages = ['Request Received', 'Queued', 'Running', 'Succeeded'] as const
  return {
    id: 'EX-' + s.id.slice(4),
    receiptId: 'ACK-' + s.id.slice(4),
    businessId: s.businessId,
    submissionId: s.id,
    stage: 'Succeeded',
    accountedRows: s.rows,
    batchEvents: [
      {
        batch: 1,
        sheet: null,
        state: 'Completed',
        rows: s.rows,
        at: new Date(Date.parse(s.submitted) + 240000).toISOString(),
      },
    ],
    scopeOutcome: 'Unverified',
    events: stages.map((stage, i) => ({
      stage,
      at: new Date(Date.parse(s.submitted) + (i + 1) * 60000).toISOString(),
    })),
  }
}
const forecasts: ForecastSummary[] = [
  {
    id: 'FC-011',
    businessId: 'loom',
    product: 'Ceramic Mug',
    weeks: [
      '2026-10-12',
      '2026-10-19',
      '2026-10-26',
      '2026-11-02',
      '2026-11-09',
      '2026-11-16',
      '2026-11-23',
      '2026-11-30',
    ].map((start, i) => ({
      start,
      units: [68, 82, 76, 99, 92, 107, 113, 120][i],
      kind: i < 5 ? 'Historical' : 'Predicted',
    })),
  },
]
// Newest first within each business. The author is a snapshot at creation time.
const loomVersionOne: RuleVersion = {
  id: 'RULE-LOOM-001',
  businessId: 'loom',
  createdBy: 'U-02',
  creatorName: 'M Arslan Ahmad',
  createdAt: '2026-11-12T04:00:00Z',
  configuration: structuredClone(initialRuleDraft),
}
const loomVersionTwo: RuleVersion = {
  ...loomVersionOne,
  id: 'RULE-LOOM-002',
  createdAt: '2026-11-14T04:00:00Z',
  configuration: {
    ...structuredClone(initialRuleDraft),
    requiredColumns: [...initialRuleDraft.requiredColumns, 'CustomerID'],
    unknownCancellation: 'Request review',
    permittedActions: [...reviewActions],
  },
}

export function seed(): Workspace {
  return structuredClone({
    dataSource: 'demo',
    businesses,
    ruleVersions: [loomVersionTwo, loomVersionOne],
    validationEvidence: [validationExample],
    detectionEvidence: detectionExamples(sharedEvidence, loomVersionTwo),
    workbooks: [retailWorkbook],
    sourceAliases: [
      { id: 'PG-LOOM', businessId: 'loom', label: 'Lahore retail transactions' },
      { id: 'PG-INDUS', businessId: 'indus', label: 'Indus retail transactions' },
    ],
    accounts,
    submissions,
    executions: submissions.slice(2, 6).map(finished),
    quarantine: records,
    explanations: [explanation],
    forecasts,
    reviewConfirmation: true,
    spending: [
      {
        id: 'CLV-015',
        businessId: 'loom',
        customer: 'CUST-018',
        amount: 284,
        currency: 'GBP',
        horizonDays: 90,
        asOf: '2026-11-15',
      },
      {
        id: 'CLV-016',
        businessId: 'loom',
        customer: 'CUST-026',
        amount: 196,
        currency: 'GBP',
        horizonDays: 90,
        asOf: '2026-11-15',
      },
    ],
  })
}
