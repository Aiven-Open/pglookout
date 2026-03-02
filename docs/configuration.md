# Configuration

## Overview

pglookout is configured using a JSON file. The configuration file path is passed as a command-line argument when starting the daemon:

```bash
pglookout /path/to/config.json
```

Configuration can be reloaded without restarting the daemon by sending a `SIGHUP` signal:

```bash
kill -HUP $(pidof pglookout)
```

When a configuration reload is triggered, pglookout will re-read the configuration file and apply changes to most settings. Some settings like connection strings and observer addresses are applied immediately, allowing dynamic cluster membership changes.

## Full Example Configuration

```json
{
    "autofollow": false,
    "failover_command": "/usr/bin/failover_command",
    "failover_sleep_time": 0.0,
    "http_address": "",
    "http_port": 15000,
    "known_gone_nodes": [],
    "log_level": "DEBUG",
    "max_failover_replication_time_lag": 120.0,
    "never_promote_these_nodes": [],
    "observers": {},
    "own_db": "1.2.3.4",
    "pg_data_directory": "/var/lib/pgsql/data",
    "pg_start_command": "",
    "pg_stop_command": "",
    "primary_conninfo_template": "user=replicator password=fake_pass sslmode=require",
    "remote_conns": {
        "1.2.3.4": "host=1.2.3.4 dbname=postgres user=pglookout password=fake_pass",
        "2.3.4.5": "host=2.3.4.5 dbname=postgres user=pglookout password=fake_pass"
    },
    "syslog": false,
    "syslog_address": "/dev/log",
    "syslog_facility": "local2",
    "warning_replication_time_lag": 30.0
}
```

## Configuration Reference

### Core

#### own_db

**Type:** `string`
**Required:** Yes (except for observer nodes)

The key of the entry in `remote_conns` that matches this node. This identifies which database connection string corresponds to the local node. For observer nodes that don't have a database of their own, this can be set to an empty string `""`.

Example:
```json
"own_db": "1.2.3.4"
```

#### remote_conns

**Type:** `object`
**Default:** `{}`

PostgreSQL database connection strings that the pglookout process should monitor. Keys are identifiers for the remotes (typically IP addresses or hostnames) and values are valid PostgreSQL connection strings or connection info objects.

Example:
```json
"remote_conns": {
    "1.2.3.4": "host=1.2.3.4 dbname=postgres user=pglookout password=secret",
    "2.3.4.5": "postgresql://pglookout:secret@2.3.4.5/postgres"
}
```

See [Connection String Formats](#connection-string-formats) for details on supported formats.

#### observers

**Type:** `object`
**Default:** `{}`

HTTP endpoints of pglookout observer processes. Observers are processes that don't take any actions but provide an additional viewpoint on the state of the cluster. This is especially useful during network partitions.

Keys are identifiers (typically IP addresses) and values are HTTP URLs where the observer's webserver can be reached.

Example:
```json
"observers": {
    "observer1": "http://10.0.0.10:15000",
    "observer2": "http://10.0.0.11:15000"
}
```

A single observer can monitor multiple PostgreSQL clusters simultaneously. It is recommended to run with at least one external observer to provide an independent viewpoint on cluster health.

### Replication Monitoring

#### db_poll_interval

**Type:** `float` (seconds)
**Default:** `5.0`

Interval for polling the connections defined in `remote_conns` for information on database replication state. This determines how frequently pglookout checks the current state of each PostgreSQL node.

Example:
```json
"db_poll_interval": 5.0
```

#### replication_state_check_interval

**Type:** `float` (seconds)
**Default:** `5.0`

How often pglookout should check the replication state to make decisions about whether a node should be promoted. This is the main decision-making interval.

Example:
```json
"replication_state_check_interval": 5.0
```

#### warning_replication_time_lag

**Type:** `float` (seconds)
**Default:** `30.0`

Replication time lag threshold at which pglookout will:

- Execute `over_warning_limit_command` if configured
- Create a `replication_delay_warning` alert file
- Start polling observers (if `poll_observers_on_warning_only` is true)

This value must be less than `max_failover_replication_time_lag`. If set higher, it will be automatically adjusted to match the failover timeout.

Example:
```json
"warning_replication_time_lag": 30.0
```

#### max_failover_replication_time_lag

**Type:** `float` (seconds)
**Default:** `120.0`

Replication time lag threshold after which:

- The `failover_command` will be executed
- A `failover_has_happened` alert file will be created

This is the critical threshold that triggers automatic failover.

Example:
```json
"max_failover_replication_time_lag": 120.0
```

#### poll_observers_on_warning_only

**Type:** `boolean`
**Default:** `false`

When set to `true`, observers are only polled when replication lag exceeds `warning_replication_time_lag`. This can reduce network traffic and observer load when the cluster is healthy.

Example:
```json
"poll_observers_on_warning_only": false
```

### Failover

#### failover_command

**Type:** `string`
**Default:** `""`
**Required:** Yes (for database nodes)

Shell command to execute when the node has determined it should be promoted to primary. This command typically performs actions such as:

- Promoting the replica to primary
- Reconfiguring IP aliases or load balancers
- Updating PgBouncer or PL/Proxy configurations
- STONITH (Shoot The Other Node In The Head) operations

The command should be tested manually with pglookout's user privileges before deployment.

Example:
```json
"failover_command": "/usr/local/bin/promote_to_primary.sh"
```

#### failover_sleep_time

**Type:** `float` (seconds)
**Default:** `0.0`

Time to sleep after the failover command has been issued. This can be useful to allow the system to stabilize before pglookout resumes normal monitoring.

Example:
```json
"failover_sleep_time": 0.0
```

#### failover_on_disconnect

**Type:** `boolean`
**Default:** `true`

Determines whether pglookout should make a failover decision when it loses connection to the primary. When `false`, disconnection from the primary alone will not trigger failover (but replication lag still will).

Example:
```json
"failover_on_disconnect": true
```

#### failover_priorities

**Type:** `object`
**Default:** `{}`

Defines explicit priorities for node promotion when multiple candidates have the same replication position. This ensures all pglookout instances elect the same standby for promotion, which is particularly useful in topologies with nodes in different network locations.

Keys are node identifiers (matching keys in `remote_conns`) and values are numeric priorities (higher numbers = higher priority). Nodes without an explicit priority default to `0`.

Example:
```json
"failover_priorities": {
    "primary-datacenter-node": 100,
    "secondary-datacenter-node": 50,
    "disaster-recovery-node": 10
}
```

If not specified, pglookout uses reverse-sorted remote connection IDs for deterministic selection.

#### known_gone_nodes

**Type:** `array` of `string`
**Default:** `[]`

Lists nodes that are explicitly known to have left the cluster. If the old primary is removed in a controlled manner (e.g., during maintenance), it should be added to this list to ensure there's no extra delay when making promotion decisions.

When the current master is found in `known_gone_nodes`, failover proceeds immediately after a confirmation recheck -- it does NOT wait for `missing_master_from_config_timeout` to elapse. In contrast, if the master simply becomes unreachable but is not in `known_gone_nodes`, pglookout waits for `missing_master_from_config_timeout` seconds (default: 15) before making a failover decision.

Use case: when decommissioning a primary in a controlled manner, add it to `known_gone_nodes` and reload config (SIGHUP) to trigger immediate failover rather than waiting for the timeout.

Example:
```json
"known_gone_nodes": ["old-primary-1.2.3.4", "decommissioned-replica"]
```

#### never_promote_these_nodes

**Type:** `array` of `string`
**Default:** `[]`

Lists nodes that should never be considered valid for promotion. Even if a node in this list has the most recent replication position, it will be skipped and another candidate will be chosen.

Example:
```json
"never_promote_these_nodes": ["backup-replica", "reporting-replica"]
```

Use case: Replicas dedicated to backup operations or reporting queries that should never become primary.

#### missing_master_from_config_timeout

**Type:** `float` (seconds)
**Default:** `15.0`

Time to wait before making a failover decision if a previously existing primary has been removed from the configuration file and a `SIGHUP` has been received. This prevents premature failover during configuration updates.

Example:
```json
"missing_master_from_config_timeout": 15.0
```

#### over_warning_limit_command

**Type:** `string` or `null`
**Default:** `null`

Shell command to execute once replication lag exceeds `warning_replication_time_lag`. This can be used for custom alerting or remediation actions before failover is triggered.

Example:
```json
"over_warning_limit_command": "/usr/local/bin/alert_replication_lag.sh"
```

### Autofollow

Autofollow enables automatic reconfiguration of a replica to follow a newly promoted primary after failover. This is useful in scenarios with one primary and two replicas: when the primary fails and one replica is promoted, autofollow allows the remaining replica to automatically start following the new primary.

#### autofollow

**Type:** `boolean`
**Default:** `false`

Enable automatic following of a newly promoted primary. When enabled, requires `pg_data_directory`, `pg_start_command`, and `pg_stop_command` to be configured.

Example:
```json
"autofollow": true
```

> **Note:** Autofollow writes to different configuration files depending on PostgreSQL version. On PG 12+, it modifies `postgresql.auto.conf` and creates `standby.signal`. On PG <12, it modifies `recovery.conf`. The PG version is detected by reading the `PG_VERSION` file in `pg_data_directory`.

#### primary_conninfo_template

**Type:** `string` or `object`
**Required:** When `autofollow` is `true`

Connection string or connection info object template used when setting a new `primary_conninfo` value for the replica configuration after failover. The hostname and database name in the template are ignored and replaced with a replication connection to the new primary node.

Example (libpq format):
```json
"primary_conninfo_template": "user=replicator password=secret sslmode=require"
```

Example (URL format):
```json
"primary_conninfo_template": "postgresql://replicator:secret@dummy/postgres?sslmode=require"
```

#### pg_data_directory

**Type:** `string`
**Default:** `"/var/lib/pgsql/data"`

PostgreSQL data directory path. Required when `autofollow` is enabled. The pglookout process must have write permissions to this directory (specifically to update replication configuration files).

Example:
```json
"pg_data_directory": "/var/lib/pgsql/data"
```

#### pg_start_command

**Type:** `string`
**Default:** `""`
**Required:** When `autofollow` is `true`

Command to start the PostgreSQL process on a node with autofollow enabled.

Example:
```json
"pg_start_command": "sudo systemctl start postgresql"
```

#### pg_stop_command

**Type:** `string`
**Default:** `""`
**Required:** When `autofollow` is `true`

Command to stop the PostgreSQL process on a node with autofollow enabled.

Example:
```json
"pg_stop_command": "sudo systemctl stop postgresql"
```

### Networking

#### http_address

**Type:** `string`
**Default:** `""` (all interfaces)

IP address for the HTTP webserver to bind to. By default, pglookout binds to all available network interfaces. Set to a specific IP address (e.g., `"127.0.0.1"`) to restrict access.

Example:
```json
"http_address": "0.0.0.0"
```

#### http_port

**Type:** `integer`
**Default:** `15000`

Port for the HTTP webserver. This webserver exposes cluster state information and is used by observer nodes and the `pglookout_current_master` helper command.

Example:
```json
"http_port": 15000
```

### State and Alerts

#### json_state_file_path

**Type:** `string`
**Default:** `"/tmp/pglookout_state.json"`

Path to the JSON state file that pglookout writes periodically. This file contains human-readable cluster state information and is used by the `pglookout_current_master` helper command.

Example:
```json
"json_state_file_path": "/var/run/pglookout/state.json"
```

The state file contains:
- Status of all database nodes
- Status of all observer nodes
- Current primary node identifier

#### alert_file_dir

**Type:** `string`
**Default:** `os.getcwd()` (current working directory)

Directory where alert files are created. Alert files are created for conditions that require human intervention:

- `authentication_error` - Authentication problem with PostgreSQL connection
- `multiple_master_warning` - Multiple primaries detected
- `replication_delay_warning` - Replication lag over warning threshold (informational)
- `failover_has_happened` - Failover command has been executed

Example:
```json
"alert_file_dir": "/var/lib/pglookout/alerts"
```

You should configure your monitoring system to check for the existence of these files.

#### maintenance_mode_file

**Type:** `string`
**Default:** `"/tmp/pglookout_maintenance_mode_file"`

If a file exists at this location, the node will not be considered for promotion to primary. This is useful during planned maintenance or when you want to temporarily exclude a node from failover eligibility.

Example:
```json
"maintenance_mode_file": "/var/lib/pglookout/maintenance_mode"
```

To enable maintenance mode:
```bash
touch /var/lib/pglookout/maintenance_mode
```

### Logging

#### log_level

**Type:** `string`
**Default:** `"INFO"`

Determines the log level for pglookout. Valid values: `"DEBUG"`, `"INFO"`, `"WARNING"`, `"ERROR"`, `"CRITICAL"`.

Example:
```json
"log_level": "INFO"
```

#### syslog

**Type:** `boolean`
**Default:** `false`

Enable logging to syslog in addition to standard output.

Example:
```json
"syslog": true
```

#### syslog_address

**Type:** `string`
**Default:** `"/dev/log"`

Syslog socket address. Only used when `syslog` is `true`. Can be a path to a Unix socket (e.g., `"/dev/log"`) or a network address (e.g., `"localhost:514"`).

Example:
```json
"syslog_address": "/dev/log"
```

#### syslog_facility

**Type:** `string`
**Default:** `"local2"`

Syslog facility to use. Only used when `syslog` is `true`. Common values: `"local0"` through `"local7"`, `"daemon"`, `"user"`.

Example:
```json
"syslog_facility": "local2"
```

### Monitoring

#### statsd

**Type:** `object` or `null`
**Default:** Disabled

Enables metrics sending to a statsd daemon that supports the StatsD/Telegraf syntax with tags.

Example:
```json
"statsd": {
    "host": "statsd.example.com",
    "port": 8125,
    "tags": {
        "environment": "production",
        "cluster": "pg-cluster-1"
    }
}
```

The `tags` field is optional and can be used to add custom tags to all metrics. Metrics follow the [Telegraf specification](https://github.com/influxdata/telegraf/tree/master/plugins/inputs/statsd).

#### cluster_monitor_health_timeout_seconds

**Type:** `float` (seconds)
**Default:** `2 * replication_state_check_interval`

If set, pglookout will increment the statsd counter `cluster_monitor_health_timeout` if the `cluster_monitor` thread has not successfully completed a check within this timeout period. This is useful for detecting when the monitoring loop itself is having problems. Set to `null` to explicitly disable the health check.

Example:
```json
"cluster_monitor_health_timeout_seconds": 30
```

#### replication_catchup_timeout

**Type:** `float` (seconds)
**Default:** `300.0`

Maximum time to wait for a standby to catch up with the primary before it becomes eligible for failover. If a standby has been replicating but hasn't caught up within this timeout, it will be considered for failover regardless.

Example:
```json
"replication_catchup_timeout": 300.0
```

## Connection String Formats

pglookout supports three formats for specifying PostgreSQL connection information in `remote_conns` and `primary_conninfo_template`:

### libpq Format

Traditional PostgreSQL connection string format using space-separated key-value pairs:

```json
"remote_conns": {
    "node1": "host=1.2.3.4 port=5432 dbname=postgres user=pglookout password=secret sslmode=require"
}
```

Values containing spaces must be single-quoted:
```json
"node1": "host=1.2.3.4 dbname='my database' user=pglookout"
```

### URL Format

PostgreSQL URL connection strings (PostgreSQL 9.2+):

```json
"remote_conns": {
    "node1": "postgresql://pglookout:secret@1.2.3.4:5432/postgres?sslmode=require"
}
```

Both `postgres://` and `postgresql://` schemes are supported.

### Dictionary Format

Connection parameters as a JSON object:

```json
"remote_conns": {
    "node1": {
        "host": "1.2.3.4",
        "port": "5432",
        "dbname": "postgres",
        "user": "pglookout",
        "password": "secret",
        "sslmode": "require"
    }
}
```

This format is useful for programmatic configuration generation.

### Common Connection Parameters

| Parameter | Description | Example |
|-----------|-------------|---------|
| `host` | Server hostname or IP address | `1.2.3.4` or `db.example.com` |
| `port` | Port number | `5432` |
| `dbname` | Database name | `postgres` |
| `user` | PostgreSQL user | `pglookout` |
| `password` | Password | `secret` |
| `sslmode` | SSL mode | `require`, `prefer`, `disable` |
| `sslcert` | Client certificate path | `/path/to/client-cert.pem` |
| `sslkey` | Client key path | `/path/to/client-key.pem` |
| `sslrootcert` | CA certificate path | `/path/to/ca-cert.pem` |
| `connect_timeout` | Connection timeout in seconds | `10` |
| `application_name` | Application name for `pg_stat_activity` | `pglookout` |

See the [PostgreSQL documentation](https://www.postgresql.org/docs/current/libpq-connect.html#LIBPQ-PARAMKEYWORDS) for a complete list of connection parameters.
