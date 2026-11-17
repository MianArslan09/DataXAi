import { expect, test } from '@playwright/test'
import { DemoProvider, demoCredential } from '../src/services/demo'
import { login, nav } from './helpers'

// Stage 4B/4C demonstration tests. All selectors correspond to the
// reviewed Seller JSX; no live Django, Groq or prediction models are used.
test.beforeEach(async ({ page }) => {
  await page.goto('/')
})

test('Stage 4B shows the original and processed repair snapshot with its provenance', async ({
  page,
}) => {
  await login(page, 'Seller')
  await nav(page, 'Repair Explanations')

  await expect(page.getByText('Post-commit demonstration scenario', { exact: true })).toBeVisible()
  await expect(page.getByText(/REP-042.*Lahore Homewares/)).toBeVisible()
  await expect(page.getByRole('heading', { name: 'DEMO-INV-042' })).toBeVisible()
  await expect(page.getByRole('row', { name: 'Unit price £40 £4' })).toBeVisible()
  await expect(page.getByRole('row', { name: 'Line total £120 £12' })).toBeVisible()
  await expect(page.getByText('Transaction difference', { exact: true })).toBeVisible()
  await expect(page.getByText('−£108', { exact: true })).toBeVisible()
  await expect(page.getByText(/Authorised by M Arslan Ahmad/)).toBeVisible()
  await expect(page.locator('.evidence-note')).toContainText('Order confirmation OC-1042')
  await expect(page.getByRole('heading', { name: 'Recorded repair facts' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Template explanation' })).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Optional Groq explanation' })).toBeVisible()
  await expect(page.getByText('EV-042 · original source preserved')).toBeVisible()
  // There is only one seeded explanation: do not add an inactive selector.
  await expect(page.getByRole('combobox', { name: 'Recorded repair snapshot' })).toHaveCount(0)
})

test('Stage 4B keeps facts and template visible across narrative response states', async ({
  page,
}) => {
  await login(page, 'Seller')
  await nav(page, 'Repair Explanations')

  const scenario = page.getByLabel('Demo response')
  await expect(page.getByText('Simulated narrative', { exact: true })).toBeVisible()
  await scenario.selectOption('Unavailable')
  await expect(page.getByText('Explanation Unavailable', { exact: true })).toBeVisible()
  await expect(page.getByText('Simulated narrative', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/The manager authorised £4 per Ceramic Mug/)).toBeVisible()
  await expect(page.getByRole('row', { name: 'Line total £120 £12' })).toBeVisible()

  // Change modes without waiting for an earlier request. Its completion must
  // not leak into the currently selected scenario.
  await scenario.selectOption('Available')
  await scenario.selectOption('Failed')
  await expect(page.getByText('Request failed', { exact: true })).toBeVisible()
  await expect(page.getByText('Simulated narrative', { exact: true })).toHaveCount(0)
  await expect(page.getByText(/The manager authorised £4 per Ceramic Mug/)).toBeVisible()

  await scenario.selectOption('Available')
  await expect(page.getByText('Simulated narrative', { exact: true })).toBeVisible()
  await expect(page.getByText(/reducing recorded historical spending by £108/)).toBeVisible()
})

test('Stage 4C forecast distinguishes historical weeks from predicted weeks and identifies its boundary', async ({
  page,
}) => {
  await login(page, 'Seller')
  await nav(page, 'Forecasts')

  await expect(page.getByRole('heading', { name: 'Product forecasts' })).toBeVisible()
  const chart = page.getByRole('img', { name: /Ceramic Mug weekly demand in units/ })
  await expect(chart).toBeVisible()
  await expect(chart).toHaveAttribute('aria-label', /12 Oct 2026 Historical 68/)
  await expect(chart).toHaveAttribute('aria-label', /16 Nov 2026 Predicted 107/)
  await expect(page.getByText('Forecast begins', { exact: true })).toBeVisible()
  await expect(page.getByRole('row', { name: '09 Nov Historical 92' })).toBeVisible()
  await expect(page.getByRole('row', { name: '16 Nov Predicted 107' })).toBeVisible()
  await expect(page.getByRole('row', { name: '30 Nov Predicted 120' })).toBeVisible()
  await expect(page.getByText('Last historical week beginning', { exact: true })).toBeVisible()
  await expect(page.getByText('Prediction horizon', { exact: true })).toBeVisible()
  await expect(page.getByText(/3 weekly periods, beginning/)).toBeVisible()
  await expect(page.getByText(/forecasting performance has not been evaluated/)).toBeVisible()
})

test('Stage 4C spending shows both customer estimates, horizon, currency and as-of date', async ({
  page,
}) => {
  await login(page, 'Seller')
  await nav(page, 'Customer Spending')

  await expect(page.getByRole('heading', { name: 'Customer spending estimates' })).toBeVisible()
  await expect(page.getByText('90-day prediction', { exact: true })).toBeVisible()
  const table = page.getByRole('table')
  await expect(table).toContainText('CLV-015')
  await expect(table).toContainText('CLV-016')
  await expect(table).toContainText('CUST-018')
  await expect(table).toContainText('CUST-026')
  await expect(table).toContainText('£284 GBP')
  await expect(table).toContainText('£196 GBP')
  await expect(table).toContainText('15 Nov 2026')
  await expect(
    page.getByText(/Actual historical spending and realised outcomes are unavailable/),
  ).toBeVisible()
  await expect(
    page.getByText(/before\/after comparison of predicted spending has not been evaluated/),
  ).toBeVisible()
})

test('Stage 4B/4C provider restricts Seller evidence by role and current business assignment', async () => {
  const service = new DemoProvider()
  const seller = await service.login('seller', demoCredential.password)
  const before = service.snapshot(seller)
  expect(before.explanations.map((record) => record.id)).toEqual(['REP-042'])
  expect(before.forecasts.map((record) => record.id)).toEqual(['FC-011'])
  expect(before.spending.map((record) => record.id)).toEqual(['CLV-015', 'CLV-016'])
  expect(before.quarantine).toEqual([])
  expect(before.explanations.every((record) => record.businessId === 'loom')).toBe(true)
  await expect(service.narrate(seller, 'REP-042', 'Available')).resolves.toContain('£108')
  await expect(service.narrate(seller, 'REP-UNKNOWN', 'Available')).rejects.toThrow()

  const manager = await service.login('manager', demoCredential.password)
  const managerView = service.snapshot(manager)
  expect(managerView.explanations).toEqual([])
  expect(managerView.forecasts).toEqual([])
  expect(managerView.spending).toEqual([])
  await expect(service.narrate(manager, 'REP-042', 'Available')).rejects.toThrow()

  const admin = await service.login('admin', demoCredential.password)
  const adminView = service.snapshot(admin)
  expect(adminView.explanations).toEqual([])
  expect(adminView.forecasts).toEqual([])
  expect(adminView.spending).toEqual([])
  service.assignBusiness(admin, seller.userId, 'indus')
  expect(() => service.snapshot(seller)).toThrow(/denied/)
  const reassigned = await service.login('seller', demoCredential.password)
  const after = service.snapshot(reassigned)
  expect(after.explanations).toEqual([])
  expect(after.forecasts).toEqual([])
  expect(after.spending).toEqual([])
  await expect(service.narrate(reassigned, 'REP-042', 'Available')).rejects.toThrow()
  // Reassignment changes access, not ownership or the underlying fixture values.
  const original = await service.login('admin', demoCredential.password)
  service.assignBusiness(original, seller.userId, 'loom')
  const restored = await service.login('seller', demoCredential.password)
  expect(service.snapshot(restored).explanations).toEqual(before.explanations)
  expect(service.snapshot(restored).forecasts).toEqual(before.forecasts)
  expect(service.snapshot(restored).spending).toEqual(before.spending)
})

test('Stage 4C forecast and spending detail remain usable on a mobile viewport', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page, 'Seller')
  const toggle = page.getByRole('button', { name: 'Toggle navigation' })
  await toggle.click()
  await nav(page, 'Forecasts')
  await expect(page.getByRole('img', { name: /Ceramic Mug weekly demand in units/ })).toBeVisible()
  await expect(page.getByRole('row', { name: '16 Nov Predicted 107' })).toBeVisible()
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1)

  await toggle.click()
  await nav(page, 'Customer Spending')
  await expect(page.getByText('Prediction evidence', { exact: true })).toBeVisible()
  await expect(page.getByRole('table')).toContainText('CLV-016')
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
    ),
  ).toBeLessThanOrEqual(1)
})
