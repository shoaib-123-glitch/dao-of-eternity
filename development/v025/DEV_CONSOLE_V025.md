# Developer Testing Console (v0.25 sandbox only)

A private QA tool for shortening regression tests. It resets cooldowns, sets
bounded test resources, stages combat scenarios and prepares companion, story
and sect tests. It is not a player feature and it is **off by default**.

It exists only in `development/v025/`. The root v0.24 runtime and launcher have
no console code. Nothing here has been run against PostgreSQL or Discord.

## How it stays off

The console turns on only when **all** of these hold. If any console setting is
present but one check fails, startup stops with a fixed reason instead of
running half-enabled:

1. The v0.25 launcher is started with `-AuthorizeV025Candidate`,
   `-AuthorizeDatabaseWrites`, `-AuthorizeDiscordLogin` and
   `-EnableDeveloperConsole`. Without that switch the launcher removes any
   inherited `DAO_DEV_CONSOLE*` variables.
2. The target passes the existing v0.25 candidate policy: database
   `dao_playtest_v023_v025_*` and login `dao_playtest_v025_*`. The v0.24
   database `dao_playtest_v023_rc1` can never qualify.
3. You retype the database name and cluster identifier, and they match the
   launch target exactly. The guild must also match.
4. You give 1–3 developer Discord IDs and 0–10 disposable test-account IDs.
5. At startup, the connected database must report that exact name and cluster
   identifier and carry the database comment `dao-v025-developer-sandbox`.
   This is checked again inside every developer action.

| Stop reason | Meaning |
| --- | --- |
| `DEV_CONSOLE_CONFIGURATION_REJECTED` | A console setting is missing, malformed or doesn't match the launch target. |
| `DEV_CONSOLE_SANDBOX_IDENTITY_REJECTED` | The database name, cluster identifier or sandbox marker comment doesn't match. |

## Enabling it — only after a separately authorized sandbox launch

These steps need their own approval; none were performed while building this.

1. Prepare a **disposable** v0.25 sandbox database and login as described in
   `RELEASE_V025_LIVING_WORLD.md`. Never use the v0.24 database.
2. Once, as that database's owner, mark it as a developer sandbox:

   ```sql
   COMMENT ON DATABASE dao_playtest_v023_v025_<name> IS 'dao-v025-developer-sandbox';
   ```

3. From `development/v025`, launch with the console switch:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_private_playtest.ps1 -AuthorizeV025Candidate -AuthorizeDatabaseWrites -AuthorizeDiscordLogin -EnableDeveloperConsole
   ```

   After the normal target prompts and approval, it asks you to retype the
   database name and cluster identifier, enter developer and test-account IDs,
   and type `ENABLE SANDBOX DEVELOPER CONSOLE`.
4. On successful startup, the bot terminal logs that the console is enabled.
   In Discord, an allowlisted developer sees a **Developer console** button on
   `/guide`. Nobody else sees it, and pressing an old copy of it is refused.

To turn the console off, restart without `-EnableDeveloperConsole`. To retire
a sandbox, drop the whole disposable database (separately authorized).

## What it can do

Every action targets your own character or an allowlisted test account. Each
is one transaction, receipted by Discord interaction ID and audited.

| Category | Actions |
| --- | --- |
| Cooldowns | Cultivation practice, focus change, companion activity, camp breathing (`/recover`), exploration, featured/rare daily bonus, teacher lesson, rare story lore |
| Test resources | Cultivation Qi (up to stage capacity), camp HP, battle HP, Combat Qi, combat marks (0–500), one material (0–20), beast Qi (up to stage cap) |
| Combat scenarios | Heavy attack next turn, Second Wind emergency, enemy at 1 HP, player at 1 HP with Second Wind spent |
| Companion | Companion trust, bond level, beast stage, evolution prerequisites |
| Story | Grant region proof, resolve chapter (no marks paid), rewind, NPC trust, clear NPC memories, personal form, form preparation |
| Sect | Clear your own commission cooldown and daily count |

A reset removes only the saved timestamp. The rule itself is unchanged, so the
next normal action starts the usual cooldown again, and other players are never
affected. **Retreat** needs no staging: use the normal Retreat button.

**Recent audit** lists the last ten actions: who, target, action and parameters.

## Built-in limits

- **No raw access.** Every action is a fixed function with typed, range-checked
  parameters. There is no SQL text, shell command, file access or arbitrary
  field editing.
- **Server-side checks.** The check runs on every button, menu, form and
  handler, not just when the panel opens.
- **Duplicate deliveries.** The same interaction ID returns the first reply and
  changes nothing more. A failure at any step rolls back every change.
- **Separate records.** Developer actions write only to `dev_console_actions`.
  They never touch player receipt tables, quest claims or the treasury.
- **Marked characters.** Each changed character gets
  `dev_console_v025.tainted = true` with an action count.
- **Multiplayer stays genuine.** The sect reset clears only your own record.
  Shared projects still need distinct real players; the console can't add
  contributors, renown or votes.
- **Telemetry.** Denials and completed actions go to the disabled-by-default
  security observer.

## Security limitations

- Discord IDs prove accounts, not people. Allowlist only accounts you control.
- The console trusts the database comment and identity values. Anyone with
  owner rights on another database could add the same comment, so the name,
  cluster and launch-target checks matter as much as the marker.
- Developer-marked characters stay in the sandbox. **Never restore or copy a
  sandbox database into a real progression database.** The marker records what
  was changed but can't stop a manual restore.
- Staged victories still pay normal rewards, so the real payout path is tested.
  Those rewards stay on the marked test character.
- The fake-database tests prove Python behaviour, not PostgreSQL locking.

## Offline evidence

`test_dev_console_v025.py` has 63 tests. They cover configuration gates and
disabled mode, marker checks, unauthorized users, guilds and targets,
parameter bounds, every action against the real game rules, duplicate and
concurrent deliveries, rollback at each write, separate receipts, the UI and
`bot.py` handlers, and the launcher switch.

## Remaining live checks (sandbox only, separately authorized)

1. Startup stops if the marker comment is missing, and logs "enabled" when it
   is present.
2. Only allowlisted developers see the button. A tester pressing a shared
   console panel is refused.
3. For each cooldown: run the normal action, see the cooldown, reset it, run
   it again, and see the cooldown return.
4. Each combat scenario in Manual, Auto and Assisted modes.
5. A double-click or retried interaction changes nothing twice.
6. Recent audit matches what you did.
7. A second tester's cooldowns and progress are untouched throughout.
