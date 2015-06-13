"""
pglookout - replication monitoring and failover daemon

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from __future__ import print_function
import copy
import datetime
import errno
import logging
import logging.handlers
import os
import psycopg2
import re
import requests
import select
import signal
import socket
import subprocess
import sys
import time
from email.utils import parsedate
from psycopg2.extras import RealDictCursor
from threading import Thread

try:
    from SocketServer import ThreadingMixIn  # pylint: disable=F0401
    from BaseHTTPServer import HTTPServer  # pylint: disable=F0401
    from SimpleHTTPServer import SimpleHTTPRequestHandler  # pylint: disable=F0401
except ImportError:  # Support Py3k
    from socketserver import ThreadingMixIn  # pylint: disable=F0401
    from http.server import HTTPServer, SimpleHTTPRequestHandler  # pylint: disable=F0401

# Prefer simplejson over json as on Python2.6 json does not play together
# nicely with other libraries as it loads strings in unicode and for example
# SysLogHandler does not like getting syslog facility as unicode string.
try:
    import simplejson as json  # pylint: disable=F0401
except ImportError:
    import json

try:
    from systemd import daemon  # pylint: disable=F0401
except:
    daemon = None

format_str = "%(asctime)s\t%(name)s\t%(levelname)s\t%(message)s"
syslog_format_str = '%(name)s %(levelname)s: %(message)s'

logging.basicConfig(level=logging.DEBUG, format=format_str)


class PglookoutTimeout(Exception):
    pass


def get_iso_timestamp(fetch_time=None):
    if not fetch_time:
        fetch_time = datetime.datetime.utcnow()
    elif fetch_time.tzinfo:
        fetch_time = fetch_time.replace(tzinfo=None) - datetime.timedelta(seconds=fetch_time.utcoffset().seconds)
    return fetch_time.isoformat() + "Z"


def parse_iso_datetime(value):
    pattern_ext = r'(?P<year>\d{4})-(?P<month>\d\d)-(?P<day>\d\d)(T(?P<hour>\d\d):(?P<minute>\d\d)(:(?P<second>\d\d)(.(?P<microsecond>\d{6}))?)?Z)?$'  # pylint: disable=C0301
    pattern_basic = r'(?P<year>\d{4})(?P<month>\d\d)(?P<day>\d\d)(T(?P<hour>\d\d)(?P<minute>\d\d)((?P<second>\d\d)((?P<microsecond>\d{6}))?)?Z)?$'  # pylint: disable=C0301
    match = re.match(pattern_ext, value)
    if not match:
        match = re.match(pattern_basic, value)
    parts = dict((key, int(match.group(key) or '0'))
                 for key in ('year', 'month', 'day', 'hour', 'minute', 'second', 'microsecond'))
    return datetime.datetime(tzinfo=None, **parts)


def convert_xlog_location_to_offset(xlog_location):
    log_id, offset = xlog_location.split("/")
    return int(log_id, 16) << 32 | int(offset, 16)


def set_syslog_handler(syslog_address, syslog_facility, logger):
    syslog_handler = logging.handlers.SysLogHandler(address=syslog_address, facility=syslog_facility)
    logger.addHandler(syslog_handler)
    formatter = logging.Formatter(syslog_format_str)
    syslog_handler.setFormatter(formatter)
    return syslog_handler


class PgLookout(object):
    def __init__(self, config_path):
        self.log = logging.getLogger("pglookout")
        self.running = True
        self.replication_lag_over_warning_limit = False

        self.config_path = config_path
        self.config = {}
        self.log_level = "DEBUG"

        self.connected_master_nodes = {}
        self.disconnected_master_nodes = {}
        self.connected_observer_nodes = {}
        self.disconnected_observer_nodes = {}
        self.replication_lag_warning_boundary = None
        self.replication_lag_failover_timeout = None
        self.own_db = None
        self.current_master = None
        self.failover_command = None
        self.over_warning_limit_command = None
        self.never_promote_these_nodes = None
        self.cluster_monitor = None
        self.syslog_handler = None
        self.load_config()

        signal.signal(signal.SIGHUP, self.load_config)
        signal.signal(signal.SIGINT, self.quit)
        signal.signal(signal.SIGTERM, self.quit)

        self.cluster_state = {}
        self.observer_state = {}
        self.overall_state = {"db_nodes": self.cluster_state, "observer_nodes": self.observer_state,
                              "current_master": self.current_master,
                              "replication_lag_over_warning": self.replication_lag_over_warning_limit}

        self.cluster_monitor = ClusterMonitor(self.config, self.cluster_state,
                                              self.observer_state, self.create_alert_file)
        # cluster_monitor doesn't exist at the time of reading the config initially
        self.cluster_monitor.log.setLevel(self.log_level)
        self.webserver = WebServer(self.config, self.cluster_state)

        if daemon:  # If we can import systemd we always notify it
            daemon.notify("READY=1")
            self.log.info("Sent startup notification to systemd that pglookout is READY")
        self.log.info("PGLookout initialized, own_hostname: %r, own_db: %r, cwd: %r",
                      socket.gethostname(), self.own_db, os.getcwd())

    def quit(self, _signal=None, _frame=None):
        self.log.warning("Quitting, signal: %r, frame: %r", _signal, _frame)
        self.cluster_monitor.running = False
        self.running = False
        self.webserver.close()

    def load_config(self, _signal=None, _frame=None):
        self.log.debug("Loading JSON config from: %r, signal: %r, frame: %r",
                       self.config_path, _signal, _frame)
        try:
            with open(self.config_path) as fp:
                self.config = json.load(fp)
                if self.cluster_monitor:
                    self.cluster_monitor.config = copy.deepcopy(self.config)
        except:
            self.log.exception("Invalid JSON config, exiting")
            sys.exit(0)

        if self.config.get("syslog") and not self.syslog_handler:
            self.syslog_handler = set_syslog_handler(self.config.get("syslog_address", "/dev/log"),
                                                     self.config.get("syslog_facility", "local2"),
                                                     self.log)
        self.own_db = self.config.get("own_db")
        # the levelNames hack is needed for Python2.6
        log_level_name = self.config.get("log_level", "DEBUG")
        if sys.version_info[0] >= 3:
            self.log_level = getattr(logging, log_level_name)
        else:
            self.log_level = logging._levelNames[log_level_name]  # pylint: disable=W0212,E1101
        try:
            self.log.setLevel(self.log_level)
            if self.cluster_monitor:
                self.cluster_monitor.log.setLevel(self.log_level)
        except ValueError:
            print("Problem setting log level %r" % self.log_level)
            self.log.exception("Problem with log_level: %r", self.log_level)
        self.never_promote_these_nodes = self.config.get("never_promote_these_nodes", [])
        # we need the failover_command to be converted into subprocess [] format
        self.failover_command = self.config.get("failover_command", "").split()
        self.over_warning_limit_command = self.config.get("over_warning_limit_command")
        self.replication_lag_warning_boundary = self.config.get("warning_replication_time_lag", 30.0)
        self.replication_lag_failover_timeout = self.config.get("max_failover_replication_time_lag", 120.0)
        self.log.debug("Loaded config: %r from: %r", self.config, self.config_path)

    def write_cluster_state_to_json_file(self):
        """Periodically write a JSON state file to disk"""
        start_time = time.time()
        state_file_path = self.config.get("json_state_file_path", "/tmp/pglookout_state.json")
        try:
            self.overall_state = {"db_nodes": self.cluster_state, "observer_nodes": self.observer_state,
                                  "current_master": self.current_master}
            json_to_dump = json.dumps(self.overall_state, indent=4)
            self.log.debug("Writing JSON state file to: %r, file_size: %r", state_file_path, len(json_to_dump))
            with open(state_file_path + ".tmp", "w") as fp:
                fp.write(json_to_dump)
            os.rename(state_file_path + ".tmp", state_file_path)
            self.log.debug("Wrote JSON state file to disk, took %.4fs", time.time() - start_time)
        except:
            self.log.exception("Problem in writing JSON: %r file to disk, took %.4fs",
                               self.overall_state, time.time() - start_time)

    def create_node_map(self, cluster_state, observer_state):
        standby_nodes, master_node, master_host = {}, None, None
        connected_master_nodes, disconnected_master_nodes = {}, {}
        connected_observer_nodes, disconnected_observer_nodes = {}, {}
        self.log.debug("Creating node map out of cluster_state: %r and observer_state: %r",
                       cluster_state, observer_state)
        for host, state in cluster_state.items():
            if 'pg_is_in_recovery' in state:
                if state['pg_is_in_recovery']:
                    standby_nodes[host] = state
                elif state['connection']:
                    connected_master_nodes[host] = state
                elif not state['connection']:
                    disconnected_master_nodes[host] = state
            else:
                self.log.debug("No knowledge on host: %r state: %r of whether it's in recovery or not", host, state)

        for observer_name, state in observer_state.items():
            connected = state.get("connection", False)
            if connected:
                connected_observer_nodes[observer_name] = state.get("fetch_time")
            else:
                disconnected_observer_nodes[observer_name] = state.get("fetch_time")
            for host, db_state in state.items():
                if host not in cluster_state:
                    # A single observer can observe multiple different replication clusters.
                    # Ignore data on nodes that don't belong in our own cluster
                    self.log.debug("Ignoring node: %r since it does not belong into our own replication cluster.", host)
                    continue
                if isinstance(db_state, dict):  # other keys are "connection" and "fetch_time"
                    own_fetch_time = parse_iso_datetime(cluster_state.get(host, {"fetch_time": get_iso_timestamp(datetime.datetime(year=2000, month=1, day=1))})['fetch_time'])  # pylint: disable=C0301
                    observer_fetch_time = parse_iso_datetime(db_state['fetch_time'])
                    self.log.debug("observer_name: %r, dbname: %r, state: %r, observer_fetch_time: %r",
                                   observer_name, host, db_state, observer_fetch_time)
                    if 'pg_is_in_recovery' in db_state:
                        if db_state['pg_is_in_recovery']:
                            # we always trust ourselves the most for localhost, and
                            # in case we are actually connected to the other node
                            if observer_fetch_time >= own_fetch_time and host != self.own_db and standby_nodes.get(host, {"connection": False})['connection'] is False:  # pylint: disable=C0301
                                standby_nodes[host] = db_state
                        else:
                            master_node = connected_master_nodes.get(host, {})
                            connected = master_node.get("connection", False)
                            self.log.debug("Observer: %r sees %r as master, we see: %r, same_master: %r, connection: %r",
                                           observer_name, host, self.current_master, host == self.current_master,
                                           db_state.get('connection'))
                            if observer_fetch_time >= own_fetch_time and host != self.own_db:
                                if connected:
                                    connected_master_nodes[host] = db_state
                                else:
                                    disconnected_master_nodes[host] = db_state
                    else:
                        self.log.warning("No knowledge on if: %r %r from observer: %r is in recovery",
                                         host, db_state, observer_name)

        self.connected_master_nodes = connected_master_nodes
        self.disconnected_master_nodes = disconnected_master_nodes
        self.connected_observer_nodes = connected_observer_nodes
        self.disconnected_observer_nodes = disconnected_observer_nodes

        if len(self.connected_master_nodes) == 0:
            self.log.warning("No known master node, disconnected masters: %r", list(disconnected_master_nodes.keys()))
            if len(disconnected_master_nodes) > 0:
                master_host, master_node = list(disconnected_master_nodes.keys())[0], list(disconnected_master_nodes.values())[0]
        elif len(self.connected_master_nodes) == 1:
            master_host, master_node = list(connected_master_nodes.keys())[0], list(connected_master_nodes.values())[0]
            if disconnected_master_nodes:
                self.log.warning("Picked %r as master since %r are in a disconnected state",
                                 master_host, disconnected_master_nodes)
        else:
            self.create_alert_file("multiple_master_warning")
            self.log.error("More than one master node connected_master_nodes: %r, disconnected_master_nodes: %r",
                           connected_master_nodes, disconnected_master_nodes)

        return master_host, master_node, standby_nodes

    def check_cluster_state(self):
        master_node = None
        cluster_state = copy.deepcopy(self.cluster_state)
        observer_state = copy.deepcopy(self.observer_state)
        if not cluster_state:
            self.log.warning("No cluster state, probably still starting up")
            return

        master_host, master_node, standby_nodes = self.create_node_map(cluster_state, observer_state)  # pylint: disable=W0612

        if master_host and master_host != self.current_master:
            self.log.info("New master node detected: old: %r new: %r: %r", self.current_master, master_host, master_node)
            previous_master = self.current_master
            self.current_master = master_host
            if self.own_db and previous_master and self.config.get("autofollow") and self.own_db != master_host:
                self.start_following_new_master(master_host, previous_master)

        own_state = self.cluster_state.get(self.own_db)

        # If we're an observer ourselves, we'll grab the IP address from HTTP server address
        observer_info = ','.join(observer_state.keys()) or 'no'
        if not self.own_db:
            observer_info = self.config.get("http_address", observer_info)

        self.log.debug("Cluster has %s standbys, %s observers and %s as master, own_db: %r, own_state: %r",
                       ','.join(standby_nodes.keys()) or 'no',
                       observer_info,
                       self.current_master,
                       self.own_db,
                       own_state or "observer")

        if self.own_db:
            if self.own_db == self.current_master:
                # We are the master of this cluster, nothing to do
                self.log.debug("We %r: %r are still the master node: %r of this cluster, nothing to do.",
                               self.own_db, own_state, master_node)
                return
            if not standby_nodes:
                self.log.warning("No standby nodes set, master node: %r", master_node)
                return
            self.check_replication_lag(own_state, standby_nodes)

    def check_replication_lag(self, own_state, standby_nodes):
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
                self.create_alert_file("replication_delay_warning")
                if self.over_warning_limit_command:
                    self.log.warning("Executing over_warning_limit_command: %r", self.over_warning_limit_command)
                    return_code = self.execute_external_command(self.over_warning_limit_command)
                    self.log.warning("Executed over_warning_limit_command: %r, return_code: %r",
                                     self.over_warning_limit_command, return_code)
                else:
                    self.log.warning("No over_warning_limit_command set")
        elif self.replication_lag_over_warning_limit:
            self.replication_lag_over_warning_limit = False
            self.delete_alert_file("replication_delay_warning")

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
        for hostname, node_state in standby_nodes.items():
            now = datetime.datetime.utcnow()
            if node_state['connection'] and now - parse_iso_datetime(node_state['fetch_time']) < datetime.timedelta(seconds=20) and hostname not in self.never_promote_these_nodes:  # pylint: disable=C0301
                xlog_pos = convert_xlog_location_to_offset(node_state['pg_last_xlog_receive_location'])
                if xlog_pos in known_replication_positions:
                    known_replication_positions[xlog_pos].append(hostname)  # pylint: disable=C0301
                else:
                    known_replication_positions[xlog_pos] = [hostname]
        return known_replication_positions

    def _have_we_been_in_contact_with_the_master_within_the_failover_timeout(self):
        # no need to do anything here if there are no disconnected masters
        if len(self.disconnected_master_nodes) > 0:
            disconnected_master_node = list(self.disconnected_master_nodes.values())[0]
            db_time = disconnected_master_node.get('db_time', get_iso_timestamp()) or get_iso_timestamp()
            time_since_last_contact = datetime.datetime.utcnow() - parse_iso_datetime(db_time)
            if time_since_last_contact < datetime.timedelta(seconds=self.replication_lag_failover_timeout):
                self.log.debug("We've had contact with master: %r at: %r within the last %.2fs, not failing over",
                               disconnected_master_node, db_time, time_since_last_contact.seconds)
                return True
        return False

    def do_failover_decision(self, own_state, standby_nodes):
        if len(self.connected_master_nodes) > 0 or self._have_we_been_in_contact_with_the_master_within_the_failover_timeout():
            self.log.warning("We still have some connected masters: %r, not failing over", self.connected_master_nodes)
            return

        known_replication_positions = self.get_replication_positions(standby_nodes)
        if not known_replication_positions:
            self.log.warning("No known replication positions, canceling failover consideration")
            return

        #  We always pick the 0th one coming out of sort, so both standbys will pick the same node for promotion
        furthest_along_host = min(known_replication_positions[max(known_replication_positions)])
        self.log.warning("Node that is furthest along is: %r, all replication positions were: %r",
                         furthest_along_host, known_replication_positions)
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

        if standby_nodes[furthest_along_host] == own_state:
            if self.check_for_maintenance_mode_file():
                self.log.warning("Canceling failover even though we were the node the furthest along, since "
                                 "this node has an existing maintenance_mode_file: %r",
                                 self.config.get("maintenance_mode_file", "/tmp/pglookout_maintenance_mode_file"))
                return
            elif self.own_db in self.never_promote_these_nodes:
                self.log.warning("Not doing a failover even though we were the node the furthest along, since this node: %r"
                                 " should never be promoted to master", self.own_db)
            elif size_of_known_state < size_of_needed_majority:
                self.log.warning("Not doing a failover even though we were the node the furthest along, since we aren't "
                                 "aware of the states of enough of the other nodes")
            else:
                start_time = time.time()
                self.log.warning("We will now do a failover to ourselves since we were the host furthest along")
                return_code = self.execute_external_command(self.failover_command)
                self.log.warning("Executed failover command: %r, return_code: %r, took: %.2fs",
                                 self.failover_command, return_code, time.time() - start_time)
                self.create_alert_file("failover_has_happened")
                # Sleep for failover time to give the DB time to restart in promotion mode
                # You want to use this if the failover command is not one that blocks until
                # the db has restarted
                time.sleep(self.config.get("failover_sleep_time", 0.0))
                if return_code == 0:
                    self.replication_lag_over_warning_limit = False
                    self.delete_alert_file("replication_delay_warning")
        else:
            self.log.warning("Nothing to do since node: %r is the furthest along", furthest_along_host)

    def modify_recovery_conf_to_point_at_new_master_host(self, new_master_host, previous_master_host):
        path_to_recovery_conf = os.path.join(self.config.get("pg_data_directory"), "recovery.conf")
        with open(path_to_recovery_conf, "r") as fp:
            old_recovery_conf_contents = fp.readlines()
        has_recovery_target_timeline = False
        new_conf = []
        with open(path_to_recovery_conf + "_temp", "w") as fp:
            for line in old_recovery_conf_contents:
                if line.startswith("recovery_target_timeline"):
                    has_recovery_target_timeline = True
                if line.startswith("primary_conninfo"):
                    line = line.replace(previous_master_host, new_master_host)
                new_conf.append(line)
            #  The timeline of the recovery.conf will require a higher timeline target
            if not has_recovery_target_timeline:
                new_conf.append("recovery_target_timeline = 'latest'\n")
            for line in new_conf:
                fp.write(line)

        self.log.debug("Previous recovery.conf: %r", ''.join(old_recovery_conf_contents))
        self.log.debug("Newly written recovery.conf: %r", ''.join(new_conf))
        os.rename(path_to_recovery_conf + "_temp", path_to_recovery_conf)

    def start_following_new_master(self, new_master_host, previous_master_host):
        start_time = time.time()
        start_command, stop_command = self.config.get("pg_start_command", "").split(), self.config.get("pg_stop_command", "").split()
        self.log.debug("Starting to follow new master: %r, will modify recovery.conf and stop/start PostgreSQL."
                       "pg_stop_command: %r, pg_start_command: %r",
                       new_master_host, start_command, stop_command)
        self.execute_external_command(stop_command)
        self.modify_recovery_conf_to_point_at_new_master_host(new_master_host, previous_master_host)
        self.execute_external_command(start_command)
        self.log.debug("Started following new master: %r, took: %.2fs", new_master_host, time.time() - start_time)

    def execute_external_command(self, command):
        self.log.warning("Executing external command: %r", command)
        return_code, output = 0, ""
        try:
            output = subprocess.check_call(command)
        except subprocess.CalledProcessError as err:
            self.log.exception("Problem with executing: %r, return_code: %r, output: %r",
                               command, err.returncode, err.output)
            return_code = err.returncode  # pylint: disable=E1101
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
        except:
            self.log.exception("Problem writing alert file: %r", filepath)

    def delete_alert_file(self, filename):
        try:
            filepath = os.path.join(self.config.get("alert_file_dir", os.getcwd()), filename)
            if os.path.exists(filepath):
                self.log.debug("Deleting alert file: %r", filepath)
                os.unlink(filepath)
        except:
            self.log.exception("Problem unlinking: %r", filepath)

    def main_loop(self):
        while self.running:
            # Separate try/except so we still write the state file
            sleep_time = 5.0
            try:
                sleep_time = float(self.config.get("replication_state_check_interval", 5.0))
                self.check_cluster_state()
            except:
                self.log.exception("Failed to check cluster state")
            try:
                self.write_cluster_state_to_json_file()
            except:
                self.log.exception("Failed to write cluster state")
            time.sleep(sleep_time)

    def run(self):
        self.cluster_monitor.start()
        self.webserver.start()
        self.main_loop()


class ThreadedWebServer(ThreadingMixIn, HTTPServer):
    cluster_state = None
    log = None


class WebServer(Thread):
    def __init__(self, config, cluster_state):
        Thread.__init__(self)
        self.config = config
        self.cluster_state = cluster_state
        self.log = logging.getLogger("WebServer")
        self.address = self.config.get("http_address", '')
        self.port = self.config.get("http_port", 15000)
        self.server = None
        self.log.debug("WebServer initialized with address: %r port: %r", self.address, self.port)

    def run(self):
        # We bind the port only when we start running
        self.server = ThreadedWebServer((self.address, self.port), RequestHandler)
        self.server.cluster_state = self.cluster_state
        self.server.log = self.log
        self.server.serve_forever()

    def close(self):
        self.log.debug("Closing WebServer")
        self.server.shutdown()
        self.log.debug("Closed WebServer")


class RequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.server.log.debug("Got request: %r", self.path)
        if self.path.startswith("/state.json"):
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            response = json.dumps(self.server.cluster_state, indent=4)
            self.send_header('Content-length', len(response))
            self.end_headers()
            self.wfile.write(response)
        else:
            self.send_response(404)


def wait_select(conn, timeout=5.0):
    end_time = time.time() + timeout
    while time.time() < end_time:
        time_left = end_time - time.time()
        state = conn.poll()
        try:
            if state == psycopg2.extensions.POLL_OK:
                return
            elif state == psycopg2.extensions.POLL_READ:
                select.select([conn.fileno()], [], [], min(timeout, time_left))
            elif state == psycopg2.extensions.POLL_WRITE:
                select.select([], [conn.fileno()], [], min(timeout, time_left))
            else:
                raise psycopg2.OperationalError("bad state from poll: %s" % state)
        except select.error as error:
            if error.args[0] != errno.EINTR:
                raise
    raise PglookoutTimeout("timed out in wait_select")


class ClusterMonitor(Thread):
    def __init__(self, config, cluster_state, observer_state, create_alert_file):
        Thread.__init__(self)
        self.log = logging.getLogger("ClusterMonitor")
        self.running = True
        self.cluster_state = cluster_state
        self.observer_state = observer_state
        self.config = config
        self.create_alert_file = create_alert_file
        self.db_conns = {}
        self.session = requests.Session()
        if self.config.get("syslog"):
            self.syslog_handler = set_syslog_handler(self.config.get("syslog_address", "/dev/log"),
                                                     self.config.get("syslog_facility", "local2"),
                                                     self.log)
        self.log.debug("Initialized ClusterMonitor with: %r", cluster_state)

    def _connect_to_db(self, hostname, dsn):
        conn = self.db_conns.get(hostname)
        if conn:
            return conn
        try:
            self.log.debug("Connecting to hostname: %r", hostname)
            conn = psycopg2.connect(dsn=dsn, async=True)
            wait_select(conn)
            self.log.debug("Connected to hostname: %r, dsn: %r", hostname, conn.dsn)
        except psycopg2.OperationalError as ex:
            self.log.warning("%s (%s) connecting to DB at: %r",
                             ex.__class__.__name__, ex, hostname)
            if hasattr(ex, "message") and 'password authentication' in ex.message:
                self.create_alert_file("authentication_error")
            conn = None
        except:
            self.log.exception("Problem in connecting to DB at: %r", hostname)
            conn = None
        self.db_conns[hostname] = conn
        return conn

    def _fetch_observer_state(self, hostname, uri):
        result = {"fetch_time": get_iso_timestamp(), "connection": True}
        try:
            fetch_uri = uri + "/state.json"
            response = self.session.get(fetch_uri, timeout=5.0)

            # check time difference for large skews
            remote_server_time = parsedate(response.headers['date'])
            remote_server_time = datetime.datetime.fromtimestamp(time.mktime(remote_server_time))
            time_diff = parse_iso_datetime(result['fetch_time']) - remote_server_time
            if time_diff > datetime.timedelta(seconds=5):
                self.log.error("Time difference own node: %r, observer node is: %r, response: %r, ignoring response",
                               hostname, time_diff, response.json())  # pylint: disable=E1103
                return
            result.update(response.json())  # pylint: disable=E1103
        except requests.ConnectionError as ex:
            self.log.warning("%s (%s) fetching state from observer: %r, %r",
                             ex.__class__.__name__, ex, hostname, fetch_uri)
            result['connection'] = False
        except:
            self.log.exception("Problem in fetching state from observer: %r, %r", hostname, fetch_uri)
            result['connection'] = False
        return result

    def fetch_observer_state(self, hostname, uri):
        start_time = time.time()
        result = self._fetch_observer_state(hostname, uri)
        if result:
            if hostname in self.observer_state:
                self.observer_state[hostname].update(result)
            else:
                self.observer_state[hostname] = result
        self.log.debug("Observer: %r state was: %r, took: %.4fs to fetch",
                       hostname, result, time.time() - start_time)

    def connect_to_cluster_nodes_and_cleanup_old_nodes(self):
        leftover_host_conns = set(self.db_conns) - set(self.config.get("remote_conns", {}))
        for leftover_conn_hostname in leftover_host_conns:
            self.log.debug("Removing leftover state for: %r", leftover_conn_hostname)
            self.db_conns.pop(leftover_conn_hostname)
            self.cluster_state.pop(leftover_conn_hostname, "")
            self.observer_state.pop(leftover_conn_hostname, "")
        #  Making sure we have a connection to all currently configured db hosts
        for hostname, connect_string in self.config.get('remote_conns', {}).items():
            self._connect_to_db(hostname, dsn=connect_string)

    def _standby_status_query(self, hostname, db_conn):
        """Status query that is executed on the standby node"""
        f_result = None
        result = {"fetch_time": get_iso_timestamp(), "connection": False}
        if not db_conn:
            db_conn = self._connect_to_db(hostname, self.config['remote_conns'].get(hostname))
            if not db_conn:
                return result
        try:
            self.log.debug("Querying DB state for DB: %r", hostname)
            c = db_conn.cursor(cursor_factory=RealDictCursor)
            query = "SELECT *, now() AS db_time, " \
                "pg_last_xact_replay_timestamp " \
                "FROM pg_last_xact_replay_timestamp(), pg_is_in_recovery(), " \
                "pg_last_xlog_receive_location(), pg_last_xlog_replay_location()"
            c.execute(query)
            wait_select(c.connection)
            f_result = c.fetchone()
            if not f_result['pg_is_in_recovery']:
                #  This is only run on masters to create txid traffic every db_poll_interval
                c.execute("SELECT txid_current()")
                wait_select(c.connection)
        except (PglookoutTimeout, psycopg2.OperationalError, psycopg2.InterfaceError):
            self.log.exception("Problem with hostname: %r, closing connection", hostname)
            db_conn.close()
            self.db_conns[hostname] = None

        if f_result:
            # abs is for catching time travel (as in going from the future to the past
            if f_result['pg_last_xact_replay_timestamp']:
                replication_time_lag = abs(f_result['db_time'] - f_result['pg_last_xact_replay_timestamp'])
                f_result["replication_time_lag"] = replication_time_lag.seconds + replication_time_lag.microseconds * 10 ** -6
                f_result['pg_last_xact_replay_timestamp'] = f_result['pg_last_xact_replay_timestamp'].isoformat() + "Z"

            if not f_result['pg_is_in_recovery']:
                # These are set to None so when we query a standby promoted to master
                # it looks identical to the results from a master node that's never been a standby
                f_result.update({'pg_last_xlog_receive_location': None,
                                 'pg_last_xact_replay_timestamp': None,
                                 'pg_last_xlog_replay_location': None,
                                 'replication_time_lag': 0.0})
            f_result.update({"db_time": get_iso_timestamp(f_result['db_time']), "connection": True})
            result.update(f_result)
        return result

    def standby_status_query(self, hostname, db_conn):
        start_time = time.time()
        result = self._standby_status_query(hostname, db_conn)
        self.log.debug("DB state gotten from: %r was: %r, took: %.4fs to fetch",
                       hostname, result, time.time() - start_time)
        if hostname in self.cluster_state:
            self.cluster_state[hostname].update(result)
        else:
            self.cluster_state[hostname] = result

    def run(self):
        while self.running:
            try:
                self.connect_to_cluster_nodes_and_cleanup_old_nodes()
                for hostname, db_conn in self.db_conns.items():
                    self.standby_status_query(hostname, db_conn)
                for hostname, uri in self.config.get('observers', {}).items():
                    self.fetch_observer_state(hostname, uri)
            except:
                self.log.exception("Problem in ClusterMonitor")
            time.sleep(self.config.get("db_poll_interval", 5.0))


def main(args=None):
    if not args:
        args = sys.argv[1:]
    if len(args) == 1 and os.path.exists(args[0]):
        pglookout = PgLookout(args[0])
        pglookout.run()
    else:
        print("Usage, pglookout <config filename>")
        return 1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
