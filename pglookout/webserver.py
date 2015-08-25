"""
pglookout - webserver component

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from logging import getLogger
from threading import Thread

# Prefer simplejson over json as on Python2.6 json does not play together
# nicely with other libraries as it loads strings in unicode and for example
# SysLogHandler does not like getting syslog facility as unicode string.
try:
    import simplejson as json  # pylint: disable=F0401
except ImportError:
    import json

try:
    from SocketServer import ThreadingMixIn  # pylint: disable=F0401
    from BaseHTTPServer import HTTPServer  # pylint: disable=F0401
    from SimpleHTTPServer import SimpleHTTPRequestHandler  # pylint: disable=F0401
except ImportError:  # Support Py3k
    from socketserver import ThreadingMixIn  # pylint: disable=F0401
    from http.server import HTTPServer, SimpleHTTPRequestHandler  # pylint: disable=F0401


class ThreadedWebServer(ThreadingMixIn, HTTPServer):
    cluster_state = None
    log = None


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
