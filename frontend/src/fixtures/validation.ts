import { sourceHeaders, type SubmissionValidation } from '../services/contracts'

// Independent examples for one submission. No source file or execution produced these observations.
export const validationExample: SubmissionValidation = {
  businessId: 'loom',
  submissionId: 'SUB-1048',
  samples: [
    {
      name: 'Complete-header sample',
      observedHeaders: sourceHeaders,
      checks: [
        {
          id: 'VAL-1048-01',
          level: 'Row',
          sourceRow: 'ROW-1048-017',
          evaluatedRuleVersion: 'RULE-LOOM-002',
          field: 'Customer ID',
          observed: null,
          outcome: 'Passed',
          reason:
            'The header is present. A null customer identifier is permitted for this field. Customer identity remains unknown.',
        },
        {
          id: 'SCHEMA-1048-01',
          level: 'Schema',
          field: 'Customer ID',
          observed: 'Present',
          outcome: 'Passed',
          reason: 'All eight required source headers are present in this sample.',
        },
        {
          id: 'VAL-1048-02',
          level: 'Row',
          sourceRow: 'ROW-1048-018',
          field: 'Quantity',
          observed: -2,
          outcome: 'Passed',
          evaluatedRuleVersion: 'RULE-LOOM-002',
          reason:
            'The sample cancellation invoice C1048-R records a return of two units. The signed quantity is preserved.',
        },
        {
          id: 'VAL-1048-03',
          level: 'Row',
          sourceRow: 'ROW-1048-019',
          field: 'Price',
          observed: -4,
          outcome: 'Failed',
          reason:
            'The sample has a negative unit price. The original value is retained for review.',
        },
      ],
    },
    {
      name: 'Missing-header variant',
      observedHeaders: sourceHeaders.filter((field) => field !== 'Customer ID'),
      checks: [
        {
          id: 'SCHEMA-1048-02',
          level: 'Schema',
          field: 'Customer ID',
          observed: 'Header absent',
          outcome: 'Failed',
          reason:
            'The source contract requires the Customer ID header even when individual identifiers are null.',
        },
        {
          id: 'VAL-1048-04',
          level: 'Row',
          field: 'Customer ID',
          observed: 'No cell available',
          outcome: 'Not evaluated',
          reason: 'The missing header prevents assessment of customer identifiers in this variant.',
        },
      ],
    },
  ],
}
