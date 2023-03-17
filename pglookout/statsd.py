# Copied from https://github.com/ohmu/ohmu_common_py ohmu_common_py/statsd.py version 0.0.1-0-unknown-b16ec0a
"""
pglookout - StatsD client

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details

Supports telegraf's statsd protocol extension for 'key=value' tags:

    https://github.com/influxdata/telegraf/tree/master/plugins/inputs/statsd
"""
from __future__ import annotations

from typing import Literal

import logging
import socket

StatsdMetricType = Literal[
    b"g",  # gauge
    b"c",  # counter
    b"s",  # set
    b"ms",  # timing
    b"h",  # histogram
    b"d",  # distribution
]


class StatsClient:
    def __init__(
        self,
        host: str | None = "127.0.0.1",
        port: int = 8125,
        tags: dict[str, str] | None = None,
    ) -> None:
        self.log: logging.Logger = logging.getLogger("StatsClient")
        self._dest_addr: tuple[str | None, int] = (host, port)
        self._socket: socket.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._tags: dict[str, str] = tags or {}

    def gauge(
        self,
        metric: str,
        value: int | float | str,
        tags: dict[str, str] | None = None,
    ) -> None:
        self._send(metric, b"g", value, tags)

    def increase(
        self,
        metric: str,
        inc_value: int | float = 1,
        tags: dict[str, str] | None = None,
    ) -> None:
        self._send(metric, b"c", inc_value, tags)

    def timing(
        self,
        metric: str,
        value: int | float,
        tags: dict[str, str] | None = None,
    ) -> None:
        self._send(metric, b"ms", value, tags)

    def unexpected_exception(
        self,
        ex: Exception,
        where: str,
        tags: dict[str, str] | None = None,
    ) -> None:
        all_tags = {
            "exception": ex.__class__.__name__,
            "where": where,
        }
        all_tags.update(tags or {})
        self.increase("exception", tags=all_tags)

    def _send(
        self,
        metric: str,
        metric_type: StatsdMetricType,
        value: int | float | str,
        tags: dict[str, str] | None,
    ) -> None:
        if None in self._dest_addr:
            # stats sending is disabled
            return

        try:
            # format: "user.logins,service=payroll,region=us-west:1|c"
            parts = [
                metric.encode("utf-8"),
                b":",
                str(value).encode("utf-8"),
                b"|",
                metric_type,
            ]
            send_tags = self._tags.copy()
            send_tags.update(tags or {})
            for tag, tag_value in send_tags.items():
                parts.insert(1, f",{tag}={tag_value}".encode("utf-8"))

            self._socket.sendto(b"".join(parts), self._dest_addr)
        except Exception as ex:  # pylint: disable=broad-except
            self.log.error("Unexpected exception in statsd send: %s: %s", ex.__class__.__name__, ex)
