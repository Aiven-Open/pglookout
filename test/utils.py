"""
Utilities for pglookout tests

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from pglookout.common import get_iso_timestamp


def add_to_observer_state(
    pgl,
    observer_name,
    db_name,
    pg_last_xlog_receive_location=None,
    pg_is_in_recovery=True,
    connection=True,
    replication_time_lag=None,
    fetch_time=None,
    db_time=None,
):
    db_node_state = create_db_node_state(
        pg_last_xlog_receive_location,
        pg_is_in_recovery,
        connection,
        replication_time_lag,
        fetch_time=fetch_time,
        db_time=db_time,
    )
    update_dict = {
        "fetch_time": get_iso_timestamp(),  # type: ignore[no-untyped-call]
        "connection": True,
        db_name: db_node_state,
    }
    if observer_name in pgl.observer_state:
        pgl.observer_state[observer_name].update(update_dict)
    else:
        pgl.observer_state[observer_name] = update_dict


def create_db_node_state(
    pg_last_xlog_receive_location=None,
    pg_is_in_recovery=True,
    connection=True,
    replication_time_lag=None,
    fetch_time=None,
    db_time=None,
):
    return {
        "connection": connection,
        "db_time": get_iso_timestamp(db_time),
        "fetch_time": get_iso_timestamp(fetch_time),
        "pg_is_in_recovery": pg_is_in_recovery,
        "pg_last_xact_replay_timestamp": None,
        "pg_last_xlog_receive_location": pg_last_xlog_receive_location,
        "pg_last_xlog_replay_location": None,
        "replication_time_lag": replication_time_lag,
        "min_replication_time_lag": 0,  # simulate that we've been in sync once
    }


def set_instance_cluster_state(
    pgl,
    *,
    instance,
    pg_last_xlog_receive_location=None,
    pg_is_in_recovery=True,
    connection=True,
    replication_time_lag=None,
    fetch_time=None,
    db_time=None,
    conn_info=None,
):
    db_node_state = create_db_node_state(
        pg_last_xlog_receive_location,
        pg_is_in_recovery,
        connection,
        replication_time_lag,
        fetch_time=fetch_time,
        db_time=db_time,
    )
    pgl.cluster_state[instance] = db_node_state
    pgl.config["remote_conns"][instance] = conn_info or {"host": instance}
