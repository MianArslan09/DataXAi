# DataXAi — Project State

Persistent checkpoint, updated at the end of every volume. Append below; don't delete history.

## Historical Snapshot — 2026-10-04

This section records live repository observations made on 2026-10-04. It is retained as history and superseded by the latest dated checkpoint at the end of this file. Its status statements do not describe the current checkout.

### Repository
- Branch at inspection: `main`, tracking `origin/main`.
- HEAD at inspection: `56fc002` (`fix(scripts): E402 lint error introduced by the sys.path fix`).
- Before these documentation edits, Git status showed the new/untracked `AGENTS.md`, `.github/copilot-instructions.md`, `dataxaienv/`, and `docs/handbook/PROJECT_ROADMAP.md`; the first two and handbook roadmap were empty. `docs/PROJECT_ROADMAP.md` did not exist.
- `dataxaienv/Scripts/python.exe` exists in the live tree. Python version, Django/pytest imports, and dependency health were not checked during this documentation update.

### Implementation Observed
- The live `apps/etl/` inventory contains the source abstraction (`CsvSource`, `ExcelSource`, `PostgresSource`, and the `SourceBatch` contract), ETL tests, and `PipelineRun` model/migration.
- No ETL transform module appears in the live ETL file inventory. Volume 4.3 remains the next intended ETL milestone, subject to the bootstrap verification gate and its pending business-rule decisions.
- Existing app implementations must be confirmed from current source before claiming their status; this snapshot is not a full application audit.

### Verification Performed for This Snapshot
- Read repository overview, existing project-state history, existing agent guidance/roadmap files, live ETL file inventory, and Git state.
- No tests, migrations, Django checks, package installs, or application runtime checks were performed as part of this documentation update.
- Overall Django/bootstrap health: **NOT VERIFIED** in this update.
- Test suite: **NOT VERIFIED** in this update.
- Docker runtime: **NOT VERIFIED** in this update.
- PostgreSQL and UCI end-to-end behavior: historical checkpoint claims remain below; not re-run in this update.

## Historical Checkpoint Log

The following checkpoint entries are preserved from prior project history. Their results describe those earlier sessions and are not current verification unless independently repeated and recorded above.

---

## Checkpoint: Pre-Volume 4 Audit (post–Volume 3)

**Repo:** 21 commits, working tree clean, restored-and-reverified from `DataXAi_Volume_03_Repo.zip` this session (sandbox does not persist across sessions — rebuild venv with `pip install -r requirements/dev.txt` after unzipping).

### Implemented (real models, migrations, passing tests)
| App | Models | What's tested |
|---|---|---|
| `accounts` | `User` (custom, `role` field) | token obtain/refresh, `/me/` |
| `core` | `TimeStampedModel` (abstract), `AuditLogEntry` | RBAC matrix (3 roles), exception handler, health check |
| `etl` | `PipelineRun` | model defaults, `SET_NULL` on user delete — **no pipeline logic yet** |
| `healing` | `QuarantineRecord` | `resolve()`, double-resolution guard |
| `warehouse` | `Customer`, `Product`, `OrderLine` | constraints (price ≥0, qty can be negative, unique customer id) |

DRF: JWT auth (SimpleJWT), RBAC permission classes (`core.permissions`), consistent error envelope (`core.exceptions.custom_exception_handler`), shared pagination class. Docker Compose: 6 services (`db`,`cache`,`web`,`worker`,`beat`,`nginx`), YAML-valid.

### Placeholder (app registered, empty `models.py`, no logic)
`clv`, `dashboard`, `detection`, `forecasting`, `nlp`, `validation`.

### Verification status
| Check | Status |
|---|---|
| `manage.py check` / `makemigrations --check` / `pytest` | ✅ 29/29 passing, re-run this session |
| `ruff` / `black` / `isort` | ✅ clean |
| `docker compose config` (YAML) | ✅ valid |
| `docker compose build` / `up` / in-container health check | ❌ **NOT VERIFIED** — no Docker daemon in this sandbox (same limitation noted since Volume 2). Needs running on a machine with Docker. |
| `requirements/ml.txt` (pandas, SQLAlchemy, GE, prophet, xgboost, btyd) | ❌ **NOT VERIFIED** — declared, never installed. First real test is Volume 4. |
| Real UCI Online Retail II data flowing through the pipeline | ❌ **NOT VERIFIED** — sandbox network allowlist blocks `archive.ics.uci.edu`; needs the file uploaded or fetched outside this sandbox. |

### Known open design decision (blocking Volume 4's source layer)
Proposal Table 4/M1 pairs SQLAlchemy with "CSV + PostgreSQL" sources. Two readings: SQLAlchemy hits the *same* app DB Django's ORM already owns (redundant), or it hits a *separate* upstream/source DB (matches Figure 1, which draws "PostgreSQL Source" as an External Source outside the pipeline core, and is the only reading where adding SQLAlchemy is justified at all). **Recommendation: separate source DB**, via a new `SOURCE_DATABASE_URL` setting — proceeding on this basis unless corrected.

### Technical debt / deferred
- `forecasting`/`clv` schemas intentionally undesigned until Volumes 8–9 (real field shapes depend on model output).
- No Postgres integration tests yet (all DB tests run against SQLite via `config.settings.test`) — Volume 4 needs at least one test against real Postgres for the source-DB read path.

### Next volume prerequisites (Volume 4 — ETL Engine)
1. Resolve the source-DB decision above (proceeding with recommendation absent objection).
2. Install + smoke-test `pandas`, `SQLAlchemy` for real.
3. A small CSV fixture resembling UCI Online Retail II's columns (generated in-repo, not the full dataset).

---

## Checkpoint: Volume 4, Milestone 4.1 (ETL Prerequisites)

**Status: 4.1 COMPLETE. 4.2 not started — awaiting approval per the controlled milestone process.**

### Real UCI Online Retail II dataset — inspected characteristics (not fabricated)
Source: `online_retail_II.xlsx` (43.5MB), sheets `Year 2009-2010` (525,461 rows) + `Year 2010-2011` (541,910 rows) = **1,067,371 rows combined**.

| Characteristic | Finding |
|---|---|
| Columns | `Invoice, StockCode, Description, Quantity, InvoiceDate, Price, Customer ID, Country` (8 cols) |
| Dtypes | Invoice=object, StockCode=object, Quantity=int64, InvoiceDate=datetime64[ns] (no tz), Price=float64, Customer ID=float64, Country=object |
| Missing values | Description 4,382 (0.41%); Customer ID 243,007 (22.77%); all else 0 |
| Duplicates | 34,335 fully-duplicate rows (3.22%) |
| Date range | 2009-12-01 07:45 → 2011-12-09 12:50 |
| Quantity | min=-80,995, max=80,995, mean=9.94; 22,950 negative (2.15%); 0 zero |
| Price | min=-53,594.36, max=38,970.00, mean=4.65; **5 negative** (all `StockCode "B"`/"Adjust bad debt"); 6,202 zero (0.58%) |
| Cancellations | 19,494 `Invoice` values start with `C` (1.83%); 19,493 have negative qty; **1 anomaly has positive qty** (`C496350`) |
| Non-cancellation negative qty | 3,457 rows — negative `Quantity` but NOT `C`-prefixed (manual stock adjustments, distinct from customer cancellations) |
| CustomerID | 5,942 distinct non-null customers |
| StockCode | 5,305 distinct values; 11 administrative/non-product codes mixed in (`POST`, `DOT`, `M`, `D`, `S`, `ADJUST`, `PADS`, `CRUK`, `B`, `GIFT`, + `BANK CHARGES`/`gift_0001_*`), ~5,587 rows (0.52%) total |
| Country | 43 distinct; United Kingdom = 981,330 rows (91.9%) |
| Memory (loaded) | ~249MB as a pandas DataFrame (object dtypes) |
| Read performance | 74.7s to read both sheets via `pandas.read_excel(engine="openpyxl")` on this sandbox — real, measured, not estimated |

**Design-relevant findings flagged for Volume 4.3 (transform/business rules) — not decided or implemented yet:**
- Administrative StockCodes (POST/D/M/etc.) are not real products; loading them as `Product` rows would pollute the catalog. Needs a decision in 4.3.
- The 5 negative-price "Adjust bad debt" rows and the 1 positive-qty cancellation anomaly are edge cases the transform layer must explicitly handle, not silently coerce.

### Files created
- `apps/etl/tests/fixtures/online_retail_ii_sample.csv` — 18 rows, extracted directly from the real dataset (not synthetic), covering every characteristic above. Companion `fixtures/README.md` documents each row.
- `data/online_retail_II.xlsx` — the real dataset, copied in locally, **gitignored, not committed** (confirmed via `git check-ignore`).

### Files modified
- `requirements/ml.txt` — added `openpyxl>=3.1,<4` (new dependency; justified: real dataset ships as `.xlsx`, `pandas.read_excel` needs it). `pandas`/`SQLAlchemy` were already declared (Volume 2); now actually installed and import-verified for the first time.
- `config/settings/base.py` — added `SOURCE_DATABASE_URL` (external/upstream source, SQLAlchemy-managed, separate from `DATABASE_URL`) and `ETL_DATA_DIR`.
- `.env.example`, `README.md` — documented the `DATABASE_URL` vs `SOURCE_DATABASE_URL` distinction and dataset placement.

### Dependencies
| Package | Status |
|---|---|
| `pandas==2.3.3` | INSTALLED, VERIFIED (import + version check passed) |
| `SQLAlchemy==2.0.51` | INSTALLED, VERIFIED |
| `openpyxl==3.1.5` | INSTALLED, VERIFIED — **new**, justified above |
| `psycopg[binary]==3.3.4` | already installed (Volume 2), re-confirmed |

### Verification
| Check | Result |
|---|---|
| `manage.py check` | LOCALLY VERIFIED — 0 issues |
| `manage.py makemigrations --check --dry-run` | LOCALLY VERIFIED — no changes detected |
| `pytest` | LOCALLY VERIFIED — 29/29 passing (unchanged from Volume 3 — 4.1 touched no application logic) |
| `ruff` / `black --check` / `isort --check-only` | LOCALLY VERIFIED — clean |
| `docker compose config` (YAML) | LOCALLY VERIFIED — valid |
| `docker compose build` / `up` | **NOT VERIFIED** — no Docker daemon in this sandbox, unchanged since Volume 2 |
| Real dataset → warehouse load | **NOT VERIFIED** — no loading code exists yet; that's 4.2–4.4 |
| `SOURCE_DATABASE_URL` live connection | **NOT VERIFIED** — no source DB stood up yet; deferred to 4.2 |
| UCI VERIFIED | **NO** — dataset was inspected (real, measured), but not yet run through any pipeline (pipeline doesn't exist yet) |

### Deferred (explicitly, not silently)
- Docker `source_db` service — belongs to 4.2 (when the PostgreSQL source class actually needs something to connect to), not 4.1 (pure configuration).
- Resolution of the administrative-StockCode / anomaly-row handling — belongs to 4.3 (business rules).
- All actual source/transform/load code — 4.2, 4.3, 4.4 respectively.

### Volume 4.2 prerequisites
1. Approval to proceed.
2. Source interface/protocol design (CSV + PostgreSQL implementations).
3. `source_db` docker-compose service (deferred from 4.1, needed here).

---

## Checkpoint: Volume 4, Milestone 4.2 (Source Abstraction)

**Status: 4.2 COMPLETE. 4.3 not started — awaiting approval.**

### What was built
`apps/etl/sources/{contract,csv_source,excel_source,postgres_source}.py` + `apps/etl/exceptions.py` (5 new exception classes, all subclassing `core.exceptions.DataXAiError` — no parallel error system). Canonical contract: every source is a context manager yielding `SourceBatch(data: DataFrame[CANONICAL_COLUMNS], ...)`. Transform (4.3) will consume this without knowing which source produced it — proven, not asserted, by `test_sources_contract.py`.

### Files created
`apps/etl/exceptions.py`; `apps/etl/sources/{__init__,contract,csv_source,excel_source,postgres_source}.py`; `apps/etl/tests/test_sources_{csv,excel,postgres,contract}.py`; `apps/etl/tests/fixtures/{online_retail_ii_sample.xlsx,malformed.csv,missing_columns.csv}`; `scripts/verify_excel_source_real_data.py`.

### Files modified
`docker-compose.yml` (+`source_db` service, +`source_postgres_data` volume — deferred from 4.1, needed now); `.github/workflows/ci.yml` (+`source_postgres` service container, seeds `raw_transactions`, sets `TEST_SOURCE_DATABASE_URL`).

### Real UCI dataset verification (UCI VERIFIED = YES, for ExcelSource)
Ran `scripts/verify_excel_source_real_data.py` against the actual `data/online_retail_II.xlsx` (not the fixture). **Run twice, in two different sandbox sessions** (state doesn't persist between sessions - see repo-restore note at top of this file) - reporting both, since they differ and hiding that would be dishonest:

| Metric | Session 1 | Session 2 (this session) |
|---|---|---|
| Rows extracted | 1,067,371 | **1,067,371** (identical - correctness is stable) |
| Batches | 22 | 22 |
| Elapsed | 182.4s | **258.7s** |
| Peak memory (tracemalloc) | 68.0 MB | **68.0 MB** (identical) |

Row counts and memory are exactly reproducible; wall-clock time varies with this sandbox's underlying CPU/IO conditions between sessions, not with the code. Treat elapsed time as "~3-4 minutes, varies," not a precise constant.

**Bug found and fixed this session:** running the script directly (`python scripts/verify_excel_source_real_data.py`) failed with `ModuleNotFoundError: No module named 'config'` — Python puts the *script's* directory (`scripts/`) on `sys.path`, not the repo root, so `config` and the `apps/` import trick were never reachable. Fixed by explicitly inserting the repo root and `apps/` onto `sys.path` at the top of the script, before `django.setup()`. This only affects this standalone script, run outside `manage.py`/`pytest` (both of which already handle this correctly) - no application code was affected, confirmed by the full regression suite still passing 50/50 after the fix.

**Honest trade-off discovered, not hidden:** this streaming approach is slower than Volume 4.1's naive `pandas.read_excel()` benchmark (~3-4 min vs 74.7s) but uses dramatically less memory (68MB vs ~249MB). The streaming `openpyxl.iter_rows()` approach was chosen deliberately for the 8GB-RAM target hardware in the proposal's hardware requirements (§5.1) — memory was prioritized over raw speed. If a future volume needs faster bulk loads on beefier hardware, `pandas.read_excel()` directly is the documented alternative, at the stated memory cost.

### Postgres verification
Real (not mocked) local Postgres 16.14 installed in this sandbox for testing; `raw_transactions` table seeded from the same 18 real fixture rows as CSV/Excel. All `PostgresSource` tests — including a genuine connection-failure case (wrong port) — run against this real server.
**LOCALLY VERIFIED** = yes (this sandbox's Postgres). **CI wiring added** (a `source_postgres` service container + seed step in `ci.yml`) but **actual GitHub Actions execution is NOT VERIFIED** — this sandbox cannot trigger a real CI run; needs confirming on an actual push.
One real hiccup during this session: the local Postgres server process stopped mid-session (data survived, `service postgresql start` brought it back) — noted here in case it recurs; not a code defect.

### Verification summary
| Check | Result |
|---|---|
| `manage.py check` / `makemigrations --check` | LOCALLY VERIFIED |
| `pytest` (full regression, Volumes 1–4.2) | LOCALLY VERIFIED — **50/50 passing** (29 pre-existing + 21 new; some Postgres tests skip cleanly, not falsely-pass, if no Postgres is reachable) |
| `ruff` / `black --check` / `isort --check-only` | LOCALLY VERIFIED — clean across `apps/`, `config/`, `scripts/` |
| `docker compose config` (YAML) | LOCALLY VERIFIED |
| `docker compose build` / `up` | **NOT VERIFIED** — no Docker daemon in this sandbox, unchanged since Volume 2 |
| CI workflow (`source_postgres` wiring) | **NOT VERIFIED** — YAML-valid, never actually run on GitHub |
| PostgresSource against real Postgres | **POSTGRES VERIFIED** (local sandbox instance) |
| ExcelSource against the real UCI file | **UCI VERIFIED** (see table above) |
| CsvSource against real-scale data | **NOT VERIFIED** at real scale — only the 18-row fixture + malformed/missing-column edge cases. Lower risk than Excel (pandas' native `chunksize` is well-established library behavior, not custom code), but not proven at 1M-row scale. Deferred as a nice-to-have, not a blocker. |

### Known limitation (documented, not hidden)
`PostgresSource` assumes the source table already matches `CANONICAL_COLUMNS`. Real arbitrary-schema upstream systems would need per-deployment column mapping — out of scope for this FYP, noted as future work.

### Dependencies
No new packages beyond Volume 4.1 (`pandas`, `SQLAlchemy`, `openpyxl`, `psycopg` — all already declared and installed). Unrelated finding while reinstalling `requirements/ml.txt` this session: `btyd>=0.2` does not resolve on PyPI (only an alpha `0.1a1` exists, Python-version-restricted). **Not a 4.2 blocker** — flagged here for Volume 9.

### Volume 4.3 prerequisites
1. Approval to proceed.
2. `CANONICAL_COLUMNS` (raw source names) → warehouse field names (`invoice_no`, `stock_code`, ...) alias map design.
3. Resolution of the administrative-StockCode (`POST`/`D`/`M`/etc.) and anomaly-row (negative price, positive-qty cancellation) handling, flagged in 4.1, still undecided.

---

## Historical Bootstrap Checkpoint — 2026-10-04

**Date:** 2026-10-04
**Repository:** `D:\DataXAi`

This section records the bootstrap/environment checkpoint reported for the checkout on 2026-10-04. It is preserved as historical evidence and is superseded by the latest dated checkpoint below.

## Git

- Alignment baseline reported: branch `main`, remote `origin` → `https://github.com/MianArslan09/DataXAi.git`, local `main` tracking `origin/main`, working tree clean after Git alignment.
- At the start of this documentation task, status showed existing context-file edits, `dataxaienv/`, and `docs/handbook/PROJECT_ROADMAP.md` as untracked. The final status check for this task lists `docs/PROJECT_STATE.md` as modified and `AGENTS.md`, `.github/copilot-instructions.md`, `docs/PROJECT_ROADMAP.md`, and `dataxaienv/` as untracked; `docs/handbook/PROJECT_ROADMAP.md` no longer appears in that final output. Do not discard these paths.

## Environment

- Environment: `D:\DataXAi\dataxaienv` (recreated; reported for this checkpoint).
- Python: `3.12.10`.
- pip: `26.2.1`.
- `requirements/dev.txt` installation: completed successfully (reported).

## Current Verification

- `python manage.py check`: **LOCALLY VERIFIED** — “System check identified no issues (0 silenced).”
- `python manage.py makemigrations --check --dry-run`: **NOT VERIFIED** in the current environment.
- Full current pytest run after environment recreation: **NOT VERIFIED**.
- Current re-verification is in progress; do not infer any additional test counts or results.
- Volume 4.2 remains historical checkpoint evidence; re-verify it against the live checkout as needed.
- Volume 4.3 implementation has **NOT started**.

---

# Current Live Checkpoint — 2026-10-11: Volume 4.3.1

This checkpoint records the live repository and verification evidence reviewed on 2026-10-11. Older dated snapshots and milestone history above are retained as historical records and are not current status.

## Repository baseline

- Repository: `D:\DataXAi`; branch: `main`.
- HEAD: `136c6db` (`feat(etl): implement transform layer and alias mapping`), also at `origin/main` during this audit.
- Preceding source-database host-port change: `d90dc51` (`fix(docker): expose source database for local ETL tests`).
- Existing local changes were preserved: modified `.gitignore` and `docs/PROJECT_STATE.md`; untracked `.github/copilot-instructions.md`, `AGENTS.md`, and `docs/PROJECT_ROADMAP.md`.
- The untracked `DataXAi Volume Tracker.xlsx` was found during this audit and updated in place; its existing sheet, table, formatting, and data-validation structure were preserved. The workbook contains no formulas.
- No application source, test, dependency, Docker, environment, or database files were changed during this documentation reconciliation.

## Volume 4.3.1 implementation

The following files are present in Git from commit `136c6db`:

- `apps/etl/transforms/__init__.py`
- `apps/etl/transforms/contract.py`
- `apps/etl/transforms/alias_mapper.py`
- `apps/etl/tests/test_transforms_contract.py`
- `apps/etl/tests/test_transforms_alias.py`

The data flow is:

`CSV / Excel / PostgreSQL -> SourceBatch -> AliasMapper -> TransformedBatch`

`SourceBatch` carries the source's raw canonical columns and extraction metadata. `AliasMapper` selects the eight required raw columns in `CANONICAL_COLUMNS` order, renames them using the immutable `ALIAS_MAP`, drops unexpected extra columns, and returns a `TransformedBatch` carrying the source name, batch number, and extraction timestamp. Missing required raw columns raise the existing `MissingColumnsError`; the mapper rechecks them because a batch's DataFrame can be mutated after construction.

Tests verify that mapping preserves row order, index, values, dtypes, row count, and batch metadata and does not mutate the input DataFrame. The mapper's output has exactly the target columns in deterministic order. `TransformedBatch` itself enforces the presence of every target column while allowing a superset for later stages to add derived fields.

Volume 4.3.1 is structural mapping only. Type coercion, date parsing/normalization, data validation, filtering, healing, and loading are separate responsibilities and are not implemented by this substep. The fixture tests also verify that this mapping does not remove or decide the status of duplicate rows, administrative stock codes, negative prices, null values, or cancellation anomalies.

## Latest local verification evidence

The ETL test runs used the existing Windows `dataxaienv` and a process-local `TEST_SOURCE_DATABASE_URL` pointing to `postgresql+psycopg://dataxai_source:dataxai_source@localhost:5433/dataxai_source`, with `PGCONNECT_TIMEOUT=3`. These overrides were not persisted as environment changes.

| Check | Result |
|---|---|
| Focused PostgreSQL/source/transform suite | **28 passed**; included real PostgresSource tests and PostgreSQL-to-transform comparison |
| Full `pytest -q -rs` suite | **89 passed, 0 skipped** |
| `ruff check apps config` | Passed |
| `black --check apps config` | Passed; 109 files unchanged |
| `isort --check-only apps config` | Exit code 0; 11 files skipped by isort configuration |
| `python manage.py check` | Passed; no system-check issues |
| `docker compose config --quiet` | Passed |
| `python manage.py makemigrations --check --dry-run` | Reported no model changes, but emitted a warning because host-side application-database authentication failed; migration-history consistency is **NOT VERIFIED** |

The source PostgreSQL service was healthy on host port `5433`. Its `dataxai_source` database contained `raw_transactions` with 18 fixture rows and the expected eight source columns. The PostgreSQL tests were therefore run against the source database, not the application database.

## Historical Application Database Caveat at the 4.3.1 Checkpoint

This records the state at the time of the Volume 4.3.1 checkpoint. The 2026-10-11 database-recovery follow-up below supersedes this caveat for the current local environment.

The separate application PostgreSQL service is `db` on host port `5432`. Its `dataxai` database and `dataxai` role exist inside the container, but the application database had no `django_migrations` table when inspected. A Windows host connection to `localhost:5432` reached PostgreSQL but failed password authentication for `dataxai`. A host-side Django migration-history check therefore did not complete. The successful `manage.py check` and “No changes detected” model-drift result did not establish application database migration state or production readiness at that earlier checkpoint.

## Milestone status and handoff

- **4.3.1 — Implemented and locally verified; awaiting project-owner review and acceptance.** Passing tests are implementation evidence, not owner acceptance.
- **4.3.1a — Planned, not started.** Scope: duplicate canonical-column protection and dedicated tests.
- **4.3.2 — Planned, not started.** Scope: type coercion. Do not begin until 4.3.1 and 4.3.1a are integrated, locally verified, reviewed, and explicitly accepted.
- Later transformation steps, ETL loading, PipelineRun orchestration, and the full Volume 4 end-to-end checkpoint remain planned. Volume 4 is not complete.
- Full UCI-dataset transformation and application-database migration-history verification were not performed in this checkpoint.

## Development diary: transform-layer checkpoint

The transform layer is needed to separate source-specific extraction from downstream processing. CSV, Excel, and PostgreSQL adapters produce the same `SourceBatch` contract, so downstream code can consume a stable representation without knowing the source implementation. `TransformedBatch` is the next contract: it retains lineage metadata while exposing warehouse-oriented field names.

For 4.3.1, the approved design is a deterministic alias map from the eight raw UCI names to warehouse field names. The mapper selects the canonical inputs in their defined order, discards unrelated extra columns, rejects missing required fields with the existing ETL exception, and changes names only. The tests demonstrate source-independent behavior across CSV, Excel, and the reachable PostgreSQL fixture, along with preservation of rows, values, dtypes, index, and metadata and no mutation of the input frame.

Type conversion, date normalization, validation, business-rule resolution, healing, and database loading are outside this substep. In particular, negative quantity remains valid business data; negative unit price is a fault for the appropriate later stage. Tests demonstrate software behavior on the checked fixtures; they are not research-paper evidence or thesis conclusions.

The source-database debugging lesson was to distinguish the application database (`db`, port 5432) from the ETL source database (`source_db`, port 5433), and to set a host-reachable test URL when running pytest from Windows. The source integration tests now pass. At this checkpoint the application database still had an authentication/migration-history caveat; the later dated follow-up records its recovery for the current local environment. The next gate is project-owner review and acceptance of 4.3.1; only after that should 4.3.1a be considered. Keep 4.3.2 blocked until both preceding substeps pass review and local verification.

---

## Follow-up Checkpoint — 2026-10-11: Application Database Recovery

This dated follow-up records the successful recovery of the local application PostgreSQL database after the authentication and missing-migration-history issue documented in the preceding checkpoint. It supersedes that earlier caveat for the current local environment only; the earlier observation remains preserved as historical evidence.

**Status: LOCALLY VERIFIED** for local application database bootstrap and migration state only.

The relevant existing milestone is **Volume 3 — Database Design, Authentication, Authorization & Core Backend Infrastructure**. The Volume 3 handbook describes creating and migrating the application's database schema. Volume 2 provides the environment and Docker database service, but this recovery concerns application schema migrations and therefore is recorded under Volume 3. It is separate from ETL Volume 4.3.1.

### Verified recovery evidence supplied for this checkpoint

- The application PostgreSQL Compose service `db` was healthy on host port `5432`.
- The target database and role were both `dataxai`.
- Before migration, the `public` schema contained zero tables.
- A read-only migration plan listed the initial Django migrations.
- `python manage.py migrate` completed successfully; every migration in the reported plan ended in `OK`.
- `python manage.py showmigrations` showed all listed migrations as applied (`[X]`).
- `python manage.py check` reported no system-check issues.
- A final PostgreSQL query reported 22 tables in the `public` schema.
- Effective Django database settings used database `dataxai`, user `dataxai`, host `localhost`, and port `5432`.

The prior host-side password-authentication failure and absence of `django_migrations` were true observations at the earlier checkpoint, before recovery. The migration-history caveat is now superseded for this local database by the successful migration and `showmigrations` evidence above.

This evidence verifies local database bootstrap and migration state only. It does not establish successful end-to-end application workflows, production readiness, or resolution of unrelated issues. As of October 11, 2026, Volume 4.3.1 remains implemented and locally verified, awaiting project-owner review and acceptance; 4.3.1a and 4.3.2 remain planned and not started.

---

## Current Milestone Status — 2026-10-16

This update supersedes the 2026-10-11 milestone status above while preserving that dated snapshot as history.

- **4.3.1 — Alias mapping: IMPLEMENTED and LOCALLY VERIFIED.** Current-main implementation commit: `c406b19`. The repository records implementation and verification; formal owner acceptance is not established by the current documentation.
- **4.3.1a — Duplicate canonical-column protection: IMPLEMENTED, COMMITTED, and LOCALLY VERIFIED.** Current-main commit: `35f0e8d`, which adds `AmbiguousColumnsError`, rejects duplicate required source labels, and adds focused ambiguity tests. Formal owner acceptance is not separately recorded in the current documentation.
- **4.3.2 — Type coercion: NEXT PLANNED MILESTONE; NOT IMPLEMENTED.** Preserve the existing prerequisite gate requiring 4.3.1 and 4.3.1a to be reviewed and explicitly accepted before starting.

This status update does not claim fresh test execution, end-to-end ETL completion, or production readiness.
