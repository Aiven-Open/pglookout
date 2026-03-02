# Examples

Practical configuration examples for common pglookout deployment topologies.

## Minimal 2-Node Cluster

Primary + 1 standby. The simplest possible setup.

**Config for the primary node** (`own_db` set to `"primary-node"`):

```json
{
    "own_db": "primary-node",
    "remote_conns": {
        "primary-node": "host=10.0.0.1 dbname=postgres user=pglookout password=secret",
        "standby-node": "host=10.0.0.2 dbname=postgres user=pglookout password=secret"
    },
    "failover_command": "/usr/local/bin/failover.sh",
    "http_port": 15000,
    "warning_replication_time_lag": 30.0,
    "max_failover_replication_time_lag": 120.0
}
```

**Config for the standby node** is identical except `own_db`:

```json
{
    "own_db": "standby-node",
    "remote_conns": {
        "primary-node": "host=10.0.0.1 dbname=postgres user=pglookout password=secret",
        "standby-node": "host=10.0.0.2 dbname=postgres user=pglookout password=secret"
    },
    "failover_command": "/usr/local/bin/failover.sh",
    "http_port": 15000,
    "warning_replication_time_lag": 30.0,
    "max_failover_replication_time_lag": 120.0
}
```

> **Warning:** A 2-node cluster has no quorum protection. A network partition between the two nodes may cause incorrect failover. Add an observer node (see [Observer-Only Node](#observer-only-node)) to prevent split-brain.

## 3-Node Cluster with Autofollow

Primary + 2 standbys. When failover promotes one standby, the other automatically reconfigures to follow the new primary.

**Config for each node** (adjust `own_db` per node):

```json
{
    "own_db": "node-1",
    "autofollow": true,
    "remote_conns": {
        "node-1": "host=10.0.0.1 dbname=postgres user=pglookout password=secret",
        "node-2": "host=10.0.0.2 dbname=postgres user=pglookout password=secret",
        "node-3": "host=10.0.0.3 dbname=postgres user=pglookout password=secret"
    },
    "failover_command": "/usr/local/bin/failover.sh",
    "http_port": 15000,
    "pg_data_directory": "/var/lib/pgsql/data",
    "pg_start_command": "sudo systemctl start postgresql",
    "pg_stop_command": "sudo systemctl stop postgresql",
    "primary_conninfo_template": "user=replicator password=secret sslmode=require",
    "warning_replication_time_lag": 30.0,
    "max_failover_replication_time_lag": 120.0
}
```

> **Note:** `pg_start_command`, `pg_stop_command`, and `primary_conninfo_template` are required when `autofollow` is `true`. The host in `primary_conninfo_template` is ignored and replaced with the new primary's address during autofollow.

## Observer-Only Node

An observer monitors the cluster from an independent network location without running PostgreSQL or executing failover commands. It provides an additional viewpoint for quorum decisions.

**Observer config:**

```json
{
    "own_db": "",
    "remote_conns": {
        "primary-node": "host=10.0.0.1 dbname=postgres user=pglookout password=secret",
        "standby-1": "host=10.0.0.2 dbname=postgres user=pglookout password=secret",
        "standby-2": "host=10.0.0.3 dbname=postgres user=pglookout password=secret"
    },
    "http_port": 15000
}
```

No `failover_command` is needed. The observer only polls database nodes and serves its view of cluster state via `GET /state.json`.

**DB node config** (add the observer to the `observers` section):

```json
{
    "own_db": "primary-node",
    "remote_conns": {
        "primary-node": "host=10.0.0.1 dbname=postgres user=pglookout password=secret",
        "standby-1": "host=10.0.0.2 dbname=postgres user=pglookout password=secret",
        "standby-2": "host=10.0.0.3 dbname=postgres user=pglookout password=secret"
    },
    "observers": {
        "observer-az2": "http://10.0.1.10:15000"
    },
    "failover_command": "/usr/local/bin/failover.sh",
    "http_port": 15000,
    "warning_replication_time_lag": 30.0,
    "max_failover_replication_time_lag": 120.0
}
```

> **Tip:** Place the observer in a different availability zone or network segment from both the primary and standbys. This ensures it provides a genuinely independent view during network partitions.

## Multi-Cluster Observer

A single observer can monitor multiple PostgreSQL clusters by running separate pglookout instances with different configs and ports.

**Observer config for cluster A** (`/etc/pglookout/cluster-a.json`):

```json
{
    "own_db": "",
    "remote_conns": {
        "cluster-a-primary": "host=10.0.0.1 dbname=postgres user=pglookout password=secret",
        "cluster-a-standby": "host=10.0.0.2 dbname=postgres user=pglookout password=secret"
    },
    "http_port": 15000
}
```

**Observer config for cluster B** (`/etc/pglookout/cluster-b.json`):

```json
{
    "own_db": "",
    "remote_conns": {
        "cluster-b-primary": "host=10.1.0.1 dbname=postgres user=pglookout password=secret",
        "cluster-b-standby": "host=10.1.0.2 dbname=postgres user=pglookout password=secret"
    },
    "http_port": 15001
}
```

Run two pglookout instances:

```bash
pglookout /etc/pglookout/cluster-a.json &
pglookout /etc/pglookout/cluster-b.json &
```

Each cluster's DB nodes reference the observer at the corresponding port in their `observers` config.

## 3-Node Cluster with Failover Priorities

When multiple standbys have the same replication position, `failover_priorities` determines which one gets promoted. Higher numbers mean higher priority.

```json
{
    "own_db": "dc1-node-1",
    "remote_conns": {
        "dc1-node-1": "host=10.0.0.1 dbname=postgres user=pglookout password=secret",
        "dc1-node-2": "host=10.0.0.2 dbname=postgres user=pglookout password=secret",
        "dc2-node-1": "host=10.1.0.1 dbname=postgres user=pglookout password=secret"
    },
    "failover_priorities": {
        "dc1-node-1": 100,
        "dc1-node-2": 100,
        "dc2-node-1": 10
    },
    "failover_command": "/usr/local/bin/failover.sh",
    "http_port": 15000,
    "warning_replication_time_lag": 30.0,
    "max_failover_replication_time_lag": 120.0
}
```

In this example, nodes in datacenter 1 (`dc1`) are preferred over the disaster recovery node in datacenter 2 (`dc2`). If both `dc1-node-1` and `dc1-node-2` have equal replication positions and equal priority, the one with the lexicographically highest name is selected (deterministic tie-breaker).

> **Note:** Failover priorities only break ties between standbys at the same WAL position. The standby furthest along in the WAL stream is always preferred, regardless of priority.
