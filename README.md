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

Django 5.2 LTS · DRF 3.15 · PostgreSQL 16 · Redis · Celery · Great Expectations 1.0+ · Pydantic v2 · scikit-learn (Isolation Forest) · Facebook Prophet · XGBoost · `btyd` (BG/NBD) · Groq (`gpt-oss`) · Loguru · Docker Compose · Nginx · pytest · GitHub Actions

Full rationale for every choice — including three updates made after the original proposal (Django 4.2→5.2 LTS, Great Expectations 0.18→1.0+, `lifetimes`→`btyd`, and Groq's Llama 3 models→`gpt-oss`) — is in [`docs/handbook/volume-01-foundations.md`](docs/handbook/volume-01-foundations.md).

## Project Structure

```
dataxai/
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

As of November 8, 2026, Volume 4.4.7 is integrated and the owner has accepted performance Gates 1–5. Performance-series Git integration is pending. The full ETL workflow remains incomplete. See [project state](docs/PROJECT_STATE.md) for benchmark evidence and the outstanding replay-threshold acknowledgment, and the [roadmap](docs/PROJECT_ROADMAP.md) for milestone boundaries.

## License

MIT — see [`LICENSE`](LICENSE). This project is released open-source per the proposal's Section 3.10 and SDG 17 alignment.
