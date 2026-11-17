import { test, expect } from '@playwright/test'
import { apiURL, logout } from './helpers'

test('real Django health, token, identity and refresh through the React API adapter', async ({
  page,
  request,
}) => {
  const health = await request.get(apiURL + 'api/health/')
  expect(health.status()).toBe(200)
  expect(await health.json()).toEqual({ status: 'ok', database: true })
  const anonymous = await request.get(apiURL + 'api/auth/me/')
  expect(anonymous.status()).toBe(401)
  await page.goto(apiURL)
  await page.getByLabel('Username', { exact: true }).fill('data_engineer')
  await page.getByLabel('Password', { exact: true }).fill('wrong')
  await page.getByRole('button', { name: 'Sign in', exact: true }).click()
  await expect(page.getByRole('alert')).toContainText('incorrect')
  for (const [role, app] of [
    ['admin', 'Admin'],
    ['data_engineer', 'Manager'],
    ['business_analyst', 'Seller'],
  ]) {
    await page.getByLabel('Username', { exact: true }).fill(role)
    await page.getByLabel('Password', { exact: true }).fill('fixture-only')
    const pair = page.waitForResponse((r) => r.url().endsWith('/api/auth/token/'))
    const me = page.waitForResponse((r) => r.url().endsWith('/api/auth/me/'))
    await page.getByRole('button', { name: 'Sign in', exact: true }).click()
    expect((await pair).status()).toBe(200)
    expect((await me).status()).toBe(200)
    await expect(page).toHaveURL(new RegExp('/' + app.toLowerCase() + '/dashboard$'))
    await expect(
      page.getByRole('heading', {
        name: app === 'Admin' ? 'Integration pending' : 'Business context unavailable',
      }),
    ).toBeVisible()
    await expect(page.getByText('Demonstration Data')).toHaveCount(0)
    await expect(page.getByText('Lahore Homewares')).toHaveCount(0)
    if (app === 'Seller') {
      const refreshed = await page.waitForResponse(
        (r) => r.url().endsWith('/api/auth/token/refresh/'),
        { timeout: 15000 },
      )
      expect(refreshed.status()).toBe(200)
      await expect(page.getByRole('navigation')).toBeVisible()
    }
    await logout(page)
  }
})
