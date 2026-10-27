"""
M1 - ETL Engine. `PipelineRun` is a row per batch execution, which the dashboard
view (M7) reads from and what M4's healing/quarantine records and M6's
audit log entries point back to via `pipeline_run` FKs.

The actual extract/transform code (Volume 4) is stateless and doesn't
live in models.py - this file only holds what needs to persist.

Volume 4.4.2 adds staging structure only. No save override performs serialization,
transformation, sanitization, completion, claiming or recovery. Field validators
run through full_clean(); database constraints also protect bulk/raw writes where
specified. PostgreSQL composite batch-membership FKs live in migration 0002.
"""

import json
import uuid

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models
from django.db.models.functions import Cast, Length
from django.db.models.lookups import Exact, LessThanOrEqual

from core.models import TimeStampedModel


class PipelineRun(TimeStampedModel):
    """
    One row per batch run. `fault_counts` and `cdqi_scores` are JSONB so
    Volume 5 (validation) and Volume 6 (healing) can add new keys - new
    fault types or new CDQI dimensions - without a schema migration.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        SUCCEEDED = "succeeded", "Succeeded"
        FAILED = "failed", "Failed"

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    records_ingested = models.PositiveIntegerField(default=0)
    records_healed = models.PositiveIntegerField(default=0)
    records_quarantined = models.PositiveIntegerField(default=0)

    fault_counts = models.JSONField(
        default=dict, blank=True, help_text="Per fault-type counts, e.g. {'missing_value': 12}"
    )
    cdqi_scores = models.JSONField(
        default=dict,
        blank=True,
        help_text="ISO 25012 dimensions (completeness/accuracy/consistency/timeliness/"
        "uniqueness) plus the composite score, before and after healing.",
    )

    triggered_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="triggered_pipeline_runs",
        help_text="Null for Celery Beat-scheduled runs.",
    )
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"PipelineRun({self.pk}, {self.status})"

    @property
    def duration_seconds(self):
        if self.started_at and self.finished_at:
            return (self.finished_at - self.started_at).total_seconds()
        return None


_REFERENCE_PATTERN = r"\A[A-Za-z0-9][A-Za-z0-9_. -]{0,254}\Z"
_HASH_PATTERN = r"\A[0-9a-f]{64}\Z"


def _unique_json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError("Non-finite JSON constant")


class EscapedJSONTextField(models.TextField):
    """ASCII JSON in a text column; Python values remain dictionaries or strings.

    Unlike JSONB or unencoded PostgreSQL text, this stores SER-v1's supported
    U+0000 strings losslessly. This is a storage envelope, not another serializer.
    JSON-path lookups are intentionally unavailable. Full SER-v1 validation stays
    with decode_source_batch after header/record reconstruction.
    """

    def __init__(self, *args, json_kind="object", **kwargs):
        if json_kind not in {"object", "string"}:
            raise ValueError("Unsupported staging JSON kind")
        self.json_kind = json_kind
        super().__init__(*args, **kwargs)

    def deconstruct(self):
        name, path, args, kwargs = super().deconstruct()
        kwargs["json_kind"] = self.json_kind
        return name, path, args, kwargs

    def to_python(self, value):
        # Do not let TextField.full_clean() stringify an already decoded object.
        return value

    def get_prep_value(self, value):
        if value is None:
            return None
        expected = dict if self.json_kind == "object" else str
        if type(value) is not expected:
            raise ValidationError("Invalid staging JSON type.", code="invalid_staging_json")
        try:
            return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
        except (TypeError, ValueError, OverflowError):
            raise ValidationError(
                "Invalid staging JSON value.", code="invalid_staging_json"
            ) from None

    def validate(self, value, model_instance):
        super().validate(value, model_instance)
        self.get_prep_value(value)

    def from_db_value(self, value, expression, connection):
        if value is None:
            return None
        try:
            decoded = json.loads(
                value, object_pairs_hook=_unique_json_object, parse_constant=_reject_json_constant
            )
        except (TypeError, ValueError):
            raise ValidationError(
                "Invalid stored staging JSON.", code="invalid_staging_json"
            ) from None
        if decoded is None:
            raise ValidationError("Invalid stored staging JSON type.", code="invalid_staging_json")
        self.get_prep_value(decoded)
        return decoded


class StoredJSONType(models.Func):
    """Check text JSON without converting to JSONB (which rejects escaped NUL)."""

    arity = 1
    function = "json_typeof"
    template = "%(function)s((%(expressions)s)::json)"
    output_field = models.CharField()

    def as_sqlite(self, compiler, connection, **extra_context):
        return self.as_sql(
            compiler,
            connection,
            template="replace(json_type(%(expressions)s), 'text', 'string')",
            **extra_context,
        )


def validate_json_object(value):
    """Require a JSON object, without imposing a competing SER-v1 wire schema."""
    if type(value) is not dict:
        raise ValidationError("Expected a JSON object.", code="invalid_json_object")


def _validate_bounded_json(value, limit):
    validate_json_object(value)
    try:
        serialized = json.dumps(value, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError, OverflowError):
        raise ValidationError(
            "Expected finite JSON-compatible data.", code="invalid_json"
        ) from None
    if len(serialized) > limit:
        raise ValidationError("JSON exceeds the storage character limit.", code="json_too_large")


def validate_attempt_configuration(value):
    """At most 16,384 serialized characters; store only non-sensitive configuration."""
    _validate_bounded_json(value, 16_384)


def validate_failure_diagnostics(value):
    """At most 4,096 serialized characters; callers must exclude secrets/raw records."""
    _validate_bounded_json(value, 4_096)


class StagingIngestion(TimeStampedModel):
    """Identity and declared lifecycle of one extraction, independent of PipelineRun.

    source_reference is an already sanitized opaque label (e.g. retail_oct.csv or
    public.raw_transactions), never a connection URL or filesystem path. This
    schema rejects unsafe reference syntax; it does not sanitize source content.
    Final counts belong only to COMPLETE; they are not calculated by the model.
    """

    class SourceKind(models.TextChoices):
        CSV = "CSV", "CSV"
        EXCEL = "EXCEL", "Excel"
        POSTGRES = "POSTGRES", "PostgreSQL"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        COMPLETE = "COMPLETE", "Complete"
        ABORTED = "ABORTED", "Aborted"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    source_kind = models.CharField(max_length=8, choices=SourceKind.choices)
    source_reference = models.CharField(
        max_length=255,
        validators=[RegexValidator(_REFERENCE_PATTERN, "Use a sanitized opaque source label.")],
        help_text="Credential-free label only; no URLs, absolute paths or query strings.",
    )
    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OPEN)
    initiated_by = models.ForeignKey(
        "accounts.User",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="staging_ingestions",
    )
    final_batch_count = models.PositiveBigIntegerField(null=True, blank=True)
    final_row_count = models.PositiveBigIntegerField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    aborted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=models.Q(source_kind__in=["CSV", "EXCEL", "POSTGRES"]),
                name="stg_ingestion_source_kind",
            ),
            models.CheckConstraint(
                condition=models.Q(source_reference__regex=_REFERENCE_PATTERN),
                name="stg_ingestion_source_reference",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="OPEN",
                        final_batch_count__isnull=True,
                        final_row_count__isnull=True,
                        completed_at__isnull=True,
                        aborted_at__isnull=True,
                    )
                    | models.Q(
                        status="COMPLETE",
                        final_batch_count__isnull=False,
                        final_row_count__isnull=False,
                        completed_at__isnull=False,
                        aborted_at__isnull=True,
                    )
                    | models.Q(
                        status="ABORTED",
                        final_batch_count__isnull=True,
                        final_row_count__isnull=True,
                        completed_at__isnull=True,
                        aborted_at__isnull=False,
                    )
                ),
                name="stg_ingestion_state",
            ),
        ]


class StagingBatch(models.Model):
    """SER-v1 header plus source provenance; records are stored positionally below.

    header is the exact encode_source_batch payload with only `records` removed,
    including the original index description and extraction metadata. Header and
    source_name use escaped JSON text, decoded back to dict/str by their fields;
    a source name is stored as a JSON string literal, including its quotes. Later loading
    code must check header/columns against these relational fields and the codec;
    this schema neither re-encodes data nor verifies hashes/aggregate row counts.
    """

    id = models.BigAutoField(primary_key=True)
    ingestion = models.ForeignKey(
        StagingIngestion,
        on_delete=models.PROTECT,
        related_name="batches",
        db_index=False,
    )
    batch_number = models.PositiveBigIntegerField()
    source_name = EscapedJSONTextField(json_kind="string")
    serializer_version = models.PositiveSmallIntegerField(default=1)
    header = EscapedJSONTextField(validators=[validate_json_object])
    row_count = models.PositiveBigIntegerField()
    content_hash = models.CharField(max_length=64, validators=[RegexValidator(_HASH_PATTERN)])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["ingestion", "batch_number"], name="stg_batch_number_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(serializer_version=1), name="stg_batch_ser_v1"
            ),
            models.CheckConstraint(
                condition=models.Q(content_hash__regex=_HASH_PATTERN), name="stg_batch_content_hash"
            ),
            models.CheckConstraint(
                condition=Exact(StoredJSONType("source_name"), models.Value("string")),
                name="stg_batch_source_name_json",
            ),
            models.CheckConstraint(
                condition=Exact(StoredJSONType("header"), models.Value("object")),
                name="stg_batch_header_json",
            ),
        ]


class StagingRecord(models.Model):
    """One original encoded SER-v1 record object, including index_label and values.

    row_position is its source identity within a batch; business duplicates remain
    distinct. The original record uses escaped JSON text, decoded to a dict by the
    field. No warehouse columns, value normalization or deduplication are added.
    """

    id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(
        StagingBatch, on_delete=models.PROTECT, related_name="records", db_index=False
    )
    row_position = models.PositiveBigIntegerField()
    record_payload = EscapedJSONTextField(validators=[validate_json_object])

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["batch", "row_position"], name="stg_record_position_unique"
            ),
            models.UniqueConstraint(fields=["id", "batch"], name="stg_record_id_batch_unique"),
            models.CheckConstraint(
                condition=Exact(StoredJSONType("record_payload"), models.Value("object")),
                name="stg_record_payload_json",
            ),
        ]


class TransformationAttempt(models.Model):
    """Declared attempt state and reserved claim metadata, with no execution logic.

    FAILED may precede execution (started_at stays null). Failure metadata is optional
    and restricted to FAILED. Claim owner/expiry are paired, and occupied claims need
    a positive generation; expiry and ownership are not evaluated or advanced here.
    JSON bounds are characters of the stored database text (not bytes); model
    validation additionally checks the Python JSON representation. Backend JSON
    rendering differences can make the database bound stricter at the boundary.
    """

    class Status(models.TextChoices):
        REGISTERED = "REGISTERED", "Registered"
        RUNNING = "RUNNING", "Running"
        SUCCEEDED = "SUCCEEDED", "Succeeded"
        FAILED = "FAILED", "Failed"

    id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(
        StagingBatch, on_delete=models.PROTECT, related_name="attempts", db_index=False
    )
    attempt_key = models.UUIDField(default=uuid.uuid4, editable=False)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.REGISTERED)
    configuration_hash = models.CharField(max_length=64, validators=[RegexValidator(_HASH_PATTERN)])
    configuration = models.JSONField(
        default=dict, blank=True, validators=[validate_attempt_configuration]
    )
    transformation_version = models.CharField(max_length=128)
    claim_generation = models.PositiveBigIntegerField(default=0)
    claim_owner = models.CharField(max_length=128, null=True, blank=True)
    claim_expires_at = models.DateTimeField(null=True, blank=True)
    safe_row_count = models.PositiveBigIntegerField(null=True, blank=True)
    unresolved_row_count = models.PositiveBigIntegerField(null=True, blank=True)
    failure_code = models.CharField(max_length=64, blank=True, default="")
    failure_stage = models.CharField(max_length=64, blank=True, default="")
    failure_diagnostics = models.JSONField(
        null=True, blank=True, validators=[validate_failure_diagnostics]
    )
    registered_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["batch", "attempt_key"], name="stg_attempt_key_unique"),
            models.UniqueConstraint(fields=["id", "batch"], name="stg_attempt_id_batch_unique"),
            models.CheckConstraint(
                condition=models.Q(configuration_hash__regex=_HASH_PATTERN),
                name="stg_attempt_config_hash",
            ),
            models.CheckConstraint(
                condition=LessThanOrEqual(
                    Length(Cast("configuration", models.TextField())), models.Value(16_384)
                ),
                name="stg_attempt_config_size",
            ),
            models.CheckConstraint(
                condition=LessThanOrEqual(
                    Length(Cast("failure_diagnostics", models.TextField())), models.Value(4_096)
                ),
                name="stg_attempt_failure_size",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(claim_owner__isnull=True, claim_expires_at__isnull=True)
                    | (
                        models.Q(
                            claim_owner__isnull=False,
                            claim_expires_at__isnull=False,
                            claim_generation__gte=1,
                        )
                        & ~models.Q(claim_owner="")
                    )
                ),
                name="stg_attempt_claim_pair",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        status="REGISTERED",
                        started_at__isnull=True,
                        finished_at__isnull=True,
                        safe_row_count__isnull=True,
                        unresolved_row_count__isnull=True,
                    )
                    | models.Q(
                        status="RUNNING",
                        started_at__isnull=False,
                        finished_at__isnull=True,
                        safe_row_count__isnull=True,
                        unresolved_row_count__isnull=True,
                    )
                    | models.Q(
                        status="SUCCEEDED",
                        started_at__isnull=False,
                        finished_at__isnull=False,
                        safe_row_count__isnull=False,
                        unresolved_row_count__isnull=False,
                    )
                    | models.Q(
                        status="FAILED",
                        finished_at__isnull=False,
                        safe_row_count__isnull=True,
                        unresolved_row_count__isnull=True,
                    )
                ),
                name="stg_attempt_state",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(status="FAILED")
                    | models.Q(failure_code="", failure_stage="", failure_diagnostics__isnull=True)
                ),
                name="stg_attempt_failure_state",
            ),
        ]


class TransformationRecordResult(models.Model):
    """An interpretation outcome, never a claim of validation or warehouse readiness.

    Composite foreign keys in migration 0002 enforce that both attempt and staged
    record belong to batch in PostgreSQL. Ordinary Django FKs alone cannot enforce
    that rule. SQLite tests exercise only the portable subset of these guarantees.
    """

    class Outcome(models.TextChoices):
        SAFE = "SAFE", "Safe"
        UNRESOLVED = "UNRESOLVED", "Unresolved"

    id = models.BigAutoField(primary_key=True)
    batch = models.ForeignKey(StagingBatch, on_delete=models.PROTECT, related_name="result_rows")
    attempt = models.ForeignKey(
        TransformationAttempt, on_delete=models.PROTECT, related_name="results", db_index=False
    )
    staged_record = models.ForeignKey(
        StagingRecord, on_delete=models.PROTECT, related_name="results"
    )
    outcome = models.CharField(max_length=10, choices=Outcome.choices)
    result_payload = models.JSONField(null=True, blank=True)
    interpretation_evidence = models.JSONField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "staged_record"], name="stg_result_record_unique"
            ),
            models.CheckConstraint(
                condition=models.Q(outcome__in=["SAFE", "UNRESOLVED"]), name="stg_result_outcome"
            ),
        ]
