"""
CI / local test settings. Uses SQLite so the test suite needs no external
services - Postgres-specific behaviour (JSONB, etc.) gets its own
integration tests against the real docker-compose db, added in Volume 3.
"""
from .base import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "test_db.sqlite3",  # noqa: F405
    }
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # faster tests only
