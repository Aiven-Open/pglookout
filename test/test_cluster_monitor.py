"""
pglookout

Copyright (c) 2015 Ohmu Ltd
See LICENSE for details
"""
from .conftest import TestPG
from contextlib import closing
from datetime import datetime, timedelta
from mock import patch
from packaging import version
from pglookout import statsd
from pglookout.cluster_monitor import ClusterMonitor
from pglookout.common import JsonObject
from psycopg2.extras import RealDictCursor
from queue import Queue
from typing import Callable, Optional, Tuple

import base64
import psycopg2
import pytest
import time


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
        "poll_observers_on_warning_only": True,
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
        is_replication_lag_over_warning_limit=lambda: False,
        replication_slots_cache={},
    )
    assert cm.last_monitoring_success_time is None
    before = time.monotonic()
    cm.main_monitoring_loop(requested_check=True)

    assert cm.last_monitoring_success_time is not None
    assert cm.last_monitoring_success_time > before
    before = cm.last_monitoring_success_time

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

    assert cm.last_monitoring_success_time > before


def test_fetch_replication_slot_info(db: TestPG) -> None:
    if version.parse(db.pgver) < version.parse("10"):
        pytest.skip(f"unsupported pg version: {db.pgver}")

    config = {
        "remote_conns": {
            "test1db": db.connection_string("testuser"),
            "test2db": db.connection_string("otheruser"),
        },
        "observers": {"local": "URL"},
        "poll_observers_on_warning_only": True,
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
        is_replication_lag_over_warning_limit=lambda: False,
        replication_slots_cache={},
    )
    cm.main_monitoring_loop(requested_check=True)

    with closing(psycopg2.connect(db.connection_string(), connect_timeout=15)) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT pg_catalog.pg_create_logical_replication_slot('testslot1', 'test_decoding')")

            replication_slots = cm._fetch_replication_slot_info("foo", cursor)  # pylint: disable=protected-access
            assert len(replication_slots) == 1
            slot = replication_slots[0]
            assert slot.slot_name == "testslot1"
            assert slot.plugin == "test_decoding"
            assert slot.slot_type == "logical"
            assert slot.database == "postgres"
            assert b"\0" in base64.b64decode(slot.state_data)

            cursor.execute("SELECT pg_drop_replication_slot('testslot1')")


def repl_slot1_data(slot_lsn: str) -> JsonObject:
    return {
        "slot_name": "test_slot_v1",
        "plugin": "wal2json",
        "slot_type": "logical",
        "database": "defaultdb",
        "catalog_xmin": "7565",
        "restart_lsn": "0/2F0021B0",
        "confirmed_flush_lsn": slot_lsn,
        "state_data": "oRwFAcg9zQUCAAAAuAAAAHRlc3Rfc2xvdF92MwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAdQAAAAAAAAAAAAACNHQAAsCEALwAAAAAAAAAAAAAAAOgh\n"
        "AC8AAAAA6CEALwAAAAAAd2FsMmpzb24AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }


def repl_slot2_data(slot_lsn: str) -> JsonObject:
    return {
        "slot_name": "test_slot_v2",
        "plugin": "wal2json",
        "slot_type": "logical",
        "database": "defaultdb",
        "catalog_xmin": "7565",
        "restart_lsn": "0/2F0021B0",
        "confirmed_flush_lsn": slot_lsn,
        "state_data": "oRwFAXYIR6MCAAAAuAAAAHRlc3Rfc2xvdF92MgAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAdQAAAAAAAAAAAAACNHQAACBkALwAAAAAAAAAAAAAAAEAZ\n"
        "AC8AAAAAQBkALwAAAAAAdGVzdF9kZWNvZGluZwAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\n"
        "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
    }


def create_query_cluster_member_state(
    slot1_lsn: Optional[str], slot2_lsn: Optional[str], standby_lsn: str
) -> Callable[[str, Tuple[str, bytes]], JsonObject]:
    def query_cluster_member_state(instance: str, _: Tuple[str, bytes]) -> JsonObject:
        # Master
        if instance == "test1db":
            return {
                "fetch_time": "2022-10-13T07:11:07.087062Z",
                "connection": True,
                "db_time": "2022-10-13T07:11:07.087648Z",
                "pg_is_in_recovery": False,
                "pg_last_xact_replay_timestamp": None,
                "pg_last_xlog_receive_location": None,
                "pg_last_xlog_replay_location": "1/E000548",
                "replication_slots": [
                    repl_slot1_data(slot1_lsn),
                    repl_slot2_data(slot2_lsn),
                ],
            }
        return {
            "fetch_time": "2022-10-13T07:11:07.087439Z",
            "connection": True,
            "db_time": "2022-10-13T07:11:07.087604Z",
            "pg_is_in_recovery": True,
            "pg_last_xact_replay_timestamp": "2022-10-13T07:11:05.085952Z",
            "pg_last_xlog_receive_location": standby_lsn,
            "pg_last_xlog_replay_location": "1/E000548",
            "replication_time_lag": 2.001652,
            "min_replication_time_lag": 0.007882,
        }

    return query_cluster_member_state


def test_update_cluster_member_state_replication_slots_cache(db: TestPG) -> None:
    if version.parse(db.pgver) < version.parse("10"):
        pytest.skip(f"unsupported pg version: {db.pgver}")

    config = {
        "remote_conns": {
            "test1db": db.connection_string("testuser"),
            "test2db": db.connection_string("otheruser"),
        },
        "observers": {"local": "URL"},
        "poll_observers_on_warning_only": True,
    }
    cluster_state = {}
    observer_state = {}

    def create_alert_file(arg) -> None:
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
        is_replication_lag_over_warning_limit=lambda: False,
        replication_slots_cache={},
    )

    # Only first slot state should be added, as the second one does not have anything flushed
    with patch.object(
        cm,
        "_query_cluster_member_state",
        create_query_cluster_member_state(slot1_lsn="0/2F0021E8", slot2_lsn=None, standby_lsn="0/2F0021E8"),
    ):
        cm.main_monitoring_loop(requested_check=True)
        assert len(cm.replication_slots_cache) == 1
        assert "test_slot_v1" in cm.replication_slots_cache
        assert cm.replication_slots_cache["test_slot_v1"] == [repl_slot1_data("0/2F0021E8")]

    # Add the same first slot state twice and the updated second slot, now we should have both, first one should not
    # be duplicated
    with patch.object(
        cm,
        "_query_cluster_member_state",
        create_query_cluster_member_state(slot1_lsn="0/2F0021E8", slot2_lsn="0/2F001940", standby_lsn="0/2F0021E8"),
    ):
        cm.main_monitoring_loop(requested_check=True)
        assert len(cm.replication_slots_cache) == 2
        assert "test_slot_v1" in cm.replication_slots_cache
        assert cm.replication_slots_cache["test_slot_v1"] == [repl_slot1_data("0/2F0021E8")]
        assert "test_slot_v2" in cm.replication_slots_cache
        assert cm.replication_slots_cache["test_slot_v2"] == [repl_slot2_data("0/2F001940")]

    # Add 5 more slot states which are much further into the future, keep standby position the same
    # it should end up in the cache of size 5, so the last state will be removed
    for lsn in ["0/2F003000", "0/2F003001", "0/2F003002", "0/2F003003", "1/2F003000"]:
        with patch.object(
            cm,
            "_query_cluster_member_state",
            create_query_cluster_member_state(slot1_lsn=lsn, slot2_lsn="0/2F001940", standby_lsn="0/2F000000"),
        ):
            cm.main_monitoring_loop(requested_check=True)

    assert len(cm.replication_slots_cache) == 2
    assert "test_slot_v1" in cm.replication_slots_cache
    assert len(cm.replication_slots_cache["test_slot_v1"]) == 5
    # First entry is still the oldest, as standby did not advance yet
    assert cm.replication_slots_cache["test_slot_v1"][0] == repl_slot1_data("0/2F0021E8")
    assert "test_slot_v2" in cm.replication_slots_cache
    assert cm.replication_slots_cache["test_slot_v2"] == [repl_slot2_data("0/2F001940")]

    # Now advance the standby position, so only the latest state remains
    with patch.object(
        cm,
        "_query_cluster_member_state",
        create_query_cluster_member_state(slot1_lsn="1/2F003000", slot2_lsn="0/2F001940", standby_lsn="1/2F003000"),
    ):
        cm.main_monitoring_loop(requested_check=True)

    assert len(cm.replication_slots_cache) == 2
    assert "test_slot_v1" in cm.replication_slots_cache
    assert cm.replication_slots_cache["test_slot_v1"] == [repl_slot1_data("0/2F003003")]
    assert "test_slot_v2" in cm.replication_slots_cache
    assert cm.replication_slots_cache["test_slot_v2"] == [repl_slot2_data("0/2F001940")]
