"""Regression tests for the Volume 4.3.2 acceptance script (small fixtures only)."""

import importlib.util
import io
import os
import subprocess
import sys
import tracemalloc
from pathlib import Path

import pytest

from etl.exceptions import TypeConversionError

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "verify_type_converter_real_data.py"
FIXTURE = Path(__file__).parent / "fixtures" / "online_retail_ii_sample.xlsx"


@pytest.fixture
def verifier(monkeypatch):
    spec = importlib.util.spec_from_file_location("type_converter_verifier", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "DATA_FILE", FIXTURE)
    return module


def test_row_count_mismatch_fails(verifier, capsys):
    assert verifier.main() == 1
    assert "FAIL: ROW COUNT MISMATCH" in capsys.readouterr().out


def test_success_returns_zero(verifier, monkeypatch, capsys):
    monkeypatch.setattr(verifier, "EXPECTED_ROWS", 18)
    assert verifier.main() == 0
    output = capsys.readouterr().out
    assert "PASS: ALL 18 ROWS ACCOUNTED FOR" in output
    assert "PASS: ZERO CONVERSION ERRORS" in output
    assert "Input rows:          18" in output
    assert "Input batches:       2" in output


def test_conversion_error_fails_even_when_row_count_matches(verifier, monkeypatch, capsys):
    class FailingConverter:
        def convert(self, batch):
            raise TypeConversionError(
                source_name=batch.source_name,
                batch_number=batch.batch_number,
                column="quantity",
                row_index=0,
                bad_value="bad",
                reason="cannot parse",
            )

    monkeypatch.setattr(verifier, "TypeConverter", FailingConverter)
    monkeypatch.setattr(verifier, "EXPECTED_ROWS", 0)
    assert verifier.main() == 1
    assert "FAIL: 2 CONVERSION ERRORS" in capsys.readouterr().out


def test_conversion_error_details_are_safe_for_windows_encoding(verifier, monkeypatch):
    class FailingConverter:
        def convert(self, batch):
            raise TypeConversionError(
                source_name=batch.source_name,
                batch_number=batch.batch_number,
                column="quantity",
                row_index=0,
                bad_value="\u0627",
                reason="cannot parse",
            )

    monkeypatch.setattr(verifier, "TypeConverter", FailingConverter)
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    assert verifier.main() == 1
    stream.flush()
    assert b"\\u0627" in raw.getvalue()
    assert b"FAIL:" in raw.getvalue()


@pytest.mark.parametrize(
    "column", ["quantity", "unit_price", "external_customer_id", "description"]
)
def test_preservation_fault_fails_verification(verifier, monkeypatch, column):
    original_converter = verifier.TypeConverter

    class CorruptingConverter:
        def convert(self, batch):
            out = original_converter().convert(batch)
            if column in ("quantity", "unit_price"):
                out.data[column] = out.data[column].abs()
            else:
                out.data[column] = out.data[column].fillna("unexpected fill")
            return out

    monkeypatch.setattr(verifier, "TypeConverter", CorruptingConverter)
    try:
        with pytest.raises(AssertionError, match="preservation failed"):
            verifier.main()
    finally:
        tracemalloc.stop()


@pytest.mark.parametrize("expected_rows", [18, 19])
def test_result_output_works_with_strict_windows_encoding(verifier, monkeypatch, expected_rows):
    monkeypatch.setattr(verifier, "EXPECTED_ROWS", expected_rows)
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(sys, "stdout", stream)
    assert verifier.main() == int(expected_rows != 18)
    stream.flush()
    output = raw.getvalue().decode("cp1252")
    assert "PASS:" in output
    if expected_rows != 18:
        assert "FAIL:" in output


@pytest.mark.parametrize("workbook_exists", [True, False])
def test_script_entrypoint_fails_without_encoding_override(tmp_path, workbook_exists):
    script_dir = tmp_path / "scripts"
    script_dir.mkdir()
    script = script_dir / SCRIPT.name
    script.write_bytes(SCRIPT.read_bytes())
    if workbook_exists:
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        (data_dir / "online_retail_II.xlsx").write_bytes(FIXTURE.read_bytes())
    env = os.environ.copy()
    env.pop("PYTHONIOENCODING", None)
    env["PYTHONPATH"] = os.pathsep.join([str(REPO_ROOT), str(REPO_ROOT / "apps")])
    result = subprocess.run(
        [sys.executable, str(script)], cwd=REPO_ROOT, env=env, capture_output=True, check=False
    )
    assert result.returncode == 1
    assert b"FAIL:" in result.stdout
    assert (b"ROW COUNT MISMATCH" if workbook_exists else b"not found") in result.stdout
