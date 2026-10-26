"""Lab10 import contract, checked by a Python re-implementation of the importer.

Node.js was not available, so the real browser code was not executed. The
functions below re-create, rule for rule, the JavaScript copied verbatim from the
read-only dashboard (Dao_SOC_Lab10_Offline_Dashboard/dao_soc_dashboard.html)
into IMPORTER_JS: the upload handler plus safeAlerts. IMPORTER_JS is always checked
against the committed copy in fixtures/; when the external original is present it
is only read, to check the committed copy has not drifted from it.
"""

from datetime import timedelta
import json
import math
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from observability import Detector, EventEmitter, EventValidationError, HealthMonitor
from observability import lab10
from observability.demo import synthetic_events, DEMO_KEY, START
from observability.exports import write_bundle
from observability.lab10 import lab10_document


IMPORTER_JS = r"""
function safeAlerts(o){if(!o||!Array.isArray(o.alerts)||o.alerts.length>2000)throw Error('Expected Lab 09 JSON with an alerts array (max 2000).');return o.alerts.map((a,i)=>{if(!a||typeof a!=='object'||!Array.isArray(a.evidence)||a.evidence.length>1000)throw Error('Invalid evidence in alert '+(i+1));return {alert_id:String(a.alert_id||('IMPORTED-'+i)),rule:String(a.rule||'UNKNOWN'),severity:['HIGH','MEDIUM','LOW'].includes(a.severity)?a.severity:'LOW',player:String(a.player||'unknown'),events:Number(a.events)||a.evidence.length,first_seen_utc:String(a.first_seen_utc||''),last_seen_utc:String(a.last_seen_utc||''),description:String(a.description||''),recommended_triage:String(a.recommended_triage||''),evidence:a.evidence.map(e=>({timestamp:String(e.timestamp||''),event_type:String(e.event_type||''),outcome:String(e.outcome||''),request_id:String(e.request_id||''),player:String(e.player||'')}))}})}
$('upload').onchange=async e=>{const f=e.target.files[0];if(!f)return;try{if(f.size>3*1024*1024)throw Error('File exceeds 3 MB safety limit.');const o=JSON.parse(await f.text());const alerts=safeAlerts(o);const ids=alerts.map(a=>a.alert_id);if(new Set(ids).size!==ids.length)throw Error('Duplicate alert IDs in JSON.');source={lab:String(o.lab||'Imported Lab 09'),data_source:String(o.data_source||'user-imported data'),alerts};...}catch(err){notify('Import failed: '+err.message)}...};
"""
DEMO = Path(__file__).resolve().parents[2] / "portfolio" / "demo-data" / "lab10_alerts.json"
DASHBOARD = Path(__file__).resolve().parents[5] / "Dao_SOC_Lab10_Offline_Dashboard" / "dao_soc_dashboard.html"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "dao_soc_dashboard.html"
ID = re.compile(r"^synthetic-[0-9a-f]{24}$")
STAMP = re.compile(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
ALERT_KEYS = {"alert_id", "rule", "severity", "player", "events", "first_seen_utc", "last_seen_utc", "description", "recommended_triage", "evidence"}
EVIDENCE_KEYS = {"timestamp", "event_type", "outcome", "request_id", "player"}


class ImportRejected(Exception):
    pass


def _truthy(v):
    return not (v is None or v is False or v == "" or (isinstance(v, (int, float)) and not isinstance(v, bool) and (v == 0 or math.isnan(v))))


def _string(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    if isinstance(v, list):
        return ",".join("" if x is None else _string(x) for x in v)
    if isinstance(v, dict):
        return "[object Object]"
    return str(v)


def _number(v):
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    if v is None:
        return 0
    if isinstance(v, list):
        return 0 if not v else _number(_string(v)) if len(v) == 1 else math.nan
    if isinstance(v, str):
        try:
            return float(v.strip()) if v.strip() else 0
        except ValueError:
            return math.nan
    return math.nan


def _get(value, name):
    if value is None:
        raise ImportRejected("TypeError: cannot read properties of null")
    return value.get(name) if isinstance(value, dict) else None


def _or(value, default):
    return _string(value) if _truthy(value) else default


def safe_alerts(o):
    alerts = _get(o, "alerts") if _truthy(o) else None
    if not isinstance(alerts, list) or len(alerts) > 2000:
        raise ImportRejected("Expected Lab 09 JSON with an alerts array (max 2000).")
    result = []
    for i, a in enumerate(alerts):
        if not _truthy(a) or not isinstance(a, (dict, list)) or not isinstance(_get(a, "evidence"), list) or len(a["evidence"]) > 1000:
            raise ImportRejected("Invalid evidence in alert " + str(i + 1))
        events = _number(a.get("events"))
        result.append({
            "alert_id": _or(a.get("alert_id"), "IMPORTED-" + str(i)), "rule": _or(a.get("rule"), "UNKNOWN"),
            "severity": a.get("severity") if a.get("severity") in ("HIGH", "MEDIUM", "LOW") else "LOW",
            "player": _or(a.get("player"), "unknown"), "events": events if _truthy(events) else len(a["evidence"]),
            "first_seen_utc": _or(a.get("first_seen_utc"), ""), "last_seen_utc": _or(a.get("last_seen_utc"), ""),
            "description": _or(a.get("description"), ""), "recommended_triage": _or(a.get("recommended_triage"), ""),
            "evidence": [{k: _or(_get(e, k), "") for k in ("timestamp", "event_type", "outcome", "request_id", "player")} for e in a["evidence"]],
        })
    return result


def import_file(data):
    if len(data) > 3 * 1024 * 1024:
        raise ImportRejected("File exceeds 3 MB safety limit.")
    text = data.decode("utf-8", errors="replace").removeprefix("\ufeff")

    def constant(name):
        raise ImportRejected("JSON.parse rejects " + name)
    try:
        o = json.loads(text, parse_constant=constant)
    except ValueError as error:
        raise ImportRejected("SyntaxError") from error
    alerts = safe_alerts(o)
    ids = [a["alert_id"] for a in alerts]
    if len(set(ids)) != len(ids):
        raise ImportRejected("Duplicate alert IDs in JSON.")
    return {"lab": _or(_get(o, "lab"), "Imported Lab 09"), "data_source": _or(_get(o, "data_source"), "user-imported data"), "alerts": alerts}


def burst_events(count=1200, spacing=0.009):
    emitter = EventEmitter(DEMO_KEY, instance_id="lab10-contract")
    return [emitter.emit("command_submitted", actor="lab10-a", operation="command", outcome="accepted",
                         timestamp=START + timedelta(seconds=index * spacing)) for index in range(count)]


class ImporterEmulationTests(unittest.TestCase):
    def test_importer_js_matches_dashboard_verbatim(self):
        html = FIXTURE.read_text(encoding="utf-8")
        for line in IMPORTER_JS.strip().splitlines():
            for part in filter(None, line.split("...")):
                self.assertIn(part, html)
        if DASHBOARD.is_file():  # Local checkouts beside the original catch fixture drift; CI has only the fixture.
            self.assertEqual(FIXTURE.read_bytes(), DASHBOARD.read_bytes())

    def doc(self, **alert):
        base = {"alert_id": "A-1", "rule": "R", "severity": "HIGH", "player": "p", "events": 1, "first_seen_utc": "t",
                "last_seen_utc": "t", "description": "d", "recommended_triage": "r",
                "evidence": [{"timestamp": "t", "event_type": "e", "outcome": "o", "request_id": "q", "player": "p"}]}
        return {"lab": "x", "data_source": "synthetic", "alerts": [{**base, **alert}]}

    def load(self, value):
        return import_file(json.dumps(value).encode("utf-8"))

    def test_bundled_lab09_shape_is_accepted_unchanged(self):
        # One alert from the dashboard's own bundled synthetic sample.
        sample = {"lab": "DOE-SOC-009", "data_source": "synthetic offline training data", "alerts": [{"alert_id": "DOE-09-003", "rule": "DATABASE_CONNECTION_FAILURES", "severity": "LOW", "player": "player_06", "events": 3, "first_seen_utc": "2026-10-25T18:03:35Z", "last_seen_utc": "2026-10-25T18:03:50Z", "description": "Three database connection failures within 60 seconds.", "recommended_triage": "Check service health, network path and credentials without exposing secrets.", "evidence": [{"timestamp": "2026-10-25T18:03:35Z", "player": "player_06", "event_type": "database", "request_id": "", "outcome": "connection_failed"}]}]}
        self.assertEqual(self.load(sample), sample)

    def test_rejection_rules(self):
        too_many = self.doc()
        too_many["alerts"] = [dict(too_many["alerts"][0], alert_id=str(i)) for i in range(2001)]
        cases = {"no alerts": {"lab": "x"}, "alerts not array": {"alerts": {}}, "array top": [], "2001 alerts": too_many,
                 "1001 evidence": self.doc(evidence=[{}] * 1001), "missing evidence": self.doc(evidence=None),
                 "null evidence item": self.doc(evidence=[None]), "null alert": {"alerts": [None]}}
        for name, value in cases.items():
            with self.subTest(name), self.assertRaises(ImportRejected):
                self.load(value)
        duplicate = self.doc()
        duplicate["alerts"] *= 2
        with self.assertRaises(ImportRejected):
            self.load(duplicate)
        with self.assertRaises(ImportRejected):
            import_file(b'{"alerts": [], "x": NaN}')
        with self.assertRaises(ImportRejected):
            import_file(b" " * (3 * 1024 * 1024 + 1))
        self.assertEqual(import_file(b'{"alerts": []}' + b" " * (3 * 1024 * 1024 - 14))["alerts"], [])

    def test_silent_normalizations_the_adapter_must_avoid(self):
        alert = self.load(self.doc(severity="high", alert_id="", player="", events=0, rule=None,
                                   evidence=[{"timestamp": 5, "outcome": False}, "x"]))["alerts"][0]
        self.assertEqual((alert["severity"], alert["alert_id"], alert["player"], alert["events"], alert["rule"]), ("LOW", "IMPORTED-0", "unknown", 2, "UNKNOWN"))
        self.assertEqual(alert["evidence"][0], {"timestamp": "5", "event_type": "", "outcome": "", "request_id": "", "player": ""})
        self.assertEqual(alert["evidence"][1]["timestamp"], "")
        self.assertEqual(self.load(self.doc(severity="CRITICAL"))["alerts"][0]["severity"], "LOW")


class Lab10AdapterContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.events = synthetic_events()
        cls.document = lab10_document(cls.events)

    def assert_contract(self, document):
        self.assertEqual(set(document), {"lab", "data_source", "alerts"})
        self.assertIn("SYNTHETIC ONLY", document["data_source"])
        self.assertTrue(isinstance(document["lab"], str) and document["lab"])
        self.assertLessEqual(len(document["alerts"]), lab10.MAX_ALERTS)
        self.assertEqual(len({a["alert_id"] for a in document["alerts"]}), len(document["alerts"]))
        for alert in document["alerts"]:
            self.assertEqual(set(alert), ALERT_KEYS)
            self.assertRegex(alert["alert_id"], ID)
            self.assertIn(alert["severity"], {"HIGH", "MEDIUM", "LOW"})
            self.assertIn(alert["rule"], lab10.DESCRIPTIONS)
            self.assertIs(type(alert["events"]), int)
            self.assertGreaterEqual(alert["events"], len(alert["evidence"]))
            self.assertTrue(1 <= len(alert["evidence"]) <= lab10.MAX_EVIDENCE)
            for name in ALERT_KEYS - {"events", "evidence"}:
                self.assertTrue(isinstance(alert[name], str) and alert[name], name)
            self.assertRegex(alert["first_seen_utc"], STAMP)
            self.assertRegex(alert["last_seen_utc"], STAMP)
            self.assertLessEqual(alert["first_seen_utc"], alert["last_seen_utc"])
            for item in alert["evidence"]:
                self.assertEqual(set(item), EVIDENCE_KEYS)
                for value in item.values():
                    self.assertTrue(isinstance(value, str) and value)
                self.assertRegex(item["timestamp"], STAMP)
                self.assertTrue(alert["first_seen_utc"] <= item["timestamp"] <= alert["last_seen_utc"])
        # The importer keeps every value exactly: no defaulting or coercion happened.
        self.assertEqual(import_file(lab10.encoded(document)), document)

    def test_bundled_demo_file_is_current_and_imports_unchanged(self):
        data = DEMO.read_bytes()
        self.assertEqual(data, lab10.encoded(self.document))
        self.assertLessEqual(len(data), lab10.MAX_BYTES)
        self.assertEqual(import_file(data), self.document)
        self.assert_contract(json.loads(data))
        self.assertEqual(len(self.document["alerts"]), 5)
        self.assertEqual({a["severity"] for a in self.document["alerts"]}, {"HIGH", "MEDIUM"})
        for alert in self.document["alerts"]:
            self.assertEqual(alert["events"], len(alert["evidence"]))

    def test_written_bundle_file_imports_unchanged(self):
        alerts = Detector().analyze(self.events)
        monitor = HealthMonitor()
        for event in self.events:
            monitor.observe(event)
        with tempfile.TemporaryDirectory() as directory:
            output = write_bundle(Path(directory) / "case", self.events, alerts, monitor.report(now=START + timedelta(seconds=159)))
            data = (output / "lab10_alerts.json").read_bytes()
        self.assertEqual(data, lab10.encoded(self.document))
        self.assertEqual(import_file(data), self.document)

    def test_fresh_document_is_deterministic_and_pseudonymous(self):
        self.assertEqual(lab10_document(reversed(self.events)), self.document)
        serialized = json.dumps(self.document)
        for raw in ("demo-observer-a", "demo-guild", "demo-transaction-craft-1", "portfolio-demo"):
            self.assertNotIn(raw, serialized)
        self.assert_contract(self.document)

    def test_evidence_is_capped_at_importer_bound(self):
        document = lab10_document(burst_events())
        self.assert_contract(document)
        largest = max(document["alerts"], key=lambda a: a["events"])
        self.assertGreater(largest["events"], lab10.MAX_EVIDENCE)
        self.assertEqual(len(largest["evidence"]), lab10.MAX_EVIDENCE)

    def test_byte_limit_measures_the_written_indented_file(self):
        compact = len(json.dumps(self.document).encode("utf-8"))
        self.assertGreater(len(lab10.encoded(self.document)), compact)
        with patch.object(lab10, "MAX_BYTES", compact + 1), self.assertRaises(EventValidationError):
            lab10_document(self.events)
        with patch.object(lab10, "MAX_BYTES", len(lab10.encoded(self.document))):
            self.assertEqual(lab10_document(self.events), self.document)
        self.assertEqual(lab10.MAX_BYTES, 3 * 1024 * 1024)

    def test_alert_bound_unique_ids_and_severity_are_enforced(self):
        alert = Detector().analyze(self.events)[0]
        with patch.object(lab10, "MAX_ALERTS", 4), self.assertRaises(EventValidationError):
            lab10_document(self.events)
        with patch.object(lab10.Detector, "analyze", return_value=[alert, alert]), self.assertRaises(EventValidationError):
            lab10_document(self.events)
        with patch.object(lab10.Detector, "analyze", return_value=[{**alert, "severity": "critical"}]), self.assertRaises(EventValidationError):
            lab10_document(self.events)

    def test_synthetic_only_provenance(self):
        live = EventEmitter(DEMO_KEY, mode="sanitized-live", allow_live=True)
        event = live.emit("service_stop", operation="shutdown", outcome="failure", reason="unexpected", timestamp=START)
        for value in ([event], self.events + [event], [object()], [event.to_dict()]):
            with self.subTest(value=type(value[-1]).__name__), self.assertRaises(EventValidationError):
                lab10_document(value)
        with self.assertRaises(EventValidationError):
            lab10_document([self.events[0]] * (lab10.MAX_EVENTS + 1))


if __name__ == "__main__":
    unittest.main()
