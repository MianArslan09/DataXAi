"""Source-only connection configuration; independent of Django's database."""

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError


def source_database_url(config):
    """Keep explicit URLs authoritative; otherwise encode component credentials."""
    explicit = config("SOURCE_DATABASE_URL", default="")
    if explicit:
        return explicit
    return URL.create(
        "postgresql+psycopg",
        username=config("SOURCE_POSTGRES_USER", default="dataxai_source"),
        password=config("SOURCE_POSTGRES_PASSWORD", default=""),
        host=config("SOURCE_POSTGRES_HOST", default="127.0.0.1"),
        port=config("SOURCE_POSTGRES_PORT", default=5433, cast=int),
        database=config("SOURCE_POSTGRES_DB", default="dataxai_source"),
    ).render_as_string(hide_password=False)


def test_source_database_url(config, source_url):
    """An explicitly supplied test URL must never fall back on failure."""
    return config("TEST_SOURCE_DATABASE_URL", default=source_url)


def validate_test_source_url(url):
    """Reject invalid explicit configuration without echoing credential values."""
    try:
        parsed = make_url(url)
        if parsed.get_backend_name() != "postgresql" or not parsed.host or not parsed.database:
            raise ValueError
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            raise ValueError
    except (ArgumentError, ValueError, TypeError):
        raise ValueError("Invalid PostgreSQL source test configuration; URL withheld") from None
    return url
