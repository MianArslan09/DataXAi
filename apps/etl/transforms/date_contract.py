"""Typed date evidence and batch accounting for Volume 4.3.3."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import pandas as pd

from etl.exceptions import ETLError
from etl.transforms.contract import TransformedBatch


class DateIssueCode(StrEnum):
    MISSING_DATE = "MISSING_DATE"
    INVALID_CALENDAR_DATE = "INVALID_CALENDAR_DATE"
    AMBIGUOUS_DATE_FORMAT = "AMBIGUOUS_DATE_FORMAT"
    UNSUPPORTED_DATE_FORMAT = "UNSUPPORTED_DATE_FORMAT"
    AMBIGUOUS_LOCAL_TIME = "AMBIGUOUS_LOCAL_TIME"
    NONEXISTENT_LOCAL_TIME = "NONEXISTENT_LOCAL_TIME"
    DATE_ORDER_CONFLICT = "DATE_ORDER_CONFLICT"
    DATE_ONLY = "DATE_ONLY"
    OUT_OF_RANGE = "OUT_OF_RANGE"


class TimezoneStatus(StrEnum):
    UTC_NORMALIZED = "UTC_NORMALIZED"
    TIMEZONE_UNKNOWN = "TIMEZONE_UNKNOWN"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class SourceDateProfile:
    """Caller-supplied source evidence; never inferred or learned from rows.

    date_order governs slash dates only. Explicit timestamp offsets take
    precedence over the timezone used to interpret naive source clocks.
    A configured profile requires a provenance description, not credentials.
    """

    name: str = "unconfigured"
    date_order: Literal["DMY", "MDY"] | None = None
    timezone: str | None = None
    provenance: str | None = None

    def __post_init__(self):
        if not isinstance(self.name, str) or not self.name.strip():
            raise ETLError("Invalid date profile: a nonblank name is required")
        if self.date_order not in (None, "DMY", "MDY"):
            raise ETLError(f"Invalid date profile {self.name!r}: date_order must be DMY or MDY")
        if self.provenance is not None and (
            not isinstance(self.provenance, str) or not self.provenance.strip()
        ):
            raise ETLError(f"Invalid date profile {self.name!r}: provenance must be nonblank")
        if (self.date_order is not None or self.timezone is not None) and not self.provenance:
            raise ETLError(
                f"Invalid date profile {self.name!r}: configured settings need provenance"
            )
        if self.timezone is not None:
            if not isinstance(self.timezone, str) or not self.timezone:
                raise ETLError(f"Invalid date profile {self.name!r}: timezone must be an IANA name")
            try:
                ZoneInfo(self.timezone)
            except (ZoneInfoNotFoundError, ValueError) as exc:
                raise ETLError(
                    f"Invalid date profile {self.name!r}: unknown IANA timezone"
                ) from exc


@dataclass(frozen=True, slots=True)
class SourceRow:
    """Position is zero-based within (source_name, batch_number), not index label."""

    source_name: str
    batch_number: int
    position: int
    index_label: object = field(repr=False)


@dataclass(frozen=True, slots=True)
class DateIssue:
    row: SourceRow
    code: DateIssueCode
    reason: str
    column: str = "invoice_date"


@dataclass(frozen=True, slots=True)
class DateEvidence:
    row: SourceRow
    original_value: object = field(repr=False)
    local_datetime: pd.Timestamp | None
    utc_datetime: pd.Timestamp | None
    timezone_status: TimezoneStatus
    timezone_origin: Literal["explicit_offset", "source_profile", "unspecified"]
    source_timezone: str | None

    def iso_value(self) -> str | None:
        """Output boundary: UTC uses Z; unknown local clocks have no suffix."""
        if self.timezone_status == TimezoneStatus.UNRESOLVED:
            return None
        if self.utc_datetime is not None:
            return self.utc_datetime.isoformat().removesuffix("+00:00") + "Z"
        return self.local_datetime.isoformat()


@dataclass(frozen=True, slots=True)
class DateInterpretationResult:
    """Safe typed/enriched rows and unresolved ORIGINAL alias-mapped rows.

    Safe invoice_date is an object column of Timestamp scalars: UTC-aware
    for known instants, naive for unknown zones. Evidence is in input order.
    The profile is shared provenance for this batch. DataFrames remain mutable,
    just as in TransformedBatch; consumers can recheck accounting explicitly.
    """

    safe_batch: TransformedBatch
    unresolved_batch: TransformedBatch
    safe_positions: tuple[int, ...]
    unresolved_positions: tuple[int, ...]
    issues: tuple[DateIssue, ...]
    date_evidence: tuple[DateEvidence, ...]
    profile: SourceDateProfile
    input_rows: int

    def __post_init__(self):
        self.check_accounting()

    def check_accounting(self) -> None:
        safe, unresolved = self.safe_positions, self.unresolved_positions
        if (
            len(self.safe_batch) != len(safe)
            or len(self.unresolved_batch) != len(unresolved)
            or tuple(sorted(safe)) != safe
            or tuple(sorted(unresolved)) != unresolved
            or sorted((*safe, *unresolved)) != list(range(self.input_rows))
            or tuple(e.row.position for e in self.date_evidence) != tuple(range(self.input_rows))
            or tuple(issue.row.position for issue in self.issues) != unresolved
        ):
            raise ETLError("Date interpretation result violates positional row accounting")
