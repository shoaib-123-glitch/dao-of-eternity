# Case 3: unexpected service stop

**Classification:** synthetic service-lifecycle training; no bot interrupted.

## Situation

The application starts at 12:00:00. After the database errors in case 2, an
explicit `service_stop` observation at 12:02:12 has outcome `failure` and reason
`unexpected`. The application starts again at 12:02:30 and is observed `healthy`
at 12:02:31.

## Evidence and interpretation

`SERVICE_INTERRUPTION` (high, `actor_ref: null`) fires with count 1, because a
single stop marked `unexpected` is enough; otherwise three stops within 300
seconds are needed. The rule depends on that explicit observation. A start with
no matching shutdown does **not** trigger it: the export may end early, logging
may be off, or the file may be incomplete. A real crash often cannot record its
own stop, so detecting abrupt process death needs an external observer, which
this lab does not have.

The evidence is a bounded synthetic timeline and a categorical reason, not a
stack trace or a recovered database record. Report that uncertainty directly.

## Analyst disposition

Use the export's last timestamp as the observation cutoff, not wall-clock
guesses. Compare with the clean start at 12:00:00. Preserve recovery records and
ask for operator evidence before any real restart. Never reset database state or
infer a failed rollback from a missing lifecycle event.

**Outcome:** an explainable training hypothesis and a safe escalation plan; no
claim of live crash detection, automatic recovery or data loss.
