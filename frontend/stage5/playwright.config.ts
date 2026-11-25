import { defineConfig } from '@playwright/test'
import { resolve } from 'node:path'
import base from '../playwright.config'

const run = process.env.STAGE5_RUN_ID
if (
  process.env.CAPTURE_COVERAGE_FIGURES !== '1' ||
  process.env.CAPTURE_FIGURES === '1' ||
  process.env.LIVE_DJANGO === '1' ||
  !run ||
  !/^stage5-[a-zA-Z0-9_-]{1,80}$/.test(run)
) {
  throw new Error('Start a new demo capture through npm run stage5:capture.')
}
const demo = Array.isArray(base.webServer) && base.webServer[0]
if (!demo || demo.env?.VITE_DATA_MODE !== 'demo') throw new Error('Demo preview is required.')

export default defineConfig({
  testDir: '.',
  testMatch: 'capture.spec.ts',
  outputDir: resolve('node_modules/.cache/stage5-playwright', run),
  use: { ...base.use, serviceWorkers: 'block' },
  webServer: { ...demo, cwd: resolve('.'), reuseExistingServer: false },
  forbidOnly: true,
  workers: 1,
  retries: 0,
  timeout: 45000,
  expect: base.expect,
  reporter: 'list',
})
