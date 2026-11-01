"""Strict, small events: no arbitrary labels, exception messages or records.

Pseudonymous identifiers remain sensitive in combination. Real-mode collection
and export require independent opt-ins; pseudonymization is not anonymization.
"""

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import hmac
import json
import math
import re
import secrets


VERSION = "1.0"
SOURCE = "dao-observability"
KINDS = {
    "authn_failure": "warning", "authz_failure": "warning",
    "unauthorized_command": "warning", "transaction_replay": "warning",
    "command_submitted": "info", "command_completed": "info",
    "database_failure": "error", "service_start": "info",
    "service_stop": "info", "unexpected_error": "error",
    "health_observation": "info",
}
OPERATIONS = frozenset({
    "command", "start", "qi", "hunt", "craft", "equip", "explore",
    "sect_commission", "sect_project", "story", "npc", "technique", "bond",
    "startup", "shutdown", "health", "connect", "guide", "companion",
    "cultivation", "inventory", "adopt", "recover",
})
OUTCOMES = frozenset({"accepted", "denied", "duplicate", "success", "failure", "healthy", "unhealthy", "unknown"})
REASONS = frozenset({
    "not_owner", "not_guild", "missing_permission", "unknown_operation",
    "duplicate_interaction", "cooldown", "rate_limit", "authentication_failed",
    "connection_refused", "connection_lost", "timeout", "configuration",
    "server_rejection", "decode", "python", "expected", "unexpected",
    "disabled", "not_observed", "synthetic", "operator_shutdown",
})
FAMILIES = frozenset({"authentication", "authorization", "server", "driver", "timeout", "connection", "python", "configuration", "unknown"})
# SQLSTATEs are fixed public protocol identifiers, never exception text.
SQLSTATES = frozenset({
    "08000", "08001", "08003", "08004", "08006", "08007", "08P01",
    "22000", "22003", "22007", "22012", "22P02", "23000", "23502",
    "23503", "23505", "23514", "25000", "25001", "25P02", "28000",
    "28P01", "40001", "40P01", "42000", "42501", "42601", "42703",
    "42804", "42883", "42P01", "42P07", "53000", "53100", "53200",
    "53300", "53400", "55000", "57014", "57P01", "57P02", "57P03",
    "58000", "58030", "XX000",
})
SERVICES = frozenset({"application", "postgresql", "container"})
MODES = frozenset({"synthetic", "sanitized-live"})
# ASCII-only and range-bounded so JSON Schema validators that treat "format" as
# an annotation still reject impossible months/hours; fromisoformat checks dates.
TIMESTAMP_PATTERN = r"^[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])T([01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9]\.[0-9]{3}Z$"


class EventValidationError(ValueError):
    """Messages are constant and never include rejected data."""


def utc(value=None):
    if value is None:
        value = datetime.now(timezone.utc)
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise EventValidationError("Timestamp must be timezone-aware.")
    return value.astimezone(timezone.utc)


def timestamp(value=None):
    return utc(value).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(TIMESTAMP_PATTERN[1:-1], value):
        raise EventValidationError("Invalid event timestamp.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EventValidationError("Invalid event timestamp.") from None


def _choice(value, choices):
    if value is not None and (not isinstance(value, str) or value not in choices):
        raise EventValidationError("Non-allowlisted event classification.")
    return value


def _ref(value, prefix):
    if value is not None and (not isinstance(value, str) or not re.fullmatch(prefix + r"_[0-9a-f]{24}", value)):
        raise EventValidationError("Invalid pseudonymous reference.")


@dataclass(frozen=True)
class SecurityEvent:
    schema_version: str
    event_id: str
    occurred_at: str
    source: str
    mode: str
    kind: str
    severity: str
    service: str
    actor_ref: str | None = None
    guild_ref: str | None = None
    request_ref: str | None = None
    operation: str | None = None
    outcome: str | None = None
    reason: str | None = None
    family: str | None = None
    sqlstate: str | None = None
    sqlstate_class: str | None = None
    duration_ms: float | None = None

    def __post_init__(self):
        if self.schema_version != VERSION or self.source != SOURCE:
            raise EventValidationError("Unsupported event contract.")
        if not isinstance(self.event_id, str) or not re.fullmatch(r"[0-9a-f]{32}", self.event_id):
            raise EventValidationError("Invalid event identifier.")
        parse_timestamp(self.occurred_at)
        if not isinstance(self.kind, str) or self.kind not in KINDS or self.severity != KINDS[self.kind]:
            raise EventValidationError("Invalid event classification.")
        if not isinstance(self.mode, str) or not isinstance(self.service, str) or self.mode not in MODES or self.service not in SERVICES:
            raise EventValidationError("Invalid event context.")
        _ref(self.actor_ref, "actor")
        _ref(self.guild_ref, "guild")
        _ref(self.request_ref, "request")
        _choice(self.operation, OPERATIONS)
        _choice(self.outcome, OUTCOMES)
        _choice(self.reason, REASONS)
        _choice(self.family, FAMILIES)
        _choice(self.sqlstate, SQLSTATES)
        if self.sqlstate_class != (self.sqlstate[:2] if self.sqlstate else None):
            raise EventValidationError("Inconsistent SQLSTATE class.")
        if self.duration_ms is not None and (
            isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, (int, float))
            # Range first: math.isfinite(10**400) raises OverflowError.
            or not 0 <= self.duration_ms <= 3_600_000 or not math.isfinite(self.duration_ms)
        ):
            raise EventValidationError("Invalid request duration.")
        if self.duration_ms == 0:
            # Normalize -0.0 so no JSON/CSV value starts with a formula sign.
            object.__setattr__(self, "duration_ms", self.duration_ms + 0)

    def to_dict(self):
        return asdict(self)

    def to_json(self):
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), allow_nan=False)

    @classmethod
    def from_dict(cls, value):
        if not isinstance(value, dict) or set(value) != set(cls.__dataclass_fields__):
            raise EventValidationError("Unexpected event fields.")
        try:
            return cls(**value)
        except (TypeError, ValueError):
            raise EventValidationError("Invalid event document.") from None


class EventEmitter:
    """Explicit HMAC key; no environment reads and no built-in file/network sink.

    A fresh process nonce prevents cross-restart event-id collisions. Tests and
    demos supply a fixed synthetic nonce; production hooks should leave it None.
    """

    def __init__(self, key, *, mode="synthetic", allow_live=False, sink=None, instance_id=None):
        if not isinstance(key, bytes) or len(key) < 32:
            raise EventValidationError("A dedicated HMAC key of at least 32 bytes is required.")
        if mode not in MODES or (mode == "sanitized-live" and allow_live is not True):
            raise EventValidationError("Live telemetry is disabled without explicit opt-in.")
        if instance_id is not None and mode != "synthetic":
            raise EventValidationError("Fixed instance identifiers are synthetic-only.")
        if instance_id is not None and (not isinstance(instance_id, str) or not re.fullmatch(r"[a-z0-9-]{1,64}", instance_id)):
            raise EventValidationError("Invalid synthetic instance identifier.")
        self._key = key
        self.mode = mode
        self._sink = sink
        self._counter = 0
        self._nonce = instance_id or secrets.token_hex(16)

    def pseudonym(self, value, prefix):
        if value is None:
            return None
        if prefix not in {"actor", "guild", "request"}:
            raise EventValidationError("Invalid reference type.")
        if isinstance(value, bool) or not isinstance(value, (str, int)) or (isinstance(value, int) and not -10**255 < value < 10**256) or not 1 <= len(str(value)) <= 256:
            raise EventValidationError("Invalid identifier input.")
        digest = hmac.new(self._key, (prefix + ":" + str(value)).encode("utf-8"), hashlib.sha256).hexdigest()[:24]
        return prefix + "_" + digest

    def emit(self, kind, *, actor=None, guild=None, request=None, operation=None,
             outcome=None, reason=None, family=None, sqlstate=None, duration_ms=None,
             service="application", timestamp=None):
        self._counter += 1
        # Do not inspect repr()/str() of errors, records or payloads at this boundary.
        event_id = hmac.new(self._key, f"event:{self._nonce}:{self._counter}".encode("ascii"), hashlib.sha256).hexdigest()[:32]
        kind = kind if isinstance(kind, str) else None
        state = sqlstate if isinstance(sqlstate, str) and sqlstate in SQLSTATES else None
        event = SecurityEvent(
            VERSION, event_id, globals()["timestamp"](timestamp), SOURCE, self.mode,
            kind, KINDS.get(kind), service, self.pseudonym(actor, "actor"),
            self.pseudonym(guild, "guild"), self.pseudonym(request, "request"),
            operation, outcome, reason, family, state, state[:2] if state else None,
            duration_ms,
        )
        if self._sink is not None:
            self._sink(event)
        return event

    def __repr__(self):
        return f"EventEmitter(mode={self.mode!r}, key=<redacted>)"

    def __reduce_ex__(self, protocol):
        # pickle/copy would serialize the HMAC key; emitters are process-local.
        raise TypeError("EventEmitter is not serializable.")


def classify_exception(error):
    """Classify safe built-in failures; SQLSTATE is read only from a string field.

    No imports of asyncpg, database connection or exception message formatting.
    Arbitrary driver names are not exported; unknown errors stay unknown.
    """
    if isinstance(error, TimeoutError):
        return {"family": "timeout", "reason": "timeout", "sqlstate": None}
    if isinstance(error, (ConnectionError, BrokenPipeError)):
        return {"family": "connection", "reason": "connection_lost", "sqlstate": None}
    if isinstance(error, UnicodeError):
        return {"family": "driver", "reason": "decode", "sqlstate": None}
    state = getattr(error, "sqlstate", None)
    if isinstance(state, str) and state in SQLSTATES:
        return {"family": "server", "reason": "server_rejection", "sqlstate": state}
    if isinstance(error, (ValueError, TypeError, KeyError, AttributeError, RuntimeError)):
        return {"family": "python", "reason": "python", "sqlstate": None}
    return {"family": "unknown", "reason": "unexpected", "sqlstate": None}
