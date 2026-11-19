import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { resolve } from 'node:path'
import { login, nav } from './helpers'
import type { AppRole } from '../src/auth/roleAdapter'

const figures: { name: string; role?: AppRole; link?: string; essential: string[] }[] = [
  {
    name: '4.1-shared-login',
    essential: ['Sign in', 'Select a role to fill in fictional credentials.'],
  },
  {
    name: '4.2-admin-dashboard',
    role: 'Admin',
    essential: ['Role distribution', 'User directory', 'Business assignments'],
  },
  {
    name: '4.3-create-account',
    role: 'Admin',
    link: 'Create Account',
    essential: ['Account details', 'Business assignment', 'Create account'],
  },
  {
    name: '4.4-manager-dashboard',
    role: 'Manager',
    essential: ['Recent submissions', 'Quarantine workload'],
  },
  {
    name: '4.5-seller-dashboard',
    role: 'Seller',
    essential: [
      'Weekly product demand',
      '90-day prediction',
      'Recent submissions',
      'Repair explanation history',
    ],
  },
  {
    name: '4.6-batch-processing',
    role: 'Manager',
    link: 'Batch Processing',
    essential: [
      'EX-1048',
      'ACK-1048',
      'Processing timeline',
      'Complete simulation',
      'Simulate failure',
    ],
  },
  {
    name: '4.7-quarantine-review',
    role: 'Manager',
    link: 'Quarantine Review',
    essential: [
      'DEMO-INV-042',
      '£40',
      '£120',
      '£4',
      '£12',
      '−£108',
      '3 units · unchanged',
      'Not Yet Recomputed',
      'Confirm Original',
      'Authorise Supported Correction',
      'Retain for Review',
    ],
  },
  {
    name: '4.8-repair-explanations',
    role: 'Seller',
    link: 'Repair Explanations',
    essential: [
      'DEMO-INV-042',
      '£40',
      '£120',
      '£4',
      '£12',
      '−£108',
      '3 units · unchanged',
      'Not Yet Recomputed',
      'Template explanation',
      'Simulated narrative',
      'Post-commit demonstration scenario',
    ],
  },
]
for (const fig of figures) {
  test('Figure ' + fig.name + ' fits 1440 × 900', async ({ page }) => {
    const errors: string[] = []
    page.on('pageerror', (error) => errors.push(error.message))
    await page.goto('/')
    if (fig.role) await login(page, fig.role)
    if (fig.link) await nav(page, fig.link)
    if (fig.name.startsWith('4.6')) {
      for (const name of ['Request processing', 'Queue execution', 'Start simulation'])
        await page.getByRole('button', { name, exact: true }).click()
    }
    for (const label of ['Demonstration Data', ...fig.essential]) {
      const target = page.getByText(label, { exact: true }).last()
      await expect(target).toBeVisible()
      const box = await target.boundingBox()
      expect(box, label).toBeTruthy()
      expect(box!.y + box!.height, label + ' bottom').toBeLessThanOrEqual(900)
      expect(box!.x, label + ' left').toBeGreaterThanOrEqual(0)
    }
    await page.evaluate(() => document.fonts.ready)
    await page.mouse.move(1, 1)
    const layout = await page.evaluate(() => {
      const tables = [...document.querySelectorAll('table')],
        elements = [...document.querySelectorAll('.card,.metric')]
      const overlap = elements.some((a, i) =>
        elements.slice(i + 1).some((b) => {
          if (a.contains(b) || b.contains(a)) return false
          const r = a.getBoundingClientRect(),
            s = b.getBoundingClientRect()
          return (
            Math.min(r.right, s.right) - Math.max(r.left, s.left) > 1 &&
            Math.min(r.bottom, s.bottom) - Math.max(r.top, s.top) > 1
          )
        }),
      )
      return {
        width: innerWidth,
        height: innerHeight,
        scroll: document.documentElement.scrollWidth,
        overlap,
        belowFold: elements.some((el) => el.getBoundingClientRect().bottom > innerHeight),
        cropped: tables.some(
          (t) => t.scrollWidth > t.clientWidth + 1 || t.getBoundingClientRect().right > innerWidth,
        ),
        font: document.fonts.check('14px "Inter Variable"'),
      }
    })
    expect(layout).toEqual({
      width: 1440,
      height: 900,
      scroll: 1440,
      overlap: false,
      belowFold: false,
      cropped: false,
      font: true,
    })
    expect(errors).toEqual([])
    if (process.env.CAPTURE_FIGURES === '1') {
      const folder = resolve('docs/figures')
      await mkdir(folder, { recursive: true })
      await page.screenshot({
        path: resolve(folder, 'figure-' + fig.name + '.png'),
        animations: 'disabled',
        caret: 'hide',
      })
    }
  })
}
