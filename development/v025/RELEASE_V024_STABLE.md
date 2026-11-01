# v0.24 Stable — stabilization candidate

Target: v0.24.0. Recommend **v0.24.0-rc2** for the next private regression session,
then Stable after the remaining live checks pass. The running bot and its saves
have not been touched. This pass implements no v0.25 gameplay.

## Demonstrated defects fixed

| Reproduction | Fix |
| --- | --- |
| Deliver the same crafting interaction twice with enough materials: two items were crafted and charged. | Production crafting records its interaction ID in the existing processed_actions table, inside the inventory transaction and lock. Same-ID retries return the original receipt. |
| Equip a blade, equip a different weapon, then replay the first interaction: the blade replaced the later weapon. | Equipment changes use the same receipt policy. A replay cannot undo a later choice. |
| Dragon battle, turn 1, 1 HP, zero Combat Qi: automation selected Second Wind then replaced it with Focus Qi. | Auto and Assisted resource planning preserve emergency Second Wind. Enemy attacks and the once-per-fight limit still apply. |
| Rare roots contradicted their advertised common counterparts in combat. | Primary/secondary arts and automated selection share the existing canonical mapping. Light uses Fire, Blood Water, Void Earth, Time Wind; Chaos receives Lightning's thunder-technique bonus. Root identity and saved mastery are retained. |

These were reproduced offline before the fixes. Receipt-failure tests also
verify rollback of crafting costs, combat loot/first clears, bond activity
rewards, world entitlements and sect completion/claims. New separate interactions
are still legitimate new actions: issuing /craft twice intentionally can craft
two items. Historical unreceipted crafting cannot be reconstructed or refunded
automatically; this update makes no retroactive balance changes.

## Player experience

- /qi defaults to six compact fields: Qi/percentage, rate/ETA and the correct
  manual advancement command, reserve, practice readiness, companion and today's
  activities. Details/Compact switches the same panel. Full boost and mastery
  information stays available; active timed boosts are flagged. No image rendering.
- /guide starts with an adaptive next step. A six-topic menu opens short pages:
  cultivation, hunts/equipment, companions, world and sect life. Topic changes
  use the existing message and need no further database calls.
- /start points toward the first hunt, camp recovery, recipes, crafting,
  equipment and adoption. The established awakening/path/focus next step remains
  adaptive. /qi and /guide display the responding bot's name to help distinguish
  two applications installed in the same test guild.
- Battle buttons show Combat Qi costs. Techniques explains effects, counters,
  companion cooldown and all three modes, even before a first hunt. Heavy intent
  no longer implies that breaking a dragon ward reduces its incoming attack.
- Battle summaries show dealt/taken/healed HP and the actual committed payout
  including materials. Camp HP and recovery readiness are separate from final
  battle HP, so a healed character does not appear to have lost that healing.
- /companion shows the last bond memory and activity readiness. Sect project
  boards identify missing activity types and explain when another player is
  needed. Reward values, enemy stats, bond/Qi progression and permanent passive
  cultivation remain intact apart from the documented combat consistency fixes.

## Economy and save compatibility

No SQL migration, table/column addition, role/grant change, new database or reset
is required. Existing additive JSON fields and unknown fields remain preserved.
Inventory receipts reuse the core processed_actions table already present in
the successful private gameplay database. Older Python calls to inventory.change
remain valid; both Discord handlers now always supply their interaction ID.

World rewards are bounded per player per UTC day: one featured-region material
and one rare-encounter material, in addition to normal expedition rewards.
Five-minute exploration cooldowns still apply. Replayed committed interactions
do not draw randomness again, including replay of a nonrare result. A rolled-back
transaction leaves neither rewards nor consumed daily entitlement.

Sect commissions retain their six-hour personal cooldown, three-per-UTC-day
limit, six marks, five renown and matching-doctrine bonus. They do not fund the
hall treasury. Shared projects require two distinct Discord user IDs and all
three activities; completion and each contributor's two-mark voucher pay once.
Unclaimed vouchers survive daily rollover.

Distinct accounts are enforceable; distinct human owners of those accounts
cannot be proved by the bot. No new account-age, payment or identity gate has
been added. A private guild should invite genuine testers; the bot does not
claim to detect every alternate account.

Permanent player and Beast Qi, reserve overflow, manual breakthroughs/evolution,
existing bond/trust/memories, equipment and consumed action receipts are retained.
No paid Qi, stats, loot, cooldown skips, mandatory purchases or streak penalties.

## Evidence and remaining live checks

| Area | Evidence now |
| --- | --- |
| Cultivation and passive Qi; Fire/Spirit/Insight; Wolf victory; challenging Serpent; Assisted pause; Fox healing; saved victory rewards | Confirmed by the supplied v0.24 live observations. New behavior is also covered offline. |
| Fox adoption, permanent Beast Qi, listen activity (+4 trust/+6 Beast Qi/memory), attunement | Confirmed live by the user; evolution preservation, receipt retries and failure rollback verified offline. |
| Sect creation, halls/doctrine/treasury, commission reward, unfinished project | Confirmed live by the user. |
| Starter crafting/equipping, free camp recovery | Code and offline regressions verified. Independent live success remains unconfirmed. |
| Featured/rare daily limits and retry safety | Verified with deterministic rules and real handlers over in-memory transactions; needs private live inventory observations. |
| Shared completion and vouchers | Two synthetic user IDs pass handler tests; genuine second-player live completion remains unverified. |
| Compact Details/guide menus, bot identity label, revised technique/recovery UI | Tested through real disconnected callbacks; needs Discord/mobile review. |

Offline fakes do not establish PostgreSQL locking or Discord delivery behavior.
No database-backed integration suite was run, and no evidence records were read,
reset or rewritten. Command registration remains **99 unique slash commands**;
no new top-level command, global sync, intent or permission requirement was added.

Final offline validation passed: **991 Python tests** (25 added in this pass),
**100 PowerShell checks** (60 binding fixtures, 28 consistency checks, 12 native
argument fixtures), Python compilation, PowerShell/documented-command syntax
and whitespace checks. Zero offline guard violations, PostgreSQL connections,
Docker invocations or database-backed suite runs.

Run the complete offline checks from the bot directory:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\validate_offline.ps1
```

## Short private-guild regression checklist

This document authorizes no restart or live operation. Use the existing
[gated private launch procedure](RELEASE_V024_LIVING_DAO.md#private-playtest--separately-authorized-next-operation)
only after separate approval. Retain the existing isolated database, application,
guild, Fire character and Fox. Select the intended application in Discord's
command picker and check its name in the new panels; navigation stays with that bot.

1. Compare /qi, /inventory and /companion with the prior session. Keep root,
   path, focus, Qi/reserve, marks, equipment, bond, trust and evolution. Check
   /start retains the character; do not reselect permanent identity choices.
2. Test /qi Refresh and Details/Compact, all guide topics and owner-only buttons
   on desktop/mobile. Confirm 99 commands synced to the test guild, including
   the existing /guide, /recover, /beast_bond and /sect_project.
3. Try an Assisted Wolf hunt: intent, cost labels, technique help, heavy pause,
   human choice and continuation. Try Manual and Auto on separate hunts.
   Verify payout against /inventory; old battle buttons must not affect a new hunt.
4. Review /recipes, craft/equip a starter item using earned resources, then
   /inventory. Check Retreat gives no reward, /recover heals camp HP without
   changing Qi/marks, its two-minute cooldown and natural recovery.
5. Bond when ready: check trust/memory/Beast Qi/attunement and early-repeat
   rejection. Preserve prior progression; manual advancement/evolution remains.
6. Explore the featured region, record materials, and repeat after the existing
   cooldown. Its chapter bonus must not repeat that UTC day. Rare rewards are
   random; do not expect one in a short session. Practice when ready; permanent
   passive Qi and manual breakthroughs must continue as before.
7. With a genuine second player, coordinate Patrol/Archives/Gardens. Two people
   need a six-hour wait before one person's second commission (or use a genuine
   third contributor). Check +15 shared project renown, two marks per contributor
   claimed once, no premature payout, unchanged treasury and cooldown rejection.

Do not force database errors, duplicate gateway deliveries or reset timers on the
live save to reproduce offline failure tests. Record normal command output and
balances instead. Passing this checklist is the remaining gate for v0.24 Stable.

## Changed files in this stabilization pass

- Combat/recovery: combat.py, combat_store.py, combat_journey_v024.py.
- Craft/equip receipts: inventory.py and the bot.py callers.
- UI/onboarding: bot.py, qi_interface_v023.py, onboarding_v024.py, storage.py.
- Companion/project presentation: companion_bonds_v024.py, sect_projects_v024.py.
- Regressions: test_living_dao_stable.py (new), test_living_dao_ui_v024.py.
- Offline checks and planning: validate_offline.ps1, this document and
  [ROADMAP_V025.md](ROADMAP_V025.md). Earlier uncommitted work is retained.
