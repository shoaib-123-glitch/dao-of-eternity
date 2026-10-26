# Case 1: repeated permission denials, a command burst and replayed transactions

**Classification:** synthetic defensive training; no live accounts or commands.

## Situation

In the demo timeline (2026-01-15, UTC), synthetic actor A makes five denied
`hunt` interactions with reason `not_owner` between 12:00:05 and 12:00:21, then
submits 20 commands between 12:00:30 and 12:00:34.750. Separately, actor C's
single `craft` request (one `request_ref`) is delivered three times between
12:01:10 and 12:01:14; each delivery is recorded as `duplicate`. Actor B has one
`missing_permission` denial as a control.

## Evidence and interpretation

| Alert | Count | Why it fired |
| --- | --- | --- |
| `AUTHZ_BURST` (high) | 5 | 5 denials by one actor in one guild within 60 s |
| `COMMAND_RATE` (medium) | 20 | 20 submissions by one actor within 10 s |
| `REPLAY_BURST` (medium) | 3 | 3 duplicate deliveries of one operation within 60 s |

Actor B's denial is not added to actor A's count, which shows identities are not
merged. The events contain no command content, Discord IDs, credentials or SQL.
The replay alert shows that duplicates were *recorded and rejected*; it does not
show resources were spent twice. That invariant (resources change once) is
covered by the gameplay transaction tests.

Plausible causes: Discord redelivery, an old message's buttons, rapid clicking,
a wrong ownership check, or a deliberate local test. Frequency alone does not
make the synthetic actor an attacker.

## Analyst disposition

Confirm the three rule IDs and counts, confirm actor B did not alert, and compare
the replay sequence with the idempotency tests. If this were real, review only
separately authorized sanitized records, follow the playbooks in the
[runbook](../RUNBOOK.md), and never ban a user automatically.

**Outcome:** expected advisory detections in a controlled simulation. No real
player impact and no claim of a prevented intrusion.
