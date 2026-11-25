# DataXAi

**Auto-heal ETL Pipelines & Predictive Analytics Platform for Pakistani E-Commerce SMEs**

Final Year Project — BS Software Engineering, National University of Modern Languages (NUML), Faisalabad Campus.

The project aims to detect eight classes of data-quality faults in e-commerce pipelines, repair eligible records, quarantine unresolved cases, and use cleaned data for demand forecasting and customer lifetime value estimation. The [engineering handbook](docs/handbook/) describes the design.

**Team:** M Arslan Ahmad ([@Arslan](https://github.com)) · Fatima Munawar
**Supervisor:** Mr. Usama Shahzore, Dept. of Software Engineering, NUML Faisalabad

## Dataset

Real ETL development uses the actual UCI Online Retail II dataset, not a synthetic substitute. Place it at:

```
data/online_retail_II.xlsx
```

(gitignored - ~44MB, two sheets: `Year 2009-2010`, `Year 2010-2011`, 1,067,371 rows combined). Get it from the [UCI ML Repository](https://archive.ics.uci.edu/dataset/502/online+retail+ii). Automated tests instead use a small, real-row-derived fixture at `apps/etl/tests/fixtures/online_retail_ii_sample.csv` - see that folder's README for exactly which rows and why.

**Two separate databases, do not confuse them:** `DATABASE_URL`/`POSTGRES_*` is DataXAi's own application/warehouse database (Django ORM). `SOURCE_DATABASE_URL` is a distinct upstream/source database that M1 reads *from* (SQLAlchemy) - it simulates an SME's own existing system and must never be the same connection as the app database.

## Quick Start

```bash
git clone <your-repo-url> dataxai && cd dataxai
cp .env.example .env          # fill in a real DJANGO_SECRET_KEY and GROQ_API_KEY
docker compose up -d --build
docker compose exec web python manage.py createsuperuser
```

Then visit:
- `http://localhost/api/health/` — should return `{"status": "ok", "database": true}`
- `http://localhost/admin/` — Django Admin

## Local development without Docker

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements/dev.txt
export DJANGO_SETTINGS_MODULE=config.settings.dev
.venv/bin/python manage.py migrate
.venv/bin/python manage.py runserver
```

Requires a local PostgreSQL 16 and Redis 7 instance — see `.env.example` for the expected connection variables, or just use `docker compose up db cache` and point `dev.py` at those.

## Tech Stack

The integrated frontend uses React 19, TypeScript and Vite. Its demo provider drives eight Admin, Manager and Seller views. API mode connects JWT login, identity, refresh and health to Django. See the [frontend README](https://github.com/MianArslan09/DataXAi/blob/beecd17edff377dfa19d113b772323e428d9fc26/frontend/README.md) and the [business API gap inventory](https://github.com/MianArslan09/DataXAi/blob/beecd17edff377dfa19d113b772323e428d9fc26/frontend/docs/INTEGRATION.md).

Backend stack and planned module dependencies:

Django 5.2 LTS · DRF 3.15 · PostgreSQL 16 · Redis · Celery · Great Expectations 1.0+ · Pydantic v2 · scikit-learn (Isolation Forest) · Facebook Prophet · XGBoost · `btyd` (BG/NBD) · Groq (`gpt-oss`) · Loguru · Docker Compose · Nginx · pytest · GitHub Actions

Full rationale for every choice — including three updates made after the original proposal (Django 4.2→5.2 LTS, Great Expectations 0.18→1.0+, `lifetimes`→`btyd`, and Groq's Llama 3 models→`gpt-oss`) — is in [`docs/handbook/volume-01-foundations.md`](docs/handbook/volume-01-foundations.md).

## Project Structure

```
dataxai/
├── frontend/            # React/TypeScript/Vite application on main (PR #13)
├── config/              # Django project package: settings/, celery.py, asgi.py, urls.py
├── apps/                # One app per module: core, accounts, etl (M1), validation (M2),
│                         #   detection (M3), healing (M4), forecasting + clv (M5),
│                         #   nlp (M6), dashboard (M7)
├── research/             # Fault injector + C1-C5 evaluation harness - NOT a Django app
├── docker/               # Dockerfile (multi-stage) + Nginx config
├── requirements/         # base / ml / dev / prod, split by when each is needed
├── docs/handbook/         # The engineering handbook, volume by volume
├── .github/workflows/     # CI: lint -> test -> docker build
└── docker-compose.yml     # db, cache, web, worker, beat, nginx
```

## Status

As of November 25, 2026, performance Gates 1–5, sequential ETL coordination and the React frontend are integrated through PRs #10, #12 and #13 respectively. On the backend feature branch, Volume 4.5.3b adds evidence-gated CSV/Excel completion. Owner-side Windows verification reconciled 1,067,371 UCI rows across 22 PostgreSQL batches and verified the sealed proof after recovery. Volume 4.5.3b was formally owner accepted on November 25, 2026, for the verified bound CSV/Excel scope with documented limitations; GitHub integration remains pending. Repairs, quarantine and predictive results shown in the frontend use demo data. See [project state](docs/PROJECT_STATE.md) for the evidence and [roadmap](docs/PROJECT_ROADMAP.md) for the remaining gates.

## License

MIT — see [`LICENSE`](LICENSE). This project is released open-source per the proposal's Section 3.10 and SDG 17 alignment.
