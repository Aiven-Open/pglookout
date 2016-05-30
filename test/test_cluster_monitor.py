"""
pglookout

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""

from pglookout import statsd
from pglookout.cluster_monitor import ClusterMonitor
from psycopg2.extensions import POLL_OK
from datetime import datetime, timedelta

try:
    from queue import Queue  # pylint: disable=import-error
    from unittest.mock import MagicMock, Mock, patch  # pylint: disable=no-name-in-module
except ImportError:
    from Queue import Queue  # pylint: disable=import-error
    from mock import MagicMock, Mock, patch  # pylint: disable=import-error


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


@patch("psycopg2.connect")
def test_main_loop(psycopg2_connect):
    config = {"remote_conns":
              {"foo": "host=1.2.3.4 dbname=postgres user=pglookout password=fake_pass",
               "bar": "host=2.3.4.5 dbname=postgres user=pglookout password=fake_pass"}}
    cluster_state = {}
    observer_state = {}

    def alert_file_func(arg):  # pylint: disable=unused-argument
        pass

    create_alert_file = alert_file_func
    trigger_check_queue = Queue()
    trigger_check_queue.put("test entry so we don't wait five seconds to get one")

    class FakeCursor(MagicMock):  # pylint: disable=too-many-ancestors
        def execute(self, query):  # pylint: disable=no-self-use,unused-argument
            return

        def fetchone(self):  # pylint: disable=no-self-use
            return {"pg_is_in_recovery": False, "pg_last_xact_replay_timestamp": datetime.utcnow(),
                    "db_time": datetime.utcnow()}

    class FakeConn(Mock):  # pylint: disable=too-many-ancestors
        def cursor(self, cursor_factory):  # pylint: disable=unused-argument
            f = FakeCursor()
            f.connection = self  # pylint: disable=attribute-defined-outside-init
            return f

        def poll(self):  # pylint: disable=no-self-use
            return POLL_OK
    psycopg2_connect.return_value = FakeConn()

    cm = ClusterMonitor(
        config=config,
        cluster_state=cluster_state,
        observer_state=observer_state,
        create_alert_file=create_alert_file,
        trigger_check_queue=trigger_check_queue,
        stats=statsd.StatsClient(host=None))
    cm.main_monitoring_loop()

    assert len(cm.cluster_state) == 2
    assert "foo" in cm.cluster_state
    assert "bar" in cm.cluster_state
