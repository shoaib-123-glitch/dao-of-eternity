"""Deterministic batch correlation, bounded windows and duplicate delivery safety."""

from datetime import timedelta
from .events import SecurityEvent, EventValidationError, parse_timestamp


RULES = {
    "AUTHN_BURST": {"kinds": {"authn_failure"}, "threshold": 5, "seconds": 60, "severity": "high", "group": "actor"},
    "AUTHZ_BURST": {"kinds": {"authz_failure", "unauthorized_command"}, "threshold": 5, "seconds": 60, "severity": "high", "group": "actor"},
    "REPLAY_BURST": {"kinds": {"transaction_replay"}, "threshold": 3, "seconds": 60, "severity": "medium", "group": "operation"},
    "COMMAND_RATE": {"kinds": {"command_submitted"}, "threshold": 20, "seconds": 10, "severity": "medium", "group": "actor"},
    "DATABASE_OUTAGE": {"kinds": {"database_failure"}, "threshold": 3, "seconds": 60, "severity": "high", "group": "service"},
    "SERVICE_INTERRUPTION": {"kinds": {"service_stop"}, "threshold": 3, "seconds": 300, "severity": "high", "group": "service"},
}


class Detector:
    """Analyze an explicit finite collection, with no background worker or IO.

    Windows include their lower bound. One alert per group per window suppresses
    noise; a single unexpected stop is always significant. Counts establish
    evidence for triage, not proof of malicious intent or unique human identity.
    """

    def analyze(self, events):
        unique = {}
        for event in events:
            if not isinstance(event, SecurityEvent):
                raise EventValidationError("Detector accepts validated events only.")
            previous = unique.get(event.event_id)
            if previous is not None and previous != event:
                raise EventValidationError("Conflicting event identifiers.")
            unique[event.event_id] = event
        ordered = sorted(unique.values(), key=lambda event: (event.occurred_at, event.event_id))
        alerts = []
        for rule_id, rule in RULES.items():
            windows, last_alert = {}, {}
            for event in ordered:
                if event.kind not in rule["kinds"]:
                    continue
                # No actor-based correlation on missing identities; do not merge
                # unrelated users into one synthetic-looking attack.
                if rule["group"] != "service" and event.actor_ref is None:
                    continue
                group = (event.mode, event.service)
                if rule["group"] != "service":
                    group += (event.guild_ref, event.actor_ref)
                if rule["group"] == "operation":
                    group += (event.operation,)
                current = parse_timestamp(event.occurred_at)
                lower = current - timedelta(seconds=rule["seconds"])
                window = windows.setdefault(group, [])
                window[:] = [item for item in window if parse_timestamp(item.occurred_at) >= lower]
                window.append(event)
                unexpected = rule_id == "SERVICE_INTERRUPTION" and event.reason == "unexpected"
                if len(window) < rule["threshold"] and not unexpected:
                    continue
                if group in last_alert and (current - last_alert[group]).total_seconds() <= rule["seconds"]:
                    continue
                last_alert[group] = current
                alerts.append({
                    "rule_id": rule_id, "severity": rule["severity"],
                    "first_seen": window[0].occurred_at, "last_seen": event.occurred_at,
                    "count": len(window), "actor_ref": event.actor_ref if rule["group"] != "service" else None,
                    "service": event.service, "event_ids": [item.event_id for item in window],
                })
        return sorted(alerts, key=lambda alert: (alert["last_seen"], alert["rule_id"]))
