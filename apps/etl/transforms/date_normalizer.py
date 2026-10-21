"""Bounded date interpretation plus the unchanged TypeConverter/BusinessRules.

Supported text grammar is deliberately explicit; pandas is never asked to guess
a string format. See docs/DATE_NORMALIZATION.md for profile and bridge semantics.
"""

import re
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from etl.exceptions import AmbiguousColumnsError, ETLError, MissingColumnsError
from etl.transforms.business_rules import BusinessRules
from etl.transforms.contract import TARGET_COLUMNS, TransformedBatch
from etl.transforms.date_contract import (
    DateEvidence,
    DateInterpretationResult,
    DateIssue,
    DateIssueCode,
    SourceDateProfile,
    SourceRow,
    TimezoneStatus,
)
from etl.transforms.type_converter import TypeConverter

_TIME = (
    r"(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})"
    r"(?::(?P<second>[0-9]{2})(?:\.(?P<fraction>[0-9]{1,9}))?)?"
)
_ISO = re.compile(
    r"(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})"
    rf"(?:[T ]{_TIME}(?P<offset>Z|[+-][0-9]{{2}}:?[0-9]{{2}})?)?"
)
_SLASH = re.compile(
    rf"(?P<first>[0-9]{{2}})/(?P<last>[0-9]{{2}})/(?P<year>[0-9]{{4}})(?: {_TIME})?"
)


class _DateProblem(Exception):
    def __init__(self, code: DateIssueCode, reason: str):
        self.code = code
        self.reason = reason


def _calendar(year, month, day):
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _parse_text(value: str, profile: SourceDateProfile) -> pd.Timestamp:
    match = _ISO.fullmatch(value)
    if match:
        parts = match.groupdict()
        calendar = _calendar(int(parts["year"]), int(parts["month"]), int(parts["day"]))
        if calendar is None:
            raise _DateProblem(DateIssueCode.INVALID_CALENDAR_DATE, "Invalid calendar date")
    else:
        match = _SLASH.fullmatch(value)
        if not match:
            raise _DateProblem(
                DateIssueCode.UNSUPPORTED_DATE_FORMAT, "Value is outside the supported date grammar"
            )
        parts = match.groupdict()
        year, first, last = (int(parts[key]) for key in ("year", "first", "last"))
        dmy, mdy = _calendar(year, last, first), _calendar(year, first, last)
        if dmy is None and mdy is None:
            raise _DateProblem(DateIssueCode.INVALID_CALENDAR_DATE, "Invalid calendar date")
        if profile.date_order:
            calendar = dmy if profile.date_order == "DMY" else mdy
            if calendar is None:
                raise _DateProblem(
                    DateIssueCode.DATE_ORDER_CONFLICT, "Slash date conflicts with source date_order"
                )
        elif dmy is not None and mdy is not None and dmy != mdy:
            raise _DateProblem(
                DateIssueCode.AMBIGUOUS_DATE_FORMAT, "Slash date needs explicit source date_order"
            )
        else:
            calendar = dmy or mdy

    if parts["hour"] is None:
        raise _DateProblem(
            DateIssueCode.DATE_ONLY, "Date has no transaction time; midnight not assumed"
        )
    fraction = (parts["fraction"] or "").ljust(9, "0")
    try:
        native = datetime(
            calendar.year,
            calendar.month,
            calendar.day,
            int(parts["hour"]),
            int(parts["minute"]),
            int(parts["second"] or 0),
            int(fraction[:6]),
        )
    except ValueError as exc:
        raise _DateProblem(DateIssueCode.INVALID_CALENDAR_DATE, "Invalid clock time") from exc

    offset = parts.get("offset")
    if offset:
        if offset == "Z":
            zone = timezone.utc
        else:
            digits = offset[1:].replace(":", "")
            hours, minutes = int(digits[:2]), int(digits[2:])
            if hours > 23 or minutes > 59 or (offset[0] == "-" and hours == minutes == 0):
                raise _DateProblem(
                    DateIssueCode.UNSUPPORTED_DATE_FORMAT,
                    "Invalid or explicitly unknown UTC offset",
                )
            delta = timedelta(hours=hours, minutes=minutes)
            zone = timezone(delta if offset[0] == "+" else -delta)
        native = native.replace(tzinfo=zone)
    # Construct the full precision at once: truncating to microseconds first
    # would put Timestamp.min just outside the representable nanosecond range.
    return pd.Timestamp(
        year=native.year,
        month=native.month,
        day=native.day,
        hour=native.hour,
        minute=native.minute,
        second=native.second,
        microsecond=native.microsecond,
        nanosecond=int(fraction[6:]),
        tzinfo=native.tzinfo,
    ).as_unit("ns", round_ok=False)


def _parse(value, profile: SourceDateProfile) -> pd.Timestamp:
    if value is None or value is pd.NA or value is pd.NaT:
        raise _DateProblem(DateIssueCode.MISSING_DATE, "Timestamp is missing")
    if isinstance(value, Decimal) and value.is_qnan():
        raise _DateProblem(DateIssueCode.MISSING_DATE, "Timestamp is missing")
    if isinstance(value, (float, np.floating)) and np.isnan(value):
        raise _DateProblem(DateIssueCode.MISSING_DATE, "Timestamp is missing")
    if isinstance(value, str):
        if not value or value.isspace():
            raise _DateProblem(DateIssueCode.MISSING_DATE, "Timestamp is blank")
        return _parse_text(value, profile)
    if isinstance(value, np.datetime64):
        if np.isnat(value):
            raise _DateProblem(DateIssueCode.MISSING_DATE, "Timestamp is missing")
        unit = np.datetime_data(value.dtype)[0]
        if unit in ("Y", "M", "W", "D"):
            raise _DateProblem(DateIssueCode.DATE_ONLY, "NumPy date has no transaction time")
        if unit in ("ps", "fs", "as"):
            raise _DateProblem(
                DateIssueCode.UNSUPPORTED_DATE_FORMAT, "Sub-nanosecond precision is not supported"
            )
        return pd.Timestamp(value).as_unit("ns", round_ok=False)
    if isinstance(value, datetime):
        return pd.Timestamp(value).as_unit("ns", round_ok=False)
    if isinstance(value, date):
        raise _DateProblem(
            DateIssueCode.DATE_ONLY, "Date has no transaction time; midnight not assumed"
        )
    raise _DateProblem(
        DateIssueCode.UNSUPPORTED_DATE_FORMAT, "Expected a supported timestamp string or datetime"
    )


def _local_to_utc(local: pd.Timestamp, zone: ZoneInfo) -> pd.Timestamp:
    # Round trips detect DST gaps/folds without guessing or pandas version-specific
    # localization defaults. Microseconds suffice for offset selection; arithmetic
    # on the original Timestamp retains all nanoseconds.
    native = local.to_pydatetime(warn=False)
    offsets = set()
    for fold in (0, 1):
        aware = native.replace(tzinfo=zone, fold=fold)
        back = aware.astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)
        if back == native:
            offsets.add(aware.utcoffset())
    if not offsets:
        raise _DateProblem(
            DateIssueCode.NONEXISTENT_LOCAL_TIME, "Source local time falls in a DST gap"
        )
    if len(offsets) > 1:
        raise _DateProblem(
            DateIssueCode.AMBIGUOUS_LOCAL_TIME, "Source local time has multiple UTC interpretations"
        )
    return (local - offsets.pop()).tz_localize("UTC").as_unit("ns", round_ok=False)


class DateNormalizer:
    """apply(alias_mapped_batch) -> date-aware, typed, cancellation-enriched result.

    No mutable parsing state is learned across rows or batches. Non-date
    conversion failures retain TypeConverter's atomic exception behavior,
    including on rows whose dates are unresolved.
    """

    def __init__(self, profile: SourceDateProfile | None = None):
        if profile is not None and not isinstance(profile, SourceDateProfile):
            raise ETLError("DateNormalizer requires a SourceDateProfile")
        self._profile = profile if profile is not None else SourceDateProfile()
        self._zone = ZoneInfo(self.profile.timezone) if self.profile.timezone else None

    @property
    def profile(self) -> SourceDateProfile:
        return self._profile

    def apply(self, batch: TransformedBatch) -> DateInterpretationResult:
        if not isinstance(batch, TransformedBatch):
            raise ETLError("DateNormalizer requires an alias-mapped TransformedBatch")
        frame = batch.data
        context = f"{batch.source_name} batch {batch.batch_number}"
        missing = [c for c in TARGET_COLUMNS if c not in frame.columns]
        if missing:
            raise MissingColumnsError(f"{context} missing transformed columns: {missing}")
        duplicates = [c for c in TARGET_COLUMNS if list(frame.columns).count(c) > 1]
        if duplicates:
            raise AmbiguousColumnsError(f"{context} duplicate required columns: {duplicates}")
        if "is_cancellation" in frame.columns:
            raise AmbiguousColumnsError(f"{context} already contains 'is_cancellation'")

        evidence, issues, safe_positions, unresolved_positions, bridge_dates = [], [], [], [], []
        for position, (index_label, original) in enumerate(frame["invoice_date"].items()):
            row = SourceRow(batch.source_name, batch.batch_number, position, index_label)
            local = utc = None
            origin, source_zone = "unspecified", None
            try:
                stamp = _parse(original, self.profile)
                # Preserve a native datetime's supplied wall clock before pandas
                # can normalize an illegally constructed aware DST-gap value.
                local = (
                    pd.Timestamp(original.replace(tzinfo=None))
                    if isinstance(original, datetime)
                    else stamp.tz_localize(None)
                ).as_unit("ns", round_ok=False)
                if stamp.tzinfo is not None:
                    origin, source_zone = "explicit_offset", str(stamp.tzinfo)
                    utc = stamp.tz_convert("UTC").as_unit("ns", round_ok=False)
                    # A native aware datetime with an IANA tzinfo can be constructed
                    # inside a DST gap. Its local clock must survive a UTC round trip.
                    if utc.tz_convert(stamp.tzinfo).tz_localize(None) != local:
                        raise _DateProblem(
                            DateIssueCode.NONEXISTENT_LOCAL_TIME,
                            "Aware datetime has invalid local time",
                        )
                elif self._zone is not None:
                    origin, source_zone = "source_profile", self.profile.timezone
                    utc = _local_to_utc(local, self._zone)
                status = (
                    TimezoneStatus.UTC_NORMALIZED
                    if utc is not None
                    else TimezoneStatus.TIMEZONE_UNKNOWN
                )
                safe_positions.append(position)
                # Private bridge ONLY: a known instant becomes its UTC clock,
                # never its original wall clock. Restored after TypeConverter.
                bridge_dates.append(utc.tz_localize(None) if utc is not None else local)
            except (_DateProblem, ValueError, OverflowError) as exc:
                if isinstance(exc, _DateProblem):
                    code, reason = exc.code, exc.reason
                else:
                    code, reason = (
                        DateIssueCode.OUT_OF_RANGE,
                        "Timestamp exceeds exact nanosecond range",
                    )
                issues.append(DateIssue(row, code, reason))
                unresolved_positions.append(position)
                status, utc = TimezoneStatus.UNRESOLVED, None
                bridge_dates.append(pd.NaT)
            evidence.append(DateEvidence(row, original, local, utc, status, origin, source_zone))

        bridge = frame.loc[:, list(TARGET_COLUMNS)].copy(deep=True)
        bridge["invoice_date"] = pd.array(bridge_dates, dtype="datetime64[ns]")
        typed = TypeConverter().convert(replace(batch, data=bridge))
        safe = frame.iloc[safe_positions].copy(deep=True)
        for column in TARGET_COLUMNS:
            if column != "invoice_date":
                safe[column] = typed.data[column].iloc[safe_positions].array
        safe["invoice_date"] = pd.array(
            [
                (
                    evidence[p].utc_datetime
                    if evidence[p].utc_datetime is not None
                    else evidence[p].local_datetime
                )
                for p in safe_positions
            ],
            dtype=object,
        )
        enriched = BusinessRules().apply(replace(batch, data=safe))
        return DateInterpretationResult(
            safe_batch=enriched,
            unresolved_batch=replace(batch, data=frame.iloc[unresolved_positions].copy(deep=True)),
            safe_positions=tuple(safe_positions),
            unresolved_positions=tuple(unresolved_positions),
            issues=tuple(issues),
            date_evidence=tuple(evidence),
            profile=self.profile,
            input_rows=len(frame),
        )
