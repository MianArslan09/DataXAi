# Chapter 4.4.1 frontend expansion — Stage 1 baseline

16 November 2026 · Owner review pending

## Fresh checks

TypeScript and the production build passed. The existing regression suite collected **29 tests and passed all 29 in 54.0 seconds** with two workers, zero failures and zero skips. This included all eight original figure cases with screenshot export unset. **Formatting failed** on `src/pages/Login.tsx`. The finding was present before the evidence update and remains open. The November 15 report’s 29 passes in 50.5 seconds are historical evidence.

### Commands and outcomes

First command at repository root: `git status --short --branch`. Follow-up inspection used:

```powershell
git rev-parse HEAD
git diff --stat
git diff --cached --stat
git ls-files --others --exclude-standard
git status --short --ignored --untracked-files=normal
```

Branch `feat/etl-4.5.3b-completion-proof`, HEAD `75ef08d22d45a17a2ef30cf4239402978c80cdf5`. The index had no changes. The tracked working diff was four paths, 80 insertions and 24 deletions: root .gitignore, apps/etl/sources/csv_source.py, apps/etl/sources/excel_source.py and config/settings/base.py. Untracked items included the seven files under _local_archives, apps/etl/source_snapshots.py, apps/etl/tests/test_source_snapshots.py and the frontend tree. Ignored datasets, environment, SQLite database, dependencies, build outputs and caches were present. An untracked frontend cannot be reconstructed from HEAD.

Exact verification commands from the frontend directory:

```powershell
npm.cmd run typecheck
npm.cmd run build -- --outDir D:/dataxai/frontend/node_modules/.cache/stage1-1794813692539/build
npm.cmd run format:check
$env:CAPTURE_FIGURES=$null
$env:LIVE_DJANGO='1'
npm.cmd test -- --output D:/dataxai/frontend/node_modules/.cache/stage1-1794813692539/results
```

| Check | Result |
|---|---|
| TypeScript | Exit 0. |
| Production build | Exit 0. Vite 7.3.8 transformed 1,600 modules and produced HTML, local font, CSS and JS in the dedicated ignored output directory. |
| Prettier | Exit 1. Only src/pages/Login.tsx flagged. No formatter write was run. |
| Playwright | Exit 0. Contract tests 8/8, API-response tests 4/4, workflow tests 8/8, original figure tests 8/8, live HTTP test 1/1. |
| Live HTTP | Temporary Django instance using in-memory SQLite. Health 200, anonymous identity 401, rejected password 401, token and identity 200 for each role, refresh and subsequent identity 200. |

The original figure tests ran inside the full suite. They exercised the UI at 1440 × 900, checked essential content, table bounds, card overlap, font and browser errors. They do not compare screenshot pixels. The existing configuration built both demo and API bundles in its ignored cache. The dedicated result directory protected prior test outputs.

## Historical screenshot custody

The checkout figure directory was initially empty. All eight matching originals were found in Downloads. The owner pasted them back during this pass. The following measurements are from the files now in frontend/docs/figures. All eight SHA-256 values match the previous planning readings. These files preserve the prior branding and display names. Current branding and names belong in the separately versioned 24-image collection after approval.

| Historical PNG | IHDR dimensions | SHA-256 |
|---|---|---|
| [figure-4.1-shared-login.png](figures/figure-4.1-shared-login.png) | 1440 × 900 | `63D19789E3B15973C7BF6F56609EEB8769C12323BC2E6A8871AB3411D8642EF9` |
| [figure-4.2-admin-dashboard.png](figures/figure-4.2-admin-dashboard.png) | 1440 × 900 | `E38C53C46D496B13075D0932E3F442596A29CB96A376BBACAF4A43B808153C03` |
| [figure-4.3-create-account.png](figures/figure-4.3-create-account.png) | 1440 × 900 | `86AFB8E3A006ECC6BE871CA712935DF5C0941D99450934C1371EBDE971CEF360` |
| [figure-4.4-manager-dashboard.png](figures/figure-4.4-manager-dashboard.png) | 1440 × 900 | `BB053DE9CCC88BB9E4F2FF7C0E0F0C8A965EEF1D496469D7ED8EAE551429A02B` |
| [figure-4.5-seller-dashboard.png](figures/figure-4.5-seller-dashboard.png) | 1440 × 900 | `7F19109E4D9E8508F3D65DC7ECDA2F3E7469E7B0D49EB7BA5D4E4F4679E3419C` |
| [figure-4.6-batch-processing.png](figures/figure-4.6-batch-processing.png) | 1440 × 900 | `00407A1844A7AAC52C8F70B2233BF4ADCA8446CE36756D2E56252E8B96E7CA19` |
| [figure-4.7-quarantine-review.png](figures/figure-4.7-quarantine-review.png) | 1440 × 900 | `9AB7311B7394ABFB2A8F7D42E01C120DCC3CF5004EF9D37ABDE0B67A33619C5E` |
| [figure-4.8-repair-explanations.png](figures/figure-4.8-repair-explanations.png) | 1440 × 900 | `67EEE027A64BAB2E8B6A5F556958436C3AD15640CFC93CA75B0CAC1770618B6F` |

Exact final image verification from the repository root:

```powershell
@'
from pathlib import Path
import struct,hashlib
files=sorted((Path('frontend/docs/figures')).rglob('*.png'))
assert len(files)==8,files
for p in files:
 b=p.read_bytes()
 assert b.startswith(b'\x89PNG\r\n\x1a\n') and b[12:16]==b'IHDR'
 w,h=struct.unpack('>II',b[16:24])
 print(f'{p.name}|{w}|{h}|{hashlib.sha256(b).hexdigest().upper()}')
'@ | .\dataxaienv\Scripts\python.exe -B -
```

## Chapter 3 source and ownership

Source: [DataXAi Chapter 3 — Requirements Specification](<D:/FYP Docx/DataXAi_Chapter_3.docx>), SHA-256 `D6D66F3CE0964A04E00CC6E43A671938C5A1B245850F7C0B35A7BDA9957DEE1A`. Read the Word document body and table cells. References below use §3.3, §3.4 Table 3.2 and the detailed use cases in §3.5. Chapter 3 resolves the previously missing ownership citation.

| Requirement and use case | Verified responsibility and current gap |
|---|---|
| FR-04 / UC-02 Manage System Configuration | Only Admin maintains platform-wide settings, including available source connections. Record who changed the setting and when. Credentials remain server-side. Current demo shows a review preference. A Seller selecting an available source in state 09 does not demonstrate Admin maintenance or its audit trail. |
| FR-11 / UC-03 Configure Processing Rules | Manager configures assigned-business validation rules, statistical alert thresholds and permitted repair actions. Changes are versioned so older runs remain interpretable. Table 3.2 also links UC-03 to FR-15. A required-column editor alone covers only part of FR-11. Preserve all three categories in the future rules view. |
| FR-08 / UC-04 Run Batch Processing, §3.5.1 | Manager selects an identified submission. The run identifies business, submission and applied rule version. Full UC references FR-07–18, FR-20 and FR-23, subject to FR-01/03. The current simulated execution lacks a rule-version field. |
| FR-19 / UC-05 Resolve Quarantined Records, §3.5.2 | Manager inspects evidence and may confirm the original, authorise supported correction or retain the item. Record Manager, reason, time and resulting eligibility, preserving earlier decisions. UC covers FR-15–20 subject to FR-01/03. Current demo stores the latest resolution. |
| FR-24 and FR-25 / UC-10 View Repair Explanations, §3.5.3 | Seller sees decision-linked recorded facts, template and optional generated explanation. Generation has read-only access to authorised business facts. On failure retain decision and template, mark generated text unavailable and preserve repair, quarantine and predictions. Table 3.2 also links FR-16. Existing fallback is simulated. Explicit shared decision identity remains future work. |

## Approved current collection — 24 states across ten groups

All locations are hash routes. Existing views below are implementation inventory, not new screenshots. Current-state export and the separate 32-PNG evidence library remain deferred.

| State | Group | Location | Current coverage or outstanding evidence |
|---:|---:|---|---|
|01|1|#/login|Shared login, existing.|
|02|1|#/admin/dashboard|Admin dashboard, existing.|
|03|1|#/admin/create-account|Account creation form, existing.|
|04|1|#/admin/configuration|System-wide preference exists. Explicit source settings/change attribution needed to illustrate full FR-04.|
|05|2|#/manager/dashboard|Manager dashboard, existing.|
|06|2|#/seller/dashboard|Seller dashboard, existing.|
|07|3|#/seller/submit|CSV receipt exists for new submissions. Canonical SUB-1048 receipt access needs reconciliation with the already seeded submission.|
|08|3|#/seller/status|Proposed workbook detail with selected, entered and completed worksheet scope on SUB-1047.|
|09|3|#/seller/submit|Proposed selection of business-authorised configured PostgreSQL source.|
|10|4|#/manager/batch?id=SUB-1048|Running EX-1048 and ACK-1048 exist. Applied version missing.|
|11|4|#/manager/batch?id=SUB-1047|Proposed interrupted EX-1047 with bounded partial progress.|
|12|4|#/manager/batch?id=SUB-1048|Success simulation exists. Separate simulated accounting from unavailable real committed counts.|
|13|5|#/manager/batch|Proposed Manager processing-rule configuration, business loom, version RULE-LOOM-002.|
|14|5|#/manager/batch?id=SUB-1048|Proposed Validation tab with bound version, dataset result and separate VAL-1048-01 on ROW-1048-017.|
|15|6|#/manager/batch?id=SUB-1048|Proposed Detection overview with statistical baseline and Isolation Forest result availability.|
|16|6|#/manager/batch?id=SUB-1048|Proposed selected finding FND-042 on ROW-1048-042 with order evidence.|
|17|7|#/manager/quarantine|Original pre-decision QR-042 and three decisions exist.|
|18|7|#/manager/quarantine|Retained state exists. Proposed DEC-042-01, reason and eligibility.|
|19|7|#/manager/quarantine|Proposed append-only history, retention followed by DEC-042-02.|
|20|8|#/seller/forecasts|FC-011 historical/predicted weekly units exist. Explicit cutoff, horizon and run-availability context needed.|
|21|9|#/seller/spending|CUST-018, CLV-015, £284, GBP, 90 days and estimate date exist. Selected customer and observation cutoff context needed.|
|22|9|#/seller/spending|Cedar Stationery Seller empty state exists.|
|23|10|#/seller/repairs|REP-042 with template and simulated narrative exists. Explicit DEC-042-02 required.|
|24|10|#/seller/repairs|Unavailable fallback exists. Same canonical DEC-042-02 required.|

Approved group sizes: 4 + 2 + 3 + 3 + 2 + 2 + 3 + 1 + 2 + 2 = 24. The preserved originals are eight additional historical files. The planned invoice chain links SUB-1048, EX-1048 and RULE-LOOM-002 to ROW-1048-042, DEMO-INV-042, FND-042, QR-042 and EV-042 / OC-1042. Three mugs at £40 yield £120; three at £4 yield £12. Historical spending changes by −£108 with quantity unchanged. DEC-042-02 must agree across Manager and Seller views. These proposed IDs and relationships are not runtime evidence. Forecast and customer estimates remain independent illustrative outputs.

## Files and integration boundaries inspected

- Frontend package scripts, Playwright configuration, figure cases and export guard, fixture server, tests for API responses, role contracts, workflows and live authentication.
- src/App.tsx, routes.ts, auth role adapter, identity and provider. Admin, Manager, Seller, Login and Repairs pages. Components for Shell, Workspace, Submissions, Analytics, shared UI, EvidenceTable and Impact.
- services/contracts.ts, providers.ts, demo.ts, apiClient.ts and fixtures/seed.ts. The typed source variants are CSV/Excel. Execution has one nullable accountedRows field. Resolution has decision, actor and timestamp. The role map retains Admin, Manager and Seller. Workflow data comes from the demo provider only.
- Frontend base, login and workspace styles, current logo path, package/configuration and lockfile inventory, existing verification and integration documentation, restored figure index and PNGs. Django root, accounts and core URL configurations, Chapter 3 attachment and the historical planning hashes.
- No PostgreSQL persistence, ETL, worker interruption, detector inference, forecast recomputation or Groq response was verified in this pass. The live authentication fixture exercises the current Django auth and health boundaries only.

## Stage 2 readiness and changes

The functional and screenshot-layout baseline is locally verified. Formatting is an open baseline failure. All eight historical PNGs are accounted for. Stage 2 can be reviewed with that exception explicitly recorded. Owner acceptance and separate Stage 2 authorisation remain pending. The substantive Chapter 3 gaps above belong to future work. Keep the existing test suite, source data, unrelated working changes and role boundaries intact.

Only authored deliverable: frontend/docs/STAGE_1_BASELINE.md. Generated build/test caches are ignored. The owner returned historical files during the pass. No application, backend, screenshot, Git index or branch changes were made by the verifier.

## Stage 1 formatting correction - 2026-11-16

### Correction performed

The previously reported Prettier formatting failure in
`frontend/src/pages/Login.tsx` was addressed manually.

Commands executed:

```powershell
npm.cmd exec -- prettier --write src/pages/Login.tsx
npm.cmd run format:check
npm.cmd run typecheck
npm.cmd run build
```

A temporary backup of Login.tsx was created before formatting.
The formatting command targeted Login.tsx only.

### Fresh verification results

| Check | Result |
|---|---|
| Prettier | PASS - All matched files use Prettier code style |
| TypeScript | PASS - tsc --noEmit |
| Production build | PASS - Vite 7.3.8, 1600 modules transformed |
| Original Playwright figures | PASS - 8/8 in 20.6 seconds |
| Full Playwright suite | PASS - 29/29 in 44.4 seconds |
| Failed tests | 0 |
| Skipped tests | 0 |

Screenshot generation was disabled using:

```powershell
$env:CAPTURE_FIGURES = $null
$env:CAPTURE_COVERAGE_FIGURES = $null
```

The full regression suite was executed with LIVE_DJANGO=1.

The live authentication test used an isolated Django test
instance backed by temporary SQLite. It does not establish
production PostgreSQL or ETL integration.

### Historical figure integrity

All eight historical screenshots remain at 1440 x 900.

Their SHA-256 hashes were checked after the formatting
correction and matched the values previously recorded in
this document.

No screenshot regeneration was performed.

### Scope and acceptance status

- Stage 1 formatting exception: Resolved.
- Frontend quality checks: Passed.
- Frontend regression tests: Passed.
- Historical screenshot integrity: Verified against baseline.
- Stage 2 implementation: Not started.
- Formal Stage 1 owner acceptance: Pending explicit confirmation.

This verification does not establish integration of source
connections, ETL execution, PostgreSQL commits, anomaly
detection, forecasting, customer spending prediction or Groq.
