from contextlib import redirect_stdout, redirect_stderr
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from observability import Detector, EventEmitter, EventValidationError, HealthMonitor
from observability.__main__ import main
from observability.demo import synthetic_events, DEMO_KEY, START
from observability.events import SecurityEvent
from observability.exports import envelope, read_events, validate_envelope, write_bundle
from observability.lab10 import lab10_document


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.events = synthetic_events()
        self.alerts = Detector().analyze(self.events)
        monitor = HealthMonitor()
        for event in self.events:
            monitor.observe(event)
        self.health = monitor.report(now=START + timedelta(seconds=159))

    def test_envelope_round_trip_and_no_raw_synthetic_ids(self):
        document = envelope(self.events)
        self.assertEqual(validate_envelope(document), self.events)
        serialized = json.dumps(document)
        for raw in ("demo-observer-a", "demo-observer-b", "demo-guild", "demo-transaction-craft-1"):
            self.assertNotIn(raw, serialized)

    def test_live_collection_and_export_are_independent_gates(self):
        emitter = EventEmitter(DEMO_KEY, mode="sanitized-live", allow_live=True)
        events = [emitter.emit("service_start", timestamp=START)]
        with self.assertRaises(EventValidationError):
            envelope(events)
        self.assertEqual(envelope(events, allow_sanitized_live_export=True)["mode"], "sanitized-live")
        with self.assertRaises(EventValidationError):
            envelope(events + self.events, allow_sanitized_live_export=True)

    def test_envelope_extra_fields_and_wrong_provenance_rejected(self):
        document = envelope(self.events)
        for changed in ({**document, "token": "secret"}, {**document, "mode": "sanitized-live"}, {**document, "schema_version": "2.0"}):
            with self.subTest(keys=list(changed)), self.assertRaises(EventValidationError):
                validate_envelope(changed)

    def test_bundle_is_exclusive_and_round_trip_csv_has_strict_header(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "new-case"
            write_bundle(output, self.events, self.alerts, self.health)
            snapshot = (output / "events.json").read_bytes()
            self.assertEqual(read_events(output / "events.json"), self.events)
            self.assertEqual(set((output / "events.csv").read_text().splitlines()[0].split(",")), set(SecurityEvent.__dataclass_fields__))
            with self.assertRaises(FileExistsError):
                write_bundle(output, self.events, self.alerts, self.health)
            self.assertEqual((output / "events.json").read_bytes(), snapshot)
            self.assertTrue((output / "lab10_alerts.json").exists())

    def test_report_injection_is_rejected_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "must-not-exist"
            with self.assertRaises(EventValidationError):
                write_bundle(output, self.events, [{"description": "secret"}], self.health)
            self.assertFalse(output.exists())
            with self.assertRaises(EventValidationError):
                write_bundle(output, self.events, self.alerts, {**self.health, "connection_string": "secret"})
            self.assertFalse(output.exists())

    def test_bounded_json_and_duplicate_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "events.json"
            target.write_text('{"mode":"synthetic","mode":"sanitized-live"}', encoding="utf-8")
            with self.assertRaises(EventValidationError):
                read_events(target)
            target.write_text(" " * 4_000_001, encoding="utf-8")
            with self.assertRaises(EventValidationError):
                read_events(target)

    def test_non_json_inputs_are_rejected_before_opening(self):
        for path in (".env", ".env.test", "credentials.txt"):
            with self.subTest(path=path), patch("pathlib.Path.open", side_effect=AssertionError("must not open")), self.assertRaises(EventValidationError):
                read_events(path)

    def test_live_input_requires_separate_explicit_opt_in(self):
        emitter = EventEmitter(DEMO_KEY, mode="sanitized-live", allow_live=True)
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "events.json"
            target.write_text(json.dumps(envelope([emitter.emit("service_start", timestamp=START)], allow_sanitized_live_export=True)), encoding="utf-8")
            with self.assertRaises(EventValidationError):
                read_events(target)
            self.assertEqual(read_events(target, allow_sanitized_live_input=True)[0].mode, "sanitized-live")

    def test_lab10_adapter_contract_and_evidence(self):
        result = lab10_document(self.events)
        self.assertEqual(set(result), {"lab", "data_source", "alerts"})
        self.assertIn("SYNTHETIC", result["data_source"])
        self.assertEqual(len(result["alerts"]), 5)
        self.assertEqual(len({item["alert_id"] for item in result["alerts"]}), 5)
        for alert in result["alerts"]:
            self.assertEqual(set(alert), {"alert_id", "rule", "severity", "player", "events", "first_seen_utc", "last_seen_utc", "description", "recommended_triage", "evidence"})
            self.assertIn(alert["severity"], {"HIGH", "MEDIUM", "LOW"})
            self.assertEqual(len(alert["evidence"]), alert["events"])
            for evidence in alert["evidence"]:
                self.assertEqual(set(evidence), {"timestamp", "event_type", "outcome", "request_id", "player"})
        self.assertEqual(result, lab10_document(list(reversed(self.events))))

    def test_lab10_rejects_real_events_even_when_other_exports_authorized(self):
        emitter = EventEmitter(DEMO_KEY, mode="sanitized-live", allow_live=True)
        with self.assertRaises(EventValidationError):
            lab10_document([emitter.emit("service_start", timestamp=START)])

    def test_partial_output_is_not_cleaned_up_or_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "partial-case"
            with patch("observability.exports._exclusive_json", side_effect=OSError("synthetic write interruption")), self.assertRaises(OSError):
                write_bundle(output, self.events, self.alerts, self.health)
            self.assertTrue(output.exists())
            with self.assertRaises(FileExistsError):
                write_bundle(output, self.events, self.alerts, self.health)


class CLITests(unittest.TestCase):
    def invoke(self, *arguments):
        stdout, stderr = io.StringIO(), io.StringIO()
        # Fail closed if anyone adds a live probe or subprocess in these paths.
        with patch("socket.socket.connect", side_effect=AssertionError("no network")), patch("socket.create_connection", side_effect=AssertionError("no network")), patch("subprocess.run", side_effect=AssertionError("no subprocess")), redirect_stdout(stdout), redirect_stderr(stderr):
            result = main(arguments)
        return result, stdout.getvalue(), stderr.getvalue()

    def test_check_has_no_probe_and_reports_only_config_validity(self):
        status, output, _ = self.invoke("check")
        self.assertEqual(status, 0)
        self.assertIn("no service availability was tested", output)

    def test_demo_and_analysis_offline_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            demo = Path(directory) / "demo"
            analysis = Path(directory) / "analysis"
            status, output, _ = self.invoke("demo", "--output", str(demo))
            self.assertEqual(status, 0)
            self.assertIn("5 alerts", output)
            status, _, _ = self.invoke("analyze", "--input", str(demo / "events.json"), "--output", str(analysis))
            self.assertEqual(status, 0)
            self.assertEqual((demo / "events.json").read_bytes(), (analysis / "events.json").read_bytes())

    def test_errors_never_print_paths_credentials_or_payloads(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "credential-like-secret-name.json"
            target.write_text('{"password":"do-not-print","token":"another-secret"}', encoding="utf-8")
            status, output, errors = self.invoke("analyze", "--input", str(target), "--output", str(Path(directory) / "output"))
            self.assertEqual(status, 2)
            for secret in ("credential-like-secret-name", "do-not-print", "another-secret", str(directory)):
                self.assertNotIn(secret, output + errors)

    def test_check_rejects_live_probe_config(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "lab.json"
            target.write_text('{"network_probes_enabled":true}', encoding="utf-8")
            status, _, _ = self.invoke("check", "--config", str(target))
            self.assertEqual(status, 2)

    def test_existing_output_rejected_without_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.invoke("demo", "--output", directory)[0], 2)


if __name__ == "__main__":
    unittest.main()
