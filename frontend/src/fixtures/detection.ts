import type { RepairEvidence, RuleVersion } from '../services/contracts'
import type { DetectionResult } from '../services/detection'

// Capture the seed version's thresholds once. Subsequent saves never re-evaluate these examples.
export function detectionExamples(
  order: RepairEvidence,
  version: RuleVersion,
): readonly DetectionResult[] {
  const scope = { businessId: 'loom', submissionId: 'SUB-1048' }
  const mug = {
    ...scope,
    groupId: 'FND-042',
    sourceRowReference: 'ROW-1048-042',
    invoice: 'DEMO-INV-042',
    field: 'Price' as const,
    observed: order.sourceUnit,
    order,
  }
  const returned = {
    ...scope,
    groupId: 'FND-018',
    sourceRowReference: 'ROW-1048-018',
    invoice: 'C1048-R',
    field: 'Quantity' as const,
    observed: -2,
  }

  return [
    {
      ...mug,
      id: 'BR-042',
      assessment: {
        method: 'Business Rules',
        predicate: 'Submitted unit price differs from the matching order unit price.',
        matched: mug.observed !== order.supportedUnit,
      },
      reason: 'The two unit prices disagree for the same Ceramic Mug line.',
    },
    {
      ...mug,
      id: 'ZS-042',
      assessment: {
        method: 'Z-score',
        score: 3.6,
        threshold: version.configuration.priceAlertSigma,
        thresholdVersion: version.id,
      },
      reason:
        'The magnitude of the supplied positive price score exceeds the saved illustrative threshold.',
    },
    {
      ...mug,
      id: 'IF-042',
      assessment: { method: 'Isolation Forest', score: -0.12 },
      reason: 'The independent fixture decision score lies below the zero cutoff.',
    },
    {
      ...returned,
      id: 'BR-018',
      validationRef: { sample: 'Complete-header sample', checkId: 'VAL-1048-02' },
      assessment: {
        method: 'Business Rules',
        predicate: 'Negative quantity without a supporting cancellation or return.',
        matched: false,
      },
      reason:
        'Cancellation invoice C1048-R supports a return of two units. The signed quantity is preserved.',
    },
    {
      ...returned,
      id: 'ZS-018',
      assessment: {
        method: 'Z-score',
        score: -3,
        threshold: version.configuration.quantityAlertSigma,
        thresholdVersion: version.id,
      },
      reason:
        'The magnitude of the supplied quantity score equals the threshold. An alert requires a strictly greater magnitude.',
    },
    {
      ...returned,
      id: 'IF-018',
      assessment: { method: 'Isolation Forest', missing: 'Not evaluated' },
      reason: 'This return was excluded from the illustrative Isolation Forest assessment.',
    },
    {
      ...scope,
      groupId: 'FND-019',
      sourceRowReference: 'ROW-1048-019',
      invoice: null,
      field: 'Price',
      observed: -4,
      id: 'IF-019',
      validationRef: { sample: 'Complete-header sample', checkId: 'VAL-1048-03' },
      assessment: { method: 'Isolation Forest', missing: 'Unavailable' },
      reason:
        'The row has a source price observation, but this sample supplies no model decision score.',
    },
  ]
}
