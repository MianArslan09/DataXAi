"""Synthetic date edge cases; only fixture integration uses real UCI-derived rows."""

from dataclasses import FrozenInstanceError, replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import pytest

from etl.exceptions import AmbiguousColumnsError, ETLError, MissingColumnsError, TypeConversionError
from etl.sources import CsvSource, ExcelSource, PostgresSource
from etl.tests.test_sources_postgres import SOURCE_DB_URL, requires_postgres
from etl.transforms import (
    TARGET_COLUMNS,
    AliasMapper,
    BusinessRules,
    DateIssueCode,
    DateNormalizer,
    SourceDateProfile,
    TimezoneStatus,
    TransformedBatch,
    TypeConverter,
)

FIXTURES = Path(__file__).parent / "fixtures"


def batch(values, *, index=None, number=7):
    n = len(values)
    frame = pd.DataFrame(
        {
            "invoice_no": pd.array(["C496350"] * n, dtype="string"),
            "stock_code": pd.array(["M"] * n, dtype="string"),
            "description": pd.array([pd.NA] * n, dtype="string"),
            "quantity": pd.array([-1] * n, dtype="Int64"),
            "invoice_date": pd.Series(values, dtype=object),
            "unit_price": pd.Series([Decimal("-10.0000")] * n, dtype=object),
            "external_customer_id": pd.array([pd.NA] * n, dtype="string"),
            "country": pd.array(["Pakistan"] * n, dtype="string"),
        }
    )
    if index is not None:
        frame.index = index
    return TransformedBatch(
        frame, "synthetic:sme", number, datetime(2026, 10, 20, tzinfo=timezone.utc)
    )


def profile(**kwargs):
    return SourceDateProfile(
        name="synthetic-export-v1", provenance="Synthetic test specification", **kwargs
    )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2009-12-01 07:45:00", "2009-12-01T07:45:00"),
        ("2026-10-20T14:30:00", "2026-10-20T14:30:00"),
        ("2026-10-20T14:30", "2026-10-20T14:30:00"),
        ("20/10/2026 14:30", "2026-10-20T14:30:00"),
        ("10/20/2026 14:30", "2026-10-20T14:30:00"),
        ("03/03/2026 14:30", "2026-03-03T14:30:00"),
        ("29/02/2024 23:59:59.123456789", "2024-02-29T23:59:59.123456789"),
        ("2026-10-20 14:30:00.000000001", "2026-10-20T14:30:00.000000001"),
        (datetime(2026, 10, 20, 14, 30, 0, 123456), "2026-10-20T14:30:00.123456"),
        (pd.Timestamp("2026-10-20 14:30:00.123456789"), "2026-10-20T14:30:00.123456789"),
        (np.datetime64("2026-10-20T14:30:00.123456789"), "2026-10-20T14:30:00.123456789"),
        (np.datetime64("2026-10-20T14:30"), "2026-10-20T14:30:00"),
        (datetime(2026, 10, 20, 0, 0), "2026-10-20T00:00:00"),
    ],
)
def test_supported_unknown_zone_preserves_exact_clock(value, expected):
    source = batch([value])
    before = source.data.copy(deep=True)
    result = DateNormalizer().apply(source)
    evidence = result.date_evidence[0]
    assert result.safe_batch.data["invoice_date"].iloc[0] == pd.Timestamp(expected)
    assert evidence.local_datetime == pd.Timestamp(expected)
    assert evidence.utc_datetime is None
    assert evidence.timezone_status == TimezoneStatus.TIMEZONE_UNKNOWN
    assert evidence.timezone_origin == "unspecified"
    assert evidence.iso_value() == expected
    assert result.safe_positions == (0,) and result.unresolved_positions == ()
    assert result.issues == ()
    assert bool(result.safe_batch.data["is_cancellation"].iloc[0])
    pd.testing.assert_frame_equal(source.data, before)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-10-20T14:30:00Z", "2026-10-20T14:30:00Z"),
        ("2026-10-20T14:30:00+05:00", "2026-10-20T09:30:00Z"),
        ("2026-10-20T00:30:00+0500", "2026-10-19T19:30:00Z"),
        ("2026-12-31T23:30:00-02:30", "2027-01-01T02:00:00Z"),
        ("2026-10-20T14:30:00.123456789+05:00", "2026-10-20T09:30:00.123456789Z"),
        (
            datetime(2026, 10, 20, 14, 30, tzinfo=timezone(timedelta(hours=5))),
            "2026-10-20T09:30:00Z",
        ),
        (pd.Timestamp("2026-10-20T14:30:00.123456789+05:00"), "2026-10-20T09:30:00.123456789Z"),
        (
            datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0),
            "2026-11-01T05:30:00Z",
        ),
        (
            datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=1),
            "2026-11-01T06:30:00Z",
        ),
    ],
)
def test_explicit_instant_overrides_profile_timezone(value, expected):
    result = DateNormalizer(profile(timezone="Asia/Karachi")).apply(batch([value]))
    evidence = result.date_evidence[0]
    assert evidence.utc_datetime == pd.Timestamp(expected)
    assert str(evidence.utc_datetime.tz) == "UTC"
    assert evidence.timezone_origin == "explicit_offset"
    assert evidence.timezone_status == TimezoneStatus.UTC_NORMALIZED
    assert evidence.iso_value() == expected
    assert result.safe_batch.data["invoice_date"].iloc[0] == pd.Timestamp(expected)


@pytest.mark.parametrize(
    ("zone", "value", "expected"),
    [
        ("Asia/Karachi", "2026-10-20 14:30:00", "2026-10-20T09:30:00Z"),
        ("Asia/Karachi", "2026-01-01 00:00:00.000000001", "2025-12-31T19:00:00.000000001Z"),
        ("America/New_York", "2026-01-20 14:30:00", "2026-01-20T19:30:00Z"),
        ("America/New_York", "2026-07-20 14:30:00", "2026-07-20T18:30:00Z"),
        ("UTC", "2026-10-20 14:30:00", "2026-10-20T14:30:00Z"),
    ],
)
def test_trusted_source_localization(zone, value, expected):
    configured = profile(timezone=zone)
    result = DateNormalizer(configured).apply(batch([value]))
    evidence = result.date_evidence[0]
    assert evidence.local_datetime == pd.Timestamp(value)
    assert evidence.utc_datetime == pd.Timestamp(expected)
    assert evidence.source_timezone == zone
    assert evidence.timezone_origin == "source_profile"
    assert result.profile is configured
    assert evidence.iso_value() == expected


@pytest.mark.parametrize(
    ("value", "code"),
    [
        (None, "MISSING_DATE"),
        (pd.NA, "MISSING_DATE"),
        (pd.NaT, "MISSING_DATE"),
        (float("nan"), "MISSING_DATE"),
        (np.datetime64("NaT", "ns"), "MISSING_DATE"),
        (Decimal("NaN"), "MISSING_DATE"),
        ("", "MISSING_DATE"),
        (" \t\n", "MISSING_DATE"),
        ("32/10/2026", "INVALID_CALENDAR_DATE"),
        ("2026-02-29 14:30:00", "INVALID_CALENDAR_DATE"),
        ("20/10/2026 25:30", "INVALID_CALENDAR_DATE"),
        ("2026-10-20T14:30:60", "INVALID_CALENDAR_DATE"),
        ("03/04/2026 14:30", "AMBIGUOUS_DATE_FORMAT"),
        ("03/04/2026", "AMBIGUOUS_DATE_FORMAT"),
        ("2026-10-20", "DATE_ONLY"),
        ("20/10/2026", "DATE_ONLY"),
        (date(2026, 10, 20), "DATE_ONLY"),
        (np.datetime64("2026-10-20", "D"), "DATE_ONLY"),
        ("tomorrow", "UNSUPPORTED_DATE_FORMAT"),
        ("2026-10-20 14:30:00 UTC", "UNSUPPORTED_DATE_FORMAT"),
        (" 2026-10-20 14:30:00", "UNSUPPORTED_DATE_FORMAT"),
        ("2026-10-20T14:30:00.1234567891", "UNSUPPORTED_DATE_FORMAT"),
        ("2026-10-20T14:30:00+24:00", "UNSUPPORTED_DATE_FORMAT"),
        ("2026-10-20T14:30:00+05:60", "UNSUPPORTED_DATE_FORMAT"),
        ("2026-10-20T14:30:00-00:00", "UNSUPPORTED_DATE_FORMAT"),
        (46000, "UNSUPPORTED_DATE_FORMAT"),
        (True, "UNSUPPORTED_DATE_FORMAT"),
        (["2026-10-20"], "UNSUPPORTED_DATE_FORMAT"),
        ({"date": "2026-10-20"}, "UNSUPPORTED_DATE_FORMAT"),
        (Decimal("sNaN"), "UNSUPPORTED_DATE_FORMAT"),
        ("2500-01-01 00:00:00", "OUT_OF_RANGE"),
        (datetime(1500, 1, 1, 12), "OUT_OF_RANGE"),
        (np.datetime64(1, "ps"), "UNSUPPORTED_DATE_FORMAT"),
    ],
)
def test_row_problems_retain_originals_with_stable_context(value, code):
    source = batch([value], index=["duplicate-label"])
    original_repr = repr(source.data["invoice_date"].iloc[0])
    result = DateNormalizer().apply(source)
    assert len(result.safe_batch) == 0 and len(result.unresolved_batch) == 1
    assert result.safe_positions == () and result.unresolved_positions == (0,)
    issue = result.issues[0]
    assert issue.code == code
    assert issue.column == "invoice_date"
    assert (
        issue.row.source_name,
        issue.row.batch_number,
        issue.row.position,
        issue.row.index_label,
    ) == ("synthetic:sme", 7, 0, "duplicate-label")
    assert result.date_evidence[0].timezone_status == TimezoneStatus.UNRESOLVED
    assert result.date_evidence[0].iso_value() is None
    assert repr(result.date_evidence[0].original_value) == original_repr
    assert repr(result.unresolved_batch.data["invoice_date"].iloc[0]) == original_repr
    assert repr(source.data["invoice_date"].iloc[0]) == original_repr
    pd.testing.assert_frame_equal(
        result.unresolved_batch.data.drop(columns="invoice_date"),
        source.data.drop(columns="invoice_date"),
    )
    assert "is_cancellation" not in result.unresolved_batch.data
    assert len(issue.reason) < 150


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("2026-11-01 01:30:00.123456789", "AMBIGUOUS_LOCAL_TIME"),
        ("2026-03-08 02:30:00", "NONEXISTENT_LOCAL_TIME"),
    ],
)
def test_dst_is_never_guessed(value, code):
    result = DateNormalizer(profile(timezone="America/New_York")).apply(batch([value]))
    assert result.issues[0].code == code
    assert result.date_evidence[0].local_datetime == pd.Timestamp(value)
    assert result.date_evidence[0].utc_datetime is None
    assert result.date_evidence[0].source_timezone == "America/New_York"


def test_native_aware_gap_is_not_a_valid_local_timestamp():
    value = datetime(2026, 3, 8, 2, 30, tzinfo=ZoneInfo("America/New_York"))
    result = DateNormalizer().apply(batch([value]))
    assert result.issues[0].code == DateIssueCode.NONEXISTENT_LOCAL_TIME


@pytest.mark.parametrize(("order", "expected"), [("DMY", "2026-04-03"), ("MDY", "2026-03-04")])
def test_explicit_date_order_is_stable_across_batches(order, expected):
    normalizer = DateNormalizer(profile(date_order=order))
    for number in (0, 1):
        result = normalizer.apply(batch(["03/04/2026 14:30"], number=number))
        assert result.date_evidence[0].local_datetime == pd.Timestamp(expected + " 14:30")
        assert result.date_evidence[0].row.batch_number == number


@pytest.mark.parametrize(
    ("order", "value"), [("DMY", "10/20/2026 14:30"), ("MDY", "20/10/2026 14:30")]
)
def test_conflicting_order_is_unresolved_not_silently_swapped(order, value):
    result = DateNormalizer(profile(date_order=order)).apply(batch([value]))
    assert result.issues[0].code == DateIssueCode.DATE_ORDER_CONFLICT


def test_unambiguous_rows_never_establish_a_permanent_order():
    normalizer = DateNormalizer()
    result = normalizer.apply(batch(["20/10/2026 14:30", "10/20/2026 14:30", "03/04/2026 14:30"]))
    assert result.safe_positions == (0, 1)
    assert result.issues[0].code == DateIssueCode.AMBIGUOUS_DATE_FORMAT
    assert normalizer.apply(batch(["03/04/2026 14:30"], number=8)).issues[0].code == (
        DateIssueCode.AMBIGUOUS_DATE_FORMAT
    )


@pytest.mark.parametrize(
    "settings",
    [
        {"date_order": "YMD"},
        {"timezone": "Not/AZone"},
        {"timezone": "../UTC"},
        {"timezone": 5},
        {"name": ""},
        {"provenance": ""},
    ],
)
def test_invalid_profile_stops_before_rows(settings):
    with pytest.raises(ETLError, match="Invalid date profile"):
        SourceDateProfile(**{"name": "synthetic", "provenance": "test", **settings})


def test_configuration_requires_evidence_and_is_immutable():
    with pytest.raises(ETLError, match="provenance"):
        SourceDateProfile(timezone="Asia/Karachi")
    with pytest.raises(ETLError, match="SourceDateProfile"):
        DateNormalizer({"timezone": "Asia/Karachi"})
    configured = profile(date_order="DMY")
    with pytest.raises(FrozenInstanceError):
        configured.date_order = "MDY"


@pytest.mark.parametrize("column", TARGET_COLUMNS)
def test_missing_schema_is_rechecked_at_use(column):
    source = batch(["2026-10-20 14:30"])
    source.data.drop(columns=column, inplace=True)
    with pytest.raises(MissingColumnsError, match=f"batch 7.*{column}"):
        DateNormalizer().apply(source)


@pytest.mark.parametrize("column", ["invoice_date", "quantity", "invoice_no"])
def test_duplicate_required_columns_fail_atomically(column):
    source = batch(["2026-10-20 14:30"])
    source.data.insert(0, column, source.data[column], allow_duplicates=True)
    before = source.data.copy(deep=True)
    with pytest.raises(AmbiguousColumnsError, match=f"batch 7.*{column}"):
        DateNormalizer().apply(source)
    pd.testing.assert_frame_equal(source.data, before)


def test_existing_marker_is_rejected_before_converter_can_drop_it():
    source = batch(["2026-10-20 14:30"])
    source.data["is_cancellation"] = pd.array([False], dtype="boolean")
    with pytest.raises(AmbiguousColumnsError, match="is_cancellation"):
        DateNormalizer().apply(source)
    assert not source.data["is_cancellation"].iloc[0]


@pytest.mark.parametrize("value", ["2026-10-20T14:30:00+05:00", "not-a-date"])
@pytest.mark.parametrize(("column", "bad"), [("quantity", "1.5"), ("unit_price", "1.12345")])
def test_non_date_errors_keep_strict_converter_contract(value, column, bad):
    source = batch([value], index=[42])
    source.data[column] = [bad]
    before = source.data.copy(deep=True)
    with pytest.raises(TypeConversionError) as caught:
        DateNormalizer().apply(source)
    assert column in str(caught.value) and "row index 42" in str(caught.value)
    pd.testing.assert_frame_equal(source.data, before)


def test_private_bridge_uses_utc_clock_then_restores_awareness(monkeypatch):
    original = TypeConverter.convert
    seen = []

    def inspect(self, source):
        seen.extend(source.data["invoice_date"].tolist())
        return original(self, source)

    monkeypatch.setattr(TypeConverter, "convert", inspect)
    result = DateNormalizer().apply(batch(["2026-10-20T14:30:00+05:00", "2026-10-20 14:30", None]))
    assert seen[:2] == [pd.Timestamp("2026-10-20 09:30"), pd.Timestamp("2026-10-20 14:30")]
    assert pd.isna(seen[2])
    assert result.safe_batch.data["invoice_date"].iloc[0] == pd.Timestamp("2026-10-20T09:30Z")
    assert result.safe_batch.data["invoice_date"].iloc[1].tzinfo is None


def test_split_lineage_values_metadata_extras_and_determinism():
    values = ["2026-10-20 14:30", None, "2026-10-20T14:30:00+05:00", "bad", "2026-10-20 14:30"]
    source = batch(values, index=pd.Index([9, 9, 2, 9, 9], name="source_label"))
    source.data["extra"] = pd.Categorical(["a", "b", "a", "b", "a"])
    source.data["invoice_no"] = pd.array(
        ["C496350", None, "489464", "c123", "C496350"], dtype="string"
    )
    source.data["quantity"] = pd.array([1, -2, -96, pd.NA, 1], dtype="Int64")
    before = source.data.copy(deep=True)
    result = DateNormalizer().apply(source)
    again = DateNormalizer().apply(source)
    assert result.safe_positions == (0, 2, 4)
    assert result.unresolved_positions == (1, 3)
    assert len(result.safe_batch) + len(result.unresolved_batch) == len(source)
    pd.testing.assert_frame_equal(source.data, before)
    pd.testing.assert_frame_equal(result.safe_batch.data, again.safe_batch.data)
    pd.testing.assert_frame_equal(result.unresolved_batch.data, before.iloc[[1, 3]])
    pd.testing.assert_frame_equal(
        result.safe_batch.data.drop(columns=["invoice_date", "is_cancellation"]),
        before.iloc[[0, 2, 4]].drop(columns="invoice_date"),
    )
    assert result.safe_batch.data["is_cancellation"].tolist() == [True, False, True]
    assert result.safe_batch.data.duplicated().sum() == 1
    assert result.safe_batch.data["unit_price"].iloc[0].as_tuple() == Decimal("-10.0000").as_tuple()
    for output in (result.safe_batch, result.unresolved_batch):
        assert (output.source_name, output.batch_number, output.extracted_at) == (
            source.source_name,
            source.batch_number,
            source.extracted_at,
        )
    assert [e.row.position for e in result.date_evidence] == list(range(5))
    assert [e.row.index_label for e in result.date_evidence] == [9, 9, 2, 9, 9]
    assert [e.original_value for e in result.date_evidence] == values
    with pytest.raises(ETLError, match="accounting"):
        replace(result, safe_positions=(0, 0, 4))


def test_empty_result_has_both_schemas_and_zero_accounting():
    source = batch([], index=pd.Index([], name="source_label"))
    result = DateNormalizer().apply(source)
    assert result.input_rows == len(result.safe_batch) == len(result.unresolved_batch) == 0
    assert (
        result.issues
        == result.date_evidence
        == result.safe_positions
        == result.unresolved_positions
        == ()
    )
    assert result.safe_batch.data["is_cancellation"].dtype == pd.BooleanDtype()
    pd.testing.assert_frame_equal(result.unresolved_batch.data, source.data)


def test_nanosecond_boundaries_and_timezone_overflow():
    good = DateNormalizer().apply(
        batch([pd.Timestamp.min, pd.Timestamp.max, str(pd.Timestamp.min), str(pd.Timestamp.max)])
    )
    assert good.safe_positions == (0, 1, 2, 3)
    assert good.date_evidence[0].local_datetime == pd.Timestamp.min
    assert good.date_evidence[1].local_datetime == pd.Timestamp.max
    assert good.date_evidence[2].local_datetime == pd.Timestamp.min
    assert good.date_evidence[3].local_datetime == pd.Timestamp.max
    overflow = DateNormalizer(profile(timezone="Asia/Karachi")).apply(batch([pd.Timestamp.min]))
    assert overflow.issues[0].code == DateIssueCode.OUT_OF_RANGE


@pytest.mark.parametrize(
    ("source_type", "filename", "lengths"),
    [
        (CsvSource, "online_retail_ii_sample.csv", [5, 5, 5, 3]),
        (ExcelSource, "online_retail_ii_sample.xlsx", [5, 4, 5, 4]),
    ],
)
def test_uci_fixture_pipeline_has_no_assumed_timezone(source_type, filename, lengths):
    outputs = []
    with source_type(FIXTURES / filename, batch_size=5) as source:
        for raw in source.extract():
            mapped = AliasMapper().transform(raw)
            expected = BusinessRules().apply(TypeConverter().convert(mapped))
            result = DateNormalizer().apply(mapped)
            assert result.unresolved_positions == () and result.issues == ()
            assert all(
                e.timezone_status == TimezoneStatus.TIMEZONE_UNKNOWN for e in result.date_evidence
            )
            pd.testing.assert_frame_equal(
                result.safe_batch.data.drop(columns="invoice_date"),
                expected.data.drop(columns="invoice_date"),
            )
            assert (
                result.safe_batch.data["invoice_date"].tolist()
                == expected.data["invoice_date"].tolist()
            )
            assert result.safe_batch.data.index.equals(raw.data.index)
            assert result.safe_batch.extracted_at == raw.extracted_at
            outputs.append(result.safe_batch)
    assert [len(o) for o in outputs] == lengths
    combined = pd.concat([o.data for o in outputs], ignore_index=True)
    assert len(combined) == 18 and combined["is_cancellation"].sum() == 5
    assert (~combined["is_cancellation"]).sum() == 13
    assert combined.duplicated().sum() == 1
    assert (combined["quantity"] < 0).sum() == 7
    assert (combined["unit_price"] < 0).sum() == 1
    assert combined["external_customer_id"].isna().sum() == 7
    assert combined["description"].isna().sum() == 2


@requires_postgres
def test_postgres_date_pipeline_matches_csv_without_guessing_zone():
    def collect(source):
        with source:
            results = [
                DateNormalizer().apply(AliasMapper().transform(raw)) for raw in source.extract()
            ]
        assert all(not r.issues and not r.unresolved_positions for r in results)
        assert all(
            e.timezone_status == TimezoneStatus.TIMEZONE_UNKNOWN
            for r in results
            for e in r.date_evidence
        )
        return pd.concat([r.safe_batch.data for r in results], ignore_index=True)

    actual = collect(PostgresSource(SOURCE_DB_URL, batch_size=5))
    expected = collect(CsvSource(FIXTURES / "online_retail_ii_sample.csv", batch_size=5))
    pd.testing.assert_frame_equal(actual, expected)


@pytest.mark.parametrize("extension", ["csv", "xlsx"])
def test_synthetic_mixed_export_preserves_split_across_batches(tmp_path, extension):
    """Synthetic SME export, not evidence about UCI source conventions."""
    frame = pd.read_csv(FIXTURES / "online_retail_ii_sample.csv").iloc[:6].copy()
    frame["InvoiceDate"] = [
        "2026-10-20T14:30:00+05:00",
        "20/10/2026 14:30",
        "10/20/2026 14:30",
        "03/04/2026 14:30",
        None,
        "2026-10-20T14:30:00.123456789Z",
    ]
    path = tmp_path / f"synthetic.{extension}"
    if extension == "csv":
        frame.to_csv(path, index=False)
        source_type = CsvSource
    else:
        frame.to_excel(path, index=False)
        source_type = ExcelSource
    with source_type(path, batch_size=3) as source:
        results = [DateNormalizer().apply(AliasMapper().transform(b)) for b in source.extract()]
    assert [r.safe_positions for r in results] == [(0, 1, 2), (2,)]
    assert [r.unresolved_positions for r in results] == [(), (0, 1)]
    assert [i.code for i in results[1].issues] == [
        DateIssueCode.AMBIGUOUS_DATE_FORMAT,
        DateIssueCode.MISSING_DATE,
    ]
    assert [e.iso_value() for r in results for e in r.date_evidence] == [
        "2026-10-20T09:30:00Z",
        "2026-10-20T14:30:00",
        "2026-10-20T14:30:00",
        None,
        None,
        "2026-10-20T14:30:00.123456789Z",
    ]
    assert sum(len(r.safe_batch) + len(r.unresolved_batch) for r in results) == 6
    assert [e.row.batch_number for r in results for e in r.date_evidence] == [0] * 3 + [1] * 3
