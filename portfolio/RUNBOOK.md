# Safe troubleshooting and incident runbook

Use the least invasive evidence first. Record UTC timestamps and fixed codes;
never paste `.env` contents, passwords, bot tokens, DSNs or raw interaction
payloads into a ticket or report. Do not discard recovery receipts or repeat
consumed provisioning or integration attempts.

## 1. Offline checks: no live authorization required

1. Confirm the working directory (the bot directory) and the project's Python.
2. Run `python -m observability check`. It validates the non-secret lab
   description: literal `127.0.0.1` targets, PostgreSQL described as
   `tcp-description-only` on a port from 1024 to 65535, application and container
   `passive` with no port, and `network_probes_enabled: false`. It proves nothing
   about a running service.
3. The intended private endpoint is `127.0.0.1:5433`, database
   `dao_playtest_v023_rc1`, login `dao_playtest_rc1`, recorded cluster identity
   `7699864005918920747`. These are recorded expectations, not live evidence.
4. Use the offline commands in [DEMO.md](DEMO.md) for the lab. For gameplay,
   keep the root v0.24 candidate and its release checklist; `development/v025`
   is a separate staged copy, not the running bot.

## 2. Optional authorized inspection: operator commands, not run here

Each command below needs separate permission because it touches the existing
runtime. Listing it here does not authorize it.

```powershell
# AUTHORIZE first: one TCP handshake to the selected local port; no SQL.
Test-NetConnection -ComputerName 127.0.0.1 -Port 5433 -InformationLevel Detailed
```

Success means something accepted a TCP connection. It does not prove the right
cluster, role, password, privileges or readiness. "Refused" usually means no
listener or no port mapping; a timeout suggests filtering or a stalled service.
Never guess credentials repeatedly.

```powershell
# AUTHORIZE first: container metadata only; no start, stop or exec.
docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
```

Expect the test container's ports column to show `127.0.0.1:5433->5432/tcp`. A
`0.0.0.0:` or `[::]:` prefix means the database is published on every interface
and should be raised with the owner. Never start, stop or remove containers,
publish ports, print environment variables, run SQL or dump broad
`docker inspect` output, which can include secrets.

## 3. Triage by symptom

| Symptom | Distinguish | Next safe decision |
| --- | --- | --- |
| Configuration rejected before connecting | Missing or invalid setting vs credential problem | Fix the non-secret setting; never bypass identity checks |
| TCP refused or timed out | Host, port, mapping, firewall, service down | Authorized metadata review; do not change grants |
| Authentication rejected (`28P01`, `28000`) | Wrong secret or role vs wrong endpoint | Operator enters the secret at a secure prompt, never on a command line |
| Cluster or database identity rejected | Wrong target vs stale expectation | Stop; reconcile through the approved read-only procedure |
| Discord command missing or duplicated | Wrong application, two test apps, guild sync | Authorized sync for the intended app only; no new top-level commands |
| Replay or permission alert | Redelivery, stale button, wrong owner, misuse | Correlate fixed codes; never punish from an alert alone |
| No shutdown observation | Incomplete export, killed process, no adapter | Ask for operator evidence; do not claim a crash or restart anything |

## 4. Alert playbooks

Every alert is advisory. Steps 1 and 2 of each playbook work on the exported
events only; anything further needs authorization.

**AUTHN_BURST / AUTHZ_BURST (high).** 1) Open the alert's `event_ids`; confirm
one `actor_ref`, one guild and the `reason` values (`not_owner`, `not_guild`,
`missing_permission`). 2) Check whether the operations match an old message's
buttons (stale UI) or many different commands (probing). 3) If real, review
sanitized records with the owner; the application already denied every attempt.
No automatic ban. Close as expected if the actor was a test account.

**REPLAY_BURST (medium).** 1) Confirm the events share an operation and, ideally,
one `request_ref`, with `outcome: duplicate`. 2) The alert proves duplicates were
*recorded and rejected*, not that anything was paid twice; the transaction
receipts and row locks are the control. 3) If a real duplicate payout is
suspected, escalate to an authorized read-only receipt review; never edit balances.

**COMMAND_RATE (medium).** 1) Count distinct operations and check cooldown or
`rate_limit` outcomes. 2) Common benign causes: Discord retries, rapid clicking,
a client bug. 3) Recommend tuning or a cooldown review; no live rate-limit change
without approval.

**DATABASE_OUTAGE (high, service-wide, `actor_ref: null`).** 1) Read `reason` and
`family`: `connection_refused` and `timeout` point at reachability,
`authentication_failed` or SQLSTATE class `28` at credentials, other SQLSTATEs at
the server. 2) Check for later `healthy` PostgreSQL observations (recovery). 3)
Use section 2 only with authorization; never fail over, restart or reset data.

**SERVICE_INTERRUPTION (high, service-wide).** 1) Separate one `unexpected` stop
from three stops in 300 seconds (restart loop). 2) Look for a following
`service_start` and `healthy` observation. 3) Request operator evidence before
any restart; preserve recovery receipts; absence of a stop event is not a crash.

## 5. Recovery boundaries

Stopping or restarting a bot, logging into Discord, syncing commands, connecting
to PostgreSQL and making gameplay writes each need explicit live authorization.
Keep the existing private character, Fox, Qi, marks, equipment and sect state.
Take an authorized backup before changing a candidate; a source-copy receipt is
not a database backup. Test v0.25 against a separately authorized disposable
database restored from a permitted backup, never against the live v0.24 save.
