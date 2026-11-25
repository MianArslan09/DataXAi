"""Bind a fresh ingestion to a retained file and seal only verified parser scope.

The caller designates one producer. Committed staging and successful terminal
ledgers are immutable through the accepted services. Recovery starts at byte zero.
"""

import codecs
import hashlib
import io
import json
import logging
import platform
import re
from dataclasses import dataclass
from datetime import UTC
from pathlib import Path
from time import perf_counter
from uuid import UUID

import numpy
import openpyxl
import pandas
from django.db import DEFAULT_DB_ALIAS, connections, transaction
from django.utils import timezone

from etl.attempt_config import _configuration, _hash, _identifier, _stored_profile, _text
from etl.exceptions import StagingIngestionError, StagingIntegrityError
from etl.models import IngestionCompletionProof, PipelineRun, StagingIngestion
from etl.pipeline_runs import _check_entry, run_staged_batch
from etl.source_snapshots import (
    SourceSnapshot,
    _validate_options,
    capture_snapshot,
    open_snapshot_csv,
    open_snapshot_excel,
)
from etl.sources.contract import SourceBatch
from etl.staging import _stage_with_digest
from etl.transforms.date_contract import SourceDateProfile

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class CsvScope:
    filename: str
    batch_size: int = 50_000
    encoding: str = "utf-8"


@dataclass(frozen=True, slots=True)
class ExcelScope:
    filename: str
    batch_size: int = 50_000
    # None selects every worksheet in the immutable workbook's stored order.
    sheet_names: tuple[str, ...] | None = None


def _require(condition, message):
    if not condition:
        raise StagingIntegrityError(message)


def _count(value):
    return type(value) is int and 0 <= value < 2**63


def _sheets(names):
    _require(type(names) is tuple and 1 <= len(names) <= 128, "Unsupported worksheet scope.")
    for name in names:
        _text(name, 31)
        _require(not re.search(r"[\\/:*?\[\]]", name), "Invalid worksheet name.")
    _require(len(set(names)) == len(names), "Duplicate selected worksheet.")


def _recipe(scope):
    _require(type(scope) in (CsvScope, ExcelScope), "A CSV or XLSX scope is required.")
    csv = type(scope) is CsvScope
    _validate_options(scope.filename, ".csv" if csv else ".xlsx", scope.batch_size)
    _require(
        re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_. -]{0,254}", scope.filename),
        "A sanitized logical filename is required.",
    )
    _require(scope.batch_size < 2**31, "Batch size exceeds the supported range.")
    recipe = {
        "contract": "file-parser-v1",
        "python": platform.python_version(),
        "pandas": pandas.__version__,
        "numpy": numpy.__version__,
    }
    if csv:
        _text(scope.encoding, 64)
        try:
            encoding = codecs.lookup(scope.encoding).name
            with io.TextIOWrapper(io.BytesIO(), encoding=encoding):
                pass
        except (LookupError, UnicodeError):
            raise StagingIngestionError("Unsupported CSV encoding.") from None
        recipe.update(encoding=encoding, header="infer", delimiter=",", engine="pandas-c-defaults")
    else:
        if scope.sheet_names is not None:
            _sheets(scope.sheet_names)
        recipe.update(
            openpyxl=openpyxl.__version__,
            sheets=None if scope.sheet_names is None else list(scope.sheet_names),
            read_only=True,
            data_only=True,
            header="first-row",
        )
    return recipe


def _bytes(value):
    return json.dumps(
        value, sort_keys=True, ensure_ascii=True, separators=(",", ":"), allow_nan=False
    ).encode("ascii")


def _digest(value):
    return hashlib.sha256(_bytes(value)).hexdigest()


def _stamp(value):
    return value.astimezone(UTC).isoformat(timespec="microseconds")


_BINDING = (
    "proof_version",
    "source_kind",
    "source_reference",
    "snapshot_sha256",
    "snapshot_size",
    "parser_recipe",
    "batch_size",
    "transformation_version",
    "configuration",
    "configuration_hash",
    "expected_batches",
    "expected_rows",
)


def _binding_digest(proof):
    return _digest(
        {
            **{key: getattr(proof, key) for key in _BINDING},
            "ingestion": str(proof.ingestion_id),
            "execution": str(proof.execution_key),
            "bound_at": _stamp(proof.bound_at),
        }
    )


def _verify_binding(ingestion, proof):
    _require(
        proof.proof_version == 1
        and ingestion.pk == proof.ingestion_id
        and ingestion.source_kind == proof.source_kind in ("CSV", "EXCEL")
        and ingestion.source_reference == proof.source_reference
        and _binding_digest(proof) == proof.binding_digest,
        "Stored ingestion binding is inconsistent.",
    )
    snapshot = SourceSnapshot(proof.snapshot_sha256, proof.snapshot_size)
    recipe = proof.parser_recipe
    _require(type(recipe) is dict, "Invalid parser recipe.")
    if proof.source_kind == "CSV":
        scope = CsvScope(proof.source_reference, proof.batch_size, recipe.get("encoding"))
    else:
        sheets = recipe.get("sheets")
        _require(sheets is None or type(sheets) is list, "Invalid bound worksheet scope.")
        scope = ExcelScope(
            proof.source_reference, proof.batch_size, tuple(sheets) if sheets is not None else None
        )
    _require(_bytes(_recipe(scope)) == _bytes(recipe), "Incompatible parser recipe or runtime.")
    return snapshot, scope, _stored_profile(proof)


def _locked_ingestion(ingestion_id):
    try:
        return StagingIngestion.objects.select_for_update().get(pk=ingestion_id)
    except StagingIngestion.DoesNotExist:
        raise StagingIngestionError("Ingestion does not exist.") from None


def bind_file_ingestion(
    *,
    ingestion_id: UUID,
    input_path: Path,
    scope: CsvScope | ExcelScope,
    execution_key: UUID,
    profile: SourceDateProfile,
    transformation_version: str,
    expected_sha256: str | None = None,
    expected_batch_count: int | None = None,
    expected_row_count: int | None = None,
) -> IngestionCompletionProof:
    """Capture under publisher custody, then bind under the staging writer's parent lock."""
    connection = connections[DEFAULT_DB_ALIAS]
    _require(
        connection.vendor == "postgresql"
        and not connection.in_atomic_block
        and connection.get_autocommit(),
        "Binding requires PostgreSQL outermost autocommit.",
    )
    _require(
        type(ingestion_id) is UUID and type(execution_key) is UUID, "Expected UUID identities."
    )
    _require(
        all(n is None or _count(n) for n in (expected_batch_count, expected_row_count)),
        "Invalid declared source counts.",
    )
    if expected_sha256 is not None:
        SourceSnapshot(expected_sha256, 0)
    configuration = _configuration(profile)
    version = _identifier(transformation_version, 128)
    recipe = _recipe(scope)
    started = perf_counter()
    snapshot = capture_snapshot(input_path)
    logger.info("completion.snapshot_capture", extra={"elapsed_seconds": perf_counter() - started})
    _require(
        expected_sha256 is None or expected_sha256 == snapshot.sha256,
        "Input differs from the declared snapshot.",
    )
    values = dict(
        proof_version=1,
        source_kind="CSV" if type(scope) is CsvScope else "EXCEL",
        source_reference=scope.filename,
        snapshot_sha256=snapshot.sha256,
        snapshot_size=snapshot.size_bytes,
        parser_recipe=recipe,
        batch_size=scope.batch_size,
        execution_key=execution_key,
        configuration=configuration,
        configuration_hash=_hash(configuration),
        transformation_version=version,
        expected_batches=expected_batch_count,
        expected_rows=expected_row_count,
    )
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        ingestion = _locked_ingestion(ingestion_id)
        _require(
            ingestion.status == "OPEN"
            and ingestion.source_kind == values["source_kind"]
            and ingestion.source_reference == scope.filename,
            "Ingestion is ineligible for binding.",
        )
        proof = IngestionCompletionProof.objects.select_for_update().filter(pk=ingestion_id).first()
        if proof is not None:
            _verify_binding(ingestion, proof)
            _require(
                proof.sealed_at is None and all(getattr(proof, k) == v for k, v in values.items()),
                "Conflicting ingestion binding.",
            )
            return proof
        _require(
            not ingestion.batches.exists(), "Cannot bind an ingestion after staging has started."
        )
        proof = IngestionCompletionProof(ingestion=ingestion, bound_at=timezone.now(), **values)
        proof.binding_digest = _binding_digest(proof)
        proof.save(force_insert=True, using=DEFAULT_DB_ALIAS)
        return proof


class _Manifest:
    def __init__(self):
        self._hash = hashlib.sha256(b"dataxai:ordered-staged-batches:v1\0")
        self.batches = self.rows = 0

    def add(self, number, name, rows, content_hash):
        _require(
            type(number) is int and number == self.batches and _count(rows),
            "Noncontiguous or invalid source manifest.",
        )
        entry = _bytes([number, name, rows, content_hash])
        self._hash.update(len(entry).to_bytes(8, "big"))
        self._hash.update(entry)
        self.batches += 1
        self.rows += rows

    @property
    def digest(self):
        return self._hash.hexdigest()


# Each lateral aggregate returns one row, including for an empty batch. The
# composite FKs and unique (attempt, staged_record) enforce positional membership.
# A single SQL snapshot covers all selected attempts and projections. No header,
# row payload, DataFrame or interpretation evidence leaves PostgreSQL here.
_RECONCILE = """
 SELECT b.batch_number, b.source_name, b.row_count, b.content_hash,
        s.n, s.first_pos, s.last_pos, r.safe, r.unresolved, h.source_name,
        (b.serializer_version=1 AND h.version::text='1'
         AND h.row_count::text=b.row_count::text AND h.batch_number::text=b.batch_number::text
         AND a.status='SUCCEEDED'
         AND a.transformation_version=%s AND a.configuration_hash=%s
         AND a.configuration=%s::jsonb AND a.claim_owner IS NULL AND a.claim_expires_at IS NULL
         AND a.safe_row_count=r.safe AND a.unresolved_row_count=r.unresolved
         AND r.n=b.row_count AND r.invalid=0
         AND p.status='succeeded' AND p.records_ingested=b.row_count
         AND p.started_at=a.started_at AND p.finished_at=a.finished_at
         AND p.triggered_by_id IS NOT DISTINCT FROM i.initiated_by_id
         AND p.records_healed=0 AND p.records_quarantined=0
         AND p.fault_counts='{}'::jsonb AND p.cdqi_scores='{}'::jsonb) AS verified
   FROM etl_stagingbatch b
   JOIN etl_stagingingestion i ON i.id=b.ingestion_id
   CROSS JOIN LATERAL json_to_record(
       CASE WHEN b.source_name=ANY(%s::text[]) THEN b.header::json ELSE '{}'::json END
   ) AS h(version json, row_count json, batch_number json, source_name text)
   LEFT JOIN etl_transformationattempt a ON a.batch_id=b.id AND a.attempt_key=%s
   LEFT JOIN etl_pipelinerun p ON p.attempt_id=a.id
   CROSS JOIN LATERAL (
       SELECT count(*) n, min(row_position) first_pos, max(row_position) last_pos
       FROM etl_stagingrecord WHERE batch_id=b.id
   ) s
   CROSS JOIN LATERAL (
       SELECT count(*) n, count(*) FILTER (WHERE outcome='SAFE') safe,
              count(*) FILTER (WHERE outcome='UNRESOLVED') unresolved,
              count(*) FILTER (WHERE result_payload IS NOT NULL OR batch_id<>b.id) invalid
       FROM etl_transformationrecordresult WHERE attempt_id=a.id
   ) r
  WHERE b.ingestion_id=%s ORDER BY b.batch_number
"""


def _receipt_digest(proof):
    return _digest(
        {
            "binding": proof.binding_digest,
            "sealed_at": _stamp(proof.sealed_at),
            **{
                key: getattr(proof, key)
                for key in (
                    "source_batches",
                    "source_rows",
                    "staged_batches",
                    "staged_rows",
                    "successful_batches",
                    "safe_rows",
                    "unresolved_rows",
                    "manifest_digest",
                    "coverage",
                )
            },
        }
    )


@dataclass(frozen=True)
class _Observation:
    batches: int
    rows: int
    digest: str
    coverage: dict


def _coverage_names(scope, coverage):
    if type(scope) is CsvScope:
        _require(
            coverage == {"csv_header": "verified", "exhaustion": "natural"}, "Invalid CSV coverage."
        )
        return {f"csv:{scope.filename}": 0}
    _require(
        type(coverage) is dict
        and set(coverage) == {"sheets", "selection", "exhaustion"}
        and coverage["exhaustion"] == "natural"
        and coverage["selection"] == ("all" if scope.sheet_names is None else "selected"),
        "Invalid Excel coverage.",
    )
    entries = coverage["sheets"]
    _require(type(entries) is list and 1 <= len(entries) <= 128, "Invalid sheet receipts.")
    for e in entries:
        _require(
            type(e) is dict
            and set(e) == {"name", "header", "rows", "batches"}
            and e["header"] == "verified"
            and _count(e["rows"])
            and _count(e["batches"]),
            "Invalid worksheet receipt.",
        )
    sheets = tuple(e["name"] for e in entries)
    _sheets(sheets)
    _require(
        scope.sheet_names is None or scope.sheet_names == sheets, "Worksheet selection changed."
    )
    return {f"xlsx:{scope.filename}:{sheet}": i for i, sheet in enumerate(sheets)}


def _reconcile(proof, scope, observed):
    names = _coverage_names(scope, observed.coverage)
    per_sheet = [[0, 0] for _ in names]
    manifest = _Manifest()
    safe = unresolved = last_scope = 0
    params = (
        proof.transformation_version,
        proof.configuration_hash,
        _bytes(proof.configuration).decode("ascii"),
        [json.dumps(name) for name in names],
        proof.execution_key,
        proof.pk,
    )
    with connections[DEFAULT_DB_ALIAS].chunked_cursor() as cursor:
        cursor.execute(_RECONCILE, params)
        while rows := cursor.fetchmany(256):
            for (
                number,
                encoded_name,
                count,
                digest,
                n,
                first,
                last,
                good,
                bad,
                header_name,
                verified,
            ) in rows:
                name = json.loads(encoded_name)
                _require(
                    name in names and name == header_name and names[name] >= last_scope,
                    "Unexpected staged source scope.",
                )
                last_scope = names[name]
                _require(
                    verified is True
                    and count == n == good + bad
                    and (first, last) == ((0, count - 1) if count else (None, None)),
                    "Staged positions, selected attempt, run or outcome coverage disagree.",
                )
                manifest.add(number, name, count, digest)
                per_sheet[last_scope][0] += count
                per_sheet[last_scope][1] += 1
                safe += good
                unresolved += bad
    if type(scope) is ExcelScope:
        _require(
            per_sheet == [[e["rows"], e["batches"]] for e in observed.coverage["sheets"]],
            "Staged worksheet accounting disagrees with traversal.",
        )
    _require(
        (manifest.batches, manifest.rows, manifest.digest)
        == (observed.batches, observed.rows, observed.digest),
        "Ordered staging manifest disagrees with source.",
    )
    _require(
        proof.expected_batches in (None, manifest.batches)
        and proof.expected_rows in (None, manifest.rows),
        "Traversed counts disagree with the independent declaration.",
    )
    return dict(
        source_batches=observed.batches,
        source_rows=observed.rows,
        staged_batches=manifest.batches,
        staged_rows=manifest.rows,
        successful_batches=manifest.batches,
        safe_rows=safe,
        unresolved_rows=unresolved,
        manifest_digest=manifest.digest,
        coverage=observed.coverage,
    )


def _seal(ingestion_id, binding_digest, observed):
    before_lock = perf_counter()
    with transaction.atomic(using=DEFAULT_DB_ALIAS):
        ingestion = _locked_ingestion(ingestion_id)
        locked_at = perf_counter()
        proof = IngestionCompletionProof.objects.select_for_update().get(pk=ingestion_id)
        _, scope, _ = _verify_binding(ingestion, proof)
        _require(proof.binding_digest == binding_digest, "Binding changed during traversal.")
        if ingestion.status == "COMPLETE":
            _require(
                proof.sealed_at is not None
                and proof.sealed_at == ingestion.completed_at
                and proof.receipt_digest == _receipt_digest(proof),
                "Invalid sealed completion receipt.",
            )
            stored = _Observation(
                proof.source_batches, proof.source_rows, proof.manifest_digest, proof.coverage
            )
            _require(observed is None or observed == stored, "Conflicting completion evidence.")
            counts = _reconcile(proof, scope, stored)
            _require(
                all(getattr(proof, k) == v for k, v in counts.items())
                and (ingestion.final_batch_count, ingestion.final_row_count)
                == (stored.batches, stored.rows),
                "Committed receipt disagrees with ingestion accounting.",
            )
        else:
            _require(
                ingestion.status == "OPEN" and proof.sealed_at is None and observed is not None,
                "Ingestion is ineligible for sealing.",
            )
            counts = _reconcile(proof, scope, observed)
            for field, value in counts.items():
                setattr(proof, field, value)
            proof.sealed_at = timezone.now()
            proof.receipt_digest = _receipt_digest(proof)
            proof.save(update_fields=[*counts, "sealed_at", "receipt_digest"])
            ingestion.status = "COMPLETE"
            ingestion.final_batch_count, ingestion.final_row_count = observed.batches, observed.rows
            ingestion.completed_at = proof.sealed_at
            ingestion.save(
                update_fields=[
                    "status",
                    "final_batch_count",
                    "final_row_count",
                    "completed_at",
                    "updated_at",
                ]
            )
    logger.info(
        "completion.finalize",
        extra={
            "lock_wait_seconds": locked_at - before_lock,
            "lock_through_commit_seconds": perf_counter() - locked_at,
        },
    )
    return proof


def complete_bound_ingestion(
    *, ingestion_id: UUID, claim_ttl_seconds: int
) -> IngestionCompletionProof:
    """Traverse from zero and seal, or verify an existing receipt without opening the source."""
    _check_entry(claim_ttl_seconds)
    _require(type(ingestion_id) is UUID, "Expected a UUID ingestion identity.")
    try:
        proof = IngestionCompletionProof.objects.select_related("ingestion").get(pk=ingestion_id)
    except IngestionCompletionProof.DoesNotExist:
        raise StagingIngestionError("Ingestion has no file binding.") from None
    snapshot, scope, profile = _verify_binding(proof.ingestion, proof)
    if proof.ingestion.status == "COMPLETE":
        return _seal(ingestion_id, proof.binding_digest, None)
    _require(
        proof.ingestion.status == "OPEN" and proof.sealed_at is None,
        "Ingestion cannot be traversed.",
    )
    csv = type(scope) is CsvScope
    parser = (
        open_snapshot_csv(
            snapshot, filename=scope.filename, batch_size=scope.batch_size, encoding=scope.encoding
        )
        if csv
        else open_snapshot_excel(
            snapshot,
            filename=scope.filename,
            batch_size=scope.batch_size,
            sheet_names=scope.sheet_names,
        )
    )
    manifest = _Manifest()
    processing = 0.0
    started = perf_counter()
    with parser as traversal:
        if csv:
            names = {f"csv:{scope.filename}"}
        else:
            _sheets(traversal.selected_sheets)
            names = {f"xlsx:{scope.filename}:{name}" for name in traversal.selected_sheets}
        for batch in traversal:
            _require(
                type(batch) is SourceBatch
                and type(batch.batch_number) is int
                and batch.batch_number == manifest.batches
                and batch.source_name in names,
                "Unexpected batch source or sequence.",
            )
            timer = perf_counter()
            staged, digest = _stage_with_digest(ingestion_id=ingestion_id, batch=batch)
            manifest.add(batch.batch_number, batch.source_name, len(batch), digest)
            run = run_staged_batch(
                ingestion_id=ingestion_id,
                batch_number=batch.batch_number,
                attempt_key=proof.execution_key,
                profile=profile,
                transformation_version=proof.transformation_version,
                claim_ttl_seconds=claim_ttl_seconds,
            )
            _require(
                run.status == PipelineRun.Status.SUCCEEDED
                and run.records_ingested == staged.row_count == len(batch),
                "Bound execution did not successfully interpret the staged batch.",
            )
            processing += perf_counter() - timer
            del batch
    _require(traversal.verified_exhausted, "Snapshot traversal was not verified.")
    if csv:
        coverage = {"csv_header": "verified", "exhaustion": "natural"}
    else:
        coverage = {
            "selection": "all" if scope.sheet_names is None else "selected",
            "exhaustion": "natural",
            "sheets": [
                dict(name=name, header="verified", rows=r, batches=b)
                for name, (r, b) in zip(
                    traversal.completed_sheets, traversal.sheet_counts, strict=True
                )
            ],
        }
    logger.info(
        "completion.traversal",
        extra={
            "inclusive_seconds": perf_counter() - started,
            "processing_seconds": processing,
            "precheck_seconds": traversal.precheck_seconds,
            "postcheck_seconds": traversal.postcheck_seconds,
        },
    )
    return _seal(
        ingestion_id,
        proof.binding_digest,
        _Observation(manifest.batches, manifest.rows, manifest.digest, coverage),
    )
