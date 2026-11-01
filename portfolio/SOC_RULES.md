# SOC detection reference: contract 1.0

Source of truth: `RULES` in `observability/detections.py`. All detections are
advisory batch correlation over validated, timestamped events; there is no
background worker. `portfolio/test_portfolio_contract.py` checks this table
against the code.

| Rule | Event kinds | Threshold | Window | Group | Severity |
| --- | --- | --- | --- | --- | --- |
| `AUTHN_BURST` | `authn_failure` | 5 | 60 s | mode, service, guild, actor | high |
| `AUTHZ_BURST` | `authz_failure`, `unauthorized_command` | 5 | 60 s | mode, service, guild, actor | high |
| `REPLAY_BURST` | `transaction_replay` | 3 | 60 s | mode, service, guild, actor, operation | medium |
| `COMMAND_RATE` | `command_submitted` | 20 | 10 s | mode, service, guild, actor | medium |
| `DATABASE_OUTAGE` | `database_failure` | 3 | 60 s | mode, service | high |
| `SERVICE_INTERRUPTION` | `service_stop` | 3, or 1 with reason `unexpected` | 300 s | mode, service | high |

## Semantics

- Events are deduplicated by `event_id`; the same ID with different content is
  rejected. Events are processed in `(occurred_at, event_id)` order.
- The window is sliding and includes its lower bound: an event exactly
  `window` seconds older than the current one still counts.
- Suppression: a group alerts at most once per window after its last alert.
- Actor rules skip events with no `actor_ref`, so unrelated anonymous events are
  never merged into one "attacker". Different actors never share a count.
- Service rules (`DATABASE_OUTAGE`, `SERVICE_INTERRUPTION`) always report
  `actor_ref: null`, even if the triggering event had an actor.
- `REPLAY_BURST` groups by operation, not request ID. It shows repeated
  duplicate deliveries were recorded; it does not show a transaction committed
  twice. Idempotency is proven by persistence tests, not alerts.
- `SERVICE_INTERRUPTION` needs explicit stop events. A start with no shutdown
  does **not** trigger it; absence of evidence is not a crash.

Alert fields: `rule_id`, `severity`, `first_seen`, `last_seen`, `count`,
`actor_ref`, `service`, `event_ids`. The dashboard checks that each listed event
exists and fits the alert, but only the Python analyzer computes thresholds.

## What feeds the rules today

Only the synthetic demo (`observability/demo.py`) produces events. The staged
v0.25 hook is disabled by default and, if enabled, would record only story-panel
`unauthorized_command` and `unexpected_error` events, which can feed
`AUTHZ_BURST` only. Nothing emits `authn_failure`, `transaction_replay`,
`command_submitted`, `database_failure` or `service_stop` from the game yet, and
no rule uses `unexpected_error` or `command_completed` (those feed health
metrics).

## ATT&CK-style context

MITRE ATT&CK describes adversary behaviour against enterprise systems. This lab
has no adversary emulation, so these are discussion aids, not coverage claims.

| Rule | Related technique | How honest the link is |
| --- | --- | --- |
| `AUTHN_BURST` | T1110 Brute Force | Reasonable if `authn_failure` came from a real login surface; the game has none today |
| `AUTHZ_BURST` | None direct | Repeated denied actions are closer to application misuse than an ATT&CK technique |
| `REPLAY_BURST` | None direct | Request replay is an application-logic concern (OWASP/CAPEC territory), not ATT&CK |
| `COMMAND_RATE` | T1499.003 Application Exhaustion Flood | Only if intent were proven; most bursts are retries or clicks |
| `DATABASE_OUTAGE` | T1499 Endpoint Denial of Service (Impact) | Weak: outages are usually operational, and the rule cannot tell |
| `SERVICE_INTERRUPTION` | T1489 Service Stop | Weak: an unexpected stop is usually a crash or operator action |

## Event schema

The envelope is `{schema_version: "1.0", source: "dao-observability", mode,
events: [...]}` with at most 10,000 events. Each event has 18 fixed fields:
categorical values from closed allowlists, a UTC millisecond timestamp, a
32-hex event ID, HMAC-SHA256 references (`actor_`/`guild_`/`request_` plus 24
hex) and a bounded `duration_ms`. There is no message or free-text field.
`sanitized-live` requires explicit opt-ins (see the
[security architecture](SECURITY_ARCHITECTURE.md#opt-ins)); the dashboard accepts
only `synthetic`.

## Tuning and false positives

These are lab defaults, not production baselines. Discord redelivery, stale
buttons, impatient players and client bugs all create alerts. Review distinct
events, ordering, cooldown outcomes and persistence tests before proposing any
action. No automatic restriction, firewall rule or database action exists.
