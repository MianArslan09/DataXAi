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
- Volume 4.3.1 is implemented and locally verified at `136c6db`, pending owner acceptance. Do not start 4.3.1a or 4.3.2 until the owner accepts 4.3.1. Do not start 4.3.2 until 4.3.1a is also integrated, locally verified, reviewed, and accepted.
- Do not create parallel implementations or modify unrelated modules. Preserve user changes; do not run destructive Git operations or rewrite history.
- Never stage automatically; when staging is explicitly authorized, stage only the named paths. Never commit or push automatically.
- Never create another virtual environment unless explicitly required. Do not install packages or modify requirements.
- Do not claim verification that was not performed against the current checkout.

For every implementation task, explicitly state:
1. What already exists.
2. What is missing.
3. What will be changed.
4. How it will be verified.
