# DataXAi Copilot Instructions

Before changing code, read:
- `AGENTS.md`
- `docs/PROJECT_STATE.md`
- `docs/handbook/volume-01-foundations.md`
- `docs/handbook/volume-02-environment-setup.md` (the requested `volume-02-environment-repository.md` is absent)
- `docs/handbook/volume-03-database-auth-core.md`
- `docs/PROJECT_ROADMAP.md`

Rules:
- Live repository state is authoritative. Inspect existing implementation and tests before editing.
- Work one gated sub-step at a time. Verify it, report exact status, and stop. Do not assume historical “Done” claims are currently verified or silently enter a later milestone.
- Volume 4.3.1 alias mapping is implemented and locally verified at current-main commit `c406b19`. Owner accepted. Volume 4.3.1a duplicate canonical-column protection is implemented, committed at current-main commit `35f0e8d`, and locally verified. Owner accepted. Volume 4.3.2 (Type Conversion) is IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-18; see `docs/PROJECT_STATE.md` for the final record and historical application `SOURCE_DATABASE_URL` authentication risk and its recorded 2026-10-22 local configuration resolution (PR #2 is merged). Volume 4.3.3 (Dates and Business Rules, DT-001 through DT-007 and BR-001 through BR-004) is IMPLEMENTED, LOCALLY VERIFIED, and FORMALLY OWNER ACCEPTED on 2026-10-21; implementation commit `6df1e1b`. See the dated acceptance record in `docs/PROJECT_STATE.md` for previously recorded verification evidence. The full ETL pipeline remains incomplete. Unknown source timezones and nullable cancellation markers still require an approved loading policy. Volume 4.4.1 (SourceBatch Serialization Contract, SER-v1) is IMPLEMENTED, TESTED, LOCALLY VERIFIED according to recorded evidence, and FORMALLY OWNER ACCEPTED; implementation commit `ab4567a`, PR #3. Acceptance is recorded in the 2026-10-24 checkpoint in `docs/PROJECT_STATE.md`; the exact earlier decision date was not supplied. Volume 4.4.2 and all later subphases/milestones remain closed until explicit owner authorization.
- Do not create parallel implementations or modify unrelated modules. Preserve user changes; do not run destructive Git operations or rewrite history.
- Never stage automatically; when staging is explicitly authorized, stage only the named paths. Never commit or push automatically.
- Never create another virtual environment unless explicitly required. Do not install packages or modify requirements.
- Do not claim verification that was not performed against the current checkout.

For every implementation task, explicitly state:
1. What already exists.
2. What is missing.
3. What will be changed.
4. How it will be verified.

## Latest Owner-Accepted Gate — 2026-10-27

<!-- DATA X AI CHECKPOINT 2026-10-27 V4.4.2 OWNER ACCEPTED -->

Volume 4.4.2 staging schema is IMPLEMENTED, TESTED, LOCALLY VERIFIED, POSTGRES VERIFIED according to recorded prior evidence, and FORMALLY OWNER ACCEPTED on 2026-10-27. Prior results: 110 focused and 810 full-suite tests passed with zero failures/skips. See the latest `docs/PROJECT_STATE.md` checkpoint for the reversible escaped JSON-text storage correction, PostgreSQL constraints, verification provenance and limitations. Tests were not rerun for this documentation update.

This record supersedes earlier blanket 4.4.2-closed statements, consistently with `AGENTS.md`. Commit/PR/CI/merge status must be inspected live; owner acceptance is not proof of Git integration. The provided 4.4.3 handoff contains proposed design material, not authorization from this documentation request to start design or implementation. Wait for explicit owner instructions and an approved implementation contract. Do not advance later milestones automatically or claim completion of the Load Layer or full ETL pipeline.

## Latest Owner-Accepted Gate — 2026-10-30

<!-- DATA X AI CHECKPOINT 2026-10-30 V4.4.3 OWNER ACCEPTED -->

Volume 4.4.3 (Atomic Raw Staging Persistence) is IMPLEMENTED, TESTED, LOCALLY VERIFIED, POSTGRES VERIFIED according to recorded evidence, and FORMALLY OWNER ACCEPTED on 2026-10-30, including final hardening and documented limitations. Use the latest dated `PROJECT_STATE.md` checkpoint for detailed execution/review provenance, consistently with `AGENTS.md`; no tests were rerun for this documentation update.

This supersedes historical statements that 4.4.3 was unauthorized or merely proposed; retain those records. Live code/tests/migrations/Git remain authoritative for implementation. Volumes 4.4.1 and 4.4.2 remain accepted; local Git history verifies 4.4.2 PR #4 merged on 2026-10-27 at `f61fc7f`. Current 4.4.3 changes remain uncommitted on `feat/etl-4.4.3-raw-staging`; do not infer commit, PR, CI, push or merge from acceptance.

Preserve ingestion-scoped batch identity, verified replay, legitimate duplicates and accepted contracts. Fingerprints do not deduplicate independent batches; outer transactions govern durability, and retry safety does not guarantee deterministic re-extraction or checkpoint recovery.

**4.4.4 is only the next proposed design gate, not implemented or authorized.** Obtain explicit owner authorization before it or later milestones. The Load Layer/full ETL pipeline and downstream loading policies remain incomplete. Preserve all existing scope-control and Git restrictions.

## Latest Owner-Accepted Gate — 2026-10-31

<!-- DATA X AI CHECKPOINT 2026-10-31 V4.4.4 OWNER ACCEPTED -->

Consistently with `AGENTS.md`, Volume 4.4.4 (SourceBatch Reconstruction and Replay) is IMPLEMENTED, TESTED, LOCALLY VERIFIED, POSTGRES VERIFIED according to recorded evidence, INDEPENDENTLY REVIEWED, and FORMALLY OWNER ACCEPTED on 2026-10-31. Refer to the dated `docs/PROJECT_STATE.md` checkpoint for Codex-reported test evidence, independent source-review provenance and accepted limitations. No tests were rerun for this documentation update.

This supersedes historical 4.4.4-proposed/unauthorized statements without deleting them. Volumes 4.4.1 and 4.4.2 remain formally accepted and merged (PR #3 / `af24048`, PR #4 / `f61fc7f`); 4.4.3 is accepted and merged through PR #5 on 2026-10-30 at `52da230`. Current 4.4.4 implementation remains uncommitted on `feat/etl-4.4.4-reconstruction` at `52da230`; do not infer commit, push, PR, merge or CI verification from acceptance.

Preserve the read-only Python reconstruction service, supported source fidelity and accepted writer/serializer/schema contracts. No HTTP endpoint is implemented. Keep one-batch memory, immutable-record consistency and fingerprint exclusions explicit.

**4.4.5 is the next architecture/design gate: not implemented, not tested and not formally accepted.** Obtain explicit owner authorization before starting it or later work. Full Volume 4.4/end-to-end ETL and downstream loading policies remain incomplete. All existing scope-control and Git restrictions continue to apply.
