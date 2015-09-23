"""
pglookout - cluster monitoring component

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from .common import (
    mask_connection_info, get_iso_timestamp, parse_iso_datetime,
    set_syslog_handler, total_seconds)
from email.utils import parsedate
from psycopg2.extras import RealDictCursor
from threading import Thread
import datetime
import errno
import logging
import psycopg2
import requests
import select
import time


class PglookoutTimeout(Exception):
    pass


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

    def _connect_to_db(self, instance, dsn):
        conn = self.db_conns.get(instance)
        if conn:
            return conn
        if not dsn:
            self.log.warning("Can't connect to %s, dsn is %r", instance, dsn)
            return None
        inst_info_str = "{0!r} ({1})".format(instance, mask_connection_info(dsn))
        try:
            self.log.info("Connecting to %s", inst_info_str)
            conn = psycopg2.connect(dsn=dsn, async=True)
            wait_select(conn)
            self.log.debug("Connected to %s", inst_info_str)
        except (PglookoutTimeout, psycopg2.OperationalError) as ex:
            self.log.warning("%s (%s) connecting to %s (%s)",
                             ex.__class__.__name__, str(ex).strip(),
                             instance, inst_info_str)
            if "password authentication" in getattr(ex, "message", ""):
                self.create_alert_file("authentication_error")
            conn = None  # make sure we don't try to use the connection if we timed out
        except:
            self.log.exception("Failed to connect to %s (%s)",
                               instance, inst_info_str)
            conn = None
        self.db_conns[instance] = conn
        return conn

    def _fetch_observer_state(self, instance, uri):
        result = {"fetch_time": get_iso_timestamp(), "connection": True}
        try:
            fetch_uri = uri + "/state.json"
            response = self.session.get(fetch_uri, timeout=5.0)

            # check time difference for large skews
            remote_server_time = parsedate(response.headers['date'])
            remote_server_time = datetime.datetime.fromtimestamp(time.mktime(remote_server_time))
            time_diff = parse_iso_datetime(result['fetch_time']) - remote_server_time
            if time_diff > datetime.timedelta(seconds=5):
                self.log.error("Time difference between us and observer node %r is %r, response: %r, ignoring response",
                               instance, time_diff, response.json())  # pylint: disable=no-member
                return
            result.update(response.json())  # pylint: disable=no-member
        except requests.ConnectionError as ex:
            self.log.warning("%s (%s) fetching state from observer: %r, %r",
                             ex.__class__.__name__, ex, instance, fetch_uri)
            result['connection'] = False
        except:
            self.log.exception("Problem in fetching state from observer: %r, %r", instance, fetch_uri)
            result['connection'] = False
        return result

    def fetch_observer_state(self, instance, uri):
        start_time = time.time()
        result = self._fetch_observer_state(instance, uri)
        if result:
            if instance in self.observer_state:
                self.observer_state[instance].update(result)
            else:
                self.observer_state[instance] = result
        self.log.debug("Observer: %r state was: %r, took: %.4fs to fetch",
                       instance, result, time.time() - start_time)

    def connect_to_cluster_nodes_and_cleanup_old_nodes(self):
        leftover_conns = set(self.db_conns) - set(self.config.get("remote_conns", {}))
        for leftover_instance in leftover_conns:
            self.log.debug("Removing leftover state for: %r", leftover_instance)
            self.db_conns.pop(leftover_instance)
            self.cluster_state.pop(leftover_instance, "")
            self.observer_state.pop(leftover_instance, "")
        #  Making sure we have a connection to all currently configured db hosts
        for instance, connect_string in self.config.get("remote_conns", {}).items():
            self._connect_to_db(instance, dsn=connect_string)

    def _standby_status_query(self, instance, db_conn):
        """Status query that is executed on the standby node"""
        f_result = None
        result = {"fetch_time": get_iso_timestamp(), "connection": False}
        if not db_conn:
            db_conn = self._connect_to_db(instance, self.config["remote_conns"].get(instance))
            if not db_conn:
                return result
        try:
            phase = "querying status from"
            self.log.debug("%s %r", phase, instance)
            c = db_conn.cursor(cursor_factory=RealDictCursor)
            fields = [
                "now() AS db_time",
                "pg_is_in_recovery()",
                "pg_last_xact_replay_timestamp()",
                "pg_last_xlog_receive_location()",
                "pg_last_xlog_replay_location()",
            ]
            query = "SELECT {}".format(", ".join(fields))
            c.execute(query)
            wait_select(c.connection)
            f_result = c.fetchone()
            if not f_result['pg_is_in_recovery']:
                # This is only run on masters to create txid traffic every db_poll_interval
                phase = "updating transaction on"
                self.log.debug("%s %r", phase, instance)
                c.execute("SELECT txid_current()")
                wait_select(c.connection)
        except (PglookoutTimeout, psycopg2.DatabaseError, psycopg2.InterfaceError, psycopg2.OperationalError) as ex:
            self.log.warning("%s (%s) %s %s", ex.__class__.__name__, str(ex).strip(), phase, instance)
            db_conn.close()
            self.db_conns[instance] = None

        result.update(self._parse_status_query_result(f_result))
        return result

    @staticmethod
    def _parse_status_query_result(result):
        if not result:
            return {}
        # abs is for catching time travel (as in going from the future to the past
        if result["pg_last_xact_replay_timestamp"]:
            replication_time_lag = abs(result["db_time"] - result["pg_last_xact_replay_timestamp"])
            result["replication_time_lag"] = total_seconds(replication_time_lag)
            result["pg_last_xact_replay_timestamp"] = get_iso_timestamp(result["pg_last_xact_replay_timestamp"])

        if not result["pg_is_in_recovery"]:
            # These are set to None so when we query a standby promoted to master
            # it looks identical to the results from a master node that's never been a standby
            result.update({
                "pg_last_xlog_receive_location": None,
                "pg_last_xact_replay_timestamp": None,
                "pg_last_xlog_replay_location": None,
                "replication_time_lag": 0.0,
            })
        result.update({"db_time": get_iso_timestamp(result["db_time"]), "connection": True})
        return result

    def standby_status_query(self, instance, db_conn):
        start_time = time.time()
        result = self._standby_status_query(instance, db_conn)
        self.log.debug("DB state gotten from: %r was: %r, took: %.4fs to fetch",
                       instance, result, time.time() - start_time)
        if instance in self.cluster_state:
            self.cluster_state[instance].update(result)
        else:
            self.cluster_state[instance] = result

    def run(self):
        while self.running:
            try:
                self.connect_to_cluster_nodes_and_cleanup_old_nodes()
                for instance, db_conn in self.db_conns.items():
                    self.standby_status_query(instance, db_conn)
                for instance, uri in self.config.get("observers", {}).items():
                    self.fetch_observer_state(instance, uri)
            except:
                self.log.exception("Problem in ClusterMonitor")
            time.sleep(self.config.get("db_poll_interval", 5.0))
