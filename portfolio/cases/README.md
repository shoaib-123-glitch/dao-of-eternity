# Synthetic incident case studies

All three cases describe the deterministic timeline produced by
`python -m observability demo` (also bundled in `portfolio/demo-data/`). There is
no real account, outage, attacker, production impact or incident response.

1. [Permission denials, a command burst and replayed transactions](01_REPLAY_AND_PERMISSION.md)
2. [Simulated database outage and recovery](02_CONNECTIVITY.md)
3. [Unexpected service stop](03_INTERRUPTION.md)

Generate and inspect the matching exports with [DEMO.md](../DEMO.md); rule
details are in [SOC_RULES.md](../SOC_RULES.md). A detected pattern is evidence to
investigate, not proof of malice. Keep notes to observations, hypotheses, safe
next checks and unknowns.
