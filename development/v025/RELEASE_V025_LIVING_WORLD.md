# v0.25 candidate — The Living World

This directory is a source-only development candidate, separated from the root
v0.24.0-rc2 runtime. It has not been launched, deployed or validated against a
live database or Discord guild. The running private bot remains untouched.
v0.24 release readiness and its outstanding live checklist remain separate.

## Playable content implemented

**Echoes of the Fallen Heaven** is an authored three-chapter solo arc integrated
with existing exploration and `/guide` buttons. No NPC conversation uses an AI
API, and no top-level slash command is added.

1. **The Broken Jade — Mistwood:** recover a Heavenly Palace seal. Return it to
   Jian Wuchen, trace its hidden script with Yue Lian, or share credit with
   Zhao Tianming. Your choice changes trust and later authored dialogue.
2. **Ashes in the Archive — Stonepeak:** compare the former guardian's account,
   the Crimson Moon records and the rival's offer to help. Protect the
   apprentices' names, investigate Yue's evidence or train beside Zhao.
3. **Echoes of the Fallen Heaven — Thunder Rift:** restore a guardian beacon,
   open the archive to everyone or establish a shared training ground. Future
   exploration describes the selected outcome. Every resolution preserves
   access to all three teachers and future essential story content.

Jian is a former Heavenly Palace guardian who teaches restraint and sword
discipline. Yue seeks lost knowledge and evidence concerning her sect's past.
Zhao develops from a proud rival into a possible training ally. The three NPCs
have separate trust scores, authored callbacks to saved choices and structured
memories. Trust is capped at 12; each NPC retains at most six memories.

Existing companions react through their personalities: a curious Jade Fox, a
watchful proud Storm Hawk or a patient Stone Tortoise. Sect Valor, Wisdom and
Harmony doctrines provide different story reactions. Neither a companion nor a
sect is required, and these responses grant no extra economic reward.

## Exploration, rewards and replay rules

The current chapter requires a persisted expedition to its region. Chapter
choices have no expiration. Resolving a chapter consumes that region proof;
another region expedition is required for the next chapter.

- Each chapter awards **3 earned marks once**, for **9 lifetime story marks**.
  The completed arc has no repeat payout.
- Existing expedition rewards, five-minute cooldown and v0.24 featured-region
  and rare-material daily caps remain intact.
- An additional rare inscription is cosmetic lore: a shared server-generated
  expedition roll below 50/1000 can record it once per UTC day. It grants no
  marks, items or Qi. The cosmetic discovery counter is capped at 30.
- The existing per-player row lock and `living_dao_actions` receipts serialize
  exploration, story choices and training. Duplicate interaction deliveries
  return the stored reply. A fresh interaction cannot replay a resolved chapter
  or change an earlier choice. Stale chapter controls fail without rewards.
- State, inventory rewards and receipts use the same existing transaction. An
  exception rolls back the complete transition; no story entitlement is reset.

## Personal Dao forms and teachers

Completing the Stonepeak chapter unlocks one personal form based on its choice:
Patient Edge, Seeking Thread or Shared Horizon. The awakened elemental root
names the form; rare roots use the existing common-root correspondence.

The first **primary Elemental Art** per battle activates the form once:

| Form | Small battle effect |
| --- | --- |
| Patient Edge | Restore up to 1–3 HP, capped at maximum HP |
| Seeking Thread | Restore up to 1–3 Combat Qi, capped at maximum Combat Qi |
| Shared Horizon | Add 1–3 damage |

Free cultivation practices refine the form: rank 1 initially, rank 2 at four
new candidate practices and rank 3 at eight. The additive preparation counter
stops at nine. Existing cultivation practice keeps its six-hour cooldown.
Practicing before meeting the teachers is retained as preparation. Lessons do
not grant practices or mastery and do not change passive Qi rates, manual
breakthroughs, equipment, companion arts or existing combat modes.

After the archive chapter, one **free teacher lesson per UTC day**, shared among
the three teachers, can retune the form. Jian teaches a guardian sword lesson;
Yue offers an investigation comparing both histories; Zhao offers a friendly
three-exchange duel. All teachers are accessible regardless of the archive
choice or trust score, allowing a later form change without a purchase.

Zhao telegraphs Rush → Feint → Guard. Guard → Strike → Feint are clean counters.
Other authored tactical sequences remain safe learning outcomes. The duel
never changes combat HP, equipment, marks, cultivation Qi or companion progress.
Lessons give one bounded trust, or two for a clean Zhao duel, and one memory.
The shared UTC limit prevents teacher-hopping for repeated same-day trust.

Teachers are reached from the Echoes story panel, not a new command. After the
archive chapter the panel shows Jian, Yue and Zhao buttons and a Teachers field
with the current form; once today's lesson is used the buttons are disabled and
the field shows when the next UTC day opens. Zhao's button opens an owner-only
duel panel with one dropdown per exchange and a Begin duel button. A missing or
invalid answer is named and spends nothing.

## Optional security observer (disabled)

`security_observer_v025.py` can forward story-handler denials and unexpected
errors to an `observability.EventEmitter`. Nothing calls `enable()`, so it does
nothing in this candidate. When enabled it passes only allowlisted kinds,
operations, outcomes and reasons plus raw IDs for the emitter to pseudonymize;
it never passes error text and never raises into a game handler.

## Database and save compatibility

**No SQL migration is needed.** No `SCHEMA` declaration changed. This candidate
uses existing player JSON, row locks, inventory records and action receipts:

- `living_dao_players.data.living_world_v025`: chapter, region proof, saved
  choices, jade echoes, NPC trust/memories, cosmetic lore and teacher cooldown.
- `characters.state.dao_practice_v025`: capped preparation count, root and the
  latest practice timestamp. Existing cultivation fields remain intact.
- New combat sessions snapshot `dao_technique` and its once-used flag. Active
  legacy sessions without these keys continue without an added effect.

Unknown JSON fields are retained. Story rules do not rewrite Beast Qi, bond
levels, evolution, companion trust, inventory, sect membership, sect treasury,
permanent owner Qi or historic progression. Daily lessons change only their own
story fields; story rewards add earned marks rather than replacing balances.

Do not point this development copy at the current v0.24 live character database.
A candidate launch needs a separately reviewed launch procedure, explicit
authorization and a dedicated disposable candidate database prepared through
an authorized backup/restore process. No such operation was performed here.

## Offline evidence and remaining live work

The guarded offline suite in this directory runs **1176 tests, all passing**,
with zero guard violations, PostgreSQL connections or Docker invocations
(`python -B -E -s validate_offline.py`). Story coverage:

- `test_living_world_v025.py`: all 27 story paths, region proofs, stale and
  duplicate deliveries, rollback/retry, daily limits, combat form bounds.
- `test_living_world_v025_persistence.py`: exact trust deltas and memories per
  choice, JSON round-trips, quest interaction, technique per choice and root,
  rank 1/2/3 progression and corrupted-save tolerance.
- `test_living_world_ui_v025.py`: story and duel panels, owner-only checks,
  handler success/error paths, observer wiring and the 99-command count.
- `test_security_observer_v025.py`: disabled-by-default, allowlists, no error
  text and an end-to-end pseudonymization check.

Corrupted saves no longer break the story panel or exploration. Invalid saved
choices, forms or sect doctrines are ignored. An invalid chapter number falls back
to the number of saved choices, so it cannot skip unpaid chapters. If both are
corrupted the player restarts at chapter 0 and could re-earn at most 9 marks.

Connection doubles test Python transaction boundaries and restoration of
synthetic state. They do not prove PostgreSQL locking, SQL execution, live
Discord delivery, latency, command synchronization or process restart behavior.

### Candidate private-guild checklist — requires separate authorization

Before any live step, in this order:

- Finish the v0.24 private regression in `PRIVATE_PLAYTEST_PROGRAM.md` first.
  v0.25 builds on v0.24 and must not share its database or running instance.
- From the bot directory, run `python -B verify_release_boundary.py` (expect
  126 unchanged files) and `python -B -E -s validate_offline.py` (1082 tests).
- From this directory, run `python -B -E -s validate_offline.py` (1176 tests).
- Get explicit approval for the sandbox database, the backup/restore, stopping
  the old bot and the Discord login, as `PRIVATE_PLAYTEST_PROGRAM.md` describes.

1. Prepare and confirm an isolated candidate database and test application/guild;
   retain the current v0.24 database and bot. Check that command labels identify
   the intended application when multiple test bots share a guild.
2. Open `/qi`, `/inventory` and `/companion` on an authorized copied test save;
   record unchanged root, Qi, equipment, marks, bond, Beast Qi and evolution.
3. Open `/guide` and the Echoes panel. Confirm Mistwood is the first destination.
   Explore it once, select a chapter choice, refresh and confirm the saved choice,
   trust and +3 mark change. Repeated/stale controls must not grant more marks.
4. Observe the normal expedition cooldown. Complete Stonepeak, check the
   remembered dialogue, companion reaction, chosen form and second +3 reward.
5. On the story panel, check the three teacher buttons and the Teachers field.
   Take one lesson; all three buttons must then disable with the next UTC day
   shown. Next day, open Zhao's duel: submitting with an exchange unchosen must
   be refused without using the lesson; then verify three visible exchanges, the
   safe outcome and that the form changed. Check desktop and mobile layout.
6. Finish Thunder Rift, confirm one of three resolutions and total story payout
   of exactly 9 marks. Revisit exploration to see the lasting resolution.
7. Hunt in Manual, Auto and Assisted modes. Use primary Elemental Art twice:
   form effect occurs once, never above 3. Confirm Fox healing, Second Wind,
   equipment, defeat/recovery and ordinary rewards still behave as before.
8. Run a free cultivation practice; confirm the normal cooldown, elemental
   mastery and permanent Qi remain, with bounded form preparation added.
9. Refresh/reconnect through an authorized test restart and confirm all story
   choices, memories, form, cooldowns and old player progression persist.
10. Have a second tester press your story or duel buttons: they must be told to
    open their own story, and nothing in your save may change.

Record command output and balances only. Do not force errors, replay gateway
events or edit saves on the sandbox to reproduce offline tests.

No claim of v0.25 live readiness or v0.24 Stable is made by this document.
