"""Passive health and latency summaries. Nothing connects or executes commands."""

from collections import Counter
from .events import SecurityEvent, EventValidationError, SERVICES, timestamp, parse_timestamp


class HealthMonitor:
    """Consumes externally observed facts; omitted and stale services are unknown.

    A passive healthy observation is not a connectivity probe, SQL query,
    container inspection or promise that a service is currently available.
    """

    def __init__(self, *, max_age_seconds=120):
        if isinstance(max_age_seconds, bool) or not isinstance(max_age_seconds, int) or not 1 <= max_age_seconds <= 3600:
            raise EventValidationError("Invalid health freshness threshold.")
        self.max_age_seconds = max_age_seconds
        self._events = {}

    def observe(self, event):
        if not isinstance(event, SecurityEvent):
            raise EventValidationError("Health monitor accepts validated events only.")
        previous = self._events.get(event.event_id)
        if previous is not None and previous != event:
            raise EventValidationError("Conflicting event identifiers.")
        self._events[event.event_id] = event

    def report(self, *, now=None):
        now_text = timestamp(now)
        now_dt = parse_timestamp(now_text)
        # Future observations cannot assert present health or distort metrics.
        events = [event for event in self._events.values() if parse_timestamp(event.occurred_at) <= now_dt]
        ordered = sorted(events, key=lambda event: (event.occurred_at, event.event_id))
        recent = [event for event in ordered if 0 <= (now_dt - parse_timestamp(event.occurred_at)).total_seconds() <= self.max_age_seconds]
        counts = Counter(event.kind for event in recent)
        latencies = sorted(event.duration_ms for event in recent if event.kind == "command_completed" and event.duration_ms is not None)
        failures = sum(event.kind == "command_completed" and event.outcome == "failure" for event in recent)
        completed = counts["command_completed"]
        services = {}
        for service in sorted(SERVICES):
            observations = [event for event in ordered if event.service == service and event.kind in {"health_observation", "database_failure", "service_start", "service_stop"}]
            last = observations[-1] if observations else None
            age = (now_dt - parse_timestamp(last.occurred_at)).total_seconds() if last else None
            status = "unknown"
            if last and age <= self.max_age_seconds:
                if last.kind in {"database_failure", "service_stop"} or last.outcome == "unhealthy":
                    status = "unhealthy"
                elif last.kind == "health_observation" and last.outcome == "healthy":
                    status = "healthy"
                # A start event proves only that startup was attempted.
            services[service] = {"status": status, "last_observed": last.occurred_at if last else None, "age_seconds": age}
        percentile = latencies[max(0, (95 * len(latencies) + 99) // 100 - 1)] if latencies else None
        return {
            "schema_version": "1.0", "observed_at": now_text, "window_seconds": self.max_age_seconds,
            "probe_mode": "passive_only", "services": services,
            "metrics": {"completed_requests": completed, "failed_requests": failures,
                        "error_rate": failures / completed if completed else None,
                        "mean_latency_ms": sum(latencies) / len(latencies) if latencies else None,
                        "p95_latency_ms": percentile, "database_failures": counts["database_failure"]},
        }


def validate_lab_config(value):
    """Validate a non-secret description; there is deliberately no probe runner."""
    if not isinstance(value, dict) or set(value) != {"schema_version", "mode", "network_probes_enabled", "targets", "max_age_seconds"}:
        raise EventValidationError("Invalid lab configuration shape.")
    if value["schema_version"] != "1.0" or value["mode"] != "synthetic" or value["network_probes_enabled"] is not False:
        raise EventValidationError("Only disabled-probe synthetic lab configuration is supported.")
    HealthMonitor(max_age_seconds=value["max_age_seconds"])
    targets = value["targets"]
    if not isinstance(targets, dict) or set(targets) != SERVICES:
        raise EventValidationError("Invalid lab target set.")
    for service, target in targets.items():
        if not isinstance(target, dict) or set(target) != {"transport", "host", "port"}:
            raise EventValidationError("Invalid lab target shape.")
        if target["host"] != "127.0.0.1":
            raise EventValidationError("Lab targets must use numeric loopback.")
        expected = "passive" if service in {"application", "container"} else "tcp-description-only"
        if target["transport"] != expected:
            raise EventValidationError("Unsupported lab transport.")
        port = target["port"]
        if service == "postgresql":
            if isinstance(port, bool) or not isinstance(port, int) or not 1024 <= port <= 65535:
                raise EventValidationError("Invalid development port.")
        elif port is not None:
            raise EventValidationError("Passive services cannot expose a listening port.")
    return value
