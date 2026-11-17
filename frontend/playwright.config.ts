import { defineConfig, type PlaywrightTestConfig } from '@playwright/test'
import { existsSync } from 'node:fs'
import { resolve } from 'node:path'

const live = process.env.LIVE_DJANGO === '1'
function frontendServer(mode: 'demo' | 'api', port: number) {
  const vite = 'node node_modules/vite/bin/vite.js'
  const output = 'node_modules/.cache/dataxai-' + mode
  return {
    command: `${vite} build --outDir ${output} && ${vite} preview --outDir ${output} --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}/`,
    env: { VITE_DATA_MODE: mode, DJANGO_ORIGIN: 'http://127.0.0.1:8193' },
    timeout: 120000,
  }
}
const servers: NonNullable<PlaywrightTestConfig['webServer']> = [
  frontendServer('demo', 4187),
  frontendServer('api', 4188),
]
if (live) {
  const python =
    process.env.DATAXAI_PYTHON ||
    (process.platform === 'win32' ? resolve('../dataxaienv/Scripts/python.exe') : 'python3')
  servers.push({
    command: '"' + python + '" -B tests/serve_django.py',
    url: 'http://127.0.0.1:8193/api/health/',
    timeout: 60000,
  })
}
export default defineConfig({
  testDir: './tests',
  testIgnore: live ? [] : ['**/live.spec.ts'],
  fullyParallel: true,
  workers: 2,
  reporter: 'list',
  forbidOnly: !!process.env.CI,
  timeout: 45000,
  expect: { timeout: 8000 },
  use: {
    baseURL: 'http://127.0.0.1:4187',
    viewport: { width: 1440, height: 900 },
    deviceScaleFactor: 1,
    locale: 'en-GB',
    timezoneId: 'Asia/Karachi',
    reducedMotion: 'reduce',
    channel:
      process.env.PLAYWRIGHT_CHANNEL ||
      (existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe') ? 'chrome' : undefined),
  },
  webServer: servers,
})
