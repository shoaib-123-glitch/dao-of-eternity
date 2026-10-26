# Resume and interview notes

These are personal-project bullets for a synthetic lab, not paid production
experience. Use a bullet only after you have run the demo yourself and can
explain the code or document it points to.

## Project line

**Dao of Eternity - Offline Network and SOC Observability Lab** (personal
project; Python standard library, JSON Schema, HTML/JavaScript, unittest)

## Networking / NOC bullets

- Documented the network path of a private Python/PostgreSQL application:
  IPv4 loopback, Docker host-to-container port publication (5433 to 5432),
  container DNS scope and why a TCP connection does not prove database identity.
- Wrote a troubleshooting runbook that separates configuration errors, TCP
  refusal or timeout, PostgreSQL authentication failure and wrong-cluster
  identity, with each live check behind an explicit authorization step.
- Built a configuration validator that only accepts numeric loopback targets and
  disabled network probes, so the lab cannot scan or contact services.

## SOC / security analyst bullets

- Designed a versioned (1.0) security-event schema with closed allowlists and
  HMAC-SHA256 pseudonyms, so logs never contain raw user IDs, exception text,
  SQL or credentials; tests check the Python validator and JSON Schema agree.
- Implemented six deterministic detection rules (login and permission bursts, replayed
  transactions, command floods, database outage, service interruption) with
  sliding windows, deduplication and alert suppression.
- Built a dependency-free browser dashboard (strict CSP, no network access,
  text-only rendering) for triaging synthetic alerts and exporting analyst notes.
- Wrote a STRIDE threat model, per-alert response playbooks and three synthetic
  incident case studies.

## What not to claim

Do not say production SIEM, live SOC monitoring, real incident response,
penetration testing, threat hunting, prevented attacks, reduced response time or
years of experience. Say "synthetic" or "lab" when describing alerts and cases.
If you have not done the browser smoke test in [DEMO.md](DEMO.md), describe the
dashboard as "implemented; manual browser test pending".

## Interview talking points

Walk one alert from signal to hypothesis to safe next action; show the control
actor in the demo that does not alert; name a false positive (Discord redelivery,
an impatient player); and say what remains unverified. Be ready to explain why a
port check does not establish database identity, why idempotency belongs in the
transaction rather than the alert, and why a pseudonymized export of real players
still needs privacy review.
