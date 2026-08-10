"""
Standalone real-dataset verification script - NOT part of the fast pytest
suite (that would make CI depend on a 43MB file that isn't committed).
Run manually: python scripts/verify_excel_source_real_data.py
"""

import os
import sys
import time
import tracemalloc
from pathlib import Path

# This script lives one directory below the repo root, so Python puts
# scripts/ (not the repo root) on sys.path by default - 'config' and the
# apps/ path trick from settings/base.py never get a chance to apply.
# Fix: put the repo root on sys.path explicitly, before anything else.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "apps"))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.test")
import django  # noqa: E402 - must follow sys.path setup above

django.setup()

from etl.sources import ExcelSource  # noqa: E402 - must follow django.setup()

path = str(REPO_ROOT / "data" / "online_retail_II.xlsx")

tracemalloc.start()
t0 = time.time()

total_rows = 0
total_batches = 0
per_sheet = {}

with ExcelSource(path, batch_size=50_000) as source:
    print(f"Sheets found: {source.sheet_names}")
    for batch in source.extract():
        total_rows += len(batch)
        total_batches += 1
        sheet = batch.source_name.split(":")[-1]
        per_sheet[sheet] = per_sheet.get(sheet, 0) + len(batch)
        if total_batches % 5 == 0:
            print(
                f"  ...batch {total_batches}, {total_rows:,} rows so far, "
                f"{time.time()-t0:.1f}s elapsed"
            )

elapsed = time.time() - t0
current, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()

print("\n=== REAL RESULTS (not estimated) ===")
print(f"Total rows extracted: {total_rows:,}")
print(f"Total batches: {total_batches}")
print(f"Per-sheet counts: {per_sheet}")
print(f"Elapsed: {elapsed:.1f}s")
print(f"Peak Python-tracked memory during extraction: {peak / 1024 / 1024:.1f} MB")
