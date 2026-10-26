"""Three deterministic synthetic incident cases, with no real user records."""

from datetime import datetime, timedelta, timezone
from .events import EventEmitter


DEMO_KEY = b"synthetic-demo-key-not-for-real-telemetry-2026"
START = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)


def synthetic_events():
    emitter = EventEmitter(DEMO_KEY, instance_id="portfolio-demo-v1")
    events = []

    def emit(second, kind, **fields):
        events.append(emitter.emit(kind, timestamp=START + timedelta(seconds=second), **fields))

    # Case 1: permission failures, then a high-frequency burst. A separate actor
    # is included to show that users are not merged into one count.
    emit(0, "service_start", operation="startup", outcome="accepted", reason="synthetic")
    emit(1, "health_observation", operation="health", outcome="healthy", reason="synthetic")
    for index in range(5):
        emit(5 + index * 4, "unauthorized_command", actor="demo-observer-a", guild="demo-guild", operation="hunt", outcome="denied", reason="not_owner")
    emit(12, "authz_failure", actor="demo-observer-b", guild="demo-guild", operation="command", outcome="denied", reason="missing_permission")
    for index in range(20):
        emit(30 + index * 0.25, "command_submitted", actor="demo-observer-a", guild="demo-guild", operation="command", outcome="accepted")
    # Case 2: three independently recorded duplicate deliveries, not three
    # copies of one log event. The protected application rejects all of them.
    for index in range(3):
        emit(70 + index * 2, "transaction_replay", actor="demo-observer-c", guild="demo-guild", request="demo-transaction-craft-1", operation="craft", outcome="duplicate", reason="duplicate_interaction")
    # Case 3: outage-like errors and an unexpected interruption, followed by
    # passive recovery evidence. No actual services are contacted or restarted.
    for index in range(3):
        emit(120 + index * 5, "database_failure", service="postgresql", operation="connect", outcome="failure", reason="connection_refused", family="connection")
    emit(131, "command_completed", actor="demo-observer-d", operation="command", outcome="failure", reason="timeout", family="timeout", duration_ms=3000)
    emit(132, "service_stop", service="application", operation="shutdown", outcome="failure", reason="unexpected")
    emit(150, "service_start", operation="startup", outcome="accepted", reason="synthetic")
    emit(151, "health_observation", operation="health", outcome="healthy", reason="synthetic")
    emit(152, "health_observation", service="postgresql", operation="health", outcome="healthy", reason="synthetic")
    emit(153, "health_observation", service="container", operation="health", outcome="healthy", reason="synthetic")
    for index, duration in enumerate([12, 20, 30, 15, 18]):
        emit(154 + index, "command_completed", actor="demo-observer-d", operation="qi", outcome="success", duration_ms=duration)
    return sorted(events, key=lambda event: (event.occurred_at, event.event_id))
