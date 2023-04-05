# Copied from https://github.com/ohmu/ohmu_common_py ohmu_common_py/logutil.py version 0.0.1-0-unknown-fa54b44
"""
pglookout - logging formats and utility functions

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""
from __future__ import annotations

from logging.handlers import SysLogHandler
from typing import Final, TYPE_CHECKING

import logging
import os

with_systemd: bool = False
try:
    from systemd import daemon

    with_systemd = True
except ImportError:
    if not TYPE_CHECKING:
        daemon = None

LOG_FORMAT: Final[str] = "%(asctime)s\t%(name)s\t%(threadName)s\t%(levelname)s\t%(message)s"
LOG_FORMAT_SHORT: Final[str] = "%(levelname)s\t%(message)s"
LOG_FORMAT_SYSLOG: Final[str] = "%(name)s %(threadName)s %(levelname)s: %(message)s"


def set_syslog_handler(address: str, facility: str | int, logger: logging.Logger) -> SysLogHandler:
    if isinstance(facility, str):
        facility_id: int = SysLogHandler.facility_names.get(facility, SysLogHandler.LOG_LOCAL2)
    else:
        facility_id = facility
    syslog_handler = SysLogHandler(address=address, facility=facility_id)
    logger.addHandler(syslog_handler)
    formatter = logging.Formatter(LOG_FORMAT_SYSLOG)
    syslog_handler.setFormatter(formatter)
    return syslog_handler


def configure_logging(level: int = logging.DEBUG, short_log: bool = False) -> None:
    # Are we running under systemd?
    if os.getenv("NOTIFY_SOCKET"):
        logging.basicConfig(level=level, format=LOG_FORMAT_SYSLOG)
        if not with_systemd:
            print("WARNING: Running under systemd but python-systemd not available, systemd won't see our notifications")
    else:
        logging.basicConfig(level=level, format=LOG_FORMAT_SHORT if short_log else LOG_FORMAT)


def notify_systemd(status: str) -> None:
    if with_systemd:
        daemon.notify(status)
