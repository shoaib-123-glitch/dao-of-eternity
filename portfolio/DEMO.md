# Offline demo and screenshot procedure

All commands below process local synthetic data only. They do not start the bot,
open database connections, contact Discord, invoke Docker or start a web server.
Run them from the bot directory. Do not pass `.env` files or player exports.

## Shortest demo

The supplied [demo-data](demo-data/) bundle is deterministic synthetic training
data: 45 events, 5 alerts, a passive health summary, a CSV copy of the events and
a Lab 10 export. Open [dashboard.html](dashboard.html) directly from File
Explorer, choose `demo-data/events.json`, then the matching `alerts.json`.
Nothing loads until you select it. No server or package installation is needed.

## Regenerate and verify

Each `--output` must be a **new** directory whose parent already exists. Earlier
output is never overwritten, reset or deleted.

```powershell
python -m observability check
python -m observability demo --output .\observability-demo-001
python -m observability analyze --input .\observability-demo-001\events.json --output .\observability-analysis-001
python -m unittest discover -s observability/tests -t . -q
python -m unittest portfolio.test_portfolio_contract -q
```

Expected output:

```text
OFFLINE_CONFIG_VALID: probes disabled; no service availability was tested.
OFFLINE_EXPORT_WRITTEN: 45 validated events; 5 alerts; no live probes.
OFFLINE_EXPORT_WRITTEN: 45 validated events; 5 alerts; no live probes.
```

followed by `OK` from both test commands. `demo` writes `events.json`,
`events.csv`, `alerts.json`, `health.json` and `lab10_alerts.json`; with the same
code they are byte-identical to `demo-data/`, because the demo uses a fixed
synthetic key, instance ID and start time (2026-01-15 12:00:00 UTC). `analyze`
re-validates an export and recomputes alerts and health from it.

If the destination exists, its parent is missing, or the input is invalid, the
CLI prints only `OFFLINE_OPERATION_REJECTED: invalid input, provenance,
configuration or output destination.` and exits with code 2. It never echoes the
rejected data, path or OS error. Pick a new name; do not delete evidence to make
a command pass. `check` validates the non-secret `observability/lab_config.json`
description only and does not test availability.

## What the bundle contains

| Alert | Trigger in the synthetic timeline |
| --- | --- |
| `AUTHZ_BURST` (high) | Actor A: 5 denied `hunt` interactions, 12:00:05 to 12:00:21 |
| `COMMAND_RATE` (medium) | Actor A: 20 submissions in 4.75 seconds from 12:00:30 |
| `REPLAY_BURST` (medium) | Actor C: 3 duplicate deliveries of one `craft` request, 12:01:10 to 12:01:14 |
| `DATABASE_OUTAGE` (high) | 3 `connection_refused` PostgreSQL failures, 12:02:00 to 12:02:10 |
| `SERVICE_INTERRUPTION` (high) | 1 application stop marked `unexpected` at 12:02:12 |

Controls that must **not** alert: actor B's single permission denial (users are
not merged), actor D's normal `qi` commands, and the recovery health
observations. `health.json` reports all three services `healthy` at 12:02:39 with
6 completed requests, 1 failure (error rate 1/6), mean latency about 515.8 ms and
p95 3000 ms. Health is passive: it summarizes events and contacts nothing.

## Browser smoke test: manual, not performed by this development cycle

1. Import `events.json`; verify 45 events, the UTC range and the timeline.
2. Import the matching `alerts.json`; check the five rule IDs against
   [SOC_RULES.md](SOC_RULES.md). The browser checks alert structure and that every
   referenced event ID exists; it does not recalculate thresholds.
3. Filter to `database failure`, then back to all kinds. The timeline shows at
   most 500 matching rows; exports keep every loaded event.
4. Add a note separating evidence, hypothesis, uncertainty and next action.
   Export JSON and text and inspect both files. Notes are capped at 6000 characters.
5. Check rejection of malformed JSON, a non-synthetic envelope and an alert file
   whose event IDs are not loaded. Errors must not echo rejected input.
6. Reload: notes disappear because the dashboard uses no browser storage.

The dashboard does not display `health.json`; read it directly. The three
[case studies](cases/README.md) explain the scenarios.

## Optional existing Lab 10 demo

Follow [LAB10_COMPATIBILITY.md](LAB10_COMPATIBILITY.md) and choose only
`lab10_alerts.json`. Compatibility is tested offline against a Python
re-implementation of that dashboard's importer, not by running the browser code.
A manual import is still needed to claim it works visually. Importing there
replaces its alerts and clears its saved notes.

## Screenshots and publishing

Capture (1) the overview with the synthetic marker, (2) a filtered timeline, (3)
a reviewed analyst report and (4) the architecture diagram in your Markdown
viewer. These are instructions; no screenshots are included. Exclude developer
tools, terminal prompts, usernames in paths, private guild names and other
applications. Never show tokens, connection strings or real player data. Do not
caption screenshots as a real attack, production outage or enterprise response.
