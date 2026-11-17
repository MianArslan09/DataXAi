import { expect, test } from '@playwright/test'
import { initialRuleDraft } from '../src/services/contracts'
import { DemoProvider, demoCredential } from '../src/services/demo'
import { countDetectionAlerts, detectionOutcome } from '../src/services/detection'
import { login, nav } from './helpers'

test('Stage 3C keeps method evidence separate and deduplicates the affected source row', async () => {
  const service = new DemoProvider()
  const manager = await service.login('manager', demoCredential.password)
  const results = service.readDetectionEvidence(manager, 'loom', 'SUB-1048')
  expect(results).toHaveLength(7)

  const mug = results.filter((result) => result.groupId === 'FND-042')
  expect(mug.map((result) => result.id)).toEqual(['BR-042', 'ZS-042', 'IF-042'])
  expect(mug.map((result) => result.assessment.method)).toEqual([
    'Business Rules',
    'Z-score',
    'Isolation Forest',
  ])
  expect(mug.map((result) => detectionOutcome(result.assessment))).toEqual([
    'Flagged',
    'Flagged',
    'Flagged',
  ])
  expect(countDetectionAlerts(mug)).toEqual({ alerts: 3, rows: 1 })
  expect(countDetectionAlerts(results)).toEqual({ alerts: 3, rows: 1 })

  const order = mug[0].order
  expect(order).toMatchObject({
    product: 'Ceramic Mug',
    quantity: 3,
    sourceUnit: 40,
    supportedUnit: 4,
  })
  expect(order!.quantity * (order!.supportedUnit - order!.sourceUnit)).toBe(-108)
})

test('Stage 3C distinguishes strict score thresholds, absent alerts and unavailable assessments', async () => {
  const service = new DemoProvider()
  const manager = await service.login('manager', demoCredential.password)
  const results = service.readDetectionEvidence(manager, 'loom', 'SUB-1048')
  const outcome = (id: string) => {
    const result = results.find((item) => item.id === id)
    expect(result, `Missing fixture result ${id}`).toBeDefined()
    return detectionOutcome(result!.assessment)
  }

  expect(outcome('BR-018')).toBe('Assessed without alert')
  expect(outcome('ZS-018')).toBe('Assessed without alert') // |-3| = 3, not greater than 3
  expect(outcome('IF-018')).toBe('Not evaluated')
  expect(outcome('IF-019')).toBe('Unavailable')
  expect(outcome('ZS-042')).toBe('Flagged') // |+3.6| > 3
  expect(outcome('IF-042')).toBe('Flagged') // decision_function -0.12 < 0
  expect(
    countDetectionAlerts(
      results.filter((result) => result.assessment.method === 'Isolation Forest'),
    ),
  ).toEqual({ alerts: 1, rows: 1 })
  expect(
    countDetectionAlerts(
      results.filter((result) => result.id === 'IF-018' || result.id === 'IF-019'),
    ),
  ).toEqual({ alerts: 0, rows: 0 })
})

test('Stage 3D provider restricts direct evidence reads to the currently assigned Manager', async () => {
  const service = new DemoProvider()
  const password = demoCredential.password
  const manager = await service.login('manager', password)
  const original = service.readFindingEvidence(manager, 'loom', 'SUB-1048', 'ROW-1048-042')
  expect(original.results.map((result) => result.id)).toEqual(['BR-042', 'ZS-042', 'IF-042'])
  expect(() => service.readFindingEvidence(manager, 'indus', 'SUB-1048', 'ROW-1048-042')).toThrow()
  expect(() => service.readFindingEvidence(manager, 'loom', 'SUB-1048', 'ROW-FOREIGN')).toThrow()
  expect(() => service.readDetectionEvidence(manager, 'indus')).toThrow()

  for (const name of ['seller', 'admin']) {
    const user = await service.login(name, password)
    expect(service.snapshot(user).detectionEvidence).toEqual([])
    expect(() => service.readFindingEvidence(user, 'loom', 'SUB-1048', 'ROW-1048-042')).toThrow()
  }

  const admin = await service.renew()
  service.assignBusiness(admin, manager.userId, 'indus')
  service.assignBusiness(admin, 'U-05', 'loom')
  const moved = await service.login('manager', password)
  expect(service.snapshot(moved).detectionEvidence).toEqual([])
  expect(() => service.readFindingEvidence(manager, 'loom', 'SUB-1048', 'ROW-1048-042')).toThrow()
  expect(() => service.readFindingEvidence(moved, 'loom', 'SUB-1048', 'ROW-1048-042')).toThrow()
  const replacement = await service.login('bilal', password)
  expect(service.readFindingEvidence(replacement, 'loom', 'SUB-1048', 'ROW-1048-042')).toEqual(
    original,
  )
})

test('Stage 3D links validation only by explicit matching fixture references and preserves history', async () => {
  const service = new DemoProvider()
  const manager = await service.login('manager', demoCredential.password)
  const mug = service.readFindingEvidence(manager, 'loom', 'SUB-1048', 'ROW-1048-042')
  const returned = service.readFindingEvidence(manager, 'loom', 'SUB-1048', 'ROW-1048-018')
  const negative = service.readFindingEvidence(manager, 'loom', 'SUB-1048', 'ROW-1048-019')

  expect(mug.relatedChecks).toEqual([])
  expect(returned.relatedChecks).toHaveLength(1)
  expect(returned.relatedChecks[0]).toMatchObject({
    sample: 'Complete-header sample',
    check: { id: 'VAL-1048-02', field: 'Quantity', observed: -2, outcome: 'Passed' },
  })
  expect(negative.relatedChecks).toHaveLength(1)
  expect(negative.relatedChecks[0]).toMatchObject({
    sample: 'Complete-header sample',
    check: { id: 'VAL-1048-03', field: 'Price', observed: -4, outcome: 'Failed' },
  })

  const before = service.snapshot(manager)
  const draft = structuredClone(initialRuleDraft)
  draft.priceAlertSigma = 4
  expect(service.saveRuleVersion(manager, 'loom', 'RULE-LOOM-002', draft)).toBe('RULE-LOOM-003')
  const after = service.snapshot(manager)
  expect(after.detectionEvidence).toEqual(before.detectionEvidence)
  expect(after.validationEvidence).toEqual(before.validationEvidence)
  expect(after.executions).toEqual(before.executions)
  expect(after.quarantine).toEqual(before.quarantine)
  expect(service.readFindingEvidence(manager, 'loom', 'SUB-1048', 'ROW-1048-042')).toEqual(mug)
})

test('Stage 3C filters recalculate alert counts and Stage 3D shows the grouped evidence', async ({
  page,
}) => {
  await page.goto('/')
  await login(page, 'Manager')
  await nav(page, 'Batch Processing')

  const execution = page.getByRole('tab', { name: 'Execution', exact: true })
  await expect(execution).toHaveAttribute('aria-selected', 'true')
  await page.getByRole('tab', { name: 'Anomaly Detection' }).click()
  const totals = page.locator('.detection [role="status"]')
  await expect(totals).toContainText('3 method alerts')
  await expect(totals).toContainText('1 distinct affected rows')

  const method = page.getByRole('combobox', { name: 'Detection method' })
  const status = page.getByRole('combobox', { name: 'Evaluation status' })
  await method.selectOption('Isolation Forest')
  await expect(totals).toContainText('1 method alerts')
  await status.selectOption('Not evaluated')
  await expect(totals).toContainText('0 method alerts')
  await expect(totals).toContainText('0 distinct affected rows')
  await method.selectOption('')
  await status.selectOption('')

  await page.getByRole('button', { name: 'View Evidence' }).click()
  await expect(page.getByRole('tab', { name: 'Finding Evidence' })).toHaveAttribute(
    'aria-selected',
    'true',
  )
  const evidence = page.getByRole('combobox', { name: 'Finding group / illustrative source row' })
  await expect(evidence).toHaveValue('ROW-1048-042')
  for (const resultId of ['BR-042', 'ZS-042', 'IF-042']) {
    await expect(page.getByText(resultId, { exact: true })).toBeVisible()
  }
  await expect(page.getByText(/Supported minus submitted:.*108/)).toBeVisible()
  await expect(page.getByText('Related row-level validation evidence: unavailable.')).toBeVisible()

  await evidence.selectOption('ROW-1048-018')
  await expect(page.getByText('Related validation · VAL-1048-02')).toBeVisible()
  await expect(
    page.getByText('No supporting-order document is supplied for this row.'),
  ).toBeVisible()
  await evidence.selectOption('ROW-1048-019')
  await expect(page.getByText('Related validation · VAL-1048-03')).toBeVisible()

  await page.getByRole('tab', { name: 'Finding Evidence' }).press('ArrowRight')
  await expect(execution).toBeFocused()
  await execution.press('End')
  await expect(page.getByRole('tab', { name: 'Finding Evidence' })).toBeFocused()
})
