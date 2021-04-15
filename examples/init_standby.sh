#!/bin/bash

# Execute pg_basebackup against the standby machine
# Before running this make sure that your /var/lib/pgsql/9.4/data (or equivalent)
# is empty, and that you're sure you want to make this machine a db standby

/usr/pgsql-9.4/bin/pg_basebackup -h 1.2.3.4 --xlog-method=stream -D /var/lib/pgsql/9.4/data/ --progress --write-recovery-conf --username postgres --label initial_base_backup

service postgresql-9.4 start
