# Getting Started

This guide walks you through building, installing, and configuring pglookout for PostgreSQL replication monitoring and automatic failover.

## Prerequisites

Before starting, ensure you have:

- PostgreSQL 10 or newer installed on all nodes
- Python 3.9 or newer
- Administrative access to all PostgreSQL nodes
- Network connectivity between all nodes (primary, replicas, observers)

## Building

Build the appropriate package for your distribution.

#### Debian/Ubuntu

```bash
make deb
```

This produces a `.deb` package in the parent directory of the Git checkout.

#### Fedora/RHEL

```bash
make rpm
```

This produces an `.rpm` package in `rpm/RPMS/noarch/`.

#### Python/Other

```bash
python setup.py bdist_egg
```

This produces an egg file in the `dist/` directory.

## Installation

Install the built package with administrative privileges.

#### Debian/Ubuntu

```bash
sudo dpkg -i ../pglookout*.deb
```

#### Fedora/RHEL

```bash
sudo dnf install rpm/RPMS/noarch/pglookout-*.rpm
```

#### Python/Other

```bash
sudo easy_install dist/pglookout-*.egg
```

## Initial Setup

Configure pglookout on all nodes in your PostgreSQL cluster. The setup requires coordination between database configuration and pglookout configuration files.

### 1. Create PostgreSQL User

On the **primary** PostgreSQL node, create a dedicated user for pglookout:

```sql
CREATE USER pglookout PASSWORD 'your_secure_password_here';
```

This user doesn't require superuser privileges. However, note that pglookout executes `txid_current()` on the primary node every poll interval to generate WAL heartbeat transactions, so monitoring is not purely read-only.

> **Note:** If your primary uses logical replication slots (PG 10+), pglookout reads slot state via `pg_read_binary_file()`. This requires the `pg_read_server_files` role:
> ```sql
> GRANT pg_read_server_files TO pglookout;
> ```
> If you don't use logical replication slots, this grant is not needed.

### 2. Configure pg_hba.conf

Edit `pg_hba.conf` on **all PostgreSQL nodes** (primary and replicas) to allow the pglookout user to connect from all cluster nodes:

```
# Allow pglookout connections from all cluster nodes
# TYPE  DATABASE        USER            ADDRESS                 METHOD
host    postgres        pglookout       1.2.3.4/32              md5
host    postgres        pglookout       2.3.4.5/32              md5
host    postgres        pglookout       3.4.5.6/32              md5
```

Replace the IP addresses with your actual node addresses. Adjust the database name if you created a dedicated database for pglookout.

After editing, reload PostgreSQL configuration:

```sql
SELECT pg_reload_conf();
```

Or send a SIGHUP signal:

```bash
sudo systemctl reload postgresql
```

### 3. Create Configuration File

Create `/var/lib/pglookout/pglookout.json` on each node. Start with this template:

```json
{
    "autofollow": false,
    "failover_command": "/usr/local/bin/failover.sh",
    "http_address": "",
    "http_port": 15000,
    "log_level": "INFO",
    "max_failover_replication_time_lag": 120.0,
    "warning_replication_time_lag": 30.0,
    "own_db": "1.2.3.4",
    "remote_conns": {
        "1.2.3.4": "host=1.2.3.4 dbname=postgres user=pglookout password=your_secure_password_here",
        "2.3.4.5": "host=2.3.4.5 dbname=postgres user=pglookout password=your_secure_password_here",
        "3.4.5.6": "host=3.4.5.6 dbname=postgres user=pglookout password=your_secure_password_here"
    },
    "observers": {}
}
```

#### Configuration Explanation

- **`own_db`**: The key from `remote_conns` that identifies this specific node
    - On `1.2.3.4`: set to `"1.2.3.4"`
    - On `2.3.4.5`: set to `"2.3.4.5"`
    - On observer nodes: set to `""`

- **`remote_conns`**: PostgreSQL connection strings for all nodes in the cluster
    - Each key is a unique identifier (typically the node's IP address)
    - Each value is a PostgreSQL connection string
    - All nodes should have the same `remote_conns` configuration

- **`failover_command`**: Path to the script that promotes a replica to primary

- **`max_failover_replication_time_lag`**: Time in seconds before triggering failover (default: 120s)

- **`observers`**: Optional external observers for split-brain prevention
    - Format: `{"observer-id": "http://observer-ip:15000"}`
    - Observers don't run PostgreSQL; they only monitor cluster state

### 4. Create Failover Script

Create the failover script referenced in your configuration (e.g., `/usr/local/bin/failover.sh`):

```bash
#!/bin/bash
# Failover script for pglookout
# This script is executed when pglookout decides to promote a replica

set -e

DATA_DIR="/var/lib/pgsql/data"
VIP="1.2.3.4"  # Virtual IP address for client connections

# 1. Set IP alias to direct traffic to this node
sudo ifconfig eth0:0 "$VIP" netmask 255.255.255.0

# 2. Promote this PostgreSQL replica to primary
pg_ctl promote -D "$DATA_DIR"

# 3. STONITH (Shoot The Other Node In The Head)
# Implement fencing to prevent split-brain scenarios
# Examples:
#   - Power off the old primary via IPMI
#   - Isolate it from the network via switch configuration
#   - Use cloud provider APIs to stop the instance
#
# Example with IPMI:
# ipmitool -I lanplus -H old-primary-ipmi -U admin -P password chassis power off

# 4. Reconfigure connection pooler (optional)
# If using PgBouncer or similar, point it to the new primary
# psql -h pgbouncer-host -c "RELOAD"

exit 0
```

Make the script executable and ensure it works with pglookout's user privileges:

```bash
sudo chmod +x /usr/local/bin/failover.sh
sudo chown postgres:postgres /usr/local/bin/failover.sh
```

> **Warning — Test the Failover Script:** Before deploying to production, test the failover script as the `postgres` user:
>
> ```bash
> sudo -u postgres /usr/local/bin/failover.sh
> ```
>
> Ensure all commands succeed, especially:
>
> - IP aliasing (may require sudo configuration)
> - PostgreSQL promotion
> - STONITH mechanism (if implemented)

> **Info — STONITH Implementation:** STONITH (Shoot The Other Node In The Head) is critical for preventing split-brain scenarios where multiple nodes believe they're the primary. Implement appropriate fencing for your environment:
>
> - **Physical hardware**: IPMI/iLO power control
> - **Virtual machines**: Hypervisor APIs
> - **Cloud environments**: Cloud provider APIs (AWS, GCP, Azure)
> - **Network isolation**: Switch port disable, VLAN changes

### 5. Deploy Configuration to All Nodes

Copy the configuration file to all nodes in your cluster:

```bash
# From your local machine or a central management node
for node in 1.2.3.4 2.3.4.5 3.4.5.6; do
    scp pglookout.json postgres@$node:/var/lib/pglookout/
done
```

**Important**: On each node, edit `/var/lib/pglookout/pglookout.json` and set `own_db` to match that node's identifier from `remote_conns`.

**For observer nodes** (nodes without PostgreSQL):

- Set `own_db` to `""`
- Keep the same `remote_conns` configuration
- Remove or leave empty: `failover_command`, `autofollow`, `pg_data_directory`

## Starting the Service

Start pglookout on all nodes after completing configuration.

### systemd (Recommended)

On Linux systems with systemd, use the provided unit file:

```ini
[Unit]
Description=PostgreSQL replication monitoring and failover daemon

[Service]
User=postgres
Group=postgres
Type=notify
Restart=always
ExecReload=/bin/kill -HUP $MAINPID
ExecStart=/usr/bin/pglookout /var/lib/pglookout/pglookout.json
WorkingDirectory=/var/lib/pglookout

[Install]
WantedBy=multi-user.target
```

Enable and start the service:

```bash
sudo systemctl enable pglookout.service
sudo systemctl start pglookout.service
```

Check the service status:

```bash
sudo systemctl status pglookout.service
```

### supervisord (Alternative)

On systems without systemd, use supervisord. Create `/etc/supervisor/conf.d/pglookout.conf` (adapted from `examples/pglookout_supervisord.conf`):

```ini
[program:pglookout]
command=/usr/bin/pglookout /var/lib/pglookout/pglookout.json
process_name=%(program_name)s
numprocs=1
directory=/var/lib/pglookout
umask=022
priority=999
autostart=true
autorestart=true
startsecs=10
startretries=3
exitcodes=0,2
stopsignal=TERM
stopwaitsecs=10
user=postgres
redirect_stderr=false
stdout_logfile=/var/log/pglookout.stdout
stdout_logfile_maxbytes=1MB
stdout_logfile_backups=10
stderr_logfile=/var/log/pglookout.stderr
stderr_logfile_maxbytes=1MB
stderr_logfile_backups=10
serverurl=AUTO
```

Reload supervisord and start pglookout:

```bash
sudo supervisorctl reread
sudo supervisorctl update
sudo supervisorctl start pglookout
```

Check the status:

```bash
sudo supervisorctl status pglookout
```

## Verifying Operation

After starting pglookout on all nodes, verify that the cluster is being monitored correctly.

### Check the State File

pglookout maintains a JSON state file (default: `/tmp/pglookout_state.json`) that provides a human-readable view of the cluster:

```bash
cat /tmp/pglookout_state.json | jq .
```

Expected output includes:

- Current cluster topology
- Replication lag for each replica
- Connection status for all nodes
- Current primary node

### Query the HTTP API

Each pglookout instance exposes an HTTP endpoint (default port: 15000):

```bash
curl http://localhost:15000/state.json
```

This returns the same information as the state file in JSON format.

### Check the Current Primary

Use the helper utility to identify the current primary:

```bash
pglookout_current_master /var/lib/pglookout/pglookout.json
```

This outputs the identifier of the current primary node.

### Monitor Logs

#### systemd

```bash
sudo journalctl -u pglookout.service -f
```

#### supervisord

```bash
sudo tail -f /var/log/pglookout.stdout
```

Look for:

- Successful connections to all nodes: `Connected to 1.2.3.4`
- Replication status checks: `Checking replication status`
- Current lag values: `Replica 2.3.4.5 lag: 0.5s`

### Alert Files

pglookout creates alert files in the alert directory (default: current working directory) when issues occur:

- **`authentication_error`**: PostgreSQL authentication failed
- **`multiple_master_warning`**: Multiple primaries detected (split-brain)
- **`replication_delay_warning`**: Replication lag exceeded `warning_replication_time_lag`
- **`failover_has_happened`**: Failover command was executed

Monitor these files in your alerting system:

```bash
ls -la /var/lib/pglookout/*.warning /var/lib/pglookout/*.error 2>/dev/null
```

## Next Steps

Once pglookout is running and verified:

1. **Test failover**: Simulate a primary failure in a non-production environment
2. **Configure autofollow**: Enable automatic replica reconfiguration after failover (see [Configuration](configuration.md))
3. **Add observers**: Deploy observer nodes for additional cluster visibility
4. **Set up monitoring**: Integrate alert files with your monitoring system
5. **Tune thresholds**: Adjust `warning_replication_time_lag` and `max_failover_replication_time_lag` for your workload

Refer to the [Configuration](configuration.md) documentation for detailed information on all available options.
