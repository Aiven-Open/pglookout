"""
pglookout - cluster monitoring component

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from .common import parse_iso_datetime, get_iso_timestamp, set_syslog_handler
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
                #  This is only run on masters to create txid traffic every db_poll_interval
                c.execute("SELECT txid_current()")
                wait_select(c.connection)
        except (PglookoutTimeout, psycopg2.OperationalError, psycopg2.InterfaceError):
            self.log.exception("Problem with hostname: %r, closing connection", hostname)
            db_conn.close()
            self.db_conns[hostname] = None

        result.update(self._parse_status_query_result(f_result))
        return result

    @staticmethod
    def _parse_status_query_result(result):
        if not result:
            return {}
        # abs is for catching time travel (as in going from the future to the past
        if result["pg_last_xact_replay_timestamp"]:
            replication_time_lag = abs(result["db_time"] - result["pg_last_xact_replay_timestamp"])
            result["replication_time_lag"] = replication_time_lag.seconds + replication_time_lag.microseconds * 10 ** -6
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
