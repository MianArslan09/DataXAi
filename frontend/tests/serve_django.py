"""Loopback auth fixture for the optional browser integration suite."""

import os
import secrets
import sys
from datetime import timedelta
from pathlib import Path
from wsgiref.simple_server import make_server

import django
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.core.wsgi import get_wsgi_application


def main():
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    os.environ["DJANGO_SETTINGS_MODULE"] = "config.settings.test"
    os.environ["DJANGO_SECRET_KEY"] = secrets.token_hex(40)
    settings.DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}
    settings.ALLOWED_HOSTS = ["localhost", "127.0.0.1"]
    settings.SIMPLE_JWT = {"ACCESS_TOKEN_LIFETIME": timedelta(seconds=21)}
    django.setup()
    call_command("migrate", "accounts", verbosity=0)
    for role in ["admin", "data_engineer", "business_analyst"]:
        get_user_model().objects.create_user(username=role, password="fixture-only", role=role)
    with make_server("127.0.0.1", 8193, get_wsgi_application()) as server:
        server.serve_forever()


if __name__ == "__main__":
    main()
