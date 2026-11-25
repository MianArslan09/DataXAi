import type { RepairEvidence } from './contracts'

export const detectionMethods = ['Business Rules', 'Z-score', 'Isolation Forest'] as const
export const detectionOutcomes = [
  'Flagged',
  'Assessed without alert',
  'Not evaluated',
  'Unavailable',
] as const

export type DetectionMethod = (typeof detectionMethods)[number]
export type DetectionOutcome = (typeof detectionOutcomes)[number]

type Assessment =
  | { method: 'Business Rules'; predicate: string; matched: boolean }
  | { method: 'Z-score'; score: number; threshold: number; thresholdVersion: string }
  | { method: 'Isolation Forest'; score: number }
  | { method: DetectionMethod; missing: 'Not evaluated' | 'Unavailable' }

export type DetectionResult = Readonly<{
  id: string
  businessId: string
  submissionId: string
  groupId: string
  sourceRowReference: string
  invoice: string | null
  field: 'Price' | 'Quantity'
  observed: number
  assessment: Readonly<Assessment>
  reason: string
  order?: Readonly<RepairEvidence>
  validationRef?: Readonly<{ sample: string; checkId: string }>
}>

export function detectionOutcome(assessment: DetectionResult['assessment']): DetectionOutcome {
  if ('missing' in assessment) return assessment.missing
  let flagged: boolean
  switch (assessment.method) {
    case 'Business Rules':
      flagged = assessment.matched
      break
    case 'Z-score':
      flagged = Math.abs(assessment.score) > assessment.threshold
      break
    case 'Isolation Forest':
      flagged = assessment.score < 0
      break
  }
  return flagged ? 'Flagged' : 'Assessed without alert'
}

function signed(value: number) {
  return value > 0 ? '+' + value : String(value)
}

export function assessmentComparison(result: DetectionResult) {
  const assessment = result.assessment
  if ('missing' in assessment) {
    return assessment.missing === 'Not evaluated' ? 'No assessment' : 'No result supplied'
  }
  switch (assessment.method) {
    case 'Business Rules':
      return assessment.matched ? 'Condition met' : 'Condition absent'
    case 'Z-score': {
      const threshold = assessment.threshold
      const label = Number.isInteger(threshold) ? threshold.toFixed(1) : String(threshold)
      return 'z = ' + signed(assessment.score) + ' · threshold ' + label
    }
    case 'Isolation Forest':
      return 'Score ' + signed(assessment.score) + ' · cutoff 0'
  }
}

export function assessmentCondition(assessment: DetectionResult['assessment']) {
  if (assessment.method === 'Isolation Forest') {
    return 'decision_function < 0 flags the row. A supplied score ≥ 0 raises no alert.'
  }
  if (assessment.method === 'Z-score') return 'Flag when abs(z_score) > threshold.'
  return 'predicate' in assessment ? assessment.predicate : 'Condition unavailable.'
}

export function countDetectionAlerts(results: readonly DetectionResult[]) {
  const flagged = results.filter((result) => detectionOutcome(result.assessment) === 'Flagged')
  const rows = new Set(
    flagged.map((result) =>
      JSON.stringify([result.businessId, result.submissionId, result.sourceRowReference]),
    ),
  )
  return { alerts: flagged.length, rows: rows.size }
}
