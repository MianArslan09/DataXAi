"""Synchronous per-batch coordination. The caller designates a single live producer.

The empty-ingestion check does not reserve exclusive extraction rights. Successful
exhaustion describes this traversal only. Neither operation finalizes an ingestion.
"""

from contextlib import ExitStack
from dataclasses import dataclass, replace
from typing import Literal
from uuid import UUID

from django.db import DEFAULT_DB_ALIAS

from etl.attempt_config import _configuration, _identifier
from etl.exceptions import (
    AttemptIntegrityError,
    AttemptRegistrationError,
    StagingBatchNotFoundError,
    StagingIngestionError,
    StagingIntegrityError,
)
from etl.models import PipelineRun, StagingIngestion
from etl.pipeline_runs import _check_entry, run_staged_batch, stage_and_run_batch
from etl.sources import CsvSource, ExcelSource, PostgresSource, Source, SourceBatch
from etl.transforms.date_contract import SourceDateProfile


@dataclass(frozen=True, slots=True)
class IngestionExecutionSummary:
    ingestion_id: UUID
    execution_key: UUID
    operation: Literal["run_ingestion", "resume_staged_range"]
    requested_first_batch: int | None = None
    requested_last_batch: int | None = None
    successful_batches: int = 0
    successful_rows: int = 0
    safe_rows: int = 0
    unresolved_rows: int = 0
    source_exhausted: bool = False
    failed_batch_number: int | None = None
    failed_run_id: int | None = None

    def _record(self, number: int, run: PipelineRun):
        if run.status == PipelineRun.Status.FAILED:
            return replace(self, failed_batch_number=number, failed_run_id=run.pk)
        if run.status != PipelineRun.Status.SUCCEEDED:
            raise AttemptIntegrityError("Single-batch execution returned a nonterminal run.")
        attempt = run.attempt
        return replace(
            self,
            successful_batches=self.successful_batches + 1,
            successful_rows=self.successful_rows + run.records_ingested,
            safe_rows=self.safe_rows + attempt.safe_row_count,
            unresolved_rows=self.unresolved_rows + attempt.unresolved_row_count,
        )


def _request(ingestion_id, execution_key, profile, transformation_version, claim_ttl_seconds):
    _check_entry(claim_ttl_seconds)
    if type(ingestion_id) is not UUID or type(execution_key) is not UUID:
        raise AttemptRegistrationError("Ingestion and execution identities must be UUIDs.")
    _configuration(profile)
    _identifier(transformation_version, 128)
    try:
        ingestion = StagingIngestion.objects.using(DEFAULT_DB_ALIAS).get(pk=ingestion_id)
    except StagingIngestion.DoesNotExist:
        raise StagingIngestionError("The requested ingestion does not exist.") from None
    return ingestion, dict(
        ingestion_id=ingestion_id,
        attempt_key=execution_key,
        profile=profile,
        transformation_version=transformation_version,
        claim_ttl_seconds=claim_ttl_seconds,
    )


def _check_source(source, kind):
    # The current Source protocol has no public open-state or kind accessor.
    if isinstance(source, CsvSource):
        expected, opened = "CSV", source._reader is not None
    elif isinstance(source, ExcelSource):
        expected, opened = "EXCEL", source._workbook is not None
    elif isinstance(source, PostgresSource):
        expected, opened = "POSTGRES", source._engine is not None or source._connection is not None
    else:
        raise StagingIngestionError("Unsupported live source adapter.")
    if opened or kind != expected:
        raise StagingIngestionError("Live traversal requires a matching, unopened source.")


def _source_names(source):
    if isinstance(source, CsvSource):
        return {f"csv:{source.path.name}"}
    if isinstance(source, ExcelSource):
        return {f"xlsx:{source.path.name}:{sheet}" for sheet in source.sheet_names}
    return {f"postgres:{source.table}"}


def run_ingestion(
    *,
    ingestion_id: UUID,
    source: Source,
    execution_key: UUID,
    profile: SourceDateProfile,
    transformation_version: str,
    claim_ttl_seconds: int,
) -> IngestionExecutionSummary:
    """Traverse one unopened source from zero. An interruption preserves committed batches."""
    ingestion, options = _request(
        ingestion_id, execution_key, profile, transformation_version, claim_ttl_seconds
    )
    _check_source(source, ingestion.source_kind)
    batches = ingestion.batches.using(DEFAULT_DB_ALIAS)
    if ingestion.status != "OPEN" or batches.exists():
        raise StagingIngestionError("Live traversal requires a fresh OPEN ingestion.")
    summary = IngestionExecutionSummary(ingestion_id, execution_key, "run_ingestion")
    with ExitStack() as resources:
        # The supported adapters have idempotent exits. Register cleanup before
        # opening, since an adapter may acquire a handle then fail in __enter__.
        resources.push(source)
        opened = source.__enter__()
        names = _source_names(opened)
        for number, batch in enumerate(opened.extract()):
            if (
                type(batch) is not SourceBatch
                or type(batch.batch_number) is not int
                or batch.batch_number != number
                or type(batch.source_name) is not str
                or batch.source_name not in names
            ):
                raise StagingIntegrityError("Unexpected live batch number or source identity.")
            run = stage_and_run_batch(batch=batch, **options)
            summary = summary._record(number, run)
            if summary.failed_run_id is not None:
                return summary
            del batch
    if batches.count() != summary.successful_batches:
        raise StagingIntegrityError("Stored batch range differs from the traversed source.")
    return replace(summary, source_exhausted=True)


def resume_staged_range(
    *,
    ingestion_id: UUID,
    first_batch: int,
    last_batch: int,
    execution_key: UUID,
    profile: SourceDateProfile,
    transformation_version: str,
    claim_ttl_seconds: int,
) -> IngestionExecutionSummary:
    """Execute an inclusive staged range. A successful retry verifies the existing ledger."""
    if (
        type(first_batch) is not int
        or type(last_batch) is not int
        or not 0 <= first_batch <= last_batch < 2**63
    ):
        raise AttemptRegistrationError("Invalid inclusive staged batch range.")
    ingestion, options = _request(
        ingestion_id, execution_key, profile, transformation_version, claim_ttl_seconds
    )
    if ingestion.status not in ("OPEN", "COMPLETE"):
        raise StagingIngestionError("Staged continuation requires an OPEN or COMPLETE ingestion.")
    selected = ingestion.batches.using(DEFAULT_DB_ALIAS).filter(
        batch_number__range=(first_batch, last_batch)
    )
    if selected.count() != last_batch - first_batch + 1:
        raise StagingBatchNotFoundError("The requested staged range contains a missing batch.")
    summary = IngestionExecutionSummary(
        ingestion_id, execution_key, "resume_staged_range", first_batch, last_batch
    )
    for number in range(first_batch, last_batch + 1):
        run = run_staged_batch(batch_number=number, **options)
        summary = summary._record(number, run)
        if summary.failed_run_id is not None:
            break
    return summary
