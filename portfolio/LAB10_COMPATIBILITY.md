# Existing SOC Lab 10: verified contract and integration boundary

Read-only inspection used only these materials (none were modified or opened in a
browser):

- `C:\Users\Mr. Qureshi\Downloads\Dao_SOC_Lab10_Offline_Dashboard\README.txt`
- `C:\Users\Mr. Qureshi\Downloads\Dao_SOC_Lab10_Offline_Dashboard\dao_soc_dashboard.html`
- `C:\Users\Mr. Qureshi\Downloads\Dao_SOC_Lab10_Offline_Dashboard\dao_soc_lab09_alerts.json`
  (the four synthetic Lab 09 alerts, also embedded in the dashboard)
- `C:\Users\Mr. Qureshi\Downloads\dao_soc_lab09.py` (the synthetic Lab 09 generator)

No browser storage, credentials, archives or real records were read.

## Import contract (from the dashboard's upload handler and `safeAlerts`)

File level:

- Rejected if the file is larger than `3*1024*1024` bytes (3,145,728). The size
  is the file on disk, so the indented JSON as written counts, not a compact form.
- Read as UTF-8 text and parsed with `JSON.parse`; `NaN`/`Infinity` are invalid.
- The top-level value must have an `alerts` array of at most 2,000 entries.
  Otherwise: `Expected Lab 09 JSON with an alerts array (max 2000).`
- `lab` and `data_source` are optional labels; falsy values become
  `Imported Lab 09` / `user-imported data`. Other top-level keys are ignored.
- After normalization, duplicate `alert_id` values reject the whole file.

Alert level (each must be a non-null object with an `evidence` array of at most
1,000 entries, otherwise `Invalid evidence in alert N`):

| Field | Normalization by the importer |
|---|---|
| `alert_id` | `String(...)`; falsy becomes `IMPORTED-<index>` |
| `rule` | `String(...)`; falsy becomes `UNKNOWN`; shown with `_` as spaces |
| `severity` | must be exactly `HIGH`, `MEDIUM` or `LOW` (case-sensitive); anything else is **silently shown as `LOW`** |
| `player` | `String(...)`; falsy becomes `unknown` |
| `events` | `Number(...)`; `0`/`NaN` becomes the evidence count |
| `first_seen_utc`, `last_seen_utc`, `description`, `recommended_triage` | `String(...)`, falsy becomes `""` |
| `evidence[]` | `timestamp`, `event_type`, `outcome`, `request_id`, `player`, each `String(...)` with falsy as `""`; a `null` entry throws and rejects the file |

There is no ID pattern, timestamp parsing, rule enum or string length limit in
the importer. Timestamps are displayed verbatim (Lab 09 uses `...:00Z`, this
adapter uses millisecond `...:00.000Z`). All values are rendered with
`textContent`, so no imported text is interpreted as HTML. Unknown keys are
dropped; the dashboard then stores the normalized data in its own local storage.

Our output has exactly the same key set as `dao_soc_lab09_alerts.json` at the top
level, per alert and per evidence record.

## Implemented adapter

`observability.lab10.lab10_document` derives alerts from validated synthetic
events and the Python detector. It emits fixed descriptions/triage, pseudonymous
references, deterministic IDs (`synthetic-` + 24 hex), uppercase severities from
an explicit mapping, non-empty strings everywhere (so no importer defaulting
occurs), an integer `events` count and at most 1,000 evidence records per alert
(`events` may then exceed the evidence count, which the importer accepts). It
refuses: non-synthetic or sanitized-live events, more than 10,000 events, more
than 2,000 alerts, duplicate alert IDs, unknown severities, and output whose
written bytes (`indent=2`, sorted keys, trailing newline, exactly as
`exports._exclusive_json` writes it) exceed 3 MiB. `demo` and synthetic
`analyze` write `lab10_alerts.json`; `portfolio/demo-data/lab10_alerts.json` is
byte-identical to the adapter output for the deterministic synthetic demo events.

Fixed during verification: the byte limit was previously measured on compact JSON
against 3,000,000 bytes, but the written file is indented and about 1.3x larger,
so files of roughly 3.15 to 4 MB could pass the adapter yet be rejected by the
dashboard. The limit is now checked on the exact written bytes. The demo file was
already current and did not need regeneration.

## How it was verified

Node.js was not available on this machine, so the dashboard JavaScript was **not
executed**. Instead `observability/tests/test_lab10.py` contains a faithful Python
re-implementation of the upload handler and `safeAlerts` (JavaScript truthiness,
`String`/`Number` coercion, null-entry `TypeError`, size and count limits, and the
duplicate-ID check), with the original JavaScript kept verbatim beside it for
review. The tests:

- import the bundled `lab10_alerts.json` bytes, a freshly generated document, and
  the file actually written by `write_bundle`, and require the importer's
  normalized result to equal the input exactly (nothing defaulted or coerced);
- check the bounds, uppercase severity, unique ID pattern, evidence field set,
  timestamp format, 1,000-evidence cap, 3 MiB written-size limit and synthetic-only
  provenance (sanitized-live and mixed input rejected);
- check the emulation itself against a Lab 09 sample alert and against each
  rejection and silent-normalization rule listed above.

Run: `python -B -m unittest discover -s observability/tests -t . -q` from the bot root.

## Limits

This is an adapter for the observed contract, not a rewrite of earlier labs or a
live SOC integration. The emulation is a reading of the source, not the browser
itself; no browser launch, import, or real-player export occurred. To finish the
optional manual check, open the existing dashboard locally, click **Import Lab 09
JSON**, choose only the synthetic `lab10_alerts.json`, inspect its evidence, then
export a synthetic report. Importing replaces the dashboard's current alerts and
clears its saved notes, so export existing investigations first. The new
repository dashboard has stricter synthetic provenance checks and uses in-memory
notes without local storage. Both tools need manual browser smoke testing before
their visual behavior can be claimed verified.
