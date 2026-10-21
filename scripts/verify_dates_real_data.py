"""Streaming, transformation-only Volume 4.3.3 verification; no database writes.

Default input is the ignored full UCI workbook. No source timezone is assumed.
Use --trace-memory to measure Python allocations (not process RSS).
"""

import argparse
import json
import os
import sys
import time
import tracemalloc
from collections import Counter
from pathlib import Path

_repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_repo))
sys.path.insert(0, str(_repo / "apps"))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")

import django  # noqa: E402

django.setup()

import pandas as pd  # noqa: E402

from etl.exceptions import ETLError  # noqa: E402
from etl.sources import ExcelSource  # noqa: E402
from etl.transforms import AliasMapper, DateNormalizer, TimezoneStatus  # noqa: E402


def _counts(frame):
    return Counter(
        negative_quantities=int((frame["quantity"] < 0).sum()),
        negative_prices=int((frame["unit_price"] < 0).sum()),
        missing_customers=int(frame["external_customer_id"].isna().sum()),
        missing_invoices=int(frame["invoice_no"].isna().sum()),
        missing_descriptions=int(frame["description"].isna().sum()),
    )


def _check_batch(mapped, result):
    result.check_accounting()
    assert result.input_rows == len(mapped), "Input accounting mismatch"
    for output, positions in (
        (result.safe_batch, result.safe_positions),
        (result.unresolved_batch, result.unresolved_positions),
    ):
        assert (output.source_name, output.batch_number, output.extracted_at) == (
            mapped.source_name,
            mapped.batch_number,
            mapped.extracted_at,
        ), "Metadata mismatch"
        assert output.data.index.equals(mapped.data.iloc[list(positions)].index), "Index mismatch"
    pd.testing.assert_frame_equal(
        result.unresolved_batch.data, mapped.data.iloc[list(result.unresolved_positions)]
    )
    expected_counts = _counts(mapped.data)
    actual_counts = _counts(result.safe_batch.data) + _counts(result.unresolved_batch.data)
    assert actual_counts == expected_counts, "Value preservation mismatch"
    assert tuple(e.row.position for e in result.date_evidence) == tuple(range(len(mapped)))
    pd.testing.assert_series_equal(
        pd.Series([e.original_value for e in result.date_evidence], dtype=object),
        pd.Series(mapped.data["invoice_date"].tolist(), dtype=object),
    )
    for output_position, input_position in enumerate(result.safe_positions):
        evidence = result.date_evidence[input_position]
        value = result.safe_batch.data["invoice_date"].iloc[output_position]
        if evidence.timezone_status == TimezoneStatus.UTC_NORMALIZED:
            assert value == evidence.utc_datetime and str(value.tzinfo) == "UTC"
        else:
            assert evidence.timezone_status == TimezoneStatus.TIMEZONE_UNKNOWN
            assert value == evidence.local_datetime and value.tzinfo is None
    # Independent prefix expectation, never based on quantity.
    invoices = result.safe_batch.data["invoice_no"]
    expected_markers = pd.array(
        [pd.NA if pd.isna(v) or not v or v.isspace() else v.startswith("C") for v in invoices],
        dtype="boolean",
    )
    pd.testing.assert_extension_array_equal(
        result.safe_batch.data["is_cancellation"].array, expected_markers
    )
    return actual_counts


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--path", type=Path, default=_repo / "data" / "online_retail_II.xlsx")
    parser.add_argument("--expected-rows", type=int, default=1_067_371)
    parser.add_argument("--expected-batches", type=int, default=22)
    parser.add_argument("--batch-size", type=int, default=50_000)
    parser.add_argument("--trace-memory", action="store_true")
    args = parser.parse_args(argv)
    if not args.path.is_file() or args.batch_size <= 0:
        print("FAIL: missing input file or invalid batch size")
        return 1

    totals, issues, preservation = Counter(), Counter(), Counter()
    normalizer = DateNormalizer()
    started = time.perf_counter()
    if args.trace_memory:
        tracemalloc.start()
    try:
        with ExcelSource(args.path, batch_size=args.batch_size) as source:
            for raw in source.extract():
                totals["input_rows"] += len(raw)
                totals["input_batches"] += 1
                mapped = AliasMapper().transform(raw)
                before = mapped.data.copy(deep=True)
                result = normalizer.apply(mapped)
                pd.testing.assert_frame_equal(mapped.data, before)
                preservation.update(_check_batch(mapped, result))
                totals["safe_date_rows"] += len(result.safe_batch)
                totals["unresolved_date_rows"] += len(result.unresolved_batch)
                totals["output_batches"] += 1
                issues.update(issue.code.value for issue in result.issues)
                for evidence in result.date_evidence:
                    totals[evidence.timezone_status.value] += 1
                markers = result.safe_batch.data["is_cancellation"]
                totals["cancellation_true"] += int(markers.sum())
                totals["cancellation_false"] += int((~markers).sum())
                totals["cancellation_unknown"] += int(markers.isna().sum())
                print(
                    f"Batch {raw.batch_number}: input={len(raw)} "
                    f"safe={len(result.safe_batch)} unresolved={len(result.unresolved_batch)} "
                    f"elapsed={time.perf_counter() - started:.1f}s",
                    flush=True,
                )
    except (ETLError, AssertionError) as exc:
        totals["errors"] += 1
        # Do not print exception text: it may contain source data or credentials.
        print(f"FAIL: {type(exc).__name__} during transformation or preservation checks")
    finally:
        elapsed = time.perf_counter() - started
        peak = tracemalloc.get_traced_memory()[1] if args.trace_memory else None
        if args.trace_memory:
            tracemalloc.stop()

    print(
        json.dumps(
            {
                "counts": dict(totals),
                "issues": dict(issues),
                "preservation": dict(preservation),
                "elapsed_seconds": round(elapsed, 3),
                "python_traced_peak_bytes": peak,
                "process_rss": "not measured",
            },
            sort_keys=True,
        )
    )
    ok = (
        not totals["errors"]
        and totals["input_rows"] == args.expected_rows
        and totals["input_batches"] == totals["output_batches"] == args.expected_batches
        and totals["input_rows"] == totals["safe_date_rows"] + totals["unresolved_date_rows"]
    )
    print("PASS: accounting and preservation verified" if ok else "FAIL: verification incomplete")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
