import { expect, test, type Page } from '@playwright/test'
import { DemoProvider } from '../src/services/demo'
import { apiURL, login, logout, nav } from './helpers'

async function go(page: Page, hash: string) {
  await page.evaluate((hash) => {
    location.hash = hash
  }, hash)
}
async function button(page: Page, name: string) {
  await page.getByRole('button', { name, exact: true }).click()
}
async function fits(page: Page, critical: string) {
  await page.evaluate(() => document.fonts.ready)
  const target = page.getByText(critical, { exact: true }).last()
  await expect(target).toBeVisible()
  const box = await target.boundingBox()
  expect(box!.y).toBeGreaterThanOrEqual(0)
  expect(box!.y + box!.height).toBeLessThanOrEqual(900)
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(1440)
  expect(
    await page.evaluate(() =>
      [...document.querySelectorAll('dialog[open],table')].some(
        (e) => e.scrollWidth > e.clientWidth + 1,
      ),
    ),
  ).toBe(false)
}
test('CSV receipt identifies the seeded source and a separate same-name registration', async ({
  page,
}) => {
  await page.goto('/')
  await login(page, 'Seller')
  await nav(page, 'Submit Data')
  await button(page, 'View sample CSV receipt')
  const dialog = page.getByRole('dialog')
  for (const value of [
    'SUB-1048',
    'Seeded sample',
    'Lahore Homewares',
    'CSV',
    'november_orders.csv',
    'Entire submitted file',
    'Registered',
    'Not Requested',
    'Unverified',
  ])
    await expect(dialog).toContainText(value)
  await fits(page, 'Entire submitted file')
  await button(page, 'Close dialog')
  await page
    .locator('input[type=file]')
    .setInputFiles({ name: 'november_orders.csv', mimeType: 'text/csv', buffer: Buffer.from('id') })
  await button(page, 'Submit data')
  await button(page, 'View receipt')
  for (const value of ['SUB-1049', 'New registration', 'Unknown', 'Not Requested'])
    await expect(dialog).toContainText(value)
})

test('named workbook scopes use the fixture only on explicit sample selection', async ({
  page,
}) => {
  await page.goto('/')
  await login(page, 'Seller')
  await nav(page, 'Submit Data')
  await page.getByLabel('Source type').selectOption('Excel')
  await page.getByLabel('Workbook source').selectOption('sample')
  await page.getByLabel(/Marketplace orders/).uncheck()
  await page.getByLabel(/Notes/).uncheck()
  await button(page, 'Submit data')
  await button(page, 'View receipt')
  await expect(
    page.getByRole('row', { name: 'Store orders 512 Yes No entry recorded' }),
  ).toBeVisible()
  await expect(
    page.getByRole('row', { name: 'Marketplace orders 344 No Outside requested scope' }),
  ).toBeVisible()
  await button(page, 'Close dialog')
  await page.getByLabel('Workbook source').selectOption('file')
  await page.locator('input[type=file]').setInputFiles({
    name: 'retail_export.xlsx',
    mimeType: 'application/octet-stream',
    buffer: Buffer.from('opaque'),
  })
  await button(page, 'Submit data')
  await button(page, 'View receipt')
  await expect(page.getByRole('dialog')).toContainText('Worksheet metadata unavailable')
  await expect(page.getByRole('row')).toHaveCount(0)
})

test('configured aliases are selected within the Seller business and never tested as connections', async ({
  page,
}) => {
  await page.goto('/')
  await login(page, 'Seller')
  await nav(page, 'Submit Data')
  await page.getByLabel('Source type').selectOption('PostgreSQL')
  const select = page.getByLabel('Configured demonstration source')
  await expect(select.locator('option')).toHaveCount(2)
  await select.selectOption('PG-LOOM')
  await fits(page, 'Submit data')
  await expect(page.getByText(/Connection status unverified/)).toBeVisible()
  await button(page, 'Submit data')
  await button(page, 'View receipt')
  await expect(page.getByRole('dialog')).toContainText('Configured source PG-LOOM')
  await expect(page.getByRole('dialog')).toContainText(
    'Connection and extraction evidence unavailable',
  )
})

test('running and completed execution separate simulated accounting and committed rows', async ({
  page,
}) => {
  await page.goto('/')
  await login(page, 'Manager')
  await go(page, '/manager/batch?id=SUB-1048')
  for (const action of ['Request processing', 'Queue execution', 'Start simulation'])
    await button(page, action)
  for (const value of [
    'EX-1048',
    'ACK-1048',
    'No version supplied',
    'Authoritative count unavailable',
  ])
    await expect(page.getByText(value, { exact: true })).toBeVisible()
  await fits(page, 'Complete simulation')
  await button(page, 'Complete simulation')
  await expect(page.locator('.card-head .badge')).toHaveText('Succeeded')
  await expect(
    page.locator('dt').filter({ hasText: 'Simulated rows accounted for' }).locator('+ dd'),
  ).toHaveText('1,240')
  await expect(page.getByText('Unverified', { exact: true })).toBeVisible()
  await fits(page, 'Batch 1 · Simulated file batch')
})

test('interrupted execution and worksheet coverage agree across actors and direct drilldown', async ({
  page,
}) => {
  await page.goto('/')
  await login(page, 'Manager')
  await go(page, '/manager/batch?id=SUB-1047')
  for (const action of [
    'Request processing',
    'Queue execution',
    'Start simulation',
    'Complete worksheet batch',
    'Interrupt simulation',
  ])
    await button(page, action)
  await expect(page.locator('.card-head .badge')).toHaveText('Interrupted')
  for (const value of [
    'EX-1047',
    'Incomplete',
    'Batch 1 · Store orders',
    'Authoritative count unavailable',
  ])
    await expect(page.getByText(value, { exact: true })).toBeVisible()
  await expect(page.getByRole('row', { name: '1 · Store orders Completed 512' })).toBeVisible()
  await expect(
    page.getByRole('row', { name: '2 · Marketplace orders Interrupted Unknown' }),
  ).toBeVisible()
  await fits(page, 'Inspect worksheet coverage')
  await page.getByRole('link', { name: 'Inspect worksheet coverage' }).click()
  await expect(page.getByRole('dialog')).toHaveAccessibleName('SUB-1047')
  await button(page, 'Close dialog')
  await logout(page)
  await login(page, 'Seller')
  await go(page, '/seller/status?id=SUB-1047')
  await expect(
    page.getByRole('row', { name: 'Store orders 512 Yes Entered · completed' }),
  ).toBeVisible()
  await expect(
    page.getByRole('row', {
      name: 'Marketplace orders 344 Yes Entered · interrupted before completion',
    }),
  ).toBeVisible()
  await expect(page.getByRole('row', { name: 'Notes 4 No Outside requested scope' })).toBeVisible()
  await fits(page, 'Outside requested scope')
})

test('provider enforces source, role, selection and execution scope', async () => {
  const service = new DemoProvider(),
    seller = await service.login('seller', 'DataXAi-demo')
  await expect(service.submit(seller, { kind: 'postgres', aliasId: 'PG-INDUS' })).rejects.toThrow(
    /unavailable/,
  )
  for (const selected of [[], ['nonexistent'], ['Notes', 'Notes']])
    await expect(
      service.submit(seller, { kind: 'workbook', workbookId: 'WB-LOOM', selected }),
    ).rejects.toThrow(/Select valid/)
  expect(() => service.advance(seller, 'SUB-1047')).toThrow(/Access denied/)
  const manager = await service.login('manager', 'DataXAi-demo')
  for (let i = 0; i < 4; i++) service.advance(manager, 'SUB-1047')
  const snapshot = () =>
    service.snapshot(manager).executions.find((e) => e.submissionId === 'SUB-1047')!
  const before = snapshot()
  expect(before.scopeOutcome).toBe('Unverified')
  expect(before.accountedRows).toBe(512)
  service.interrupt(manager, 'SUB-1047')
  expect(snapshot().batchEvents.slice(0, -1)).toEqual(before.batchEvents)
  expect(() => service.advance(manager, 'SUB-1047')).toThrow(/already finished/)
  const other = await service.login('bilal', 'DataXAi-demo')
  expect(() => service.interrupt(other, 'SUB-1047')).toThrow(/No active worksheet/)
  expect(() => service.advance(other, 'SUB-1047')).toThrow(/unavailable/)
  const otherSeller = await service.login('noor', 'DataXAi-demo')
  expect(service.snapshot(otherSeller).workbooks).toEqual([])
  await expect(
    service.submit(otherSeller, { kind: 'workbook', workbookId: 'WB-LOOM', selected: ['Notes'] }),
  ).rejects.toThrow(/unavailable/)
})
test('selected workbook scope completes only after every selected batch finishes', async () => {
  const s = new DemoProvider(),
    m = await s.login('manager', 'DataXAi-demo')
  for (let i = 0; i < 5; i++) s.advance(m, 'SUB-1047')
  const e = s.snapshot(m).executions.find((e) => e.id === 'EX-1047')!
  expect(e).toMatchObject({
    stage: 'Succeeded',
    accountedRows: 856,
    scopeOutcome: 'Reconciled in simulation',
  })
  expect(
    e.batchEvents.filter((event) => event.state === 'Completed').map((event) => event.sheet),
  ).toEqual(['Store orders', 'Marketplace orders'])
})
test('a failed worksheet keeps completed batches and closes active traversal', async () => {
  const service = new DemoProvider()
  const manager = await service.login('manager', 'DataXAi-demo')
  for (let i = 0; i < 4; i++) service.advance(manager, 'SUB-1047')
  service.advance(manager, 'SUB-1047', true)
  const e = service.snapshot(manager).executions.find((e) => e.id === 'EX-1047')!
  expect(e).toMatchObject({ stage: 'Failed', scopeOutcome: 'Incomplete', accountedRows: 512 })
  expect(e.batchEvents.at(-1)).toMatchObject({ batch: 2, state: 'Failed', rows: null })
})

test('mobile navigation reaches Seller coverage and Manager batch controls', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await login(page, 'Seller')
  await button(page, 'Toggle navigation')
  await nav(page, 'Submission Status')
  await button(page, 'retail_export.xlsx')
  const dialog = page.getByRole('dialog')
  await expect(dialog).toContainText('No entry recorded')
  expect(await dialog.evaluate((el) => el.scrollWidth <= el.clientWidth + 1)).toBe(true)
  await page.keyboard.press('Escape')
  await expect(dialog).toHaveCount(0)
  await button(page, 'Toggle navigation')
  await logout(page)
  await login(page, 'Manager')
  await button(page, 'Toggle navigation')
  await nav(page, 'Batch Processing')
  await page.getByRole('combobox', { name: 'Submission', exact: true }).selectOption('SUB-1047')
  for (const label of [
    'Request processing',
    'Queue execution',
    'Start simulation',
    'Complete worksheet batch',
    'Interrupt simulation',
  ])
    await button(page, label)
  await expect(page.getByRole('row', { name: '1 · Store orders Completed 512' })).toBeVisible()
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390)
})

test('foreign submission and execution deep links do not expose data', async ({ page }) => {
  await page.goto('/')
  await login(page, 'Manager')
  for (const id of ['SUB-1030', 'EX-1030']) {
    await go(page, '/manager/batch?id=' + id)
    await expect(page.getByRole('option', { name: 'Submission unavailable' })).toHaveCount(1)
    await expect(page.getByText('fabric_orders.csv', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('button', { name: 'Request processing' })).toHaveCount(0)
  }
  await logout(page)
  await login(page, 'Seller')
  await go(page, '/seller/status?id=SUB-1030')
  await expect(page.getByText('Submission unavailable for this workspace.')).toBeVisible()
  await expect(page.getByRole('dialog')).toHaveCount(0)
})
test('API-authenticated seller cannot reach demonstration registration', async ({ page }) => {
  const access =
    'stub.' +
    Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 3600 })).toString(
      'base64url',
    ) +
    '.stub'
  await page.route('**/api/auth/token/', (r) => r.fulfill({ json: { access, refresh: 'stub' } }))
  await page.route('**/api/auth/me/', (r) =>
    r.fulfill({ json: { id: 3, username: 'seller', role: 'business_analyst', is_active: true } }),
  )
  await page.goto(apiURL)
  await page.getByLabel('Username', { exact: true }).fill('seller')
  await page.getByLabel('Password', { exact: true }).fill('stub')
  await button(page, 'Sign in')
  await expect(page.getByRole('heading', { name: 'Business context unavailable' })).toBeVisible()
  await go(page, '/seller/submit')
  await expect(page.getByRole('heading', { name: 'Business context unavailable' })).toBeVisible()
  await expect(page.getByLabel('Source type')).toHaveCount(0)
  await expect(page.getByText('PG-LOOM')).toHaveCount(0)
})
