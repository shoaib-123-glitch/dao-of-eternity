# Private gameplay test: v0.23.0-rc1

For an existing failed launch, use the
[focused launch investigation and read-only diagnostic](PRIVATE_PLAYTEST_LAUNCH_INVESTIGATION.md)
before attempting another gameplay launch. It does not require reprovisioning.

For the shortest setup using the already recorded Docker installation, follow
[the consolidated Docker/Discord setup plan](PRIVATE_PLAYTEST_DOCKER_SETUP.md).
It provides the exact new database/login, Developer Portal steps and launch inputs.

This prepares a real private Discord session, separate from the failed integration
attempt. No launch, database connection, command sync or secret-file access was
performed during preparation. Use the dedicated launcher below; ordinary
`python bot.py` retains the existing production configuration behavior.

## Before the first launch

- Have an administrator independently identify a **dedicated disposable test
  PostgreSQL 18 cluster**, its TCP port and its recorded `system_identifier`.
  The launcher accepts only `127.0.0.1`, port 1024-65535 excluding 5432. It must be separate
  from the Windows PostgreSQL service holding player data. A different database
  name on that production cluster is insufficient. Do not assume a host or port
  from an old integration record is a safe gameplay target.
- Have a fresh, empty database named `dao_playtest_v023_<name>` and a dedicated
  login named `dao_playtest_<name>`, provisioned separately with approval. Use
  lowercase letters/digits/underscores for the suffix (database: 1-24 characters;
  login: 1-32). The login must own that database and have CREATE on its `public`
  schema. It must have no elevated role flags, direct role memberships or
  production access; the database must have no enabled event triggers.
- The login must **already be permitted** to execute the read-only
  `pg_control_system()` identity lookup. The launcher grants nothing: missing
  permission stops the session. This is a real prerequisite, not a reason to
  elevate the login or bypass the identity check. No provisioning, SQL or
  privilege changes are performed by this procedure during offline preparation.
- Use a separate Discord Developer Portal **test application and test bot token**,
  installed only in the private test guild with `bot` and `applications.commands`
  scopes. Disable Public Bot (`bot_public=false`) in its settings. Record its
  application ID and the guild ID independently. Allow View
  Channels, Send Messages, Embed Links and Attach Files in the test channel;
  Administrator and privileged member/message-content intents are unnecessary.
- Have the existing Python environment and `requirements.txt` dependencies ready.
  Never paste tokens/passwords into command arguments, the chat, a transcript or
  a new environment file. The launcher prompts for secrets without echoing them.

If the independent cluster or test application does not already exist, their
separately approved creation is the remaining operational blocker. Nothing in
the offline review can establish which process currently owns a network port.

## One launch command

From this bot directory, run this **only after authorizing both the dedicated test
database writes and the test Discord login/guild command sync**:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_private_playtest.ps1 -AuthorizeDatabaseWrites -AuthorizeDiscordLogin
```

Supply the independently verified port, database, login, cluster identifier,
application ID and guild ID when prompted. Review the summary and type
`LAUNCH PRIVATE PLAYTEST` before entering the test token/password securely.
Use Ctrl+C to stop. The database retains test progress for the next session;
this procedure does not delete it or reset any characters.

The wrapper uses explicit private-test configuration without dotenv loading,
rejects implicit/default targets, clears ambient PostgreSQL/production credential
settings, and forces developer commands off. Before database setup, it verifies
the logged-in application ID, private-bot setting and access to the pinned guild.
Each newly created pool connection, including replacements, then passes a
read-only PostgreSQL identity check before it can perform application setup or
gameplay. The check pins cluster identifier, database and owner/login, PostgreSQL
18.x, schema/search path, role restrictions and absence of enabled event triggers.

No endpoint is certified offline. A changed port alone is insufficient; physical
cluster separation and the independently recorded identifier remain operator
prerequisites. A misdirected connection must fail before application DDL/writes,
but the identity check itself necessarily requires connecting to its supplied
endpoint. Never substitute the failed integration schema, its reservation, journal
or consumed bundle.

## What startup does

`bot.py` ordinarily calls `config.load_config()` at import. Production defaults
to `.env`; the old test path loads `.env.test` with override and checks only
database/user names. Those names alone do not prove endpoint isolation. The
private launcher supplies its separate configuration before importing the bot;
it does not read either file or inherit an ambient database URL/token as a target.

Discord login invokes `DaoBot.setup_hook()`. In private mode, application/guild
checks precede `Repository.connect(PrivatePlaytestConfig)`, which creates the guarded
pool and runs core table setup, followed by combat, Living Dao, sect
halls, v0.22 sects, households/bonds and Golden Core sandbox setup. These are real
DDL/writes. The v0.22 sect setup also inserts missing Gathering Hall history;
`CREATE TABLE IF NOT EXISTS` is not a read-only dry run. Existing startup contains
31 CREATE statements for 29 unique tables; the separate integration manifest's
27-table coverage is narrower and does not describe the entire bot startup.
None of these statements was executed during preparation. Startup then copies and
syncs commands to the configured guild and starts the five-minute Qi-event loop.
The loop can write announcement records and post when a world channel is set.
Leave `/set_qi_channel` unused during the initial smoke test.

v0.23 adds no DDL migration: focus/mastery, beast practice and sect renown are
lazy additive JSON state. A clean gameplay database still requires the existing
startup tables. Every gameplay/status command that settles Qi may write; the
dashboard is not a database read-only inspection. The old integration runner and
all recovery records are outside this launch path and remain unchanged.

## Short gameplay checklist (about 10 minutes, then time-gated follow-up)

Use the Discord option labels if the client displays them instead of the option
values below. Keep test results/screenshots free of credentials.

1. `/start`, then `/qi`. Expect a private character, Eternal Cultivation enabled,
   the Shattered Heavens card plus exact text, and Refresh / Cultivate / Journey
   controls. Run `/start` again: progress must remain. Wait two minutes and refresh:
   earned Qi must increase, including when stage Qi is full (overflow is banked).
   Base Qi/hour is 40 / 60 / 85 by realm; root, focus and current world effects
   change the displayed rate. Compare totals rather than demanding exactly 40.
2. `/affinities`, then `/awaken root:Lightning background:Wandering Mortal` (or
   choose another common root). The root is permanent. `/journey` must show its
   practice utility and no payment requirement. With another tester, try Wind
   for extra mastery or Water for recovery; no root rerolls are needed.
3. `/cultivation_focus focus:gather`, then `/qi`: the selected focus contributes
   +10% passive Qi from its activation time. An immediate change to Insight or
   Resonance must show the six-hour cooldown. Other testers can initially choose
   Insight (+2 mastery/practice) or Resonance (13% encounter chance versus 8%).
4. Before the first practice, earn a companion: `/explore region:thunder_rift`
   gives 10 marks; `/quest_claim quest:scout` adds 6. `/adopt beast:jade_fox` costs
   those 16 earned marks. `/beast_cultivation` must show independent cultivation.
   It earns 18 Qi/hour from adoption; a few minutes are enough to see growth.
5. `/cultivation_practice`, then `/journey`, `/qi` and `/beast_cultivation`.
   Expect +4 root mastery (+2 with Insight, +1 for Wind-equivalent roots), the
   root utility and +6 Beast Qi in addition to elapsed passive growth. Lightning
   banks 12 owner Qi; healing/cleansing utilities stop at existing caps. Immediate
   repeat practice, including the Cultivate button, must award nothing and show
   the six-hour cooldown. Refresh must remain usable. Controls expire after
   three minutes; open a new `/qi` afterwards.
6. Two more `/explore region:thunder_rift` expeditions, at least five minutes apart,
   earn 20 marks. `/sect_create name:Jade Lantern`, `/sect_doctrine doctrine:valor`,
   `/sect_commission activity:patrol`, then `/sect_domain`: expect 6 personal marks,
   6 sect renown and Valor identity. Treasury must not increase from commissions.
   A second tester can `/sect_join name:Jade Lantern`; a non-founder must be
   refused doctrine changes. Immediate repeat commission must award nothing.
7. Stop with Ctrl+C, restart through the same launcher and open `/qi` and
   `/beast_cultivation`: progress and elapsed offline growth must persist. The
   separate application should have commands only in the pinned private guild;
   direct messages/other guilds must not run gameplay.

Do not change the system clock, edit timestamps, reset cooldowns or enable dev
commands to accelerate these checks. After six real hours, practice and focus
changes become available; doctrines change after 24 hours. Commissions allow
three per member per UTC day, subject to the six-hour cooldown. Mastery adds
1% passive Qi at each 20 points, capped at 5%; it needs multiple sessions.

Rare encounters are **not guaranteed in a short session**. Successful practice
has an 8% chance (13% with Resonance) and may grant 2x/120-minute, 3x/60-minute or
5x/30-minute personal Qi. Inspect the active boost and expiry if one occurs;
an existing personal boon must remain rather than being overwritten. Deterministic
offline tests cover every outcome and expiry. Permanent growth beyond seven days
is likewise covered offline; a ten-minute live check cannot prove that duration.

## Release limits

The complete updated suite passed: **877 Python tests**, including **33 new private
launch regressions**, and **100 PowerShell fixture/static checks**. Syntax,
documented-command parsing and whitespace checks passed with zero offline guard
violations, database connections, Docker invocations or database-backed tests.
This is offline validation, not live database or Discord certification.
Actual login, pool creation,
table setup, persistence, permissions and Discord rendering remain unverified
until the authorized session runs. Stop on a target/identity rejection or startup
failure; do not retry against the Windows player-data database.

The historical `INVENTORY_QUERY` failure is still unresolved and blocks integration
certification. It is not part of normal bot startup and does not require rerunning
the consumed attempt before an explicitly authorized isolated gameplay smoke test.
No integration suite, catalog probe or old-schema recovery operation is authorized
by this launch command. Promote to v0.23.0 only after recording private-session
findings and separately resolving any required database certification.

Files added: `private_playtest.py`, `launch_private_playtest.ps1`,
`test_private_playtest.py` and this guide. Updated: `config.py`, `bot.py`,
`storage.py` (including failed-startup pool cleanup), `validate_offline.ps1`,
`fresh_integration.py` (future source seals only) and the release guide.
Historical bundles, receipts, attempts and recovery records were not modified.

API behavior was checked against the primary documentation:
[asyncpg connection/pool arguments and initialization hook](https://magicstack.github.io/asyncpg/current/api/index.html#asyncpg.pool.create_pool),
[discord.py login/setup hook](https://discordpy.readthedocs.io/en/stable/ext/commands/api.html#discord.ext.commands.Bot.setup_hook),
and [PostgreSQL 18 identity, privileges and control-data functions](https://www.postgresql.org/docs/18/functions-info.html).
