# v0.25 Story Walkthrough — Echoes of the Fallen Heaven

A checklist for playing the whole v0.25 story arc in Discord with your **disposable
Lightning test character** in the **v0.25 sandbox** (`dao_playtest_v023_v025_dev`).
Never do this on the real v0.24 save or while the v0.24 bot uses the same Discord app.

**How to read each step:** **Do** = what to press · **See** = what should appear ·
**Expect** = reward or saved change · **Skip with** = Developer Console action
(menu label, with the internal key in brackets).

**Opening the Developer Console:** `/guide` → button **Developer console** → pick a
**Category** → pick an **Action** → keep **Target: yourself** → **Run** (actions with
numbers open a small form). Every console action marks the character as
developer-modified; that is expected on this test account.

**Two kinds of marks — keep them apart:**
- *Exploration marks* come from every expedition: Mistwood +3, Stonepeak +6,
  Thunder Rift +10.
- *Story marks* are **+3 per chapter choice, once**: 9 total for the arc.

To check a story payout, read `/inventory` marks **right before and right after
pressing a choice button**. Don't explore in between.

---

## 0. Before you start

- [ ] **Do:** `/qi`, `/inventory`, `/companion`. Write down root, combat marks, companion and sect.
  **See:** root **Lightning**. Companion and sect are optional, but needed for step 10.
  **Expect:** nothing changes; this is your baseline.
  Result/notes: ______________________________________________

- [ ] **Do:** `/world_event`. Note today's featured region.
  **See:** the shared chapter name and featured region for this UTC day.
  Result/notes: ______________________________________________

---

## 1. Start the Echoes arc

- [ ] **Do:** `/guide` → button **Echoes of the Fallen Heaven**.
  **See:** a private panel titled *The Living World · Echoes of the Fallen Heaven* showing:
  - **I · The Broken Jade**, with the line *Next: /explore region:mistwood. The usual 5-minute expedition cooldown applies.*
  - Jian Wuchen, Yue Lian and Zhao Tianming each at *Acquaintance 0/12*.
  - *Jade echoes: 0 / 3*.
  - A **Teachers** field saying to complete Stonepeak first.
  - Buttons **Refresh story** and **Explore mistwood**. No choice buttons yet.
  **Expect:** nothing is saved just by opening the panel.
  Result/notes: ______________________________________________

---

## 2. Chapter 1 — Mistwood (The Broken Jade)

- [ ] **Do:** press **Explore mistwood** on the panel, or `/explore region:Mistwood Vale`.
  **See:**
  - *Explored Mistwood Vale! Earned 3 combat marks and 1 material…*
  - **I · The Broken Jade** — the chapter text and *Open /guide and choose Echoes of the Fallen Heaven.*
  **Expect:** +3 exploration marks, +1 Wolf Fang and a saved region proof.
  **Skip with:** *Exploration ready* [`exploration_ready`] if the 5-minute cooldown blocks you. *Grant current region proof* [`story_region_proof`] grants the proof without an expedition and gives no exploration rewards.
  Result/notes: ______________________________________________

- [ ] **Do:** reopen the panel (`/guide` → **Echoes of the Fallen Heaven**). Note `/inventory` marks first.
  **See:** three choice buttons: **Return the guardian seal**, **Trace the hidden script**, **Share credit with Zhao**.
  Result/notes: ______________________________________________

- [ ] **Do:** press one choice.
  **See:**
  - An NPC line, then *Chapter complete · +3 saved marks · Jade echo 1/3*.
  - *Next region: stonepeak.*
  - Your companion's or sect's line, if you have one.
  **Expect:**
  - Exactly **+3 marks**.
  - Trust changes as in the table in step 8.
  - The panel moves to **II · Ashes in the Archive**, and a remembered line from your NPC appears in the chapter text.
  Result/notes: ______________________________________________

- [ ] **Do (no double payout):** if the old chapter-1 panel is still on screen, press a different choice on it.
  **See:** *That story choice is already resolved or belongs to an earlier chapter… no reward was repeated.*
  **Expect:** marks unchanged.
  Result/notes: ______________________________________________

---

## 3. Chapter 2 — Stonepeak (Ashes in the Archive) + personal Dao form

- [ ] **Do:** wait out the 5-minute cooldown, then **Explore stonepeak** on the panel or `/explore region:Stonepeak Sanctuary`.
  **See:** *Explored Stonepeak Sanctuary! Earned 6 combat marks…* and the **II · Ashes in the Archive** text.
  **Expect:** +6 exploration marks, +1 Stone Core and a region proof.
  **Skip with:** *Exploration ready* [`exploration_ready`], or *Grant current region proof* [`story_region_proof`].
  Result/notes: ______________________________________________

- [ ] **Do:** note your marks, reopen the panel and press one choice:
  - **Protect the apprentices' record** unlocks the **Patient Edge** form (heals HP).
  - **Investigate Yue's evidence** unlocks **Seeking Thread** (restores Combat Qi).
  - **Train beside Zhao** unlocks **Shared Horizon** (extra damage).
  **See:**
  - *Chapter complete · +3 saved marks · Jade echo 2/3*.
  - *Personal Dao form: <name>. Free /cultivation_practice refines it…*
  **Expect:**
  - Exactly **+3 marks** (6 story marks so far).
  - The panel now shows **III**, three teacher buttons, and a Teachers field naming your form.
  Result/notes: ______________________________________________

- [ ] **Do:** `/hunt` any enemy. Once you have 6 Combat Qi, press **Elemental Art**.
  **See:** *Lightning · <form>: +N … (once this battle)*. Rank 1 means N is at most 1, and Patient Edge shows +0 if you're at full HP.
  **Expect:** the effect fires on the **first** Elemental Art only. Pressing it again in the same battle shows no form line.
  **Skip with:**
  - *Set Combat Qi* [`set_battle_qi`] if you lack 6 Combat Qi.
  - *Set battle HP* [`set_battle_hp`] to drop HP so a heal is visible.
  Result/notes: ______________________________________________

---

## 4. Chapter 3 — Thunder Rift (Echoes of the Fallen Heaven)

- [ ] **Do:** **Explore thunder rift** on the panel, or `/explore region:Thunder Rift Expanse`.
  **See:** *Earned 10 combat marks…* and the **III · Echoes of the Fallen Heaven** text.
  **Expect:** +10 exploration marks, +1 Serpent Scale and a region proof.
  **Skip with:** *Exploration ready* [`exploration_ready`], or *Grant current region proof* [`story_region_proof`].
  Result/notes: ______________________________________________

- [ ] **Do:** note your marks and press one choice: **Restore the guardian beacon**, **Open the record to all** or **Found a shared training ground**.
  **See:**
  - *Chapter complete · +3 saved marks · Jade echo 3/3*.
  - *Arc complete. No repeat payouts…*
  **Expect:**
  - Exactly **+3 marks**, for **9 story marks in total**.
  - The panel says *Echoes · Arc complete* and has no choice buttons.
  Result/notes: ______________________________________________

- [ ] **Do:** explore Thunder Rift again after the cooldown.
  **See:** one lasting line, depending on your choice:

  | Resolution | Lasting line |
  |---|---|
  | Beacon | *A guardian lantern now guides the road.* |
  | Archive | *Travelers exchange copies of the recovered records.* |
  | Training ground | *A shared training ground welcomes rival schools.* |

  **Expect:** only the normal exploration reward, no story marks.
  Result/notes: ______________________________________________

- [ ] **Do (optional — the other two resolutions; do this only after step 9):**
  1. Console *Rewind story* [`story_rewind`] with chapter index **2**.
  2. Console *Resolve current chapter* [`story_resolve_chapter`] with option **1**, **2** or **3**.
  3. Explore Thunder Rift and read its line.
  4. Repeat steps 1–3 for the next option.
  **See:** each resolution's reply and lasting exploration line.
  **Expect:** console resolutions pay **no** marks.
  **Warning:** after a rewind, a *real* choice button would pay +3 again, because rewind removes the saved choice. Use only the console action here.
  Result/notes (beacon / archive / training ground): ______________________________________________

---

## 5. Teacher lessons (one per UTC day, shared by all three)

- [ ] **Do:** on the panel, press **Jian Wuchen · Sword lesson** or **Yue Lian · Archive investigation**.
  **See:**
  - The teacher's lesson text, then *Trust X/12 · Personal form: <teacher's form>*.
  - Jian adds an extra line if your chapter-1 choice was *Return the guardian seal*; Yue does if it was *Trace the hidden script*.
  **Expect:**
  - +1 trust with that teacher.
  - Your form changes to the teacher's form: Jian → Patient Edge, Yue → Seeking Thread, Zhao → Shared Horizon.
  - No marks, items or Qi.
  Result/notes: ______________________________________________

- [ ] **Do:** refresh the panel.
  **See:** all three teacher buttons **greyed out**, and the Teachers field shows *Next free lesson in …* (the next UTC day).
  **Expect:** pressing a teacher now changes nothing.
  **Skip with:** *Teacher lesson ready* [`teacher_lesson_ready`].
  Result/notes: ______________________________________________

- [ ] **Do (form rank):** run `/cultivation_practice` four times.
  **See:** each reply ends *Your personal Dao form grows through free practice (n/9…)*. Lightning also banks +12 Qi per practice.
  **Expect:** rank 1 at 0–3 practices, rank 2 at 4–7, rank 3 at 8–9. A **new** hunt then shows up to +2 (rank 2) or +3 (rank 3) on the first Elemental Art.
  **Skip with:**
  - *Cultivation practice ready* [`practice_ready`] for the 6-hour cooldown.
  - Or jump straight there with *Set form preparation* [`set_form_practice`] = 4 or 8.
  - Start a new battle afterwards, because a battle keeps the form it started with.
  Result/notes: ______________________________________________

---

## 6. Zhao duel (three telegraphed exchanges)

- [ ] **Do:** make sure a lesson is available, then press **Zhao Tianming · Friendly three-exchange duel**.
  **See:** a private duel panel: *Zhao: Three exchanges, no injuries. I telegraph Rush → Feint → Guard…*, with three dropdowns (*Exchange 1 · Zhao Rush → your answer*, and so on) and a **Begin duel** button.
  **Skip with:** *Teacher lesson ready* [`teacher_lesson_ready`] if today's lesson is used.
  Result/notes: ______________________________________________

- [ ] **Do (refusal check):** leave one dropdown empty and press **Begin duel**.
  **See:** *Choose a tactic for every exchange first (missing: N). Nothing was spent.*
  **Expect:** the lesson isn't used, and the teacher buttons stay active.
  Result/notes: ______________________________________________

- [ ] **Do:** choose **Guard**, **Strike**, **Feint** and press **Begin duel**.
  **See:** three lines ending *clean counter.*, then *Friendly duel: 3/3 clean counters; a shared victory.*
  **Expect:**
  - **+2** Zhao trust, because 2 or more clean counters count as a win.
  - Form becomes **Shared Horizon**.
  - Combat HP, equipment, marks and Qi are unchanged.
  Result/notes: ______________________________________________

- [ ] **Do (losing path):** console *Teacher lesson ready*, then duel again with wrong answers (for example Strike, Guard, Strike).
  **See:** *he holds the advantage; try <counter>* on each miss, then *a useful lesson, no defeat penalty.*
  **Expect:** only **+1** trust and no penalty.
  Result/notes: ______________________________________________

---

## 7. Exploration cooldown and daily caps

- [ ] **Do:** explore, then immediately explore again.
  **See:** *Your spiritual senses are recovering. Try again in Ns.*
  **Expect:** no reward on the refused attempt.
  **Skip with:** *Exploration ready* [`exploration_ready`].
  Result/notes: ______________________________________________

- [ ] **Do:** explore today's **featured region** from `/world_event` twice, with a cooldown skip in between.
  **See:** the first time adds *…+1 regional material (once per UTC day).* The second time doesn't.
  **Skip with:** *Featured and rare daily bonus ready* [`world_daily_ready`].
  Result/notes: ______________________________________________

- [ ] **Do:** watch for the two rare lines over several expeditions:
  - *Rare trail encounter … +1 regional material* (about 8% per expedition).
  - *Rare discovery: … (lore only; at most once per UTC day)* (about 5%).
  **Expect:**
  - Each happens at most once per UTC day.
  - The lore gives nothing but text, and its counter stops at 30.
  **Skip with:**
  - *Featured and rare daily bonus ready* [`world_daily_ready`] for the trail encounter.
  - *Rare story lore ready* [`story_lore_ready`] for the lore.
  Result/notes: ______________________________________________

---

## 8. NPC trust and memories

The trust each choice gives:

| Chapter choice | Jian | Yue | Zhao |
|---|---|---|---|
| I · Return the guardian seal | +3 | +1 | |
| I · Trace the hidden script | +1 | +3 | |
| I · Share credit with Zhao | +1 | | +3 |
| II · Protect the apprentices' record | +3 | | +1 |
| II · Investigate Yue's evidence | +1 | +3 | |
| II · Train beside Zhao | | +1 | +3 |
| III · Restore the guardian beacon | +3 | +1 | +1 |
| III · Open the record to all | +1 | +3 | +1 |
| III · Found a shared training ground | +2 | +2 | +3 |

Teacher lessons add +1, and a clean Zhao duel adds +2.

- [ ] **Do:** after each choice and lesson, press **Refresh story** and read the three NPC lines.
  **See:** *Acquaintance* below 3, *Growing trust* at 3–5, *Trusted ally* at 6 or more. Trust never goes above 12.
  Result/notes: ______________________________________________

- [ ] **Do:** look for memories in what you can see on screen. The saved memory list itself isn't displayed anywhere.
  **See:**
  - The remembered line in the chapter text, which comes from your latest chapter choice.
  - Jian's or Yue's extra lesson line in step 5.
  **Optional:** *Set NPC trust* [`story_set_trust`] and *Clear NPC memories* [`story_clear_memories`] let you test the edges. Clearing memories keeps trust.
  Result/notes: ______________________________________________

---

## 9. Story marks total = 9, and no double payout

- [ ] **Do:** add up the three before/after differences from steps 2, 3 and 4.
  **Expect:** exactly **9** story marks. Exploration marks are counted separately.
  Result/notes: ______________________________________________

- [ ] **Do:** after the arc is complete, reopen the panel and explore every region again.
  **See:** *Echoes · Arc complete* and no choice buttons. Expeditions give only the normal region reward.
  **Expect:** no further story marks.
  Result/notes: ______________________________________________

- [ ] **Do:** quickly double-click one choice button on a fresh chapter. This only works before completing a chapter, so do it during a re-test, or check it when you first play step 2.
  **Expect:** one +3 only. A second press shows the already-resolved message.
  Result/notes: ______________________________________________

---

## 10. Companion and sect reactions

Each reaction appears on story expeditions and chapter choices.

- [ ] **Do:** explore a story region or make a choice with a companion adopted.
  **See:** one of these:
  - *Your curious Jade Fox noses a hidden seam in the jade.*
  - *Your proud Storm Hawk keeps watch above the ruins.*
  - *Your patient Stone Tortoise steadies the fractured floor.*
  **Expect:** no extra reward.
  **Skip with:** *Companion activity ready* [`companion_activity_ready`] if you test companion activities alongside.
  Result/notes: ______________________________________________

- [ ] **Do:** the same while in a sect. Note the hall doctrine.
  **See:**
  - **Wisdom:** *Yue welcomes your sect's tradition of careful study.*
  - **Harmony:** *Jian asks your sect to safeguard the mountain paths.*
  - **Valor:** *Zhao recognizes your sect's discipline in the storm.*
  - **No doctrine:** *The three travelers ask your sect to remember the people behind the legend.*
  **Expect:** no extra reward, and no sect or treasury change.
  Result/notes: ______________________________________________

---

## Feel and balance

Answer as a player, not a tester.

- [ ] Was it clear how to start the arc and where the next step was (`/guide` → Echoes, then explore)? Notes: __________
- [ ] Was anything confusing — region names, proof vs exploration, story marks vs exploration marks, the form name? Notes: __________
- [ ] Too slow? (5-minute expeditions, one lesson per UTC day, 6-hour practice, 8 practices to reach rank 3.) Notes: __________
- [ ] Too fast? Did the arc feel finished too quickly for 9 marks? Notes: __________
- [ ] Rewarding enough? Did +3 marks per chapter and a small once-per-battle form effect feel worth it? Notes: __________
- [ ] Too strong? Did any form at rank 3 (+3 HP, Qi or damage) make hunts trivial? Notes: __________
- [ ] Did the three choices per chapter feel meaningfully different? Did you want to replay for the other paths? Notes: __________
- [ ] Did Jian, Yue and Zhao feel like distinct people? Did their remembered lines feel earned? Notes: __________
- [ ] Was the Zhao duel readable from the telegraph? Too easy once you know the answer? Notes: __________
- [ ] Did companion and sect reactions add flavor, or go unnoticed? Notes: __________
- [ ] Anything that felt like a bug, a wrong number or an unfair limit? Notes: __________
