"""
pglookout tests

Copyright (c) 2016 Ohmu Ltd

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""
from pglookout.webserver import WebServer
import requests


def test_webserver():
    config = {}
    cluster_state = {}
    web = WebServer(config=config, cluster_state=cluster_state)
    web.start()
    result = requests.get("http://127.0.0.1:15000/state.json").json()
    assert result == {}
    web.close()
