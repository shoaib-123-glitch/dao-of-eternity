import json
from datetime import datetime, timezone
from pathlib import Path
import unittest
from unittest.mock import patch

from observability.events import EventEmitter, EventValidationError, SecurityEvent, classify_exception
from observability.schema import export_schema


KEY = b"synthetic-unit-test-key-at-least-32-bytes"
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class EventTests(unittest.TestCase):
    def setUp(self):
        self.emitter = EventEmitter(KEY, instance_id="events-test")

    def emit(self, **fields):
        return self.emitter.emit("command_submitted", timestamp=NOW, **fields)

    def test_round_trip_strict_document(self):
        event = self.emit(operation="hunt", actor=123, guild=456, request=789)
        self.assertEqual(SecurityEvent.from_dict(json.loads(event.to_json())), event)

    def test_hmac_references_do_not_include_raw_identifiers(self):
        event = self.emit(actor="discord-user-12345", guild="guild-67890", request="request-777")
        self.assertNotIn("discord-user-12345", event.to_json())
        self.assertNotIn("guild-67890", event.to_json())
        self.assertNotIn("request-777", event.to_json())
        self.assertRegex(event.actor_ref, r"^actor_[a-f0-9]{24}$")

    def test_hmac_domains_and_keys_are_separate(self):
        self.assertNotEqual(self.emitter.pseudonym(7, "actor"), self.emitter.pseudonym(7, "guild"))
        other = EventEmitter(b"a-different-private-key-at-least-32-bytes")
        self.assertNotEqual(self.emitter.pseudonym(7, "actor"), other.pseudonym(7, "actor"))

    def test_fixed_synthetic_nonce_reproduces_event_ids(self):
        other = EventEmitter(KEY, instance_id="events-test")
        self.assertEqual(self.emit(), other.emit("command_submitted", timestamp=NOW))
        self.assertNotEqual(self.emit().event_id, self.emit().event_id)

    def test_real_instances_do_not_reuse_event_ids(self):
        first = EventEmitter(KEY, mode="sanitized-live", allow_live=True)
        second = EventEmitter(KEY, mode="sanitized-live", allow_live=True)
        self.assertNotEqual(first.emit("service_start").event_id, second.emit("service_start").event_id)

    def test_no_default_sink_or_environment_access(self):
        with patch("os.getenv", side_effect=AssertionError("must not read environment")):
            self.assertIsInstance(self.emit(), SecurityEvent)

    def test_sink_only_receives_validated_event(self):
        sink = []
        emitter = EventEmitter(KEY, sink=sink.append)
        with self.assertRaises(EventValidationError):
            emitter.emit("command_submitted", reason="TOKEN=secret")
        self.assertEqual(sink, [])

    def test_unknown_fields_and_sensitive_values_rejected(self):
        event = self.emit().to_dict()
        for field in ("token", "password", "message", "connection_string", "sql", "parameters", "ip_address"):
            with self.subTest(field=field), self.assertRaises(EventValidationError):
                SecurityEvent.from_dict({**event, field: "do-not-export"})
        for field in ("operation", "outcome", "reason", "family"):
            with self.subTest(field=field), self.assertRaises(EventValidationError) as caught:
                self.emit(**{field: "postgresql://user:secret@private/db"})
            self.assertNotIn("secret", str(caught.exception))

    def test_arbitrary_exception_message_is_never_read(self):
        class DangerousError(ValueError):
            def __str__(self):
                raise AssertionError("error message must not be read")
        result = classify_exception(DangerousError())
        self.assertEqual(result["family"], "python")
        self.assertNotIn("message", result)

    def test_key_requirements_and_redacted_repr(self):
        for key in (b"short", "string", None):
            with self.subTest(key=type(key)), self.assertRaises(EventValidationError):
                EventEmitter(key)
        self.assertNotIn(KEY.decode(), repr(self.emitter))
        self.assertIn("<redacted>", repr(self.emitter))

    def test_real_mode_requires_explicit_true(self):
        for approval in (False, 1, "yes", None):
            with self.subTest(approval=approval), self.assertRaises(EventValidationError):
                EventEmitter(KEY, mode="sanitized-live", allow_live=approval)
        with self.assertRaises(EventValidationError):
            EventEmitter(KEY, mode="sanitized-live", allow_live=True, instance_id="fixed")

    def test_safe_sqlstate_and_class(self):
        event = self.emit(sqlstate="42501")
        self.assertEqual((event.sqlstate, event.sqlstate_class), ("42501", "42"))
        for unrecognized in ("password", "99999", {"secret": "value"}, 42501):
            with self.subTest(value=type(unrecognized)):
                result = self.emit(sqlstate=unrecognized)
                self.assertIsNone(result.sqlstate)
                self.assertIsNone(result.sqlstate_class)

    def test_sqlstate_class_cannot_be_spoofed(self):
        data = self.emit(sqlstate="42501").to_dict()
        with self.assertRaises(EventValidationError):
            SecurityEvent.from_dict({**data, "sqlstate_class": "28"})

    def test_safe_exception_categories(self):
        examples = [(TimeoutError("secret"), "timeout"), (ConnectionError("secret"), "connection"),
                    (UnicodeError("secret"), "driver"), (KeyError("secret"), "python"),
                    (Exception("secret"), "unknown")]
        for error, expected in examples:
            with self.subTest(expected=expected):
                result = classify_exception(error)
                self.assertEqual(result["family"], expected)
                self.assertNotIn("secret", json.dumps(result))
        class ServerError(Exception):
            sqlstate = "28P01"
        self.assertEqual(classify_exception(ServerError("secret"))["sqlstate"], "28P01")

    def test_bad_timestamps_and_nonfinite_metrics_rejected(self):
        with self.assertRaises(EventValidationError):
            self.emitter.emit("command_completed", timestamp=datetime(2026, 1, 1))
        for duration in (-1, True, float("nan"), float("inf"), 3_600_001, "10"):
            with self.subTest(duration=duration), self.assertRaises(EventValidationError):
                self.emit(duration_ms=duration)

    def test_spoofed_severity_source_and_raw_refs_rejected(self):
        original = self.emit().to_dict()
        for field, value in (("source", "discord"), ("severity", "error"), ("actor_ref", "12345"), ("mode", "real")):
            with self.subTest(field=field), self.assertRaises(EventValidationError):
                SecurityEvent.from_dict({**original, field: value})

    def test_identifier_objects_are_not_formatted(self):
        class Record:
            def __str__(self):
                raise AssertionError("must not stringify arbitrary objects")
        with self.assertRaises(EventValidationError):
            self.emit(actor=Record())

    def test_schema_artifact_matches_runtime_contract(self):
        artifact = Path(__file__).parents[1] / "event.schema.json"
        self.assertEqual(json.loads(artifact.read_text(encoding="utf-8")), export_schema())
        definition = export_schema()["$defs"]["event"]
        self.assertFalse(definition["additionalProperties"])
        self.assertEqual(set(definition["required"]), set(SecurityEvent.__dataclass_fields__))
