# Architecture

## Overview

pglookout is a multi-threaded daemon that monitors PostgreSQL replication clusters and performs automatic failover when the primary becomes unavailable or replication lag exceeds configured thresholds. The daemon consists of three main threads that communicate via shared state dictionaries and queues:

```
┌─────────────────────────────────────────────────────────────────┐
│                         PgLookout Process                        │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌────────────────┐     ┌─────────────────┐    ┌────────────┐  │
│  │   PgLookout    │     │ ClusterMonitor  │    │ WebServer  │  │
│  │  (main thread) │     │     Thread      │    │   Thread   │  │
│  └────────────────┘     └─────────────────┘    └────────────┘  │
│         │                       │                      │         │
│         │                       │                      │         │
│    Signal handling         DB polling             HTTP API      │
│    Failover logic       Observer fetching         /state.json   │
│    State writing         Queue updates            /check POST   │
│    Alert files                                                   │
│                                                                  │
│  Shared State:                                                   │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │ cluster_state dict │ observer_state dict │ Queue(s)        │ │
│  └────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────┘
```

## Components

### PgLookout (Main Thread)

The `PgLookout` class implements the main control loop and failover decision logic. It is responsible for:

**Configuration Management**

- Loads configuration from JSON file at startup and on SIGHUP
- Validates remote connection strings and observer URIs
- Manages replication lag thresholds and failover timeouts

**Signal Handling**

- `SIGHUP`: Reloads configuration
- `SIGTERM`/`SIGINT`: Initiates graceful shutdown
- `SIGUSR1`: Requests immediate cluster state check via queue

**Failover Decision Making**

The main loop (`main_loop()`) continuously evaluates cluster state and makes failover decisions:

1. Reads current cluster state from shared dictionaries populated by `ClusterMonitor`
2. Classifies nodes into categories (connected/disconnected, master/standby/observer)
3. Calculates replication lag by converting WAL positions to byte offsets
4. Determines if failover conditions are met
5. Selects the best standby for promotion
6. Executes failover command if configured

**State Persistence**

- Writes `cluster_state` to JSON state file at regular intervals
- Enables external monitoring and debugging

**Alert File Management**

- Creates alert files in configured directory for various conditions
- Alert types: `authentication_error`, `multiple_master_warning`, `replication_delay_warning`, `failover_has_happened`
- Alert files are simple text files (content: `"alert"`); monitoring systems should check for file existence

### ClusterMonitor (Background Thread)

The `ClusterMonitor` class runs in a separate thread and is responsible for gathering cluster state information. It implements a polling loop with configurable interval (`db_poll_interval`).

**Database Connection Management**

- Maintains persistent psycopg2 connections to all configured cluster members
- Uses asynchronous connections (`async_=True`) with `wait_select()` for non-blocking I/O
- Sets `synchronous_commit = off` on each session to prevent monitoring queries from waiting for replication confirmation. This means pglookout's view of data may be slightly ahead of what standbys have confirmed.
- Automatically reconnects when connections fail
- Cleans up connections to nodes removed from configuration

**Database State Polling**

For each cluster member, the monitor queries:

```sql
-- For all nodes:
SELECT now() AS db_time,
       pg_is_in_recovery(),
       pg_last_xact_replay_timestamp(),
       pg_last_wal_receive_lsn() AS pg_last_xlog_receive_location,
       pg_last_wal_replay_lsn() AS pg_last_xlog_replay_location

-- For primary nodes only:
SELECT pg_current_wal_lsn() AS pg_last_xlog_replay_location,
       txid_current()  -- Creates heartbeat transaction
```

> **Warning:** pglookout is NOT read-only. The `txid_current()` call generates a real write transaction on the primary every `db_poll_interval` seconds. This creates a WAL heartbeat that standbys must replay, ensuring replication lag is always measurable. DBAs should account for this additional write load.

On PostgreSQL 10+, the monitor also fetches logical replication slot information from the primary:

```sql
SELECT slot_name, plugin, slot_type, database,
       catalog_xmin, restart_lsn, confirmed_flush_lsn,
       pg_read_binary_file('pg_replslot/' || slot_name || '/state') AS state_data
FROM pg_catalog.pg_replication_slots
WHERE slot_type = 'logical' AND NOT temporary
```

**Replication Metrics Calculation**

The monitor calculates:

- `replication_time_lag`: Time difference between `db_time` and `pg_last_xact_replay_timestamp`
- `min_replication_time_lag`: Lowest observed lag for each standby (tracks best-case performance)
- `replication_start_time`: Monotonic timestamp when replication first observed (used for timeout calculations)

**Observer State Fetching**

Observers are other pglookout instances monitoring the same cluster from different network locations. The monitor fetches their view of cluster state via HTTP:

```
GET http://<observer_uri>/state.json
```

Observer polling behavior:

- Always polls if `poll_observers_on_warning_only` is `false`
- Polls only when replication lag exceeds warning threshold if `poll_observers_on_warning_only` is `true`
- Validates clock skew between local time and observer's HTTP `Date` header (rejects if >5 seconds)

**Parallel Execution**

Uses `ThreadPoolExecutor` to query all cluster members and observers in parallel, minimizing monitoring loop duration.

**State Updates**

All query results are written to shared dictionaries:

- `cluster_state[instance]`: Database node state
- `observer_state[instance]`: Observer node state

These dictionaries are read by the main thread for failover decisions.

### WebServer (Background Thread)

The `WebServer` class implements a simple HTTP API using Python's built-in `http.server` module with `ThreadingMixIn` for concurrent request handling.

**Endpoints**

`GET /state.json`

Returns current cluster state as JSON:

```json
{
  "node1": {
    "connection": true,
    "pg_is_in_recovery": false,
    "pg_last_xlog_replay_location": "0/3000000",
    "db_time": "2024-01-15T10:30:00Z",
    "fetch_time": "2024-01-15T10:30:00Z",
    ...
  },
  "node2": {
    "connection": true,
    "pg_is_in_recovery": true,
    "replication_time_lag": 0.5,
    ...
  }
}
```

This endpoint enables:

- Observer protocol (other pglookout instances fetch this)
- External monitoring and alerting
- Debugging and operational visibility

`POST /check`

Triggers immediate cluster state check by enqueueing a request to the `ClusterMonitor` thread. Returns HTTP 204 (No Content). The monitor processes the request on its next loop iteration.

## State Flow

The flow of cluster state information through the system:

```
┌──────────────────────────────────────────────────────────────────┐
│                         State Flow                                │
└──────────────────────────────────────────────────────────────────┘

1. ClusterMonitor Thread (every db_poll_interval seconds):
   ┌──────────────────────────────────────────────────────────────┐
   │ Connect to all configured DB nodes via psycopg2              │
   │         │                                                     │
   │         ▼                                                     │
   │ Execute SQL queries (pg_is_in_recovery, WAL positions, etc.) │
   │         │                                                     │
   │         ▼                                                     │
   │ Calculate replication_time_lag, min_replication_time_lag     │
   │         │                                                     │
   │         ▼                                                     │
   │ Fetch observer state via HTTP (conditionally)                │
   │         │                                                     │
   │         ▼                                                     │
   │ Update shared cluster_state and observer_state dicts         │
   └──────────────────────────────────────────────────────────────┘
                           │
                           ▼
2. PgLookout Main Thread (every mainloop_interval seconds):
   ┌──────────────────────────────────────────────────────────────┐
   │ Read cluster_state and observer_state dicts                  │
   │         │                                                     │
   │         ▼                                                     │
   │ Classify nodes into categories:                              │
   │   - connected_master_nodes                                   │
   │   - disconnected_master_nodes                                │
   │   - connected_standby_nodes                                  │
   │   - disconnected_standby_nodes                               │
   │   - connected_observer_nodes                                 │
   │   - disconnected_observer_nodes                              │
   │         │                                                     │
   │         ▼                                                     │
   │ Create node_map with replication positions and priorities    │
   │         │                                                     │
   │         ▼                                                     │
   │ Calculate replication lag (convert WAL to byte offset)       │
   │         │                                                     │
   │         ▼                                                     │
   │ Evaluate failover conditions                                 │
   │         │                                                     │
   │         ▼                                                     │
   │ Select best standby (if failover needed)                     │
   │         │                                                     │
   │         ▼                                                     │
   │ Execute failover command                                     │
   │         │                                                     │
   │         ▼                                                     │
   │ Write state to JSON file                                     │
   └──────────────────────────────────────────────────────────────┘
                           │
                           ▼
3. WebServer Thread (on request):
   ┌──────────────────────────────────────────────────────────────┐
   │ HTTP GET /state.json → Return cluster_state dict as JSON     │
   │                                                               │
   │ HTTP POST /check → Enqueue check request to ClusterMonitor   │
   └──────────────────────────────────────────────────────────────┘
```

## Failover Decision Logic

### When Failover is Considered

The main thread evaluates failover conditions on each iteration. Failover is considered when:

**Primary Unreachable**

- All configured master nodes have `connection: False` in `cluster_state`
- Observers (if configured) also report the primary as unreachable
- This prevents split-brain: failover only proceeds if multiple independent observers agree

**Replication Lag Exceeds Threshold**

Lag calculation:

```python
standby_lag = master_position - standby_position  # byte offset difference
```

Where positions are converted from WAL locations like `0/3000000` to 64-bit byte offsets.

Failover triggers when:

1. `standby_lag > replication_lag_warning_boundary` (enters warning state)
2. Warning state persists for at least `replication_lag_failover_timeout` seconds
3. Calculated using `replication_start_time` monotonic timestamp

**Catchup Timeout**

If a standby has been receiving replication data but hasn't caught up within `replication_catchup_timeout` seconds:

```python
time_since_replication_started = now_monotonic - replication_start_time
if time_since_replication_started > replication_catchup_timeout:
    # Failover eligible
```

### Best Standby Selection

When multiple standbys are available, the main thread selects the best candidate using a multi-stage algorithm:

**Stage 1: Replication Position (Primary Criterion)**

Convert WAL positions to byte offsets and select the standby with the highest `pg_last_xlog_replay_location`:

```python
standby_position = convert_xlog_location_to_offset(
    cluster_state[instance]["pg_last_xlog_replay_location"]
)
```

The standby furthest ahead in the WAL stream is preferred.

**Stage 2: Failover Priorities (Tie-Breaker)**

If multiple standbys have identical replication positions, use the `failover_priorities` configuration:

```json
{
  "failover_priorities": {
    "node1": 100,
    "node2": 50,
    "node3": 10
  }
}
```

Higher numeric values are preferred (the code uses `max()`). Nodes without explicit priority default to `0`.

**Stage 3: Connection ID (Final Tie-Breaker)**

If priorities are also identical, the standby with the lexicographically largest connection identifier wins (the code sorts identifiers in reverse order and picks the first). This provides deterministic behavior and works well in environments where nodes have incrementing identifiers, so the latest node is preferred.

### Failover Execution

When failover is triggered:

**1. Command Execution**

If `failover_command` is configured, it is executed as-is (split by whitespace into a subprocess argument list). The command does not receive any arguments from pglookout — all failover logic (which node to promote, STONITH, etc.) must be embedded in the script itself.

```python
self.failover_command = self.config.get("failover_command", "").split()
subprocess.check_call(self.failover_command)
```

**2. Alert File Creation**

An alert file named `failover_has_happened` is created in `alert_file_dir`. The file is a simple text marker (content: `"alert"`) indicating that failover was executed.

**3. Post-Failover Sleep**

The main thread sleeps for `failover_sleep_time` seconds (default: 0) to allow the cluster to stabilize before resuming normal monitoring.

**4. State Reset**

After failover, certain state is cleared:

- `replication_start_time` is reset for all nodes
- Observer state cache is invalidated
- The monitor will detect the new topology on the next poll

## Observer Protocol

Observers prevent split-brain scenarios by providing independent views of cluster state from different network locations.

### Observer Configuration

Observers are other pglookout instances monitoring the same cluster:

```json
{
  "observers": {
    "observer1": "http://10.0.1.100:15000",
    "observer2": "http://10.0.2.100:15000"
  },
  "poll_observers_on_warning_only": true
}
```

### Observer State Sharing

Each pglookout instance exposes its view via `GET /state.json`. The `ClusterMonitor` thread fetches this periodically and stores it in `observer_state[observer_name]`.

### Clock Skew Protection

When fetching observer state, the monitor validates the observer's clock:

```python
remote_time = parse_http_date_header(response.headers["date"])
local_time = parse_iso_datetime(fetch_time)
time_diff = abs(local_time - remote_time)

if time_diff > 5 seconds:
    log.error("Clock skew too large, ignoring observer response")
    return None
```

This prevents incorrect failover decisions due to time desynchronization. This threshold is hardcoded at 5 seconds and cannot be configured.

When processing observer data about master nodes, pglookout also compares the observer's fetch timestamp against its own fetch timestamp. If the difference exceeds `db_poll_interval` (default: 5 seconds), the observer's data for that node is silently discarded. This prevents stale observer data from influencing failover decisions.

### Failover Consensus

Before executing failover, each pglookout instance checks whether it has sufficient cluster knowledge. The quorum formula:

```
total = standbys + 1 (master) - never_promote_nodes + total_observers
majority = total × 0.5
known_state = standbys_with_known_replication_positions + connected_observers
failover proceeds only if: known_state >= majority
```

Practical example: in a 3-node cluster (1 master + 2 standbys) with no observers, `total = 3`, `majority = 1.5`, so a single standby knowing its replication position is enough. Adding observers increases `total` and thus raises the threshold.

This ensures each pglookout instance independently reaches the same conclusion without explicit leader election.

## Autofollow

Autofollow enables standbys to automatically reconfigure themselves to follow a new primary after failover.

### When Autofollow Triggers

The main thread detects autofollow conditions:

1. A standby sees its upstream primary disappear (`pg_last_xlog_receive_location` stops advancing)
2. The standby detects a different node is now the primary (based on `observer_state` or direct checks)
3. The standby's own connection ID does not match the new primary

### Autofollow Execution

When autofollow is triggered, pglookout performs three steps directly:

**1. Stop PostgreSQL** (via `pg_stop_command`)

**2. Update Replication Configuration**

For PostgreSQL <12 (recovery.conf):

```conf
standby_mode = 'on'
primary_conninfo = 'host=new_primary port=5432 ...'
recovery_target_timeline = 'latest'
```

For PostgreSQL 12+ (postgresql.auto.conf + standby.signal):

```conf
primary_conninfo = 'host=new_primary port=5432 ...'
```

```bash
touch /var/lib/postgresql/data/standby.signal
```

**3. Start PostgreSQL** (via `pg_start_command`)

The standby will connect to the new primary and resume replication from the appropriate WAL position.

### Autofollow Safety

Autofollow only proceeds if:

- `autofollow` is `true` and `primary_conninfo_template`, `pg_start_command`, `pg_stop_command` are configured
- The recovery configuration actually changes (no-op if already following the correct primary)

## Node Classification

The main thread continuously classifies nodes based on `cluster_state` and `observer_state`.

### Classification Algorithm

```python
def classify_nodes(cluster_state, observer_state):
    connected_master_nodes = {}
    disconnected_master_nodes = {}
    connected_standby_nodes = {}
    disconnected_standby_nodes = {}
    connected_observer_nodes = {}
    disconnected_observer_nodes = {}

    for instance, state in cluster_state.items():
        if state.get("connection"):
            if state.get("pg_is_in_recovery"):
                connected_standby_nodes[instance] = state
            else:
                connected_master_nodes[instance] = state
        else:
            if state.get("pg_is_in_recovery"):
                disconnected_standby_nodes[instance] = state
            else:
                disconnected_master_nodes[instance] = state

    for instance, state in observer_state.items():
        if state.get("connection"):
            connected_observer_nodes[instance] = state
        else:
            disconnected_observer_nodes[instance] = state

    return (
        connected_master_nodes,
        disconnected_master_nodes,
        connected_standby_nodes,
        disconnected_standby_nodes,
        connected_observer_nodes,
        disconnected_observer_nodes,
    )
```

### Node Categories

**Connected Master**

- `connection: True`
- `pg_is_in_recovery: False`
- Actively serving writes

**Disconnected Master**

- `connection: False`
- Last known state was `pg_is_in_recovery: False`
- May be down, unreachable due to network partition, or experiencing authentication issues

**Connected Standby**

- `connection: True`
- `pg_is_in_recovery: True`
- Actively receiving replication stream
- Eligible for promotion during failover

**Disconnected Standby**

- `connection: False`
- Last known state was `pg_is_in_recovery: True`
- Cannot be promoted until connectivity is restored

**Connected Observer**

- HTTP connection to observer's `/state.json` succeeded
- Provides independent view of cluster state

**Disconnected Observer**

- HTTP connection to observer's `/state.json` failed
- Observer's view is unavailable for failover consensus

### State Staleness

Node classification uses the most recent state available. If a node disconnects, its last known state remains in the dictionary with `connection: False`, but other fields (`pg_is_in_recovery`, `replication_time_lag`, etc.) reflect the last successful query.

The `fetch_time` field indicates when state was last updated, enabling staleness detection.
