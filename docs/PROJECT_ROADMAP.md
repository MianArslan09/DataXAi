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
- Volume 4.4.1 is owner accepted as recorded below; Volume 4.4.2 and all later subphases/milestones remain closed until explicit owner authorization
- any business rules beyond approved cancellation enrichment require separate owner authorization
- downstream treatment of unknown source timezones and nullable cancellation markers must be explicitly designed before loading

Date interpretation and cancellation enrichment do not complete the full ETL pipeline. Acceptance of 4.4.1 below does not authorize Volume 4.3.4, Volume 4.4.2, or later work.

Volume 4.3 must not implement Great Expectations validation, the Pydantic Validation Engine, auto-healing, anomaly detection, forecasting, CLV, or NLP/chatbot.

Volume 4.3.1 implements structural alias mapping, Volume 4.3.2 implements strict type conversion, and Volume 4.3.3 adds date interpretation and cancellation enrichment by composition. The full Volume 4 ETL pipeline remains incomplete; end-to-end loading has not been verified.

Historical configuration risk at the 4.3 acceptance checkpoint: the application's `SOURCE_DATABASE_URL` credentials failed authentication, while source-test PostgreSQL verification used a separate connection. This open-risk statement is superseded for the verified local configuration by the 2026-10-22 stabilization evidence in `docs/PROJECT_STATE.md`; configuration PR #2 is merged into main (`c8943b0`). Docker application-container startup remains unverified by that evidence. No database connection was retested for this documentation reconciliation.

Business rules:
- negative quantity is valid
- negative unit price is a fault

These business rules constrain later ETL and validation behavior; they are not decisions applied by the 4.3.1 alias mapper.

## Volume 4.4 Subphase Status

**4.4.1 - SourceBatch Serialization Contract (SER-v1): IMPLEMENTED, TESTED, LOCALLY VERIFIED according to recorded evidence, and FORMALLY OWNER ACCEPTED.** Implementation commit: `ab4567a`, branch `feat/etl-4.4.1-serialization`, [PR #3](https://github.com/MianArslan09/DataXAi/pull/3) (open according to the owner at reconciliation). The acceptance record is dated 2026-10-24; the exact earlier acceptance-decision date was not supplied.

Recorded evidence: owner-reported local 237 focused tests passed, Ruff/Black/isort and staged whitespace checks passed; owner-reported [GitHub Actions Run #17](https://github.com/MianArslan09/DataXAi/actions/runs/40003492128) succeeded with lint, 700 tests passed, 99% reported coverage, and Docker build. These are prior execution results, not fresh tests performed during documentation reconciliation. The coverage figure is the run's reported aggregate, not a serializer-specific guarantee. See the appended checkpoint in `docs/PROJECT_STATE.md` for the public API, supported fidelity, prior fixture verification, and limitations.

SER-v1 preserves supported SourceBatch representations through encoding/decoding and canonical content fingerprinting. It does not implement PostgreSQL staging, warehouse loading, transformation, validation, or end-to-end pipeline execution. Volume 4.4 Load Layer and the full ETL pipeline remain incomplete. Unknown source timezones and nullable cancellation markers still require an approved downstream loading policy.

**4.4.2 and all later subphases/milestones remain closed until explicit owner authorization.** This acceptance supersedes older blanket 4.4-closed statements only for 4.4.1. It does not authorize another implementation step or imply that PR #3 has been merged.

## Status Rule

Roadmap scope and completion labels are not proof of implementation or verification. Determine implementation status from the live code, tests, migrations, Git state, and current verification recorded in `docs/PROJECT_STATE.md`.

## Volume 3 Application Database Verification

**Volume 3 — Database, Auth & Core Infrastructure: LOCALLY VERIFIED** for local application database bootstrap and migration state (2026-10-11). This milestone owns the application schema and its migrations. The Docker/environment setup is Volume 2; this recovery is distinct from ETL Volume 4.3.1. See `docs/PROJECT_STATE.md` for the supplied migration, `showmigrations`, system-check, effective-settings, and 22-table evidence. This local verification does not claim end-to-end application behavior or production readiness.

## Latest Volume 4 Gate — 2026-10-27

<!-- DATA X AI CHECKPOINT 2026-10-27 V4.4.2 OWNER ACCEPTED -->

This checkpoint supersedes earlier current-gate statements above while preserving their historical evidence.

- **4.4.1 — SER-v1:** Remains formally owner accepted. The supplied handoff reports PR #3 merged; this update has not independently verified its remote status.
- **4.4.2 — Staging Database Schema:** IMPLEMENTED, TESTED, LOCALLY VERIFIED, POSTGRES VERIFIED according to recorded prior evidence, and FORMALLY OWNER ACCEPTED on 2026-10-27. Accepted scope is five models, reversible migration, same-batch PostgreSQL composite constraints and lossless escaped JSON-text storage of authoritative SER-v1 data, including `U+0000`. Prior results: 110 focused and 810 full-suite tests passed with zero failures/skips, exit 0; system, migration-drift and quality checks passed. These checks were not rerun for documentation.
- **Git integration:** At this checkpoint the four scoped implementation/test files are uncommitted on `feat/etl-4.4.2-staging-schema` at baseline HEAD `c39da8d`. Owner plans PR and merge after documentation review. No 4.4.2 CI or merge success is claimed.
- **4.4.3 — Raw Staging Persistence:** The handoff supplies proposed design topics for a later owner-directed task: atomic batch persistence, explicit ingestion identity, positional reconciliation, bounded bulk insertion and idempotency. This update does not start design or authorize implementation. Obtain explicit authorization and review the detailed contract before implementing.
- **Remaining Volume 4:** Loader/attempt services, PipelineRun integration, error handling and end-to-end verification remain incomplete. Unknown timezone and nullable cancellation handling still require approved downstream policies. Operational/derived JSONB restrictions and full SER-v1/accounting checks remain relevant to later persistence design.

See the 2026-10-27 acceptance checkpoint in `PROJECT_STATE.md` for evidence, storage guarantees and limitations. Acceptance of the schema does not complete the Load Layer or the full ETL pipeline.

## Latest Volume 4 Gate — 2026-10-30

<!-- DATA X AI CHECKPOINT 2026-10-30 V4.4.3 OWNER ACCEPTED -->

This current checkpoint supersedes earlier gate statements, including the historical 4.4.3-not-authorized statement, without rewriting those records.

- **4.4.1 — SER-v1:** Remains formally owner accepted.
- **4.4.2 — Staging Database Schema:** Remains formally owner accepted on 2026-10-27. Verified historical Git integration: PR #4 merged on 2026-10-27, merge commit `f61fc7f`.
- **4.4.3 — Atomic Raw Staging Persistence:** IMPLEMENTED, TESTED, LOCALLY VERIFIED, POSTGRES VERIFIED according to recorded evidence, and FORMALLY OWNER ACCEPTED on 2026-10-30. Accepted scope includes atomic header/record persistence, bounded bulk insertion, ingestion-scoped identity and sequencing, verified idempotent replay, conflict rejection, PostgreSQL concurrency and rollback checks, and final contract hardening.
- **Evidence:** Codex-reported initial 21 focused PostgreSQL tests, 110 staging regression tests and 831 full-suite tests passed; full suite had zero failures/skips, exit 0. Final hardening: 23 PostgreSQL tests passed, zero failures/skips, 12.58 seconds, exit 0; quality/whitespace checks passed. The full suite was not rerun after hardening. Independent source review preceded the two final tests. No tests were rerun for this documentation update.
- **Current Git boundary:** 4.4.3 implementation remains uncommitted on `feat/etl-4.4.3-raw-staging` at `f61fc7f`. Owner acceptance does not claim a 4.4.3 commit, push, PR, CI result or merge.
- **4.4.4 — Next proposed design gate:** Not implemented or automatically authorized. Obtain explicit owner authorization before starting; later milestones remain gated.

Retries must reuse the original ingestion UUID and batch number. Equal content under another valid sequential batch number remains distinct. Outer transactions govern ultimate durability; persistence retry safety does not guarantee deterministic PostgreSQL re-extraction or cross-session checkpoint recovery. See the 2026-10-30 `PROJECT_STATE.md` checkpoint for the accepted limitations and evidence provenance.

Volume 4.4 and end-to-end ETL remain incomplete. Transformation-attempt execution, ingestion finalization, replay orchestration, PipelineRun integration and warehouse publication remain future work. Unknown source timezones and nullable cancellation markers still require approved loading policies; no million-row staging throughput or full-pipeline verification is claimed.

## Latest Volume 4 Gate — 2026-10-31

<!-- DATA X AI CHECKPOINT 2026-10-31 V4.4.4 OWNER ACCEPTED -->

This checkpoint supersedes earlier 4.4.4-proposed/unauthorized statements while preserving them as historical records.

- **4.4.1 — SER-v1:** Formally accepted and merged through PR #3 (`af24048`, 2026-10-24).
- **4.4.2 — Staging Database Schema:** Formally accepted and merged through PR #4 (`f61fc7f`, 2026-10-27).
- **4.4.3 — Atomic Raw Staging Persistence:** Formally accepted and merged through PR #5 on 2026-10-30 (`52da230`). These integration records are confirmed by local Git history.
- **4.4.4 — SourceBatch Reconstruction and Replay:** IMPLEMENTED, TESTED, LOCALLY VERIFIED, POSTGRES VERIFIED according to recorded evidence, INDEPENDENTLY REVIEWED, and FORMALLY OWNER ACCEPTED on 2026-10-31. The read-only Python service reconstructs one staged batch with metadata, position/count, SER-v1 and fingerprint checks; it is not an HTTP endpoint. Its implementation remains uncommitted on `feat/etl-4.4.4-reconstruction` at `52da230`; no push, PR, merge or GitHub CI success is claimed.
- **4.4.5 — Next architecture/design gate:** Not implemented, not tested and not formally accepted. Explicit owner authorization is required before starting; later milestones remain gated.

See the 2026-10-31 `PROJECT_STATE.md` checkpoint for Codex-reported results (29 reader, 23 persistence, 110 schema and 862 full-suite tests passed), independent source-review provenance, initial sandbox limitations and accepted architectural boundaries. No tests were rerun for this documentation update.

Reconstruction materializes one batch, assumes immutable committed records, and retains the existing fingerprint exclusions. Full Volume 4.4/end-to-end ETL remain incomplete. Downstream transformation execution, recovery/orchestration, ingestion finalization and warehouse publication remain outside 4.4.4; real-dataset reconstruction throughput is not verified. Unknown timezone and nullable cancellation loading policies remain pending.
