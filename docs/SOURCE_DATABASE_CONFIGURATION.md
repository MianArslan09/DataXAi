# Source PostgreSQL configuration

The Django application/warehouse database and upstream ETL source database are
separate connections. Source configuration never reads `POSTGRES_*` credentials.

| Execution context | Application database | Source database |
| --- | --- | --- |
| Windows development | `127.0.0.1:5432` | `127.0.0.1:5433` |
| Docker Compose services | `db:5432` | `source_db:5432` |

## Local configuration

Keep the existing source role password in ignored `.env` as
`SOURCE_POSTGRES_PASSWORD`. Do not copy a new example password over a working
installation. Changing this variable does not change an existing PostgreSQL
role's password or initialize an existing volume again.

Use the component keys illustrated in `.env.example`:
`SOURCE_POSTGRES_HOST`, `SOURCE_POSTGRES_PORT`, `SOURCE_POSTGRES_DB`,
`SOURCE_POSTGRES_USER`, and `SOURCE_POSTGRES_PASSWORD`. Leave
`SOURCE_DATABASE_URL` empty to derive it from those components. Passwords are raw
values here, not manually URL-encoded; SQLAlchemy `URL.create` performs encoding.
For the shared `.env`, use a single-line, single-quoted literal with **no
apostrophes and no trailing backslash**, with nothing after its closing quote:

```dotenv
# Synthetic example only; never replace a working password with this example.
SOURCE_POSTGRES_PASSWORD='synthetic"quote$literal # space\path'
```

Both parsers preserve double quotes, dollar signs (including `${NAME}`), hash
signs, spaces, and literal internal backslashes in this representation. Do not
add escape backslashes, double existing backslashes, or append inline comments.
Python-decouple strips surrounding quotes but does not decode Compose escapes.
An escaped apostrophe, double-quoted `\n`, unquoted interpolation/comment syntax,
or a backslash immediately before the closing quote is not portable between
these parsers. This supported subset is deliberate, not a password restriction
on PostgreSQL or SQLAlchemy.

For passwords outside that shared-file subset, omit the password assignment
from `.env` and supply the exact raw value through `SOURCE_POSTGRES_PASSWORD`
in the process environment for both Python and Compose. Environment values
must not include dotenv wrapper quotes or URL encoding. A trusted secret
manager can supply them; for interactive Windows use, this avoids a literal
password in command history:

```powershell
$sourceCredential = Get-Credential -UserName dataxai_source -Message 'Existing source role password'
$env:SOURCE_POSTGRES_PASSWORD = $sourceCredential.GetNetworkCredential().Password
# Run the required Python or Compose command in this same shell.
# When finished, remove the override (or restore its prior value if one existed).
Remove-Item Env:SOURCE_POSTGRES_PASSWORD
Remove-Variable sourceCredential
```

The process contains the raw secret while it is needed; do not print it or store
it in a script/profile. This is credential transport, not password rotation.
Multiline/NUL passwords are outside the documented and tested shared-file
contract. The tested environment alternative includes apostrophes, both quote
characters, dollar/hash/space combinations and trailing backslashes.
Never print the resolved URL or full Compose configuration when sharing diagnostics.


A nonempty `SOURCE_DATABASE_URL` remains an authoritative compatibility override.
It must already be a correctly percent-encoded SQLAlchemy PostgreSQL URL. A bad
URL does not cause a retry against another database. The Windows defaults are
source host `127.0.0.1`, port `5433`, database/role `dataxai_source`; there is no
hardcoded source password in Python. Configure the password explicitly.

## Precedence and Docker

- Python-decouple resolves process environment first, then `.env`, then defaults.
  Old PowerShell `$env:SOURCE_DATABASE_URL` or component overrides therefore
  continue to override the file until removed from that shell. Tests likewise
  respect a process `TEST_SOURCE_DATABASE_URL` before its `.env` value.
- Compose interpolation uses the invoking shell before the project `.env`.
  `SOURCE_POSTGRES_PASSWORD` is required, with no silent development-password
  fallback. The same interpolation supplies the source server and its clients.
- Service `environment` overrides `env_file`. Compose explicitly sets source
  host/port to `source_db:5432` for web, worker, and beat, with the source database
  and role matching `source_db`. It sets `SOURCE_DATABASE_URL` to empty, overriding
  any Windows-only URL from `.env`, and derives the URL in Python.
- Explicit remote-source deployments can use `SOURCE_DATABASE_URL` outside this
  local Compose stack, or deliberately override the Compose service environment.
  Do not put an unencoded password into a YAML URL.

No container recreation, password rotation, migrations, reseeding, or volume
deletion is necessary to repair the local client configuration. Already-created
application containers do not automatically reload new environment settings;
applying service configuration to them is a separate operational action.

## PostgreSQL integration tests

Tests resolve `TEST_SOURCE_DATABASE_URL` through python-decouple when explicitly
provided, otherwise use Django's configured `SOURCE_DATABASE_URL`. CI's existing
explicit test URL remains supported. Omit the test variable to share the local
source connection; an explicitly empty or malformed test URL is an error, not
a request to choose a fallback. There is no source fallback to application port
5432.

The availability gate uses a three-second connection timeout and disposes its
engine on success or failure. Connection failures still skip database-dependent
tests; skip messages contain no URL or credentials. Invalid URL configuration
fails collection with a sanitized message. Unexpected programming errors are
not suppressed as database unavailability. Always inspect passed/skipped counts:
an exit code of zero with skipped tests is not PostgreSQL verification.

From a fresh Windows terminal using the project's existing environment:

```powershell
.\dataxaienv\Scripts\python.exe manage.py check
.\dataxaienv\Scripts\python.exe -m pytest apps/etl/tests/test_sources_postgres.py -q -rN
```

The broader PostgreSQL selection also covers source-contract, alias mapping,
type conversion and dates:

```powershell
.\dataxaienv\Scripts\python.exe -m pytest `
  apps/etl/tests/test_sources_postgres.py `
  apps/etl/tests/test_sources_contract.py `
  apps/etl/tests/test_transforms_alias.py `
  apps/etl/tests/test_transforms_type_converter.py `
  apps/etl/tests/test_transforms_dates.py `
  -k 'postgres or all_three_sources' -q -rN
```

These tests require the existing 18-row `public.raw_transactions` fixture. They
do not seed or repair it. A healthy container confirms server availability,
not that a particular client's password, hostname, or port is correct.
