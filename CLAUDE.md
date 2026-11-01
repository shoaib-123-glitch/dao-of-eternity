# CLAUDE.md
Read AGENTS.md first; it is the main instruction file. This file adds Claude Code safety rules.

## Layout
- Root folder = v0.24.0-rc2 private-test runtime. Do not edit for v0.25 work. Check with: python -B verify_release_boundary.py (126 files unchanged).
- development/v025/ = staged v0.25 Living World + Developer Testing Console. Offline tests: python -B -E -s validate_offline.py (from development/v025/).
- observability/, portfolio/ = synthetic-only SOC/portfolio work. No real player data.
- v0.24 and v0.25 are kept as separate tracks for now.

## Never without explicit approval in the current conversation
Docker commands, any SQL write, migrations, backups/restores, starting or stopping the bot, Discord login or command sync, token resets, deleting files, git commit/push/merge/reset/clean/checkout of files, stash.

## Databases
- dao_playtest_v023_rc1 = real v0.24 save. Preserve. Never restore sandbox data into it.
- dao_playtest_v023_v025_dev = v0.25 sandbox (comment dao-v025-developer-sandbox; check with shobj_description). Only place the Developer Console may run.
- dao_eternity_test = old certification attempts. Do not touch.

## Rules
- Never read or print .env, tokens or passwords. Secrets go only through the launcher's masked prompt.
- Never run v0.24 and v0.25 bots on the same Discord app at once. Stop a bot with one Ctrl+C in its own terminal; never kill all python or Docker.
- On a failed launch, stop and run the read-only diagnostic (-DiagnoseOnly -AuthorizeReadOnlyConnection) instead of retrying.
- Report evidence honestly: offline test vs live-verified vs user-reported.
- The user is a beginner: explain steps simply and give copyable commands.
