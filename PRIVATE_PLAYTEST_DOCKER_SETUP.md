# v0.23 private test: reuse the existing Docker PostgreSQL

Prepared offline. None of the Docker, SQL, Discord installation or launch steps
below has been executed. Each external stage needs your authorization.

The recorded installation can satisfy the launcher: reuse the container and
create one separate gameplay database/login inside it. A second container is
unnecessary unless verification shows the recorded installation has changed.
The existing integration database/login cannot be used by this launcher.

| Recorded setting | Value |
| --- | --- |
| Container | `dao-postgres18-test` |
| Full container ID | `18667622c6f45feca03e96570d82fde22ba822b66d1fc3c9585b9d9f2899b8ea` |
| PostgreSQL | 18.6 |
| Windows client endpoint | `127.0.0.1:5433` |
| Container listener | TCP 5432 |
| Cluster system identifier | `7699864005918920747` |
| Existing Docker administrator | `postgres` |

These are historical pins, not a fresh live verification. They come from
`POSTGRES_DOCKER_PREFLIGHT_LAUNCH.md` and the existing provisioning receipt/attempt.
No secret files were read. The current network mapping, authentication rules and
availability remain unverified. Do not recreate the container or volume.

## 1. Verify the existing container — authorization: Docker inspection

In PowerShell, inspect only public identity/network fields, never full inspect
output or `Config.Env`:

```powershell
$daoPlaytestContainer = '18667622c6f45feca03e96570d82fde22ba822b66d1fc3c9585b9d9f2899b8ea'
docker inspect --type container --format '{{.Id}} | {{.Name}} | running={{.State.Running}} | ports={{json .NetworkSettings.Ports}}' $daoPlaytestContainer
```

Require that exact ID, name `/dao-postgres18-test`, running `true`, and only the
recorded PostgreSQL binding `127.0.0.1:5433 -> 5432/tcp`. Stop on a mismatch.
If the container is stopped, starting that existing container needs separate
authorization; no replacement container is part of this plan.

## 2. Provision only the new gameplay target — authorization: SQL and new resources

Use the existing Docker administrator. This connects to the container's own
maintenance database, never the Windows PostgreSQL service. The password prompt
is for the Docker `postgres` account. Do not use production credentials.

```powershell
docker exec --interactive --tty --user postgres $daoPlaytestContainer env -u PGPASSWORD -u PGHOST -u PGPORT -u PGDATABASE -u PGUSER -u PGSERVICE -u PGSERVICEFILE 'PGOPTIONS=-c default_transaction_read_only=on' PGPASSFILE=/dev/null PGSSLMODE=disable psql --no-psqlrc --password --host=127.0.0.1 --port=5432 --username=postgres --dbname=postgres --set=ON_ERROR_STOP=on --set=HISTFILE=/dev/null --pset=pager=off
```

Inside psql, first paste this read-only identity/name check. Its failure exits
psql. Proceed only after `Identity and unused names verified`:

```psql
BEGIN READ ONLY;
SELECT (
 pg_catalog.current_database() = 'postgres'
 AND current_user = 'postgres' AND session_user = current_user
 AND pg_catalog.current_setting('server_version_num')::int = 180006
 AND pg_catalog.current_setting('transaction_read_only') = 'on'
 AND NOT pg_catalog.pg_is_in_recovery()
 AND (SELECT system_identifier::text FROM pg_catalog.pg_control_system()) = '7699864005918920747'
 AND (SELECT rolsuper FROM pg_catalog.pg_roles WHERE rolname=current_user)
 AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_database WHERE datname='dao_playtest_v023_rc1')
 AND NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname='dao_playtest_rc1')
) IS TRUE AS dao_ok
\gset
\if :dao_ok
\echo Identity and unused names verified
\else
\echo STOP: identity mismatch or target already exists; no provisioning permitted
\quit 1
\endif
COMMIT;
```

After approval for creating this new role/database, paste:

```sql
SET default_transaction_read_only = off;
CREATE ROLE dao_playtest_rc1 NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 5;
CREATE DATABASE dao_playtest_v023_rc1 OWNER dao_playtest_rc1 TEMPLATE template0 ALLOW_CONNECTIONS false CONNECTION LIMIT 5;
REVOKE ALL ON DATABASE dao_playtest_v023_rc1 FROM PUBLIC;
ALTER DATABASE dao_playtest_v023_rc1 SET default_transaction_read_only = off;
SET password_encryption = 'scram-sha-256';
```

Next enter this **single command separately**, answer its two hidden password
prompts, and keep the new password in your password manager:

```psql
\password dao_playtest_rc1
```

After the password command succeeds, finish:

```psql
ALTER DATABASE dao_playtest_v023_rc1 ALLOW_CONNECTIONS true;
ALTER ROLE dao_playtest_rc1 LOGIN;
\quit
```

This creates a fresh database from `template0`; ownership gives the new login
CREATE in its own `public` schema on stock PostgreSQL 18. No schema, ACL, setting
or password in any existing database/role is altered. The new database's PUBLIC
rights are revoked before connections are enabled, so it adds no PUBLIC database
access for `dao_test_user`. Read/write default is changed only for the new
database; existing integration read-only defaults remain intact.
[PostgreSQL CREATE DATABASE](https://www.postgresql.org/docs/18/sql-createdatabase.html),
[database-owner role](https://www.postgresql.org/docs/18/predefined-roles.html).

`\password` avoids a plaintext password in SQL/history. No password belongs in a
PowerShell argument or environment file.
[psql password command](https://www.postgresql.org/docs/18/app-psql.html).
On any error or existing name, stop and preserve partial state; do not drop,
reset or rerun provisioning blindly. If these resources were already created
successfully, skip provisioning on later sessions and use the same test progress.

## 3. Create the Discord test app — authorization: new app/token and guild install

1. Open the [Developer Portal](https://discord.com/developers/applications), choose
   **New Application**, name it **Dao of Eternity v0.23 TEST**, and record its
   **Application ID** from General Information.
2. On **Bot**, generate/reset the token for this new app and save it in your
   password manager. Keep **Requires OAuth2 Code Grant** and all three privileged
   intents off. This bot uses the gateway; leave Interactions Endpoint URL empty.
3. Under **Installation**, select **Guild Install** only and set **Install Link**
   to **None**. Then on **Bot**, turn **Public Bot** off and save. The launcher
   requires `bot_public=false`.
4. Use a private test server you control. Enable Discord **User Settings ->
   Advanced -> Developer Mode**, right-click that server and **Copy Server ID**.
5. Generate the owner-only invitation below, open the printed URL manually, and
   authorize installation into that private server. No client secret/public key
   is needed. Leave existing production applications/tokens unchanged.

The app/token/install flow follows [Discord's setup guide](https://docs.discord.com/developers/quick-start/getting-started).
The Install Link requirement is documented in
[Discord's private-bot guidance](https://support-dev.discord.com/hc/en-us/articles/21204493235991-How-Can-Users-Discover-and-Play-My-Activity).
See [copying server IDs](https://support.discord.com/hc/en-us/articles/206346498-Where-can-I-find-my-User-Server-Message-ID).

```powershell
$daoTestAppId = Read-Host 'NEW test application ID'
$daoTestGuildId = Read-Host 'Private TEST server ID'
if ($daoTestAppId -notmatch '\A[1-9][0-9]+\z' -or $daoTestGuildId -notmatch '\A[1-9][0-9]+\z') { throw 'Enter decimal IDs only.' }
'https://discord.com/oauth2/authorize?client_id={0}&scope=bot%20applications.commands&permissions=52224&integration_type=0&guild_id={1}&disable_guild_select=true' -f $daoTestAppId, $daoTestGuildId
```

`52224` requests View Channels, Send Messages, Embed Links and Attach Files.
Allow those in the test channel; testers also need Use Application Commands.
Administrator is unnecessary. The URL contains IDs only, never the bot token.
[OAuth2 bot authorization](https://docs.discord.com/developers/topics/oauth2),
[permission flags](https://docs.discord.com/developers/topics/permissions).

## 4. Launch — authorization: test Discord login/sync and gameplay database writes

From `dao_of_eternity_bot`, with the existing dependencies installed:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_private_playtest.ps1 -AuthorizeDatabaseWrites -AuthorizeDiscordLogin
```

| Launcher input | Enter |
| --- | --- |
| Host | Automatic: `127.0.0.1` |
| Port | `5433` |
| Database | `dao_playtest_v023_rc1` |
| Login | `dao_playtest_rc1` |
| Cluster system identifier | `7699864005918920747` exactly |
| Application ID | The NEW private test application's ID |
| Guild ID | Your private TEST server ID |
| Review phrase | `LAUNCH PRIVATE PLAYTEST` |
| Test bot token, masked prompt | Token from the NEW application |
| Database password, masked prompt | Password set by `\password dao_playtest_rc1` |

If necessary, append `-PythonPath 'C:\path\to\existing\Scripts\python.exe'`
to select your existing Python environment. No dotenv, Windows database URL or
production token is used. Every new pool connection verifies the recorded cluster,
database and owner/login before migration/gameplay writes. The identity check
itself needs a connection; offline preparation cannot certify a current endpoint.

Startup creates the existing application tables, performs the sect-history
backfill, syncs guild commands and starts the bot. It creates no additional
container or gameplay database. Developer commands stay disabled.

## 5. First playable check and remaining limits

In the private server, select this TEST app's `/start`, then `/qi`. Expect a
private character and the Shattered Heavens dashboard with exact Qi text and
controls. Repeat `/start` without losing progress. Wait about two minutes and
refresh `/qi`; passive earnings should increase. Stop with Ctrl+C; later launches
retain the same test progress. The longer feature checklist is in
`PRIVATE_GAMEPLAY_V023_RC1.md`.

Minimum remaining work: verify the existing Docker target, provision only the
new database/login, create/install the separate Discord app, then authorize the
launcher. If container identity, administrator authentication, new-role network
authentication or any launcher check rejects, stop for review; no HBA, grants,
old schemas or safety pins are changed automatically. The unresolved historical
inventory failure remains a separate integration-certification issue, outside
normal gameplay startup. Neither old schema nor consumed attempt is reused.

No bot/runtime code was changed for this plan. Repeated offline validation passed
877 Python tests and 100 PowerShell checks with zero guard violations or database
connections. All four PowerShell blocks above parsed without execution; the
24 original fresh-bundle/recovery-inspection files still match their SHA-256
baseline. SQL and live Discord remain untested.
