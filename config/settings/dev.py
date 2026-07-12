"""Local development settings. Runs against a real Postgres (via docker-compose)."""

from .base import *  # noqa: F401,F403

DEBUG = True
POSTGRES_HOST = config("POSTGRES_HOST", default="localhost")  # noqa: F405
DATABASES["default"]["HOST"] = POSTGRES_HOST  # noqa: F405
