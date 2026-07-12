"""
ASGI entrypoint. Served in production via
    gunicorn config.asgi:application -k uvicorn.workers.UvicornWorker
per the Volume 1 architecture decision (M6's streaming chatbot needs async support).
"""
import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

application = get_asgi_application()
