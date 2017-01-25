"""
pglookout

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""

from pglookout import statsd
from pglookout.cluster_monitor import ClusterMonitor
from datetime import datetime, timedelta

try:
    from queue import Queue  # pylint: disable=import-error
except ImportError:
    from Queue import Queue  # pylint: disable=import-error


def test_replication_lag():
    # pylint: disable=protected-access
    now = datetime.now()
    status = {
        "db_time": now,
        "pg_is_in_recovery": True,
        "pg_last_xact_replay_timestamp": now,
        "pg_last_xlog_receive_location": "0/0000001",
        "pg_last_xlog_replay_location": "0/0000002",
    }
    result = ClusterMonitor._parse_status_query_result(status.copy())
    assert result["replication_time_lag"] == 0.0
    status["db_time"] += timedelta(seconds=50, microseconds=42)
    result = ClusterMonitor._parse_status_query_result(status.copy())
    assert result["replication_time_lag"] == 50.000042
    status["db_time"] = now + timedelta(hours=42)
    result = ClusterMonitor._parse_status_query_result(status.copy())
    assert result["replication_time_lag"] == 151200.0


def test_main_loop(db):
    config = {
        "remote_conns": {
            "test1db": db.connection_string("testuser"),
            "test2db": db.connection_string("otheruser"),
        },
    }
    cluster_state = {}
    observer_state = {}

    def create_alert_file(arg):
        raise Exception(arg)

    trigger_check_queue = Queue()
    trigger_check_queue.put("test entry so we don't wait five seconds to get one")

    cm = ClusterMonitor(
        config=config,
        cluster_state=cluster_state,
        observer_state=observer_state,
        create_alert_file=create_alert_file,
        trigger_check_queue=trigger_check_queue,
        stats=statsd.StatsClient(host=None),
    )
    cm.main_monitoring_loop()

    assert len(cm.cluster_state) == 2
    assert "test1db" in cm.cluster_state
    assert "test2db" in cm.cluster_state
