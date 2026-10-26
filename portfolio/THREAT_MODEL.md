# Threat model and authorized defensive tests

Scope: a private, owner-controlled game process, its isolated PostgreSQL test
database, the optional v0.25 telemetry hook and the offline analysis lab.
Testing third-party services, scanning, credential guessing, permission changes
and destructive database operations are out of scope.

## STRIDE summary

| Category | Example threat here | Main control | Where it is tested |
| --- | --- | --- | --- |
| Spoofing | Another player presses someone else's buttons; the bot connects to an unintended database on the same port | Owner/guild checks on views and handlers; launcher checks cluster identifier, database and role | v0.25 UI owner-only tests; offline identity-rejection fixtures |
| Tampering | Edited event or alert file changes conclusions; replayed interaction changes balances | Strict schema, duplicate-key and conflicting-ID rejection; receipts and row locks | `observability/tests`; persistence and duplicate-transaction regressions |
| Repudiation | No trustworthy record of who was denied what | Pseudonymous `actor_ref`/`guild_ref` plus fixed reason codes; exclusive (never overwritten) export files | Detection and export tests |
| Information disclosure | Tokens, DSNs, SQL or raw Discord IDs leak into logs or a portfolio | No free-text fields, message-free exception classification, HMAC pseudonyms, synthetic-only portfolio | `test_redaction_schema.py` secret-leak and pickle/copy tests |
| Denial of service | Command floods; huge or deeply nested import files | Game cooldowns; 4,000,000-byte and 10,000-event limits; bounded windows; 500-row dashboard view | Boundary and oversize-input tests |
| Elevation of privilege | Telemetry or troubleshooting used to grant rewards or database rights | Observer cannot return values or raise into handlers; runbook forbids grant changes and admin logins | Observer failure tests; documented authorization gates |

## Threats, defenses and limits

| Threat or failure | Impact | Primary defense | Safe evidence/test | Residual limitation |
| --- | --- | --- | --- | --- |
| Replayed craft/equip/reward interaction | Double spend or award | `living_dao_actions` receipts, row locks, idempotent handlers | Duplicate and rollback regressions; synthetic `REPLAY_BURST` | Telemetry alone cannot prove a database mutation |
| Unauthorized component or command | Using another player's controls | Owner and guild checks | Fake-interaction unit tests; synthetic `AUTHZ_BURST` | Several accounts run by one person cannot be identified |
| Excessive command submissions | Load, poor usability | Existing cooldowns plus advisory `COMMAND_RATE` | Simulated submissions at fixed timestamps | No load test or live rate-limit tuning |
| Wrong PostgreSQL endpoint, role or cluster | Progress written to the wrong data | Launcher identity checks | Offline configuration rejection fixtures | Live listener needs authorized inspection |
| Lost database connectivity | Failed actions | Transaction rollback, safe error replies, no blind retry | Fake connection errors; synthetic outage and recovery | No real outage or failover tested |
| Abrupt process stop | Incomplete work, no final log | Transactions and operator recovery steps | Synthetic `unexpected` stop | Missing shutdown is a hypothesis, not proof |
| Secret leakage through diagnostics | Credential disclosure | Fixed fields and error categories | Token/DSN/password redaction tests | Analyst free-text notes still need review |
| HMAC key exposure | Pseudonyms can be reversed by recomputing from known Discord IDs | Key passed explicitly, never read from files by the package, not serializable | `repr`/pickle/copy tests | Key storage and rotation are operator duties |
| Telemetry failure | Changed or broken reply | Observer swallows every exception and returns nothing | Failing-emitter and disabled-observer tests | Hook covers story handlers only |
| Malicious dashboard import | Script injection or misleading view | Synthetic-only schema, size caps, CSP, `textContent` | Contract tests on the dashboard source | Rendering and download need a manual browser check |
| Event flood or out-of-order records | Wrong alerts, memory growth | Bounded files and windows; deterministic sort | Boundary, ordering and duplicate-event tests | Lab analyzer, not a production SIEM |

## Test authorization

All supplied tests use fabricated actors, in-memory state and synthetic events.
The v0.25 suite blocks sockets, subprocesses and `.env` access and reports guard
violations. No test connects to Discord or PostgreSQL, runs Docker, scans ports,
injects SQL, resets player state or changes credentials. Any future login, TCP
probe, container inspection or database operation needs separate authorization.

## Acceptance criteria

Duplicate requests must not duplicate rewards; observer failure must not change
replies or award resources; malformed or secret-bearing records must fail
closed; the synthetic demo must produce exactly its five expected alerts while
the control actors stay silent; exports must stay labeled synthetic. An alert
should explain its evidence and bounds, never claim a confirmed compromise.
