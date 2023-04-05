# Copyright (c) 2023 Aiven, Helsinki, Finland. https://aiven.io/
from __future__ import annotations

from pglookout.pgutil import ConnectionInfo
from typing import Literal, TypedDict


class Statsd(TypedDict, total=False):
    host: str
    port: int
    tags: dict[str, str]


class Config(TypedDict, total=False):
    alert_file_dir: str
    autofollow: bool
    cluster_monitor_health_timeout_seconds: float | None
    db_poll_interval: float
    failover_command: str
    failover_sleep_time: float
    http_address: str
    http_port: int
    json_state_file_path: str
    known_gone_nodes: list[str]
    log_level: Literal["NOTSET", "DEBUG", "INFO", "WARNING", "WARN", "ERROR", "FATAL", "CRITICAL"]
    maintenance_mode_file: str
    max_failover_replication_time_lag: float
    missing_master_from_config_timeout: float
    never_promote_these_nodes: list[str]
    observers: dict[str, str]
    over_warning_limit_command: str
    own_db: str
    pg_data_directory: str
    pg_start_command: str
    pg_stop_command: str
    poll_observers_on_warning_only: bool
    primary_conninfo_template: str
    remote_conns: dict[str, ConnectionInfo]
    replication_catchup_timeout: float
    replication_state_check_interval: float
    statsd: Statsd
    syslog: bool
    syslog_address: str
    # fmt: off
    # https://docs.python.org/3/library/logging.handlers.html#logging.handlers.SysLogHandler.encodePriority
    syslog_facility: Literal[
        "auth", "authpriv", "console", "cron", "daemon", "ftp", "kern", "lpr",
        "mail", "news", "ntp", "security", "solaris-cron", "syslog", "user", "uucp",
        "local0", "local1", "local2", "local3", "local4", "local5", "local6", "local7",
    ]
    # fmt: on
    warning_replication_time_lag: float
