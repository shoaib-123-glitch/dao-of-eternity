"""Explicit synthetic-only adapter for the inspected SOC Lab10 file contract.

Only the existing HTML importer source/README were inspected. This adapter
does not load lab records, launch a browser or prove manual browser acceptance.
Importer rules (safeAlerts + upload handler): file <= 3 MiB, alerts <= 2000,
evidence <= 1000 per alert, unique alert_id, severity exactly HIGH/MEDIUM/LOW
(anything else is silently shown as LOW), every value rendered as text.
"""

import hashlib
import json
from .detections import Detector
from .events import EventValidationError, SecurityEvent


MAX_EVENTS, MAX_ALERTS, MAX_EVIDENCE, MAX_BYTES = 10_000, 2000, 1000, 3 * 1024 * 1024
SEVERITY = {"high": "HIGH", "medium": "MEDIUM", "low": "LOW"}
DESCRIPTIONS = {
    "AUTHN_BURST": "Synthetic repeated authentication failures.",
    "AUTHZ_BURST": "Synthetic repeated authorization failures.",
    "REPLAY_BURST": "Synthetic repeated protected-transaction deliveries.",
    "COMMAND_RATE": "Synthetic command submission frequency exceeded the lab threshold.",
    "DATABASE_OUTAGE": "Synthetic database connectivity failures exceeded the lab threshold.",
    "SERVICE_INTERRUPTION": "Synthetic service interruptions require operator review.",
}
TRIAGE = "Review the synthetic timeline, correlate references, and distinguish retries or configuration errors from misuse. No live containment is authorized."


def encoded(document):
    # Byte-identical to exports._exclusive_json, i.e. the file the importer sizes.
    return (json.dumps(document, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def lab10_document(events):
    events = list(events)
    if any(not isinstance(event, SecurityEvent) or event.mode != "synthetic" for event in events):
        raise EventValidationError("The public lab adapter accepts synthetic events only.")
    if len(events) > MAX_EVENTS:
        raise EventValidationError("Synthetic lab input exceeds its limit.")
    by_id = {event.event_id: event for event in events}
    alerts = []
    for alert in Detector().analyze(events):
        if alert["severity"] not in SEVERITY:
            raise EventValidationError("Lab10 accepts HIGH, MEDIUM or LOW severity only.")
        identifier = hashlib.sha256((alert["rule_id"] + ":" + ":".join(alert["event_ids"])).encode("ascii")).hexdigest()[:24]
        evidence = []
        for event_id in alert["event_ids"][:MAX_EVIDENCE]:
            event = by_id[event_id]
            evidence.append({"timestamp": event.occurred_at, "event_type": event.kind,
                             "outcome": event.outcome or "unknown", "request_id": event.request_ref or event.event_id,
                             "player": event.actor_ref or "synthetic-service"})
        alerts.append({
            "alert_id": "synthetic-" + identifier, "rule": alert["rule_id"],
            "severity": SEVERITY[alert["severity"]], "player": alert["actor_ref"] or "synthetic-service",
            "events": alert["count"], "first_seen_utc": alert["first_seen"], "last_seen_utc": alert["last_seen"],
            "description": DESCRIPTIONS[alert["rule_id"]], "recommended_triage": TRIAGE,
            "evidence": evidence,
        })
    if len(alerts) > MAX_ALERTS:
        raise EventValidationError("Synthetic lab output exceeds its alert limit.")
    if len({alert["alert_id"] for alert in alerts}) != len(alerts):
        raise EventValidationError("Synthetic lab output has duplicate alert identifiers.")
    result = {"lab": "Dao Observability / SOC Lab10 Synthetic Adapter v1.0",
              "data_source": "SYNTHETIC ONLY - no Discord, PostgreSQL or Docker access",
              "alerts": alerts}
    if len(encoded(result)) > MAX_BYTES:
        raise EventValidationError("Synthetic lab output exceeds its byte limit.")
    return result
