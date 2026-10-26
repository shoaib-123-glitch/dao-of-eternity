from copy import deepcopy
from datetime import timedelta
from pathlib import Path
import json
import unittest

from observability import EventEmitter, EventValidationError, HealthMonitor
from observability.demo import DEMO_KEY, START
from observability.health import validate_lab_config


class HealthTests(unittest.TestCase):
    def setUp(self):
        self.emitter = EventEmitter(DEMO_KEY, instance_id="health-test")
        self.monitor = HealthMonitor()

    def emit(self, kind, **fields):
        event = self.emitter.emit(kind, timestamp=START, **fields)
        self.monitor.observe(event)
        return event

    def test_missing_services_are_unknown(self):
        report = self.monitor.report(now=START)
        self.assertEqual({entry["status"] for entry in report["services"].values()}, {"unknown"})
        self.assertIsNone(report["metrics"]["error_rate"])

    def test_start_is_not_proof_of_health(self):
        self.emit("service_start", outcome="accepted")
        self.assertEqual(self.monitor.report(now=START)["services"]["application"]["status"], "unknown")

    def test_passive_health_expires(self):
        self.emit("health_observation", service="postgresql", outcome="healthy")
        self.assertEqual(self.monitor.report(now=START)["services"]["postgresql"]["status"], "healthy")
        self.assertEqual(self.monitor.report(now=START + timedelta(seconds=121))["services"]["postgresql"]["status"], "unknown")

    def test_outage_then_recovery(self):
        self.emit("database_failure", service="postgresql", reason="connection_lost", outcome="failure")
        self.assertEqual(self.monitor.report(now=START)["services"]["postgresql"]["status"], "unhealthy")
        self.monitor.observe(self.emitter.emit("health_observation", service="postgresql", outcome="healthy", timestamp=START + timedelta(seconds=1)))
        self.assertEqual(self.monitor.report(now=START + timedelta(seconds=2))["services"]["postgresql"]["status"], "healthy")

    def test_future_observation_does_not_prove_present_health(self):
        self.monitor.observe(self.emitter.emit("health_observation", outcome="healthy", timestamp=START + timedelta(seconds=1)))
        self.assertEqual(self.monitor.report(now=START)["services"]["application"]["status"], "unknown")

    def test_metrics_are_deduplicated_and_numerically_correct(self):
        first = self.emit("command_completed", outcome="success", duration_ms=10)
        self.monitor.observe(first)
        self.emit("command_completed", outcome="failure", duration_ms=30)
        report = self.monitor.report(now=START)
        self.assertEqual(report["metrics"]["completed_requests"], 2)
        self.assertEqual(report["metrics"]["error_rate"], 0.5)
        self.assertEqual(report["metrics"]["mean_latency_ms"], 20)
        self.assertEqual(report["metrics"]["p95_latency_ms"], 30)

    def test_stale_metrics_leave_reporting_window(self):
        self.emit("command_completed", outcome="failure", duration_ms=10)
        self.assertEqual(self.monitor.report(now=START + timedelta(seconds=121))["metrics"]["completed_requests"], 0)

    def test_config_is_offline_numeric_loopback_only(self):
        path = Path(__file__).parents[1] / "lab_config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        self.assertIs(validate_lab_config(config), config)
        for host in ("0.0.0.0", "localhost", "database.internal", "192.168.1.1"):
            changed = deepcopy(config)
            changed["targets"]["postgresql"]["host"] = host
            with self.subTest(host=host), self.assertRaises(EventValidationError):
                validate_lab_config(changed)

    def test_config_cannot_enable_probes_or_embed_credentials(self):
        path = Path(__file__).parents[1] / "lab_config.json"
        config = json.loads(path.read_text(encoding="utf-8"))
        for change in ({"network_probes_enabled": True}, {"password": "secret"}, {"mode": "live"}):
            with self.subTest(change=list(change)), self.assertRaises(EventValidationError):
                validate_lab_config({**config, **change})

    def test_invalid_metrics_windows_rejected(self):
        for age in (False, 0, -1, 3601, "120"):
            with self.subTest(age=age), self.assertRaises(EventValidationError):
                HealthMonitor(max_age_seconds=age)
