# Frontend Stage 5 · Chapter 4 screenshot evidence

The owner runs the capture on the local React demonstration. Each of the 24 recipes starts with a fresh browser context. The ten figure numbers below follow Chapter 4, section 4.4.1. The eight older filenames follow the Stage 1 inventory. For example, historical `figure-4.8-repair-explanations.png` belongs to that inventory. Thesis Figure 4.8 depicts the weekly sales forecast.

## Capture and verify on Windows

Use Node 22.12 or later, the frontend dependencies from `package-lock.json`, and the browser used for the accepted frontend tests. The default on Windows is Google Chrome when found at the configured location, otherwise Playwright Chromium. The capture starts its own demo preview on loopback port 4187. Close any competing preview first. Ordinary regression also needs port 4188. Keep the frontend source and browser version fixed throughout a run.

Run these commands from one PowerShell session. Each command checks its own exit code. The capture switch is independent of the historical screenshot writer.

```powershell
Set-Location D:\dataxai\frontend
$env:CAPTURE_FIGURES = $null
$env:LIVE_DJANGO = $null
$env:CAPTURE_COVERAGE_FIGURES = $null

npm.cmd run typecheck
if ($LASTEXITCODE -ne 0) { throw 'TypeScript check failed' }
npm.cmd run format:check
if ($LASTEXITCODE -ne 0) { throw 'Formatting check failed' }
npm.cmd run build
if ($LASTEXITCODE -ne 0) { throw 'Production build failed' }

$run = 'stage5-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '-' + [guid]::NewGuid().ToString('N').Substring(0,8)
$evidence = Join-Path 'docs\figures\stage5' $run
$env:CAPTURE_COVERAGE_FIGURES = '1'
try {
    npm.cmd run stage5:capture -- $run
    if ($LASTEXITCODE -ne 0) { throw "Capture failed. Preserve $evidence" }
} finally {
    $env:CAPTURE_COVERAGE_FIGURES = $null
}
npm.cmd run stage5:verify-raw -- $run
if ($LASTEXITCODE -ne 0) { throw 'Raw evidence verification failed' }
npm.cmd run stage5:compose -- $run
if ($LASTEXITCODE -ne 0) { throw 'Figure preparation failed' }
npm.cmd run stage5:verify -- $run
if ($LASTEXITCODE -ne 0) { throw 'Final evidence verification failed' }

Get-Content -LiteralPath "$evidence\metadata\capture-summary.md"
Get-Content -LiteralPath "$evidence\metadata\thesis-figure-map.json"
Invoke-Item -LiteralPath "$evidence\thesis"

npm.cmd test
if ($LASTEXITCODE -ne 0) { throw 'Frontend regression failed' }
```

The ordinary Playwright configuration discovers `tests/` only. Stage 5 uses a separate configuration and builds a preview with the demo provider. It rejects the legacy write flag, a live Django capture, or an existing run directory. Leave `CAPTURE_FIGURES` unset even when running the ordinary regression suite.

## Figure and capture map

The panel column gives the export order, starting with (a). Every listed recipe also has its own full-resolution raw PNG. The [capture catalog](../stage5/catalog.json) records the routes, roles, business scope and fixture references.

| Figure and exact caption | Capture ID and state | Export panels |
|---|---|---|
| 4.1 Account access and administration | G01-01 public login, G01-02 Admin overview, G01-03 unsaved Create Account form, G01-04 System Configuration | G01-01, G01-02 |
| 4.2 Manager and Seller dashboard design | G02-01 Manager, G02-02 Seller, both assigned to Lahore Homewares | G02-01, G02-02 |
| 4.3 Retail submission and source receipt | G03-01 CSV receipt for SUB-1048, G03-02 selected workbook worksheets, G03-03 unverified PG-LOOM alias | G03-01, G03-02 |
| 4.4 UC-04 batch processing and execution status | G04-01 running EX-1048, G04-02 interrupted second worksheet, G04-03 succeeded simulation with 1,240 accounted rows | G04-01, G04-02 |
| 4.5 Rule configuration and validation findings | G05-01 saved rule versions, G05-02 nullable Customer ID on ROW-1048-017 | G05-01, G05-02 |
| 4.6 Detection findings for an invoice line | G06-01 three method alerts for one row, G06-02 method assessments and supporting order OC-1042 | G06-01, G06-02 |
| 4.7 UC-05 quarantine review and repair decision | G07-01 awaiting review, G07-02 retained with one history entry, G07-03 authorised with two entries and open evidence disclosure | G07-01, G07-03 |
| 4.8 UC-08 weekly product sales forecast | G08-01 FC-011 historical and predicted weekly units | G08-01 |
| 4.9 UC-09 estimated customer net spending over 90 days | G09-01 CLV-015 and CLV-016, G09-02 Indus Textiles empty state following the Admin's reassignment of the Seller | G09-01 |
| 4.10 UC-10 Seller view of a recorded repair | G10-01 available simulated narrative for REP-042, G10-02 unavailable narrative with facts and deterministic template still visible | G10-01 |

## Evidence and failure handling

- **Historical archive.** Before capture, the launcher checks the eight originals against the [Stage 2 SHA-256 inventory](STAGE_2_VERIFICATION.md) and copies them into the new run's `historical/` directory. It verifies both sets afterward and again on subsequent verification. A digest mismatch stops the operation. Preserve the originals and investigate using the prior inventory.
- **Browser evidence.** `raw/Gxx-yy.png` is the unedited full page. Assertions check the intended state, actual route, role and business where applicable, demo mode, dimensions, and horizontal overflow or clipped evidence containers. Each recipe uses 1440 × 900 CSS pixels, device scale 1, en-GB, Asia/Karachi and reduced motion. The app clock is fixed at 2026-11-17 09:00 UTC. The capture timestamp comes from the runner's real clock.
- **Provenance.** `metadata/run.json` records the browser channel, tooling, platform, repository HEAD if available and a SHA-256 fingerprint of the actual source inputs, including untracked files. Per-capture JSON records the browser version, interactions, asserted state, fixture identifiers, timestamp, PNG dimensions and digest. `capture-manifest.json`, `hashes.sha256` and `capture-summary.md` account for the run. Rendering can vary with browser, operating system and installed fonts.
- **Failure.** Individual recipes run independently, once each. A failed assertion leaves the expected state unverified. The manifest identifies failed or unexecuted IDs, and the command exits unsuccessfully. A forcibly interrupted process can leave a partial directory without a final manifest. Preserve that directory and the terminal output. After resolving the cause, generate a **new** run ID and repeat. A partial capture cannot be composed. A second composition cannot overwrite even a partially populated thesis directory.
- **Figure export.** `thesis/figure-4.1.png` through `thesis/figure-4.10.png` are either exact copies or native-resolution panels stacked with a separate label band. `thesis/panels/` holds byte-identical copies of the selected originals. `metadata/thesis-figure-map.json` records their captions, source IDs, dimensions and hashes. Both verification commands decode PNGs with Chromium and compare recorded hashes and dimensions. Final verification also compares each composite's image regions pixel-for-pixel with its source panels. Repeating verification writes a new report in `metadata/`.

## Owner visual review

1. Open every raw image at 100%. Confirm labels, values, tenant identity and evidence IDs. Inspect modal edges, disclosure text, table columns, focus outlines and the forecast boundary. Natural vertical scrolling produces a taller PNG.
2. Review each thesis figure at the **actual intended Word page width and print size**. Check chart labels, table cells and panel identifiers. If a tall figure needs too much reduction, use the individual panels at a readable width on successive lines or pages under the same figure number. Record the run ID, figure, panel IDs, reviewer, date and disposition separately. Machine verification leaves visual review pending.
3. For supplementary responsive inspection, start the demo locally with `$env:VITE_DATA_MODE='demo'` and `npm.cmd run dev -- --host 127.0.0.1`. Open the printed local URL at 390 × 844 in browser device emulation. Check navigation, receipt dismissal, review actions and chart/table access. Keep the 1440-pixel desktop capture as the thesis source.

## Interpreting the evidence

Admin controls assignments, Managers review their business's work, and Sellers see their active business. G09-02 reassigns the specific demonstration Seller through the Admin UI, then signs out and signs in as that Seller in the same tab. The historical Lahore estimates remain owned by Lahore Homewares.

Source registration, simulated execution, validation examples and grouped detector alerts are separate states. G07-03 records **Correction authorised — not applied** in local decision history. G10 uses the separately seeded **Post-commit demonstration scenario**. Its three Ceramic Mugs remain three units: £40 and £4 per unit give £120 and £12, a processed-minus-original transaction difference of −£108. The figures supply no measured change in model performance or predictive value. Forecast and spending figures use illustrative series and estimates. Optional narrative scenarios use the demonstration provider. Claims of Django persistence, real extraction, model inference or live Groq execution require separate evidence.
