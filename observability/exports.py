"""Versioned SOC handoff. Explicit files only; no discovery, uploads or secrets."""

import csv
import json
from pathlib import Path
from .events import SecurityEvent, EventValidationError, VERSION, SOURCE, MODES


MAX_INPUT_BYTES = 4_000_000
MAX_EVENTS = 10_000


def envelope(events, *, allow_sanitized_live_export=False):
    values = list(events)
    if len(values) > MAX_EVENTS or any(not isinstance(item, SecurityEvent) for item in values):
        raise EventValidationError("Export requires a bounded list of validated events.")
    modes = {event.mode for event in values}
    if len(modes) > 1:
        raise EventValidationError("Synthetic and real observations cannot share an export.")
    mode = next(iter(modes), "synthetic")
    if mode == "sanitized-live" and allow_sanitized_live_export is not True:
        raise EventValidationError("Real-event export requires independent explicit authorization.")
    return {"schema_version": VERSION, "source": SOURCE, "mode": mode, "events": [item.to_dict() for item in values]}


def validate_envelope(value):
    if not isinstance(value, dict) or set(value) != {"schema_version", "source", "mode", "events"}:
        raise EventValidationError("Unexpected export envelope fields.")
    if value["schema_version"] != VERSION or value["source"] != SOURCE or not isinstance(value["mode"], str) or value["mode"] not in MODES:
        raise EventValidationError("Unsupported export contract.")
    if not isinstance(value["events"], list) or len(value["events"]) > MAX_EVENTS:
        raise EventValidationError("Invalid event collection.")
    events = [SecurityEvent.from_dict(item) for item in value["events"]]
    if any(event.mode != value["mode"] for event in events):
        raise EventValidationError("Inconsistent export provenance.")
    return events


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise EventValidationError("Duplicate JSON fields are not accepted.")
        value[key] = item
    return value


def read_events(path, *, allow_sanitized_live_input=False):
    target = Path(path)
    # Fixed interchange suffix prevents reading credential files by accident.
    if target.suffix.lower() != ".json" or target.is_symlink():
        raise EventValidationError("Expected a regular JSON export file.")
    if target.stat().st_size > MAX_INPUT_BYTES:
        raise EventValidationError("Event export exceeds offline input limit.")
    with target.open("r", encoding="utf-8") as stream:
        try:
            value = json.load(stream, object_pairs_hook=_reject_duplicate_keys)
        # ValueError also covers oversized integer literals (int digit limit).
        except (ValueError, RecursionError):
            raise EventValidationError("Invalid JSON export.") from None
    events = validate_envelope(value)
    if value["mode"] == "sanitized-live" and allow_sanitized_live_input is not True:
        raise EventValidationError("Real-event import requires explicit authorization.")
    return events


def _exclusive_json(path, value):
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def write_bundle(output, events, alerts, health, *, allow_sanitized_live_export=False):
    """Require a new directory, never overwrite a previous case or evidence file.

    A failed write can leave a partial new directory. It is intentionally never
    deleted, reset or automatically reused; select a different fresh destination.
    """
    events = list(events)
    event_envelope = envelope(events, allow_sanitized_live_export=allow_sanitized_live_export)
    # Alerts and health must be recomputed from these validated events rather
    # than taking arbitrary report strings/fields supplied by a caller.
    from .detections import Detector
    from .health import HealthMonitor
    expected_alerts = Detector().analyze(events)
    if alerts != expected_alerts:
        raise EventValidationError("Alerts must match validated event analysis.")
    monitor = HealthMonitor(max_age_seconds=health.get("window_seconds", 120))
    for event in events:
        monitor.observe(event)
    from .events import parse_timestamp
    expected_health = monitor.report(now=parse_timestamp(health.get("observed_at")))
    if health != expected_health:
        raise EventValidationError("Health report must match validated observations.")
    destination = Path(output)
    destination.mkdir(parents=False, exist_ok=False)
    _exclusive_json(destination / "events.json", event_envelope)
    fields = list(SecurityEvent.__dataclass_fields__)
    with (destination / "events.csv").open("x", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(event_envelope["events"])
    provenance = {"schema_version": VERSION, "source": SOURCE, "mode": event_envelope["mode"]}
    _exclusive_json(destination / "alerts.json", {**provenance, "alerts": alerts})
    _exclusive_json(destination / "health.json", {**provenance, "health": health})
    if event_envelope["mode"] == "synthetic":
        from .lab10 import lab10_document
        _exclusive_json(destination / "lab10_alerts.json", lab10_document(events))
    return destination
