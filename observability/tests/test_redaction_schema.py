"""Schema/runtime agreement, secret-redaction fuzzing and detection boundaries.

Offline only: a tiny JSON Schema subset validator (no third-party packages),
temporary directories, and synthetic keys/identifiers.
"""

from contextlib import redirect_stdout, redirect_stderr
import copy
import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import pickle
import re
import tempfile
import unittest

from observability import Detector, EventEmitter, EventValidationError, HealthMonitor
from observability.__main__ import main
from observability.demo import START, synthetic_events
from observability.detections import RULES
from observability.events import (
    KINDS, OPERATIONS, OUTCOMES, REASONS, FAMILIES, SQLSTATES, SERVICES, MODES, SecurityEvent, classify_exception,
)
from observability.exports import envelope, read_events, validate_envelope, write_bundle
from observability.schema import export_schema


PACKAGE = Path(__file__).parents[1]
KEY = b"synthetic-redaction-test-key-0123456789"
REJECTED = "OFFLINE_OPERATION_REJECTED: invalid input, provenance, configuration or output destination.\n"
SECRETS = [
    "hunter2-P@ssw0rd!", "MTA1NjM0NTY3ODkwMTIzNDU2Nw.GhIjKl.abcdefghijklmnopqrstuvwxyz0123456789AB",
    "postgresql://dao_user:sup3r-s3cret@db.internal:5432/dao", "postgres://u:p4ss@10.0.0.5/db",
    "192.168.1.77", "10.13.37.2:5433", "SELECT * FROM players WHERE user_id = 812345678901234567",
    "DROP TABLE living_dao_actions;--", "812345678901234567", "cultivator@example.invalid",
    "DISCORD_TOKEN=abc.def.ghi", "=HYPERLINK(\"http://x\")", "+cmd|' /C calc'!A0", "-2+3", "@SUM(1)",
]
FORMULA = ("=", "+", "-", "@", "\t", "\r")
MESSAGES = set()
for name in ("events.py", "exports.py", "health.py", "detections.py"):
    MESSAGES |= set(re.findall(r'EventValidationError\("([^"]+)"\)', (PACKAGE / name).read_text(encoding="utf-8")))


def _is_type(value, name):
    return {"string": isinstance(value, str), "null": value is None, "object": isinstance(value, dict),
            "array": isinstance(value, list), "boolean": isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool)}[name]


def _equal(a, b):
    # JSON equality: booleans are not numbers.
    return type(a) is type(b) and a == b if isinstance(a, bool) or isinstance(b, bool) else a == b


def schema_valid(value, schema, root):
    """Subset of draft 2020-12 used by event.schema.json; format asserted."""
    if "$ref" in schema:
        return schema_valid(value, root["$defs"][schema["$ref"].rsplit("/", 1)[1]], root)
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        if not any(_is_type(value, name) for name in types):
            return False
    if "const" in schema and not _equal(value, schema["const"]):
        return False
    if "enum" in schema and not any(_equal(value, item) for item in schema["enum"]):
        return False
    if isinstance(value, str):
        # ECMA-262 "$" never matches before a trailing newline; Python "$" does.
        if "pattern" in schema and re.search(schema["pattern"][:-1] + r"\Z" if schema["pattern"].endswith("$") else schema["pattern"], value, re.ASCII) is None:
            return False
        if schema.get("format") == "date-time":
            try:
                datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return False
    if _is_type(value, "number"):
        if "minimum" in schema and not value >= schema["minimum"]:
            return False
        if "maximum" in schema and not value <= schema["maximum"]:
            return False
    if isinstance(value, dict):
        if any(key not in value for key in schema.get("required", [])):
            return False
        properties = schema.get("properties", {})
        if schema.get("additionalProperties") is False and set(value) - set(properties):
            return False
        if any(key in value and not schema_valid(value[key], sub, root) for key, sub in properties.items()):
            return False
    if isinstance(value, list):
        if len(value) > schema.get("maxItems", len(value)):
            return False
        if "items" in schema and not all(schema_valid(item, schema["items"], root) for item in value):
            return False
    for condition in schema.get("allOf", []):
        if "if" in condition:
            if schema_valid(value, condition["if"], root) and not schema_valid(value, condition["then"], root):
                return False
        elif not schema_valid(value, condition, root):
            return False
    return True


def runtime_valid(document):
    try:
        SecurityEvent.from_dict(document)
        return True
    except EventValidationError:
        return False


class SchemaAgreementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = json.loads((PACKAGE / "event.schema.json").read_text(encoding="utf-8"))
        cls.event_schema = cls.root["$defs"]["event"]
        emitter = EventEmitter(KEY, instance_id="schema-agreement")
        full = emitter.emit("database_failure", actor=1, guild=2, request=3, operation="connect", outcome="failure",
                            reason="timeout", family="timeout", sqlstate="57014", duration_ms=12.5,
                            service="postgresql", timestamp=START)
        cls.bases = [json.loads(full.to_json()), json.loads(emitter.emit("service_start", timestamp=START).to_json())]

    def candidates(self):
        enums = sorted(KINDS) + sorted(OPERATIONS) + sorted(OUTCOMES) + sorted(REASONS) + sorted(FAMILIES)
        enums += sorted(SQLSTATES) + sorted({s[:2] for s in SQLSTATES}) + sorted(SERVICES) + sorted(MODES)
        enums += ["info", "warning", "error", "1.0", "dao-observability", "2.0", "KIND", "hunt ", " hunt"]
        hexes = ["actor_" + "a" * 24, "guild_" + "b" * 24, "request_" + "c" * 24, "actor_" + "A" * 24,
                 "actor_" + "a" * 23, "actor_" + "a" * 25, "actor_" + "a" * 24 + "\n", "actor_" + "١" * 24,
                 "a" * 32, "A" * 32, "a" * 31, "a" * 33, "a" * 32 + "\n", "g" * 32]
        stamps = ["2026-01-15T12:00:00.000Z", "2026-13-01T00:00:00.000Z", "2026-00-01T00:00:00.000Z",
                  "2026-02-30T00:00:00.000Z", "2024-02-29T23:59:59.999Z", "2026-01-32T00:00:00.000Z",
                  "2026-01-15T24:00:00.000Z", "2026-01-15T12:60:00.000Z", "2026-01-15T12:00:60.000Z",
                  "2026-01-15T12:00:00Z", "2026-01-15T12:00:00.000z", "2026-01-15T12:00:00.000+00:00",
                  "2026-01-15 12:00:00.000Z", "٢٠٢٦-01-15T12:00:00.000Z",
                  "2026-01-15T12:00:00.000Z\n", "0001-01-01T00:00:00.000Z", "9999-12-31T23:59:59.999Z"]
        numbers = [0, -0.0, 0.0, 1, 1.5, -1, -0.001, 3_600_000, 3_600_000.0, 3_600_000.001, 10**400, 1e308]
        return [None, True, False, "", [], {}, ["hunt"], {"x": 1}] + enums + hexes + stamps + numbers + SECRETS

    def test_committed_artifact_equals_builder(self):
        self.assertEqual(json.loads((PACKAGE / "event.schema.json").read_text(encoding="utf-8")), export_schema())
        self.assertEqual(self.event_schema["required"], sorted(SecurityEvent.__dataclass_fields__))
        self.assertEqual(set(self.event_schema["properties"]), set(SecurityEvent.__dataclass_fields__))
        self.assertIs(self.event_schema["additionalProperties"], False)
        self.assertIs(self.root["additionalProperties"], False)

    def test_enums_bounds_and_patterns_mirror_runtime_allowlists(self):
        p = self.event_schema["properties"]
        for field, values in (("operation", OPERATIONS), ("outcome", OUTCOMES), ("reason", REASONS),
                              ("family", FAMILIES), ("sqlstate", SQLSTATES)):
            self.assertEqual(p[field]["enum"], [None, *sorted(values)])
        self.assertEqual(p["kind"]["enum"], sorted(KINDS))
        self.assertEqual(p["mode"]["enum"], sorted(MODES))
        self.assertEqual(p["service"]["enum"], sorted(SERVICES))
        self.assertEqual((p["duration_ms"]["minimum"], p["duration_ms"]["maximum"]), (0, 3_600_000))
        for definition in p.values():
            if "pattern" in definition:
                self.assertTrue(definition["pattern"].startswith("^") and definition["pattern"].endswith("$"))
                self.assertNotIn("\\d", definition["pattern"])

    def test_valid_events_pass_both(self):
        for event in synthetic_events():
            document = json.loads(event.to_json())
            self.assertTrue(schema_valid(document, self.event_schema, self.root))
            self.assertTrue(runtime_valid(document))

    def test_single_field_mutations_agree(self):
        checked = 0
        for base in self.bases:
            for field in sorted(base):
                for value in self.candidates():
                    document = {**base, field: value}
                    with self.subTest(field=field, value=repr(value)[:60]):
                        self.assertEqual(schema_valid(document, self.event_schema, self.root), runtime_valid(document))
                    checked += 1
                for document in ({k: v for k, v in base.items() if k != field}, {**base, field + "_extra": None}):
                    self.assertFalse(schema_valid(document, self.event_schema, self.root))
                    self.assertFalse(runtime_valid(document))
        self.assertGreater(checked, 3000)

    def test_cross_field_rules_agree(self):
        base = self.bases[0]
        for kind in KINDS:
            for severity in ("info", "warning", "error"):
                document = {**base, "kind": kind, "severity": severity}
                self.assertEqual(schema_valid(document, self.event_schema, self.root), runtime_valid(document))
        for state in [None, *sorted(SQLSTATES)[:8]]:
            for state_class in [None, "08", "42", "57", "XX"]:
                document = {**base, "sqlstate": state, "sqlstate_class": state_class}
                self.assertEqual(schema_valid(document, self.event_schema, self.root), runtime_valid(document))

    def test_envelope_schema_and_validator_agree(self):
        document = json.loads(json.dumps(envelope(synthetic_events())))
        self.assertTrue(schema_valid(document, self.root, self.root))
        for changed in ({**document, "mode": "sanitized-live"}, {**document, "mode": ["synthetic"]},
                        {**document, "extra": 1}, {**document, "events": {}}, {**document, "schema_version": 1.0}):
            self.assertFalse(schema_valid(changed, self.root, self.root))
            with self.assertRaises(EventValidationError):
                validate_envelope(changed)

    def test_huge_numbers_rejected_with_validation_error_not_overflow(self):
        for value in (10**400, -(10**400)):
            with self.assertRaises(EventValidationError):
                SecurityEvent.from_dict({**self.bases[0], "duration_ms": value})

    def test_negative_zero_duration_is_normalized(self):
        event = SecurityEvent.from_dict({**self.bases[0], "duration_ms": -0.0})
        self.assertIn('"duration_ms":0.0,', event.to_json())


class RedactionTests(unittest.TestCase):
    def setUp(self):
        self.emitter = EventEmitter(KEY, instance_id="redaction-test")

    def assertClean(self, text):
        for secret in SECRETS + [KEY.decode(), KEY.hex()]:
            self.assertNotIn(secret, text)

    def test_classify_exception_never_reads_or_returns_messages(self):
        class Opaque(Exception):
            def __str__(self):
                raise AssertionError("message read")
            __repr__ = __str__
        for secret in SECRETS:
            for error_type in (Exception, ValueError, TimeoutError, ConnectionError, OSError, RuntimeError, KeyError, UnicodeError):
                error = error_type(secret)
                error.sqlstate = secret
                result = classify_exception(error)
                self.assertEqual(set(result), {"family", "reason", "sqlstate"})
                self.assertIn(result["family"], FAMILIES)
                self.assertIn(result["reason"], REASONS)
                self.assertIsNone(result["sqlstate"])
                self.assertClean(json.dumps(result))
        for error_type in (Opaque, type("Wrapped", (Opaque, ValueError), {})):
            error = error_type()
            error.sqlstate = "28P01"
            self.assertEqual(classify_exception(error)["sqlstate"], "28P01")

    def test_emit_rejects_secrets_in_every_field_with_constant_messages(self):
        fields = ("operation", "outcome", "reason", "family", "service", "duration_ms", "timestamp")
        sink = []
        emitter = EventEmitter(KEY, instance_id="sink-test", sink=sink.append)
        for secret in SECRETS:
            for field in fields:
                with self.assertRaises(EventValidationError) as caught:
                    emitter.emit("command_completed", **{field: secret})
                self.assertIn(str(caught.exception), MESSAGES)
                self.assertClean(str(caught.exception) + repr(caught.exception.args))
            with self.assertRaises(EventValidationError) as caught:
                emitter.emit(secret)
            self.assertIn(str(caught.exception), MESSAGES)
            for unhashable in ([secret], {secret: 1}):
                with self.assertRaises(EventValidationError):
                    emitter.emit(unhashable)
            event = emitter.emit("command_completed", sqlstate=secret, actor=secret, guild=secret, request=secret)
            self.assertIsNone(event.sqlstate)
            self.assertClean(event.to_json() + repr(event) + str(event))
        self.assertEqual(len(sink), len(SECRETS))

    def test_raw_identifiers_never_appear(self):
        for raw in (812345678901234567, "812345678901234567", "cultivator@example.invalid", "Player Name#0001"):
            event = self.emitter.emit("command_submitted", actor=raw, guild=raw, request=raw)
            text = event.to_json() + repr(event)
            self.assertNotIn(str(raw), text)
            self.assertRegex(event.actor_ref, r"^actor_[0-9a-f]{24}$")
            self.assertRegex(event.guild_ref, r"^guild_[0-9a-f]{24}$")
            self.assertNotEqual(event.actor_ref[6:], event.guild_ref[6:])
            self.assertNotEqual(event.actor_ref[6:], event.request_ref[8:])

    def test_identifier_bounds_raise_constant_validation_errors(self):
        for value in ("", "x" * 257, 10**256, -(10**255), 10**5000, 1.5, b"bytes", True, None.__class__):
            with self.assertRaises(EventValidationError) as caught:
                self.emitter.pseudonym(value, "actor")
            self.assertIn(str(caught.exception), MESSAGES)
        self.assertTrue(self.emitter.pseudonym("x" * 256, "actor"))
        self.assertTrue(self.emitter.pseudonym(10**255, "actor"))
        with self.assertRaises(EventValidationError):
            self.emitter.pseudonym("x", "secret")

    def test_key_never_serialized_or_reported(self):
        for key in (b"k" * 31, b"", bytearray(b"k" * 40), memoryview(b"k" * 40), "k" * 40, None):
            with self.assertRaises(EventValidationError) as caught:
                EventEmitter(key)
            self.assertIn(str(caught.exception), MESSAGES)
            self.assertNotIn("kkkk", str(caught.exception))
        self.assertIsInstance(EventEmitter(b"k" * 32), EventEmitter)
        self.assertClean(repr(self.emitter) + str(self.emitter) + format(self.emitter))
        for serialize in (pickle.dumps, copy.copy, copy.deepcopy):
            with self.assertRaises(TypeError) as caught:
                serialize(self.emitter)
            self.assertClean(str(caught.exception))
        event = self.emitter.emit("service_start")
        self.assertClean(event.to_json() + repr(event) + pickle.dumps(event).hex())

    def test_exports_have_no_secrets_or_formula_cells(self):
        events = [self.emitter.emit("command_completed", actor=s, guild=s, request=s, operation="qi", outcome="success",
                                    duration_ms=d, timestamp=START + timedelta(seconds=i))
                  for i, (s, d) in enumerate(zip(SECRETS, [-0.0, 0, 0.0, 1e-9, 3_600_000] * 3))]
        events += [self.emitter.emit("database_failure", service="postgresql", sqlstate=s, timestamp=START + timedelta(seconds=30)) for s in SECRETS[:1]]
        monitor = HealthMonitor()
        for event in events:
            monitor.observe(event)
        health = monitor.report(now=START + timedelta(seconds=40))
        with tempfile.TemporaryDirectory() as directory:
            output = write_bundle(Path(directory) / "case", events, Detector().analyze(events), health)
            for path in output.iterdir():
                self.assertClean(path.read_text(encoding="utf-8"))
            with (output / "events.csv").open(encoding="utf-8", newline="") as stream:
                for row in csv.reader(stream):
                    for cell in row:
                        self.assertFalse(cell.startswith(FORMULA), cell)

    def test_output_destination_is_never_reused_or_created_recursively(self):
        events = synthetic_events()
        monitor = HealthMonitor()
        for event in events:
            monitor.observe(event)
        health = monitor.report(now=START + timedelta(seconds=159))
        alerts = Detector().analyze(events)
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory) / "inner"
            base.mkdir()
            (base / "existing.json").write_text("{}", encoding="utf-8")
            for target in (base, base / "..", base / "existing.json", base / "missing" / "case", Path(directory)):
                with self.subTest(target=target.name), self.assertRaises(OSError):
                    write_bundle(target, events, alerts, health)
            self.assertEqual(sorted(p.name for p in Path(directory).rglob("*")), ["existing.json", "inner"])
            self.assertEqual((base / "existing.json").read_text(encoding="utf-8"), "{}")


class CLIRedactionTests(unittest.TestCase):
    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            status = main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_rejected_inputs_print_only_constant_message(self):
        base = json.loads(json.dumps(envelope(synthetic_events())))
        payloads = []
        for secret in SECRETS:
            event = dict(base["events"][0])
            payloads += [{**base, "events": [{**event, "reason": secret}]}, {**base, secret: 1},
                         {**base, "events": [{**event, "actor_ref": secret}]}, {**base, "mode": secret}]
        raw = ['{"events":[{"duration_ms":1' + "0" * 400 + "}]}", '{"x":' + "9" * 5000 + "}", "[" * 100000, "﻿{}", '{"a":NaN}']
        with tempfile.TemporaryDirectory() as directory:
            for index, payload in enumerate(payloads + raw):
                target = Path(directory) / f"postgresql-user-pass-{index}.json"
                target.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
                output = Path(directory) / f"out-{index}"
                status, out, err = self.invoke("analyze", "--input", str(target), "--output", str(output))
                self.assertEqual((status, out, err), (2, "", REJECTED))
                self.assertFalse(output.exists())

    def test_config_and_credential_paths_are_rejected_unread(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in (".env", ".env.test", "credentials.txt", "token"):
                target = Path(directory) / name
                # validate_offline.py denies opening any .env path, so those names stay uncreated.
                if not name.startswith(".env"):
                    target.write_text("DISCORD_TOKEN=" + SECRETS[1], encoding="utf-8")
                for args in (("check", "--config", str(target)), ("analyze", "--input", str(target), "--output", str(Path(directory) / "o"))):
                    self.assertEqual(self.invoke(*args), (2, "", REJECTED))
            config = Path(directory) / "lab.json"
            config.write_text(json.dumps({"schema_version": "1.0", "mode": "synthetic", "network_probes_enabled": False,
                                          "max_age_seconds": 120, "targets": {"application": {"transport": "passive", "host": SECRETS[2], "port": None}}}), encoding="utf-8")
            self.assertEqual(self.invoke("check", "--config", str(config)), (2, "", REJECTED))

    def test_output_traversal_to_existing_directory_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            inner = Path(directory) / "inner"
            inner.mkdir()
            for target in (str(inner / ".."), str(inner), str(inner / "a" / "b")):
                self.assertEqual(self.invoke("demo", "--output", target), (2, "", REJECTED))
            self.assertEqual([p.name for p in Path(directory).rglob("*")], ["inner"])

    def test_success_output_is_counts_only(self):
        with tempfile.TemporaryDirectory() as directory:
            status, out, err = self.invoke("demo", "--output", str(Path(directory) / "secret-case-name"))
            self.assertEqual((status, err), (0, ""))
            self.assertEqual(out, "OFFLINE_EXPORT_WRITTEN: 45 validated events; 5 alerts; no live probes.\n")


class DetectionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.emitter = EventEmitter(KEY, instance_id="boundary-test")

    def fields(self, rule_id):
        rule = RULES[rule_id]
        kind = sorted(rule["kinds"])[0]
        fields = {"actor": "player", "guild": "guild"} if rule["group"] != "service" else {}
        if rule["group"] == "operation":
            fields["operation"] = "craft"
        if rule_id == "SERVICE_INTERRUPTION":
            fields["reason"] = "operator_shutdown"
        if rule_id == "DATABASE_OUTAGE":
            fields.update(service="postgresql", actor="unrelated-player")
        return kind, fields

    def burst(self, rule_id, offsets):
        kind, fields = self.fields(rule_id)
        return [self.emitter.emit(kind, timestamp=START + timedelta(seconds=offset), **fields) for offset in offsets]

    def test_readme_table_matches_rules(self):
        text = (PACKAGE / "README.md").read_text(encoding="utf-8")
        rows = dict(re.findall(r"^\| ([A-Z_]+) \| (.+) \|$", text, re.M))
        self.assertEqual(set(rows) - {"ID"}, set(RULES))
        for rule_id, rule in RULES.items():
            threshold, window, group, severity = [cell.strip() for cell in rows[rule_id].split("|")]
            self.assertIn(str(rule["threshold"]), threshold.split())
            self.assertEqual(window, f"{rule['seconds']} seconds")
            self.assertEqual(severity, rule["severity"])
            expected = {"actor": "mode/guild/actor/service", "operation": "mode/guild/actor/service/operation", "service": "mode/service"}
            self.assertEqual(group, expected[rule["group"]])

    def test_threshold_and_inclusive_lower_bound_for_every_rule(self):
        for rule_id, rule in RULES.items():
            n, w = rule["threshold"], rule["seconds"]
            middle = [0.001 * (index + 1) for index in range(n - 2)]
            with self.subTest(rule=rule_id):
                exact = Detector().analyze(self.burst(rule_id, [0, *middle, w]))
                self.assertEqual([a["rule_id"] for a in exact], [rule_id])
                self.assertEqual(exact[0]["count"], n)
                self.assertEqual(Detector().analyze(self.burst(rule_id, [0, *middle, w + 0.001])), [])
                self.assertEqual(Detector().analyze(self.burst(rule_id, [0.001 * i for i in range(n - 1)])), [])

    def test_one_alert_per_group_per_window_and_duplicates_ignored(self):
        for rule_id, rule in RULES.items():
            n, w = rule["threshold"], rule["seconds"]
            with self.subTest(rule=rule_id):
                first = self.burst(rule_id, [0] * (3 * n))
                self.assertEqual(len(Detector().analyze(first + first)), 1)
                self.assertEqual(len(Detector().analyze(first + self.burst(rule_id, [w] * n))), 1)
                self.assertEqual(len(Detector().analyze(first + self.burst(rule_id, [w + 0.001] * n))), 2)

    def test_service_alerts_do_not_attribute_actors(self):
        for rule_id in ("DATABASE_OUTAGE", "SERVICE_INTERRUPTION"):
            kind, fields = self.fields(rule_id)
            events = [self.emitter.emit(kind, actor="someone", timestamp=START, **{k: v for k, v in fields.items() if k != "actor"}) for _ in range(3)]
            alert = Detector().analyze(events)[0]
            self.assertIsNone(alert["actor_ref"])


if __name__ == "__main__":
    unittest.main()
