# pglookout

**PostgreSQL replication monitoring and failover daemon**

## Overview

pglookout monitors PostgreSQL replication clusters and automatically handles failover scenarios. It continuously tracks the health and replication status of database nodes, detects primary failures, and executes predefined failover commands to promote a new primary when necessary.

The daemon is designed for high availability PostgreSQL deployments where automatic failover is critical for minimizing downtime.

## Key Features

- **Automatic failover**: Detects primary node failures and promotes the most suitable standby replica
- **Observer nodes**: External observation points prevent split-brain scenarios during network partitions
- **Autofollow mode**: Remaining replicas automatically reconfigure to follow newly promoted primaries
- **StatsD metrics**: Export cluster health and replication metrics via StatsD/Telegraf
- **HTTP state API**: Query current cluster state and replication status via HTTP endpoints
- **Maintenance mode**: Temporarily exclude nodes from failover consideration during maintenance windows
- **Failover priorities**: Define explicit promotion order for standbys with equivalent replication positions
- **Alert files**: Generate filesystem markers for integration with external monitoring systems

## Node Types

pglookout supports two deployment models:

- **DB nodes**: Installed directly on PostgreSQL primary and replica nodes. Monitor local database state and execute failover commands when elected for promotion.
- **Observer nodes**: Deployed on independent infrastructure. Provide additional viewpoints on cluster health without executing failover actions. A single observer can monitor multiple PostgreSQL clusters simultaneously.

> **Tip:** Deploy at least one observer node in a separate network segment or availability zone to ensure accurate failure detection during network partitions.

## Requirements

- PostgreSQL 10 or later
- Python 3.9 or later (CPython)
- [psycopg2](http://initd.org/psycopg/) Python driver
- [requests](http://www.python-requests.org/) HTTP library
- Linux x86-64 (primary platform; other POSIX systems may work)

## Quick Links

- **Getting Started**: Installation, setup, and basic configuration
- **Configuration**: Detailed configuration reference for all settings
- **Architecture**: System design, failure detection, and decision-making logic
- **Operations**: Running pglookout, alert files, and operational procedures
- **Development**: Contributing guidelines and development setup

## License

pglookout is licensed under the Apache License, Version 2.0. Originally created by Hannu Valtonen and the Ohmu team for F-Secure, now maintained by Aiven developers.

See the [LICENSE](https://github.com/aiven/pglookout/blob/main/LICENSE) file for full license text.
