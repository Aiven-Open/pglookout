"""
pglookout - webserver component

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""
import json
from logging import getLogger
from threading import Thread

try:
    from SocketServer import ThreadingMixIn  # pylint: disable=import-error
    from BaseHTTPServer import HTTPServer  # pylint: disable=import-error
    from SimpleHTTPServer import SimpleHTTPRequestHandler  # pylint: disable=import-error
except ImportError:  # Support Py3k
    from socketserver import ThreadingMixIn  # pylint: disable=import-error
    from http.server import HTTPServer, SimpleHTTPRequestHandler  # pylint: disable=import-error


class ThreadedWebServer(ThreadingMixIn, HTTPServer):
    cluster_state = None
    log = None
    allow_reuse_address = True


class WebServer(Thread):
    def __init__(self, config, cluster_state):
        Thread.__init__(self)
        self.config = config
        self.cluster_state = cluster_state
        self.log = getLogger("WebServer")
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
        if self.server:
            self.log.debug("Closing WebServer")
            self.server.shutdown()
            self.log.debug("Closed WebServer")


class RequestHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        self.server.log.debug("Got request: %r", self.path)
        if self.path.startswith("/state.json"):
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            response = json.dumps(self.server.cluster_state, indent=4).encode("utf8")
            self.send_header('Content-length', len(response))
            self.end_headers()
            self.wfile.write(response)
        else:
            self.send_response(404)
