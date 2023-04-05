"""
pglookout - webserver component

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""
from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer, SimpleHTTPRequestHandler
from logging import getLogger, Logger
from pglookout.common_types import MemberState
from pglookout.config import Config
from queue import Queue
from socketserver import ThreadingMixIn
from threading import Thread

import json
import threading


class ThreadedWebServer(ThreadingMixIn, HTTPServer):
    allow_reuse_address: bool = True

    def __init__(
        self,
        address: str,
        port: int,
        RequestHandlerClass: type[BaseHTTPRequestHandler],
        cluster_state: dict[str, MemberState],
        log: Logger,
        cluster_monitor_check_queue: Queue[str],
    ) -> None:
        super().__init__((address, port), RequestHandlerClass)
        self.cluster_state: dict[str, MemberState] = cluster_state
        self.log: Logger = log
        self.cluster_monitor_check_queue: Queue[str] = cluster_monitor_check_queue


class WebServer(Thread):
    def __init__(
        self, config: Config, cluster_state: dict[str, MemberState], cluster_monitor_check_queue: Queue[str]
    ) -> None:
        super().__init__()
        self.config: Config = config
        self.cluster_state: dict[str, MemberState] = cluster_state
        self.cluster_monitor_check_queue: Queue[str] = cluster_monitor_check_queue
        self.log: Logger = getLogger("WebServer")
        self.address: str = self.config.get("http_address", "")
        self.port: int = self.config.get("http_port", 15000)
        self.server: ThreadedWebServer | None = None
        self.log.debug("WebServer initialized with address: %r port: %r", self.address, self.port)
        self.is_initialized: threading.Event = threading.Event()

    def run(self) -> None:
        # We bind the port only when we start running
        self.server = ThreadedWebServer(
            address=self.address,
            port=self.port,
            RequestHandlerClass=RequestHandler,
            cluster_state=self.cluster_state,
            log=self.log,
            cluster_monitor_check_queue=self.cluster_monitor_check_queue,
        )
        self.is_initialized.set()
        self.server.serve_forever()

    def close(self) -> None:
        if self.server is None:
            return

        self.log.debug("Closing WebServer")
        self.server.shutdown()
        self.log.debug("Closed WebServer")


class RequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self) -> None:
        assert isinstance(self.server, ThreadedWebServer), f"server: {self.server!r}"
        self.server.log.debug("Got request: %r", self.path)
        if self.path.startswith("/state.json"):
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            response = json.dumps(self.server.cluster_state, indent=4).encode("utf8")
            self.send_header("Content-length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
        else:
            self.send_response(404)

    def do_POST(self) -> None:
        assert isinstance(self.server, ThreadedWebServer), f"server: {self.server!r}"
        self.server.log.debug("Got request: %r", self.path)
        if self.path.startswith("/check"):
            self.server.cluster_monitor_check_queue.put("request from webserver")
            self.server.log.info("Immediate status check requested")
            self.send_response(204)
            self.send_header("Content-length", str(0))
            self.end_headers()
        else:
            self.send_response(404)
