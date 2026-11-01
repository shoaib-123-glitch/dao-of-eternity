"""JSON Schema contract builder; checked against the committed schema artifact."""

from .events import TIMESTAMP_PATTERN, VERSION, SOURCE, KINDS, OPERATIONS, OUTCOMES, REASONS, FAMILIES, SQLSTATES, SERVICES, MODES


def _nullable_enum(values):
    return {"enum": [None, *sorted(values)]}


def event_schema():
    properties = {
        "schema_version": {"const": VERSION},
        "event_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
        "occurred_at": {"type": "string", "format": "date-time", "pattern": TIMESTAMP_PATTERN},
        "source": {"const": SOURCE}, "mode": {"enum": sorted(MODES)},
        "kind": {"enum": sorted(KINDS)}, "severity": {"enum": sorted(set(KINDS.values()))},
        "service": {"enum": sorted(SERVICES)},
        "operation": _nullable_enum(OPERATIONS), "outcome": _nullable_enum(OUTCOMES),
        "reason": _nullable_enum(REASONS), "family": _nullable_enum(FAMILIES),
        "sqlstate": _nullable_enum(SQLSTATES),
        "sqlstate_class": _nullable_enum({value[:2] for value in SQLSTATES}),
        "duration_ms": {"type": ["number", "null"], "minimum": 0, "maximum": 3_600_000},
    }
    for prefix in ("actor", "guild", "request"):
        properties[prefix + "_ref"] = {"type": ["string", "null"], "pattern": "^" + prefix + "_[0-9a-f]{24}$"}
    conditions = [
        {"if": {"properties": {"kind": {"const": kind}}}, "then": {"properties": {"severity": {"const": severity}}}}
        for kind, severity in sorted(KINDS.items())
    ]
    for state in [None, *sorted(SQLSTATES)]:
        conditions.append({"if": {"properties": {"sqlstate": {"const": state}}}, "then": {"properties": {"sqlstate_class": {"const": state[:2] if state else None}}}})
    return {"type": "object", "additionalProperties": False, "required": sorted(properties), "properties": properties, "allOf": conditions}


def export_schema():
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "urn:dao-observability:events:1.0",
        "title": "Dao sanitized security event envelope v1.0",
        "description": "Strict classifications and HMAC references. Pseudonymous live data remains private; synthetic mode is the only portfolio-safe default.",
        "type": "object", "additionalProperties": False,
        "required": ["schema_version", "source", "mode", "events"],
        "properties": {"schema_version": {"const": VERSION}, "source": {"const": SOURCE},
                       "mode": {"enum": sorted(MODES)}, "events": {"type": "array", "maxItems": 10_000, "items": {"$ref": "#/$defs/event"}}},
        "$defs": {"event": event_schema()},
        "allOf": [{"if": {"properties": {"mode": {"const": mode}}}, "then": {"properties": {"events": {"items": {"properties": {"mode": {"const": mode}}}}}}} for mode in sorted(MODES)],
    }
