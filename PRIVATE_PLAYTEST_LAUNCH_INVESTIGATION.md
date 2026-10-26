# Private-playtest launch investigation — 2026-10-25

The original failure is **not yet attributable to a particular startup check**.
The previous catch-all discarded the exception and stage. Its message proves
that Python reached the launcher's exception handler, not that Discord login or
database setup succeeded. Lack of an online bot does not prove that no startup
DDL/backfill ran: failure could have happened after those writes. Preserve the
current gameplay database and test progress; do not reprovision or reset it.

## Findings and confirmed fixes

- The confirmed endpoint/name/role/cluster values satisfy the existing target
  validation. Independently successful psql authentication does not establish
  the Python interpreter/dependencies, Discord token/application/guild settings,
  asyncpg connection path, database ownership, schema CREATE or startup DDL.
- All 95 actual slash commands registered and serialized offline using synthetic
  settings with startup/network/database calls blocked. The installed interpreter
  was Python 3.15.0, with discord.py 2.7.1, asyncpg 0.32.0, python-dotenv 1.2.4 and
  Pillow 12.3.0. That does not identify the interpreter used by the failed launch;
  `python` previously depended on shell resolution. No interpreter downgrade or
  command-registration change is justified by the evidence.
- The demonstrated diagnostic defect is fixed: each boundary now emits a fixed
  stage and safe exception family. Nested failures retain the innermost stage,
  including errors raised in asyncpg pool-init tasks during Discord setup.
- A demonstrated cleanup defect is fixed: failure closing the repository could
  previously skip Discord HTTP cleanup. A `finally` now closes that client too.
- No defect in the fixed identity SQL or its permission/ownership/isolation
  predicates was demonstrated. SQL and rejection requirements remain unchanged;
  failed predicates now have fixed reason codes instead of one generic rejection.
- The executable script delegates to the canonical `private_playtest` module so
  the entry point and bot/config/storage share the same diagnostic exception type.
  This supports consistent new diagnostics; it is not evidence of the old cause.

## Failure boundaries

| Stage | What failed or can reject |
| --- | --- |
| PowerShell `INTERPRETER_RESOLUTION` | Selected executable missing/unavailable; scripts/functions are not used as Python |
| PowerShell `PROJECT_CHECK`, `TARGET_INPUT`, `CONFIGURATION`, `TARGET_REVIEW`, `ENVIRONMENT_SETUP`, `SECRET_PROMPT`, `PYTHON_PROCESS` | Project/input, target approval, secure credential transfer or child-process launch |
| Python `CONFIGURATION` | Missing/invalid target, token/password or mode authorization; no dotenv fallback |
| `IMPORT_DISCORD`, `IMPORT_ASYNCPG`, `IMPORT_DOTENV`, `IMPORT_PIL` | Missing/incompatible dependencies or native-library loading |
| `BOT_IMPORT` | Imports, bot construction, decorators and slash-command registration |
| `DISCORD_LOGIN` | REST authentication/application-info retrieval; invalid token, HTTP, timeout or transport failure |
| `DISCORD_APPLICATION_CHECK` | Missing application/user, wrong application ID, Public Bot enabled or authenticated user not a bot |
| `DISCORD_GUILD_FETCH`, `DISCORD_GUILD_CHECK` | Guild REST access, wrong guild ID or mismatched result |
| `DATABASE_CONNECT` | asyncpg authentication, network, timeout, server settings or pool startup |
| `DATABASE_IDENTITY_QUERY` | Read-only transaction begin/fetch/end or fixed catalog query error |
| `DATABASE_IDENTITY_CHECK` | Database/current/session role, owner, cluster, version, read-only state, path/schema, CREATE capability, elevated flags, memberships or event triggers |
| `DATABASE_CORE_SCHEMA` | Core startup DDL/acquisition; new pool is terminated on failure |
| `SETUP_COMBAT`, `SETUP_LIVING_DAO`, `SETUP_SECT_HALLS`, `SETUP_SECT_V022`, `SETUP_BONDS_V022`, `SETUP_GOLDEN_SANDBOX` | Corresponding setup import/DDL/backfill |
| `GUILD_COMMAND_COPY`, `GUILD_COMMAND_SYNC`, `ANNOUNCER_START` | Local command copy, Discord guild synchronization or task start |
| `GATEWAY_CONNECT` | WebSocket/gateway startup after login/setup |
| `CLEANUP` | Database/client disposal; failed cleanup never produces diagnostic success |
| `RUNTIME` | Event-loop/client-run failures outside a more specific boundary |

The ordinary login/setup/connect order is unchanged.
[discord.py login/setup hook](https://discordpy.readthedocs.io/en/stable/ext/commands/api.html#discord.ext.commands.Bot.setup_hook),
[asyncpg pool initialization](https://magicstack.github.io/asyncpg/current/api/index.html#asyncpg.pool.create_pool).

## Sanitized output

`PRIVATE_PLAYTEST_RUNTIME` reports numeric Python/package versions and whether a
virtual environment is active. It reports no executable/installation paths.
`PRIVATE_PLAYTEST_PROCESS_EXIT` reports only a nonzero numeric child exit code,
including interpreter failures that occur before Python can emit a startup stage.
`PRIVATE_PLAYTEST_STOPPED` reports only fixed stage/family/reason fields, an
allowlisted HTTP status and allowlisted SQLSTATE class/code/provenance.

Families distinguish dependency imports, Python syntax/errors, Discord token
rejection, forbidden/not-found/other HTTP responses, local timeout/connection/OS
errors and the existing PostgreSQL/driver families. `28P01`, for example, can
identify server password rejection; `42501` can identify permission denial.
Driver class-default SQLSTATEs remain unattributed instead of being mislabeled
as server errors. No messages, exception args, stack frames, SQL, parameters,
headers, URLs, response bodies, connection strings, credentials or returned
catalog records are printed. The existing operator target-review summary still
shows the public settings the operator entered, before masked secret prompts.

Example of a possible future result, not an observed live diagnosis:

```text
PRIVATE_PLAYTEST_STOPPED {"stage":"DISCORD_APPLICATION_CHECK","family":"SAFETY","reason":"PUBLIC_BOT_ENABLED","http_status":null,"sqlstate_class":null,"sqlstate_code":null,"sqlstate_source":"UNAVAILABLE"}
```

## One targeted next command — separate authorization required

After approval for test Discord REST login/guild reads and a strictly read-only
PostgreSQL connection, run from this **updated** bot checkout:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_private_playtest.ps1 -DiagnoseOnly -AuthorizeReadOnlyConnection -AuthorizeDiscordLogin
```

Use `5433`, `dao_playtest_v023_rc1`, `dao_playtest_rc1`, cluster identifier
`7699864005918920747`, and the test application's/guild's actual IDs. The host
remains fixed at `127.0.0.1`. Approve the displayed target with
`DIAGNOSE PRIVATE PLAYTEST`, then use the masked test-token/password prompts.
Use the same interpreter as the failed launch; if that was an explicit environment,
append its known `-PythonPath` value. Do not paste credentials into arguments.

This mode does not import `bot.py`, open the gateway, sync commands, start tasks,
create a pool, execute setup/migrations, run integration tests or modify gameplay
records. It uses a plain Discord REST client and the existing fixed identity
query with read-only enabled from PostgreSQL connection startup. Both clients
close on success/failure; failed database closure attempts termination before
HTTP cleanup. Its narrower authorization cannot load the writable bot config or
open the writable repository pool. Database-write flags are rejected in this mode.

The earliest failure gives the next concrete action: select the proper Python
environment for import errors; review the test token/application/private setting
or guild for Discord stages; inspect the exact SQLSTATE/reason for database
stages. Do not grant permissions, change pins, reset state or retry alternate
endpoints to force success. Only the sanitized runtime/result lines are needed
for follow-up; do not share secrets or a full terminal transcript.

`PRIVATE_PLAYTEST_DIAGNOSTIC_PASSED` establishes REST/catalog compatibility and
acknowledged cleanup at that time. It cannot certify DDL/backfill, guild command
sync, gateway startup or gameplay. If it passes, those later stages require a
separately authorized ordinary launch using the now-instrumented startup path.
No live diagnostic or bot launch was performed during this investigation.

Changed runtime files: `private_playtest.py`, `launch_private_playtest.ps1`,
`bot.py`, `storage.py`. Regressions: `test_private_playtest.py` and
`test_private_playtest_diagnostics.py`; static validation includes this guide.
Historical attempts, receipts, journals and private-test data were not modified.

Offline validation: **907 Python tests passed**, including 30 new startup/diagnostic
regressions and the updated 33 private-launch tests; **100 PowerShell fixture/static
checks passed**. Python compilation, PowerShell/document parsing and whitespace
checks passed with zero offline guard violations, database connections, Docker
invocations or database-backed suite executions. The command-registration test
constructs only a disconnected client and never invokes login/startup/sync.
