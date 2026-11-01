"""Offline artifact and browser-source contract checks; not a browser execution.

No endpoint probes, file discovery outside this directory, subprocesses or secret
files. Manual rendering/download verification is documented in DEMO.md.
"""

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import unittest

from observability.demo import synthetic_events
from observability.detections import Detector, RULES
from observability.events import (
    SecurityEvent, VERSION, KINDS, OPERATIONS, OUTCOMES, REASONS, FAMILIES, SQLSTATES,
)
from observability.exports import validate_envelope
from observability.lab10 import lab10_document


ROOT = Path(__file__).resolve().parent


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids = []
        self.resources = []
        self.csp = None
        self.inline_handlers = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if "id" in values:
            self.ids.append(values["id"])
        if tag in {"script", "link", "img", "iframe"}:
            self.resources.append(values.get("src", values.get("href", "")))
        if tag == "meta" and values.get("http-equiv", "").lower() == "content-security-policy":
            self.csp = values.get("content")
        self.inline_handlers.extend(key for key in values if key.startswith("on"))


class PortfolioContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script = (ROOT / "dashboard.js").read_text(encoding="utf-8")
        cls.page = Page((ROOT / "dashboard.html").read_text(encoding="utf-8"))
        cls.events_doc = json.loads((ROOT / "demo-data" / "events.json").read_text(encoding="utf-8"))
        cls.events = validate_envelope(cls.events_doc)

    def array(self, name):
        match = re.search(r"\b" + name + r":\s*(\[[^\]]*\])", self.script)
        self.assertIsNotNone(match)
        return json.loads(match.group(1))

    def test_browser_field_contract_matches_canonical_event(self):
        self.assertEqual(set(self.array("fields")), set(SecurityEvent.__dataclass_fields__))

    def test_browser_classification_allowlists_match_canonical_schema(self):
        for key, values in (("operations", OPERATIONS), ("outcomes", OUTCOMES),
                            ("reasons", REASONS), ("families", FAMILIES),
                            ("sqlstates", SQLSTATES), ("rules", RULES)):
            with self.subTest(key=key):
                self.assertEqual(set(self.array(key)), set(values))
        match = re.search(r"kinds:\s*\{([^}]+)\}", self.script)
        self.assertIsNotNone(match)
        kinds = dict(re.findall(r'(\w+):"(\w+)"', match.group(1)))
        self.assertEqual(kinds, KINDS)

    def test_browser_dom_bindings_have_unique_existing_targets(self):
        self.assertEqual(len(self.page.ids), len(set(self.page.ids)))
        refs = set(re.findall(r'el\("([a-z-]+)"\)', self.script))
        self.assertTrue(refs)
        self.assertFalse(refs - set(self.page.ids))

    def test_dashboard_resources_are_local_and_network_blocked(self):
        self.assertEqual(set(self.page.resources), {"dashboard.css", "dashboard.js"})
        for relative in self.page.resources:
            self.assertTrue((ROOT / relative).is_file())
        self.assertIn("connect-src 'none'", self.page.csp)
        self.assertIn("object-src 'none'", self.page.csp)
        self.assertIn("script-src 'self'", self.page.csp)
        self.assertEqual(self.page.inline_handlers, [])
        self.assertNotRegex(self.script, r"\b(fetch|XMLHttpRequest|WebSocket|localStorage|sessionStorage|eval)\s*[(.]")
        self.assertNotRegex((ROOT / "dashboard.css").read_text(), r"@import|url\s*\(")

    def test_untrusted_text_uses_text_content_without_html_sinks(self):
        self.assertIn("n.textContent = String(text)", self.script)
        self.assertNotRegex(self.script, r"innerHTML|outerHTML|insertAdjacentHTML|document\.write|new\s+Function")
        self.assertIn("el(\"notes\").value.slice(0,6000)", self.script)
        self.assertIn("value.mode !== \"synthetic\"", self.script)

    def test_bundled_events_are_deterministic_synthetic_events(self):
        self.assertEqual(self.events_doc["mode"], "synthetic")
        self.assertEqual([event.to_dict() for event in self.events],
                         [event.to_dict() for event in synthetic_events()])
        self.assertEqual(len(self.events), 45)
        self.assertEqual(len({event.event_id for event in self.events}), len(self.events))

    def test_bundled_alerts_are_recomputed_with_resolvable_evidence(self):
        value = json.loads((ROOT / "demo-data" / "alerts.json").read_text(encoding="utf-8"))
        self.assertEqual(value["mode"], "synthetic")
        self.assertEqual(value["alerts"], Detector().analyze(self.events))
        self.assertEqual(len(value["alerts"]), 5)
        by_id = {event.event_id: event for event in self.events}
        for alert in value["alerts"]:
            self.assertEqual(alert["count"], len(alert["event_ids"]))
            for identifier in alert["event_ids"]:
                event = by_id[identifier]
                self.assertEqual(event.service, alert["service"])
                self.assertLessEqual(alert["first_seen"], event.occurred_at)
                self.assertLessEqual(event.occurred_at, alert["last_seen"])

    def test_lab10_demo_is_exact_adapter_output_and_observed_contract(self):
        document = json.loads((ROOT / "demo-data" / "lab10_alerts.json").read_text(encoding="utf-8"))
        self.assertEqual(document, lab10_document(self.events))
        self.assertLessEqual(len(document["alerts"]), 2000)
        self.assertEqual(len({a["alert_id"] for a in document["alerts"]}), len(document["alerts"]))
        for alert in document["alerts"]:
            self.assertIn(alert["severity"], {"HIGH", "MEDIUM", "LOW"})
            self.assertEqual(alert["events"], len(alert["evidence"]))
            self.assertLessEqual(len(alert["evidence"]), 1000)
            for event in alert["evidence"]:
                self.assertEqual(set(event), {"timestamp", "event_type", "outcome", "request_id", "player"})

    def test_local_document_links_resolve_without_external_source_reads(self):
        for document in ROOT.rglob("*.md"):
            for target in re.findall(r"\]\(([^)]+)\)", document.read_text(encoding="utf-8")):
                if "://" in target or target.startswith("#"):
                    continue
                with self.subTest(document=document.name, target=target):
                    self.assertTrue((document.parent / target.split("#")[0]).exists())

    def test_soc_rule_table_matches_detector_rules(self):
        text = (ROOT / "SOC_RULES.md").read_text(encoding="utf-8")
        self.assertIn("contract " + VERSION, text)
        rows = {}
        for line in text.splitlines():
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if len(cells) == 6 and re.fullmatch(r"`[A-Z_]+`", cells[0]):
                rows[cells[0].strip("`")] = cells
        self.assertEqual(set(rows), set(RULES))
        for rule_id, rule in RULES.items():
            with self.subTest(rule=rule_id):
                kinds, threshold, window, group, severity = rows[rule_id][1:]
                self.assertEqual(set(re.findall(r"`(\w+)`", kinds)), rule["kinds"])
                self.assertTrue(threshold.startswith(str(rule["threshold"])))
                self.assertEqual(window, f"{rule['seconds']} s")
                self.assertEqual(severity, rule["severity"])
                self.assertEqual(group.split(", ")[-1] == "service", rule["group"] == "service")
                if rule["group"] == "operation":
                    self.assertTrue(group.endswith("operation"))

    def test_documented_demo_counts_match_generated_bundle(self):
        alerts = Detector().analyze(self.events)
        text = (ROOT / "DEMO.md").read_text(encoding="utf-8")
        self.assertIn(f"OFFLINE_EXPORT_WRITTEN: {len(self.events)} validated events; {len(alerts)} alerts; no live probes.", text)
        for alert in alerts:
            self.assertIn("`" + alert["rule_id"] + "` (" + alert["severity"] + ")", text)

    def test_markdown_has_no_trailing_whitespace_and_ends_with_newline(self):
        for document in ROOT.rglob("*.md"):
            with self.subTest(document=document.name):
                text = document.read_text(encoding="utf-8")
                self.assertTrue(text.endswith("\n"))
                self.assertFalse(re.search(r"[ \t]+$", text, re.MULTILINE))

    def test_portfolio_docs_keep_honest_scope_markers(self):
        for name, marker in (("README.md", "does not claim"), ("RESUME.md", "What not to claim"),
                             ("DEMO.md", "manual, not performed"), ("THREAT_MODEL.md", "STRIDE")):
            with self.subTest(document=name):
                self.assertIn(marker, (ROOT / name).read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
