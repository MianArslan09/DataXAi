# DataXAi Engineering Handbook
## Volume 2 — Environment, Git, Directory Structure, Docker, CI/CD & Initial Django Setup

Everything in this volume was actually built and verified in a real sandbox before being handed to you — not described in the abstract. Where something couldn't be verified (Docker itself isn't available in that sandbox), that's stated plainly rather than implied.

---

## 2.1 What Actually Exists Now

A cloned-and-runnable Django 5.2 project with:
- 10 apps registered (`core`, `accounts` + one per module `etl`/`validation`/`detection`/`healing`/`forecasting`/`clv`/`nlp`/`dashboard`)
- A custom `User` model, migrated, with a working JWT token endpoint
- A `/api/health/` endpoint
- 2 passing tests, 82% coverage (the rest is untouched placeholder code, expected at this stage)
- A full Docker Compose stack (db, cache, web, worker, beat, nginx) — YAML-validated, not yet build-tested (see 2.9)
- A GitHub Actions CI pipeline (lint → test → docker build)
- 10 real git commits, Conventional Commits style
- Split requirements files, with the ML stack deliberately deferred

Everything below explains a decision already made in the code, not a plan.

---

## 2.2 Environment Setup

**Python 3.12**, one version ahead of the 3.11 floor Volume 1 assumed — the sandbox this was built in ships 3.12, Django 5.2 supports it, and there's no reason to pin backward. Use `python3.12 -m venv .venv` locally; the Docker image also targets `python:3.12-slim`, so "works in the container" and "works on your laptop" mean the same interpreter.

**Why `python-decouple`, not `django-environ` or bare `os.environ`.** One job (read `.env`, cast types), smaller surface than `django-environ`'s combined env/URL-parsing API, and every setting in `base.py` reads through `config(...)` uniformly — no mixed `os.environ.get()` / `config()` call sites to keep straight later.

**Why a plain venv, not Poetry/Pipenv.** Two people, a fixed pinned `requirements/*.txt`, a Docker-first workflow — a lockfile-based tool earns its complexity on bigger teams with more dependency churn. `pip-tools` (`pip-compile`) is the natural upgrade if `requirements.txt` drift becomes a real problem later; not needed yet.

---

## 2.3 Directory Structure, Explained

```
dataxai/
├── manage.py
├── config/                        # Django project package
│   ├── settings/
│   │   ├── base.py                # everything shared
│   │   ├── dev.py                 # DEBUG=True, Postgres on localhost
│   │   ├── test.py                # SQLite, eager Celery, fast password hasher
│   │   └── prod.py                # security headers, HSTS, secure cookies
│   ├── celery.py                  # Celery app, autodiscovers tasks/ in every app
│   ├── asgi.py                    # served via gunicorn + UvicornWorker (async, for M6)
│   ├── wsgi.py                    # kept for management commands / WSGI-only tooling
│   └── urls.py                    # root router: /admin/, /api/health/, /api/auth/
├── apps/                          # one Django app per concern (see 2.3.1)
├── research/                      # Fault Injector + C1-C5 harness - NOT in INSTALLED_APPS
├── docker/
│   ├── django/Dockerfile          # multi-stage: build wheels, then lean runtime
│   └── nginx/                     # reverse proxy, SSE-aware (buffering off)
├── requirements/
│   ├── base.txt                   # web framework + celery + auth - installed now
│   ├── ml.txt                     # pandas/GE/sklearn/prophet/xgboost/btyd - Volumes 4-9
│   ├── dev.txt                    # -r base.txt + pytest/black/isort/ruff/pre-commit
│   └── prod.txt                   # -r base.txt -r ml.txt (what the Docker image installs)
├── docs/handbook/                 # this handbook, volume by volume, versioned with the code
├── data/                          # gitignored - local dataset staging (see 2.10)
├── static/ · templates/           # Volume 11 territory, scaffolded now so paths exist
├── .github/workflows/ci.yml       # lint -> test -> docker build
├── docker-compose.yml             # db, cache, web, worker, beat, nginx
├── .env.example · .gitignore · .dockerignore
├── .pre-commit-config.yaml · pyproject.toml (black/isort/ruff config) · pytest.ini
├── Makefile                       # `make up`, `make test`, `make lint`, etc.
├── README.md · LICENSE (MIT)
```

### 2.3.1 Why one app per module, not one big `apps.py`

Direct mapping from Table 4: `etl`=M1, `validation`=M2, `detection`=M3, `healing`=M4, `forecasting`+`clv`=M5 (split — see Volume 1, §1.5's SRP reasoning), `nlp`=M6, `dashboard`=M7. `accounts` is split out of `dashboard`/M7 for one concrete reason: **the custom `User` model has to exist and be `AUTH_USER_MODEL` before the very first migration ever runs.** Bundling it into a bigger `dashboard` app that also holds five view classes and template logic would mean touching a much bigger, messier app on day one just to get the user model in place. `core` holds what has no natural single owner: `TimeStampedModel`, `BaseService`, `BaseRepository`, and the health check.

### 2.3.2 The `apps/` import trick

`INSTALLED_APPS` lists `"etl"`, not `"apps.etl"`. This works because `base.py` does:

```python
sys.path.insert(0, str(BASE_DIR / "apps"))
```

before `INSTALLED_APPS` is read. This is a well-known, standard pattern (see *Two Scoops of Django*) — it keeps the repo root uncluttered by ten app folders sitting next to `config/`, `docker/`, `docs/`, etc.

---

## 2.4 Git Repository & Workflow

**Branching: GitHub Flow, not Git Flow.** Git Flow's `develop`/`release`/`hotfix` branch hierarchy exists to manage multiple things in production at once — irrelevant for a 2-person FYP with one deployment target. GitHub Flow is simpler and matches how you'll actually work: `main` is always deployable, every change is a short-lived `feature/*` branch, merged via PR once CI passes.

**Branch naming:** `feature/<module>-<short-description>` (e.g. `feature/etl-alias-mapping`), `fix/<short-description>`, `docs/<short-description>`.

**Commit convention: Conventional Commits.** `feat:`, `fix:`, `chore:`, `docs:`, `ci:`, `test:`, with an optional scope — `feat(accounts): ...`. This is what the 10 commits already in the repo follow; here's the actual log:

```
c471d6c ci: add GitHub Actions workflow (lint, test, docker build) and pre-commit hooks
97d9de1 chore(docker): add multi-stage Dockerfile, docker-compose stack, and nginx config
ba3b76c chore: add split requirements (base/ml/dev/prod) and tool config
e1927ec chore(research): add Research Harness placeholder, deliberately outside INSTALLED_APPS
e876e15 chore(apps): register placeholder apps for M1-M7, annotated with target volume
fe9c2f7 feat(accounts): add custom User model with role field and JWT token endpoints
08dbb17 feat(core): add TimeStampedModel, BaseService/BaseRepository, and /api/health/
0e10986 feat: bootstrap Django 5.2 project with settings split (base/dev/test/prod)
d4e0ad5 chore: initialize DataXAi repository structure
```

(plus one more tracking the empty `data/` directory). Each commit is small and reversible on purpose — exactly the granularity `git bisect` and code review both want.

---

## 2.5 Initial Django Setup — What Was Actually Wired

- **Settings split** (`base`/`dev`/`test`/`prod`) so `manage.py test` never touches Postgres, `prod.py` carries the security headers Volume 15 will build on, and `dev.py` is the only one a local `runserver` needs.
- **Custom `User` model** (`accounts.User`, extends `AbstractUser`, adds a `role` field with three choices matching the proposal's three roles) — migrated, registered in Django Admin. Full RBAC enforcement (permission classes checking `role`, not just storing it) is Volume 3's job; what exists now is the field and the migration, which is the part that's expensive to retrofit.
- **JWT proof of life**: `POST /api/auth/token/` and `POST /api/auth/token/refresh/` both work against SimpleJWT right now — tested in `apps/accounts/tests/test_auth.py`.
- **`/api/health/`**: unauthenticated, checks a live DB connection, returns 503 if the DB is down — this is what `docker-compose.yml`'s `web` healthcheck polls.
- **Celery app** (`config/celery.py`) configured with `autodiscover_tasks()`, so any app that adds a `tasks.py` starting in Volume 4 is picked up with zero settings changes.

---

## 2.6 Docker & Compose, Explained

**Multi-stage Dockerfile.** Stage 1 (`builder`) compiles wheels with the full build toolchain (`build-essential`, `libpq-dev`); stage 2 copies only the compiled wheels and `libpq5` (the runtime `.so`, not the dev headers) into a slim final image, and drops root (`USER dataxai`). This keeps the shipped image meaningfully smaller and follows the standard "don't ship a compiler in production" practice.

**Why Nginx has `proxy_buffering off` and a 120s `proxy_read_timeout`.** This is the one non-default Nginx setting, and it exists specifically for M6: SSE connections are long-lived and stream incrementally — Nginx's default buffering would hold the whole response until it's complete or a buffer fills, defeating the entire point of server-sent events. This single line is why the "async view + StreamingHttpResponse" decision from Volume 1 §1.6 actually works end-to-end once Nginx is in front of it.

**Service list matches Table 8 exactly**: `db` (postgres:16-alpine), `cache` (redis:7-alpine), `web` (gunicorn + UvicornWorker, runs migrate + collectstatic on boot, healthchecked), `worker` (Celery), `beat` (Celery Beat, database-backed schedule via `django-celery-beat`), `nginx` (reverse proxy + static file serving). `web`, `worker`, and `beat` all wait on `db`/`cache` healthchecks via `depends_on: condition: service_healthy`, not just "container started" — this avoids the classic Compose race where Django tries to migrate before Postgres has finished initializing.

**What's verified vs. what isn't (2.9 has the honest version).**

---

## 2.7 CI/CD, Explained

Three jobs, each depending on the last:

1. **`lint`** — `ruff check`, `black --check`, `isort --check-only`. Fails fast, cheapest job, runs first.
2. **`test`** — spins up real `postgres:16-alpine` and `redis:7-alpine` service containers (not SQLite — this is GitHub Actions' equivalent of `docker compose`'s `db`/`cache`), runs `pytest --cov=apps`.
3. **`docker-build`** — actually builds the production Dockerfile. This is the job that would have caught a broken `requirements/prod.txt` or a Dockerfile typo before you ever `git push` to a branch you're about to deploy from.

Runs on push and PR to `main` and `develop`. No deploy job yet — that's Volume 14.

---

## 2.8 Verification Log (real output, condensed)

```
$ python manage.py check
System check identified no issues (0 silenced).

$ python manage.py makemigrations --check --dry-run
No changes detected

$ pytest --cov=apps --cov-report=term-missing
apps/accounts/tests/test_auth.py::test_can_obtain_jwt_token_pair PASSED
apps/core/tests/test_health.py::test_health_check_returns_ok PASSED
2 passed in 1.52s — 82% coverage (remainder is untouched placeholder apps)

$ python manage.py check --deploy   (prod settings, throwaway test key)
WARNINGS:
?: (security.W009) Your SECRET_KEY has less than 50 characters...
```

That last warning is Django's deploy checklist correctly flagging a deliberately weak throwaway key used only for this check — not a real issue, but worth knowing that checklist exists and runs clean otherwise. Run `python manage.py check --deploy` again once `.env` has a real generated `DJANGO_SECRET_KEY` and it should report zero issues.

---

## 2.9 A Real Bug, and the Honest Limits of This Verification

**The bug:** the first version of `config/urls.py` mounted `core.urls` at `"api/health/"`, and `core/urls.py` *also* defined `"health/"` — so the actual route was `/api/health/health/`, and the health-check test failed with a 404. Fixed by mounting `core.urls` at `"api/"` instead, letting the app's own `urls.py` own the `health/` segment. This is the single most common Django routing mistake — double-check what a `path()` prefix actually concatenates to before assuming it's right; a hitting test is what caught it here, not a read-through.

**What wasn't verified:** this sandbox has no Docker daemon. `docker-compose.yml` is YAML-syntax-validated and every service/healthcheck/volume was written against Table 8 and cross-checked against the Dockerfile it references, but `docker compose up` itself has not been run. Run `docker compose config` (validates interpolation and merge, not just YAML) and then `docker compose up --build` as your first step after unzipping — if anything's wrong, it'll most likely be a missing env var or a build-context path, both quick fixes. `requirements/ml.txt` is written and version-pinned but not yet installed anywhere — Prophet specifically has a slow `cmdstanpy` build step that deserves its own focused volume (8) rather than being rushed here.

---

## 2.10 Getting This Running

1. Unzip into a folder, `cd` into it — the `.git` history from this volume is already there.
2. `cp .env.example .env`, fill in a real `DJANGO_SECRET_KEY` (`python -c "import secrets; print(secrets.token_urlsafe(50))"` works) and your Groq key.
3. Create an empty repo on GitHub, then `git remote add origin <url> && git push -u origin main`.
4. `docker compose up -d --build`, then `docker compose exec web python manage.py createsuperuser`.
5. Confirm `http://localhost/api/health/` returns `{"status": "ok", "database": true}`.
6. Download the UCI Online Retail II dataset (id 502) yourself and drop it in `data/` — this sandbox can't reach `archive.ics.uci.edu` (network allowlist is package-registry-only), but your machine or Claude Code can.

---

## Summary

Real repo, real commits, real passing tests, one real bug caught and fixed by the tests that were written to catch it. Docker Compose is written to spec and YAML-valid but not build-tested — that's on you to confirm on a machine that has Docker, and it's the very first thing to do after unzipping. The ML stack is deliberately not installed yet; that starts in Volume 4.

## Before Volume 3

- [ ] Confirm `docker compose up --build` actually works on your machine
- [ ] Push this repo to GitHub
- [ ] Generate a real `DJANGO_SECRET_KEY` and Groq key in `.env`
- [ ] Decide with Fatima: shared repo from here, or merge later per §1.7's ownership split

Volume 3 is Database Design + Auth + Core Backend Infrastructure — the Quarantine DLQ table, the audit log schema, the full RBAC permission classes (not just the `role` field), and the ER diagram for everything M1–M7 will eventually write to.
