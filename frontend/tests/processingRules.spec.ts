import { expect, test } from '@playwright/test'
import { initialRuleDraft, type RuleConfiguration } from '../src/services/contracts'
import { DemoProvider, demoCredential } from '../src/services/demo'
import { login, nav } from './helpers'

test('rule history belongs to the business after the creating Manager moves', async () => {
  const service = new DemoProvider()
  const password = demoCredential.password
  const manager = await service.login('manager', password)
  const id = service.saveRuleVersion(manager, 'loom', 'RULE-LOOM-002', initialRuleDraft)
  const history = service.snapshot(manager).ruleVersions
  expect(history[0]).toMatchObject({ id, businessId: 'loom', createdBy: manager.userId })
  expect(() => service.saveRuleVersion(manager, 'indus', null, initialRuleDraft)).toThrow()

  const admin = await service.login('admin', password)
  expect(service.snapshot(admin).ruleVersions).toEqual([])
  expect(() => service.saveRuleVersion(admin, 'loom', id, initialRuleDraft)).toThrow()
  service.assignBusiness(admin, manager.userId, 'indus')
  service.assignBusiness(admin, 'U-05', 'loom')
  const moved = await service.login('manager', password)
  expect(service.snapshot(moved).ruleVersions).toEqual([])
  expect(() => service.snapshot(manager)).toThrow()
  expect(() => service.saveRuleVersion(moved, 'loom', id, initialRuleDraft)).toThrow()
  expect(service.saveRuleVersion(moved, 'indus', null, initialRuleDraft)).toBe('RULE-INDUS-001')

  const replacement = await service.login('bilal', password)
  expect(service.snapshot(replacement).ruleVersions).toEqual(history)
  const seller = await service.login('seller', password)
  expect(service.snapshot(seller).ruleVersions).toEqual([])
  expect(() => service.saveRuleVersion(seller, 'loom', id, initialRuleDraft)).toThrow()
  service.logout()
  expect(() => service.snapshot(replacement)).toThrow()
})

test('explicit saves append immutable versions and leave execution history intact', async () => {
  const service = new DemoProvider()
  const manager = await service.login('manager', demoCredential.password)
  service.advance(manager, 'SUB-1047')
  service.advance(manager, 'SUB-1048')
  const before = service.snapshot(manager)
  expect(before.ruleVersions.map((v) => v.id)).toEqual(['RULE-LOOM-002', 'RULE-LOOM-001'])
  const draft = structuredClone(before.ruleVersions[0].configuration)
  draft.priceAlertSigma = 4
  const id = service.saveRuleVersion(manager, 'loom', 'RULE-LOOM-002', draft)
  expect(id).toBe('RULE-LOOM-003')
  const saved = service.snapshot(manager).ruleVersions[0]
  expect(saved.configuration).toEqual(draft)
  draft.requiredColumns.push('ChangedAfterSave')
  saved.configuration.permittedActions.length = 0
  expect(() => service.saveRuleVersion(manager, 'loom', 'RULE-LOOM-002', draft)).toThrow()
  const after = service.snapshot(manager)
  expect(after.ruleVersions.slice(1)).toEqual(before.ruleVersions)
  expect(after.ruleVersions[0].configuration.requiredColumns).not.toContain('ChangedAfterSave')
  expect(after.ruleVersions[0].configuration.permittedActions.length).toBeGreaterThan(0)
  expect(after.executions).toEqual(before.executions)
  for (const ref of ['EX-1047', 'EX-1048']) {
    const execution = after.executions.find((e) => e.id === ref)
    expect(execution).toBeDefined()
    expect(execution).not.toHaveProperty('ruleVersion')
  }
})

test('invalid draft values cannot append a version or authorise automatic repairs', async () => {
  const service = new DemoProvider()
  const manager = await service.login('manager', demoCredential.password)
  const before = service.snapshot(manager).ruleVersions
  const invalid = [
    { requiredColumns: [] },
    { requiredColumns: ['  '] },
    { requiredColumns: ['Quantity', ' quantity '] },
    { unknownCancellation: 'Assume cancelled' },
    { priceAlertSigma: 0 },
    { priceAlertSigma: NaN },
    { quantityAlertSigma: Infinity },
    { quantityAlertSigma: -1 },
    { permittedActions: ['Force positive quantity'] },
    { permittedActions: ['Guess unit price'] },
  ]
  for (const patch of invalid) {
    const draft = { ...initialRuleDraft, ...patch } as RuleConfiguration
    expect(() => service.saveRuleVersion(manager, 'loom', 'RULE-LOOM-002', draft)).toThrow()
  }
  expect(service.snapshot(manager).ruleVersions).toEqual(before)
})

test('Manager can inspect history, cancel a draft and explicitly save a new version', async ({
  page,
}) => {
  await page.goto('/')
  await login(page, 'Manager')
  await nav(page, 'Batch Processing')
  await page.getByRole('tab', { name: 'Processing Rules' }).click()
  const history = page.getByRole('combobox', { name: 'Inspect saved version' })
  await expect(history).toHaveValue('RULE-LOOM-002')
  await history.selectOption('RULE-LOOM-001')
  await expect(page.getByText('Preserve unknown · preserve signed quantities')).toBeVisible()
  await page.getByRole('button', { name: 'Edit current rules' }).click()
  await page.getByRole('spinbutton', { name: 'Unit price alert (σ)' }).fill('5')
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await expect(history.locator('option')).toHaveCount(2)
  await page.getByRole('button', { name: 'Edit current rules' }).click()
  await expect(page.getByRole('spinbutton', { name: 'Unit price alert (σ)' })).toHaveValue('3')
  await page.getByRole('spinbutton', { name: 'Unit price alert (σ)' }).fill('4')
  await page.getByRole('button', { name: 'Save new version' }).click()
  await expect(history).toHaveValue('RULE-LOOM-003')
  await expect(history.locator('option')).toHaveCount(3)
})
