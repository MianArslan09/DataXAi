import { expect, test } from '@playwright/test'
import { apiURL } from './helpers'

const jwt =
  'stub.' +
  Buffer.from(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 300 })).toString('base64url') +
  '.stub'
async function enter(page: import('@playwright/test').Page) {
  await page.goto(apiURL)
  await page.getByLabel('Username', { exact: true }).fill('demo.identity')
  await page.getByLabel('Password', { exact: true }).fill('fixture')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
}
test('API mode rejects unknown identity and never shows demonstration records', async ({
  page,
}) => {
  await page.route('**/api/auth/token/', (r) =>
    r.fulfill({ json: { refresh: 'stub-refresh', access: jwt } }),
  )
  await page.route('**/api/auth/me/', (r) =>
    r.fulfill({ json: { id: 1, username: 'admin', is_active: true, role: 'unsupported' } }),
  )
  await enter(page)
  await expect(page.getByRole('alert')).toContainText('Unsupported role')
  await expect(page.getByRole('navigation')).toHaveCount(0)
  await expect(page.getByText('Account Access')).toHaveCount(0)
})
for (const [status, expected] of [
  [403, 'Permission denied'],
  [503, 'could not complete'],
] as const) {
  test('API mode explains Django ' + status, async ({ page }) => {
    await page.route('**/api/auth/token/', (r) =>
      r.fulfill({ status, json: { error: { message: 'test' } } }),
    )
    await enter(page)
    await expect(page.getByRole('alert')).toContainText(expected)
    await expect(page.getByRole('navigation')).toHaveCount(0)
  })
}
test('network loss and invalid refresh end access without switching mode', async ({ page }) => {
  await page.route('**/api/auth/token/', (r) => r.abort())
  await enter(page)
  await expect(page.getByRole('alert')).toContainText('unavailable')
  await page.unroute('**/api/auth/token/')
  await page.clock.install()
  await page.route('**/api/auth/token/', (r) =>
    r.fulfill({ json: { access: jwt, refresh: 'stub-refresh' } }),
  )
  await page.route('**/api/auth/me/', (r) =>
    r.fulfill({ json: { id: 1, username: 'manager', role: 'data_engineer', is_active: true } }),
  )
  await page.route('**/api/auth/token/refresh/', (r) => r.fulfill({ status: 401, json: {} }))
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Business context unavailable' })).toBeVisible()
  await page.clock.fastForward(301000)
  await expect(page.getByRole('alert')).toContainText('Session expired')
  await expect(page.getByText('Lahore Homewares')).toHaveCount(0)
})
