import { expect, test, type Page } from '@playwright/test'
import { createHash } from 'node:crypto'
import { readFileSync } from 'node:fs'
import { readFile, writeFile } from 'node:fs/promises'
import { resolve } from 'node:path'
import { login, logout, nav } from '../tests/helpers'
import type captureCatalog from './catalog.json'

const catalogFile = resolve('stage5/catalog.json')
const catalog: typeof captureCatalog = JSON.parse(readFileSync(catalogFile, 'utf8'))
type CaptureId = keyof typeof catalog.scenes
const run = process.env.STAGE5_RUN_ID!
const directory = resolve('docs/figures/stage5', run)
const ids = Object.keys(catalog.scenes) as CaptureId[]

async function prepare(page: Page, id: CaptureId, steps: string[]) {
  const scene = catalog.scenes[id]
  const button = async (name: string) => {
    steps.push('Click ' + name)
    await page.getByRole('button', { name, exact: true }).click()
  }
  const select = async (label: string, value: string) => {
    steps.push('Select ' + value + ' in ' + label)
    await page.getByRole('combobox', { name: label, exact: true }).selectOption(value)
    await expect(page.getByRole('combobox', { name: label, exact: true })).toHaveValue(value)
  }
  const tab = async (name: string) => {
    steps.push('Open ' + name + ' tab')
    const item = page.getByRole('tab', { name, exact: true })
    await item.click()
    await expect(item).toHaveAttribute('aria-selected', 'true')
  }
  const visible = async (...labels: string[]) => {
    for (const label of labels) {
      await expect(page.getByText(label).filter({ visible: true }).first()).toBeVisible()
    }
  }
  steps.push('Open fresh demo login')
  await page.goto('/')
  await visible('Demonstration Data')
  if (id === 'G09-02') {
    steps.push(
      'Sign in as Admin',
      'Open Business Assignments',
      'Assign Seller U-03 to Indus Textiles',
    )
    await login(page, 'Admin')
    await nav(page, 'Business Assignments')
    const seller = page.getByRole('row').filter({
      has: page.getByRole('cell', { name: 'Seller', exact: true }),
    })
    const assignment = seller.getByLabel('Business for M Arslan Ahmad', { exact: true })
    await expect(assignment).toHaveValue('loom')
    await assignment.selectOption('indus')
    await expect(assignment).toHaveValue('indus')
    steps.push('Sign out in the same tab')
    await logout(page)
  }
  if (scene.role === 'Admin' || scene.role === 'Manager' || scene.role === 'Seller') {
    steps.push('Sign in through the ' + scene.role + ' demo preset')
    await login(page, scene.role)
    await expect(page.locator('.profile small')).toHaveText(scene.role)
    await expect(page.locator('.business')).toHaveText(scene.business ?? 'Organisation')
    await expect(page.locator('.side-mode')).toHaveText('Local demonstration')
    if (scene.navigation && scene.navigation !== 'Dashboard') {
      steps.push('Navigate to ' + scene.navigation)
      await nav(page, scene.navigation)
    }
  }
  if (scene.navigation === 'Batch Processing') {
    await select('Submission', id === 'G04-02' ? 'SUB-1047' : 'SUB-1048')
  }
  switch (id) {
    case 'G01-01':
      await visible('Select a role to fill in fictional credentials.')
      for (const name of ['Admin', 'Manager', 'Seller', 'Sign in']) {
        await expect(page.getByRole('button', { name, exact: true })).toBeVisible()
      }
      await expect(page.getByLabel('Password', { exact: true })).toHaveValue('')
      break
    case 'G01-02':
      await visible('Role distribution', 'User directory', 'Business assignments')
      break
    case 'G01-03':
      await select('Account role', 'business_analyst')
      await select('Business assignment', 'loom')
      await visible('Account details')
      break
    case 'G01-04':
      await visible('Review preferences', 'Manager confirmation is required for every decision.')
      await expect(page.getByRole('checkbox', { name: /evidence reminder/ })).toBeVisible()
      break
    case 'G02-01':
      await visible('Recent submissions', 'Quarantine workload')
      break
    case 'G02-02':
      await visible(
        'Weekly product demand',
        '90-day prediction',
        'Recent submissions',
        'Repair explanation history',
      )
      break
    case 'G03-01':
      await button('View sample CSV receipt')
      for (const text of [
        'SUB-1048',
        'CSV',
        'november_orders.csv',
        'Lahore Homewares',
        'Registered',
        'Not Requested',
        'Unverified',
      ]) {
        await expect(page.getByRole('dialog')).toContainText(text)
      }
      break
    case 'G03-02':
      await select('Source type', 'Excel')
      await select('Workbook source', 'sample')
      await expect(page.getByLabel('Workbook source').locator('option:checked')).toContainText(
        'retail_export.xlsx',
      )
      for (const sheet of ['Store orders', 'Marketplace orders', 'Notes']) {
        await expect(page.getByRole('checkbox', { name: new RegExp(sheet) })).toBeChecked()
      }
      break
    case 'G03-03':
      await select('Source type', 'PostgreSQL')
      await select('Configured demonstration source', 'PG-LOOM')
      await visible('Connection status unverified')
      break
    case 'G04-01':
    case 'G04-02':
    case 'G04-03': {
      for (const action of ['Request processing', 'Queue execution', 'Start simulation']) {
        await button(action)
      }
      const badge = page.getByRole('tabpanel').locator('.card-head .badge')
      if (id === 'G04-02') {
        await button('Complete worksheet batch')
        await button('Interrupt simulation')
        await expect(badge).toHaveText('Interrupted')
        await visible('EX-1047', 'Incomplete')
        for (const name of [
          '1 · Store orders Completed 512',
          '2 · Marketplace orders Interrupted Unknown',
        ]) {
          await expect(page.getByRole('row', { name })).toBeVisible()
        }
      } else {
        await visible('EX-1048', 'ACK-1048', 'Processing timeline')
        await expect(badge).toHaveText('Running')
        if (id === 'G04-03') {
          await button('Complete simulation')
          await expect(badge).toHaveText('Succeeded')
          await expect(
            page.locator('dt').filter({ hasText: 'Simulated rows accounted for' }).locator('+ dd'),
          ).toHaveText('1,240')
          await visible('Unverified')
        }
      }
      await visible('Authoritative count unavailable')
      break
    }
    case 'G05-01':
      await tab('Processing Rules')
      await visible('Version history', 'Current version: RULE-LOOM-002')
      await expect(page.getByLabel('Inspect saved version')).toHaveValue('RULE-LOOM-002')
      await expect(page.getByLabel('Inspect saved version').locator('option')).toHaveText([
        'RULE-LOOM-002',
        'RULE-LOOM-001',
      ])
      break
    case 'G05-02':
      await tab('Validation Findings')
      await visible('ROW-1048-017', 'Missing (null)', 'Customer ID')
      await expect(page.getByRole('row', { name: 'VAL-1048-02 Row Quantity Passed' })).toBeVisible()
      await visible('All eight required source headers are present in this sample.')
      break
    case 'G06-01':
    case 'G06-02':
      await tab('Anomaly Detection')
      await button('Inspect FND-042, Business Rules')
      await visible(
        '3 method alerts',
        '1 distinct affected rows',
        'Ceramic Mug',
        'Z-score',
        'Isolation Forest',
      )
      if (id === 'G06-02') {
        await button('View Evidence')
        await expect(page.getByLabel('Finding group / illustrative source row')).toHaveValue(
          'ROW-1048-042',
        )
        await visible('BR-042', 'ZS-042', 'IF-042', 'OC-1042', 'Supported minus submitted')
        await expect(page.getByRole('row', { name: 'Unit price £40 £4' })).toBeVisible()
      }
      break
    case 'G07-01':
    case 'G07-02':
    case 'G07-03': {
      const history = page.getByRole('region', { name: 'Local Review Decision History' })
      const events = history.locator('ol > li')
      steps.push('Select the quarantine entry for DEMO-INV-042')
      const invoice = page.getByRole('button', { name: /DEMO-INV-042/ })
      await invoice.click()
      await expect(invoice).toHaveAttribute('aria-pressed', 'true')
      await visible(
        'DEMO-INV-042',
        'No local review events recorded for QR-042.',
        'Awaiting Review',
      )
      await expect(page.getByRole('row', { name: 'Unit price £40 £4' })).toBeVisible()
      await visible('3 units · unchanged')
      await expect(events).toHaveCount(0)
      if (id !== 'G07-01') {
        await button('Retain for Review')
        await button('Confirm decision')
        await expect(events).toHaveCount(1)
        await expect(events.first().locator('strong')).toHaveText('Retain for Review')
        await visible('Retained for Review')
      }
      if (id === 'G07-03') {
        await button('Authorise Supported Correction')
        await button('Confirm decision')
        await expect(events).toHaveCount(2)
        await expect(events.first()).toHaveAttribute('value', '1')
        await expect(events.nth(1)).toHaveAttribute('value', '2')
        await expect(events.nth(1).locator('strong')).toHaveText('Authorise Supported Correction')
        steps.push('Expand the first event’s supporting order')
        await events.first().locator('summary').click()
        await visible('Correction authorised — not applied', 'Snapshot at confirmation', 'OC-1042')
      }
      break
    }
    case 'G08-01': {
      await visible('FC-011', 'Forecast begins', 'forecasting performance has not been evaluated')
      const chart = page.getByRole('img', { name: /Ceramic Mug weekly demand in units/ })
      await expect(chart).toBeVisible()
      await expect(chart).toHaveAttribute('aria-label', /16 Nov 2026 Predicted 107/)
      for (const name of ['09 Nov Historical 92', '16 Nov Predicted 107', '30 Nov Predicted 120']) {
        await expect(page.getByRole('row', { name })).toBeVisible()
      }
      break
    }
    case 'G09-01':
      await visible(
        '90-day prediction',
        'Actual historical spending and realised outcomes are unavailable',
      )
      for (const text of [
        'CLV-015',
        'CLV-016',
        '£284 GBP',
        '£196 GBP',
        '15 Nov 2026',
        'CUST-018',
        'CUST-026',
      ]) {
        await expect(page.getByRole('table')).toContainText(text)
      }
      break
    case 'G09-02':
      await visible('No spending estimate available for this business.')
      await expect(page.getByText(/CLV-015|CLV-016|CUST-018|CUST-026/)).toHaveCount(0)
      break
    case 'G10-01':
    case 'G10-02':
      if (id === 'G10-02') await select('Demo response', 'Unavailable')
      await visible(
        'REP-042',
        'Post-commit demonstration scenario',
        'Recorded repair facts',
        'Template explanation',
        'OC-1042',
        '−£108',
        '3 units · unchanged',
      )
      await visible(id === 'G10-01' ? 'Simulated narrative' : 'Explanation Unavailable')
      await expect(page.getByRole('row', { name: 'Line total £120 £12' })).toBeVisible()
      await expect(page.getByRole('row', { name: 'Unit price £40 £4' })).toBeVisible()
      break
  }
}

for (const id of ids) {
  test(id + ' ' + catalog.scenes[id].state, async ({ page, browser }) => {
    const steps: string[] = []
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.name))
    page.on('console', (message) => {
      if (message.type() === 'error') errors.push('Browser console error')
    })
    await page.context().route('**/*', async (route) => {
      const url = new URL(route.request().url())
      if (url.origin === 'http://127.0.0.1:4187' && !url.pathname.startsWith('/api/')) {
        await route.continue()
      } else {
        errors.push('Unexpected network destination')
        await route.abort()
      }
    })
    const record = resolve(directory, 'metadata', id + '.json')
    try {
      const provenance = JSON.parse(await readFile(resolve(directory, 'metadata/run.json'), 'utf8'))
      expect(provenance.run).toBe(run)
      expect(provenance.source.sha256).toMatch(/^[a-f0-9]{64}$/)
      await page.clock.setFixedTime(new Date('2026-11-17T09:00:00Z'))
      await prepare(page, id, steps)
      const fontReady = await page.evaluate(async () => {
        await document.fonts.ready
        await Promise.all(Array.from(document.images, (image) => image.decode()))
        window.scrollTo(0, 0)
        return document.fonts.check('14px "Inter Variable"')
      })
      expect(fontReady, 'The bundled application font is ready').toBe(true)
      await page.mouse.move(1, 1)
      const environment = await page.evaluate(() => ({
        viewport: { width: innerWidth, height: innerHeight },
        deviceScaleFactor: devicePixelRatio,
        locale: navigator.language,
        timezone: Intl.DateTimeFormat().resolvedOptions().timeZone,
        reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
        route: location.hash.slice(1).split('?')[0] || '/login',
      }))
      expect(environment).toEqual({
        viewport: { width: 1440, height: 900 },
        deviceScaleFactor: 1,
        locale: 'en-GB',
        timezone: 'Asia/Karachi',
        reducedMotion: true,
        route: catalog.scenes[id].route,
      })
      const fitsWidth = await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      )
      expect(fitsWidth).toBe(true)
      const clipped = await page
        .locator('dialog[open], .card, .estimate-table, .detection-table')
        .evaluateAll((nodes) =>
          nodes.some(
            (el) =>
              el.clientWidth &&
              (el.scrollWidth > el.clientWidth + 1 ||
                (el.matches('dialog') && el.scrollHeight > el.clientHeight + 1)),
          ),
        )
      expect(clipped, 'Evidence container clips content').toBe(false)
      const png = await page.screenshot({ fullPage: true, caret: 'hide', animations: 'disabled' })
      expect(errors, 'Browser errors during capture').toEqual([])
      const file = 'raw/' + id + '.png'
      await writeFile(resolve(directory, file), png, { flag: 'wx' })
      const url = new URL(page.url())
      const metadata = {
        ...catalog.scenes[id],
        ...environment,
        id,
        run,
        browserPath: url.pathname + url.hash,
        sourceSha256: provenance.source.sha256,
        mode: 'demo',
        steps,
        method: 'Playwright full-page PNG',
        browser: browser.version(),
        fixedDemoTime: '2026-11-17T09:00:00Z',
        capturedAt: new Date().toISOString(),
        file,
        width: png.readUInt32BE(16),
        height: png.readUInt32BE(20),
        sha256: createHash('sha256').update(png).digest('hex'),
        assertions: 'passed',
        visualReview: 'pending',
      }
      await writeFile(record, JSON.stringify(metadata, null, 2), { flag: 'wx' })
    } catch (error) {
      const failure = {
        id,
        run,
        steps,
        assertions: 'failed',
        visualReview: 'pending',
        reason: error instanceof Error ? error.message.slice(0, 3000) : 'Capture failed',
      }
      await writeFile(record, JSON.stringify(failure, null, 2), { flag: 'wx' })
      throw error
    }
  })
}
