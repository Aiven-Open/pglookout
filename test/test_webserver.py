"""
pglookout tests

Copyright (c) 2016 Ohmu Ltd

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""
from pglookout.webserver import WebServer
from queue import Queue

import random
import requests


def test_webserver():
    config = {
        "http_port": random.randint(10000, 32000),
    }
    cluster_state = {
        "hello": 123,
    }
    http_port = config["http_port"]
    base_url = f"http://127.0.0.1:{http_port}"
    cluster_monitor_check_queue = Queue()

    web = WebServer(config=config, cluster_state=cluster_state, cluster_monitor_check_queue=cluster_monitor_check_queue)
    try:
        web.start()
        # wait for the thread to have started, else we're blocking forever as web.close can't shutdown the thread
        web.is_initialized.wait(timeout=30.0)

        result = requests.get(f"{base_url}/state.json", timeout=5).json()
        assert result == cluster_state

        result = requests.post(f"{base_url}/check", timeout=5)
        assert result.status_code == 204
        res = cluster_monitor_check_queue.get(timeout=1.0)
        assert res == "request from webserver"
    finally:
        web.close()
