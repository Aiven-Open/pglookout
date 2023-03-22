"""
pglookout - cluster monitoring component

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""
from __future__ import annotations

from . import logutil
from .common import get_iso_timestamp, parse_iso_datetime
from .config import Config
from .pgutil import mask_connection_info
from concurrent.futures import as_completed, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from email.utils import parsedate
from logging.handlers import SysLogHandler
from pglookout.statsd import StatsClient
from psycopg2.extensions import POLL_OK, POLL_READ, POLL_WRITE
from psycopg2.extras import RealDictCursor, RealDictRow
from queue import Empty, Queue
from threading import Thread
from typing import Any, Callable, cast, Dict, Final, TypedDict

import datetime
import errno
import logging
import psycopg2
import requests
import select
import time

# https://www.psycopg.org/docs/connection.html#connection.server_version
PG_VERSION_10: Final[int] = 10_00_00  # 10.0.0


class PglookoutTimeout(Exception):
    pass


@dataclass(frozen=True)
class ReplicationSlot:
    slot_name: str
    plugin: str
    slot_type: str
    database: str
    catalog_xmin: str
    restart_lsn: str
    confirmed_flush_lsn: str
    state_data: str


class ReplicationSlotAsDict(TypedDict, total=True):
    slot_name: str
    plugin: str
    slot_type: str
    database: str
    catalog_xmin: str
    restart_lsn: str
    confirmed_flush_lsn: str
    state_data: str


class MemberState(TypedDict, total=False):
    """Represents the state of a member of the cluster.

    Note:
        This is a very loose type as no key is mandatory. This is because
        it is too dangerous to impose a stricter type until we have a
        better test coverage, as it would change some behaviour in the
        code (some unconventional behaviour was detected, and it may be a
        bug or a feature).
    """

    # Connection Status
    connection: bool
    fetch_time: str
    # Queried Status
    db_time: str | datetime.datetime
    pg_is_in_recovery: bool
    pg_last_xact_replay_timestamp: datetime.datetime | None
    pg_last_xlog_receive_location: str | None
    pg_last_xlog_replay_location: str | None
    # Replication info
    replication_slots: list[ReplicationSlotAsDict]
    replication_time_lag: float | None
    min_replication_time_lag: float | None
    replication_start_time: float | None


# Note for future improvements:
# If we want ObserverState to accept arbitrary keys, we have three choices:
# - Use a different type (pydantic, dataclasses, etc.)
# - Use a TypedDict for static keys (connection, fetch_time) and a sub-dict for
#   dynamic keys (received from state.json).
# - Wait for something like `allow_extra` to be implemented into TypedDict (unlikely)
#   https://github.com/python/mypy/issues/4617
ObserverState = Dict[str, Any]


def wait_select(conn: psycopg2.connection, timeout: float = 5.0) -> None:
    end_time = time.monotonic() + timeout
    while time.monotonic() < end_time:
        time_left = end_time - time.monotonic()
        state = conn.poll()
        try:
            if state == POLL_OK:
                return
            if state == POLL_READ:
                select.select([conn.fileno()], [], [], min(timeout, time_left))
            elif state == POLL_WRITE:
                select.select([], [conn.fileno()], [], min(timeout, time_left))
            else:
                raise psycopg2.OperationalError(f"bad state from poll: {state}")
        except select.error as error:
            if error.args[0] != errno.EINTR:
                raise
    raise PglookoutTimeout("timed out in wait_select")


class ClusterMonitor(Thread):
    def __init__(
        self,
        config: Config,
        cluster_state: dict[str, MemberState],
        observer_state: dict[str, ObserverState],
        create_alert_file: Callable[[str], None],
        cluster_monitor_check_queue: Queue[str],
        failover_decision_queue: Queue[str],
        is_replication_lag_over_warning_limit: Callable[[], bool],
        stats: StatsClient,
    ):
        """Thread which collects cluster state.

        Basically a loop which tries to connect to each cluster member and
        to external observers for status information. The information is collected
        in the cluster_state/observer_state dictionaries, which are shared with the main thread.
        """
        Thread.__init__(self)
        self.log: logging.Logger = logging.getLogger("ClusterMonitor")
        self.stats: StatsClient = stats
        self.running: bool = True
        self.cluster_state: dict[str, MemberState] = cluster_state
        self.observer_state: dict[str, ObserverState] = observer_state
        self.config: Config = config
        self.create_alert_file: Callable[[str], None] = create_alert_file
        self.db_conns: dict[str, psycopg2.connection | None] = {}
        self.cluster_monitor_check_queue: Queue[str] = cluster_monitor_check_queue
        self.failover_decision_queue: Queue[str] = failover_decision_queue
        self.is_replication_lag_over_warning_limit: Callable[[], bool] = is_replication_lag_over_warning_limit
        self.session: requests.Session = requests.Session()
        if self.config.get("syslog"):
            # Function `set_syslog_handler` already adds the handler to the provided logger.
            # We just keep a reference to it here.
            self.syslog_handler: SysLogHandler = logutil.set_syslog_handler(
                address=self.config.get("syslog_address", "/dev/log"),
                facility=self.config.get("syslog_facility", "local2"),
                logger=self.log,
            )
        self.last_monitoring_success_time: float | None = None
        self.log.debug("Initialized ClusterMonitor with: %r", cluster_state)

    def _connect_to_db(self, instance: str, dsn: str | None) -> psycopg2.connection | None:
        conn = self.db_conns.get(instance)

        if conn:
            return conn

        if not dsn:
            self.log.warning("Can't connect to %s, dsn is %r", instance, dsn)
            return None

        masked_connection_info = mask_connection_info(dsn)
        inst_info_str = f"{instance!r} ({masked_connection_info})"

        try:
            self.log.info("Connecting to %s", inst_info_str)
            conn = psycopg2.connect(dsn=dsn, async_=True)
            wait_select(conn)
            self.log.debug("Connected to %s", inst_info_str)
        except (PglookoutTimeout, psycopg2.OperationalError) as ex:
            self.log.warning(
                "%s (%s) connecting to %s (%s)",
                ex.__class__.__name__,
                str(ex).strip(),
                instance,
                inst_info_str,
            )
            if "password authentication" in getattr(ex, "message", ""):
                self.create_alert_file("authentication_error")
            conn = None  # make sure we don't try to use the connection if we timed out
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Failed to connect to %s (%s)", instance, inst_info_str)
            self.stats.unexpected_exception(ex, where="_connect_to_db")
            conn = None

        self.db_conns[instance] = conn
        return conn

    def _fetch_observer_state(self, instance: str, uri: str) -> ObserverState | None:
        result = {"fetch_time": get_iso_timestamp(), "connection": True}
        fetch_uri = uri + "/state.json"

        try:
            response = self.session.get(fetch_uri, timeout=5.0)

            # check time difference for large skews
            remote_server_ptime = parsedate(response.headers["date"])
            if remote_server_ptime is None:
                self.log.error(
                    "Failed to parse date from observer node %r, response: %r, ignoring response",
                    instance,
                    response.json(),
                )
                return None
            remote_server_time = datetime.datetime.fromtimestamp(time.mktime(remote_server_ptime))
            time_diff = parse_iso_datetime(result["fetch_time"]) - remote_server_time
            if time_diff > datetime.timedelta(seconds=5):
                self.log.error(
                    "Time difference between us and observer node %r is %r, response: %r, ignoring response",
                    instance,
                    time_diff,
                    response.json(),
                )  # pylint: disable=no-member
                return None
            result.update(response.json())  # pylint: disable=no-member
        except requests.ConnectionError as ex:
            self.log.warning(
                "%s (%s) fetching state from observer: %r, %r",
                ex.__class__.__name__,
                ex,
                instance,
                fetch_uri,
            )
            result["connection"] = False
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Problem in fetching state from observer: %r, %r", instance, fetch_uri)
            self.stats.unexpected_exception(ex, where="_fetch_observer_state")
            result["connection"] = False

        return result

    def fetch_observer_state(self, instance: str, uri: str) -> None:
        start_time = time.monotonic()
        result = self._fetch_observer_state(instance, uri)

        if result:
            if instance in self.observer_state:
                self.observer_state[instance].update(result)
            else:
                self.observer_state[instance] = result

        self.log.debug(
            "Observer: %r state was: %r, took: %.4fs to fetch",
            instance,
            result,
            time.monotonic() - start_time,
        )

    def connect_to_cluster_nodes_and_cleanup_old_nodes(self) -> None:
        leftover_conns = set(self.db_conns) - set(self.config.get("remote_conns", {}))

        for leftover_instance in leftover_conns:
            self.log.debug("Removing leftover state for: %r", leftover_instance)
            self.db_conns.pop(leftover_instance)
            self.cluster_state.pop(leftover_instance, None)
            self.observer_state.pop(leftover_instance, None)

        #  Making sure we have a connection to all currently configured db hosts
        for instance, connect_string in self.config.get("remote_conns", {}).items():
            self._connect_to_db(instance, dsn=connect_string)

    def _fetch_replication_slot_info(self, instance: str, cursor: RealDictCursor) -> list[ReplicationSlot]:
        """Fetch logical replication slot definitions"""

        self.log.debug("reading replication slot state from %r", instance)
        cursor.execute(
            """SELECT
                              slot_name,
                              plugin,
                              slot_type,
                              database,
                              catalog_xmin,
                              restart_lsn,
                              confirmed_flush_lsn,
                              pg_catalog.encode(pg_catalog.pg_read_binary_file(
                                  'pg_replslot/' || slot_name || '/state'), 'base64'
                              ) AS state_data
                            FROM pg_catalog.pg_replication_slots
                            WHERE slot_type = 'logical' AND NOT temporary
        """
        )
        wait_select(cursor.connection)
        replication_slots = [
            ReplicationSlot(**cast(RealDictRow, slot)) for slot in cursor.fetchall()  # type: ignore[redundant-cast]
        ]
        self.log.debug("found %d replication slot(s)", len(replication_slots))
        return replication_slots

    def _query_cluster_member_state(self, instance: str, db_conn: psycopg2.connection | None) -> MemberState:
        """Query a single cluster member for its state"""
        f_result: MemberState | None = None
        result: MemberState = {"fetch_time": get_iso_timestamp(), "connection": False}

        if not db_conn:
            dsn: str | None = self.config["remote_conns"].get(instance)
            db_conn = self._connect_to_db(instance, dsn)
            if not db_conn:
                return result

        phase = "querying status from"
        try:
            self.log.debug("%s %r", phase, instance)

            c = db_conn.cursor(cursor_factory=RealDictCursor)

            c.execute(self._get_statement_query_status(db_conn.server_version))
            wait_select(c.connection)
            maybe_standby_result: MemberState = cast(MemberState, c.fetchone())

            if maybe_standby_result["pg_is_in_recovery"]:
                f_result = maybe_standby_result
            else:
                # First try reading current WAL LSN separately as txid_current may fail in some cases
                phase = "getting master LSN position"

                c.execute(self._get_statement_query_master_lsn_position(db_conn.server_version))
                wait_select(c.connection)
                master_position: RealDictRow = cast(RealDictRow, c.fetchone())
                maybe_standby_result["pg_last_xlog_replay_location"] = master_position["pg_last_xlog_replay_location"]
                f_result = maybe_standby_result

                if db_conn.server_version >= PG_VERSION_10:
                    f_result["replication_slots"] = [
                        cast(ReplicationSlotAsDict, asdict(slot)) for slot in self._fetch_replication_slot_info(instance, c)
                    ]

                # This is only run on masters to create txid traffic every db_poll_interval
                phase = "updating transaction on"
                self.log.debug("%s %r", phase, instance)
                # With pg_current_wal_lsn we simulate replay_location on the master
                # With txid_current we force a new transaction to occur every poll interval to ensure there's
                # a heartbeat for the replication lag.
                c.execute(self._get_statement_query_updating_transaction(db_conn.server_version))
                wait_select(c.connection)
                master_result: RealDictRow = cast(RealDictRow, c.fetchone())

                f_result["pg_last_xlog_replay_location"] = master_result["pg_last_xlog_replay_location"]
        except (
            PglookoutTimeout,
            psycopg2.DatabaseError,
            psycopg2.InterfaceError,
            psycopg2.OperationalError,
        ) as ex:
            self.log.warning("%s (%s) %s %s", ex.__class__.__name__, str(ex).strip(), phase, instance)
            db_conn.close()
            self.db_conns[instance] = None

        if f_result:
            result.update(self._parse_status_query_result(f_result))
        return result

    @staticmethod
    def _get_statement_query_status(server_version: int) -> str:
        if server_version >= PG_VERSION_10:
            return (
                "SELECT now() AS db_time, "
                "pg_is_in_recovery(), "
                "pg_last_xact_replay_timestamp(), "
                "pg_last_wal_receive_lsn() AS pg_last_xlog_receive_location, "
                "pg_last_wal_replay_lsn() AS pg_last_xlog_replay_location"
            )
        return (
            "SELECT now() AS db_time, "
            "pg_is_in_recovery(), "
            "pg_last_xact_replay_timestamp(), "
            "pg_last_xlog_receive_location(), "
            "pg_last_xlog_replay_location()"
        )

    @staticmethod
    def _get_statement_query_master_lsn_position(server_version: int) -> str:
        if server_version >= PG_VERSION_10:
            return "SELECT pg_current_wal_lsn() AS pg_last_xlog_replay_location"
        return "SELECT pg_current_xlog_location() AS pg_last_xlog_replay_location"

    @staticmethod
    def _get_statement_query_updating_transaction(server_version: int) -> str:
        if server_version >= PG_VERSION_10:
            return "SELECT txid_current(), pg_current_wal_lsn() AS pg_last_xlog_replay_location"
        return "SELECT txid_current(), pg_current_xlog_location() AS pg_last_xlog_replay_location"

    # FIXME: Find a tighter input + return type
    @staticmethod
    def _parse_status_query_result(result: MemberState) -> MemberState:
        if not result:
            return {}

        db_time = cast(datetime.datetime, result["db_time"])
        # abs is for catching time travel (as in going from the future to the past
        if result["pg_last_xact_replay_timestamp"]:
            replication_time_lag: datetime.timedelta = abs(db_time - result["pg_last_xact_replay_timestamp"])
            result["replication_time_lag"] = replication_time_lag.total_seconds()
            result["pg_last_xact_replay_timestamp"] = get_iso_timestamp(result["pg_last_xact_replay_timestamp"])

        if not result["pg_is_in_recovery"]:
            # These are set to None so when we query a standby promoted to master
            # it looks identical to the results from a master node that's never been a standby
            result.update(
                {
                    "pg_last_xlog_receive_location": None,
                    "pg_last_xact_replay_timestamp": None,
                    # We simulate replay_location with the results of pg_current_xlog_location on master
                    "pg_last_xlog_replay_location": result["pg_last_xlog_replay_location"],
                    "replication_time_lag": None,  # differentiate from actual lag=0.0
                }
            )
        result.update({"db_time": get_iso_timestamp(db_time), "connection": True})
        return result

    def update_cluster_member_state(self, instance: str, db_conn: psycopg2.connection | None) -> None:
        """Update the cluster state entry for a single cluster member"""
        start_time = time.monotonic()
        result = self._query_cluster_member_state(instance, db_conn)
        self.log.debug(
            "DB state gotten from: %r was: %r, took: %.4fs to fetch",
            instance,
            result,
            time.monotonic() - start_time,
        )
        if instance in self.cluster_state:
            self.cluster_state[instance].update(result)
        else:
            self.cluster_state[instance] = result

        # record the first time we saw replication happen from the master
        if result.get("pg_last_xlog_receive_location"):
            result.setdefault("replication_start_time", time.monotonic())

        # maintain lowest seen lag in seconds in the state
        min_lag = self.cluster_state[instance].get("min_replication_time_lag")
        now_lag = result.get("replication_time_lag")
        if now_lag is not None:
            if min_lag is None:
                self.cluster_state[instance]["min_replication_time_lag"] = now_lag
            else:
                self.cluster_state[instance]["min_replication_time_lag"] = min(min_lag, now_lag)

    def main_monitoring_loop(self, requested_check: bool = False) -> None:
        self.connect_to_cluster_nodes_and_cleanup_old_nodes()
        thread_count = len(self.db_conns) + len(self.config.get("observers", {}))
        futures = []
        always_observers = not self.config.get("poll_observers_on_warning_only")
        with ThreadPoolExecutor(max_workers=thread_count) as tex:
            for instance, db_conn in self.db_conns.items():
                futures.append(tex.submit(self.update_cluster_member_state, instance, db_conn))
            if always_observers or self.is_replication_lag_over_warning_limit():
                for instance, uri in self.config.get("observers", {}).items():
                    futures.append(tex.submit(self.fetch_observer_state, instance, uri))
            for future in as_completed(futures):
                if future.exception():
                    self.log.error("Got error: %r when checking cluster state", future.exception())
        if requested_check:
            self.failover_decision_queue.put("Completed requested monitoring loop")

        self.last_monitoring_success_time = time.monotonic()

    def run(self) -> None:
        self.main_monitoring_loop()
        while self.running:
            requested_check = False
            try:
                requested_check = bool(
                    self.cluster_monitor_check_queue.get(timeout=self.config.get("db_poll_interval", 5.0))
                )
            except Empty:
                pass
            self.main_monitoring_loop(requested_check)
