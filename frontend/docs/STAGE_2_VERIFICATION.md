# Stage 2 verification — retail submission and ETL execution evidence

16 November 2026 · Chapter 4.4.1 · Groups 3 and 4 · States 07–12

**IMPLEMENTED · TESTED · LOCALLY VERIFIED** for the React demonstration. Formal Stage 2 acceptance is pending owner review.

## Reproducing the six states

Use the Lahore Homewares (`loom`) demonstration presets. Hash navigation and signing out retain in-memory evidence in the same tab. A full reload starts a fresh demonstration. To reproduce 08 and 11 with identical evidence, prepare 11 first, then change to Seller in the same tab.

| State | Actor and route | Interaction and expected evidence |
| --- | --- | --- |
| 07 · CSV receipt | Seller · `#/seller/submit` | Choose **View sample CSV receipt**. It shows **Seeded sample**, Lahore Homewares, CSV, `november_orders.csv`, `SUB-1048`, entire submitted file and **Registered**. Initially its execution is **Not Requested** and overall completion **Unverified**. Uploading a file of the same name creates a separate **New registration**, initially `SUB-1049`. |
| 08 · Workbook scope | Seller · `#/seller/status?id=SUB-1047` | Open `retail_export.xlsx` after 11. **Store orders** has 512 declared rows and completed simulated traversal. **Marketplace orders** has 344 declared rows and interrupted traversal. Both were selected. **Notes** has 4 declared rows and was outside the request. Before processing the selected sheets show **No entry recorded**. |
| 09 · Configured PostgreSQL source | Seller · `#/seller/submit` | Select **PostgreSQL**, then **Lahore retail transactions · PG-LOOM**. Connection status is unverified. Only aliases for the signed-in business are selectable. Submitting registers the selected alias. |
| 10 · Running CSV execution | Manager · `#/manager/batch?id=SUB-1048` | **Request processing → Queue execution → Start simulation**. The running timeline shows `ACK-1048`, `EX-1048` and business context. Applied rule version reads **No version supplied**. Simulated row accounting and the authoritative committed count are unavailable. |
| 11 · Interrupted workbook execution | Manager · `#/manager/batch?id=SUB-1047` | **Request processing → Queue execution → Start simulation → Complete worksheet batch → Interrupt simulation**. `EX-1047` shows batch 1 completed with 512 simulated rows, batch 2 entered and interrupted with unknown rows, and incomplete scope. **Inspect worksheet coverage** opens the same record in the Manager register. |
| 12 · Completed CSV simulation | Manager · `#/manager/batch?id=SUB-1048` | From 10 choose **Complete simulation**. The execution is **Succeeded**. One simulated batch accounts for 1,240 fixture rows. Overall submission completion is **Unverified**. PostgreSQL committed rows read **Authoritative count unavailable**. |

## Evidence contracts and scope

- A submission records registration ID, business, origin, filename/source label, source type and requested scope. The provider still accepts filenames. New request variants identify a known workbook and selected sheets, or a business-authorised alias. Choosing any local file, including one named `retail_export.xlsx`, registers only its filename. Its worksheet metadata is unavailable.
- `WB-LOOM`, the ordered worksheet names/counts and the configured aliases are illustrative fixture choices. The seeded request selects the first two of three sheets, totalling 856 declared rows. Sample registration validates selection and preserves manifest order. One worksheet is one simulated batch. Unknown counts are stored as `null`.
- Execution records carry a processing receipt, stage history, optional applied rule version, per-batch events, simulated accounted rows and a separate overall scope outcome. Interruption retains earlier events and completed row accounting. Only explicit completion of every selected sample sheet reconciles the requested subset in simulation. A completed file batch leaves overall source coverage unverified.
- FR-04 / UC-02 reserves platform source configuration to Admin. Seller selects from fixture aliases. FR-08 / UC-04 places the processing request with Manager. Stage 3 must supply the saved business-specific rule version under FR-11 / UC-03 before an execution can display it. Stage 2 does not assign `RULE-LOOM-002`.
- The demo provider enforces business ownership on snapshots, registrations and execution actions. Direct references resolve against that snapshot. API mode has no workspace service for these actions. Mocked authentication exercises the unavailable-integration view. The prototype does not parse uploads, probe connections, dispatch Celery jobs, retry work or persist PostgreSQL counts.
- Stage 2 processing has no repair or prediction side effects. The existing `DEMO-INV-042` / `OC-1042` evidence still represents three Ceramic Mugs at £40 / £120 source and £4 / £12 supported, with a −£108 historical spending difference and the same quantity.

## Files reviewed and changed

Paths below are relative to `frontend/`.

| Files created or edited | Scope |
| --- | --- |
| `src/services/contracts.ts`, `src/fixtures/seed.ts`, `src/services/demo.ts` | Typed source scopes and batch events, sample data, registration identity, business checks and simulation transitions. |
| `src/pages/Submit.tsx` (new), `src/components/SubmissionReceipt.tsx` (new) | Seller controls and shared receipt / worksheet view. |
| `src/App.tsx`, `src/pages/Seller.tsx`, `src/components/Submissions.tsx` | Scoped direct receipt selection, submission register and navigation. |
| `src/pages/Manager.tsx` | Execution controls, drill-down and batch history. Both dashboards label successful execution counts by their unit. |
| `src/components/ui.tsx`, `src/styles/base.css` | Interruption warning icon, receipt dialog width and worksheet controls. |
| `tests/stage2.spec.ts` (new) | Eleven focused browser/provider cases. |
| `docs/STAGE_2_VERIFICATION.md` (new) | This record. |

Inspection also included `docs/STAGE_1_BASELINE.md`, `docs/figures/`, `package.json`, `tsconfig.json`, Vite and Playwright configuration, `src/routes.ts`, API/demo provider selection, workspace context, shared shell and responsive styling, and the existing workflow, contract, API and figure specs and helpers.

## Verification execution

Before editing, we reviewed the Stage 1 follow-up. It recorded 29/29 tests with live Django enabled, eight original figure checks and the corrected Login formatting check. Owner acceptance of Stage 1 preceded this implementation.

These verification commands ran from `frontend/` in PowerShell.

```powershell
npm.cmd run typecheck
npm.cmd run build -- --outDir node_modules/.cache/stage2-review/build
npm.cmd run format:check
$env:CAPTURE_FIGURES=$null
$env:CAPTURE_COVERAGE_FIGURES=$null
$env:LIVE_DJANGO=$null
npm.cmd test -- tests/stage2.spec.ts tests/workflows.spec.ts tests/contracts.spec.ts tests/api.spec.ts tests/figures.spec.ts --output node_modules/.cache/stage2-review/results
```

TypeScript, the Vite production build and Prettier passed. The final Playwright run passed **39/39 in 43.0 seconds** on two workers with zero failures and zero skips.

Coverage comprises eight original 1440 × 900 figure-layout regressions, eight workflow tests, eight contract cases, four mocked API tests and eleven Stage 2 cases. Browser assertions exercised all six planned states at 1440 × 900. The mobile flow used 390 × 844. Live Django, backend, performance and CI verification are **DEFERRED** for this stage.

During development TypeScript caught a missing `Empty` import in `Submissions.tsx`. The additional mobile test initially timed out trying to sign out with the drawer closed. Its next run timed out matching the submission selector by exact wrapping-label text. Each run passed 38 of 39 cases. The corrections added the missing import, opened the drawer before sign-out, and selected the combobox by its accessible role and name. Existing regression assertions were retained. Build and test output was directed to the frontend dependency cache. Targeted formatting used these commands.

```powershell
.\node_modules\.bin\prettier.cmd --write src/services/contracts.ts src/services/demo.ts src/fixtures/seed.ts src/App.tsx src/components/SubmissionReceipt.tsx src/components/Submissions.tsx src/pages/Seller.tsx src/pages/Submit.tsx src/pages/Manager.tsx src/styles/base.css tests/stage2.spec.ts
.\node_modules\.bin\prettier.cmd --write src/components/SubmissionReceipt.tsx src/components/ui.tsx src/pages/Manager.tsx src/services/contracts.ts src/services/demo.ts tests/stage2.spec.ts
```

## Git and historical evidence

The first command was `git status --short --branch`. The initial and final root short status matched.

```text
## feat/etl-4.5.3b-completion-proof
 M .gitignore
 M apps/etl/sources/csv_source.py
 M apps/etl/sources/excel_source.py
 M config/settings/base.py
?? _local_archives/
?? apps/etl/source_snapshots.py
?? apps/etl/tests/test_source_snapshots.py
?? frontend/
```

HEAD `75ef08d22d45a17a2ef30cf4239402978c80cdf5`. `git diff --cached --name-only` returned no paths. The untracked frontend appears as one directory in root short status. Existing tracked modifications span four files (80 insertions, 24 deletions). No staging, branch change, commit, push or PR occurred.

The following command produced identical before/after SHA-256 values. The eight PNGs also match the Stage 1 inventory. Neither capture flag was enabled and no images were exported.

```powershell
Get-ChildItem docs/figures/*.png,docs/STAGE_1_BASELINE.md | Get-FileHash | Select-Object Hash,Path | ConvertTo-Json
```

| Protected file | SHA-256 |
| --- | --- |
| `figure-4.1-shared-login.png` | `63D19789E3B15973C7BF6F56609EEB8769C12323BC2E6A8871AB3411D8642EF9` |
| `figure-4.2-admin-dashboard.png` | `E38C53C46D496B13075D0932E3F442596A29CB96A376BBACAF4A43B808153C03` |
| `figure-4.3-create-account.png` | `86AFB8E3A006ECC6BE871CA712935DF5C0941D99450934C1371EBDE971CEF360` |
| `figure-4.4-manager-dashboard.png` | `BB053DE9CCC88BB9E4F2FF7C0E0F0C8A965EEF1D496469D7ED8EAE551429A02B` |
| `figure-4.5-seller-dashboard.png` | `7F19109E4D9E8508F3D65DC7ECDA2F3E7469E7B0D49EB7BA5D4E4F4679E3419C` |
| `figure-4.6-batch-processing.png` | `00407A1844A7AAC52C8F70B2233BF4ADCA8446CE36756D2E56252E8B96E7CA19` |
| `figure-4.7-quarantine-review.png` | `9AB7311B7394ABFB2A8F7D42E01C120DCC3CF5004EF9D37ABDE0B67A33619C5E` |
| `figure-4.8-repair-explanations.png` | `67EEE027A64BAB2E8B6A5F556958436C3AD15640CFC93CA75B0CAC1770618B6F` |
| `STAGE_1_BASELINE.md` | `DFD2759033D9A8D84AE3ED27E14D4FEDFA42809A3806944ED76F091D6E72AAEB` |

The next gate is owner review of these six interactive states. Stage 3 and figure capture require separate authorisation.
