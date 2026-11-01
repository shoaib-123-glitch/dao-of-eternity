from datetime import timedelta
from dataclasses import replace
import unittest

from observability import Detector, EventEmitter, EventValidationError
from observability.demo import synthetic_events, START, DEMO_KEY


class DetectionTests(unittest.TestCase):
    def setUp(self):
        self.emitter = EventEmitter(DEMO_KEY, instance_id="detection-test")

    def events(self, kind, count, *, spacing=1, actor="test-player", **fields):
        return [self.emitter.emit(kind, actor=actor, timestamp=START + timedelta(seconds=index * spacing), **fields) for index in range(count)]

    def test_demo_has_expected_five_incident_signals(self):
        alerts = Detector().analyze(synthetic_events())
        self.assertEqual({alert["rule_id"] for alert in alerts}, {"AUTHZ_BURST", "REPLAY_BURST", "COMMAND_RATE", "DATABASE_OUTAGE", "SERVICE_INTERRUPTION"})
        self.assertEqual(len(alerts), 5)

    def test_threshold_and_missing_identity_do_not_false_alert(self):
        self.assertEqual(Detector().analyze(self.events("authz_failure", 4)), [])
        self.assertEqual(Detector().analyze(self.events("authz_failure", 10, actor=None)), [])

    def test_authentication_burst(self):
        alerts = Detector().analyze(self.events("authn_failure", 5))
        self.assertEqual(alerts[0]["rule_id"], "AUTHN_BURST")

    def test_authorization_window_boundary_is_inclusive(self):
        within = self.events("authz_failure", 5, spacing=15)
        self.assertEqual(len(Detector().analyze(within)), 1)
        outside = self.events("authz_failure", 5, spacing=15.001)
        self.assertEqual(Detector().analyze(outside), [])

    def test_duplicate_log_deliveries_do_not_inflate_threshold(self):
        event = self.events("transaction_replay", 1, operation="craft")[0]
        self.assertEqual(Detector().analyze([event] * 10), [])

    def test_conflicting_identifiers_rejected(self):
        event = self.events("command_submitted", 1)[0]
        with self.assertRaises(EventValidationError):
            Detector().analyze([event, replace(event, operation="hunt")])

    def test_replay_group_separates_operation_and_actor(self):
        events = self.events("transaction_replay", 2, operation="craft")
        events += self.events("transaction_replay", 2, operation="equip")
        events += self.events("transaction_replay", 2, actor="different-player", operation="craft")
        self.assertEqual(Detector().analyze(events), [])

    def test_guilds_are_separate_authorization_domains(self):
        events = self.events("authz_failure", 3, guild="one") + self.events("authz_failure", 3, guild="two")
        self.assertEqual(Detector().analyze(events), [])

    def test_alert_deduplication_resets_after_window(self):
        early = self.events("authz_failure", 20)
        later = [self.emitter.emit("authz_failure", actor="test-player", timestamp=START + timedelta(seconds=80 + index)) for index in range(5)]
        self.assertEqual(len(Detector().analyze(early + later)), 2)

    def test_input_order_does_not_change_timeline(self):
        original = synthetic_events()
        self.assertEqual(Detector().analyze(original), Detector().analyze(list(reversed(original))))

    def test_expected_stop_once_is_not_interruption(self):
        events = self.events("service_stop", 1, reason="operator_shutdown", service="application")
        self.assertEqual(Detector().analyze(events), [])
        self.assertEqual(Detector().analyze(self.events("service_stop", 1, reason="unexpected"))[0]["rule_id"], "SERVICE_INTERRUPTION")

    def test_three_expected_stops_are_still_unusual(self):
        events = self.events("service_stop", 3, reason="operator_shutdown", spacing=100)
        self.assertEqual(Detector().analyze(events)[0]["count"], 3)

    def test_raw_documents_not_accepted(self):
        with self.assertRaises(EventValidationError):
            Detector().analyze([{"kind": "authz_failure"}])
