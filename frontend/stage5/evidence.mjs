import assert from 'node:assert/strict'
import { createHash, randomUUID } from 'node:crypto'
import { spawnSync } from 'node:child_process'
import {
  constants,
  copyFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  writeFileSync,
} from 'node:fs'
import { dirname, join, relative, resolve, sep } from 'node:path'
import { fileURLToPath } from 'node:url'
import { createRequire } from 'node:module'
import { chromium } from '@playwright/test'

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const require = createRequire(import.meta.url)
const figuresRoot = join(root, 'docs', 'figures')
const outputs = join(figuresRoot, 'stage5')
const json = (file) => JSON.parse(readFileSync(file, 'utf8'))
const write = (file, content) => writeFileSync(file, content, { flag: 'wx' })
const writeJSON = (file, value) => write(file, JSON.stringify(value, null, 2) + '\n')
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex')
const catalog = json(join(root, 'stage5/catalog.json'))
const ids = Object.keys(catalog.scenes).sort()
const [command, run] = process.argv.slice(2)
assert(['capture', 'verify-raw', 'compose', 'verify'].includes(command), 'Unknown Stage 5 command')
assert(/^stage5-[a-zA-Z0-9_-]{1,80}$/.test(run ?? ''), 'Supply a run ID: stage5-<unique-name>')
assert(process.env.CAPTURE_FIGURES !== '1', 'Clear the legacy CAPTURE_FIGURES flag first.')
const dir = join(outputs, run)
const meta = join(dir, 'metadata')
const historical = join(dir, 'historical')
const thesis = join(dir, 'thesis')
const channel =
  process.env.PLAYWRIGHT_CHANNEL ||
  (existsSync('C:/Program Files/Google/Chrome/Application/chrome.exe') ? 'chrome' : undefined)

function historicalHashes() {
  // This is the previously reviewed inventory, independent of the bytes copied this run.
  const baseline = readFileSync(join(root, 'docs/STAGE_2_VERIFICATION.md'), 'utf8')
  const pattern = /^\| `(figure-4\.[1-8]-[\w-]+\.png)` \| `([A-F0-9]{64})` \|\r?$/gm
  const rows = [...baseline.matchAll(pattern)]
  const hashes = Object.fromEntries(rows.map(([, file, hash]) => [file, hash.toLowerCase()]))
  assert.equal(rows.length, 8, 'Historical baseline must contain eight PNG records')
  assert.equal(new Set(rows.map(([, file]) => file.split('-')[1])).size, 8)
  for (const [file, hash] of Object.entries(hashes)) {
    const actual = digest(readFileSync(join(figuresRoot, file)))
    assert.equal(actual, hash, 'Historical mismatch: ' + file)
  }
  return hashes
}

function sourceIdentity() {
  const inputs = []
  function inspect(file) {
    inputs.push([relative(root, file).split(sep).join('/'), digest(readFileSync(file))])
  }
  function walk(folder) {
    for (const entry of readdirSync(folder, { withFileTypes: true })) {
      const file = join(folder, entry.name)
      if (entry.isDirectory()) walk(file)
      else if (entry.isFile()) inspect(file)
      else throw new Error('Unsupported entry in source fingerprint: ' + entry.name)
    }
  }
  for (const folder of ['src', 'public', 'stage5']) walk(join(root, folder))
  for (const file of [
    'package.json',
    'package-lock.json',
    'index.html',
    'tsconfig.json',
    'playwright.config.ts',
    'vite.config.ts',
    'tests/helpers.ts',
    'docs/STAGE_2_VERIFICATION.md',
  ]) {
    inspect(join(root, file))
  }
  inputs.sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
  const revision = spawnSync('git', ['rev-parse', 'HEAD'], { cwd: root, encoding: 'utf8' })
  const index = spawnSync('git', ['ls-files', '--cached', '-z', '--', '.'], {
    cwd: root,
    encoding: 'utf8',
  })
  const tracked = index.status === 0 ? new Set(index.stdout.split('\0')) : null
  const untrackedInputs = tracked ? inputs.filter(([file]) => !tracked.has(file)).length : null
  return {
    repositoryHead: revision.status === 0 ? revision.stdout.trim() : null,
    untrackedInputs,
    revisionLimitation: untrackedInputs
      ? 'The repository commit does not identify the untracked capture inputs. Use the working-tree fingerprint.'
      : 'The repository commit is context only. The working-tree fingerprint identifies the inputs.',
    basis:
      'SHA-256 of JSON-encoded sorted [relative path, file SHA-256] pairs from the working tree',
    sha256: digest(JSON.stringify(inputs)),
    inputs,
  }
}

function archiveCheck(hashes) {
  assert.deepEqual(readdirSync(historical).sort(), Object.keys(hashes).sort())
  for (const [file, hash] of Object.entries(hashes)) {
    assert.equal(digest(readFileSync(join(historical, file))), hash, 'Archive mismatch: ' + file)
  }
}

function checksumText(rows) {
  return rows.map(({ sha256, file }) => sha256 + '  ' + file + '\n').join('')
}

function capture() {
  assert.equal(process.env.CAPTURE_COVERAGE_FIGURES, '1', 'Set CAPTURE_COVERAGE_FIGURES=1')
  assert.notEqual(process.env.LIVE_DJANGO, '1', 'Demo preview only')
  const baseline = historicalHashes()
  mkdirSync(outputs, { recursive: true })
  mkdirSync(dir) // Exclusive. All subsequent writes belong to this new run.
  for (const sub of [meta, historical, join(dir, 'raw')]) mkdirSync(sub)
  console.log('Stage 5 evidence directory:', dir)
  const source = sourceIdentity()
  const info = {
    schema: 1,
    run,
    startedAt: new Date().toISOString(),
    source,
    historical: baseline,
    playwright: require('@playwright/test/package.json').version,
    node: process.version,
    platform: process.platform,
    arch: process.arch,
    channel: channel ?? 'bundled Chromium',
    visualReview: 'pending',
  }
  writeJSON(join(meta, 'run.json'), info)
  for (const file of Object.keys(baseline)) {
    copyFileSync(join(figuresRoot, file), join(historical, file), constants.COPYFILE_EXCL)
  }
  archiveCheck(baseline)
  const cli = require.resolve('@playwright/test/cli')
  const args = [cli, 'test', '--config', 'stage5/playwright.config.ts']
  const result = spawnSync(process.execPath, args, {
    cwd: root,
    stdio: 'inherit',
    env: { ...process.env, STAGE5_RUN_ID: run },
  })
  const rows = ids.map((id) => {
    const file = join(meta, id + '.json')
    if (existsSync(file)) return json(file)
    return {
      id,
      run,
      assertions: 'incomplete',
      reason: 'No completed recipe record. Inspect the runner output.',
    }
  })
  const problems = []
  if (result.status !== 0) {
    problems.push('Playwright failed: ' + (result.status ?? result.error?.code ?? result.signal))
  }
  try {
    assert.deepEqual(historicalHashes(), baseline)
    archiveCheck(baseline)
    assert.equal(sourceIdentity().sha256, source.sha256, 'Source changed during capture')
  } catch (error) {
    problems.push(error.message)
  }
  const passed = rows.filter((row) => row.assertions === 'passed')
  if (passed.length !== ids.length) problems.push('Incomplete capture coverage')
  const manifest = {
    schema: 1,
    run,
    sourceSha256: source.sha256,
    finishedAt: new Date().toISOString(),
    complete: problems.length === 0,
    problems,
    captures: rows,
  }
  writeJSON(join(meta, 'capture-manifest.json'), manifest)
  write(join(meta, 'hashes.sha256'), checksumText(passed))
  const summary = [
    '# ' + run,
    '',
    'Browser assertions passed: ' + passed.length + '/' + ids.length,
    'Visual review: pending',
    '',
    ...rows.map((r) => '- ' + r.id + ' — ' + catalog.scenes[r.id].state + ' — ' + r.assertions),
    '',
    ...problems,
    '',
  ]
  write(join(meta, 'capture-summary.md'), summary.join('\n'))
  assert(manifest.complete, problems.join('; '))
}

// Decode the actual PNG with Chromium, including its pixel data. IHDR alone
// would accept a truncated file. No application page is opened for this step.
async function decodedSize(page, file) {
  const bytes = readFileSync(file)
  assert(bytes.subarray(0, 8).equals(Buffer.from('89504e470d0a1a0a', 'hex')), 'Expected PNG')
  const encoded = bytes.toString('base64')
  const size = await page.evaluate(async (encoded) => {
    const img = new Image()
    img.src = 'data:image/png;base64,' + encoded
    await img.decode()
    return [img.naturalWidth, img.naturalHeight]
  }, encoded)
  return { width: size[0], height: size[1], sha256: digest(bytes) }
}

async function verifyRaw(page) {
  const info = json(join(meta, 'run.json'))
  const manifest = json(join(meta, 'capture-manifest.json'))
  assert.equal(info.run, run)
  assert.deepEqual(historicalHashes(), info.historical)
  archiveCheck(info.historical)
  assert.equal(digest(JSON.stringify(info.source.inputs)), info.source.sha256)
  assert.equal(manifest.sourceSha256, info.source.sha256)
  assert.equal(manifest.run, run)
  assert.equal(manifest.complete, true, 'Capture did not complete successfully')
  assert.deepEqual(manifest.problems, [])
  assert.equal(ids.length, 24)
  const capturedIds = manifest.captures.map((r) => r.id)
  const expectedFiles = ids.map((id) => id + '.png')
  assert.deepEqual(capturedIds, ids)
  assert.deepEqual(readdirSync(join(dir, 'raw')).sort(), expectedFiles)
  for (const row of manifest.captures) {
    const { id } = row
    assert.deepEqual(row, json(join(meta, id + '.json')))
    for (const [key, value] of Object.entries(catalog.scenes[id])) {
      assert.deepEqual(row[key], value, id + ' ' + key)
    }
    const actualRoute = row.browserPath.split('#')[1] || '/login'
    assert.equal(actualRoute.split('?')[0], row.route, id + ' browser URL')
    assert.equal(row.run, run)
    assert.equal(row.sourceSha256, info.source.sha256)
    assert.equal(row.mode, 'demo')
    assert.equal(row.assertions, 'passed')
    assert.equal(row.visualReview, 'pending')
    assert.equal(row.file, 'raw/' + id + '.png')
    assert.deepEqual(row.viewport, { width: 1440, height: 900 })
    assert.equal(row.deviceScaleFactor, 1)
    assert.equal(row.locale, 'en-GB')
    assert.equal(row.reducedMotion, true)
    assert.equal(row.timezone, 'Asia/Karachi')
    const image = await decodedSize(page, join(dir, row.file))
    assert.deepEqual(image, { width: row.width, height: row.height, sha256: row.sha256 }, id)
    assert(row.width === 1440 && row.height >= 900 && row.steps.length > 0)
    assert(Number.isFinite(Date.parse(row.capturedAt)))
  }
  assert.equal(readFileSync(join(meta, 'hashes.sha256'), 'utf8'), checksumText(manifest.captures))
  return manifest
}

async function compose(page, manifest) {
  mkdirSync(thesis)
  mkdirSync(join(thesis, 'panels'))
  const figures = []
  for (const group of catalog.figures) {
    const panels = group.panels.map((id) => {
      const raw = manifest.captures.find((r) => r.id === id)
      assert(raw && raw.figure === group.number)
      const file = 'thesis/panels/' + id + '.png'
      copyFileSync(join(dir, raw.file), join(dir, file), constants.COPYFILE_EXCL)
      return { id, file, sha256: raw.sha256 }
    })
    const file = 'thesis/figure-' + group.number + '.png'
    if (panels.length === 1) {
      copyFileSync(join(dir, panels[0].file), join(dir, file), constants.COPYFILE_EXCL)
    } else {
      const sources = panels.map((p, i) => ({
        label: '(' + String.fromCharCode(97 + i) + ') ' + catalog.scenes[p.id].state,
        data: readFileSync(join(dir, p.file)).toString('base64'),
      }))
      const png = await page.evaluate(async (sources) => {
        const images = await Promise.all(
          sources.map(async ({ data }) => {
            const image = new Image()
            image.src = 'data:image/png;base64,' + data
            await image.decode()
            return image
          }),
        )
        const canvas = document.createElement('canvas')
        canvas.width = Math.max(...images.map((img) => img.naturalWidth))
        canvas.height = images.reduce((height, img) => height + 48 + img.naturalHeight, 0)
        const ctx = canvas.getContext('2d')
        if (!ctx) throw new Error('A 2D canvas is required.')
        ctx.fillStyle = '#ffffff'
        ctx.fillRect(0, 0, canvas.width, canvas.height)
        ctx.font = '24px Arial, sans-serif'
        ctx.fillStyle = '#202020'
        let y = 0
        images.forEach((image, i) => {
          ctx.fillText(sources[i].label, 16, y + 32)
          ctx.drawImage(image, 0, y + 48)
          y += image.naturalHeight + 48
        })
        return canvas.toDataURL('image/png').split(',')[1]
      }, sources)
      write(join(dir, file), Buffer.from(png, 'base64'))
    }
    figures.push({
      number: group.number,
      caption: group.caption,
      file,
      panels,
      ...(await decodedSize(page, join(dir, file))),
      visualReview: 'pending',
    })
  }
  writeJSON(join(meta, 'thesis-figure-map.json'), { run, figures })
}

async function verifyFigures(page, manifest) {
  const map = json(join(meta, 'thesis-figure-map.json'))
  assert.equal(map.run, run)
  assert.equal(map.figures.length, 10)
  const exports = catalog.figures.map((group) => 'figure-' + group.number + '.png')
  assert.deepEqual(readdirSync(thesis).sort(), [...exports, 'panels'].sort())
  const expectedPanels = catalog.figures.flatMap((group) => group.panels.map((id) => id + '.png'))
  assert.deepEqual(readdirSync(join(thesis, 'panels')).sort(), expectedPanels.sort())
  for (const [index, group] of catalog.figures.entries()) {
    const figure = map.figures[index]
    assert.equal(figure.number, group.number)
    assert.equal(figure.caption, group.caption)
    assert.equal(figure.file, 'thesis/figure-' + group.number + '.png')
    const panelIds = figure.panels.map((p) => p.id)
    assert.deepEqual(panelIds, group.panels)
    let height = 0
    for (const panel of figure.panels) {
      const raw = manifest.captures.find((r) => r.id === panel.id)
      assert.equal(raw.figure, figure.number)
      assert.equal(panel.file, 'thesis/panels/' + panel.id + '.png')
      assert.equal(panel.sha256, raw.sha256)
      assert.equal(digest(readFileSync(join(dir, panel.file))), raw.sha256)
      height += raw.height + (figure.panels.length > 1 ? 48 : 0)
    }
    assert.equal(figure.height, height)
    assert.equal(figure.width, 1440)
    assert.deepEqual(await decodedSize(page, join(dir, figure.file)), {
      width: figure.width,
      height,
      sha256: figure.sha256,
    })
    if (figure.panels.length === 1) {
      assert.equal(figure.sha256, figure.panels[0].sha256)
    } else {
      const encoded = (file) => readFileSync(join(dir, file)).toString('base64')
      const images = [encoded(figure.file), ...figure.panels.map((panel) => encoded(panel.file))]
      const matches = await page.evaluate(async (images) => {
        const decode = async (encoded) => {
          const image = new Image()
          image.src = 'data:image/png;base64,' + encoded
          await image.decode()
          return image
        }
        const [output, ...panels] = await Promise.all(images.map(decode))
        const canvas = document.createElement('canvas')
        const ctx = canvas.getContext('2d', { willReadFrequently: true })
        let offset = 0
        for (const panel of panels) {
          offset += 48
          canvas.width = panel.naturalWidth
          canvas.height = panel.naturalHeight
          ctx.drawImage(output, 0, -offset)
          const actual = ctx.getImageData(0, 0, canvas.width, canvas.height).data
          ctx.clearRect(0, 0, canvas.width, canvas.height)
          ctx.drawImage(panel, 0, 0)
          const expected = ctx.getImageData(0, 0, canvas.width, canvas.height).data
          if (!expected.every((value, i) => value === actual[i])) return false
          offset += canvas.height
        }
        return true
      }, images)
      assert(matches, 'Figure pixels differ from the selected panels: ' + figure.number)
    }
  }
}

async function inspectRun() {
  const report = {
    run,
    command,
    at: new Date().toISOString(),
    passed: false,
    visualReview: 'pending',
  }
  let browser
  try {
    browser = await chromium.launch({ channel })
    const page = await browser.newPage()
    const manifest = await verifyRaw(page)
    if (command === 'compose') await compose(page, manifest)
    if (command !== 'verify-raw') await verifyFigures(page, manifest)
    report.passed = true
    console.log(command, 'passed for', run)
  } catch (error) {
    report.reason = error.message
    throw error
  } finally {
    await browser?.close()
    writeJSON(join(meta, command + '-' + randomUUID() + '.json'), report)
  }
}

try {
  if (command === 'capture') capture()
  else await inspectRun()
} catch (error) {
  console.error('Stage 5:', error.message)
  process.exitCode = 1
}
