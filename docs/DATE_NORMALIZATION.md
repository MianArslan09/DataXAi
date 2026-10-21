# Volume 4.3.3 date interpretation contract

This is deterministic transformation, not validation, healing, or warehouse
loading. The owner authorized DT-001 through DT-007 alongside the existing
BR-001 through BR-004. Formal milestone acceptance remains an owner decision.

## Public interface and composition

```python
from etl.transforms import AliasMapper, DateNormalizer, SourceDateProfile

normalizer = DateNormalizer(
    SourceDateProfile(
        name="confirmed-export-v1",
        date_order="DMY",
        timezone="Asia/Karachi",
        provenance="Source export specification confirmed by its operator",
    )
)
result = normalizer.apply(AliasMapper().transform(source_batch))
result.check_accounting()
```

Use `DateNormalizer()` without a profile for UCI Retail II: its source timezone
has not been established. A filename, country, or Django timezone is not evidence.
The example configured profile above is illustrative, not a claim about UCI.

`SourceDateProfile` is immutable, reusable across batches, and contains `name`,
optional `date_order` (`DMY` or `MDY`), optional IANA `timezone`, and `provenance`.
Configured interpretation settings require nonblank provenance. No settings are
learned from neighboring records or previous batches. Invalid profile settings
raise `ETLError` before processing. Explicit offsets in individual timestamps
take precedence over the profile timezone; the latter governs naive clocks only.

`DateNormalizer.apply(TransformedBatch) -> DateInterpretationResult` internally
composes date interpretation, the unchanged `TypeConverter.convert`, and the
unchanged `BusinessRules.apply`. The existing SourceBatch `Transform` protocol
is unchanged.

## Bounded input grammar

- `YYYY-MM-DD HH:MM[:SS[.fraction]]` and
  `YYYY-MM-DDTHH:MM[:SS[.fraction]]`, with optional uppercase `Z` or numeric
  `+HH:MM`, `-HH:MM`, `+HHMM`, or `-HHMM` offset.
- `DD/MM/YYYY HH:MM[:SS[.fraction]]` and
  `MM/DD/YYYY HH:MM[:SS[.fraction]]`.
- Fractional seconds contain 1–9 digits. No rounding to fit nanoseconds.
- Native Python `datetime`, pandas `Timestamp`, and NumPy datetime values with
  explicit time resolution, within the exact pandas nanosecond range.
- Excel datetime cells as delivered by the existing Excel source.

Digits, separators, and case are explicit. There is no general-purpose guessing,
whitespace trimming, locale dependence, or automatic format correction. Calendar
dates and clock ranges are checked. Leap-second `:60` is not supported.
Negative zero offset (`-00:00`/`-0000`, indicating an unknown offset) is unresolved,
not relabeled UTC. Positive zero and `Z` establish UTC.

Without a date-order profile, slash dates are accepted only when one convention
is calendar-valid or both conventions give the same date. `03/04/2026 14:30`
is ambiguous. A configured convention is authoritative: an otherwise valid
opposite-convention date produces `DATE_ORDER_CONFLICT`, never silent swapping.
ISO year-first dates are independent of the slash-date setting.

Date-only text, Python `date`, and NumPy year/month/week/day values produce
`DATE_ONLY`. Midnight is not invented. An explicitly supplied native datetime
at midnight is a timestamp and is accepted. Excel readers may already deliver
a date-formatted cell as a midnight datetime without its original cell-format
metadata; this layer cannot recover time precision that the source has discarded.
Raw Excel serial numbers and sub-nanosecond NumPy units are unsupported.

## Timezone semantics and strict-converter bridge

Known instants become UTC-aware pandas timestamps. Naive clocks with a trusted
IANA source zone are localized using a UTC round-trip check. DST folds produce
`AMBIGUOUS_LOCAL_TIME`; DST gaps produce `NONEXISTENT_LOCAL_TIME`. No fold is
guessed and no clock is shifted to repair a gap. An already aware datetime's
explicit fold/offset determines its instant, but an invalid native aware gap
is rejected.

Parsed naive clocks without a source timezone remain naive and have
`TIMEZONE_UNKNOWN` status. They belong to the safe-date path, not the issue path.
Explicit offsets yield `UTC_NORMALIZED`, irrespective of `Country`.

The accepted converter only accepts naive timestamps. Its private bridge uses:

- Known instant: the **UTC clock** with the timezone temporarily removed.
- Unknown timezone: the original interpreted naive clock.
- Unresolved date: `NaT` solely for non-date conversion checks.

Thus `2026-10-20T14:30:00+05:00` crosses the private bridge as naive `09:30`,
then is restored to aware `09:30Z`. The bridge is not returned as a public batch.
Date evidence is kept outside the converter's eight-column selection.

Every row still passes the existing non-date converter checks. An invalid
quantity or price retains its contextual `TypeConversionError`, including on
an unresolved-date row. Such a failure is atomic; there is no partial result.
Direct converter behavior, including null-date `NaT`, is unchanged.

## Result, lineage, and output boundary

`DateInterpretationResult` exposes:

- `safe_batch`: typed, cancellation-enriched rows in original relative order.
  Its `invoice_date` uses object dtype containing UTC-aware or naive Timestamp
  scalars according to each row's evidence. This accommodates mixed states
  without falsely coercing unknown clocks to UTC.
- `unresolved_batch`: original alias-mapped rows, with original values and dtypes,
  no fabricated date and no implied cancellation enrichment or load readiness.
- `safe_positions` and `unresolved_positions`: disjoint zero-based input positions.
- `date_evidence`: one `DateEvidence` per original row, in input order.
- `issues`: one structured `DateIssue` per unresolved row, in original order.
- `profile` and `input_rows`: shared provenance and explicit accounting.

`SourceRow(source_name, batch_number, position, index_label)` identifies rows by
source/batch/position; index labels are additional evidence and may repeat.
This is extraction-relative identity, not a persistent cross-run database key.
`DateEvidence` retains the original date value, interpreted local timestamp when
available, UTC instant when established, timezone status, timezone origin, and
source timezone. The result's immutable profile records shared provenance.
Raw date values and index labels are excluded from automatic evidence reprs.

`DateEvidence.iso_value()` is the explicit serialization boundary: known instants
use ISO text ending `Z`; unknown clocks have no timezone suffix; unresolved rows
return `None`. Inspect the issue and original evidence instead of treating that
`None` as a repaired date.

`input_rows == len(safe_batch) + len(unresolved_batch)`. Every input position
occurs exactly once. Metadata, indexes, order, duplicate transactions, and
additional input columns survive the split. Safe non-date fields follow the
existing converter contract; already typed Decimal representations and nullable
types are preserved. No input DataFrame is mutated. Frozen dataclasses still
contain mutable DataFrames, so callers can explicitly recheck accounting.

Missing/duplicate required columns and a pre-existing `is_cancellation` field
raise contextual structural errors before field selection can discard evidence.
Only eligible safe rows receive the existing strict uppercase-C nullable marker;
its direct typed-input and collision contracts remain unchanged.

## Controlled issues and deferred concerns

Issue codes: `MISSING_DATE`, `INVALID_CALENDAR_DATE`, `AMBIGUOUS_DATE_FORMAT`,
`UNSUPPORTED_DATE_FORMAT`, `AMBIGUOUS_LOCAL_TIME`, `NONEXISTENT_LOCAL_TIME`,
`DATE_ORDER_CONFLICT`, `DATE_ONLY`, and `OUT_OF_RANGE`. Each includes source,
batch, position, original index label, column, and a concise reason. Original
values remain accessible through evidence without being printed in diagnostics.

Safe-date means interpretable, not validated or warehouse-ready. Negative prices,
negative quantities, missing identifiers, and duplicates are not repaired or
filtered. Unknown timezones and nullable cancellation markers need downstream
loading policies before Volume 4.4; this milestone does not invent those policies.
No source-profile registry, validation engine, healing, quarantine persistence,
database loading, schema changes, or application credential changes are included.

## Reproducible streaming verification

```powershell
.\dataxaienv\Scripts\python.exe scripts/verify_dates_real_data.py
$verificationExitCode = $LASTEXITCODE
Write-Output "Exit code: $verificationExitCode"
```

Defaults: ignored `data/online_retail_II.xlsx`, batch size 50,000, expected
1,067,371 rows and 22 batches. The script performs transformation only, checks
input immutability, lineage/accounting, date evidence, cancellation markers,
and preservation counts, and reports observed safe/unresolved/status/issue counts.
Failures return nonzero; output is ASCII-compatible. Optional `--trace-memory`
reports Python-traced allocation peak, not process RSS. Without that option,
memory is not measured. Processing retains bounded batches, not a full workbook
DataFrame. The source library's workbook structures may still consume memory.
