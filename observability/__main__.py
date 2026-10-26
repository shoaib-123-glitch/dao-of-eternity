"""Only offline configuration checks and explicit sanitized file processing."""

import argparse
from datetime import timedelta
import json
from pathlib import Path
import sys

from .demo import synthetic_events
from .detections import Detector
from .events import EventValidationError, parse_timestamp
from .exports import read_events, write_bundle
from .health import HealthMonitor, validate_lab_config


def _report(events):
    monitor = HealthMonitor()
    for event in events:
        monitor.observe(event)
    from .demo import START
    last = max((parse_timestamp(event.occurred_at) for event in events), default=START)
    return Detector().analyze(events), monitor.report(now=last + timedelta(seconds=1))


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline synthetic security telemetry; no probes, bot login or database access.")
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Write a new synthetic demonstration bundle.")
    demo.add_argument("--output", required=True)
    check = commands.add_parser("check", help="Validate non-secret offline lab description only.")
    check.add_argument("--config", default=str(Path(__file__).with_name("lab_config.json")))
    analyze = commands.add_parser("analyze", help="Validate and analyze an explicit event export.")
    analyze.add_argument("--input", required=True)
    analyze.add_argument("--output", required=True)
    analyze.add_argument("--allow-sanitized-live-export", action="store_true", help="Explicit local import/export of pseudonymous live telemetry; never use for public demos.")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "check":
            target = Path(arguments.config)
            if target.suffix.lower() != ".json" or target.is_symlink() or target.stat().st_size > 32_768:
                raise EventValidationError("Expected a bounded non-secret JSON lab configuration.")
            with target.open("r", encoding="utf-8") as stream:
                value = json.load(stream)
            validate_lab_config(value)
            print("OFFLINE_CONFIG_VALID: probes disabled; no service availability was tested.")
            return 0
        if arguments.command == "demo":
            events = synthetic_events()
            allow_live = False
        else:
            allow_live = arguments.allow_sanitized_live_export
            events = read_events(arguments.input, allow_sanitized_live_input=allow_live)
        alerts, health = _report(events)
        write_bundle(arguments.output, events, alerts, health, allow_sanitized_live_export=allow_live)
        print(f"OFFLINE_EXPORT_WRITTEN: {len(events)} validated events; {len(alerts)} alerts; no live probes.")
        return 0
    except (EventValidationError, OSError, ValueError, TypeError, RecursionError):
        # Never print rejected JSON, file paths, OS messages or traceback details.
        print("OFFLINE_OPERATION_REJECTED: invalid input, provenance, configuration or output destination.", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
