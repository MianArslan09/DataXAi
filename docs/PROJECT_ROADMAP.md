# DataXAi — Project Roadmap

## Detailed Documentation

- Volume 1: [`docs/handbook/volume-01-foundations.md`](handbook/volume-01-foundations.md)
- Volume 2: [`docs/handbook/volume-02-environment-setup.md`](handbook/volume-02-environment-setup.md)
- Volume 3: [`docs/handbook/volume-03-database-auth-core.md`](handbook/volume-03-database-auth-core.md)
- Volume 4 checkpoints and current implementation state: [`docs/PROJECT_STATE.md`](PROJECT_STATE.md)

The supplied filename `volume-02-environment-repository.md` is not present in the live repository; the existing Volume 2 handbook is named `volume-02-environment-setup.md`.

## 18-Volume Roadmap

1. Foundations
2. Environment & Repository
3. Database, Auth & Core Infrastructure
4. ETL Engine
   - 4.1 ETL Prerequisites
   - 4.2 ETL Source Abstraction
   - 4.3 ETL Transform Layer
   - 4.4 ETL Load Layer
   - 4.5 PipelineRun Integration
   - 4.6 ETL Error Handling
   - 4.7–4.8 ETL Testing
   - 4.9 ETL Verification Checkpoint
5. Data Validation Engine
6. Auto-Healing Engine
7. Anomaly Detection
8. Forecasting
9. CLV
10. REST APIs
11. Dashboard
12. NLP Chatbot
13. Testing Hardening
14. Deployment
15. Production Hardening
16. Research Evaluation
17. Documentation
18. Final Repository Review

## Volume 4.3 Submilestone Status

**4.3.1 — Transform contract and alias mapping: IMPLEMENTED, LOCALLY VERIFIED, and OWNER ACCEPTED.** Current-main commit: `c406b19`.
- **4.3.1a — Duplicate canonical-column protection: IMPLEMENTED, COMMITTED, LOCALLY VERIFIED, and OWNER ACCEPTED.** Current-main commit: `35f0e8d`.
- **4.3.2 — Type Conversion: IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-18.** See the final dated acceptance record and verification evidence in `docs/PROJECT_STATE.md`.

**4.3.3 — Dates and Business Rules: IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-21.** Acceptance covers DT-001 through DT-007 and BR-001 through BR-004. Implementation commit: `6df1e1b` on `feat/etl-4.3.3`. Date interpretation now composes with the accepted converter and cancellation component, preserving safe/unresolved paths and explicit unknown-timezone evidence. Previously recorded verification evidence (not rerun for this documentation reconciliation): 342 focused tests passed; full suite 437 passed with zero skips; nine PostgreSQL integration tests passed; Ruff, Black, and isort passed across 124 repository Python files. See the 2026-10-21 formal acceptance record and preserved 2026-10-20 checkpoint in `docs/PROJECT_STATE.md` for verification evidence and limitations, and [`DATE_NORMALIZATION.md`](DATE_NORMALIZATION.md) for the contract.

Remaining gates:
- Volume 4.4 and all later milestones require explicit owner authorization
- any business rules beyond approved cancellation enrichment require separate owner authorization
- downstream treatment of unknown source timezones and nullable cancellation markers must be explicitly designed before loading

Date interpretation and cancellation enrichment do not complete the full ETL pipeline. No authorization is implied for Volume 4.3.4, Volume 4.4, or later work.

Volume 4.3 must not implement Great Expectations validation, the Pydantic Validation Engine, auto-healing, anomaly detection, forecasting, CLV, or NLP/chatbot.

Volume 4.3.1 implements structural alias mapping, Volume 4.3.2 implements strict type conversion, and Volume 4.3.3 adds date interpretation and cancellation enrichment by composition. The full Volume 4 ETL pipeline remains incomplete; end-to-end loading has not been verified.

Separate configuration risk: the application's existing `SOURCE_DATABASE_URL` credentials failed authentication. PostgreSQL verification used the existing CI `TEST_SOURCE_DATABASE_URL` on `localhost:5433`; it does not establish that the application-configured connection works. This risk remains open and does not change the owner's acceptance of the type-conversion milestone.

Business rules:
- negative quantity is valid
- negative unit price is a fault

These business rules constrain later ETL and validation behavior; they are not decisions applied by the 4.3.1 alias mapper.

## Status Rule

Roadmap scope and completion labels are not proof of implementation or verification. Determine implementation status from the live code, tests, migrations, Git state, and current verification recorded in `docs/PROJECT_STATE.md`.

## Volume 3 Application Database Verification

**Volume 3 — Database, Auth & Core Infrastructure: LOCALLY VERIFIED** for local application database bootstrap and migration state (2026-10-11). This milestone owns the application schema and its migrations. The Docker/environment setup is Volume 2; this recovery is distinct from ETL Volume 4.3.1. See `docs/PROJECT_STATE.md` for the supplied migration, `showmigrations`, system-check, effective-settings, and 22-table evidence. This local verification does not claim end-to-end application behavior or production readiness.
