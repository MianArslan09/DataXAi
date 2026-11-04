"""Shared attempt configuration identity and validation, independent of both services.

Canonical hashing, profile bounds and controlled error semantics are unchanged
from Volume 4.4.5. Callers validate the already locked model instance; no queries
or state changes are performed here.
"""

import hashlib
import json
import re

from django.core.exceptions import ValidationError

from etl.exceptions import AttemptIntegrityError, AttemptRegistrationError, ETLError
from etl.models import validate_attempt_configuration
from etl.transforms.date_contract import SourceDateProfile

_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")

_CREDENTIAL = re.compile(
    r"(?i)(?:password|passwd|pwd|secret|token|api[_ -]?key|authorization)\s*[:=]"
    r"|://|\bbearer\s+|-----BEGIN .*PRIVATE KEY"
)


def _require(condition, message, error=AttemptIntegrityError):
    if not condition:
        raise error(message)


def _text(value, limit):
    _require(
        type(value) is str and bool(value.strip()) and len(value) <= limit,
        "Attempt metadata requires bounded nonblank text.",
        AttemptRegistrationError,
    )
    _require(
        not any(ord(c) < 32 or 0xD800 <= ord(c) <= 0xDFFF for c in value)
        and not _CREDENTIAL.search(value),
        "Attempt metadata contains unsupported or credential-bearing text.",
        AttemptRegistrationError,
    )
    return value


def _identifier(value, limit):
    _text(value, limit)
    _require(
        _IDENTIFIER.fullmatch(value) is not None,
        "Attempt codes and versions must be bounded ASCII identifiers.",
        AttemptRegistrationError,
    )
    return value


def _configuration(profile):
    _require(
        type(profile) is SourceDateProfile,
        "A validated SourceDateProfile is required.",
        AttemptRegistrationError,
    )
    fields = {
        "name": _text(profile.name, 128),
        "date_order": profile.date_order,
        "timezone": profile.timezone,
        "provenance": profile.provenance,
    }
    for key in ("date_order", "timezone", "provenance"):
        if fields[key] is not None:
            _text(fields[key], 16_384)
    try:
        SourceDateProfile(**fields)  # Recheck even a tampered frozen profile.
        config = {"schema": "interpretation-config-v1", "date_profile": fields}
        validate_attempt_configuration(config)
    except (ETLError, ValidationError):
        raise AttemptRegistrationError("Invalid or oversized date profile configuration.") from None
    return config


def _hash(config):
    canonical = json.dumps(
        config, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stored_profile(attempt):
    config = attempt.configuration
    _require(
        type(config) is dict
        and set(config) == {"schema", "date_profile"}
        and config["schema"] == "interpretation-config-v1"
        and type(config["date_profile"]) is dict
        and set(config["date_profile"]) == {"name", "date_order", "timezone", "provenance"},
        "The stored attempt configuration is invalid.",
    )
    try:
        fields = config["date_profile"]
        # Validate primitive types before the profile constructor evaluates settings.
        for value in fields.values():
            if value is not None:
                _text(value, 16_384)
        profile = SourceDateProfile(**fields)
        expected = _configuration(profile)
        _identifier(attempt.transformation_version, 128)
    except ETLError:
        raise AttemptIntegrityError("The stored attempt configuration is invalid.") from None
    _require(
        config == expected and attempt.configuration_hash == _hash(expected),
        "The stored attempt configuration hash is inconsistent.",
    )
    return profile
