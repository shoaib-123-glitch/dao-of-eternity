# v0.24.0-rc1 — The Living Dao

Offline release candidate for the existing private Discord playtest. No bot was
started or stopped, no database was contacted, and no player or PostgreSQL recovery
record was modified. Keep the successful v0.23 test character and database.

## A connected first adventure

1. /start activates permanent passive Qi and offers Guide and Qi shortcuts.
   /guide adapts its next step to the character: /awaken, /choose_path and
   /cultivation_focus. Awakening and paths retain their existing commitment rules.
2. /qi displays one embed with elemental color, exact Qi, percentage, remaining Qi,
   reserve, rate, practice readiness and companion status. No image duplication or
   character progress bar. Refresh edits that panel; buttons open Guide, Assisted
   Hunt, Companion, Practice and Journey. Existing commands remain available.
3. Hunt Spirit Wolf first. Assisted now pauses before every enemy's heavy attack
   and resumes after a player's move. Manual and Auto use the same combat engine.
   Action feedback shows actual HP, enemy HP and Combat Qi changes.
4. Second Wind restores up to 18 HP and 4 Combat Qi once per battle and braces
   against the responding enemy attack. Retreat gives no marks or loot. Neither
   defeat nor retreat removes cultivation, inventory or companions.
5. /recover restores up to 60% battle HP for free, every two minutes between
   fights. Natural camp recovery is 1 HP per five seconds. Injury and the recovery
   cooldown persist between hunts. Recovery never earns currency, loot or Qi.
6. The Wolf's base reward stays 8 marks; each of Wolf, Guardian and Serpent grants
   an additional 4 marks on its first v0.24 clear only. Two Wolf wins earn 20:
   craft a Mistwood Blade for 4 marks + one Fang, equip it, and save 16 for a Fox.
   Traveler Wraps cost 6 marks + two Fangs and add 8 HP/1 ward. Existing equipment
   and recipes are retained. Qi Refining rank adds at most 32 HP/4 attack;
   higher realms retain that bounded training bonus and existing realm benefits.
   Enemy descriptions recommend preparation without locking old hunts.

## Companions, cultivation and the world

- /beast_bond offers Listen, Roam and Meditate, free every six hours. Foxes are
  curious and enjoy listening, Hawks proud and enjoy roaming, Tortoises patient
  and enjoy meditation. Favorite activities award more trust. The first activity
  guarantees a personality story; later activities have a 15% story chance.
- Each activity grants six Beast Qi and 2–4 trust. Twenty trust earns a bond
  level, capped at five. Existing mark-funded /bond remains optional. No feeding,
  absence penalty, streak or paid requirement was added.
- Bonding grants twelve hours of modest battle attunement: Fox art heals three
  extra HP, Hawk adds two extra opening Combat Qi, Tortoise gains one ward.
  Hawk's existing advertised base opening bonus now supplies two Combat Qi.
  Benefits are snapshotted at the start of a hunt; an activity during a fight
  does not change that battle. Beast growth remains permanent at 18 Qi/hour,
  with existing bank, evolution, stages and manual advancement preserved.
- The first three practices while at Qi Refining stages 1–3 grant twelve
  additional Qi each: at most 36 lifetime, including reserve overflow. No new
  late-realm rate increase. Practice still has its six-hour cooldown; breakthroughs
  stay manual. Existing rare-boon probabilities, durations and multiplier cap
  are unchanged; new Dao memories give those discoveries a world story.
- /world_event adds a shared daily chapter and featured exploration region.
  The first matching expedition per UTC day earns one extra local material.
  Expeditions have an 8% rare story chance, capped at one extra rare material per
  player per UTC day. Base rewards and five-minute exploration cooldown remain.
  The historical Qi event schedule and offline accrual are unchanged.
- Sect commissions now contribute to a shared daily project: at least two
  distinct members and all three activities (Patrol, Archives, Gardens).
  Completion grants 15 shared renown and a two-mark voucher per contributor,
  once per UTC day. /sect_project shows progress and collects one's vouchers;
  unclaimed vouchers never expire. Existing commission rewards, cooldown,
  doctrine, treasury, halls and cooperative raids remain. Solo players retain
  personal commissions and trials without needing a project.

## Compatibility and migration

No new tables, columns, roles, grants, database or schema migration is required.
All new saved state is additive JSON in existing rows:

| Existing row | New data |
| --- | --- |
| characters.state | living_dao_v024 epiphany/memory counters |
| combat_sessions.battle | trail_v024 HP, recovery cursor, cooldown, first clears and outcomes; battle_id, mode and bounded battle snapshots |
| living_dao_players.data | companion_v024 trust, activities, memories and attunement cursor; world_v024 daily story counters |
| sect_hall_state.data | project_v024 daily participation, projects_completed_v024 and project_vouchers_v024 |

Old completed combat records begin the new recovery system fully rested. An
already active fight retains its saved HP/stats and may be resumed; it gains the
recovery record only when finished. Existing mastery carries across hunts.
First v0.24 clear rewards are not backdated and cannot repeat after defeat,
retreat, recovery, a new hunt, or replay of an interaction.

Reward writes keep their existing transactions, row locks and interaction
receipts. Companion bonding shares the existing Living Dao receipt table.
Project collection uses inventory-before-sect locking, matching commissions and
contributions. Old battle buttons reject a later hunt's identity. New modules
are included in future integration source seals; no old bundle was resealed or
consumed attempt reset.

## Offline validation

From this bot directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\validate_offline.ps1
```

The suite runs pure rules, fake-connection transactions/concurrency schedules,
owner-bound Discord callbacks and disconnected real command registration.
It also compiles Python source and checks PowerShell, documented commands and
whitespace. Offline guards reject database/network/startup/secret access.
Validation completed: **966 Python tests (59 new), 100 PowerShell checks**,
Python compilation, PowerShell/documented-command parsing and whitespace checks
all passed. Zero offline guard violations, Docker commands, PostgreSQL
connections or database-backed suite runs.

## Files changed for this update

Earlier uncommitted work was retained. This update changes only the following
source, regression and release-preparation files:

| Area | Files |
| --- | --- |
| Combat and starter equipment | combat.py, combat_store.py, inventory.py, combat_journey_v024.py (new) |
| Companions | beast_v022.py, companion_bonds_v024.py (new), living_dao.py |
| Cultivation and world stories | shattered_heavens_v023.py, living_world_v024.py (new) |
| Cooperative sect projects | sect_halls.py, sect_projects_v024.py (new) |
| Onboarding and Discord interface | bot.py, storage.py, qi_interface_v023.py, onboarding_v024.py (new) |
| New regressions | test_living_dao_v024.py, test_living_dao_ui_v024.py |
| Updated reward/UI expectations | test_persistence_regressions.py, test_postgres_concurrency_schedules.py, test_private_playtest_diagnostics.py, test_qi_interface_v023.py |
| Offline validation and future suite compatibility | validate_offline.ps1, fresh_integration.py (new source dependencies only), postgres_cases.py (first-clear reward expectation) |
| Release and playtest procedure | RELEASE_V024_LIVING_DAO.md (this file) |

## Private playtest — separately authorized next operation

Offline restart review: the existing launcher changes into its own directory,
starts private_playtest.py in a fresh Python process, and imports the current
bot.py and v0.24 gameplay modules from this folder. No launcher or configuration
change is needed. Its older v023 labels do not pin an old release or require
an empty replacement database. Use the same working Python environment as v0.23.

Registration checks confirm 99 unique slash commands, including the four new
commands: /guide, /recover, /beast_bond and /sect_project. Startup copies the local
definitions and syncs only the configured test guild; it does not globally sync
commands. Guild sync replaces this application's command set in that guild.
Do not keep v0.23 running or restart it alongside v0.24, as it can sync its older
command list back. Discord-side permissions, availability and successful sync
remain live checks; do not repeatedly restart if sync fails. Use the launcher's
sanitized failure stage to identify the problem.

Shortest restart: after separate operational authorization, stop the old test
process cleanly with Ctrl+C in its own console and wait for it to exit; take
the usual private-test backup, then run the existing command below from this
directory. Confirm the pinned identities and wait for the 99-command sync and
successful startup before playing: open a fresh /qi panel titled "The Living Dao"
and check that /guide, /recover, /beast_bond and /sect_project are available.
The launcher's default logging may hide the INFO-level sync-count line; terminal
silence alone is not a failed launch. Old message buttons are not restored across
restart; open fresh panels using /qi or /battle_status.

Do not run a second bot process alongside the currently running v0.23 process.
The operator must separately authorize and perform the switch to this candidate,
including stopping the existing test process, taking their normal private-test
backup, and restarting through the existing gated launcher. This preparation
has performed none of those operations.

Reuse the successful isolated target: 127.0.0.1:5433,
database dao_playtest_v023_rc1, login dao_playtest_rc1, recorded cluster identifier
7699864005918920747, and the same private application/guild. The v023 database
prefix is an existing launcher safety requirement; do not rename the database
or create another one for the release number. Independently confirm those
identities remain current. Never use the Windows player-data database or either
failed integration schema.

**Only after authorization for test database writes and Discord login/sync:**

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\launch_private_playtest.ps1 -AuthorizeDatabaseWrites -AuthorizeDiscordLogin
```

Enter the existing non-secret identities when prompted and test secrets through
the launcher's secure prompts. No environment files are needed. Its existing
identity/permission checks still run before setup. Startup retains its existing
CREATE IF NOT EXISTS/setup behavior, so this command is a live writing operation
even though v0.24 itself needs no DDL migration.

Private test checklist:

- Open the existing character's /qi and /inventory; verify Fire root, Spirit
  path, Insight focus, Fox, Qi reserve, bond, evolution and loot are retained.
  /start must keep that same character; /guide should show the current next step.
- Confirm one /qi embed, no attachment or block bar, one-panel refresh, functional
  shortcuts, private replies and rejection of another user's button press.
- Run an Assisted Wolf hunt, check the heavy warning at turn two, choose Guard
  and check automatic continuation. Try Second Wind while injured. Use Manual
  for a separate hunt and Auto for another; do not expect harder enemies to be
  guaranteed victories. An old hunt's buttons must reject the new hunt.
- Check the once-only 12-mark first Wolf payout, later 8-mark payout and one Fang
  per victory. Craft/equip the starter blade if desired; preserve the old Fox.
  Retreat or lose a harder fight: no rewards or loss of cultivation/inventory.
  Check /recover, its cooldown, and natural healing before the next hunt.
- /companion, /beast_bond listen, /beast_cultivation: confirm Fox personality,
  first memory, trust, six Beast Qi, twelve-hour attunement, preserved bank,
  and continued 18/hour growth. A repeat bond before six hours should explain
  readiness and grant nothing. Check manual /beast_advance when full.
- /cultivation_practice should keep Fire mastery/Ember Refinement and add an early
  epiphany only if still in the eligible stages. Leave Qi earning offline and
  verify a manual breakthrough still controls advancement.
- Check /world_event, explore the featured region, inspect materials; subsequent
  expeditions that day must not repeat the chapter bonus. Rare scenes are random
  and are not guaranteed during a short session.
- With a second consenting tester in the same sect, use three commission types
  across both members (the third can be done by a third member immediately or
  by either of the first two after the existing six-hour cooldown). Check
  shared completion, +15 renown and two marks per contributor claimed once.

## Limits and next release

Recommended version: **v0.24.0-rc1**, then v0.24.0 after the private checklist.
No live validation of the new behavior has been performed. In-memory tests do
not prove PostgreSQL behavior, Discord delivery or balance across real players.
The bot has 99 slash commands (Discord limit 100); the next release should group
related commands before expanding the command list. Existing saved action
receipts keep their original text on replay.

The battle engine remains deterministic with the existing fourteen-turn limit.
The new recovery and first-clear path need live balance feedback, especially
Serpent/Dragon readiness. Bond stories are text events and attunement is temporary;
losing attunement never removes permanent bond or Beast Qi. Projects need two
members, so validate them with consenting testers rather than enabling developer
commands or changing player progress. No extra database investigation or
provisioning is required to begin this private gameplay review.
