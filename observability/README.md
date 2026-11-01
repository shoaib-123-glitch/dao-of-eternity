# Dao observability: offline security and network lab

This independent standard-library package validates and correlates strict,
versioned security events. It does not start the game, read environment files,
connect to a database, contact Discord, inspect Docker or listen on a port.
The root v0.24 runtime is unchanged. Optional staged v0.25 hooks are disabled by
default and require separate review before private testing.

From the bot directory:

```powershell
python -m unittest discover -s observability/tests -v
python -m observability check
python -m observability demo --output .\observability-demo-001
python -m observability analyze --input .\observability-demo-001\events.json --output .\observability-analysis-001
```

Choose a new output directory each time. Files are created exclusively; previous
incident reports and partial output directories are never overwritten or reset.
`check` validates a descriptive JSON file only. Its PostgreSQL loopback target
is not probed and no network authorization is implied.

## Implemented

- Strict JSON envelope/schema version `1.0`; unknown keys and arbitrary strings
  are rejected. Only allowlisted classifications, numeric latency and keyed HMAC
  references leave the boundary. Exception messages, SQL, parameters, tokens,
  passwords, raw user IDs, names, content, IP addresses and connection URLs have
  no event fields. Unrecognized SQLSTATE codes are omitted.
- HMAC-SHA256 identifiers use separate actor/guild/request domains. A dedicated
  explicit 32-byte-or-longer key is required. The package reads no credential
  files or environment variables. Emitters refuse pickle/copy so the key is
  never serialized. Demo keys are synthetic and must never be
  used for real telemetry. Separate real deployments need privately managed,
  rotatable keys and retention policies.
- Six deterministic correlation rules: authentication/authorization bursts,
  replay bursts, excessive command frequency, database outages and service
  interruptions. Duplicate copies of one event do not inflate counts; conflicting
  event IDs are rejected. Actor rules include guild and mode in their grouping.
- Passive application/PostgreSQL/container health observations, freshness,
  request mean/p95 latency, completed-request error rate and database-error count.
  Missing/stale observations are `unknown`; startup alone is not healthy.
- Validated JSON/CSV exports and a finite-file analyzer. Synthetic and live
  events cannot be mixed in one envelope. An additional explicit export opt-in
  is required for real events, and reports retain their `sanitized-live` label.

## Safe API

```python
from observability import EventEmitter

events = []
emitter = EventEmitter(b"synthetic-example-key-at-least-32-bytes", sink=events.append,
                       instance_id="documentation-example")
emitter.emit("unauthorized_command", actor="synthetic-player", guild="synthetic-guild",
             operation="hunt", outcome="denied", reason="not_owner")
```

There is no automatic sink, uploader or file writer. For an explicitly enabled
private integration, `mode="sanitized-live", allow_live=True` enables event
construction; callers choose a bounded private sink and must handle telemetry
failure without changing game transactions. A live exporter separately requires
`allow_sanitized_live_export=True`; CLI import/export requires
`--allow-sanitized-live-export`. Never put real telemetry into portfolio demos.
Pseudonymization is not anonymization. Keeping a key stable allows correlation;
rotation deliberately changes identities and breaks cross-key correlation.

`events.classify_exception(error)` returns fixed family/reason/SQLSTATE fields.
It never formats error messages. SQLSTATE, when allowlisted, identifies a server
rejection; timeout/connection/Unicode decode/builtin Python failures are separate.
Unknown drivers and arbitrary exception classes are not guessed into a category.

## Rule semantics

| ID | Threshold | Window | Group | Severity |
| --- | --- | --- | --- | --- |
| AUTHN_BURST | 5 failures | 60 seconds | mode/guild/actor/service | high |
| AUTHZ_BURST | 5 denied commands | 60 seconds | mode/guild/actor/service | high |
| REPLAY_BURST | 3 recorded retries | 60 seconds | mode/guild/actor/service/operation | medium |
| COMMAND_RATE | 20 submissions | 10 seconds | mode/guild/actor/service | medium |
| DATABASE_OUTAGE | 3 errors | 60 seconds | mode/service | high |
| SERVICE_INTERRUPTION | 1 unexpected stop or 3 stops | 300 seconds | mode/service | high |

Windows include the lower bound; one group alerts at most once per window.
Service-grouped alerts (database/interruption) carry `actor_ref: null`.
Alerts are triage signals, not proof of attacks. A crash cannot emit its own stop
event reliably; external observation is needed to detect abrupt process death.
The passive freshness check helps identify missing observations but is not a
running liveness watchdog.

## Network boundary and limitations

The supplied lab config describes `127.0.0.1:5433` only, with probes disabled.
Docker host publishing, database credentials and container health must still be
verified by separately authorized operator actions. Container names resolve on a
Docker network; a Windows host normally uses the loopback published port. A
successful TCP connection would not prove database identity or SQL authorization.
This implementation deliberately has no socket, SQL or subprocess probe adapter.
Prometheus/Grafana are not required for finite offline cases; a production collector,
metrics listener, dashboards and secure storage remain future work.

`schema.py` builds the committed `event.schema.json`; tests verify exact equality.
The Python validator enforces the same strict fields at runtime without requiring
third-party schema packages; `tests/test_redaction_schema.py` fuzzes both for
agreement. The timestamp pattern is ASCII and range-bounded so validators that
treat `format` as an annotation still reject impossible months and hours. SOC Lab compatibility, where provided, uses an explicit
synthetic adapter, not automatic access to existing lab incidents or the game DB.
