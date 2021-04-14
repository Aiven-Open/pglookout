"""
pglookout - replication monitoring and failover daemon

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from . import logutil, statsd, version
from .cluster_monitor import ClusterMonitor
from .common import convert_xlog_location_to_offset, parse_iso_datetime, get_iso_timestamp
from .pgutil import (
    create_connection_string, get_connection_info, get_connection_info_from_config_line)
from .webserver import WebServer
from distutils.version import LooseVersion
from psycopg2.extensions import adapt
from queue import Empty, Queue
import argparse
import copy
import datetime
import json
import logging
import logging.handlers
import os
import signal
import socket
import subprocess
import sys
import time


class PgLookout:
    def __init__(self, config_path):
        self.log = logging.getLogger("pglookout")
        self.stats = None
        self.running = True
        self.replication_lag_over_warning_limit = False

        self.config_path = config_path
        self.config = {}
        self.log_level = "DEBUG"

        self.connected_master_nodes = {}
        self.disconnected_master_nodes = {}
        self.connected_observer_nodes = {}
        self.disconnected_observer_nodes = {}
        self.replication_catchup_timeout = None
        self.replication_lag_warning_boundary = None
        self.replication_lag_failover_timeout = None
        self.missing_master_from_config_timeout = None
        self.own_db = None
        self.current_master = None
        self.failover_command = None
        self.known_gone_nodes = None
        self.over_warning_limit_command = None
        self.never_promote_these_nodes = None
        self.primary_conninfo_template = None
        self.cluster_monitor = None
        self.syslog_handler = None
        self.cluster_nodes_change_time = time.monotonic()
        self.cluster_monitor_check_queue = Queue()
        self.failover_decision_queue = Queue()
        self.observer_state_newer_than = datetime.datetime.min
        self.load_config()

        signal.signal(signal.SIGHUP, self.load_config)
        signal.signal(signal.SIGINT, self.quit)
        signal.signal(signal.SIGTERM, self.quit)

        self.cluster_state = {}
        self.observer_state = {}
        self.overall_state = {"db_nodes": self.cluster_state, "observer_nodes": self.observer_state,
                              "current_master": self.current_master,
                              "replication_lag_over_warning": self.replication_lag_over_warning_limit}

        self.cluster_monitor = ClusterMonitor(
            config=self.config,
            cluster_state=self.cluster_state,
            observer_state=self.observer_state,
            create_alert_file=self.create_alert_file,
            cluster_monitor_check_queue=self.cluster_monitor_check_queue,
            failover_decision_queue=self.failover_decision_queue,
            is_replication_lag_over_warning_limit=self.is_replication_lag_over_warning_limit,
            stats=self.stats,
        )
        # cluster_monitor doesn't exist at the time of reading the config initially
        self.cluster_monitor.log.setLevel(self.log_level)
        self.webserver = WebServer(self.config, self.cluster_state, self.cluster_monitor_check_queue)

        logutil.notify_systemd("READY=1")
        self.log.info("PGLookout initialized, local hostname: %r, own_db: %r, cwd: %r",
                      socket.gethostname(), self.own_db, os.getcwd())

    def quit(self, _signal=None, _frame=None):
        self.log.warning("Quitting, signal: %r, frame: %r", _signal, _frame)
        self.cluster_monitor.running = False
        self.running = False
        self.webserver.close()

    def load_config(self, _signal=None, _frame=None):
        self.log.debug("Loading JSON config from: %r, signal: %r, frame: %r",
                       self.config_path, _signal, _frame)

        previous_remote_conns = self.config.get("remote_conns")
        try:
            with open(self.config_path) as fp:
                self.config = json.load(fp)
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Invalid JSON config, exiting")
            self.stats.unexpected_exception(ex, where="load_config")
            sys.exit(1)

        # statsd settings may have changed
        stats = self.config.get("statsd", {})
        self.stats = statsd.StatsClient(host=stats.get("host"), port=stats.get("port"),
                                        tags=stats.get("tags"))

        if previous_remote_conns != self.config.get("remote_conns"):
            self.cluster_nodes_change_time = time.monotonic()

        if self.config.get("autofollow"):
            try:
                self.primary_conninfo_template = get_connection_info(self.config["primary_conninfo_template"])
            except (KeyError, ValueError):
                self.log.exception("Invalid or missing primary_conninfo_template; not enabling autofollow")
                self.config["autofollow"] = False

        if self.cluster_monitor:
            self.cluster_monitor.config = copy.deepcopy(self.config)

        if self.config.get("syslog") and not self.syslog_handler:
            self.syslog_handler = logutil.set_syslog_handler(
                address=self.config.get("syslog_address", "/dev/log"),
                facility=self.config.get("syslog_facility", "local2"),
                logger=logging.getLogger(),
            )
        self.own_db = self.config.get("own_db")

        log_level_name = self.config.get("log_level", "DEBUG")
        self.log_level = getattr(logging, log_level_name)
        try:
            self.log.setLevel(self.log_level)
            if self.cluster_monitor:
                self.cluster_monitor.log.setLevel(self.log_level)
        except ValueError:
            print("Problem setting log level %r" % self.log_level)
            self.log.exception("Problem with log_level: %r", self.log_level)
        self.known_gone_nodes = self.config.get("known_gone_nodes", [])
        self.never_promote_these_nodes = self.config.get("never_promote_these_nodes", [])
        # we need the failover_command to be converted into subprocess [] format
        self.failover_command = self.config.get("failover_command", "").split()
        self.over_warning_limit_command = self.config.get("over_warning_limit_command")
        self.replication_lag_warning_boundary = self.config.get("warning_replication_time_lag", 30.0)
        self.replication_lag_failover_timeout = self.config.get("max_failover_replication_time_lag", 120.0)
        self.replication_catchup_timeout = self.config.get("replication_catchup_timeout", 300.0)
        self.missing_master_from_config_timeout = self.config.get("missing_master_from_config_timeout", 15.0)

        if self.replication_lag_warning_boundary >= self.replication_lag_failover_timeout:
            msg = "Replication lag warning boundary (%s) is not lower than its failover timeout (%s)"
            self.log.warning(msg, self.replication_lag_warning_boundary, self.replication_lag_failover_timeout)
            if self.replication_lag_warning_boundary > self.replication_lag_failover_timeout:
                self.replication_lag_warning_boundary = self.replication_lag_failover_timeout
                msg = "Replication lag warning boundary set to %s"
                self.log.warning(msg, self.replication_lag_warning_boundary)
        self.log.debug("Loaded config: %r from: %r", self.config, self.config_path)
        self.cluster_monitor_check_queue.put("new config came, recheck")

    def write_cluster_state_to_json_file(self):
        """Periodically write a JSON state file to disk"""
        start_time = time.monotonic()
        state_file_path = self.config.get("json_state_file_path", "/tmp/pglookout_state.json")
        try:
            self.overall_state = {"db_nodes": self.cluster_state, "observer_nodes": self.observer_state,
                                  "current_master": self.current_master}
            json_to_dump = json.dumps(self.overall_state, indent=4)
            self.log.debug("Writing JSON state file to: %r, file_size: %r", state_file_path, len(json_to_dump))
            with open(state_file_path + ".tmp", "w") as fp:
                fp.write(json_to_dump)
            os.rename(state_file_path + ".tmp", state_file_path)
            self.log.debug("Wrote JSON state file to disk, took %.4fs", time.monotonic() - start_time)
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Problem in writing JSON: %r file to disk, took %.4fs",
                               self.overall_state, time.monotonic() - start_time)
            self.stats.unexpected_exception(ex, where="write_cluster_state_to_json_file")

    def create_node_map(self, cluster_state, observer_state):
        standby_nodes, master_node, master_instance = {}, None, None
        connected_master_nodes, disconnected_master_nodes = {}, {}
        connected_observer_nodes, disconnected_observer_nodes = {}, {}
        self.log.debug("Creating node map out of cluster_state: %r and observer_state: %r",
                       cluster_state, observer_state)
        for instance, state in cluster_state.items():
            if 'pg_is_in_recovery' in state:
                if state['pg_is_in_recovery']:
                    standby_nodes[instance] = state
                elif state['connection']:
                    connected_master_nodes[instance] = state
                elif not state['connection']:
                    disconnected_master_nodes[instance] = state
            else:
                self.log.debug("No knowledge on instance: %r state: %r of whether it's in recovery or not", instance, state)

        for observer_name, state in observer_state.items():
            connected = state.get("connection", False)
            if connected:
                connected_observer_nodes[observer_name] = state.get("fetch_time")
            else:
                disconnected_observer_nodes[observer_name] = state.get("fetch_time")
            for instance, db_state in state.items():
                if instance not in cluster_state:
                    # A single observer can observe multiple different replication clusters.
                    # Ignore data on nodes that don't belong in our own cluster
                    self.log.debug("Ignoring instance: %r since it does not belong into our own replication cluster",
                                   instance)
                    continue
                if isinstance(db_state, dict):  # other keys are "connection" and "fetch_time"
                    own_fetch_time = parse_iso_datetime(cluster_state[instance]["fetch_time"])
                    observer_fetch_time = parse_iso_datetime(db_state['fetch_time'])
                    self.log.debug("observer_name: %r, instance: %r, state: %r, observer_fetch_time: %r",
                                   observer_name, instance, db_state, observer_fetch_time)
                    if 'pg_is_in_recovery' in db_state:
                        if db_state['pg_is_in_recovery']:
                            # we always trust ourselves the most for localhost, and
                            # in case we are actually connected to the other node
                            if observer_fetch_time >= own_fetch_time and instance != self.own_db:
                                if instance not in standby_nodes or standby_nodes[instance]["connection"] is False:
                                    standby_nodes[instance] = db_state
                        else:
                            master_node = connected_master_nodes.get(instance, {})
                            connected = master_node.get("connection", False)
                            self.log.debug("Observer: %r sees %r as master, we see: %r, same_master: %r, connection: %r",
                                           observer_name, instance, self.current_master, instance == self.current_master,
                                           db_state.get('connection'))
                            if self.within_dbpoll_time(observer_fetch_time, own_fetch_time) and instance != self.own_db:
                                if connected or db_state["connection"]:
                                    connected_master_nodes[instance] = db_state
                                else:
                                    disconnected_master_nodes[instance] = db_state
                    else:
                        self.log.warning("No knowledge on %r %r from observer: %r is in recovery",
                                         instance, db_state, observer_name)

        self.connected_master_nodes = connected_master_nodes
        self.disconnected_master_nodes = disconnected_master_nodes
        self.connected_observer_nodes = connected_observer_nodes
        self.disconnected_observer_nodes = disconnected_observer_nodes

        if not self.connected_master_nodes:
            self.log.warning("No known master node, disconnected masters: %r", list(disconnected_master_nodes))
            if disconnected_master_nodes:
                master_instance, master_node = list(disconnected_master_nodes.items())[0]
        elif len(self.connected_master_nodes) == 1:
            master_instance, master_node = list(connected_master_nodes.items())[0]
            if disconnected_master_nodes:
                self.log.warning("Picked %r as master since %r are in a disconnected state",
                                 master_instance, disconnected_master_nodes)
        else:
            self.create_alert_file("multiple_master_warning")
            self.log.error("More than one master node connected_master_nodes: %r, disconnected_master_nodes: %r",
                           connected_master_nodes, disconnected_master_nodes)

        return master_instance, master_node, standby_nodes

    def is_restoring_or_catching_up_normally(self, state):
        """
        Return True if node is still in the replication catchup phase and
        replication lag alerts/metrics should not yet be generated.
        """
        replication_start_time = state.get("replication_start_time")
        min_lag = state.get("min_replication_time_lag", self.replication_lag_warning_boundary)
        if replication_start_time and time.monotonic() - replication_start_time > self.replication_catchup_timeout:
            # we've been replicating for too long and should have caught up with the master already
            return False
        if not state.get("pg_last_xlog_receive_location"):
            # node has not received anything from the master yet
            return True
        if min_lag >= self.replication_lag_warning_boundary:
            # node is catching up the master and has not gotten close enough yet
            return True

        # node has caught up with the master so we should be in sync
        return False

    def emit_stats(self, state):
        if self.is_restoring_or_catching_up_normally(state):
            # do not emit misleading lag stats during catchup at restore
            return

        replication_time_lag = state.get("replication_time_lag")
        if replication_time_lag is not None:
            self.stats.gauge("pg.replication_lag", replication_time_lag)

    def is_master_observer_new_enough(self, observer_state):
        if not self.replication_lag_over_warning_limit:
            return True
        if not self.current_master or self.current_master not in self.config.get("observers", {}):
            self.log.warning("Replication lag is over warning limit, but"
                             " current master (%s) is not configured to be polled via observers", self.current_master)
            return True
        db_poll_intervals = datetime.timedelta(seconds=5 * self.config.get("db_poll_interval", 5.0))
        now = datetime.datetime.utcnow()
        if (now - self.observer_state_newer_than) < db_poll_intervals:
            self.log.warning("Replication lag is over warning limit, but"
                             " not waiting for observers to be polled because 5 db_poll_intervals have passed")
            return True
        if self.current_master not in observer_state:
            self.log.warning("Replication lag is over warning limit, but observer for master (%s)"
                             " has not been polled yet", self.current_master)
            return False
        fetch_time = parse_iso_datetime(observer_state[self.current_master]["fetch_time"])
        if fetch_time < self.observer_state_newer_than:
            self.log.warning("Replication lag is over warning limit, but observer's data for"
                             " master  is stale, older than %r", self.observer_state_newer_than)
            return False
        return True

    def check_cluster_state(self):
        master_node = None
        cluster_state = copy.deepcopy(self.cluster_state)
        observer_state = copy.deepcopy(self.observer_state)
        configured_node_count = len(self.config.get("remote_conns", {}))
        if not cluster_state or len(cluster_state) != configured_node_count:
            self.log.warning("No cluster state: %r, probably still starting up, node_count: %r, configured node_count: %r",
                             cluster_state, len(cluster_state), configured_node_count)
            return

        if self.config.get("poll_observers_on_warning_only") and not self.is_master_observer_new_enough(observer_state):
            self.log.warning("observer data is not good enough, skipping check")
            return

        master_instance, master_node, standby_nodes = self.create_node_map(cluster_state, observer_state)

        if master_instance and master_instance != self.current_master:
            self.log.info("New master node detected: old: %r new: %r: %r", self.current_master, master_instance, master_node)
            self.current_master = master_instance
            if self.own_db and self.own_db != master_instance and self.config.get("autofollow"):
                self.start_following_new_master(master_instance)

        own_state = self.cluster_state.get(self.own_db)

        observer_info = ",".join(observer_state) or "no"
        if self.own_db:  # Emit stats if we're a non-observer node
            self.emit_stats(own_state)
        else:  # We're an observer ourselves, grab the IP address from HTTP server address
            observer_info = self.config.get("http_address", observer_info)

        standby_info = ",".join(standby_nodes) or "no"
        self.log.debug("Cluster has %s standbys, %s observers and %s as master, own_db: %r, own_state: %r",
                       standby_info, observer_info, self.current_master, self.own_db, own_state or "observer")

        if self.own_db:
            if self.own_db == self.current_master:
                # We are the master of this cluster, nothing to do
                self.log.debug("We %r: %r are still the master node: %r of this cluster, nothing to do.",
                               self.own_db, own_state, master_node)
                return
            if not standby_nodes:
                self.log.warning("No standby nodes set, master node: %r", master_node)
                return
            self.consider_failover(own_state, master_node, standby_nodes)

    def consider_failover(self, own_state, master_node, standby_nodes):
        if not master_node:
            # no master node at all in the cluster?
            self.log.warning("No master node in cluster, %r standby nodes exist, "
                             "%.2f seconds since last cluster config update, failover timeout set "
                             "to %r seconds, previous master: %r",
                             len(standby_nodes), time.monotonic() - self.cluster_nodes_change_time,
                             self.replication_lag_failover_timeout, self.current_master)
            if self.current_master:
                self.cluster_monitor_check_queue.put("Master is missing, ask for immediate state check")
                master_known_to_be_gone = self.current_master in self.known_gone_nodes
                now = time.monotonic()
                config_timeout_exceeded = (now - self.cluster_nodes_change_time) >= self.missing_master_from_config_timeout
                if master_known_to_be_gone or config_timeout_exceeded:
                    # we've seen a master at some point in time, but now it's
                    # missing, perform an immediate failover to promote one of
                    # the standbys
                    self.log.warning("Performing failover decision because existing master node "
                                     "disappeared from configuration")
                    self.do_failover_decision(own_state, standby_nodes)
                    return
            else:
                # we've never seen a master and more than failover_timeout
                # seconds have passed since last config load (and start of
                # connection attempts to other nodes); perform failover
                self.log.warning("Performing failover decision because no master node was seen in cluster before timeout")
                self.do_failover_decision(own_state, standby_nodes)
                return
        self.check_replication_lag(own_state, standby_nodes)

    def is_replication_lag_over_warning_limit(self):
        return self.replication_lag_over_warning_limit

    def check_replication_lag(self, own_state, standby_nodes):
        if self.is_restoring_or_catching_up_normally(own_state):
            # do not raise alerts during catchup at restore
            return

        replication_lag = own_state.get('replication_time_lag')
        if not replication_lag:
            self.log.warning("No replication lag set in own node state: %r", own_state)
            return
        if replication_lag >= self.replication_lag_warning_boundary:
            self.log.warning("Replication time lag has grown to: %r which is over WARNING boundary: %r, %r",
                             replication_lag, self.replication_lag_warning_boundary,
                             self.replication_lag_over_warning_limit)
            if not self.replication_lag_over_warning_limit:  # we just went over the boundary
                self.replication_lag_over_warning_limit = True
                if self.config.get("poll_observers_on_warning_only"):
                    self.observer_state_newer_than = datetime.datetime.utcnow()
                self.create_alert_file("replication_delay_warning")
                if self.over_warning_limit_command:
                    self.log.warning("Executing over_warning_limit_command: %r", self.over_warning_limit_command)
                    return_code = self.execute_external_command(self.over_warning_limit_command)
                    self.log.warning("Executed over_warning_limit_command: %r, return_code: %r",
                                     self.over_warning_limit_command, return_code)
                else:
                    self.log.warning("No over_warning_limit_command set")
                # force looping one more time since we just passed the warning limit
                return
        elif self.replication_lag_over_warning_limit:
            self.replication_lag_over_warning_limit = False
            self.delete_alert_file("replication_delay_warning")
            self.observer_state_newer_than = datetime.datetime.min

        if replication_lag >= self.replication_lag_failover_timeout:
            self.log.warning("Replication time lag has grown to: %r which is over CRITICAL boundary: %r"
                             ", checking if we need to failover",
                             replication_lag, self.replication_lag_failover_timeout)
            self.do_failover_decision(own_state, standby_nodes)
        else:
            self.log.debug("Replication lag was: %r, other nodes status was: %r", replication_lag, standby_nodes)

    def get_replication_positions(self, standby_nodes):
        self.log.debug("Getting replication positions from: %r", standby_nodes)
        known_replication_positions = {}
        for instance, node_state in standby_nodes.items():
            now = datetime.datetime.utcnow()
            if node_state['connection'] and \
                now - parse_iso_datetime(node_state['fetch_time']) < datetime.timedelta(seconds=20) and \
                instance not in self.never_promote_these_nodes:  # noqa # pylint: disable=line-too-long
                # use pg_last_xlog_receive_location if it's available,
                # otherwise fall back to pg_last_xlog_replay_location but
                # note that both of them can be None.  We prefer
                # receive_location over replay_location as some nodes may
                # not yet have replayed everything they've received, but
                # also consider the replay location in case receive_location
                # is empty as a node that has been brought up from backups
                # without ever connecting to a master will not have an empty
                # pg_last_xlog_receive_location
                lsn = node_state['pg_last_xlog_receive_location'] or node_state['pg_last_xlog_replay_location']
                wal_pos = convert_xlog_location_to_offset(lsn) if lsn else 0
                known_replication_positions.setdefault(wal_pos, set()).add(instance)
        return known_replication_positions

    def _been_in_contact_with_master_within_failover_timeout(self):
        # no need to do anything here if there are no disconnected masters
        if self.disconnected_master_nodes:
            disconnected_master_node = list(self.disconnected_master_nodes.values())[0]
            db_time = disconnected_master_node.get('db_time', get_iso_timestamp()) or get_iso_timestamp()
            time_since_last_contact = datetime.datetime.utcnow() - parse_iso_datetime(db_time)
            if time_since_last_contact < datetime.timedelta(seconds=self.replication_lag_failover_timeout):
                self.log.debug("We've had contact with master: %r at: %r within the last %.2fs, not failing over",
                               disconnected_master_node, db_time, time_since_last_contact.total_seconds())
                return True
        return False

    def do_failover_decision(self, own_state, standby_nodes):
        if self.connected_master_nodes or self._been_in_contact_with_master_within_failover_timeout():
            self.log.warning("We still have some connected masters: %r, not failing over", self.connected_master_nodes)
            return

        known_replication_positions = self.get_replication_positions(standby_nodes)
        if not known_replication_positions:
            self.log.warning("No known replication positions, canceling failover consideration")
            return
        # If there are multiple nodes with the same replication positions pick the one with the "highest" name
        # to make sure pglookouts running on all standbys make the same decision.  The rationale for picking
        # the "highest" node is that there's no obvious way for pglookout to decide which of the nodes is
        # "best" beyond looking at replication positions, but picking the highest id supports environments
        # where nodes are assigned identifiers from an incrementing sequence identifiers and where we want to
        # promote the latest and greatest node.  In static environments node identifiers can be priority
        # numbers, with the highest number being the one that should be preferred.
        furthest_along_instance = max(known_replication_positions[max(known_replication_positions)])
        self.log.warning("Node that is furthest along is: %r, all replication positions were: %r",
                         furthest_along_instance, sorted(known_replication_positions))
        total_observers = len(self.connected_observer_nodes) + len(self.disconnected_observer_nodes)
        # +1 in the calculation comes from the master node
        total_amount_of_nodes = len(standby_nodes) + 1 - len(self.never_promote_these_nodes) + total_observers
        size_of_needed_majority = total_amount_of_nodes * 0.5
        amount_of_known_replication_positions = 0
        for known_replication_position in known_replication_positions.values():
            amount_of_known_replication_positions += len(known_replication_position)
        size_of_known_state = amount_of_known_replication_positions + len(self.connected_observer_nodes)
        self.log.debug("Size of known state: %.2f, needed majority: %r, %r/%r", size_of_known_state,
                       size_of_needed_majority, amount_of_known_replication_positions, int(total_amount_of_nodes))

        if standby_nodes[furthest_along_instance] == own_state:
            if self.check_for_maintenance_mode_file():
                self.log.warning("Canceling failover even though we were the node the furthest along, since "
                                 "this node has an existing maintenance_mode_file: %r",
                                 self.config.get("maintenance_mode_file", "/tmp/pglookout_maintenance_mode_file"))
            elif self.own_db in self.never_promote_these_nodes:
                self.log.warning("Not doing a failover even though we were the node the furthest along, since this node: %r"
                                 " should never be promoted to master", self.own_db)
            elif size_of_known_state < size_of_needed_majority:
                self.log.warning("Not doing a failover even though we were the node the furthest along, since we aren't "
                                 "aware of the states of enough of the other nodes")
            else:
                start_time = time.monotonic()
                self.log.warning("We will now do a failover to ourselves since we were the instance furthest along")
                return_code = self.execute_external_command(self.failover_command)
                self.log.warning("Executed failover command: %r, return_code: %r, took: %.2fs",
                                 self.failover_command, return_code, time.monotonic() - start_time)
                self.create_alert_file("failover_has_happened")
                # Sleep for failover time to give the DB time to restart in promotion mode
                # You want to use this if the failover command is not one that blocks until
                # the db has restarted
                time.sleep(self.config.get("failover_sleep_time", 0.0))
                if return_code == 0:
                    self.replication_lag_over_warning_limit = False
                    self.delete_alert_file("replication_delay_warning")
        else:
            self.log.warning("Nothing to do since node: %r is the furthest along", furthest_along_instance)

    def modify_recovery_conf_to_point_at_new_master(self, new_master_instance):
        with open(os.path.join(self.config.get("pg_data_directory"), "PG_VERSION"), "r") as fp:
            pg_version = fp.read().strip()

        if LooseVersion(pg_version) >= "12":
            recovery_conf_filename = "postgresql.auto.conf"
        else:
            recovery_conf_filename = "recovery.conf"

        path_to_recovery_conf = os.path.join(self.config.get("pg_data_directory"), recovery_conf_filename)
        with open(path_to_recovery_conf, "r") as fp:
            old_conf = fp.read().splitlines()
        has_recovery_target_timeline = False
        new_conf = []
        old_conn_info = None
        for line in old_conf:
            if line.startswith("recovery_target_timeline"):
                has_recovery_target_timeline = True
            if line.startswith("primary_conninfo"):
                # grab previous entry: strip surrounding quotes and replace two quotes with one
                try:
                    old_conn_info = get_connection_info_from_config_line(line)
                except ValueError:
                    self.log.exception("failed to parse previous %r, ignoring", line)
                continue  # skip this line
            new_conf.append(line)

        # If has_recovery_target_timeline is set and old_conn_info matches
        # new info we don't have to do anything
        new_conn_info = get_connection_info(self.primary_conninfo_template)
        master_instance_conn_info = get_connection_info(self.config["remote_conns"][new_master_instance])
        assert "host" in master_instance_conn_info
        new_conn_info["host"] = master_instance_conn_info["host"]
        if "port" in master_instance_conn_info:
            new_conn_info["port"] = master_instance_conn_info["port"]
        if new_conn_info == old_conn_info:
            self.log.debug("recovery.conf already contains conninfo matching %r, not updating", new_master_instance)
            return False
        # Otherwise we append the new primary_conninfo
        new_conf.append("primary_conninfo = {0}".format(adapt(create_connection_string(new_conn_info))))
        # The timeline of the recovery.conf will require a higher timeline target
        if not has_recovery_target_timeline:
            new_conf.append("recovery_target_timeline = 'latest'")
        # prepend our tag
        new_conf.insert(0,
                        "# pglookout updated primary_conninfo for instance {0} at {1}"
                        .format(new_master_instance, get_iso_timestamp()))
        # Replace old recovery.conf with a fresh copy
        with open(path_to_recovery_conf + "_temp", "w") as fp:
            fp.write("\n".join(new_conf) + "\n")

        os.rename(path_to_recovery_conf + "_temp", path_to_recovery_conf)
        return True

    def start_following_new_master(self, new_master_instance):
        start_time = time.monotonic()
        updated_config = self.modify_recovery_conf_to_point_at_new_master(new_master_instance)
        if not updated_config:
            self.log.info("Already following master %r, no need to start following it again", new_master_instance)
            return
        start_command = self.config.get("pg_start_command", "").split()
        stop_command = self.config.get("pg_stop_command", "").split()
        self.log.info("Starting to follow new master %r, modified recovery.conf and restarting PostgreSQL"
                      "; pg_start_command %r; pg_stop_command %r",
                      new_master_instance, start_command, stop_command)
        self.execute_external_command(stop_command)
        self.execute_external_command(start_command)
        self.log.info("Started following new master %r, took: %.2fs", new_master_instance, time.monotonic() - start_time)

    def execute_external_command(self, command):
        self.log.warning("Executing external command: %r", command)
        return_code, output = 0, ""
        try:
            output = subprocess.check_call(command)
        except subprocess.CalledProcessError as err:
            self.log.exception("Problem with executing: %r, return_code: %r, output: %r",
                               command, err.returncode, err.output)
            self.stats.unexpected_exception(err, where="execute_external_command")
            return_code = err.returncode  # pylint: disable=no-member
        self.log.warning("Executed external command: %r, output: %r", return_code, output)
        return return_code

    def check_for_maintenance_mode_file(self):
        return os.path.exists(self.config.get("maintenance_mode_file", "/tmp/pglookout_maintenance_mode_file"))

    def create_alert_file(self, filename):
        try:
            filepath = os.path.join(self.config.get("alert_file_dir", os.getcwd()), filename)
            self.log.debug("Creating alert file: %r", filepath)
            with open(filepath, "w") as fp:
                fp.write("alert")
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Problem writing alert file: %r", filepath)
            self.stats.unexpected_exception(ex, where="create_alert_file")

    def delete_alert_file(self, filename):
        try:
            filepath = os.path.join(self.config.get("alert_file_dir", os.getcwd()), filename)
            if os.path.exists(filepath):
                self.log.debug("Deleting alert file: %r", filepath)
                os.unlink(filepath)
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Problem unlinking: %r", filepath)
            self.stats.unexpected_exception(ex, where="delete_alert_file")

    def within_dbpoll_time(self, time1, time2):
        return abs((time1 - time2).total_seconds()) < self.config.get("db_poll_interval", 5.0)

    def main_loop(self):
        while self.running:
            try:
                self.check_cluster_state()
            except Exception as ex:  # pylint: disable=broad-except
                self.log.exception("Failed to check cluster state")
                self.stats.unexpected_exception(ex, where="main_loop_check_cluster_state")
            try:
                self.write_cluster_state_to_json_file()
            except Exception as ex:  # pylint: disable=broad-except
                self.log.exception("Failed to write cluster state")
                self.stats.unexpected_exception(ex, where="main_loop_writer_cluster_state")
            try:
                self.failover_decision_queue.get(timeout=float(self.config.get("replication_state_check_interval", 5.0)))
                q = self.failover_decision_queue
                while not q.empty():
                    try:
                        q.get(False)
                    except Empty:
                        continue
                self.log.info("Immediate failover check completed")
            except Empty:
                pass

    def run(self):
        self.cluster_monitor.start()
        self.webserver.start()
        self.main_loop()


def main(args=None):
    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="pglookout",
        description="postgresql replication monitoring and failover daemon")
    parser.add_argument("--version", action="version", help="show program version",
                        version=version.__version__)
    parser.add_argument("config", help="configuration file")
    arg = parser.parse_args(args)

    if not os.path.exists(arg.config):
        print("pglookout: {!r} doesn't exist".format(arg.config))
        return 1

    logutil.configure_logging()

    pglookout = PgLookout(arg.config)
    return pglookout.run()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
