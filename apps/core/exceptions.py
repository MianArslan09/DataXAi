"""
Domain exceptions + a DRF exception handler that wraps every error
response (validation, permission, not-found, or an uncaught domain
exception) in one consistent JSON shape:

    {"error": {"code": "quarantine_resolution_error", "message": "..."}}

so the dashboard (M7) never has to special-case DRF's default shape vs.
a raw 500 vs. a domain error.
"""

import logging

from rest_framework import exceptions as drf_exceptions
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_default_exception_handler

logger = logging.getLogger(__name__)


class DataXAiError(Exception):
    """Base class for every domain-level exception in this project."""

    default_message = "An unexpected error occurred."
    code = "dataxai_error"

    def __init__(self, message: str | None = None):
        self.message = message or self.default_message
        super().__init__(self.message)


class QuarantineResolutionError(DataXAiError):
    default_message = "This quarantine record has already been resolved."
    code = "quarantine_resolution_error"


class InvalidRepairStrategyError(DataXAiError):
    """Raised by the strategy registry (Volume 6) for an unregistered fault code."""

    default_message = "No repair strategy is registered for this fault type."
    code = "invalid_repair_strategy"


def custom_exception_handler(exc, context):
    """
    Registered via REST_FRAMEWORK["EXCEPTION_HANDLER"]. Handles our own
    DataXAiError subclasses (which DRF doesn't know about) by converting
    them to a 400, then falls back to DRF's default handler for
    everything else (ValidationError, PermissionDenied, Http404, ...),
    and finally re-shapes whatever DRF produced into the consistent
    {"error": {...}} envelope.
    """
    if isinstance(exc, DataXAiError):
        logger.warning("DataXAiError: %s", exc.message, extra={"code": exc.code})
        return Response({"error": {"code": exc.code, "message": exc.message}}, status=400)

    response = drf_default_exception_handler(exc, context)
    if response is None:
        return None

    code = "error"
    if isinstance(exc, drf_exceptions.APIException):
        code = getattr(exc, "default_code", exc.__class__.__name__.lower())

    response.data = {"error": {"code": code, "message": _flatten_detail(response.data)}}
    return response


def _flatten_detail(detail) -> str:
    """DRF error `detail` can be a string, a list, or a nested dict of
    field errors - flatten all three into one human-readable string for
    the envelope's `message` field."""
    if isinstance(detail, (list, tuple)):
        return "; ".join(_flatten_detail(item) for item in detail)
    if isinstance(detail, dict):
        return "; ".join(f"{key}: {_flatten_detail(value)}" for key, value in detail.items())
    return str(detail)
