# Bound ingestion completion

The public services are `bind_file_ingestion()` and `complete_bound_ingestion()` in
`apps/etl/ingestion_completion.py`. Both require application PostgreSQL and outermost
autocommit. Binding takes a UUID ingestion and execution identity, input path,
`CsvScope(filename, batch_size, encoding)` or
`ExcelScope(filename, batch_size, sheet_names)`, date profile and transformation
version. Optional declared hash and counts are checked independently.

The ingestion must initially be OPEN and empty. Its sanitized source reference must
equal the logical filename. An identical existing binding can be retrieved after a
committed prefix. Binding uses the existing transformation configuration contract.
Parser runtime versions are pinned in the binding. Selected Excel sheets are ordered
and bounded to 128 names. None means every worksheet in workbook order.

Completion traverses the retained snapshot from zero, stages each batch once and
delegates interpretation to the accepted single-batch coordinator. It reuses the
writer's validated digest. A rolling, length-framed manifest distinguishes identical
content at different positions. Worksheet receipts include valid header disposition,
rows and emitted batches. CSV retains its explicit header budget.

The final transaction locks ingestion then proof, reconciles an ordered database
manifest, positions, selected execution, successful PipelineRuns and SQL NULL outcome
payloads. PostgreSQL composite foreign keys and unique outcome membership establish
record coverage. File parsing and pandas processing precede this transaction.
Existing services independently verify stored content and ledgers during processing.
Committed staging and terminal ledgers must remain immutable through cooperating
writers. Privileged database edits require a separate audit.

The receipt and COMPLETE transition commit together. Acknowledgment verifies the
stored receipt and accounting without reopening the snapshot. COMPLETE certifies
the bound parser scope and successful interpretation, including UNRESOLVED outcomes.
Business validation, repair, quarantine and warehouse publication retain their own
milestone responsibilities. PostgreSQL-source completion remains deferred.

Migration 0004 depends on 0003. It adds one protected primary-key one-to-one proof,
all-or-none receipt constraints and PostgreSQL immutable-binding/sealed-receipt
protection. Reversal locks the proof table and refuses whenever any row exists.
Historical ingestions receive no invented bindings. Deploy the migration before
using these services. Test reversal only in disposable databases.

Provision ETL_SNAPSHOT_ROOT outside the repository and served paths. Protect its
ancestors and object ownership. POSIX supports a private publisher directory or a
2750 reader-group directory. Windows requires NTFS DACLs limited to publisher,
SYSTEM, administrators and a named read-only parser group. Ordinary parser workers
must have no write, ownership or ACL-changing privileges. Retain snapshots and
database backups together. Restore a missing object only from bytes matching its
recorded digest and size. Hard links must be supported. Windows directory durability
and privileged transient modifications remain operational limitations.

## Owner verification

Run each command separately and stop on a nonzero exit code.

```powershell
Set-Location D:\dataxai
$env:DATAXAI_REQUIRE_STAGING_POSTGRES='1'
$env:PGCONNECT_TIMEOUT='3'
.\dataxaienv\Scripts\python.exe -m pytest apps/etl/tests/test_ingestion_completion.py apps/etl/tests/test_source_snapshots.py --ds=config.settings.test -q -o addopts='' -p no:cacheprovider -rs
.\dataxaienv\Scripts\python.exe -m pytest apps/etl/tests --ds=config.settings.test -q -o addopts='' -p no:cacheprovider -rs
.\dataxaienv\Scripts\python.exe -m pytest --ds=config.settings.test -q -o addopts='' -p no:cacheprovider -rs
.\dataxaienv\Scripts\python.exe manage.py check --settings=config.settings.test
.\dataxaienv\Scripts\python.exe manage.py makemigrations --check --dry-run --settings=config.settings.test
git diff --check
git status --short --branch
```

On Linux with the project's dependencies and checkout, use a native Linux temporary
directory for the directory-sync regression.

```sh
python -m pytest apps/etl/tests/test_source_snapshots.py::test_directory_sync_failure_leaves_verified_retry --ds=config.settings.test -q -p no:cacheprovider
```

## Separately authorized UCI experiment

The harness `python -m scripts.verify_bound_completion` reuses the existing benchmark
database guard. Provision and migrate an isolated database manually. Its name must
start with dataxai_completion_. Configure POSTGRES_* and ETL_SNAPSHOT_ROOT through
process-local environment variables. Output reports outside the repository.

```powershell
$env:DJANGO_SETTINGS_MODULE='config.settings.dev'
$env:POSTGRES_DB='dataxai_completion_review'
$env:POSTGRES_HOST='127.0.0.1'
$env:POSTGRES_PORT='5432'
$env:ETL_SNAPSHOT_ROOT='D:\DataXAi-Snapshots'
$execution=[guid]::NewGuid().ToString()
$ingestion=[guid]::NewGuid().ToString()
$common=@('--ingestion-id',$ingestion,'--execution-key',$execution,'--expected-db',$env:POSTGRES_DB,'--expected-host',$env:POSTGRES_HOST,'--expected-port',$env:POSTGRES_PORT,'--authorize-benchmark-writes')
.\dataxaienv\Scripts\python.exe -m scripts.verify_bound_completion @common --mode interrupt --path D:\dataxai\data\online_retail_II.xlsx --report D:\DataXAi-Benchmark-Evidence\completion-interrupt.json
.\dataxaienv\Scripts\python.exe -m scripts.verify_bound_completion @common --mode recover --report D:\DataXAi-Benchmark-Evidence\completion-recover.json
.\dataxaienv\Scripts\python.exe -m scripts.verify_bound_completion @common --mode ack --report D:\DataXAi-Benchmark-Evidence\completion-ack.json
```

Each command is a separate process. Interruption occurs after verified traversal
and the last durable batch, before sealing. Report files use exclusive creation.
Use new UUIDs and report names for baseline and fresh modes. Both require --path.
The fixed UCI experiment checks the historical digest against actual captured bytes,
both sheet names, 22 batches and 1,067,371 rows. It is intentionally a full-run
owner experiment. No historical throughput result establishes its performance.

Compare baseline versus fresh using equal hardware, PostgreSQL settings and batch
size, separate warm-ups and five alternating paired trials. Report median, range,
CPU and process peak memory. Recovery has its own measured execution. Service logs
expose snapshot capture, pre/post checks, inclusive traversal, processing, lock wait
and lock-through-commit seconds. SQL wrapper time excludes some fetching and commit
work. Nested durations overlap. Investigate repeatable regressions against trial
variability and stage measurements before attributing a cause.
