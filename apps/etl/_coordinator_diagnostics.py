"""Local, best-effort coordinator observations; never transaction evidence.

Each boundary owns its observations. Setting stage to None delegates exception
reporting to an instrumented child, without propagating context or marking errors.
Only catalog descriptors and strictly typed in-memory identities leave this file.
"""

import json
import logging
from dataclasses import replace
from uuid import UUID

_OPERATIONS = frozenset(
    {
        "stage_and_run_batch",
        "run_staged_batch",
        "run_ingestion",
        "resume_staged_range",
        "bind_file_ingestion",
        "complete_bound_ingestion",
    }
)
_STAGES = frozenset(
    {
        "admission",
        "snapshot_capture",
        "binding",
        "source_read",
        "source_cleanup",
        "staging",
        "reconstruction",
        "claiming",
        "interpretation",
        "finalization",
        "sealing",
        "acknowledgment",
    }
)


def safe_log(logger, level, event, *, extra):
    """Call with controlled text/extras only. A sink must never change ETL outcomes."""
    try:
        logger.log(level, event, extra=extra)
    except BaseException:
        # Even a sink interruption must not replace the processing exception or
        # turn a committed operation into an apparent failure. No fallback sink.
        pass


class CoordinatorDiagnostic:
    """One local boundary; no ORM access, global state or automatic recovery."""

    def __init__(self, logger, operation, ingestion_id, execution_key=None, batch_number=None):
        self.logger = logger
        self.operation = operation
        self.ingestion_id = ingestion_id
        self.execution_key = execution_key
        self.batch_number = batch_number
        self.attempt_id = self.pipeline_run_id = None
        self.stage = "admission"
        self._failed_result = False
        self._failed_error = None

    def __enter__(self):
        return self

    def record_run(self, run):
        # Optional diagnostics must not access deferred fields or related models.
        try:
            loaded = vars(run)
            self.pipeline_run_id = loaded.get("id")
            self.attempt_id = loaded.get("attempt_id", self.attempt_id)
        except BaseException:
            pass

    def failed_result(self, *, error=None):
        # The caller has observed FAILED, not necessarily created it. An optional
        # exact error identity distinguishes the bound stop from cleanup errors.
        self._failed_result = True
        self._failed_error = error

    def __exit__(self, exc_type, exc, traceback):
        if self.stage is None or (exc is None and not self._failed_result):
            return False
        try:
            # Lazy imports allow the source adapters to use safe_log without
            # creating a classifier -> snapshot -> source import cycle.
            from etl.error_handling import (
                FailureCategory,
                FailureReason,
                RecoveryGuidance,
                classify_etl_failure,
            )
            from etl.exceptions import AttemptConflictError

            failure = classify_etl_failure(exc if exc is not None else AttemptConflictError())
            if self._failed_result and exc is self._failed_error:
                failure = replace(
                    failure,
                    category=FailureCategory.CONFLICT,
                    reason=FailureReason.TERMINAL_ATTEMPT_FAILED,
                    message="A terminal failed attempt was observed. Operator review is required.",
                    recovery=RecoveryGuidance.OPERATOR_REVIEW,
                )
            fields = failure.as_dict()
            fields["operation"] = (
                self.operation
                if type(self.operation) is str and self.operation in _OPERATIONS
                else "unknown"
            )
            fields["stage"] = (
                self.stage if type(self.stage) is str and self.stage in _STAGES else "unknown"
            )
            fields["observation"] = "exception" if exc is not None else "failed_result"
            for key in ("ingestion_id", "execution_key"):
                value = getattr(self, key)
                if type(value) is UUID:
                    fields[key] = str(value)
            for key in ("batch_number", "attempt_id", "pipeline_run_id"):
                value = getattr(self, key)
                minimum = 0 if key == "batch_number" else 1
                if type(value) is int and minimum <= value < 2**63:
                    fields[key] = value
            # Ordinary formatters render only the message. Serialize this same
            # allowlisted projection, never the exception or diagnostic object's dict.
            rendered = json.dumps(fields, sort_keys=True, ensure_ascii=True, allow_nan=False)
            safe_log(
                self.logger,
                logging.WARNING,
                "etl.coordinator_failure " + rendered,
                extra={"etl": fields},
            )
        except BaseException:
            # Projection itself is observational too; never format the raw error.
            pass
        return False
