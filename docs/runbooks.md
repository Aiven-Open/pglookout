# Runbooks

Operational procedures for common pglookout cluster management tasks.

## Planned Switchover

pglookout handles automatic failover, not planned switchover. Follow these steps to promote a specific replica.

**1. Prevent other replicas from being promoted**

On each replica that should NOT be promoted, create the maintenance mode file:

```bash
touch /tmp/pglookout_maintenance_mode_file
```

**2. Stop pglookout on all nodes**

```bash
systemctl stop pglookout.service
```

**3. Shut down the current primary cleanly**

```bash
pg_ctl stop -D $PGDATA -m fast
```

**4. Promote the target replica**

```bash
pg_ctl promote -D $PGDATA
```

Wait for promotion to complete:

```bash
pg_isready -h localhost
```

**5. Update `remote_conns` in all config files if needed**

If node identifiers or connection strings change, update `/var/lib/pglookout/pglookout.json` on every node.

**6. Reconfigure remaining replicas**

If `autofollow` is enabled, remaining replicas will auto-reconfigure on pglookout start. Otherwise, manually update replication configuration:

For PostgreSQL 12+:

```bash
# Edit postgresql.auto.conf on each remaining replica
# Set primary_conninfo to point at the new primary
```

For PostgreSQL <12:

```bash
# Edit recovery.conf on each remaining replica
# Set primary_conninfo to point at the new primary
```

**7. Start pglookout on all nodes**

```bash
systemctl start pglookout.service
```

**8. Verify the new topology**

```bash
curl http://localhost:15000/state.json | python3 -m json.tool
```

Confirm the new primary shows `pg_is_in_recovery: false` and replicas show `pg_is_in_recovery: true`.

**9. Remove maintenance mode files**

```bash
rm /tmp/pglookout_maintenance_mode_file
```

## Adding a New Replica

**1. Provision the replica**

```bash
pg_basebackup -h <primary-host> -U replicator -D $PGDATA -Fp -Xs -P
```

**2. Configure replication**

For PostgreSQL 12+:

```bash
touch $PGDATA/standby.signal
```

Add to `postgresql.auto.conf`:

```
primary_conninfo = 'host=<primary-host> port=5432 user=replicator password=secret'
```

For PostgreSQL <12, create `recovery.conf`:

```
standby_mode = 'on'
primary_conninfo = 'host=<primary-host> port=5432 user=replicator password=secret'
recovery_target_timeline = 'latest'
```

**3. Start PostgreSQL on the new replica**

```bash
systemctl start postgresql
```

**4. Add the replica to all pglookout configs**

Add the new connection string to `remote_conns` in every pglookout config file:

```json
"remote_conns": {
    "existing-nodes": "...",
    "new-replica": "host=10.0.0.4 dbname=postgres user=pglookout password=secret"
}
```

**5. Reload pglookout on all nodes**

```bash
kill -HUP $(pidof pglookout)
```

**6. Verify**

```bash
curl http://localhost:15000/state.json | python3 -m json.tool
```

Confirm the new replica appears with `connection: true` and `pg_is_in_recovery: true`.

## Removing a Replica

**1. Add the replica to `known_gone_nodes`**

If the replica is being permanently removed, add it to the config on all nodes:

```json
"known_gone_nodes": ["replica-to-remove"]
```

**2. Remove the replica from `remote_conns`**

Remove the entry from all pglookout config files.

**3. Reload pglookout on all nodes**

```bash
kill -HUP $(pidof pglookout)
```

**4. Stop pglookout on the removed replica**

```bash
systemctl stop pglookout.service
```

**5. Stop PostgreSQL on the removed replica**

```bash
systemctl stop postgresql
```

## Removing the Primary (Controlled Decommission)

This procedure triggers immediate failover by removing the primary from configuration.

**1. Add current primary to `known_gone_nodes` on all nodes**

```json
"known_gone_nodes": ["current-primary"]
```

**2. Remove current primary from `remote_conns` on all nodes**

**3. Reload pglookout on all nodes**

```bash
kill -HUP $(pidof pglookout)
```

This triggers immediate failover. When the primary is in `known_gone_nodes`, pglookout skips the `missing_master_from_config_timeout` delay.

**4. Verify failover completed**

Check for the alert file:

```bash
ls -la /var/lib/pglookout/failover_has_happened
```

Verify the new primary:

```bash
curl http://localhost:15000/state.json | python3 -m json.tool
```

**5. Stop the old primary**

```bash
systemctl stop postgresql
```

> **Warning:** Do not stop the old primary before failover completes. Stopping it first may cause data loss if the new primary has not caught up.

## Upgrading PostgreSQL

### Minor Version Upgrades

Rolling restart, one node at a time. Start with replicas, finish with primary.

**1. Upgrade a replica**

```bash
systemctl stop postgresql
# Upgrade PostgreSQL packages
systemctl start postgresql
```

**2. Verify the replica reconnects**

```bash
curl http://localhost:15000/state.json | python3 -m json.tool
```

pglookout will detect temporary disconnections and reconnect automatically.

**3. Repeat for remaining replicas**

**4. Upgrade the primary last**

> **Warning:** Ensure each node's upgrade completes within `max_failover_replication_time_lag` (default: 120s) to avoid unwanted failover. If upgrades take longer, temporarily increase this value or stop pglookout during the upgrade window.

### Major Version Upgrades

Major version upgrades (e.g., 11 to 12) require planning:

- **PostgreSQL 12+ changes:** Autofollow writes to `postgresql.auto.conf` + `standby.signal` instead of `recovery.conf`. No pglookout configuration change is needed; pglookout reads `PG_VERSION` to determine the correct behavior.
- **Full cluster rebuild:** Use `pg_basebackup` to rebuild all replicas from the upgraded primary.
- **pg_upgrade:** If using `pg_upgrade` on the primary, rebuild replicas afterward (pg_upgrade does not upgrade replicas).

Recommended procedure:

1. Stop pglookout on all nodes
2. Upgrade the primary (pg_upgrade or dump/restore)
3. Rebuild replicas via `pg_basebackup`
4. Start PostgreSQL on all nodes
5. Update pglookout configs if connection parameters changed
6. Start pglookout on all nodes
7. Verify via `/state.json`

## Post-Failover Recovery

**1. Investigate root cause**

Check pglookout logs:

```bash
journalctl -u pglookout.service --since "1 hour ago"
```

Check for alert files:

```bash
ls -la /var/lib/pglookout/alerts/
```

**2. Clear the `failover_has_happened` alert file**

After documenting the incident:

```bash
rm /var/lib/pglookout/failover_has_happened
```

**3. Recover the old primary**

If the old primary is recoverable, rebuild it as a replica:

```bash
# On the old primary:
systemctl stop postgresql
rm -rf $PGDATA/*
pg_basebackup -h <new-primary-host> -U replicator -D $PGDATA -Fp -Xs -P
```

Configure replication (see [Adding a New Replica](#adding-a-new-replica) step 2).

Start PostgreSQL:

```bash
systemctl start postgresql
```

**4. Add the recovered node back to the cluster**

Add it to `remote_conns` in all pglookout configs and reload:

```bash
kill -HUP $(pidof pglookout)
```

**5. If the old primary is lost**

Provision a fresh replica using `pg_basebackup` and add it to the cluster following the [Adding a New Replica](#adding-a-new-replica) procedure.
