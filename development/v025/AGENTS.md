
# Dao of Eternity — Development Instructions

## Project
A private Discord Xianxia cultivation RPG using Python
and PostgreSQL.

## Main goals
- Create a deep, living cultivation world.
- Keep the game fair for free-to-play players.
- Support cultivation, combat, companions, sects,
  relationships, households, and cooperative activities.
- Build integrated feature updates, not tiny isolated changes.

## Data safety
- Never expose Discord tokens or database passwords.
- Never commit .env files or database backups.
- Never delete or reset player progression.
- Use additive database migrations where possible.
- Require approval before destructive database changes.

## Development rules
- Preserve compatibility with existing player records.
- Write tests for new features and bug fixes.
- Run the existing test suite before finalizing changes.
- Use clear, beginner-friendly setup instructions.
- Do not modify production data during automated tests.
- Review Git changes before committing or pushing.

## Gameplay principles
- Cultivation should involve meaningful progression.
- Spirit companions and NPCs cultivate independently.
- Multiplayer partnerships require consent.
- Solo players must have competitive alternatives.
- Avoid pay-to-win mechanics.

## Workflow
1. Inspect relevant existing code.
2. Make a short implementation plan.
3. Implement related changes together.
4. Run automated tests.
5. Summarize changed files and remaining risks.
6. Ask before pushing or deploying changes.
