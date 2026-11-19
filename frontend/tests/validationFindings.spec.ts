import { expect, test } from '@playwright/test'
import { initialRuleDraft, sourceHeaders } from '../src/services/contracts'
import { DemoProvider, demoCredential } from '../src/services/demo'
import { login, nav } from './helpers'

test('structural presence, row nullability and signed returns have independent outcomes', async () => {
  const service = new DemoProvider()
  const manager = await service.login('manager', demoCredential.password)
  const [report] = service.readValidationEvidence(manager, 'loom')
  expect(report).toMatchObject({ businessId: 'loom', submissionId: 'SUB-1048' })
  const [complete, missing] = report.samples
  expect(complete.observedHeaders).toEqual(sourceHeaders)
  expect(complete.checks).toContainEqual(
    expect.objectContaining({
      id: 'VAL-1048-01',
      sourceRow: 'ROW-1048-017',
      level: 'Row',
      field: 'Customer ID',
      observed: null,
      outcome: 'Passed',
      evaluatedRuleVersion: 'RULE-LOOM-002',
    }),
  )
  expect(complete.checks).toContainEqual(
    expect.objectContaining({
      field: 'Quantity',
      observed: -2,
      outcome: 'Passed',
      reason: expect.stringContaining('C1048-R'),
    }),
  )
  expect(complete.checks).toContainEqual(
    expect.objectContaining({
      field: 'Price',
      observed: -4,
      outcome: 'Failed',
    }),
  )
  expect(missing.observedHeaders).not.toContain('Customer ID')
  expect(missing.checks.map((c) => [c.level, c.outcome])).toEqual([
    ['Schema', 'Failed'],
    ['Row', 'Not evaluated'],
  ])
  expect(missing.checks.every((c) => c.sourceRow === undefined)).toBe(true)
})

test('direct reads and workspace snapshots follow the current Manager assignment', async () => {
  const service = new DemoProvider()
  const password = demoCredential.password
  const manager = await service.login('manager', password)
  const original = service.snapshot(manager).validationEvidence
  expect(original).toHaveLength(1)
  expect(() => service.readValidationEvidence(manager, 'indus')).toThrow()
  for (const name of ['seller', 'admin']) {
    const user = await service.login(name, password)
    expect(service.snapshot(user).validationEvidence).toEqual([])
    expect(() => service.readValidationEvidence(user, 'loom')).toThrow()
  }
  const admin = await service.renew()
  service.assignBusiness(admin, manager.userId, 'indus')
  service.assignBusiness(admin, 'U-05', 'loom')
  const moved = await service.login('manager', password)
  expect(() => service.readValidationEvidence(manager, 'loom')).toThrow()
  expect(() => service.readValidationEvidence(moved, 'loom')).toThrow()
  expect(service.snapshot(moved).validationEvidence).toEqual([])
  const replacement = await service.login('bilal', password)
  expect(service.readValidationEvidence(replacement, 'loom')).toEqual(original)
})

test('a new rule version preserves historical findings and Stage 2 execution evidence', async () => {
  const service = new DemoProvider()
  const manager = await service.login('manager', demoCredential.password)
  const before = service.snapshot(manager)
  for (let i = 0; i < 4; i++) {
    service.advance(manager, 'SUB-1047')
    service.advance(manager, 'SUB-1048')
  }
  service.interrupt(manager, 'SUB-1047')
  const executions = service.snapshot(manager).executions
  expect(executions.find((e) => e.id === 'EX-1047')).toMatchObject({
    stage: 'Interrupted',
    accountedRows: 512,
    scopeOutcome: 'Incomplete',
  })
  expect(executions.find((e) => e.id === 'EX-1048')).toMatchObject({
    stage: 'Succeeded',
    accountedRows: 1240,
    scopeOutcome: 'Unverified',
  })
  expect(service.saveRuleVersion(manager, 'loom', 'RULE-LOOM-002', initialRuleDraft)).toBe(
    'RULE-LOOM-003',
  )
  const after = service.snapshot(manager)
  expect(after.executions).toEqual(executions)
  expect(after.executions.every((e) => e.ruleVersion === undefined)).toBe(true)
  expect(after.ruleVersions.slice(1)).toEqual(before.ruleVersions)
  expect(after.validationEvidence).toEqual(before.validationEvidence)
  expect(after.validationEvidence[0].samples[0].checks[0].evaluatedRuleVersion).toBe(
    'RULE-LOOM-002',
  )
})

test('Manager can navigate the five tabs and inspect separate sample outcomes', async ({
  page,
}) => {
  await page.goto('/')
  await login(page, 'Manager')
  await nav(page, 'Batch Processing')
  const execution = page.getByRole('tab', { name: 'Execution', exact: true })
  await expect(execution).toHaveAttribute('aria-selected', 'true')
  await execution.focus()
  await page.keyboard.press('ArrowRight')
  await expect(page.getByRole('tab', { name: 'Processing Rules' })).toBeFocused()
  await page.keyboard.press('End')
  await expect(page.getByRole('tab', { name: 'Finding Evidence' })).toBeFocused()
  await page.getByRole('tab', { name: 'Validation Findings' }).click()
  await expect(page.getByText('ROW-1048-017', { exact: true })).toBeVisible()
  await expect(page.getByText('Missing (null)', { exact: true })).toBeVisible()
  await page.getByRole('combobox', { name: 'Demonstration sample' }).selectOption('1')
  await page.getByRole('combobox', { name: 'Filter by outcome' }).selectOption('Not evaluated')
  await expect(page.getByRole('button', { name: 'VAL-1048-04' })).toBeVisible()
  await expect(page.getByText('No cell available', { exact: true })).toBeVisible()
  await page.getByRole('tab', { name: 'Validation Findings' }).press('ArrowRight')
  const detection = page.getByRole('tab', { name: 'Anomaly Detection' })
  await expect(detection).toBeFocused()
  await detection.press('ArrowRight')
  const evidence = page.getByRole('tab', { name: 'Finding Evidence' })
  await expect(evidence).toBeFocused()
  await evidence.press('ArrowRight')
  await expect(execution).toBeFocused()
  await page.getByRole('combobox', { name: 'Submission', exact: true }).selectOption('SUB-1047')
  await page.getByRole('tab', { name: 'Validation Findings' }).click()
  await expect(page.getByText(/No demonstration evidence is available/)).toBeVisible()
})
