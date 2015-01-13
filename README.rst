pglookout
=========

pglookout is a python based PostgreSQL database replication state.
The purpose of pglookout is to monitor PG database nodes, and their
replication status, and act according to that status.

This can mean for example calling a set failover command to
promote a new master in case the previous one goes missing.

pglookout supports two different nodetypes, ones that are installed
on the db nodes themselves, and observer nodes that can be installed
anywhere. The purpose of the pglookout nodes on the DB nodes is to
monitor the replication status of the cluster, and act accordingly,
the observers have a more limited remit, they just observe the cluster
status to give another viewpoint to the cluster state.


Alert files
===========

multiple_master_warning

This alert file is created when multiple masters are detected in the
same cluster.

replication_delay_warning

This alert file is created when replication delay goes over the set
warning limit.

failover_has_happened

This alert file is created whenever the failover command has been
issued.


Config keys
===========

db_poll_interval (default 5.0)

Interval on how often should the connections defined in remote_conns
be polled for information on DB replication state.

remote_conns (default {})

PG database connection strings that the pglookout process should monitor.

observers (default {})

This object contains key value pairs like {"1.2.3.4":
"http://2.3.4.5:15000"}. They are used to determine the location of
pglookout observer processes. Observers are processes that don't take
any actions, but simply give a third party viewpoint on the state of
the cluster. Useful especially during net splits.

http_address (default '')

HTTP webserver address, by default pglookout binds to all interfaces.

http_port (default 15000)

HTTP webserver port.

replication_state_check_interval (default 10.0)

How often should pglookout check the replication state in order to
make decisions on should the node be promoted.

failover_sleep_time (default 0.0)

Time to sleep after a failover command has been issued.

maintenance_mode_file (default "/tmp/pglookout_maintenance_mode_file")

If a file exists in this location, this node will not be considered
for promotion to master.

alert_file_dir defualt os.getcwd()

Directory in which alert files for replication warning and failover
are created.

json_state_file_path (default "/tmp/pglookout_state.json")

Location of a JSON state file which describes the state of the
pglookout process.

max_failover_replication_time_lag" (default 120.0)

Replication time lag after which failover_command will be executed and a
failover_has_happened file will be created.

warning_replication_time_lag (default 30.0)

Replication time lag at which point to exeute
over_warning_limit_command and to create a warning file.

failover_command (default "")

Shell command to execute in case the node has deemed itself in need of promotion

never_promote_these_nodes (default [])

Lists the nodes that will never be considered valid for promotion. As
in if you have master m which fails and standby a and b. b is ahead but is listed
in never_promote_these_nodes, a will be promoted.

over_warning_limit_command (default None)

Shell command to be executed once replication lag is warning_replication_time_lag

own_db

This is how pglookout determines which one of the dbs listed, is it's
own.

log_level (default INFO)

Determines log level of pglookout.

syslog (default false)

Determines whether syslog logging should be turned on or not.

syslog_address (default /dev/log)

Determines syslog address to use in logging (requires syslog to be
true as well)

syslog_facility (default local2)

Determines syslog log facility. (requires syslog to be true as well)


Vulnerability reporting
=======================

If you would like to report a vulnerability or have a security concern on pglookout, please contact hannu.valtonen@ohmu.fi


Copyright
=========

Copyright (C) 2014 F-Secure

License
=======

pglookout is licensed under the Apache License, Version 2.0. Full license text is available in the ``LICENSE`` file and at http://www.apache.org/licenses/LICENSE-2.0.txt.
