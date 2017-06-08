"""
pglookout tests

Copyright (c) 2016 Ohmu Ltd

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""
from pglookout.webserver import WebServer
import random
import requests
import time


def test_webserver():
    config = {
        "http_port": random.randint(10000, 32000),
    }
    cluster_state = {
        "hello": 123,
    }
    base_url = "http://127.0.0.1:{}".format(config["http_port"])
    web = WebServer(config=config, cluster_state=cluster_state)
    try:
        web.start()
        time.sleep(1)
        result = requests.get("{}/state.json".format(base_url)).json()
        assert result == cluster_state
    finally:
        web.close()
