# Dao of Eternity: The Shattered Heavens — v0.23.0-rc1

Offline release candidate prepared on 2026-10-24. Existing commands, characters,
roots, gear, partnerships, sect buildings and earned progression are retained.
All updated source files are in this workspace; the update archive contains
complete files suitable for review and overlay onto this feature-branch checkout.
It is an update bundle, requiring the existing application and dependencies.

Private gameplay preparation now has a dedicated, explicitly authorized launch
path: see `PRIVATE_GAMEPLAY_V023_RC1.md` for prerequisites, one PowerShell command
and a short playable checklist. Its updated offline validation passed 877 Python
tests (including 33 launch regressions) and the same 100 PowerShell checks. The
new `dao_of_eternity_v023_private_playtest_update.zip` supersedes the earlier
review bundle for this launch path; neither archive contains credentials or
historical evidence. No private session has been launched during preparation.

## A playable cultivation loop

`/start` continues to enable permanent Eternal Cultivation automatically.
Passive growth has no seven-day deadline, subscription, streak requirement or
return-to-game requirement. Whole offline intervals count; a full stage stores
overflow indefinitely and breakthroughs remain manual. Old characters retain
their exact existing rates until they choose the new progression options.

| Command | New behavior |
| --- | --- |
| `/cultivation_focus` | Choose Gather (+10% passive Qi), Insight (+2 mastery per practice) or Resonance (13% rare encounters instead of 8%). Changing focus is free, with a six-hour cooldown. |
| `/cultivation_practice` | One free practice every six hours: elemental utility, four mastery, possible rare encounter and +6 Beast Qi when a companion is bonded. |
| `/journey` | Freely inspect focus, affinity, mastery, practice cooldown and discoveries, including during a breakthrough trial. |
| `/qi` | New Shattered Heavens dashboard, exact accessible text, companion growth, sect activities, and owner-bound Refresh / Cultivate / Journey buttons. |
| `/sect_commission` | Patrol, restore archives or tend gardens. Earn six marks and five shared renown; matching doctrine adds one renown. Three per member per UTC day, six-hour cooldown. |
| `/sect_doctrine` | Founder chooses Valor, Wisdom or Harmony; changes are free and limited to once per 24 hours. |
| `/sect_domain` | Existing treasury/halls plus doctrine, commission board and earned renown tier. |

Mastery is saved separately for every root, capped at 100. Every 20 points adds
1% passive Qi, up to 5%; Wind-equivalent roots earn one extra mastery per practice.
Gather and mastery bonuses start at their actual activation time, including
fractional seconds. They never increase Qi earned before activation. Existing
world events, elixirs, hall bonuses and dual/solo harmony remain within the 6× cap.

The sixteen roots now have cultivation identities alongside their existing combat
differences. Rare roots use the same utility and growth budget as their common
counterparts; their rarity is not a purchased advantage.

| Common root | Practice utility | Rare equivalents |
| --- | --- | --- |
| Fire | Cleanse two impurity; bank four Qi | Light |
| Water | Restore four meridian integrity | Blood |
| Earth | Restore four stability | Void |
| Wind | One additional mastery | Shadow, Time |
| Wood | Restore two integrity and two stability | Poison |
| Metal | One training seal | — |
| Lightning | Bank twelve Qi | Chaos |
| Ice | Cleanse four impurity | Soul |

Recovery stops at the existing stat caps. Existing roots are never replaced or
rerolled. A rare practice encounter grants the established personal boosts:
2× for 120 minutes, 3× for 60 minutes, or 5× for 30 minutes. An already active
personal boon is preserved instead of overwritten or stacked against itself.
Cooldowns and the random draw are controlled by the server; button clicks and
replayed interaction IDs cannot reroll or double-award rewards.

Spirit beasts begin their independent 18 Qi/hour cultivation at adoption, rather
than at the first status check. Old inactive beasts activate on first settlement
without invented historical backpay. Beast stages, bank, evolution and bond are
preserved; advancement/evolution remain manual. Existing NPC household growth
continues unchanged. Practice rewards and companion growth persist in the same
transaction as the owner's interaction response.

Sect renown is earned through member actions and progresses through Novice,
Rising (30), Established (90), Renowned (180) and Heavenly (360). It does not mint
hall treasury, replace contributed prosperity or change members' existing ranks.
Commissions and trials use a consistent inventory-before-sect lock order and
recheck duplicate interactions after acquiring their locks.

## Interface and accessibility

The new `/qi` card uses an original procedural constellation, a dantian ring,
elemental colors and jade/gold panels. The text embed retains exact numbers when
artwork abbreviates very large values. Rendering failure still delivers text and
controls, and all responses remain private. Controls belong to the dashboard's
owner, expire after three minutes, and call the existing guild-authorized handlers.
The previous commands are retained; there are 95 unique slash commands.
Refresh currently opens another private dashboard instead of replacing the previous
one; existing controls expire normally.

The preview image is illustrative, generated entirely offline from sample data:
`artifacts/v023_qi_dashboard.png`. UI implementation follows the installed
discord.py View/button APIs, reviewed against the
[official interaction documentation](https://discordpy.readthedocs.io/en/stable/interactions/api.html#discord.ui.View).

## F2P progression and balance

All new Qi, mastery, beast rewards, doctrines and commissions are earned without
payment. There is no paid energy, paid root reroll, premium multiplier, loss on
absence or new seven-day expiration. Existing solo meditation remains equivalent
to the partnership Qi boon. The new daily/cooldown limits bound active rewards;
they never stop permanent passive accrual or discard earned banked Qi.

This candidate introduces no payment system. A future monetization release should
be limited to optional presentation: titles, profile frames, colors or cosmetic
beast appearances, with the same numerical progression for every player. Live
economy telemetry and playtesting are still required before changing reward rates.

## Storage compatibility and migration requirements

No DDL migration is required. None of the seven reviewed SCHEMA literals or the
27-table manifest has changed. New data is additive inside existing JSONB:

- `characters.state.cultivation_v023`: focus, per-root mastery, cooldowns, bounded
  activation windows and practice/discovery counters.
- `characters.state.eternal.subsecond_remainder`: optional precision remainder;
  existing scale-version-2 remainder values retain their exact meaning.
- `living_dao_players.data.beast_cultivation`: adoption cursor and practice totals;
  old stages, bank, bond and evolution are retained.
- `sect_hall_state.data`: doctrine, renown and per-member commission records;
  unknown JSON fields, treasury spend, hall levels and trials survive updates.

Initialization is lazy under existing row locks, with no destructive reset, eager
backfill, schema recreation or progression conversion. No migration, bot startup,
command sync or database operation has been executed during preparation. Normal
bot startup still contains the older setup/backfill hooks, so starting it remains
a separately reviewed external operation even though v0.23 needs no new DDL.

## Database reliability: focused closure

The second failed suite's original exception and SQLSTATE are unavailable. Its
separately authorized recovery inspection observed an empty private schema with
unchanged evidence and acknowledged closure. This does not establish the old
rollback or recover the lost query failure. Historical rollback verification
remains UNKNOWN. No inventory SQL or isolation policy change was justified.

Future INVENTORY_QUERY failures now retain a fixed exception-family category,
allowlisted SQLSTATE class/code and SQLSTATE provenance. They distinguish reliable
server rejections/cancellation/timeouts/connections, explicit driver decoding,
driver protocol/interface/internal errors, local timeouts/connections and Python
errors. Local asyncpg DataError is not automatically labeled a server rejection.
No messages, SQL, arguments, query parameters, credentials or records are recorded.
Old journal diagnostic shapes remain readable without rewriting any evidence.

See `POSTGRES_INVENTORY_DIAGNOSTICS_REVIEW.md` for the finite investigation and
catalog-only probe limits. A separately approved READ ONLY parse/describe or
non-ANALYZE plan probe could test catalog compatibility without creating tables.
An empty-schema fetch cannot exercise the missing table-dependent inventory
branches. No probe or integration rerun has been executed or authorized here.

The original 21-file bundle and three successful recovery-inspection records stay
unchanged. Source changes intentionally invalidate the consumed bundle's current
source check. Its seal, receipts, attempt and journal must never be reset/resealed.
Future live integration needs a NEW reservation/batch and separate write approval.
The new diagnostic module and gameplay dependencies are included in future seals.

## Validation and next release

Completed offline validation: **844 Python tests passed**, plus **60 binding
fixtures, 28 static consistency checks and 12 native-argument fixtures**.
All Python source compilation, PowerShell parsing, documented command parsing
and whitespace checks passed. The guarded suite recorded zero violations,
database connections, Docker invocations or database-backed suite executions.
SHA-256 comparison confirmed all 21 original fresh-bundle files and three
successful recovery-inspection records unchanged.

Run all offline tests and static checks from the bot directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\validate_offline.ps1
```

The Python guard denies database connections, external network/process launches,
bot/config startup, environment files and all historical evidence directories.
Tests use pure rules, actual asyncpg exception/Record construction and synthetic
transaction/interaction doubles. They cannot certify PostgreSQL locking, live
Discord rendering or transaction durability.

Recommended next version: **v0.23.0-rc1 — The Shattered Heavens**. Review the source
and offline preview first. Then separately authorize a catalog-only PostgreSQL
compatibility probe; any later integration run must use a new schema reservation.
Separately authorize private-guild command sync and a bot smoke test before
promoting to v0.23.0. Remaining blockers are the unrecovered historical inventory
failure and the absence of an authorized live PostgreSQL/Discord smoke test.
They do not block continued offline gameplay development.

## Changed files in this update

Gameplay and interface:

- `shattered_heavens_v023.py`, `game.py`, `eternal.py`, `heavens.py`: cultivation
  focus, mastery, elemental practice, encounters and precise passive settlement.
- `sect_activities_v023.py`, `sect_halls.py`: doctrine, commissions, renown and
  consistent inventory/sect locking.
- `beast_v022.py`, `living_dao.py`: companion adoption and fractional-time growth.
- `storage.py`, `bot.py`, `qi_interface_v023.py`: protected command dispatch,
  atomic owner/beast practice rewards and the new dashboard.
- `test_shattered_heavens_v023.py`, `test_sect_companion_v023.py`,
  `test_qi_interface_v023.py`, `test_persistence_regressions.py`: rule, transaction,
  replay, rollback, interface and compatibility regressions.

Database diagnostics and offline validation:

- `inventory_fetch_diagnostics.py`, `integration_diagnostics.py`,
  `integration_support.py`: safe fetch-family/SQLSTATE diagnostics and compatible
  journal decoding.
- `fresh_integration.py`: seal the new diagnostic and gameplay dependencies in
  future batches; historical seals remain unchanged.
- `test_inventory_fetch_diagnostics.py`, `test_inventory_query_investigation.py`:
  exception classification and setup/cleanup regressions.
- `validate_offline.py`, `validate_offline.ps1`, `.github/workflows/tests.yml`:
  guarded offline validation, static checks and CI entry point.
- `.gitignore`: exclude generated preview/update artifacts.
- `POSTGRES_INVENTORY_DIAGNOSTICS_REVIEW.md`, this release guide, and
  `artifacts/v023_qi_dashboard.png`: investigation, release plan and sample UI.

Other existing changes on this feature branch predate this gameplay update and
are retained. The update archive contains full versions of the files listed in
its hash manifest; it excludes all environment files and historical evidence.

No commit, push, merge, deployment, live bot startup, Docker command or SQL
execution was performed for this candidate.
