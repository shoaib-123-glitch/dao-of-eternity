# Coordinated development cycle: release separation

The root bot directory is the existing v0.24.0-rc2 candidate. Its runtime,
launcher, gameplay modules, old receipts and recovery files must remain intact.
The supplied live checklist has not passed in full; v0.24 is not declared Stable.

Before v0.25 edits, create a new source-only copy at development/v025.
Copy only top-level Python/PowerShell/Markdown/psql source and requirements.txt,
using exclusive writes. Never copy environment/credential files, caches,
artifacts, player data, Git metadata or recovery/evidence directories. Record
SHA-256 hashes of the original sources in development/v025-baseline.json.
The receipt identifies a source copy, not a database snapshot or live test.

No branch, index, stash, commit or worktree operation is required. Existing
uncommitted files stay where they are. Root runtime hashes are compared again
after work. New gameplay implementation and tests belong only in development/v025;
the original private launcher continues to load only the root v0.24 source.
Any v0.25 private launch requires a separately reviewed candidate procedure and
explicit authorization. Prefer a separate disposable gameplay database restored
from an authorized backup, never the v0.24 character's live database.

The standalone observability package, synthetic tests and exports belong under
observability/. Portfolio documentation and synthetic demonstrations belong under
portfolio/. There is no automatic export of game records, no collector daemon,
and no enabled connection probe or public listener. Integration hooks, if added,
are confined to the staged v0.25 copy and disabled by default.

Parallel ownership: v0.25 engine/persistence, isolated observability implementation,
and portfolio materials can proceed independently after the copy exists. The
primary agent owns release-readiness instructions, staged Discord integration,
cross-workstream review and offline validation. No agent may read secrets or
perform live operations.
