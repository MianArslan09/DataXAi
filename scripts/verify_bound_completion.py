"""Owner-operated UCI proof experiment. See docs/etl-bound-completion.md.

Run as python -m scripts.verify_bound_completion. This harness never provisions,
migrates, cleans, or resets a database. Each invocation is a separate process.
"""

import argparse
import json
import logging
import re
import sys
from contextlib import ExitStack
from pathlib import Path
from time import perf_counter, process_time
from unittest.mock import patch
from uuid import UUID

from scripts.verify_staging_real_data import _database_guard, _dataset, _process_memory

# isort: split
# The shared benchmark module initializes Django before model imports.
from django.conf import settings
from django.db import connection
from django.test import override_settings

from etl import ingestion_completion as service
from etl.ingestion_runs import run_ingestion
from etl.models import IngestionCompletionProof, StagingIngestion
from etl.sources import ExcelSource
from etl.transforms.date_contract import SourceDateProfile

WORKBOOK = "online_retail_II.xlsx"
SHA = "bcbe73b35f5b7babf197fb0cb983a11f5d9ff929078d4aa53d171b1f2df2e980"
SHEETS = ["Year 2009-2010", "Year 2010-2011"]
ROWS, BATCHES, SIZE = 1_067_371, 22, 50_000
VERSION = "bound-uci-v1"


def require(condition):
    if not condition:
        raise ValueError("Verification precondition or reconciliation failed.")


class StageTimes(logging.Handler):
    def __init__(self):
        super().__init__()
        self.observations = {}

    def emit(self, record):
        if record.name == service.__name__:
            self.observations[record.msg] = {
                k: v for k, v in vars(record).items() if k.endswith("_seconds")
            }


class AfterTraversal(Exception):
    """Deliberate benchmark interruption, injected before the seal transaction."""


def interrupt(ingestion_id, binding_digest, observed):
    require((observed.rows, observed.batches) == (ROWS, BATCHES))
    raise AfterTraversal


def measure(args, report):
    require(args.authorize_benchmark_writes)
    require(re.fullmatch(r"dataxai_completion_[a-z0-9_]+", args.expected_db))
    _database_guard(args)
    source = args.mode in ("baseline", "fresh", "interrupt")
    require(not source or (args.path and args.path.is_file() and args.path.name == WORKBOOK))
    require(settings.ETL_SNAPSHOT_ROOT or args.mode == "baseline")
    if args.mode == "baseline":
        require(_dataset(args.path)["sha256"] == SHA)
    if source:
        ingestion = StagingIngestion.objects.create(
            pk=args.ingestion_id, source_kind="EXCEL", source_reference=WORKBOOK
        )
    else:
        existing = IngestionCompletionProof.objects.select_related("ingestion").get(
            pk=args.ingestion_id
        )
        require(
            existing.snapshot_sha256 == SHA
            and existing.execution_key == args.execution_key
            and existing.source_reference == WORKBOOK
            and existing.expected_rows == ROWS
            and existing.expected_batches == BATCHES
            and existing.batch_size == SIZE
            and existing.transformation_version == VERSION
            and existing.parser_recipe["sheets"] is None
            and (
                existing.ingestion.status == "OPEN"
                if args.mode == "recover"
                else existing.ingestion.status == "COMPLETE"
            )
        )
        ingestion = existing.ingestion
    options = dict(
        ingestion_id=ingestion.pk,
        profile=SourceDateProfile(),
        execution_key=args.execution_key,
        transformation_version=VERSION,
    )
    counts = {"statements": 0, "execute_wall_seconds": 0.0}

    def sql(execute, query, params, many, context):
        start = perf_counter()
        try:
            return execute(query, params, many, context)
        finally:
            counts["statements"] += 1
            counts["execute_wall_seconds"] += perf_counter() - start

    times = StageTimes()
    with ExitStack() as stack:
        stack.enter_context(override_settings(DEBUG=False))
        previous_debug_cursor = connection.force_debug_cursor
        connection.force_debug_cursor = False
        stack.callback(setattr, connection, "force_debug_cursor", previous_debug_cursor)
        stack.enter_context(connection.execute_wrapper(sql))
        stack.callback(service.logger.setLevel, service.logger.level)
        service.logger.setLevel(logging.INFO)
        service.logger.addHandler(times)
        stack.callback(service.logger.removeHandler, times)
        wall, cpu = perf_counter(), process_time()
        if args.mode == "baseline":
            summary = run_ingestion(
                **options, source=ExcelSource(args.path, batch_size=SIZE), claim_ttl_seconds=3600
            )
            require(
                summary.source_exhausted
                and (summary.successful_batches, summary.successful_rows) == (BATCHES, ROWS)
            )
            report.update(
                source_rows=summary.successful_rows,
                batches=summary.successful_batches,
                safe=summary.safe_rows,
                unresolved=summary.unresolved_rows,
                status="BASELINE_TRAVERSED",
            )
        else:
            if source:
                service.bind_file_ingestion(
                    **options,
                    input_path=args.path,
                    scope=service.ExcelScope(WORKBOOK, SIZE),
                    expected_sha256=SHA,
                    expected_batch_count=BATCHES,
                    expected_row_count=ROWS,
                )
            if args.mode == "interrupt":
                stack.enter_context(patch.object(service, "_seal", interrupt))
            try:
                proof = service.complete_bound_ingestion(
                    ingestion_id=ingestion.pk, claim_ttl_seconds=3600
                )
            except AfterTraversal:
                require(args.mode == "interrupt")
                ingestion.refresh_from_db()
                require(ingestion.status == "OPEN" and ingestion.completion_proof.sealed_at is None)
                report.update(status="INTERRUPTED_AFTER_TRAVERSAL")
            else:
                require(
                    args.mode != "interrupt"
                    and proof.source_rows == proof.staged_rows == ROWS
                    and proof.successful_batches == BATCHES
                    and proof.safe_rows + proof.unresolved_rows == ROWS
                    and [s["name"] for s in proof.coverage["sheets"]] == SHEETS
                )
                report.update(
                    status="VERIFIED",
                    source_rows=proof.source_rows,
                    batches=proof.source_batches,
                    safe=proof.safe_rows,
                    unresolved=proof.unresolved_rows,
                    coverage=proof.coverage,
                    receipt_digest=proof.receipt_digest,
                    manifest_digest=proof.manifest_digest,
                    sealed_at=proof.sealed_at.isoformat(),
                )
        report.update(
            elapsed_seconds=perf_counter() - wall,
            cpu_seconds=process_time() - cpu,
            sql=counts,
            timings=times.observations,
        )
    report["memory"] = _process_memory()
    if sys.platform == "linux":
        import resource

        report["memory"] = {
            "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024,
            "scope": "process lifetime",
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mode", choices=("baseline", "fresh", "interrupt", "recover", "ack"), required=True
    )
    parser.add_argument("--path", type=Path)
    parser.add_argument("--ingestion-id", type=UUID, required=True)
    parser.add_argument("--execution-key", type=UUID, required=True)
    parser.add_argument("--expected-db", required=True)
    parser.add_argument("--expected-host", required=True)
    parser.add_argument("--expected-port", type=int, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--authorize-benchmark-writes", action="store_true", required=True)
    args = parser.parse_args()
    require(not args.report.resolve().is_relative_to(settings.BASE_DIR))
    report = {
        "schema": "bound-uci-v1",
        "mode": args.mode,
        "ingestion": str(args.ingestion_id),
        "execution": str(args.execution_key),
        "dataset_expected_sha256": SHA,
    }
    with args.report.open("x", encoding="utf-8") as output:
        try:
            measure(args, report)
        except Exception as exc:
            report.update(status="FAILED", failure_type=type(exc).__name__)
        json.dump(report, output, indent=2, allow_nan=False)
    print(report["status"])
    return int(report["status"] == "FAILED")


if __name__ == "__main__":
    sys.exit(main())
