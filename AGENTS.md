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
- Volume 4.3.1 (transform contract and alias mapping) is implemented at current-main commit `c406b19`, locally verified, and owner accepted. Volume 4.3.1a (duplicate canonical-column protection) is implemented, committed at current-main commit `35f0e8d`, locally verified, and owner accepted. Volume 4.3.2 (Type Conversion) is IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-18. Volume 4.3.3 remains closed until the project owner explicitly authorizes it; acceptance of 4.3.2 does not authorize the next step. Do not silently continue into any later milestone.

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

Volume 4.3.2 (Type Conversion) is IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-18. Volume 4.3.1 and 4.3.1a remain owner accepted. See `docs/PROJECT_STATE.md` for the final acceptance record, verification evidence, and separate application `SOURCE_DATABASE_URL` authentication risk. Type conversion is complete; the full ETL pipeline remains incomplete. Volume 4.3.3 is closed until the project owner explicitly authorizes it.
