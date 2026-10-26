# Private playtest release boundaries and next operations

## What has been checked offline

The original v0.24 source passes 991 Python tests and 100 PowerShell checks in
this cycle, including command registration at 99. The crafting/equipment replay,
Second Wind priority and rare-root fixes are present. The original sources are
recorded by SHA-256 in development/v025-baseline.json and checked for changes.
No root runtime edits are part of v0.25 or the security module.

The existing v0.24 live observations remain valid evidence only for the actions
actually observed. v0.24 is still a release candidate, not declared Stable.

## v0.24 private regression — live authorization required

1. **Authorize stopping the existing test instance.** In its original console,
   press Ctrl+C once and wait for it to exit. Do not kill every Python process,
   stop a database container, delete files or reset the database.
2. **Authorize the usual private-test backup** using the established operator
   procedure. This preparation does not run a backup or read its contents.
3. **Authorize test database writes and Discord login/guild sync**, then run from
   the original bot directory, not development/v025:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_private_playtest.ps1 -AuthorizeDatabaseWrites -AuthorizeDiscordLogin
```

Use the same working interpreter as the successful test. The verified prior
target was 127.0.0.1:5433, database dao_playtest_v023_rc1, login dao_playtest_rc1,
cluster identifier 7699864005918920747, and the existing private test application
and guild. Confirm these remain current; the launcher checks their identities.
Supply secrets only through its secure prompts. No credential file is needed.
Startup performs its existing setup/writes even without a new SQL migration.

Select the intended application in the Discord command picker. Check its name
on the panels and open fresh /qi, /guide and /battle_status panels after restart.
Do not run two releases of the same application at once: guild command sync
replaces that application's command list and the old instance can restore an old list.

Record normal output for this compact checklist:

| Check | Expected |
| --- | --- |
| /start, /qi, /inventory, /companion | Existing Fire/Spirit/Insight character, Qi/reserve, marks, Fox, bond/trust/evolution and equipment retained. |
| /qi Refresh and Details/Compact; all six /guide topics | One compact panel, correct bot name, owner-only controls, no image delay or incorrect command names. |
| /recipes, one earned /craft and /equip | Exact cost/material deduction and chosen equipment visible in /inventory. A second separately issued craft is a new purchase, not a retry test. |
| Assisted Wolf; Manual and Auto separate hunts | Heavy pause, readable costs, Second Wind once, Fox assistance, persisted payout. Old battle panels cannot act on a new hunt. |
| Retreat and free /recover | No defeat/retreat loot, no cultivation loss, camp HP heals, two-minute breathing cooldown and natural healing persist across hunts. |
| /beast_bond when ready; later /companion | Memory/trust/Beast Qi retained and early repeat rejected; no passive-Qi expiry or automatic evolution. |
| Featured /explore then another after five minutes | Base rewards repeat normally; featured bonus once/UTC day, rare extra at most once/UTC day. Record materials; rare discoveries are not guaranteed. |
| Two genuine sect members and all three commission types | Existing six-hour personal cooldown, +6 marks/+5 renown per commission, treasury unchanged; project +15 shared renown and 2 marks per contributor claimed once. No payout when incomplete. |

For two players, the third commission needs one player's six-hour cooldown to
elapse; alternatively invite a genuine third tester. Distinct account IDs do not
prove distinct humans. Do not force failures, replay gateway packets, edit timers
or reset progression in the live save. Stable requires these remaining checks.

## v0.25 candidate — a separate live decision

The implemented candidate is in development/v025. The root launcher does not
load it. Its staged launcher requires an additional -AuthorizeV025Candidate
switch, and its policy refuses the original v0.24 database/login.

Before any v0.25 launch, separately authorize:

- A private sandbox database on the isolated test cluster, named
  dao_playtest_v023_v025_<name>, and dedicated login dao_playtest_v025_<name>.
  Its ownership, restricted privileges and identity-read permission must satisfy
  the existing launcher checks. No container or schema is provisioned here.
- If testing existing progression, an authorized backup/restore into this separate
  sandbox; never repoint v0.25 at the live v0.24 character. No backup is read here.
- Stopping the old test bot, test Discord login and guild command sync. Use the
  same application only after the other instance exits, or another private app.

After those approvals, from development/v025 only:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_private_playtest.ps1 -AuthorizeV025Candidate -AuthorizeDatabaseWrites -AuthorizeDiscordLogin
```

All original cluster, owner, schema/search-path and role checks still apply.
Ordinary production configuration is disabled in this staged copy; it cannot
fall back to environment files. Use /guide's Echoes of the Fallen Heaven button
or /quests, explore Mistwood, Stonepeak and Thunder Rift in chapter order, and
resolve one choice per chapter. After the Archive, teacher training is optional.
Record choices, NPC memories, trust, technique effects, once-only rewards and
preservation of old progression. These live checks have not been performed.

## Security lab

Synthetic demos and analysis require no bot, database, container, network probe
or live authorization. See portfolio/README.md. Application telemetry is disabled
by default, and no real game records are automatically exported to the SOC demo.
Do not publish sanitized-live events without a separate privacy review and export
authorization. Public portfolio artifacts are synthetic examples only.
