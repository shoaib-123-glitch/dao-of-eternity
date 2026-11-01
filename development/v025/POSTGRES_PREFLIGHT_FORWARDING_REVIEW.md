# PostgreSQL preflight: Docker forwarding review

Status: proposed local changes for review on
`feature/postgres-integration-safeguards`, 2026-10-23. No environment files,
PostgreSQL connections, SQL execution, Docker commands, bot startup, commits,
pushes or merges were used for this investigation. Existing uncommitted work
was preserved. This proposal has only offline validation.

## Confirmed mismatch

The previous `verify_connection()` required both:

```python
row['server_address'] == '127.0.0.1'
row['server_port'] == config.port
```

`config.port` comes from `DAO_TEST_PORT` and is supplied to the client driver.
The catalog's `server_port` comes from `inet_server_port()`, which describes the
listener that accepted the connection on the server. Docker can forward a
published host port to a different container port. For the user-reported client
destination `127.0.0.1:5433` and internal listener `5432`, the comparison rejects
even after authentication to `dao_eternity_test` as `dao_test_user` succeeds.
No claim about actual container metadata was inferred beyond those reported
values. The container's server-side address was not supplied; it can also differ
from the client's loopback destination.

Sources: [PostgreSQL 18 session information functions](https://www.postgresql.org/docs/18/functions-info.html#FUNCTIONS-INFO-SESSION),
[Docker port publishing](https://docs.docker.com/engine/network/port-publishing/).

## Proposed policy

The default endpoint policy remains strict. Omitting `expected_server` still
requires the server to report `127.0.0.1` and the exact configured client port.
The new offline regression explicitly confirms that this default continues to
reject `5433 -> 5432`. There is no automatic Docker detection, fallback after a
mismatch, wildcard endpoint, or collection of acceptable ports.

The catalog-only API additionally accepts an explicit immutable
`ExpectedServerIdentity(address, port, system_identifier)`. All three values are
required and validated before connecting. The address must be an exact canonical
IPv4 address at `127.0.0.1` or within an RFC1918 private network. This restriction
only bounds possible pins: an actual session must match the one exact pinned
address, not any address in those networks. Public addresses, other loopbacks,
IPv6, masks, sockets, missing values and malformed ports/identifiers reject.

| Value | Purpose and verification |
| --- | --- |
| `DAO_TEST_HOST` | Client destination; still exactly `localhost` or `127.0.0.1`, resolved by the connector to literal `127.0.0.1` |
| `DAO_TEST_PORT` | Client destination port; exact validated integer passed to `asyncpg.connect`, with no retry or replacement port |
| Database | Configuration and actual `current_database()` must both be exactly `dao_eternity_test` |
| User | Configuration, actual `current_user`, and actual `session_user` must all be exactly `dao_test_user` |
| Expected server address/port | Exact internal listener from an independently reviewed container, checked against `inet_server_addr()`/`inet_server_port()` |
| Expected system identifier | Exact cluster identifier obtained independently from that container, checked with one fixed read-only `pg_catalog.pg_control_system()` SELECT |

Only the explicit policy issues the additional fixed `SERVER_IDENTITY_SQL`.
Its result must contain an integer `system_identifier` exactly matching the pin.
Missing, unknown, malformed or mismatched metadata rejects before privilege
queries. Permission denial is `PRIVILEGES`; an unavailable function/catalog is
`CATALOG_COMPATIBILITY`; transport failure is `CONNECTION`. There is no grant,
elevated-role retry, marker creation or fallback to endpoint/name checks.

The role, membership, event-trigger, version, primary-session, temporary-schema,
read-only, effective privilege and default-ACL checks remain required. Both
read-only startup and fixed catalog SELECT restrictions are retained. No new
`execute`, `SET`, DDL, DML, schema creation, privilege modification, or application
table access occurs. Every acquired connection still closes on success,
rejection or cancellation, with failed disposal overriding a successful report.

The report keeps `host`/`port` as the client destination and adds `server_port`
and `cluster_identity_verified`. The latter is true only after the explicit
cluster check succeeds. Raw cluster identifiers, internal addresses, catalog
rows, passwords and driver errors are excluded from report output. The existing
launcher prints these two additional allowlisted fields. Its default remains
the direct policy; the explicit `-DockerForwarding` mode now selects the pinned
catalog-only policy for the independently verified target. See the
[exact Docker launch procedure](POSTGRES_DOCKER_PREFLIGHT_LAUNCH.md), including
the independently pinned internal address `172.17.0.2`, reviewed cluster
identifier `7699864005918920747` and secure password prompt.
It cannot silently enable forwarded connections. The future
write-capable API's setup/checkout/cleanup policy is unchanged and does not
accept the new keyword. Other entry points require separate implementation/review.

## Independently establishing the intended Docker identity

The pin must not be learned from the same client SQL connection being checked.
No placeholder or user-reported port is treated as sufficient proof of a cluster.
The following is a proposed later workflow, not actions performed here:

1. Review the full immutable container ID using the trusted local Docker daemon.
   Use narrowly formatted inspection fields; do not dump container environment
   variables. Confirm the intended image, running state, published binding
   `127.0.0.1:5433 -> 5432/tcp`, network address and dedicated data volume. Confirm
   that no production volume is attached and that the cluster was independently
   initialized rather than copied from production. Labels/names alone do not
   prove isolation.
2. From that exact container ID and its verified data directory, obtain the
   PostgreSQL system identifier through the read-only `pg_controldata` utility.
   It reads cluster control metadata without making a SQL connection. The
   reviewed container network address and the listener configuration establish
   the expected internal endpoint. Do not assume that Docker forwards to an
   internal loopback address; if topology differs, review it explicitly.
3. Review those nonsecret expected values outside any `.env` file. Supply the
   complete immutable policy explicitly to the catalog-only API only after
   execution is separately authorized. No new environment-variable fallback or
   default system identifier is implemented.
4. If the role cannot execute the built-in metadata function, stop for review.
   This proposal does not change permissions. Revalidate pins when the container,
   network address, mapping or database volume changes; never update them from
   an identity rejection automatically.

PostgreSQL documents that its control data functions and `pg_controldata` read
the same cluster-wide metadata, including the system identifier:
[control data functions](https://www.postgresql.org/docs/18/functions-info.html#FUNCTIONS-CONTROLDATA),
[pg_controldata](https://www.postgresql.org/docs/18/app-pgcontroldata.html).

This method binds a connection to an independently reviewed cluster against
accidental misrouting under the existing trusted local OS/Docker/DBA assumption.
It does not cryptographically authenticate a container: a physical copy of a
cluster retains its identifier, and a malicious local server/administrator can
spoof metadata. Dedicated-volume and independently initialized-cluster review
are therefore necessary. TLS with independently trusted server credentials
would be separate work if the threat model includes malicious local services.

## Offline regression coverage

Validation result: **391 tests passed**, including **18 new forwarding/identity
tests** (baseline: 373 passing tests). PowerShell syntax parsing and
`git diff --check` also passed. The launcher was parsed, never executed.

`test_preflight_port_forwarding.py` uses synthetic identities and mocked driver
calls. No server behavior is emulated beyond returned metadata. It covers:

- Successful client `127.0.0.1:5433` to internal `5432` forwarding with exact
  endpoint and cluster pins, including loopback and private Docker address fixtures.
- Default-policy rejection without the pin and unchanged direct connections.
- Unexpected cluster with otherwise matching names and endpoint; unexpected
  address/port even when cluster metadata would match.
- Missing/NULL/wrong-type identifiers, malformed policy values, subclasses and
  mutated configurations; failures before driver calls for invalid inputs.
- Strict client host/port/database/user validation and exact actual database,
  effective role and session role, plus existing privilege/read-only gates.
- Metadata permission denial, unavailable function and transport failures without
  retries or weaker fallbacks; cancellation and unconfirmed connection cleanup.
- Fixed SELECTs only, zero execute/transaction calls, closed connections, report
  client/server port separation, and exclusion of identifiers/passwords from output.

Existing diagnostic and launcher regressions remain in scope. The full offline
suite is run with environment-file reads, runtime config/bot/dotenv imports,
external network connections and external processes blocked. Windows asyncio's
own socket-pair construction is permitted only during that synchronous standard
library operation. The platform version probe is mocked to avoid spawning a
shell during dependency imports. These tests do not validate PostgreSQL query
permissions, live catalog compatibility, actual Docker mappings or isolation.

Stop for review before using the proposed path against PostgreSQL.
