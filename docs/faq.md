# FAQ

## Why does pglookout generate write transactions on the primary?

pglookout runs `SELECT txid_current()` on the primary every `db_poll_interval` seconds (default: 5). This creates a small WAL heartbeat transaction.

Without this heartbeat, a quiet primary with no user writes would produce no new WAL records. Replicas would show zero replication lag even if replication is completely broken, since `pg_last_xact_replay_timestamp()` on the replica would remain frozen at the time of the last real transaction.

The heartbeat ensures that a stalled replica is detected within `db_poll_interval + max_failover_replication_time_lag` seconds. The write load is minimal: one small transaction per poll interval.

## How does pglookout compare to Patroni / repmgr?

pglookout is a focused failover daemon, not a full cluster management tool.

| | pglookout | Patroni | repmgr |
|---|---|---|---|
| **Scope** | Failover only | Full cluster lifecycle | Cluster management |
| **DCS dependency** | None (uses observer protocol) | etcd, ZooKeeper, or Consul | None (optional witness) |
| **Replica provisioning** | No | Yes (pg_basebackup) | Yes |
| **Config management** | No | Yes (DCS-stored) | Yes |
| **Configuration** | JSON file + failover script | YAML + DCS | INI file + CLI |

pglookout trades automation breadth for operational simplicity. No external distributed consensus store is required. The observer protocol provides split-brain prevention with fewer moving parts.

Choose pglookout when you want minimal dependencies and are comfortable handling replica provisioning and configuration management yourself.

## Can pglookout handle planned switchovers (not just failover)?

pglookout is designed for automatic failover, not planned switchover. It does not provide a "promote node X" command.

For planned switchover, stop pglookout on all nodes, manually promote the target replica, update configs, and restart pglookout. See the [Runbooks](runbooks.md#planned-switchover) page for step-by-step procedures.

## What happens during a network partition?

Observers are critical for preventing split-brain during network partitions.

**Without observers:** A standby isolated from the primary may see the primary as unreachable and incorrectly trigger failover, creating two primaries.

**With observers in a different network segment:** The isolated standby cannot reach the observer either. Without enough nodes agreeing that the primary is down, the standby will not achieve quorum and will not promote itself.

The quorum formula:

```
size_of_known_state >= total_nodes * 0.5
```

Where:
- `size_of_known_state` = number of standbys with known replication positions + number of connected observers
- `total_nodes` = standbys + 1 (master) - never_promote nodes + total observers

> **Tip:** Deploy at least one observer in a network segment separate from both the primary and the standbys.

## Does pglookout support synchronous replication?

pglookout works with both asynchronous and synchronous replication. No special configuration is needed.

pglookout sets `synchronous_commit = off` on its own monitoring connections to avoid blocking its heartbeat transactions when synchronous replication is enabled. This setting applies only to the pglookout session and does not affect user connections.

Failover decisions are based on WAL position and time lag, regardless of whether the replica is configured as a synchronous standby.

## What permissions does the pglookout database user need?

No superuser required. Basic connection privilege to the `postgres` database is sufficient for core monitoring:

```sql
CREATE USER pglookout PASSWORD 'your_secure_password';
```

The monitoring queries use only `pg_is_in_recovery()`, `pg_last_wal_receive_lsn()`, `pg_last_wal_replay_lsn()`, `pg_current_wal_lsn()`, `pg_last_xact_replay_timestamp()`, and `txid_current()`. These are all available to regular users.

The `pg_read_server_files` role is needed only if the primary has logical replication slots. pglookout reads slot state files via `pg_read_binary_file()` to replicate slot definitions during failover:

```sql
GRANT pg_read_server_files TO pglookout;
```

If you do not use logical replication slots, this grant is not needed.
