# Frontend verification record

**15 November 2026 · IMPLEMENTED · TESTED · LOCALLY VERIFIED.** The eight-screen React frontend is ready for owner review. Formal acceptance remains with the owner.

## Checkout and scope

The inspected checkout was `feat/etl-4.5.3b-completion-proof` at `75ef08d`, the local PR #12 merge. The index was empty. The root `.gitignore` had an existing modification and `_local_archives/` was untracked. Ignored local datasets, environment configuration, caches and the existing test database were outside this work. Inspection found no existing React app or package manifest. The frontend handoff authorised a separate root-level React app. All deliverables are under `frontend/`. The backend milestone gate and historical checkpoints were preserved.

## Executed checks

The final run used Node.js 24.18.0, npm 12.0.1, React 19.3.1, TypeScript 5.9.3, Vite 7.3.8, Playwright 1.65.1, Google Chrome 157.0.8103.51, and the repository’s Django 5.2.17 environment.

| Check | Command or method | Observed result |
| --- | --- | --- |
| Dependency resolution | npm install with the frontend package manifest and local cache | Dependencies installed, lockfile generated. Local font loaded during screenshot tests. |
| Type checking and production bundle | `npm.cmd run build` | Exit 0. Strict `tsc --noEmit` passed, Vite transformed 1,600 modules and emitted HTML, JavaScript, CSS and a local Inter font under dist. |
| Code formatting | `npm.cmd run format:check` | Exit 0. Prettier checked application code, test TypeScript, configuration and the HTML entry point. |
| Focused frontend and HTTP suite | `npm.cmd test` with LIVE_DJANGO=1 and CAPTURE_FIGURES=1 | **29 collected, 29 passed, 0 failed, 0 skipped**, two workers, 50.5 seconds. Both demo and API configurations were built and served as production bundles. |
| Chapter captures | Eight Playwright figure cases, PNG header checks and visual inspection | Eight viewport PNGs, each 1440 × 900 at 1× scale. The suite checked horizontal overflow, table bounds, panel overlap, required content and card bottoms. The saved figures and their local handoff links were checked after export. |
| Responsive navigation | Browser workflow at 390 × 844 | Seller opened and dismissed the menu with Escape, navigated to a repair explanation, and retained the viewport width. |
| Repository review | Git status, cached and working-tree diff inspection | New deliverables under frontend. The pre-existing root change and archive entry remained. Nothing staged or committed. |

To reproduce the full suite from the frontend directory, use the repository’s Python environment or set `DATAXAI_PYTHON` to a Python executable with its backend dependencies installed.

```powershell
$env:LIVE_DJANGO = '1'
$env:CAPTURE_FIGURES = '1'
npm.cmd test
Remove-Item Env:LIVE_DJANGO,Env:CAPTURE_FIGURES
```

Playwright builds each frontend mode into its own ignored cache directory. The optional WSGI fixture binds only to loopback port 8193 and migrates the account dependencies into a fresh in-memory SQLite database. Synthetic identities, a temporary signing key and a short access-token lifetime exercise refresh within the test. The fixture exits with the suite. The default suite omits the one live-Django case and can run independently.

## Coverage

| Test file | Cases | Evidence |
| --- | ---: | --- |
| contracts.spec.ts | 8 | All three legacy roles map to the correct dashboard with raw role preserved. Unknown roles and prototype property names are rejected. Scope and role changes restrict both reads and mutations. All three Manager decisions preserve original invoice evidence. |
| api.spec.ts | 4 | Controlled responses cover unsupported roles, denied access, server failure, connection loss, missing business context and rejected refresh. Browser interception isolates these tests from the live Django case. |
| workflows.spec.ts | 8 | Account creation, duplicate rejection, reassignment, scoped login, preferences, local file submission, manager/seller continuity, batch success/failure, queue search, confirmation, retention, original/correction decisions, narrative fallback, weekly units, 90-day estimates, expiry, direct-route denial and mobile navigation. |
| figures.spec.ts | 8 | Each mandated screen has a reproducible capture. The initial layout check detected a decision below the target viewport. The revised review screen places the proposed analytical impact alongside the evidence, leaving all three actions in view. |
| live.spec.ts | 1 | Real HTTP token issuance for all three roles, wrong-password rejection, authenticated/anonymous identity responses, successful token refresh and health. API-mode screens used the actual identity response and restricted missing business scope. |

The UI presents `DEMO-INV-042` as three mugs at £40 each, with evidence for £4 each. Original and processed totals are £120 and £12, giving a £108 historical difference. The manager sees a proposed correction, and the seller sees a separate simulated post-commit snapshot. The automated checks also exercised the explanation fallback. [Figures and reproduction steps](FIGURES.md)

## Files delivered

All paths below are relative to `frontend/`.

| Area | New files |
| --- | --- |
| Project and tooling | .env.example, .gitignore, .prettierignore, .prettierrc.json, index.html, package.json, package-lock.json, tsconfig.json, vite.config.ts, playwright.config.ts |
| Entry and navigation | public/mark.svg, src/main.tsx, src/App.tsx, src/routes.ts |
| Auth | src/auth/AuthProvider.tsx, src/auth/identity.ts, src/auth/roleAdapter.ts |
| Contracts, providers and fixtures | src/services/contracts.ts, src/services/providers.ts, src/services/apiClient.ts, src/services/demo.ts, src/fixtures/seed.ts |
| Shared UI | src/components/ui.tsx, src/components/Shell.tsx, src/components/Workspace.tsx, src/components/Submissions.tsx, src/components/Analytics.tsx |
| Views | src/pages/Login.tsx, src/pages/Admin.tsx, src/pages/Manager.tsx, src/pages/Seller.tsx, src/pages/Repairs.tsx |
| Styles | src/styles/base.css, src/styles/workspace.css, src/styles/login.css |
| Test harness | tests/helpers.ts, tests/serve_django.py, tests/contracts.spec.ts, tests/api.spec.ts, tests/workflows.spec.ts, tests/figures.spec.ts, tests/live.spec.ts |
| Handoff and figures | README.md, docs/INTEGRATION.md, docs/FIGURES.md, docs/VERIFICATION.md and eight named PNG files in docs/figures, linked individually in the figure index |

## Verification limits

The live test covered Django identity and health with isolated SQLite. Production deployment, PostgreSQL, Docker, CI, the full backend suite, ETL execution, Groq calls, model inference and persistent account/business/repair workflows were outside this verification. Chrome was the tested browser. The demonstration is tab-local and resets on reload. Business assignment, authoritative processing receipts and outcomes, evidence-backed review writes, published repair facts and dated model results require future permission-checked APIs. [API inventory and workflow gap register](INTEGRATION.md)

## Verification checkpoint - 2026-11-16

### Stage 1 follow-up verification

The previously reported Login.tsx Prettier formatting issue
was corrected using the locally installed Prettier formatter.

Fresh verification performed on 16 November 2026:

- Prettier formatting: PASS.
- TypeScript type checking: PASS.
- Vite production build: PASS.
- Eight original figure regressions: 8 passed.
- Full frontend Playwright regression: 29 passed.
- Failed tests: 0.
- Skipped tests: 0.
- Historical screenshot SHA-256 values: Unchanged.

The figure-only test run completed in 20.6 seconds.
The complete frontend regression run completed in 44.4 seconds.

Screenshot exports were disabled throughout verification.

The full test suite included the isolated Django authentication
and health test. This does not verify PostgreSQL-backed ETL,
Celery processing, ML inference or live Groq generation.

### Screenshot expansion status

The Chapter 4.4.1 expansion plan defines:

- 10 thesis figure groups.
- 24 planned current-version screenshot states.
- 8 historical screenshots to preserve separately.

The expanded screenshots have not been generated.
Stages 2-5 have not been implemented.

### Acceptance status

Stage 1 functional and formatting verification passed.
Formal Stage 1 acceptance remains subject to the project
owner's explicit confirmation.

See docs/STAGE_1_BASELINE.md for the detailed baseline,
historical screenshot hashes and verification evidence.
