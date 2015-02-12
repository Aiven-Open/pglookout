pglookout |BuildStatus|_
========================

.. |BuildStatus| image:: https://travis-ci.org/ohmu/pglookout.png?branch=master
.. _BuildStatus: https://travis-ci.org/ohmu/pglookout

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


Building
========

To build an installation package for your distribution, go to the root
directory of a pglookout Git checkout and then run:

Debian::

  make deb

This will produce a .deb package into the parent directory of the Git checkout.

Fedora::

  make rpm

This will produce a .rpm package usually into ~/rpmbuild/RPMS/noarch/ .

Python/Other::

  python setup.py bdist_egg

This will produce an egg file into a dist directory within the same folder.

Installation
============

To install it run as root:

Debian::

  dpkg -i ../pglookout*.deb

Fedora::

  rpm -Uvh ~/rpmbuild/RPMS/noarch/pglookout*

On Fedora it is recommended to simply run pglookout under systemd::

  systemctl enable pglookout.service

and eventually after the setup section, you can just run::

  systemctl start pglookout.service

Python/Other::

  easy_install dist/pglookout-1.1.0-py2.7.egg

On Debian/Other systems it is recommended that you run pglookout within
a supervisord (http://supervisord.org) Process control system.
(see examples directory for an example supervisord.conf)


Setup
=====

After this you need to create a suitable JSON configuration file for your
installation.

1. Create a suitable PostgreSQL user account for pglookout::

     CREATE USER pglookout PASSWORD 'putyourpasswordhere';

2. Edit the local ``pg_hba.conf`` to allow access for the newly
   created account to the ``postgres`` (or other suitable database of your choice)
   from the master, slave and possible observer nodes. While pglookout will
   only need to run a few builtin functions within the database, it is
   still recommended to setup a separate empty database for this
   use. Remember to reload the configuration with either::

     SELECT pg_reload_conf();

   or by sending directly a ``SIGHUP`` to the PostgreSQL postmaster process.

3. Fill in the created user account and master/slave/observer
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
   user priviledges to see that it does indeed work.

5. Now copy the same ``pglookout.json`` configuration to the slave
   and possible observer nodes but you need to edit the configuration
   on the other nodes so that the ``own_db`` configuration
   variable matches the node's address given in the ``remote_conns`` as the key.
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

``db_poll_interval`` (default ``5.0``)

Interval on how often should the connections defined in remote_conns
be polled for information on DB replication state.

``remote_conns`` (default ``{}``)

PG database connection strings that the pglookout process should monitor.

``observers`` (default ``{}``)

This object contains key value pairs like ``{"1.2.3.4":
"http://2.3.4.5:15000"}``.  They are used to determine the location of
pglookout observer processes.  Observers are processes that don't take any
actions, but simply give a third party viewpoint on the state of the
cluster.  Useful especially during net splits.

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

``never_promote_these_nodes`` (default ``[]``)

Lists the nodes that will never be considered valid for promotion. As
in if you have master m which fails and standby a and b. b is ahead but is listed
in never_promote_these_nodes, a will be promoted.

``over_warning_limit_command`` (default ``null``)

Shell command to be executed once replication lag is warning_replication_time_lag

``own_db``

This is how pglookout determines which one of the dbs listed, is it's
own.

``log_level`` (default ``"INFO"``)

Determines log level of pglookout.

``syslog`` (default ``false``)

Determines whether syslog logging should be turned on or not.

``syslog_address`` (default ``"/dev/log"``)

Determines syslog address to use in logging (requires syslog to be
true as well)

``syslog_facility`` (default ``"local2"``)

Determines syslog log facility. (requires syslog to be true as well)


Vulnerability reporting
=======================

If you would like to report a vulnerability or have a security concern on
pglookout, please contact hannu.valtonen@ohmu.fi


Copyright
=========

Copyright (C) 2015 Ohmu Ltd
Copyright (C) 2014 F-Secure


License
=======

pglookout is licensed under the Apache License, Version 2.0. Full license
text is available in the ``LICENSE`` file and at
http://www.apache.org/licenses/LICENSE-2.0.txt
