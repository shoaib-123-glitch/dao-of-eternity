# Dao of Eternity: network and SOC observability portfolio

A local, synthetic monitoring lab built around the trust boundaries of a private
Python Discord RPG and its isolated PostgreSQL playtest database. It demonstrates
structured security events, keyed pseudonymization, bounded alert correlation,
incident triage and operational documentation. It does not claim enterprise SOC
experience, live monitoring or any real incident.

The game and the lab are separate. The demo needs no game database, Discord
connection, Docker, background collector or network listener. The v0.24 release
candidate remains the gameplay baseline; new gameplay is staged under
`development/v025` and is not deployed by this portfolio.

## Quick start (offline, about one minute)

From the bot directory (the folder that contains `observability/` and
`portfolio/`), with the project's existing Python and no extra packages:

```powershell
python -m observability check
python -m observability demo --output .\observability-demo-001
python -m observability analyze --input .\observability-demo-001\events.json --output .\observability-analysis-001
python -m unittest discover -s observability/tests -t . -q
python -m unittest portfolio.test_portfolio_contract -q
```

Expected: `OFFLINE_CONFIG_VALID: ...`, then twice
`OFFLINE_EXPORT_WRITTEN: 45 validated events; 5 alerts; no live probes.`, then
`OK` from both test runs. Each `--output` must be a **new** directory whose parent
exists; reusing a name prints one fixed `OFFLINE_OPERATION_REJECTED` line and
exits with code 2. Then open [dashboard.html](dashboard.html) in a browser and
select the generated (or bundled `demo-data/`) `events.json` and `alerts.json`.
Full steps are in [the demo guide](DEMO.md).

## Skills demonstrated

| Area | What is in this repository | Evidence | Honest scope |
| --- | --- | --- | --- |
| TCP/IP and loopback | `127.0.0.1` vs `::1`, container loopback vs host loopback, why a TCP connect is not database identity | [Network architecture](NETWORK_ARCHITECTURE.md), `observability/health.py` `validate_lab_config` (numeric loopback only, probes must be disabled) | Documented and validated as configuration; no endpoint probed |
| Published ports and Docker networking | Host `5433` to container `5432` publication, loopback-only binding, Compose service-name DNS | [Network architecture](NETWORK_ARCHITECTURE.md), [runbook](RUNBOOK.md) | Expected topology; not inspected live in this cycle |
| PostgreSQL authentication boundaries | Listener vs `pg_hba.conf` vs role vs database privilege vs cluster identity | [Network architecture](NETWORK_ARCHITECTURE.md), [threat model](THREAT_MODEL.md) | Concepts and launcher checks; no SQL run for this lab |
| Secure event design | Versioned schema `1.0`, closed allowlists, HMAC-SHA256 pseudonyms, fixed error messages | `observability/events.py`, `event.schema.json`, `tests/test_redaction_schema.py` | Library plus disabled-by-default v0.25 hook |
| Detection engineering | Six threshold/window rules with deduplication, grouping and suppression | [SOC rules](SOC_RULES.md), `observability/detections.py`, `tests/test_detections.py` | Deterministic lab defaults, not tuned production baselines |
| ATT&CK-style reasoning | Rules related to ATT&CK techniques only where the mapping is honest | [SOC rules](SOC_RULES.md#attck-style-context) | Discussion aid; no adversary emulation |
| Threat modeling | STRIDE table plus threat/defense/test/limit table | [Threat model](THREAT_MODEL.md) | Private hobby system scope |
| Incident response | Symptom triage and per-alert playbooks with authorization gates | [Runbook](RUNBOOK.md), [case studies](cases/README.md) | Synthetic cases; no live response |
| Analyst tooling | Dependency-free dashboard with strict CSP, `textContent` rendering, notes and report export | `dashboard.html`, `dashboard.js`, [contract tests](test_portfolio_contract.py) | Browser smoke test is manual |
| Interoperability | Synthetic export for an earlier SOC Lab 10 dashboard | [Lab 10 compatibility](LAB10_COMPATIBILITY.md) | Checked against a Python re-implementation of the importer, not a browser |

## Documents

- [Demo guide](DEMO.md) and [runbook](RUNBOOK.md)
- [Network architecture](NETWORK_ARCHITECTURE.md) and [security architecture](SECURITY_ARCHITECTURE.md)
- [SOC rule reference](SOC_RULES.md) and [threat model](THREAT_MODEL.md)
- [Case studies](cases/README.md) and [resume bullets](RESUME.md)
- [Lab 10 compatibility](LAB10_COMPATIBILITY.md) and the
  [observability package README](../observability/README.md)

The external Lab 10 dashboard was inspected read-only and its importer was
re-implemented in Python for tests; no browser import was performed. Labs 07-09
source was not inspected and earlier lab incident records were not imported.

## Data handling

Only exports marked `mode: synthetic` are accepted by the dashboard. Never place
`.env` files, tokens, passwords, connection strings, database dumps, Discord names,
message content or player records in this directory. Demo identities come from a
published synthetic key (`observability/demo.py`) and map to no real user. A
keyed hash of a real Discord ID is pseudonymous, not anonymous: a pseudonymized
real-player export is still personal data and is never portfolio material.
Analyst notes are free text and must be reviewed before sharing.

## Validation and limitations

Offline tests cover the event contract, schema/validator agreement, redaction,
deterministic detections, exports, passive health summaries, the Lab 10 adapter
and the dashboard's source contract. Dashboard rendering and downloads need the
short manual browser check in the demo guide. No live outage, scan, probe or
production response is claimed. Monitoring does not replace transaction
correctness, backups, authorization checks or recovery evidence.
