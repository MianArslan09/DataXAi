"""Configuration-only regressions; synthetic passwords, no database writes."""

import os
import shutil
import subprocess

import pytest
from decouple import Config, RepositoryEnv
from sqlalchemy.engine import make_url

from config.source_database import source_database_url
from config.source_database import test_source_database_url as resolve_test_url
from config.source_database import validate_test_source_url


@pytest.fixture
def read_config(tmp_path, monkeypatch):
    for key in list(os.environ):
        if key.startswith("SOURCE_") or key == "TEST_SOURCE_DATABASE_URL":
            monkeypatch.delenv(key)

    def load(contents=""):
        path = tmp_path / "synthetic.env"
        path.write_text(contents, encoding="utf-8")
        return Config(RepositoryEnv(str(path)))

    return load


def test_windows_defaults_and_special_password_round_trip(read_config):
    password = "synthetic@:/?#%$ value"
    config = read_config("SOURCE_POSTGRES_PASSWORD=" + password)
    url = make_url(source_database_url(config))
    assert (url.host, url.port, url.database, url.username) == (
        "127.0.0.1",
        5433,
        "dataxai_source",
        "dataxai_source",
    )
    assert url.password == password


def test_process_components_override_dotenv(read_config, monkeypatch):
    config = read_config("SOURCE_POSTGRES_HOST=127.0.0.1\nSOURCE_POSTGRES_PORT=5433")
    monkeypatch.setenv("SOURCE_POSTGRES_HOST", "source_db")
    monkeypatch.setenv("SOURCE_POSTGRES_PORT", "5432")
    url = make_url(source_database_url(config))
    assert (url.host, url.port) == ("source_db", 5432)


def test_explicit_source_url_and_docker_blank_override(read_config, monkeypatch):
    explicit = "postgresql+psycopg://user:synthetic@remote:6543/upstream"
    config = read_config("SOURCE_DATABASE_URL=" + explicit)
    assert source_database_url(config) == explicit
    monkeypatch.setenv("SOURCE_DATABASE_URL", "")
    monkeypatch.setenv("SOURCE_POSTGRES_HOST", "source_db")
    assert make_url(source_database_url(config)).host == "source_db"


def test_tests_default_to_source_and_preserve_ci_override(read_config, monkeypatch):
    config = read_config()
    source = source_database_url(config)
    assert resolve_test_url(config, source) == source
    ci = "postgresql+psycopg://user:synthetic@localhost:5433/ci_source"
    monkeypatch.setenv("TEST_SOURCE_DATABASE_URL", ci)
    assert validate_test_source_url(resolve_test_url(config, source)) == ci


def test_test_url_can_be_loaded_from_dotenv(read_config):
    url = "postgresql+psycopg://user:synthetic@127.0.0.1:5433/test_source"
    assert resolve_test_url(read_config("TEST_SOURCE_DATABASE_URL=" + url), "unused") == url


@pytest.mark.parametrize(
    "url", ["", "invalid-secret", "sqlite:///wrong", "postgresql://", "postgresql://host:99999/db"]
)
def test_invalid_explicit_test_url_never_falls_back_or_leaks(read_config, url):
    config = read_config("TEST_SOURCE_DATABASE_URL=" + url)
    resolved = resolve_test_url(config, "postgresql://valid/source")
    assert resolved == url
    with pytest.raises(ValueError) as caught:
        validate_test_source_url(resolved)
    assert str(caught.value) == "Invalid PostgreSQL source test configuration; URL withheld"


def test_invalid_component_port_never_falls_back(read_config):
    with pytest.raises(ValueError):
        source_database_url(read_config("SOURCE_POSTGRES_PORT=invalid"))


@pytest.fixture
def compose_password(tmp_path):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI required for dotenv compatibility checks")
    compose = tmp_path / "compose.yaml"
    compose.write_text(
        "services:\n  probe:\n    image: postgres:16-alpine\n"
        "    environment:\n      VALUE: ${SOURCE_POSTGRES_PASSWORD}\n"
    )

    def parse(contents, override=None):
        path = tmp_path / "compose.env"
        path.write_text(contents, encoding="utf-8")
        env = {k: v for k, v in os.environ.items() if not k.startswith(("SOURCE_", "COMPOSE_"))}
        env["DATAXAI_SYNTHETIC"] = "expanded"
        if override is not None:
            env["SOURCE_POSTGRES_PASSWORD"] = override
        result = subprocess.run(
            [
                "docker",
                "compose",
                "--env-file",
                str(path),
                "-f",
                str(compose),
                "config",
                "--environment",
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode:
            return None
        # --environment gives parsed values; rendered Compose JSON escapes dollars.
        return next(
            line.split("=", 1)[1]
            for line in result.stdout.splitlines()
            if line.startswith("SOURCE_POSTGRES_PASSWORD=")
        )

    return parse


@pytest.mark.parametrize(
    "password",
    [
        "synthetic",
        'syn"quote',
        "syn${DATAXAI_SYNTHETIC}$value",
        " syn # space ",
        r"syn\path\nvalue",
        r"syn\\path",
        "syn@:/?% value",
    ],
)
def test_shared_single_quoted_password(read_config, compose_password, password):
    contents = "SOURCE_POSTGRES_PASSWORD='" + password + "'\n"
    config = read_config(contents)
    assert config("SOURCE_POSTGRES_PASSWORD") == password
    assert compose_password(contents) == password
    assert make_url(source_database_url(config)).password == password


@pytest.mark.parametrize(
    "literal",
    [
        r"'syn\'quote'",
        r'"syn\nvalue"',
        "syn # comment",
        "syn${DATAXAI_SYNTHETIC}value",
        "'syn\\'",
    ],
)
def test_unsupported_shared_syntax_is_not_equivalent(read_config, compose_password, literal):
    contents = "SOURCE_POSTGRES_PASSWORD=" + literal + "\n"
    config = read_config(contents)
    assert compose_password(contents) != config("SOURCE_POSTGRES_PASSWORD")


@pytest.mark.parametrize("password", ["syn'quote", "syn'\"$ # space", "syn\\"])
def test_raw_process_password_override(read_config, compose_password, monkeypatch, password):
    contents = "SOURCE_POSTGRES_PASSWORD='synthetic-placeholder'\n"
    config = read_config(contents)
    monkeypatch.setenv("SOURCE_POSTGRES_PASSWORD", password)
    assert compose_password(contents, override=password) == password
    assert make_url(source_database_url(config)).password == password
