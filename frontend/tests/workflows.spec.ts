import { test, expect } from '@playwright/test'
import { login, logout, nav } from './helpers'

test.beforeEach(async ({ page }) => {
  await page.goto('/')
})
test('configuration controls the evidence reminder and original confirmation keeps the source', async ({
  page,
}) => {
  await login(page, 'Admin')
  await nav(page, 'System Configuration')
  await page.getByRole('checkbox').uncheck()
  await logout(page)
  await login(page, 'Manager')
  await nav(page, 'Quarantine Review')
  await page.getByRole('button', { name: 'Confirm Original', exact: true }).click()
  await expect(page.getByRole('dialog')).not.toContainText('Supported unit price')
  await page.getByRole('button', { name: 'Confirm decision' }).click()
  await expect(page.getByText('Original confirmed', { exact: true }).last()).toBeVisible()
  await expect(page.getByRole('row', { name: 'Line total £120 £12' })).toBeVisible()
})
test('a seller can reach the explanation from the mobile navigation', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await login(page, 'Seller')
  const toggle = page.getByRole('button', { name: 'Toggle navigation' })
  await toggle.click()
  await expect(toggle).toHaveAttribute('aria-expanded', 'true')
  await page.keyboard.press('Escape')
  await expect(toggle).toHaveAttribute('aria-expanded', 'false')
  await toggle.click()
  await nav(page, 'Repair Explanations')
  await expect(page.getByRole('heading', { name: 'DEMO-INV-042' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
})
test('login validation, invalid credentials, role guard and expiry', async ({ page }) => {
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Enter your username')
  await page.getByLabel('Username', { exact: true }).fill('incorrect')
  await page.getByLabel('Password', { exact: true }).fill('wrong')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('incorrect')
  await page.clock.install()
  await login(page, 'Seller')
  await page.evaluate(() => {
    location.hash = '/admin/users'
  })
  await expect(page.getByRole('heading', { name: 'Access denied' })).toBeVisible()
  await page.clock.fastForward(31 * 60 * 1000)
  await expect(page.getByRole('alert')).toContainText('Session expired')
})
test('admin creates local account, rejects duplicates and changes its business', async ({
  page,
}) => {
  await login(page, 'Admin')
  await nav(page, 'Create Account')
  const submit = page.getByRole('button', { name: 'Create account', exact: true })
  await submit.click()
  await expect(page.getByRole('alert')).toContainText('required')
  async function fill() {
    await page.getByLabel('Full name').fill('Test Seller')
    await page.getByLabel('Username').fill('test.seller')
    await page.getByLabel('Email address').fill('test.seller@example.test')
    await page.getByLabel('Account role').selectOption('business_analyst')
    await page
      .getByRole('combobox', { name: 'Business assignment', exact: true })
      .selectOption('loom')
  }
  await fill()
  await submit.click()
  await expect(page.getByRole('status')).toContainText('No Django account was created')
  await fill()
  await submit.click()
  await expect(page.getByRole('alert')).toContainText('already exists')
  await nav(page, 'Users')
  await page.getByRole('searchbox').fill('test.seller')
  await expect(page.locator('tbody tr')).toHaveCount(1)
  await nav(page, 'Business Assignments')
  await page.getByLabel('Business for Test Seller').selectOption('indus')
  await logout(page)
  await page.getByLabel('Username', { exact: true }).fill('test.seller')
  await page.getByLabel('Password', { exact: true }).fill('DataXAi-demo')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByText('Indus Textiles')).toBeVisible()
  await expect(page.getByText('fabric_orders.csv')).toBeVisible()
  await expect(page.getByText('november_orders.csv')).toHaveCount(0)
  await expect(page.getByText('No forecast available for this business.')).toBeVisible()
})
test('seller submission carries through manager execution and seller status', async ({ page }) => {
  await login(page, 'Seller')
  await nav(page, 'Submit Data')
  await page.getByRole('button', { name: 'Submit data', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('Choose a CSV')
  await page
    .locator('input[type=file]')
    .setInputFiles({ name: 'retail.csv', mimeType: 'text/csv', buffer: Buffer.from('id\n42') })
  await page.getByRole('button', { name: 'Submit data', exact: true }).click()
  await expect(page.getByRole('status')).toContainText('SUB-1049')
  await logout(page)
  await login(page, 'Manager')
  await nav(page, 'Submissions')
  await page.getByRole('searchbox').fill('retail.csv')
  await page.getByRole('link', { name: 'Inspect', exact: true }).click()
  for (const [action, stage] of [
    ['Request processing', 'Request Received'],
    ['Queue execution', 'Queued'],
    ['Start simulation', 'Running'],
    ['Complete simulation', 'Succeeded'],
  ]) {
    await page.getByRole('button', { name: action, exact: true }).click()
    await expect(page.locator('.card-head .badge')).toHaveText(stage)
  }
  await expect(page.getByText('ACK-1049')).toBeVisible()
  await expect(page.getByText('EX-1049')).toBeVisible()
  await logout(page)
  await login(page, 'Seller')
  await nav(page, 'Submission Status')
  await page.getByRole('searchbox').fill('retail.csv')
  await page.getByLabel('Filter by execution status').selectOption('Succeeded')
  await page.getByRole('button', { name: 'retail.csv' }).click()
  await expect(page.getByRole('dialog')).toContainText('Unknown')
})
test('failed execution has an error and no simulated completion count', async ({ page }) => {
  await login(page, 'Manager')
  await nav(page, 'Batch Processing')
  for (const name of [
    'Request processing',
    'Queue execution',
    'Start simulation',
    'Simulate failure',
  ])
    await page.getByRole('button', { name, exact: true }).click()
  await expect(page.locator('.card-head .badge')).toHaveText('Failed')
  await expect(page.getByText(/simulated worker stopped/)).toBeVisible()
  await expect(page.getByText('Unavailable', { exact: true })).toBeVisible()
})
test('quarantine selection, cancel, retain and authorise preserve evidence', async ({ page }) => {
  await login(page, 'Manager')
  await nav(page, 'Quarantine Review')
  await page.getByRole('searchbox').fill('039')
  await expect(page.getByRole('heading', { name: 'DEMO-INV-039' })).toBeVisible()
  await page.getByRole('searchbox').fill('042')
  await page.getByRole('button', { name: 'Confirm Original', exact: true }).click()
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  for (const name of ['Retain for Review', 'Authorise Supported Correction']) {
    await page.getByRole('button', { name, exact: true }).click()
    await page.getByRole('button', { name: 'Confirm decision' }).click()
    await expect(page.getByRole('status')).toContainText('No PostgreSQL resolution')
  }
  await expect(page.getByRole('row', { name: 'Unit price £40 £4' })).toBeVisible()
  await expect(
    page.getByText('Correction authorised — not applied', { exact: true }).last(),
  ).toBeVisible()
})
test('forecast history and spending horizon stay distinct from repair arithmetic', async ({
  page,
}) => {
  await login(page, 'Seller')
  await nav(page, 'Forecasts')
  await expect(page.getByRole('row', { name: '16 Nov Predicted 107' })).toBeVisible()
  await nav(page, 'Customer Spending')
  await expect(page.getByRole('row', { name: /CUST-018 90 days £284/ })).toBeVisible()
  await nav(page, 'Repair Explanations')
  await expect(page.getByText('Simulated narrative')).toBeVisible()
  for (const [mode, status] of [
    ['Unavailable', 'Explanation Unavailable'],
    ['Failed', 'Request failed'],
    ['Available', 'Simulated narrative'],
  ]) {
    await page.getByLabel('Demo response').selectOption(mode)
    await expect(page.getByText(status, { exact: true })).toBeVisible()
    await expect(page.getByText(/The manager authorised £4/)).toBeVisible()
    await expect(page.getByText('Not Yet Recomputed')).toBeVisible()
  }
})
