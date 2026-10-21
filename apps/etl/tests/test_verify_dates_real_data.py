"""Small-fixture verification of accounting, failure exits and ASCII output."""

import importlib.util
import io
import json
from dataclasses import replace
from pathlib import Path

import pytest

from etl.exceptions import ETLError

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = Path(__file__).parent / "fixtures" / "online_retail_ii_sample.xlsx"


@pytest.fixture
def verifier():
    spec = importlib.util.spec_from_file_location(
        "dates_verifier", ROOT / "scripts" / "verify_dates_real_data.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def arguments():
    return ["--path", str(FIXTURE), "--expected-rows", "18", "--expected-batches", "2"]


def test_fixture_counts_and_unknown_timezone(verifier, capsys):
    assert verifier.main(arguments()) == 0
    output = capsys.readouterr().out
    report = json.loads(next(line for line in output.splitlines() if line.startswith("{")))
    assert report["counts"]["safe_date_rows"] == report["counts"]["TIMEZONE_UNKNOWN"] == 18
    assert report["counts"]["unresolved_date_rows"] == 0
    assert report["counts"].get("UTC_NORMALIZED", 0) == 0
    assert report["counts"]["cancellation_true"] == 5
    assert report["counts"]["cancellation_false"] == 13
    assert report["issues"] == {}
    assert report["preservation"]["negative_quantities"] == 7
    assert report["preservation"]["negative_prices"] == 1
    assert "PASS:" in output


@pytest.mark.parametrize(
    ("option", "value"), [("--expected-rows", "19"), ("--expected-batches", "3")]
)
def test_accounting_failure_is_nonzero(verifier, option, value):
    assert verifier.main([*arguments(), option, value]) == 1


def test_missing_file_is_nonzero(verifier, tmp_path):
    assert verifier.main(["--path", str(tmp_path / "absent.xlsx")]) == 1


def test_transformation_error_is_nonzero_without_leaking_value(verifier, monkeypatch, capsys):
    class Broken:
        def apply(self, batch):
            raise ETLError("sensitive source value")

    monkeypatch.setattr(verifier, "DateNormalizer", Broken)
    assert verifier.main(arguments()) == 1
    assert "sensitive source value" not in capsys.readouterr().out


@pytest.mark.parametrize("fault", ["quantity", "date", "accounting"])
def test_corrupted_result_fails(verifier, monkeypatch, fault):
    original = verifier.DateNormalizer

    class Corrupting(original):
        def apply(self, batch):
            result = super().apply(batch)
            if fault == "quantity":
                result.safe_batch.data["quantity"] = 0
            elif fault == "date":
                result.safe_batch.data["invoice_date"] = None
            else:
                # Public frames are mutable: verifier must recheck accounting.
                result = replace(result)
                result.safe_batch.data.drop(result.safe_batch.data.index[:1], inplace=True)
            return result

    monkeypatch.setattr(verifier, "DateNormalizer", Corrupting)
    assert verifier.main(arguments()) == 1


def test_default_windows_encoding_output(verifier, monkeypatch):
    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252", errors="strict")
    monkeypatch.setattr(verifier.sys, "stdout", stream)
    assert verifier.main(arguments()) == 0
    stream.flush()
    assert b"PASS:" in raw.getvalue()
