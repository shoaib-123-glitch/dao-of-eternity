# Security architecture and trust boundaries

## Assets and actors

Protected assets: Discord bot credentials, database credentials, player
progression, private guild activity, database identity evidence, recovery
records and the telemetry HMAC key. Ordinary players, authorized operators,
application code, the optional event hook and a portfolio viewer have different
privileges. A dashboard input file is untrusted even when an operator made it.

| Boundary | Allowed information | Control |
| --- | --- | --- |
| Discord interaction to game handler | Validated action and invoking identity | Guild/owner checks and transaction receipts; raw payloads never logged |
| Game handler to PostgreSQL | Validated bounded changes | Row locks, `living_dao_actions` receipts, rollback; telemetry never authorizes or awards |
| Game handler to v0.25 observer | Allowlisted kind, operation, outcome, reason; integer actor/guild IDs | `security_observer_v025.record` drops anything else and swallows all errors |
| Observer to emitter | Same fields | `EventEmitter` converts IDs to HMAC pseudonyms before an event exists |
| Event file to analysis | Versioned, bounded records | Strict schema, 4,000,000-byte and 10,000-event limits, no file discovery |
| Analysis to public portfolio | Synthetic-only bundle and reviewed notes | `mode: synthetic` required by the dashboard; manual review |

## Event contract (`observability/events.py`)

- Schema version `1.0`, source `dao-observability`, 18 fixed fields. Unknown or
  missing fields, duplicate JSON keys, non-allowlisted values, bad timestamps
  and inconsistent SQLSTATE classes are rejected. The committed
  `event.schema.json` is generated from `schema.py`; tests require exact equality
  and fuzz both validators for agreement.
- There is no free-text field. Exceptions are reduced by `classify_exception` to
  a fixed family, reason and an allowlisted SQLSTATE read from the error's
  `sqlstate` attribute; the message is never read.
- Error messages are constant strings and never include the rejected value. The
  CLI prints one fixed rejection line and exits 2.
- `duration_ms` is bounded to 0..3,600,000 and `-0.0` is normalized so no CSV
  cell starts with a formula character.

## Pseudonymization (HMAC-SHA256)

`EventEmitter(key, ...)` requires a `bytes` key of at least 32 bytes passed
explicitly; it reads no environment variables or files. Each identifier becomes
`prefix_` plus the first 24 hex characters (96 bits) of
`HMAC-SHA256(key, "actor:" | "guild:" | "request:" + str(id))`, so the same raw
number gives unrelated actor and guild references. Event IDs are an HMAC of a
per-process random nonce and a counter. `repr` hides the key, and pickling or
copying an emitter raises `TypeError` so the key cannot be serialized.

Limits, stated plainly:

- Pseudonymous is not anonymous. Anyone with the key can recompute a reference
  from a Discord ID, and Discord IDs are not secret. Protect and rotate the key;
  rotation intentionally breaks correlation across keys.
- With a stable key, references are linkable across events, which is the point
  for detection and also a privacy cost. Retention must be decided by the operator.
- An integer ID and its string form give the same reference.
- The demo key in `observability/demo.py` is public and for synthetic data only.

## Opt-ins

| Step | Default | Opt-in required |
| --- | --- | --- |
| Create live events | `mode="synthetic"` | `mode="sanitized-live", allow_live=True` |
| Fixed instance ID (deterministic event IDs) | random nonce | synthetic mode only |
| Export live events | refused | `allow_sanitized_live_export=True`, CLI `--allow-sanitized-live-export` |
| Import live events | refused | same CLI flag (`allow_sanitized_live_input`) |
| Mixed synthetic and live in one envelope | refused | not possible |
| v0.25 game hook | disabled; nothing calls `enable()` | an operator explicitly calling `enable(emitter)` |
| Lab 10 export | synthetic only | not available for live data |

There is no built-in sink, uploader, daemon or network writer. The v0.25 hook is
wired into the story handlers only, recording `unauthorized_command` denials and
`unexpected_error` failures; its own `classify` maps error types to fixed reasons.
Because it is disabled, v0.25 currently emits nothing.

## Dashboard

`dashboard.html` loads only local `dashboard.css` and `dashboard.js` under a CSP
of `default-src 'none'` with `connect-src 'none'` and `object-src 'none'`. It has
no inline handlers, `fetch`, storage or HTML sinks; imported values are rendered
with `textContent`. Files over 4,000,000 bytes or not marked synthetic are
rejected with messages that do not echo input. Analyst notes are not sanitized:
never type secrets or real-player details into them.

## Availability and operational limits

Detection is advisory: no automated ban, firewall rule or database action.
Correlation cannot establish intent or link alternate accounts to one person.
Missing shutdown events cannot prove a crash. TCP reachability cannot prove
database identity. No public endpoint, Docker socket, database administrator
role or grant change is needed by anything in this portfolio.
