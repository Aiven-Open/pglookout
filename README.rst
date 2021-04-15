pglookout |BuildStatus|_
========================

.. |BuildStatus| image:: https://travis-ci.org/aiven/pglookout.png?branch=master
.. _BuildStatus: https://travis-ci.org/aiven/pglookout

pglookout is a PostgreSQL replication monitoring and failover daemon.
pglookout monitors PG database nodes and their replication status and acts
according to that status, for example calling a predefined failover command
to promote a new master in case the previous one goes missing.

pglookout supports two different node types, ones that are installed on the
db nodes themselves, and observer nodes that can be installed anywhere.  The
purpose of pglookout on the PostgreSQL DB nodes is to monitor the replication
status of the cluster and act accordingly, the observers have a more limited
remit: they just observe the cluster status to give another viewpoint to the
cluster state.

A single observer can observe any number of PostgreSQL replication
clusters simultaneously. This makes it possible to share an observer
between multiple replication clusters. In general it is recommended
that you run with at least one external observer giving an additional
viewpoint on the health of the cluster.


Requirements
============

pglookout can monitor PostgreSQL versions 9.1 and above.  Previous versions don't
provide enough replication information to support pglookout.

pglookout has been developed and tested on modern Linux x86-64 systems, but
should work on other platforms that provide the required modules.  pglookout is
implemented in Python and works with CPython versions 3.5 or
newer.  pglookout depends on the Requests_ and Psycopg2_ Python modules.

.. _`Requests`: http://www.python-requests.org/en/latest/
.. _`Psycopg2`: http://initd.org/psycopg/


Building
========

To build an installation package for your distribution, go to the root
directory of a pglookout Git checkout and then run:

Debian::

  make deb

This will produce a .deb package into the parent directory of the Git checkout.

Fedora::

  make rpm

This will produce a ``.rpm`` package into ``rpm/RPMS/noarch/``.

Python/Other::

  python setup.py bdist_egg

This will produce an egg file into a dist directory within the same folder.


Installation
============

To install it run as root:

Debian::

  dpkg -i ../pglookout*.deb

Fedora::

  dnf install rpm/RPMS/noarch/*

On Linux systems it is recommended to simply run ``pglookout`` under
``systemd``::

  systemctl enable pglookout.service

and eventually after the setup section, you can just run::

  systemctl start pglookout.service

Python/Other::

  easy_install dist/pglookout-1.4.0-py3.8.egg

On systems without ``systemd`` it is recommended that you run ``pglookout``
under Supervisor_ or other similar process control system.

.. _`Supervisor`: http://supervisord.org


Setup
=====

After this you need to create a suitable JSON configuration file for your
installation.

1. Create a suitable PostgreSQL user account for pglookout::

     CREATE USER pglookout PASSWORD 'putyourpasswordhere';

2. Edit the local ``pg_hba.conf`` to allow access for the newly
   created account to the ``postgres`` (or other suitable database of your choice)
   from the master, standby and possible observer nodes. While pglookout will
   only need to run a few builtin functions within the database, it is
   still recommended to setup a separate empty database for this
   use. Remember to reload the configuration with either::

     SELECT pg_reload_conf();

   or by sending directly a ``SIGHUP`` to the PostgreSQL postmaster process.

3. Fill in the created user account and master/standby/observer
   addresses into the configuration file ``pglookout.json`` to the
   section ``remote_conns``.

4. Create a failover script and add the path to it into the
   configuration key ``failover_command``. As an example
   failover script, a shell script that uses IP aliasing is provided
   in the examples. It is recommended to provide some way to provide
   STONITH (Shoot The Other Node In The Head) capability in the
   script. Other common methods of doing the failover and getting DB
   traffic diverted to the newly promoted master are the switching of
   PgBouncer (or other poolers) traffic, or changes in PL/Proxy configuration.

   You should try to run the failover script you provide with pglookout's
   user privileges to see that it does indeed work.

5. Now copy the same ``pglookout.json`` configuration to the standby
   and possible observer nodes but you need to edit the configuration
   on the other nodes so that the ``own_db`` configuration
   variable matches the ``remote_conns`` key of the node.
   For observer nodes, you can leave it as an empty '' value, since they
   don't have a DB of their own.

Other possible configuration settings are covered in more detail
under the `Configuration keys`_ section of this README.

6. If all has been set up correctly up to this point, pglookout should
   now be ready to be started.


Alert files
===========

Alert files are created whenever an error condition that requires
human intervention to solve. You're recommended to add checks for the
existence of these files to your alerting system.

``authentication_error``

There has been a problem in the authentication of at least one of the
PostgreSQL connections. This usually denotes either a wrong
username/password or incorrect ``pg_hba.conf`` settings.

``multiple_master_warning``

This alert file is created when multiple masters are detected in the
same cluster.

``replication_delay_warning``

This alert file is created when replication delay goes over the set
warning limit. (this is warning is an exception to the rule that human
intervention is required. It is only meant as an informative heads up
alert that a failover may be imminent. In case the replication delay
drops below the warning threshold again, the alert will be removed)

``failover_has_happened``

This alert file is created whenever the failover command has been
issued.


General notes
=============

If correctly installed, pglookout comes with two executables,
``pglookout`` and ``pglookout_current_master`` that both take as
their arguments the path to the node's JSON configuration file.

``pglookout`` is the main process that should be run under systemd or
supervisord.

``pglookout_current_master`` is a helper that will simply parse the
state file and return which node is the current master.

While pglookout is running it may be useful to read the JSON state
file that exists where ``json_state_file_path`` points. The JSON
state file is human readable and should give an understandable
description of the current state of the cluster which is under monitoring.


Configuration keys
==================

``autofollow`` (default ``false``)

Do you want pglookout to try to start following the new master. Useful
in scenarios where you have a master and two standbys, master dies
and another standby is promoted. This will allow the remaining standby
to start following the new master. Requires ``pg_data_directory``, ``pg_start_command``
and ``pg_stop_command`` configuration keys to be set.

``db_poll_interval`` (default ``5.0``)

Interval on how often should the connections defined in remote_conns
be polled for information on DB replication state.

``remote_conns`` (default ``{}``)

PG database connection strings that the pglookout process should monitor.
Keys of the object should be names of the remotes and values must be valid
PostgreSQL connection strings or connection info objects.

``primary_conninfo_template``

Connection string or connection info object template to use when setting a new
primary_conninfo value for recovery.conf after a failover has happened.  Any
provided hostname and database name in the template is ignored and they are
replaced with a replication connection to the new master node.

Required when ``autofollow`` is true.

``observers`` (default ``{}``)

This object contains key value pairs like ``{"1.2.3.4":
"http://2.3.4.5:15000"}``.  They are used to determine the location of
pglookout observer processes.  Observers are processes that don't take any
actions, but simply give a third party viewpoint on the state of the
cluster.  Useful especially during net splits.

``poll_observers_on_warning_only`` (default ``False``)

this allows observers to be polled only when replication lag is over
``warning_replication_time_lag``

``http_address`` (default ``""``)

HTTP webserver address, by default pglookout binds to all interfaces.

``http_port`` (default ``15000``)

HTTP webserver port.

``replication_state_check_interval`` (default ``10.0``)

How often should pglookout check the replication state in order to
make decisions on should the node be promoted.

``failover_sleep_time`` (default ``0.0``)

Time to sleep after a failover command has been issued.

``maintenance_mode_file`` (default ``"/tmp/pglookout_maintenance_mode_file"``)

If a file exists in this location, this node will not be considered
for promotion to master.

``missing_master_from_config_timeout`` (default ``15``)

In seconds the amount of time before we do a failover decision if a
previously existing master has been removed from the config file and
we have gotten a SIGHUP.

``alert_file_dir`` (default ``os.getcwd()``)

Directory in which alert files for replication warning and failover
are created.

``json_state_file_path`` (default ``"/tmp/pglookout_state.json"``)

Location of a JSON state file which describes the state of the
pglookout process.

``max_failover_replication_time_lag`` (default ``120.0``)

Replication time lag after which failover_command will be executed and a
failover_has_happened file will be created.

``warning_replication_time_lag`` (default ``30.0``)

Replication time lag at which point to execute
over_warning_limit_command and to create a warning file.

``failover_command`` (default ``""``)

Shell command to execute in case the node has deemed itself in need of promotion

``known_gone_nodes`` (default ``[]``)

Lists nodes that are explicitly known to have left the cluster. If old master is
removed in a controlled manner it should be added to this list to ensure there's
no extra delay when making promotion decision.

``never_promote_these_nodes`` (default ``[]``)

Lists the nodes that will never be considered valid for promotion. As
in if you have master m which fails and standby a and b. b is ahead but is listed
in never_promote_these_nodes, a will be promoted.

``over_warning_limit_command`` (default ``null``)

Shell command to be executed once replication lag is warning_replication_time_lag

``own_db``

The key of the entry in ``remote_conns`` that matches this node.

``log_level`` (default ``"INFO"``)

Determines log level of pglookout.

``pg_data_directory`` (default ``"/var/lib/pgsql/data"``)

PG data directory that needs to be set when autofollow has been turned on.
Note that pglookout needs to have the permissions to write there. (specifically
to recovery.conf)

``pg_start_command`` (default ``""``)

Command to start a PostgreSQL process on a node which has autofollow set to
true. Usually something like "sudo systemctl start postgresql".

``pg_stop_command`` (default ``""``)

Command to stop a PostgreSQL process on a node which has autofollow set to
true. Usually something like "sudo systemctl start postgresql".

``syslog`` (default ``false``)

Determines whether syslog logging should be turned on or not.

``syslog_address`` (default ``"/dev/log"``)

Determines syslog address to use in logging (requires syslog to be
true as well)

``syslog_facility`` (default ``"local2"``)

Determines syslog log facility. (requires syslog to be true as well)

``statsd`` (default: disabled)

Enables metrics sending to a statsd daemon that supports the StatsD /
Telegraf syntax with tags.

The value is a JSON object::

  {
      "host": "<statsd address>",
      "port": "<statsd port>",
      "tags": {
          "<tag>": "<value>"
      }
  }

The ``tags`` setting can be used to enter optional tag values for the metrics.

Metrics sending follows the `Telegraf spec`_.

.. _`Telegraf spec`: https://github.com/influxdata/telegraf/tree/master/plugins/inputs/statsd


License
=======

pglookout is licensed under the Apache License, Version 2.0. Full license
text is available in the ``LICENSE`` file and at
http://www.apache.org/licenses/LICENSE-2.0.txt


Credits
=======

pglookout was created by Hannu Valtonen <hannu.valtonen@ohmu.fi> for
F-Secure_ and is now maintained by `Ohmu Ltd`_ hackers and `Aiven Cloud
Database`_ developers <pglookout@ohmu.fi>.

.. _`F-Secure`: https://www.f-secure.com/
.. _`Ohmu Ltd`: https://ohmu.fi/
.. _`Aiven Cloud Database`: https://aiven.io/

Recent contributors are listed on the GitHub project page,
https://github.com/aiven/pglookout/graphs/contributors


Contact
=======

Bug reports and patches are very welcome, please post them as GitHub issues
and pull requests at https://github.com/aiven/pglookout .  Any possible
vulnerabilities or other serious issues should be reported directly to the
maintainers <pglookout@ohmu.fi>.
