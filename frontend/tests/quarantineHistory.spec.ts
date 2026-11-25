import { expect, test, type Page } from '@playwright/test'
import { login, nav } from './helpers'

// Stage 4A browser/UI tests. These deliberately do not require live Django or PostgreSQL.
// The decision history is a labelled <section> nested inside the Manager decision card.
// Scope queries to that section so action buttons and order evidence cannot match.
function historyRegion(page: Page) {
  return page.getByRole('region', { name: 'Local Review Decision History' })
}

async function confirmDecision(page: Page, decision: string) {
  await page.getByRole('button', { name: decision, exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: 'Confirm decision', exact: true }).click()
  await expect(page.getByRole('dialog')).toHaveCount(0)
}

async function openReview(page: Page, mobile = false) {
  if (mobile) await page.setViewportSize({ width: 390, height: 844 })
  await page.goto('/')
  await login(page, 'Manager')
  if (mobile) await page.getByRole('button', { name: 'Toggle navigation' }).click()
  await nav(page, 'Quarantine Review')
}

test('Stage 4A renders an empty local decision history and cancellation adds no event', async ({
  page,
}) => {
  await openReview(page)
  const history = historyRegion(page)
  await expect(history).toBeVisible()
  await expect(history.locator('ol > li')).toHaveCount(0)
  await expect(page.getByRole('heading', { name: 'DEMO-INV-042' })).toBeVisible()
  await expect(page.getByText('Awaiting Review', { exact: true }).last()).toBeVisible()

  await page.getByRole('button', { name: 'Confirm Original', exact: true }).click()
  await expect(page.getByRole('dialog')).toBeVisible()
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()

  await expect(page.getByRole('dialog')).toHaveCount(0)
  await expect(page.getByText('Awaiting Review', { exact: true }).last()).toBeVisible()
  await expect(
    page.getByText('No local review events recorded for QR-042.', { exact: true }),
  ).toBeVisible()
  await expect(history.locator('ol > li')).toHaveCount(0)
  await expect(page.getByRole('row', { name: 'Unit price £40 £4' })).toBeVisible()
})

test('Stage 4A renders ordered Retain then Authorise events and expandable source snapshots', async ({
  page,
}) => {
  await openReview(page)
  const history = historyRegion(page)
  await confirmDecision(page, 'Retain for Review')
  await expect(history).toContainText('Retain for Review')
  await expect(page.getByText('Retained for Review', { exact: true }).last()).toBeVisible()

  await confirmDecision(page, 'Authorise Supported Correction')
  await expect(history).toContainText('Authorise Supported Correction')
  await expect(
    page.getByText('Correction authorised — not applied', { exact: true }).last(),
  ).toBeVisible()

  // Each <li> in the labelled history section is a recorded decision event.
  // The <summary> inside each event names its evidence, not its decision.
  const events = history.locator('ol > li')
  await expect(events).toHaveCount(2)
  await expect(events.nth(0)).toHaveAttribute('value', '1')
  await expect(events.nth(1)).toHaveAttribute('value', '2')
  await expect(events.nth(0).locator('strong')).toHaveText('Retain for Review')
  await expect(events.nth(1).locator('strong')).toHaveText('Authorise Supported Correction')
  await expect(events.nth(0).locator('time')).toHaveAttribute('datetime', /.+/)
  await expect(events.nth(1).locator('time')).toHaveAttribute('datetime', /.+/)

  // Expand the FIRST event's supporting evidence; assert the event-time snapshot.
  const firstSnapshot = events.nth(0).locator('details')
  const disclosure = firstSnapshot.locator('summary')
  await expect(disclosure).toContainText('EV-042')
  await expect(disclosure).toContainText('Order confirmation OC-1042')
  await disclosure.click()
  await expect(firstSnapshot).toHaveAttribute('open', '')
  await expect(firstSnapshot).toContainText('Snapshot at confirmation')
  await expect(firstSnapshot).toContainText('Submitted £40 each (£120 total)')
  await expect(firstSnapshot).toContainText('Order supports £4 each (£12 total)')
  await expect(firstSnapshot).toContainText('−£108')
  await expect(events.nth(1).locator('details > summary')).toContainText('OC-1042')

  await expect(page.getByRole('row', { name: 'Unit price £40 £4' })).toBeVisible()
  await expect(page.getByRole('row', { name: 'Line total £120 £12' })).toBeVisible()
  await expect(
    page
      .locator('.action-row')
      .getByRole('button', { name: 'Authorise Supported Correction', exact: true }),
  ).toHaveCount(0)
})

test('Stage 4A changes the displayed history with the selected quarantine record', async ({
  page,
}) => {
  await openReview(page)
  const history = historyRegion(page)
  await confirmDecision(page, 'Confirm Original')
  await expect(history.locator('ol > li')).toHaveCount(1)
  await expect(history.locator('ol > li').first().locator('strong')).toHaveText('Confirm Original')

  const search = page.getByRole('searchbox')
  await search.fill('039')
  await expect(page.getByRole('heading', { name: 'DEMO-INV-039' })).toBeVisible()
  await expect(page.getByText('Awaiting Review', { exact: true }).last()).toBeVisible()
  await expect(
    page.getByText('No local review events recorded for QR-039.', { exact: true }),
  ).toBeVisible()
  await expect(history.locator('ol > li')).toHaveCount(0)

  await search.fill('042')
  await expect(page.getByRole('heading', { name: 'DEMO-INV-042' })).toBeVisible()
  await expect(history.locator('ol > li')).toHaveCount(1)
  await expect(history.locator('ol > li').first().locator('strong')).toHaveText('Confirm Original')
  await expect(page.getByRole('row', { name: 'Unit price £40 £4' })).toBeVisible()
})

test('Stage 4A decision history stays readable at a mobile viewport', async ({ page }) => {
  await openReview(page, true)
  const history = historyRegion(page)
  await expect(history).toBeVisible()
  await confirmDecision(page, 'Retain for Review')
  await expect(history).toContainText('Retain for Review')

  const horizontalOverflow = await page.evaluate(
    () => document.documentElement.scrollWidth - document.documentElement.clientWidth,
  )
  expect(horizontalOverflow).toBeLessThanOrEqual(1)
})
