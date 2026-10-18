"""
Volume 4.3.2 — Real UCI workbook acceptance test.

Runs ExcelSource → AliasMapper → TypeConverter over the full
online_retail_II.xlsx and reports per-batch and aggregate results.

Usage:
    dataxaienv\\Scripts\\python.exe scripts\\verify_type_converter_real_data.py
"""

import os
import sys
import time
import tracemalloc

# Ensure the repo root and apps/ are importable
_repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _repo)
sys.path.insert(0, os.path.join(_repo, "apps"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
import django  # noqa: E402

django.setup()

from decimal import Decimal  # noqa: E402
from pathlib import Path  # noqa: E402

import pandas as pd  # noqa: E402

from etl.exceptions import TypeConversionError  # noqa: E402
from etl.sources import ExcelSource  # noqa: E402
from etl.transforms import TARGET_COLUMNS, AliasMapper, TypeConverter  # noqa: E402

DATA_FILE = Path(_repo) / "data" / "online_retail_II.xlsx"
EXPECTED_ROWS = 1_067_371


def main() -> int:
    if not DATA_FILE.exists():
        print(f"FAIL: {str(DATA_FILE)!a} not found.")
        return 1

    tracemalloc.start()
    t0 = time.perf_counter()

    mapper = AliasMapper()
    converter = TypeConverter()
    input_rows = 0
    input_batches = 0
    total_rows = 0
    total_batches = 0
    errors = []
    neg_qty_count = 0
    neg_price_count = 0
    null_customer_count = 0
    null_desc_count = 0

    with ExcelSource(DATA_FILE, batch_size=50_000) as src:
        for sb in src.extract():
            input_rows += len(sb)
            input_batches += 1
            mapped = mapper.transform(sb)
            try:
                out = converter.convert(mapped)
            except TypeConversionError as exc:
                errors.append(str(exc))
                continue

            n = len(out)
            total_rows += n
            total_batches += 1

            # Verify schema
            assert list(out.data.columns) == list(
                TARGET_COLUMNS
            ), f"Batch {out.batch_number}: columns mismatch"
            assert (
                out.data["quantity"].dtype == pd.Int64Dtype()
            ), f"Batch {out.batch_number}: quantity dtype {out.data['quantity'].dtype}"
            assert (
                out.data["invoice_date"].dtype == "datetime64[ns]"
            ), f"Batch {out.batch_number}: invoice_date dtype {out.data['invoice_date'].dtype}"
            assert (
                out.data["unit_price"].dtype == object
            ), f"Batch {out.batch_number}: unit_price dtype {out.data['unit_price'].dtype}"
            assert out.data["invoice_no"].dtype == pd.StringDtype()
            assert out.data["stock_code"].dtype == pd.StringDtype()
            assert out.data["external_customer_id"].dtype == pd.StringDtype()
            assert out.data["description"].dtype == pd.StringDtype()
            assert out.data["country"].dtype == pd.StringDtype()

            # Verify all unit_price values are Decimal or NA
            for val in out.data["unit_price"]:
                assert isinstance(val, Decimal) or pd.isna(
                    val
                ), f"Batch {out.batch_number}: unit_price has non-Decimal {type(val)}"

            # Verify metadata preservation
            assert out.source_name == sb.source_name
            assert out.batch_number == sb.batch_number
            assert out.extracted_at == sb.extracted_at
            assert len(out) == len(sb)

            # Compare preservation counts with this actual input batch.
            batch_neg_qty = (out.data["quantity"] < 0).sum()
            batch_neg_price = (
                out.data["unit_price"].apply(lambda v: isinstance(v, Decimal) and v < 0).sum()
            )
            batch_null_customer = out.data["external_customer_id"].isna().sum()
            batch_null_desc = out.data["description"].isna().sum()
            assert batch_neg_qty == (sb.data["Quantity"] < 0).sum(), "quantity preservation failed"
            assert batch_neg_price == (sb.data["Price"] < 0).sum(), "price preservation failed"
            assert (
                batch_null_customer == sb.data["Customer ID"].isna().sum()
            ), "customer-null preservation failed"
            assert (
                batch_null_desc == sb.data["Description"].isna().sum()
            ), "description-null preservation failed"
            neg_qty_count += batch_neg_qty
            neg_price_count += batch_neg_price
            null_customer_count += batch_null_customer
            null_desc_count += batch_null_desc

            # Check no "nan" or "None" string in customer IDs
            non_null_ids = out.data["external_customer_id"].dropna()
            assert (
                "nan" not in non_null_ids.values
            ), f"Batch {out.batch_number}: found literal 'nan' in customer IDs"
            assert (
                "None" not in non_null_ids.values
            ), f"Batch {out.batch_number}: found literal 'None' in customer IDs"

    elapsed = time.perf_counter() - t0
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()

    print("=" * 60)
    print("Volume 4.3.2 - Real UCI Dataset Acceptance Test")
    print("=" * 60)
    print(f"File:                {str(DATA_FILE)!a}")
    print(f"Input rows:          {input_rows:,}")
    print(f"Input batches:       {input_batches}")
    print(f"Total rows:          {total_rows:,}")
    print(f"Total batches:       {total_batches}")
    print(f"Conversion errors:   {len(errors)}")
    print(f"Negative quantities: {neg_qty_count:,}")
    print(f"Negative prices:     {neg_price_count}")
    print(f"Null customer IDs:   {null_customer_count:,}")
    print(f"Null descriptions:   {null_desc_count:,}")
    print(f"Elapsed:             {elapsed:.1f}s")
    print(f"Peak memory:         {peak / 1024 / 1024:.1f} MB")
    print()

    if errors:
        print(f"CONVERSION ERRORS ({len(errors)}):")
        for e in errors:
            print(f"  {e!a}")
        print()

    if total_rows == EXPECTED_ROWS:
        print(f"PASS: ALL {EXPECTED_ROWS:,} ROWS ACCOUNTED FOR")
    else:
        print(f"FAIL: ROW COUNT MISMATCH - expected {EXPECTED_ROWS:,}, got {total_rows:,}")

    if not errors:
        print("PASS: ZERO CONVERSION ERRORS")
    else:
        print(f"FAIL: {len(errors)} CONVERSION ERRORS")

    return int(total_rows != EXPECTED_ROWS or bool(errors))


if __name__ == "__main__":
    sys.exit(main())
