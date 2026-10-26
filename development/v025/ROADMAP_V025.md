# v0.25 roadmap — planning only

Working direction: a smaller world that remembers the player. Begin only after
v0.24's private regression checklist passes. Ship a connected story/adventure loop
before expanding realms, economies or infrastructure.

## Essential milestones, in order

| Milestone | Small playable scope | Acceptance gate |
| --- | --- | --- |
| 1. Navigation and content foundation | Group related commands with a compatibility plan before adding slash commands; keep current names through a documented transition. Add paged journal/history using buttons. Define a reusable small encounter format rather than a general narrative engine. | Existing commands/progression still work; normal interactions are quick on mobile; stay within the command limit. |
| 2. One living NPC story | Expand one existing NPC (Lin Yue, Mei Xin or Ren Qiu): three short scenes with two meaningful choices each. Remember promises and choices; offer cooperation or polite refusal. Reuse household state and independent cultivation. | Choices have visible, reversible story consequences and receipts; no forced romance, attendance penalty or irreversible player-progress loss. Solo characters get equivalent routes. |
| 3. One connected expedition | One Mistwood route with three nodes, a secret, an NPC consequence and a companion reaction. Introduce two local faction reputations as story access, not passive power multipliers. Reuse the current daily reward ceiling and cooldowns. | Retries cannot reroll a committed node; rewards are bounded; missed days lose nothing; the full loop works for a starter with earned gear. |
| 4. Strategy and a personal Dao technique | Build on current Qi Refining/Foundation/Golden Core rather than immediately adding another realm. Add an explained tribulation decision and one earnable technique with two balanced styles, selectable through existing navigation. Connect its unlock to the expedition/NPC choice. | Manual breakthroughs remain required. Manual/Auto/Assisted share rules. Different styles win through preparation, not an objectively superior paid or rare roll. No retroactive Qi-rate inflation. |

The initial v0.25 slice ends here: one NPC, one route and one technique.
Test it with new and existing characters before taking on optional scope.

## Optional follow-up milestones

- **Companion memories with consequences:** two additional memories per existing
  beast, a preference discovered through interaction and a small tactical choice
  in the expedition. Keep bond, trust, Beast Qi and evolution; no hunger system,
  gacha requirement or decaying friendship.
- **Sect identity and cooperation:** one doctrine-themed shared expedition,
  contribution summaries and cosmetic hall milestones. Add friendly opt-in
  rivalries through capped objectives; no stealing resources, punitive losses
  or requirements that lock solo players out of personal progression.
- **World variety:** a second route and a rotating faction-secret scene selected
  by existing world chapters. Cap content combinations; do not build an endless
  procedural world before authored scenes prove enjoyable.
- **Tribulation depth:** personalized preparation clues and recovery choices for
  existing realms. A new realm requires evidence that players reach the current
  cap, a tested long-term Qi budget and a backward-compatible migration plan.

## Later, after the core loop proves enjoyable

- More NPC arcs and cross-faction consequences; cross-sect events with explicit
  participation and moderation rules.
- A realm beyond Golden Core, additional beast evolutions and complex combo
  customization, each with its own balance and compatibility review.
- Player markets, competitive rankings or large-scale rivalry systems only after
  economy/exploit analysis. None is necessary for v0.25's first playable slice.
- Optional cosmetics: profile frames/colors, titles, companion appearance
  variants and sect banner styles. Begin with earned cosmetic unlocks; consider
  paid cosmetics only after the free game and entitlement/refund behavior work.

## F2P and optional monetization boundaries

Every realm, story, companion and combat style remains earnable. Do not sell Qi,
stats, mastery, better roots, drop chances, energy, cooldown reductions, extra
reward queues, offline bank capacity, paid retries or exclusive optimal builds.
Avoid loot boxes and streak pressure.

Acceptable convenience is presentation only: saved dashboard layouts, filters,
favorite guide pages and cosmetic outfit presets. It must not automate additional
reward actions, advance faster or improve odds. Keep readable defaults and core
navigation free. No payment integration or price-setting is included in this plan.

## Delivery and data requirements

- Implement and test one milestone at a time while keeping each release a
  coherent playable slice. Use additive save fields where practical; define
  defaults and preserve unknown fields and prior receipts.
- Reuse the current interaction receipts, transaction locks, cooldowns and bounded
  rewards. Add deterministic tests for branches, retries, rollback, old saves,
  command count and UI controls; do not create a second testing framework.
- Give players an understandable journal entry for a consequence. Avoid hidden
  permanent penalties, irreversible relationship locks and mandatory purchases.
- Measure through private testers' observed command output: can a beginner find
  the next step, explain an NPC consequence, choose a combat strategy and finish
  the route without staff assistance? Do not add analytics collection by default.
- Gate every live restart, migration, command sync or deployment separately.
  This roadmap creates no tables, story state, commands or live operations.
