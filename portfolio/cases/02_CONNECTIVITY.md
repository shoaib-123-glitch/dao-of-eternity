# Case 2: simulated database outage and recovery

**Classification:** synthetic infrastructure training; no connection attempts.

## Situation

The demo records three PostgreSQL `database_failure` events with reason
`connection_refused` and family `connection` at 12:02:00, 12:02:05 and 12:02:10.
At 12:02:11 one application request fails with reason `timeout` after 3000 ms.
At 12:02:32 a `healthy` PostgreSQL observation records simulated recovery. The
events stand for signals a future authorized adapter could produce; no adapter
is enabled and the private database did not go down.

## Evidence and interpretation

`DATABASE_OUTAGE` (high, `actor_ref: null`) fires with count 3: three failures
for one service inside 60 seconds. The final `health.json` shows the PostgreSQL
service `healthy` (last observed 12:02:32) and request metrics of 6 completed,
1 failed, p95 latency 3000 ms. Health is computed only from events; nothing was
probed.

Check fixed codes, not messages. A refused TCP connection, a timeout and a
PostgreSQL authentication failure (`28P01`) are different hypotheses: no
listener or mapping, filtering or a stalled server, and a wrong secret or role.
The timeline alone cannot establish the current listener, the Docker mapping,
the cluster identity or the login's privileges. A wrong target configuration is
a hypothesis to check, not something this case shows.

## Analyst disposition

Confirm the outage alert and that recovery comes after it. Record the intended
loopback endpoint and identity boundary from the
[network architecture](../NETWORK_ARCHITECTURE.md), then stop at the
authorization gate in the [runbook](../RUNBOOK.md). Do not try the Windows player
database, change grants, publish a port or guess passwords. An authorized TCP
check would prove only reachability; identity is a separate guarded check.

**Outcome:** deterministic simulated outage alert and recovery evidence; no real
service outage or remediation claim.
