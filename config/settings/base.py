"""
Base settings shared by every environment.
Environment-specific settings (dev/test/prod) import * from this module
and override only what differs. See Volume 1, Section 1.4 for the
layered-architecture rationale behind the apps/ split below.
"""

import sys
from pathlib import Path

from decouple import Csv, config

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Local apps live under apps/ and are importable without an "apps." prefix,
# e.g. INSTALLED_APPS = [..., "etl", "healing", ...] not "apps.etl".
sys.path.insert(0, str(BASE_DIR / "apps"))

SECRET_KEY = config("DJANGO_SECRET_KEY", default="dev-insecure-change-me-in-prod-please-really-do")
DEBUG = config("DJANGO_DEBUG", default=False, cast=bool)
ALLOWED_HOSTS = config("DJANGO_ALLOWED_HOSTS", default="localhost,127.0.0.1", cast=Csv())

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "rest_framework_simplejwt",
    "django_celery_beat",
]

# One app per production module (M1-M7), plus core (shared base classes)
# and accounts (auth/RBAC, technically part of M7 but kept separate because
# the User model has to exist before the first migration ever runs).
LOCAL_APPS = [
    "core",
    "accounts",
    "warehouse",  # Clean Warehouse domain: Customer, Product, OrderLine
    "etl",  # M1
    "validation",  # M2
    "detection",  # M3
    "healing",  # M4
    "forecasting",  # M5 (Prophet half)
    "clv",  # M5 (XGBoost half)
    "nlp",  # M6
    "dashboard",  # M7
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": config("POSTGRES_DB", default="dataxai"),
        "USER": config("POSTGRES_USER", default="dataxai"),
        "PASSWORD": config("POSTGRES_PASSWORD", default="dataxai"),
        "HOST": config("POSTGRES_HOST", default="db"),
        "PORT": config("POSTGRES_PORT", default="5432"),
    }
}

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "Asia/Karachi"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": ("rest_framework.permissions.IsAuthenticated",),
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardResultsPagination",
    "PAGE_SIZE": 25,
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
}

# Celery - broker/result backend is Redis, matching Table 8's Docker Compose stack.
REDIS_URL = config("REDIS_URL", default="redis://cache:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULER = "django_celery_beat.schedulers:DatabaseScheduler"

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": config("REDIS_CACHE_URL", default="redis://cache:6379/1"),
    }
}

# M6 - Groq. Model IDs updated per Volume 1 Section 1.5 (llama-3.x deprecated
# by Groq on 2026-06-17). Real key comes from .env, never committed.
GROQ_API_KEY = config("GROQ_API_KEY", default="")
GROQ_NARRATION_MODEL = config("GROQ_NARRATION_MODEL", default="openai/gpt-oss-120b")
GROQ_CHATBOT_MODEL = config("GROQ_CHATBOT_MODEL", default="openai/gpt-oss-20b")

LOGIN_URL = "/admin/login/"
