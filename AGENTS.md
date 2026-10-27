# DataXAi Agent Instructions

## Project

DataXAi — Auto-Heal ETL Pipelines & Predictive Analytics Platform for Pakistani E-Commerce SMEs.

Repository: `D:\DataXAi`

## Source of Truth

Use this priority whenever sources disagree:
1. Live code, tests, migrations, and actual Git state.
2. `docs/PROJECT_STATE.md`.
3. `docs/handbook/volume-01-foundations.md`.
4. The live Volume 2 handbook file, currently `docs/handbook/volume-02-environment-setup.md`.
5. `docs/handbook/volume-03-database-auth-core.md`.
6. `docs/PROJECT_ROADMAP.md` for future intended work.

The requested path `volume-02-environment-repository.md` is absent from the live checkout. Never treat historical documentation or a roadmap's “Done” label as proof of current verification.

## Milestone-Gated Development

- Inspect the live implementation, tests, migrations, settings, documentation, and CI before changing anything.
- Implement one approved sub-step at a time, verify that sub-step for real, report the result, and stop for approval.
- Never silently continue into another milestone or implement a later volume early.
- Volume 4.3.1 (transform contract and alias mapping) is implemented at current-main commit `c406b19`, locally verified, and owner accepted. Volume 4.3.1a (duplicate canonical-column protection) is implemented, committed at current-main commit `35f0e8d`, locally verified, and owner accepted. Volume 4.3.2 (Type Conversion) is IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-18. Volume 4.3.3 (Dates and Business Rules, DT-001 through DT-007 and BR-001 through BR-004) is IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-21; implementation commit `6df1e1b`. Volume 4.4.1 (SourceBatch Serialization Contract, SER-v1) is IMPLEMENTED, TESTED, LOCALLY VERIFIED according to recorded evidence, and FORMALLY OWNER ACCEPTED; implementation commit `ab4567a`, PR #3. Acceptance is recorded in the 2026-10-24 checkpoint in `docs/PROJECT_STATE.md`; the exact earlier decision date was not supplied. Volume 4.4.2 and all later subphases/milestones remain closed until explicit owner authorization.

## Implementation Rules

- Existing implementations are the baseline. Modify the existing architecture in place; never create parallel implementations.
- Keep changes narrow. No uncontrolled broad refactors or unrelated module changes.
- No TODO/pseudocode placeholders in implemented work; deliver complete files.
- Preserve project naming and architecture. Leave placeholder apps untouched until their designated volume.

## Git Rules

- Never delete or recreate `.git` or the repository.
- Never run destructive Git operations, rewrite history, or reset/discard user changes.
- Never stage automatically; when staging is explicitly authorized, stage only the named paths.
- Never commit or push automatically. Review `git diff` before suggesting a commit.

## Verification Honesty

Use only these status labels when appropriate: `IMPLEMENTED`, `TESTED`, `LOCALLY VERIFIED`, `DOCKER VERIFIED`, `POSTGRES VERIFIED`, `UCI VERIFIED`, `NOT VERIFIED`, and `DEFERRED`.

Never claim a test, migration, Django check, Docker run, PostgreSQL run, UCI run, or other verification occurred unless it actually occurred against the current repository state. Keep current verification separate from historical evidence in `docs/PROJECT_STATE.md` and preserve historical checkpoints.

## DataXAi Architecture Boundaries

Preserve Django 5.2 LTS + DRF; one Django app per module under `apps/`; JWT via SimpleJWT; Admin as the RBAC permission superset; PostgreSQL warehouse; separate `SOURCE_DATABASE_URL`; and the CSV/Excel/PostgreSQL source abstraction via `SourceBatch`.

Keep Great Expectations dataset-level validation, Pydantic row-level validation, deterministic auto-healing, quarantine/DLQ, Prophet forecasting, `btyd` + XGBoost CLV, Groq narration/chatbot, Django templates + Bootstrap 5 + HTMX, Plotly.js + ECharts.js, Celery + Redis, and Docker Compose within their designated module boundaries.

## Critical Business Rules

- Negative `OrderLine.quantity` is valid business data because returns/cancellations are legitimate.
- Negative `OrderLine.unit_price` is a data-quality fault.
- ETL transformation is not the Validation Engine.
- Do not move validation, healing, anomaly detection, forecasting, CLV, or NLP logic into the ETL transform layer.

## Current Development Gate

Volume 4.3.2 (Type Conversion) is IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-18. Volume 4.3.1 and 4.3.1a remain owner accepted. See `docs/PROJECT_STATE.md` for the final acceptance record, verification evidence, and historical application `SOURCE_DATABASE_URL` authentication risk and its recorded 2026-10-22 local configuration resolution (PR #2 is merged). Volume 4.3.3 (Dates and Business Rules, DT-001 through DT-007 and BR-001 through BR-004) is IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-21; implementation commit `6df1e1b`. See the dated acceptance record in `docs/PROJECT_STATE.md` for previously recorded verification evidence. The full ETL pipeline remains incomplete. Unknown source timezones and nullable cancellation markers still require an approved loading policy. Volume 4.4.1 (SourceBatch Serialization Contract, SER-v1) is IMPLEMENTED, TESTED, LOCALLY VERIFIED according to recorded evidence, and FORMALLY OWNER ACCEPTED; implementation commit `ab4567a`, PR #3. Acceptance is recorded in the 2026-10-24 checkpoint in `docs/PROJECT_STATE.md`; the exact earlier decision date was not supplied. Volume 4.4.2 and all later subphases/milestones remain closed until explicit owner authorization.

## Latest Owner-Accepted Gate — 2026-10-27

<!-- DATA X AI CHECKPOINT 2026-10-27 V4.4.2 OWNER ACCEPTED -->

Volume 4.4.2 (Staging Database Schema) is IMPLEMENTED, TESTED, LOCALLY VERIFIED, POSTGRES VERIFIED according to recorded prior evidence, and FORMALLY OWNER ACCEPTED on 2026-10-27. Its five models, reversible migration, PostgreSQL composite batch-membership constraints and escaped JSON-text SER-v1 storage preserve supported `U+0000` values. Prior verification: 110 focused and 810 full-suite tests passed, zero failures/skips; see `docs/PROJECT_STATE.md`. No tests were rerun for this documentation update.

This checkpoint supersedes earlier blanket 4.4.2-closed statements. At acceptance reconciliation the implementation is uncommitted on `feat/etl-4.4.2-staging-schema`, baseline HEAD `c39da8d`; inspect live Git before claiming commit, PR, CI or merge. The owner intends to perform PR/merge after documentation review.

The supplied 4.4.3 raw staging persistence handoff is proposed design reference material. This documentation request does not authorize starting that task or implementing it. Proceed only on explicit owner instruction; later implementations require an approved scope and acceptance criteria. Preserve SER-v1, existing staging and PipelineRun behavior, positional lineage and source data. Full Volume 4.4 and end-to-end ETL remain incomplete.
