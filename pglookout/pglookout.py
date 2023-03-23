"""
pglookout - replication monitoring and failover daemon

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""
from __future__ import annotations

from argparse import ArgumentParser
from copy import deepcopy
from datetime import datetime, timedelta
from logging import DEBUG, getLevelNamesMapping, getLogger, Logger
from logging.handlers import SysLogHandler
from packaging.version import parse as parse_version
from pathlib import Path
from pglookout.cluster_monitor import ClusterMonitor
from pglookout.common import convert_xlog_location_to_offset, get_iso_timestamp, parse_iso_datetime
from pglookout.common_types import MemberState, ObservedState
from pglookout.config import Config, Statsd
from pglookout.default import (
    JSON_STATE_FILE_PATH,
    MAINTENANCE_MODE_FILE,
    MAX_FAILOVER_REPLICATION_TIME_LAG,
    MISSING_MASTER_FROM_CONFIG_TIMEOUT,
    PG_DATA_DIRECTORY,
    REPLICATION_CATCHUP_TIMEOUT,
    WARNING_REPLICATION_TIME_LAG,
)
from pglookout.logutil import configure_logging, notify_systemd, set_syslog_handler
from pglookout.pgutil import (
    ConnectionParameterKeywords,
    create_connection_string,
    get_connection_info,
    get_connection_info_from_config_line,
)
from pglookout.statsd import StatsClient
from pglookout.version import __version__
from pglookout.webserver import WebServer
from psycopg2.extensions import adapt, QuotedString
from queue import Empty, Queue
from signal import SIGHUP, SIGINT, signal, SIGTERM
from socket import gethostname
from subprocess import CalledProcessError, check_call
from types import FrameType
from typing import cast, Final, Optional

import json
import sys
import time

DEFAULT_LOG_LEVEL: Final[str] = "DEBUG"
LOG_LEVEL_NAMES_MAPPING: Final[dict[str, int]] = getLevelNamesMapping()


class PgLookout:
    def __init__(self, config_path: Path | str) -> None:
        self.log: Logger = getLogger("pglookout")
        # dummy to make sure we never get an AttributeError -> gets overwritten after the first config loading
        self.stats: StatsClient = StatsClient(host=None)
        self.running: bool = True
        self.replication_lag_over_warning_limit: bool = False

        self.config_path: Path = Path(config_path)
        self.config: Config = {}
        self.log_level: int = LOG_LEVEL_NAMES_MAPPING[DEFAULT_LOG_LEVEL]

        self.connected_master_nodes: dict[str, MemberState] = {}
        self.disconnected_master_nodes: dict[str, MemberState] = {}
        self.connected_observer_nodes: dict[str, str | None] = {}  # name => ISO fetch time
        self.disconnected_observer_nodes: dict[str, str | None] = {}  # name => ISO fetch time
        self.replication_catchup_timeout: float = REPLICATION_CATCHUP_TIMEOUT
        self.replication_lag_warning_boundary: float = WARNING_REPLICATION_TIME_LAG
        self.replication_lag_failover_timeout: float = MAX_FAILOVER_REPLICATION_TIME_LAG
        self.missing_master_from_config_timeout: float = MISSING_MASTER_FROM_CONFIG_TIMEOUT
        self.own_db: str = ""
        self.current_master: str | None = None
        self.failover_command: list[str] = []
        self.known_gone_nodes: list[str] = []
        self.over_warning_limit_command: str | None = None
        self.never_promote_these_nodes: list[str] = []
        self.primary_conninfo_template: ConnectionParameterKeywords = {}
        self.cluster_monitor: ClusterMonitor | None = None
        self.syslog_handler: SysLogHandler | None = None
        self.cluster_nodes_change_time: float = time.monotonic()
        self.cluster_monitor_check_queue: Queue[str] = Queue()
        self.failover_decision_queue: Queue[str] = Queue()
        self.observer_state_newer_than: datetime = datetime.min
        self._start_time: float | None = None
        self.load_config()

        signal(SIGHUP, self.load_config_from_signal)
        signal(SIGINT, self.quit)
        signal(SIGTERM, self.quit)

        self.cluster_state: dict[str, MemberState] = {}
        self.observer_state: dict[str, ObservedState] = {}

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
        self.webserver: WebServer = WebServer(self.config, self.cluster_state, self.cluster_monitor_check_queue)

        if not self._is_initial_state_valid():
            self.log.error("Initial state is invalid, exiting.")
            sys.exit(1)

        notify_systemd("READY=1")
        self.log.info(
            "PGLookout initialized, local hostname: %r, own_db: %r, cwd: %r",
            gethostname(),
            self.own_db,
            Path.cwd(),
        )

    def _is_initial_state_valid(self) -> bool:
        """Check if the initial state of PgLookout is valid.

        Note:
            This method is only needed because we had to pick some default values before loading the config.
            A better approach would be to load the config first, and then initialize PgLookout.
            For that, :any:`load_config` should be split into two methods: one for loading the config, and one for
            applying the config when doing hot reloads. The method loading the config should make minimal usage of
            ``self`` and return a new ``Config`` object. The method applying the config should accept a ``Config``
            object and apply it to ``self``. Meanwhile, ``__init__`` should use this ``Config`` object to initialize
            itself, and it could be in different ways than during hot reloads (some internals don't depend on the config).

        Note:
            To developers: Any attribute that should be mandatory in the config should be checked here.
        """
        is_valid = True

        if not self.config or not self.config_path.is_file():
            self.log.error("Config is empty!")
            is_valid = False

        if not self.own_db:
            self.log.error("`own_db` has not been set!")
            is_valid = False

        return is_valid

    def quit(self, _signal: int | None = None, _frame: FrameType | None = None) -> None:
        if self.cluster_monitor is None:
            raise RuntimeError("Cluster monitor is not initialized!")

        self.log.warning("Quitting, signal: %r, frame: %r", _signal, _frame)
        self.cluster_monitor.running = False
        self.running = False
        self.webserver.close()

    def load_config_from_signal(self, _signal: int, _frame: FrameType | None = None) -> None:
        self.log.debug(
            "Loading JSON config from: %r, signal: %r, frame: %r",
            self.config_path,
            _signal,
            _frame,
        )
        self.load_config()

    def load_config(self) -> None:
        self.log.debug("Loading JSON config from: %r", self.config_path)

        previous_remote_conns = self.config.get("remote_conns")
        try:
            with self.config_path.open() as fp:
                self.config = json.load(fp)
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Invalid JSON config, exiting")
            self.stats.unexpected_exception(ex, where="load_config")
            sys.exit(1)

        # statsd settings may have changed
        stats: Statsd = self.config.get("statsd", {})
        self.stats = StatsClient(**stats)

        if previous_remote_conns != self.config.get("remote_conns"):
            self.cluster_nodes_change_time = time.monotonic()

        if self.config.get("autofollow", False):
            try:
                self.primary_conninfo_template = get_connection_info(self.config["primary_conninfo_template"])
            except (KeyError, ValueError):
                self.log.exception("Invalid or missing primary_conninfo_template; not enabling autofollow")
                self.config["autofollow"] = False

        if self.cluster_monitor is not None:
            self.cluster_monitor.config = deepcopy(self.config)

        if self.config.get("syslog") and self.syslog_handler is None:
            self.syslog_handler = set_syslog_handler(
                address=self.config.get("syslog_address", "/dev/log"),
                facility=self.config.get("syslog_facility", "local2"),
                logger=getLogger(),
            )
        self.own_db = self.config.get("own_db", "")

        log_level_name = self.config.get("log_level", DEFAULT_LOG_LEVEL)
        self.log_level = LOG_LEVEL_NAMES_MAPPING[log_level_name]
        try:
            self.log.setLevel(self.log_level)
            if self.cluster_monitor is not None:
                self.cluster_monitor.log.setLevel(self.log_level)
        except ValueError:
            print(f"Problem setting log level {self.log_level!r}")
            self.log.exception("Problem with log_level: %r", self.log_level)
        self.known_gone_nodes = self.config.get("known_gone_nodes", [])
        self.never_promote_these_nodes = self.config.get("never_promote_these_nodes", [])
        # we need the failover_command to be converted into subprocess [] format
        # XXX BF-1971: The next two lines are potentially unsafe. We should use shlex.split instead.
        self.failover_command = self.config.get("failover_command", "").split()
        self.over_warning_limit_command = self.config.get("over_warning_limit_command")
        self.replication_lag_warning_boundary = self.config.get("warning_replication_time_lag", WARNING_REPLICATION_TIME_LAG)
        self.replication_lag_failover_timeout = self.config.get(
            "max_failover_replication_time_lag", MAX_FAILOVER_REPLICATION_TIME_LAG
        )
        self.replication_catchup_timeout = self.config.get("replication_catchup_timeout", REPLICATION_CATCHUP_TIMEOUT)
        self.missing_master_from_config_timeout = self.config.get(
            "missing_master_from_config_timeout", MISSING_MASTER_FROM_CONFIG_TIMEOUT
        )

        if self.replication_lag_warning_boundary >= self.replication_lag_failover_timeout:
            msg = "Replication lag warning boundary (%s) is not lower than its failover timeout (%s)"
            self.log.warning(
                msg,
                self.replication_lag_warning_boundary,
                self.replication_lag_failover_timeout,
            )
            if self.replication_lag_warning_boundary > self.replication_lag_failover_timeout:
                self.replication_lag_warning_boundary = self.replication_lag_failover_timeout
                msg = "Replication lag warning boundary set to %s"
                self.log.warning(msg, self.replication_lag_warning_boundary)
        self.log.debug("Loaded config: %r from: %r", self.config, self.config_path)
        self.cluster_monitor_check_queue.put("new config came, recheck")

    def write_cluster_state_to_json_file(self) -> None:
        """Periodically write a JSON state file to disk

        Currently only used to share state with the current_master helper command, pglookout itself does
        not rely in this file.
        """
        start_time = time.monotonic()
        state_file_path = Path(self.config.get("json_state_file_path", JSON_STATE_FILE_PATH))
        overall_state = {
            "db_nodes": self.cluster_state,
            "observer_nodes": self.observer_state,
            "current_master": self.current_master,
        }
        try:
            json_to_dump = json.dumps(overall_state, indent=4)
            self.log.debug(
                "Writing JSON state file to: %s, file_size: %r",
                state_file_path,
                len(json_to_dump),
            )

            state_file_path_tmp = state_file_path.with_name(f"{state_file_path.name}.tmp")
            state_file_path_tmp.write_text(json_to_dump)
            state_file_path_tmp.rename(state_file_path)

            self.log.debug(
                "Wrote JSON state file to disk, took %.4fs",
                time.monotonic() - start_time,
            )
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception(
                "Problem in writing JSON: %r file to disk, took %.4fs",
                overall_state,
                time.monotonic() - start_time,
            )
            self.stats.unexpected_exception(ex, where="write_cluster_state_to_json_file")

    def create_node_map(
        self, cluster_state: dict[str, MemberState], observer_state: dict[str, ObservedState]
    ) -> tuple[str | None, MemberState | None, dict[str, MemberState]]:
        """Computes roles for each known member of cluster.

        Use the information gathered in the ``cluster_state`` and ``observer_state`` to figure out the roles of each member.

        Returns:
            A 3-tuple with the following elements:
            - The name of the master instance
            - The state of the master instance
            - A dictionary of the standby instances and their states
        """
        master_instance: str | None = None
        master_node: MemberState | None = None
        standby_nodes: dict[str, MemberState] = {}

        connected_master_nodes: dict[str, MemberState] = {}
        disconnected_master_nodes: dict[str, MemberState] = {}
        connected_observer_nodes: dict[str, str | None] = {}
        disconnected_observer_nodes: dict[str, str | None] = {}

        self.log.debug(
            "Creating node map out of cluster_state: %r and observer_state: %r",
            cluster_state,
            observer_state,
        )

        for instance_name, member_state in cluster_state.items():
            if "pg_is_in_recovery" in member_state:
                if member_state["pg_is_in_recovery"]:
                    standby_nodes[instance_name] = member_state
                elif member_state["connection"]:
                    connected_master_nodes[instance_name] = member_state
                else:
                    disconnected_master_nodes[instance_name] = member_state
            else:
                self.log.debug(
                    "No knowledge on instance: %r state: %r of whether it's in recovery or not",
                    instance_name,
                    member_state,
                )

        for observer_name, observed_state in observer_state.items():  # pylint: disable=too-many-nested-blocks
            connected = cast(bool, observed_state.get("connection", False))
            state_fetch_time = cast(Optional[str], observed_state.get("fetch_time"))
            if connected:
                connected_observer_nodes[observer_name] = state_fetch_time
            else:
                disconnected_observer_nodes[observer_name] = state_fetch_time
            for ob_state_key, ob_state_value in observed_state.items():
                if ob_state_key in ["connection", "fetch_time"]:
                    continue

                observed_member_name = ob_state_key
                observed_member_state = cast(MemberState, ob_state_value)

                if observed_member_name not in cluster_state:
                    # A single observer can observe multiple different replication clusters.
                    # Ignore data on nodes that don't belong in our own cluster
                    self.log.debug(
                        "Ignoring instance: %r since it does not belong into our own replication cluster",
                        observed_member_name,
                    )
                    continue

                if not isinstance(observed_member_state, dict):  # other keys are "connection" and "fetch_time"
                    self.log.error(
                        "Observer %r has invalid state for instance %r: %r",
                        observer_name,
                        observed_member_name,
                        observed_member_state,
                    )
                    self.log.error(
                        "Allowed keys are 'connection' (bool) and 'fetch_time' (str)"
                        " and all observed instance names for the cluster."
                    )
                    continue

                own_fetch_time = parse_iso_datetime(cluster_state[observed_member_name]["fetch_time"])
                observer_fetch_time = parse_iso_datetime(observed_member_state["fetch_time"])
                self.log.debug(
                    "observer_name: %r, instance: %r, state: %r, observer_fetch_time: %r",
                    observer_name,
                    observed_member_name,
                    observed_member_state,
                    observer_fetch_time,
                )
                if "pg_is_in_recovery" in observed_member_state:
                    if observed_member_state["pg_is_in_recovery"]:
                        # we always trust ourselves the most for localhost, and
                        # in case we are actually connected to the other node
                        if observer_fetch_time >= own_fetch_time and observed_member_name != self.own_db:
                            if (
                                observed_member_name not in standby_nodes
                                or standby_nodes[observed_member_name]["connection"] is False
                            ):
                                standby_nodes[observed_member_name] = observed_member_state
                    else:
                        master_node = connected_master_nodes.get(observed_member_name, MemberState())
                        connected = master_node.get("connection", False)
                        self.log.debug(
                            "Observer: %r sees %r as master, we see: %r, same_master: %r, connection: %r",
                            observer_name,
                            observed_member_name,
                            self.current_master,
                            observed_member_name == self.current_master,
                            observed_member_state.get("connection"),
                        )
                        if (
                            self.within_dbpoll_time(observer_fetch_time, own_fetch_time)
                            and observed_member_name != self.own_db
                        ):
                            if connected or observed_member_state["connection"]:
                                connected_master_nodes[observed_member_name] = observed_member_state
                            else:
                                disconnected_master_nodes[observed_member_name] = observed_member_state
                else:
                    self.log.warning(
                        "No knowledge on %r %r from observer: %r is in recovery",
                        observed_member_name,
                        observed_member_state,
                        observer_name,
                    )

        self.connected_master_nodes = connected_master_nodes
        self.disconnected_master_nodes = disconnected_master_nodes
        self.connected_observer_nodes = connected_observer_nodes
        self.disconnected_observer_nodes = disconnected_observer_nodes

        if not self.connected_master_nodes:
            self.log.warning(
                "No known master node, disconnected masters: %r",
                list(disconnected_master_nodes),
            )
            if disconnected_master_nodes:
                master_instance, master_node = list(disconnected_master_nodes.items())[0]
        elif len(self.connected_master_nodes) == 1:
            master_instance, master_node = list(connected_master_nodes.items())[0]
            if disconnected_master_nodes:
                self.log.warning(
                    "Picked %r as master since %r are in a disconnected state",
                    master_instance,
                    disconnected_master_nodes,
                )
        else:
            self.create_alert_file("multiple_master_warning")
            self.log.error(
                "More than one master node connected_master_nodes: %r, disconnected_master_nodes: %r",
                connected_master_nodes,
                disconnected_master_nodes,
            )

        return master_instance, master_node, standby_nodes

    def is_restoring_or_catching_up_normally(self, state: MemberState) -> bool:
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

    def emit_stats(self, state: MemberState) -> None:
        if self.is_restoring_or_catching_up_normally(state):
            # do not emit misleading lag stats during catchup at restore
            return

        replication_time_lag = state.get("replication_time_lag")
        if replication_time_lag is not None:
            self.stats.gauge("pg.replication_lag", replication_time_lag)

    def is_master_observer_new_enough(self, observer_state: dict[str, ObservedState]) -> bool:
        if not self.replication_lag_over_warning_limit:
            return True

        if not self.current_master or self.current_master not in self.config.get("observers", {}):
            self.log.warning(
                "Replication lag is over warning limit, but"
                " current master (%s) is not configured to be polled via observers",
                self.current_master,
            )
            return True

        db_poll_intervals = timedelta(seconds=5 * self.config.get("db_poll_interval", 5.0))
        now = datetime.utcnow()
        if (now - self.observer_state_newer_than) < db_poll_intervals:
            self.log.warning(
                "Replication lag is over warning limit, but"
                " not waiting for observers to be polled because 5 db_poll_intervals have passed"
            )
            return True

        if self.current_master not in observer_state:
            self.log.warning(
                "Replication lag is over warning limit, but observer for master (%s) has not been polled yet",
                self.current_master,
            )
            return False

        fetch_time = parse_iso_datetime(cast(str, observer_state[self.current_master]["fetch_time"]))
        if fetch_time < self.observer_state_newer_than:
            self.log.warning(
                "Replication lag is over warning limit, but observer's data for master  is stale, older than %r",
                self.observer_state_newer_than,
            )
            return False

        return True

    def check_cluster_state(self) -> None:
        # master_node = None
        cluster_state = deepcopy(self.cluster_state)
        observer_state = deepcopy(self.observer_state)
        configured_node_count = len(self.config.get("remote_conns", {}))
        if not cluster_state or len(cluster_state) != configured_node_count:
            self.log.warning(
                "No cluster state: %r, probably still starting up, node_count: %r, configured node_count: %r",
                cluster_state,
                len(cluster_state),
                configured_node_count,
            )
            return

        if self.config.get("poll_observers_on_warning_only") and not self.is_master_observer_new_enough(observer_state):
            self.log.warning("observer data is not good enough, skipping check")
            return

        master_instance, master_node, standby_nodes = self.create_node_map(cluster_state, observer_state)

        if master_instance and master_instance != self.current_master:
            self.log.info(
                "New master node detected: old: %r new: %r: %r",
                self.current_master,
                master_instance,
                master_node,
            )
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
        self.log.debug(
            "Cluster has %s standbys, %s observers and %s as master, own_db: %r, own_state: %r",
            standby_info,
            observer_info,
            self.current_master,
            self.own_db,
            own_state or "observer",
        )

        if self.own_db:
            if self.own_db == self.current_master:
                # We are the master of this cluster, nothing to do
                self.log.debug(
                    "We %r: %r are still the master node: %r of this cluster, nothing to do.",
                    self.own_db,
                    own_state,
                    master_node,
                )
                return
            if not standby_nodes:
                self.log.warning("No standby nodes set, master node: %r", master_node)
                return
            self.consider_failover(own_state, master_node, standby_nodes)

    def consider_failover(
        self,
        own_state: MemberState,
        master_node: MemberState | None,
        standby_nodes: dict[str, MemberState],
    ) -> None:
        if not master_node:
            # no master node at all in the cluster?
            self.log.warning(
                "No master node in cluster, %r standby nodes exist, "
                "%.2f seconds since last cluster config update, failover timeout set "
                "to %r seconds, previous master: %r",
                len(standby_nodes),
                time.monotonic() - self.cluster_nodes_change_time,
                self.replication_lag_failover_timeout,
                self.current_master,
            )
            if self.current_master:
                self.cluster_monitor_check_queue.put("Master is missing, ask for immediate state check")
                master_known_to_be_gone = self.current_master in self.known_gone_nodes
                now = time.monotonic()
                config_timeout_exceeded = (now - self.cluster_nodes_change_time) >= self.missing_master_from_config_timeout
                if master_known_to_be_gone or config_timeout_exceeded:
                    # we've seen a master at some point in time, but now it's
                    # missing, perform an immediate failover to promote one of
                    # the standbys
                    self.log.warning(
                        "Performing failover decision because existing master node disappeared from configuration"
                    )
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

    def is_replication_lag_over_warning_limit(self) -> bool:
        return self.replication_lag_over_warning_limit

    def check_replication_lag(self, own_state: MemberState, standby_nodes: dict[str, MemberState]) -> None:
        if self.is_restoring_or_catching_up_normally(own_state):
            # do not raise alerts during catchup at restore
            return

        replication_lag = own_state.get("replication_time_lag")
        if not replication_lag:
            self.log.warning("No replication lag set in own node state: %r", own_state)
            return
        if replication_lag >= self.replication_lag_warning_boundary:
            self.log.warning(
                "Replication time lag has grown to: %r which is over WARNING boundary: %r, %r",
                replication_lag,
                self.replication_lag_warning_boundary,
                self.replication_lag_over_warning_limit,
            )
            if not self.replication_lag_over_warning_limit:  # we just went over the boundary
                self.replication_lag_over_warning_limit = True
                if self.config.get("poll_observers_on_warning_only"):
                    self.observer_state_newer_than = datetime.utcnow()
                self.create_alert_file("replication_delay_warning")
                if self.over_warning_limit_command:
                    self.log.warning(
                        "Executing over_warning_limit_command: %r",
                        self.over_warning_limit_command,
                    )
                    return_code = self.execute_external_command(self.over_warning_limit_command)
                    self.log.warning(
                        "Executed over_warning_limit_command: %r, return_code: %r",
                        self.over_warning_limit_command,
                        return_code,
                    )
                else:
                    self.log.warning("No over_warning_limit_command set")
                # force looping one more time since we just passed the warning limit
                return
        elif self.replication_lag_over_warning_limit:
            self.replication_lag_over_warning_limit = False
            self.delete_alert_file("replication_delay_warning")
            self.observer_state_newer_than = datetime.min

        if replication_lag >= self.replication_lag_failover_timeout:
            self.log.warning(
                "Replication time lag has grown to: %r which is over CRITICAL boundary: %r"
                ", checking if we need to failover",
                replication_lag,
                self.replication_lag_failover_timeout,
            )
            self.do_failover_decision(own_state, standby_nodes)
        else:
            self.log.debug(
                "Replication lag was: %r, other nodes status was: %r",
                replication_lag,
                standby_nodes,
            )

    def get_replication_positions(self, standby_nodes: dict[str, MemberState]) -> dict[int, set[str]]:
        self.log.debug("Getting replication positions from: %r", standby_nodes)
        known_replication_positions: dict[int, set[str]] = {}
        for instance, node_state in standby_nodes.items():
            now = datetime.utcnow()
            if (
                node_state["connection"]
                and now - parse_iso_datetime(node_state["fetch_time"]) < timedelta(seconds=20)
                and instance not in self.never_promote_these_nodes
            ):  # noqa # pylint: disable=line-too-long
                # use pg_last_xlog_receive_location if it's available,
                # otherwise fall back to pg_last_xlog_replay_location but
                # note that both of them can be None.  We prefer
                # receive_location over replay_location as some nodes may
                # not yet have replayed everything they've received, but
                # also consider the replay location in case receive_location
                # is empty as a node that has been brought up from backups
                # without ever connecting to a master will not have an empty
                # pg_last_xlog_receive_location
                lsn = node_state["pg_last_xlog_receive_location"] or node_state["pg_last_xlog_replay_location"]
                wal_pos = convert_xlog_location_to_offset(lsn) if lsn else 0
                known_replication_positions.setdefault(wal_pos, set()).add(instance)
        return known_replication_positions

    def _been_in_contact_with_master_within_failover_timeout(self) -> bool:
        # no need to do anything here if there are no disconnected masters
        if self.disconnected_master_nodes:
            disconnected_master_node = list(self.disconnected_master_nodes.values())[0]
            db_time = disconnected_master_node.get("db_time", get_iso_timestamp()) or get_iso_timestamp()
            db_time_as_dt = db_time if isinstance(db_time, datetime) else parse_iso_datetime(db_time)
            time_since_last_contact = datetime.utcnow() - db_time_as_dt
            if time_since_last_contact < timedelta(seconds=self.replication_lag_failover_timeout):
                self.log.debug(
                    "We've had contact with master: %r at: %r within the last %.2fs, not failing over",
                    disconnected_master_node,
                    db_time,
                    time_since_last_contact.total_seconds(),
                )
                return True
        return False

    def do_failover_decision(self, own_state: MemberState, standby_nodes: dict[str, MemberState]) -> None:
        if self.connected_master_nodes:
            self.log.warning(
                "We still have some connected masters: %r, not failing over",
                self.connected_master_nodes,
            )
            return
        if self._been_in_contact_with_master_within_failover_timeout():
            self.log.warning(
                "No connected master nodes, but last contact was still within failover timeout (%ss), not failing over",
                self.replication_lag_failover_timeout,
            )
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
        self.log.warning(
            "Node that is furthest along is: %r, all replication positions were: %r",
            furthest_along_instance,
            sorted(known_replication_positions),
        )
        total_observers = len(self.connected_observer_nodes) + len(self.disconnected_observer_nodes)
        # +1 in the calculation comes from the master node
        total_amount_of_nodes = len(standby_nodes) + 1 - len(self.never_promote_these_nodes) + total_observers
        size_of_needed_majority = total_amount_of_nodes * 0.5
        amount_of_known_replication_positions = 0
        for known_replication_position in known_replication_positions.values():
            amount_of_known_replication_positions += len(known_replication_position)
        size_of_known_state = amount_of_known_replication_positions + len(self.connected_observer_nodes)
        self.log.debug(
            "Size of known state: %.2f, needed majority: %r, %r/%r",
            size_of_known_state,
            size_of_needed_majority,
            amount_of_known_replication_positions,
            int(total_amount_of_nodes),
        )

        if standby_nodes[furthest_along_instance] == own_state:
            if self.check_for_maintenance_mode_file():
                self.log.warning(
                    "Canceling failover even though we were the node the furthest along, since "
                    "this node has an existing maintenance_mode_file: %r",
                    self.config.get("maintenance_mode_file", MAINTENANCE_MODE_FILE),
                )
            elif self.own_db in self.never_promote_these_nodes:
                self.log.warning(
                    "Not doing a failover even though we were the node the furthest along, since this node: %r"
                    " should never be promoted to master",
                    self.own_db,
                )
            elif size_of_known_state < size_of_needed_majority:
                self.log.warning(
                    "Not doing a failover even though we were the node the furthest along, since we aren't "
                    "aware of the states of enough of the other nodes"
                )
            else:
                start_time = time.monotonic()
                self.log.warning("We will now do a failover to ourselves since we were the instance furthest along")
                return_code = self.execute_external_command(self.failover_command)
                self.log.warning(
                    "Executed failover command: %r, return_code: %r, took: %.2fs",
                    self.failover_command,
                    return_code,
                    time.monotonic() - start_time,
                )
                self.create_alert_file("failover_has_happened")
                # Sleep for failover time to give the DB time to restart in promotion mode
                # You want to use this if the failover command is not one that blocks until
                # the db has restarted
                time.sleep(self.config.get("failover_sleep_time", 0.0))
                if return_code == 0:
                    self.replication_lag_over_warning_limit = False
                    self.delete_alert_file("replication_delay_warning")
        else:
            self.log.warning(
                "Nothing to do since node: %r is the furthest along",
                furthest_along_instance,
            )

    def modify_recovery_conf_to_point_at_new_master(self, new_master_instance: str) -> bool:
        pg_data_directory = Path(self.config.get("pg_data_directory", PG_DATA_DIRECTORY))
        pg_version = (pg_data_directory / "PG_VERSION").read_text().strip()

        if parse_version(pg_version) >= parse_version("12"):
            recovery_conf_filename = "postgresql.auto.conf"
        else:
            recovery_conf_filename = "recovery.conf"

        path_to_recovery_conf = pg_data_directory / recovery_conf_filename
        old_conf = path_to_recovery_conf.read_text().splitlines()

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
            self.log.debug(
                "recovery.conf already contains conninfo matching %r, not updating",
                new_master_instance,
            )
            return False

        # Otherwise we append the new primary_conninfo
        # Mypy: ignore the typing of `adapt`, as we cannot override it ourselves. The provided stubs are incomplete.
        # Writing our own stubs would discard all the other type information from `psycopg2.extensions`.
        quoted_connection_string: QuotedString = adapt(
            create_connection_string(new_conn_info)
        )  # type: ignore[no-untyped-call]
        new_conf.append(f"primary_conninfo = {quoted_connection_string}")

        # The timeline of the recovery.conf will require a higher timeline target
        if not has_recovery_target_timeline:
            new_conf.append("recovery_target_timeline = 'latest'")

        # prepend our tag
        iso_timestamp = get_iso_timestamp()
        new_conf.insert(
            0,
            f"# pglookout updated primary_conninfo for instance {new_master_instance} at {iso_timestamp}",
        )

        # Replace old recovery.conf with a fresh copy
        path_to_recovery_conf_new = path_to_recovery_conf.with_name(f"{path_to_recovery_conf.name}._temp")
        path_to_recovery_conf_new.write_text("\n".join(new_conf) + "\n")
        path_to_recovery_conf_new.rename(path_to_recovery_conf)

        return True

    def start_following_new_master(self, new_master_instance: str) -> None:
        start_time = time.monotonic()
        updated_config = self.modify_recovery_conf_to_point_at_new_master(new_master_instance)
        if not updated_config:
            self.log.info(
                "Already following master %r, no need to start following it again",
                new_master_instance,
            )
            return
        start_command = self.config.get("pg_start_command", "").split()
        stop_command = self.config.get("pg_stop_command", "").split()
        self.log.info(
            "Starting to follow new master %r, modified recovery.conf and restarting PostgreSQL"
            "; pg_start_command %r; pg_stop_command %r",
            new_master_instance,
            start_command,
            stop_command,
        )
        self.execute_external_command(stop_command)
        self.execute_external_command(start_command)
        self.log.info(
            "Started following new master %r, took: %.2fs",
            new_master_instance,
            time.monotonic() - start_time,
        )

    def execute_external_command(self, command: list[str] | str) -> int:
        self.log.warning("Executing external command: %r", command)
        return_code, output = 0, ""
        try:
            check_call(command)
        except CalledProcessError as err:
            self.log.exception(
                "Problem with executing: %r, return_code: %r, output: %r",
                command,
                err.returncode,
                err.output,
            )
            self.stats.unexpected_exception(err, where="execute_external_command")
            return_code = err.returncode  # pylint: disable=no-member
        self.log.warning("Executed external command: %r, output: %r", return_code, output)
        return return_code

    def check_for_maintenance_mode_file(self) -> bool:
        return Path(self.config.get("maintenance_mode_file", MAINTENANCE_MODE_FILE)).is_file()

    def create_alert_file(self, filename: str) -> None:
        alert_file_dir = Path(self.config.get("alert_file_dir", Path.cwd()))
        filepath = alert_file_dir / filename
        self.log.debug("Creating alert file: %r", str(filepath))
        try:
            filepath.write_text("alert")
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Problem writing alert file: %r", filename)
            self.stats.unexpected_exception(ex, where="create_alert_file")

    def delete_alert_file(self, filename: str) -> None:
        alert_file_dir = Path(self.config.get("alert_file_dir", Path.cwd()))
        filepath = alert_file_dir / filename
        try:
            if filepath.is_file():
                self.log.debug("Deleting alert file: %r", filepath)
                filepath.unlink(missing_ok=True)
        except Exception as ex:  # pylint: disable=broad-except
            self.log.exception("Problem unlinking: %r", filepath)
            self.stats.unexpected_exception(ex, where="delete_alert_file")

    def within_dbpoll_time(self, time1: datetime, time2: datetime) -> bool:
        return abs((time1 - time2).total_seconds()) < self.config.get("db_poll_interval", 5.0)

    def _check_cluster_monitor_thread_health(self, now: float) -> None:
        if self.cluster_monitor is None:
            raise RuntimeError("Cluster Monitor is not initialized!")

        health_timeout_seconds = self._get_health_timeout_seconds()
        if health_timeout_seconds:
            last_successful_run = self.cluster_monitor.last_monitoring_success_time or self._start_time
            # last_successful_run can only be None if main_loop or this function is called directly
            if last_successful_run is not None:
                seconds_since_last_run = now - last_successful_run
                if seconds_since_last_run >= health_timeout_seconds:
                    self.stats.increase("cluster_monitor_health_timeout")
                    self.log.warning("cluster_monitor has not been running for %.1f seconds", seconds_since_last_run)

    def _get_health_timeout_seconds(self) -> float | None:
        if "cluster_monitor_health_timeout_seconds" in self.config:
            config_value = self.config.get("cluster_monitor_health_timeout_seconds")
            return config_value if config_value is None else float(config_value)
        else:
            return self._get_check_interval() * 2

    def _get_check_interval(self) -> float:
        return float(self.config.get("replication_state_check_interval", 5.0))

    def main_loop(self) -> None:
        while self.running:
            try:
                self.check_cluster_state()
                self._check_cluster_monitor_thread_health(now=time.monotonic())
            except Exception as ex:  # pylint: disable=broad-except
                self.log.exception("Failed to check cluster state")
                self.stats.unexpected_exception(ex, where="main_loop_check_cluster_state")

            try:
                self.write_cluster_state_to_json_file()
            except Exception as ex:  # pylint: disable=broad-except
                self.log.exception("Failed to write cluster state")
                self.stats.unexpected_exception(ex, where="main_loop_writer_cluster_state")

            try:
                self.failover_decision_queue.get(timeout=self._get_check_interval())
                q = self.failover_decision_queue
                while not q.empty():
                    try:
                        q.get(False)
                    except Empty:
                        continue
                self.log.info("Immediate failover check completed")
            except Empty:
                pass

    def run(self) -> None:
        if self.cluster_monitor is None:
            raise RuntimeError("Cluster Monitor is not initialized!")

        self._start_time = time.monotonic()
        self.cluster_monitor.start()
        self.webserver.start()
        self.main_loop()


def get_argument_parser() -> ArgumentParser:
    parser = ArgumentParser(
        prog="pglookout",
        description="postgresql replication monitoring and failover daemon",
    )
    parser.add_argument(
        "--version",
        action="version",
        help="show program version",
        version=__version__,
    )
    # it's a type of filepath
    parser.add_argument("config", type=Path, help="configuration file")

    return parser


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]

    parser = get_argument_parser()
    arg = parser.parse_args(args)

    if not arg.config.is_file():
        print(f"pglookout: {arg.config!r} doesn't exist")
        return 1

    configure_logging()

    pglookout = PgLookout(arg.config)
    pglookout.run()

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
