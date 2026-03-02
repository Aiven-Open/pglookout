# Operations Reference

This guide covers the operational aspects of running pglookout in production: CLI commands, HTTP API, signal handling, alert files, state management, and metrics.

## CLI Commands

### pglookout

Main daemon process that monitors PostgreSQL replication and makes failover decisions.

```bash
pglookout /etc/pglookout.json
```

**What it does:**

- Polls configured PostgreSQL nodes to check replication state
- Monitors replication lag against configured thresholds
- Makes failover decisions when the primary becomes unavailable or lag exceeds limits
- Optionally polls observer nodes for additional cluster state perspective
- Serves HTTP API for cluster state queries and manual triggers
- Writes cluster state to JSON file for external consumption
- Creates alert files when human intervention is required

**Running under systemd:**

```bash
systemctl enable pglookout.service
systemctl start pglookout.service
systemctl status pglookout.service
```

### pglookout_current_master

Helper command that reads the configuration file, locates the state file, and prints the current primary node.

```bash
pglookout_current_master /etc/pglookout.json
```

The argument is the **configuration file** (not the state file directly). The command reads `json_state_file_path` from the config to find the state file.

**Exit codes:**

- `0` — Success, current primary printed to stdout
- `1` — Configuration file does not exist
- `-1` — State file is stale (older than 60 seconds) or other error

**Usage in scripts:**

```bash
#!/bin/bash
current_master=$(pglookout_current_master /etc/pglookout.json)
if [ $? -eq 0 ]; then
    echo "Current primary: $current_master"
else
    echo "Failed to determine current primary"
    exit 1
fi
```

> **Note:** Despite the argument name in `--help` suggesting "state file", the argument is actually the **configuration file** path. The command reads the config to find `json_state_file_path`, then reads and parses that state file.

## HTTP API

The webserver binds to `http_address` (default: all interfaces) on `http_port` (default: 15000).

### GET /state.json

Returns current cluster state as JSON.

**Request:**

```bash
curl http://localhost:15000/state.json
```

**Response structure:**

```json
{
    "db_nodes": {
        "primary_node_name": {
            "connection": true,
            "db_time": 1234567890.123,
            "fetch_time": 1234567890.456,
            "pg_is_in_recovery": false,
            "pg_last_xact_replay_timestamp": null,
            "pg_last_xlog_receive_location": "0/3000000",
            "pg_last_xlog_replay_location": null,
            "replication_time_lag": 0.0,
            ...
        },
        "replica_node_name": {
            "connection": true,
            "db_time": 1234567890.123,
            "fetch_time": 1234567890.456,
            "pg_is_in_recovery": true,
            "pg_last_xact_replay_timestamp": "2024-01-15T12:34:56.789Z",
            "pg_last_xlog_receive_location": "0/3000000",
            "pg_last_xlog_replay_location": "0/2FF0000",
            "replication_time_lag": 5.2,
            ...
        }
    },
    "observer_nodes": {
        "observer_address": {
            "connection": true,
            "db_nodes": {
                ...
            }
        }
    },
    "current_master": "primary_node_name"
}
```

**Key fields:**

- `db_nodes` — State of each configured PostgreSQL node
- `observer_nodes` — State reported by external observer nodes
- `current_master` — Name of the current primary node (or `null` if none detected)
- `connection` — Whether pglookout can connect to the node
- `pg_is_in_recovery` — `false` for primary, `true` for replica
- `replication_time_lag` — Replication lag in seconds for replicas

### POST /check

Triggers an immediate cluster state check, bypassing the normal `replication_state_check_interval`. The check is asynchronous — the response returns immediately, and the actual check happens on the monitor's next iteration.

**Request:**

```bash
curl -X POST http://localhost:15000/check
```

**Response:**

```
HTTP/1.0 204 No Content
```

**Use cases:**

- Force a recheck after making configuration changes
- Trigger state evaluation after manual intervention
- Integration with external monitoring systems

## Signal Handling

### SIGHUP

Reloads configuration from disk without restarting the process.

```bash
kill -HUP $(cat /var/run/pglookout.pid)
# or with systemd
systemctl reload pglookout.service
```

**What gets reloaded:**

- Remote connection configuration (`remote_conns`)
- Threshold settings (`warning_replication_time_lag`, `max_failover_replication_time_lag`)
- Failover command and priorities
- Observer configuration
- StatsD configuration

**What does NOT get reloaded:**

- HTTP server address/port (requires restart)
- State file path (requires restart)
- Alert file directory (requires restart)

> **Warning — Removing primary from configuration:** If you remove a primary node from `remote_conns` and send SIGHUP, pglookout will wait `missing_master_from_config_timeout` seconds (default: 15) before making a failover decision.

### SIGTERM / SIGINT

Graceful shutdown. Stops the daemon and closes all connections.

```bash
kill -TERM $(cat /var/run/pglookout.pid)
# or
systemctl stop pglookout.service
```

## Alert Files

Alert files are created in `alert_file_dir` (default: current working directory) when conditions require human intervention. Monitor these files in your alerting system.

### authentication_error

**When created:** PostgreSQL connection attempt fails with a "password authentication" error message.

**Causes:**

- Incorrect username/password in `remote_conns`
- Missing or incorrect `pg_hba.conf` entries
- Network connectivity issues preventing authentication

**Resolution:**

1. Verify credentials in configuration
2. Check `pg_hba.conf` on the target node allows connections from pglookout
3. Run `SELECT pg_reload_conf();` on PostgreSQL after fixing `pg_hba.conf`
4. Verify network connectivity and firewall rules

**Auto-clears:** No. Remove manually after fixing the issue.

### multiple_master_warning

**When created:** More than one node reports `pg_is_in_recovery = false` with an active connection.

**Causes:**

- Network partition causing pglookout to see isolated nodes as separate primaries
- Failover script failed to demote old primary
- Manual promotion without proper STONITH

**Resolution:**

1. Identify which node should be the primary
2. Demote incorrect primary nodes
3. Verify replication is following the correct primary
4. Investigate why STONITH failed (if applicable)

> **DANGER — Split-brain condition:** This is a critical condition that can cause data divergence. Resolve immediately.

**Auto-clears:** No. Remove manually after resolving split-brain.

### replication_delay_warning

**When created:** Replication time lag exceeds `warning_replication_time_lag` threshold.

**Auto-clears:** Yes. Deleted when lag drops below the warning threshold, OR when a successful failover completes (exit code 0).

**Interpretation:**

- Heads-up alert that a failover may be imminent
- Not necessarily a failure requiring intervention
- May indicate high write load or slow replica

**Actions to consider:**

- Check for long-running queries on replica
- Verify replica hardware performance
- Review write load on primary
- Check network latency between primary and replica

### failover_has_happened

**When created:** After the `failover_command` is executed, regardless of its exit code. The alert file is created even if the command fails.

**Auto-clears:** No.

**Post-failover actions:**

1. Verify new primary is serving traffic
2. Check replication is re-established from other replicas
3. Investigate root cause of the failover
4. Plan recovery of failed node (if applicable)
5. Clear alert file after incident is documented

> **Note:** pglookout sets `synchronous_commit = off` on all its database connections. This means its monitoring queries don't wait for WAL to be flushed to standbys. The state pglookout reports may be slightly ahead of what standbys have actually confirmed receiving.

## State File

The JSON state file provides a human-readable snapshot of cluster state.

**Location:** `json_state_file_path` (default: `/tmp/pglookout_state.json`)

**Update frequency:** Every iteration of the main loop (`replication_state_check_interval`, default: 5 seconds)

**Structure:**

```json
{
    "db_nodes": { ... },
    "observer_nodes": { ... },
    "current_master": "node_name"
}
```

**Use cases:**

- Consumed by `pglookout_current_master` helper
- Manual inspection during troubleshooting
- Integration with external monitoring dashboards
- Debugging replication state issues

> **Note:** The state file is written atomically (via `.tmp` file and `rename(2)`) to prevent readers from seeing partial updates.

## Maintenance Mode

Create `maintenance_mode_file` to prevent a node from being promoted to primary.

**Default location:** `/tmp/pglookout_maintenance_mode_file`

**When to use:**

- Planned maintenance on a replica (e.g., hardware upgrades, OS patches)
- Testing failover behavior without affecting a specific node
- Temporarily excluding a node with known issues

**Enable maintenance mode:**

```bash
touch /tmp/pglookout_maintenance_mode_file
```

**Disable maintenance mode:**

```bash
rm /tmp/pglookout_maintenance_mode_file
```

**Behavior:**

- Node will continue to monitor replication and report state
- Node will NOT be considered for promotion during failover
- Applies only to the local node (not cluster-wide)

> **Tip:** Use `never_promote_these_nodes` configuration for permanent exclusions instead of relying on maintenance mode files.

## StatsD Metrics

pglookout can emit metrics to a StatsD-compatible daemon using the Telegraf tag format.

**Configuration:**

```json
{
    "statsd": {
        "host": "statsd.example.com",
        "port": 8125,
        "tags": {
            "environment": "production",
            "cluster": "pg-cluster-01"
        }
    }
}
```

**Metric format:** Telegraf StatsD with tags

```
metric_name,tag1=value1,tag2=value2:value|type
```

### Available Metrics

| Metric | Type | Description |
|--------|------|-------------|
| `pg.replication_lag` | Gauge | Replication lag in seconds for the current node |
| `failover_decision_on_disconnect_not_taken` | Counter | Failovers not executed because `failover_on_disconnect` is `false` |
| `cluster_monitor_health_timeout` | Counter | Incremented when cluster monitor thread hasn't completed a check within `cluster_monitor_health_timeout_seconds` |
| `exception` | Counter | Unexpected exceptions, tagged with `exception` and `where` |

**Configurable health check:**

```json
{
    "cluster_monitor_health_timeout_seconds": 30
}
```

Defaults to `2 * replication_state_check_interval`. If the cluster monitor thread doesn't complete a check within this timeout, the `cluster_monitor_health_timeout` counter is incremented.

### Example Telegraf Configuration

```toml
[[inputs.statsd]]
  protocol = "udp"
  service_address = ":8125"
  metric_separator = "."
  parse_data_dog_tags = false
  allowed_pending_messages = 10000
  percentile_limit = 1000
```

### Monitoring Recommendations

**Critical alerts:**

- `cluster_monitor_health_timeout` — Cluster monitor is stuck
- `exception{exception=*}` — Unexpected errors in pglookout

**Warning alerts:**

- `pg.replication_lag > warning_replication_time_lag` — Replication falling behind
- `failover_decision_on_disconnect_not_taken` — Failovers being skipped

**Dashboards:**

- Graph `pg.replication_lag` per node over time
- Table of `exception` counts grouped by `where` and `exception` type
- Cluster health status based on state file contents (via external scraping)

## Operational Checklist

### Daily Operations

- [ ] Monitor alert file directory for new files
- [ ] Check `pg.replication_lag` metrics are within acceptable bounds
- [ ] Verify state file is being updated (mtime within last minute)

### Configuration Changes

- [ ] Test configuration file syntax with `python3 -m json.tool /etc/pglookout.json`
- [ ] Send SIGHUP to reload configuration
- [ ] Verify configuration reloaded by checking logs
- [ ] Trigger manual check via `POST /check` to force re-evaluation

### Failover Response

- [ ] Verify failover alert file exists
- [ ] Check new primary is accepting writes
- [ ] Confirm replicas are following new primary
- [ ] Review logs to determine failover cause
- [ ] Plan recovery of failed node
- [ ] Document incident and clear alert file

### Troubleshooting

**pglookout not making failover decisions:**

1. Check for `maintenance_mode_file` on the node
2. Verify node is not in `never_promote_these_nodes`
3. Check `failover_on_disconnect` is `true` (default)
4. Review logs for `failover_decision_on_disconnect_not_taken` messages
5. Verify `max_failover_replication_time_lag` threshold is appropriate
6. No connected standby nodes exist -- pglookout logs a warning ("No standby nodes set") and skips failover entirely. At least one connected standby with a known replication position is required for failover.

**No failover despite master being down:**

1. Check that at least one standby is connected and has a known replication position
2. If `known_gone_nodes` is empty, pglookout waits `missing_master_from_config_timeout` seconds (default: 15) after the last config change before acting
3. Verify quorum: pglookout requires knowledge of at least half the cluster (standbys with positions + connected observers >= total nodes x 0.5)
4. Check `failover_on_disconnect` is `true` (default)
5. Check that no `maintenance_mode_file` exists on the candidate node
6. Verify the candidate is not in `never_promote_these_nodes`

**State file not updating:**

1. Check pglookout process is running
2. Verify filesystem permissions on `json_state_file_path` directory
3. Check for "Problem in writing JSON" errors in logs
4. Verify no disk space issues

**Replication lag metrics not appearing:**

1. Verify `statsd` configuration is present and correct
2. Check network connectivity to StatsD host
3. Verify Telegraf/StatsD daemon is listening
4. Check for "Unexpected exception in statsd send" in logs
