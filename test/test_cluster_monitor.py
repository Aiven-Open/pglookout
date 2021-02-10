"""
pglookout

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""

from mock import patch
from pglookout import statsd
from pglookout.cluster_monitor import ClusterMonitor
from datetime import datetime, timedelta
from queue import Queue


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
        "observers": {"local": "URL"},
        "poll_observers_on_warning_only": True
    }
    cluster_state = {}
    observer_state = {}

    def create_alert_file(arg):
        raise Exception(arg)

    cluster_monitor_check_queue = Queue()
    failover_decision_queue = Queue()

    cm = ClusterMonitor(
        config=config,
        cluster_state=cluster_state,
        observer_state=observer_state,
        create_alert_file=create_alert_file,
        cluster_monitor_check_queue=cluster_monitor_check_queue,
        failover_decision_queue=failover_decision_queue,
        stats=statsd.StatsClient(host=None),
        is_replication_lag_over_warning_limit=lambda: False
    )
    cm.main_monitoring_loop(requested_check=True)

    assert len(cm.cluster_state) == 2
    assert "test1db" in cm.cluster_state
    assert "test2db" in cm.cluster_state

    assert failover_decision_queue.get(timeout=5) == "Completed requested monitoring loop"

    with patch.object(cm, "fetch_observer_state") as fetch_observer_state:
        cm.main_monitoring_loop(requested_check=True)
        fetch_observer_state.assert_not_called()

    with patch.object(cm, "fetch_observer_state") as fetch_observer_state:
        with patch.object(cm, "is_replication_lag_over_warning_limit", lambda: True):
            cm.main_monitoring_loop(requested_check=True)
            fetch_observer_state.assert_called_once_with("local", "URL")

    with patch.object(cm, "fetch_observer_state") as fetch_observer_state:
        with patch.dict(cm.config, {"poll_observers_on_warning_only": False}):
            cm.main_monitoring_loop(requested_check=True)
            fetch_observer_state.assert_called_once_with("local", "URL")
