"""
Shared choice sets used by more than one app. Kept in `core` so `healing`
(QuarantineRecord) and `core` itself (AuditLogEntry) reference the exact
same 8 fault codes instead of two drifting copies of the same list.

Matches Table 1 of the proposal exactly (Data Quality Fault Taxonomy).
"""

from django.db import models


class FaultType(models.TextChoices):
    MISSING_VALUE = "missing_value", "Missing Value"
    DUPLICATE_RECORD = "duplicate_record", "Duplicate Record"
    SCHEMA_DRIFT = "schema_drift", "Schema Drift"
    STATISTICAL_OUTLIER = "statistical_outlier", "Statistical Outlier"
    TYPE_ERROR = "type_error", "Type Error"
    RANGE_VIOLATION = "range_violation", "Range Violation"
    FORMAT_INCONSISTENCY = "format_inconsistency", "Format Inconsistency"
    REFERENTIAL_INTEGRITY = "referential_integrity", "Referential Integrity Fault"
