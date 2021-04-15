"""
pglookout tests

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""
from pglookout.common import get_iso_timestamp
from pglookout.pgutil import get_connection_info, get_connection_info_from_config_line
import datetime
import json
import os
import time


def test_connect_to_cluster_nodes_and_cleanup_old_nodes(pgl):
    pgl.cluster_monitor.db_conns = {
        "1.2.3.4": "bar",
        "2.3.4.5": "foo",
        "3.4.5.6": "foo",
        None: "foo",
    }
    pgl.cluster_monitor.connect_to_cluster_nodes_and_cleanup_old_nodes()
    assert pgl.cluster_monitor.db_conns == {}


def test_state_file_write(pgl, tmpdir):
    state_file_path = tmpdir.join("state_file").strpath
    pgl.config["json_state_file_path"] = state_file_path
    pgl.write_cluster_state_to_json_file()
    assert os.path.exists(state_file_path)
    with open(state_file_path, "r") as fp:
        state = json.load(fp)
    assert isinstance(state, dict)


def test_load_config(pgl):
    pgl.own_db = "old_value"
    pgl.load_config()
    assert pgl.own_db == "1.2.3.4"


def _create_db_node_state(pg_last_xlog_receive_location=None, pg_is_in_recovery=True,
                          connection=True, replication_time_lag=None, fetch_time=None,
                          db_time=None):
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


def _add_to_observer_state(pgl, observer_name, db_name, pg_last_xlog_receive_location=None,
                           pg_is_in_recovery=True, connection=True, replication_time_lag=None,
                           fetch_time=None, db_time=None):
    db_node_state = _create_db_node_state(pg_last_xlog_receive_location, pg_is_in_recovery,
                                          connection, replication_time_lag, fetch_time=fetch_time,
                                          db_time=db_time)
    update_dict = {
        "fetch_time": get_iso_timestamp(),
        "connection": True,
        db_name: db_node_state,
    }
    if observer_name in pgl.observer_state:
        pgl.observer_state[observer_name].update(update_dict)
    else:
        pgl.observer_state[observer_name] = update_dict


def _add_db_to_cluster_state(pgl, instance, pg_last_xlog_receive_location=None,
                             pg_is_in_recovery=True, connection=True, replication_time_lag=None,
                             fetch_time=None, db_time=None, conn_info=None):
    db_node_state = _create_db_node_state(pg_last_xlog_receive_location, pg_is_in_recovery,
                                          connection, replication_time_lag, fetch_time=fetch_time,
                                          db_time=db_time)
    pgl.cluster_state[instance] = db_node_state
    pgl.config["remote_conns"][instance] = conn_info or {"host": instance}


def test_check_cluster_state_warning(pgl):
    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=40.0)

    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=True)
    pgl.current_master = "old_master"
    pgl.own_db = "kuu"
    pgl.over_warning_limit_command = "fake_command"
    pgl.execute_external_command.return_value = 0
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1
    assert pgl.create_alert_file.call_count == 1
    pgl.check_cluster_state()

    # call count does not change when we have sent a single warning
    assert pgl.execute_external_command.call_count == 1
    assert pgl.replication_lag_over_warning_limit
    assert pgl.create_alert_file.call_count == 1

    # and then the replication catches up
    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=5.0)
    pgl.check_cluster_state()
    assert not os.path.exists("replication_delay_warning")
    assert pgl.replication_lag_over_warning_limit is False


def test_check_cluster_do_failover_one_standby(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False,
                             db_time=datetime.datetime(year=2014, month=1, day=1))

    _add_db_to_cluster_state(pgl, "own_db", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)

    pgl.own_db = "own_db"
    pgl.execute_external_command.return_value = 0
    pgl.replication_lag_over_warning_limit = False
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1
    assert pgl.replication_lag_over_warning_limit is False


def test_check_cluster_master_gone_one_standby_one_observer(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False,
                             db_time=datetime.datetime(year=2014, month=1, day=1))

    _add_db_to_cluster_state(pgl, "own_db", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=0.0)
    pgl.own_db = "own_db"
    _add_to_observer_state(pgl, "observer", "old_master", pg_is_in_recovery=False, connection=False,
                           db_time=datetime.datetime(year=2014, month=1, day=1))
    _add_to_observer_state(pgl, "observer", "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                           pg_is_in_recovery=True, connection=True, replication_time_lag=0.0)

    # Simulate existing master connection
    pgl.current_master = "old_master"
    pgl.execute_external_command.return_value = 0
    pgl.replication_lag_over_warning_limit = False

    del pgl.config["remote_conns"]["old_master"]
    # Old master removed from config, cluster monitor would remove node from cluster state so do the same here
    del pgl.cluster_state["old_master"]
    pgl.cluster_nodes_change_time = time.monotonic()

    # First call does not promote due to missing master because config has been updated just recently and there's
    # by default a grace period that's waited after list of known cluster nodes changes
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is False

    # If we say that master is known to be gone promotion will happen even though config was updated recently
    pgl.known_gone_nodes.append("old_master")
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1
    assert pgl.replication_lag_over_warning_limit is False


def test_check_cluster_do_failover_one_standby_one_observer(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False,
                             db_time=datetime.datetime(year=2014, month=1, day=1))

    _add_db_to_cluster_state(pgl, "own_db", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.own_db = "own_db"
    _add_to_observer_state(pgl, "observer", "old_master", pg_is_in_recovery=False, connection=False,
                           db_time=datetime.datetime(year=2014, month=1, day=1))
    _add_to_observer_state(pgl, "observer", "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                           pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)

    pgl.execute_external_command.return_value = 0
    pgl.replication_lag_over_warning_limit = False
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1
    assert pgl.replication_lag_over_warning_limit is False


def test_check_cluster_do_failover_with_a_node_which_is_is_maintenance(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False,
                             db_time=datetime.datetime(year=2014, month=1, day=1))

    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)

    pgl.never_promote_these_nodes = []
    pgl.own_db = "kuu"
    pgl.execute_external_command.return_value = 0
    pgl.replication_lag_over_warning_limit = True
    pgl.check_for_maintenance_mode_file.return_value = True
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True
    assert pgl.check_for_maintenance_mode_file.call_count == 1


def test_check_cluster_do_failover_with_a_node_which_should_never_be_promoted(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False)

    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.never_promote_these_nodes = ["kuu"]
    pgl.own_db = "kuu"
    pgl.execute_external_command.return_value = 0
    pgl.replication_lag_over_warning_limit = True
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True


def test_check_cluster_do_failover_two_standbys(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False)

    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.own_db = "kuu"
    # we put the second standby _WELL_ ahead
    _add_db_to_cluster_state(pgl, "puu", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)

    pgl.execute_external_command.return_value = 0
    pgl.replication_lag_over_warning_limit = True
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True  # we keep the warning on


def test_check_cluster_do_failover_two_standbys_when_the_one_ahead_can_never_be_promoted(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False,
                             db_time=datetime.datetime(year=2014, month=1, day=1))

    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.own_db = "kuu"
    # we put the second standby _WELL_ ahead
    _add_db_to_cluster_state(pgl, "puu", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.never_promote_these_nodes = ["puu"]
    pgl.execute_external_command.return_value = 0
    pgl.replication_lag_over_warning_limit = True
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1
    assert pgl.replication_lag_over_warning_limit is False


def test_failover_with_no_master_anymore(pgl):
    # this should trigger an immediate failover as we have two
    # standbys online but we've never seen a master
    pgl.own_db = "kuu"
    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="F/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=0)
    _add_db_to_cluster_state(pgl, "puu", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=1)

    pgl.execute_external_command.return_value = 0
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1


def test_failover_over_replication_lag_when_still_connected_to_master(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False)

    # We will make our own node to be the furthest along so we get considered for promotion
    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.own_db = "kuu"

    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True  # we keep the warning on


def test_failover_over_replication_lag_with_one_observer_one_standby_no_connections(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False)

    # We will make our own node to be the furthest along so we get considered for promotion
    _add_db_to_cluster_state(pgl, "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.own_db = "own_db"

    _add_to_observer_state(pgl, "observer", "old_master", pg_is_in_recovery=False, connection=False,
                           db_time=datetime.datetime(year=2014, month=1, day=1))
    _add_to_observer_state(pgl, "observer", "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                           pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
    pgl.observer_state["observer"]["connection"] = False
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True  # we keep the warning on


def test_cluster_state_when_observer_has_also_non_members_of_our_current_cluster(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=True)

    # We will make our own node to be the furthest along so we get considered for promotion
    _add_db_to_cluster_state(pgl, "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.own_db = "own_db"

    _add_to_observer_state(pgl, "observer", "old_master", pg_is_in_recovery=False, connection=False,
                           db_time=datetime.datetime(year=2014, month=1, day=1))
    _add_to_observer_state(pgl, "observer", "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                           pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
    _add_to_observer_state(pgl, "observer", "some_other_cluster", pg_last_xlog_receive_location="3/aaaaaaaa",
                           pg_is_in_recovery=False, connection=True, replication_time_lag=0.0)
    pgl.check_cluster_state()
    assert len(pgl.connected_master_nodes) == 1
    assert "old_master" in pgl.connected_master_nodes


def test_failover_no_connections(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False)

    # We will make our own node to be the furthest along so we get considered for promotion
    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.own_db = "kuu"

    # we put the second standby _WELL_ ahead
    _add_db_to_cluster_state(pgl, "puu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True  # we keep the warning on


def test_failover_master_two_standbys_one_observer_no_connection_between_standbys(pgl):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False,
                             db_time=datetime.datetime(year=2014, month=1, day=1))
    # We will make our own node to be the furthest along so we get considered for promotion
    _add_db_to_cluster_state(pgl, "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.own_db = "own"

    _add_db_to_cluster_state(pgl, "other", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)

    # Add observer state
    _add_to_observer_state(pgl, "observer", "old_master", pg_is_in_recovery=False, connection=False,
                           db_time=datetime.datetime(year=2014, month=1, day=1))
    _add_to_observer_state(pgl, "observer", "other", pg_last_xlog_receive_location="1/aaaaaaaa",
                           pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    _add_to_observer_state(pgl, "observer", "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                           pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
    pgl.execute_external_command.return_value = 0
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1

    assert pgl.replication_lag_over_warning_limit is False


def test_failover_master_one_standby_one_observer_no_connections(pgl):
    pgl.own_db = "own"

    # Add observer state
    _add_to_observer_state(pgl, "observer", "old_master", pg_is_in_recovery=False, connection=True)

    # add db state
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=True)
    _add_db_to_cluster_state(pgl, "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=40.0)

    pgl.check_cluster_state()
    assert pgl.replication_lag_over_warning_limit is True  # we keep the warning on
    assert pgl.execute_external_command.call_count == 0

    # Add observer state
    _add_to_observer_state(pgl, "observer", "old_master", pg_is_in_recovery=False, connection=True)
    _add_to_observer_state(pgl, "observer", "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                           pg_is_in_recovery=True, connection=True, replication_time_lag=9.0)

    _add_db_to_cluster_state(pgl, "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=140.0)

    pgl.check_cluster_state()

    # No failover yet
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit is True  # we keep the warning on

    # observer state
    _add_to_observer_state(pgl, "observer", "old_master", pg_is_in_recovery=False, connection=False,
                           db_time=datetime.datetime(year=2014, month=1, day=1))
    _add_to_observer_state(pgl, "observer", "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                           pg_is_in_recovery=True, connection=False, replication_time_lag=140.0)
    # lose own connection to master
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False,
                             db_time=datetime.datetime(year=2014, month=1, day=1))
    # now do failover
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1


def test_find_current_master(pgl):
    _add_db_to_cluster_state(pgl, "master", pg_is_in_recovery=False, connection=True)
    # We will make our own node to be the furthest along so we get considered for promotion
    _add_db_to_cluster_state(pgl, "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=True, replication_time_lag=0.1)
    pgl.own_db = "master"
    pgl.check_cluster_state()
    assert pgl.current_master == "master"


def test_two_standby_failover_and_autofollow(pgl, tmpdir):
    _add_db_to_cluster_state(pgl, "old_master", pg_is_in_recovery=False, connection=False,
                             fetch_time=datetime.datetime(year=2014, month=1, day=1))
    # We will make our own node to be the furthest from master so we don't get considered for promotion
    _add_db_to_cluster_state(pgl, "own", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
    pgl.own_db = "own"
    _add_db_to_cluster_state(pgl, "other", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
    pgl.check_cluster_state()

    assert pgl.replication_lag_over_warning_limit is True  # we keep the warning on
    assert pgl.execute_external_command.call_count == 0
    assert pgl.current_master == "old_master"

    _add_db_to_cluster_state(pgl, "other", pg_last_xlog_receive_location="2/aaaaaaaa",
                             pg_is_in_recovery=False, connection=True, replication_time_lag=0.0,
                             conn_info={"host": "otherhost.example.com", "port": 11111})

    pg_data_dir = tmpdir.join("test_pgdata").strpath
    os.makedirs(pg_data_dir)
    primary_conninfo = (
        "user=replication password=vjsh8l7sv4a902y1tsdz "
        "host=old_master port=5432 "
        "sslmode=prefer sslcompression=1 krbsrvname=postgres"
    )
    old_recovery_conf = "standby_mode = 'on'\nprimary_conninfo = '{0}'\n".format(primary_conninfo)
    with open(os.path.join(pg_data_dir, "recovery.conf"), "w") as fp:
        fp.write(old_recovery_conf)

    pgl.config["pg_data_directory"] = pg_data_dir
    pgl.config["autofollow"] = True
    pgl.primary_conninfo_template = get_connection_info(primary_conninfo)

    with open(os.path.join(pg_data_dir, "PG_VERSION"), "w") as fp:
        fp.write("11\n")

    pgl.check_cluster_state()
    assert pgl.current_master == "other"

    with open(os.path.join(pg_data_dir, "recovery.conf"), "r") as fp:
        new_lines = fp.read().splitlines()
    assert new_lines.pop(0).startswith("# pglookout updated primary_conninfo")
    assert new_lines.pop(0) == "standby_mode = 'on'"
    assert new_lines[0].startswith("primary_conninfo = ")
    new_primary_conninfo = new_lines.pop(0)
    assert new_lines.pop(0) == "recovery_target_timeline = 'latest'"
    assert new_lines == []
    old_conn_info = get_connection_info(primary_conninfo)
    new_conn_info = get_connection_info_from_config_line(new_primary_conninfo)
    assert new_conn_info == dict(old_conn_info, host="otherhost.example.com", port="11111")


def test_replication_positions(pgl):
    standby_nodes = {
        "10.255.255.10": {
            "connection": True,
            "db_time": "2014-08-28T14:09:57.919301+00:00Z",
            "fetch_time": "2014-08-28T14:09:57.918753Z",
            "pg_is_in_recovery": True,
            "pg_last_xlog_receive_location": "0/9000090",
            "pg_last_xlog_replay_location": "0/9000090",
            "pg_last_xact_replay_timestamp": "2014-08-28T14:05:43.577357+00:00Z",
            "replication_time_lag": 254.341944,
        },
    }
    # the above node shouldn't show up as its fetch_time is (way) older than 20 seconds
    positions = {}
    assert pgl.get_replication_positions(standby_nodes) == positions
    standby_nodes["10.255.255.10"]["fetch_time"] = get_iso_timestamp()
    positions[0x9000090] = set(["10.255.255.10"])
    assert pgl.get_replication_positions(standby_nodes) == positions
    # add another standby, further ahead
    standby_nodes["10.255.255.11"] = dict(standby_nodes["10.255.255.10"], pg_last_xlog_receive_location="1/0000AAAA")
    positions[1 << 32 | 0xAAAA] = set(["10.255.255.11"])
    assert pgl.get_replication_positions(standby_nodes) == positions
    # add another standby which hasn't received anything
    standby_nodes["10.255.255.12"] = dict(standby_nodes["10.255.255.10"], pg_last_xlog_receive_location=None)
    positions[0x9000090].add("10.255.255.12")
    assert pgl.get_replication_positions(standby_nodes) == positions


def test_node_map(pgl):
    cluster_state = {
        "10.255.255.10": {
            "connection": True,
            "db_time": "2014-08-28T14:26:51.067084+00:00Z",
            "fetch_time": "2014-08-28T14:26:51.066368Z",
            "pg_is_in_recovery": False,
            "pg_last_xact_replay_timestamp": "2014-08-28T14:05:43.577357+00:00Z",
            "pg_last_xlog_receive_location": "0/9000090",
            "pg_last_xlog_replay_location": "0/9000090",
            "replication_time_lag": 1267.489727,
        },
        "10.255.255.9": {
            "connection": False,
            "fetch_time": "2014-08-28T14:26:51.068151Z",
        }
    }
    observer_state = {
        "10.255.255.11": {
            "10.255.255.10": {
                "connection": True,
                "db_time": "2014-08-28T14:26:47.105901+00:00Z",
                "fetch_time": "2014-08-28T14:26:47.104849Z",
                "pg_is_in_recovery": False,
                "pg_last_xact_replay_timestamp": "2014-08-28T14:05:43.577357+00:00Z",
                "pg_last_xlog_receive_location": "0/9000090",
                "pg_last_xlog_replay_location": "0/9000090",
                "replication_time_lag": 1263.528544,
            },
            "10.255.255.9": {
                "connection": False,
                "db_time": "2014-08-28T14:06:15.172820+00:00Z",
                "fetch_time": "2014-08-28T14:26:47.107115Z",
                "pg_is_in_recovery": False,
                "pg_last_xact_replay_timestamp": None,
                "pg_last_xlog_receive_location": None,
                "pg_last_xlog_replay_location": None,
            },
            "connection": True,
            "fetch_time": "2014-08-28T14:26:51.069891Z",
        }
    }
    master_host, _, standby_nodes = pgl.create_node_map(cluster_state, observer_state)
    assert master_host == "10.255.255.10"
    assert standby_nodes == {}


def test_node_map_disconnected_current_master(pgl):
    pgl.current_master = "10.255.255.7"
    cluster_state = {
        "10.255.255.7": {
            "connection": False,
            "db_time": "2014-09-07T15:26:23.957151+00:00Z",
            "fetch_time": "2014-09-07T15:26:34.736495Z",
            "pg_is_in_recovery": False,
            "pg_last_xact_replay_timestamp": None,
            "pg_last_xlog_receive_location": None,
            "pg_last_xlog_replay_location": None,
        },
        "10.255.255.8": {
            "connection": True,
            "db_time": "2014-09-07T15:26:23.959461+00:00Z",
            "fetch_time": "2014-09-07T15:26:23.919281Z",
            "pg_is_in_recovery": True,
            "pg_last_xact_replay_timestamp": "2014-09-07T15:25:40.372936+00:00Z",
            "pg_last_xlog_receive_location": "0/74713D8",
            "pg_last_xlog_replay_location": "0/74713D8",
            "replication_time_lag": 43.586525,
        }
    }
    observer_state = {}
    master_host, _, standby_nodes = pgl.create_node_map(cluster_state, observer_state)
    assert master_host == "10.255.255.7"
    assert list(standby_nodes.keys())[0] == "10.255.255.8"


def test_standbys_failover_equal_replication_positions(pgl):
    now = datetime.datetime.utcnow()
    _add_db_to_cluster_state(
        pgl,
        instance="192.168.54.183",
        pg_last_xlog_receive_location="0/70004D8",
        pg_is_in_recovery=True,
        connection=True,
        replication_time_lag=400.435871,
        fetch_time=now,
        db_time=now,
        conn_info="foobar",
    )
    _add_db_to_cluster_state(
        pgl,
        instance="192.168.57.180",
        pg_last_xlog_receive_location=None,
        pg_is_in_recovery=False,
        connection=False,
        replication_time_lag=0.0,
        fetch_time=now - datetime.timedelta(seconds=3600),
        db_time=now - datetime.timedelta(seconds=3600),
        conn_info="foobar",
    )
    _add_db_to_cluster_state(
        pgl,
        instance="192.168.63.4",
        pg_last_xlog_receive_location="0/70004D8",
        pg_is_in_recovery=True,
        connection=True,
        replication_time_lag=401.104655,
        fetch_time=now,
        db_time=now,
        conn_info="foobar",
    )

    pgl.current_master = "192.168.57.180"
    # We select the node with the "highest" identifier so call_count should stay zero if we're not the
    # highest standby currently.
    pgl.own_db = "192.168.54.183"
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 0
    # If we're the highest we should see call_count increment
    pgl.own_db = "192.168.63.4"
    pgl.check_cluster_state()
    assert pgl.execute_external_command.call_count == 1


def test_node_map_when_only_observer_sees_master(pgl):
    cluster_state = {
        "10.255.255.10": {
            "connection": False,
            "db_time": "2014-08-28T14:26:51.067084+00:00Z",
            "fetch_time": "2014-08-28T14:26:51.066368Z",
            "pg_is_in_recovery": False,
            "pg_last_xact_replay_timestamp": "2014-08-28T14:05:43.577357+00:00Z",
            "pg_last_xlog_receive_location": "0/9000090",
            "pg_last_xlog_replay_location": "0/9000090",
            "replication_time_lag": 1267.489727,
        },
    }
    observer_state = {
        "10.255.255.11": {
            "10.255.255.10": {
                "connection": True,
                "db_time": "2014-08-28T14:26:47.105901+00:00Z",
                "fetch_time": "2014-08-28T14:26:50.104849Z",
                "pg_is_in_recovery": False,
                "pg_last_xact_replay_timestamp": "2014-08-28T14:05:43.577357+00:00Z",
                "pg_last_xlog_receive_location": "0/9000090",
                "pg_last_xlog_replay_location": "0/9000090",
                "replication_time_lag": 1263.528544,
            },
            "connection": True,
            "fetch_time": "2014-08-28T14:26:51.069891Z",
        }
    }
    master_instance, _, _ = pgl.create_node_map(cluster_state, observer_state)
    assert master_instance == "10.255.255.10"
    # because observer saw it and its fetch time is later than cluster time
    assert master_instance in pgl.connected_master_nodes


def test_poll_observers_on_warning_only(pgl):
    pgl.config["poll_observers_on_warning_only"] = True
    pgl.config["observers"] = {"local": "URL"}
    pgl.own_db = "kuu"
    _add_db_to_cluster_state(pgl, "master", pg_is_in_recovery=False, connection=True, db_time=datetime.datetime.min)
    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, replication_time_lag=40.0)
    pgl.check_cluster_state()
    assert "master" not in pgl.disconnected_master_nodes
    assert "master" in pgl.connected_master_nodes
    assert pgl.execute_external_command.call_count == 0
    assert pgl.replication_lag_over_warning_limit
    assert pgl.observer_state_newer_than is not None

    _add_db_to_cluster_state(pgl, "master", pg_is_in_recovery=False, connection=False, db_time=datetime.datetime.min)
    _add_db_to_cluster_state(pgl, "kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                             pg_is_in_recovery=True, replication_time_lag=140.0)
    _add_to_observer_state(pgl, "observer", "master", pg_is_in_recovery=False,
                           connection=False, db_time=datetime.datetime.min)
    pgl.check_cluster_state()
    # this check makes sure we did not skip doing checks because db_poll_inteval has
    # pass but because observer data is available and create_node_map was called
    assert "master" in pgl.disconnected_master_nodes
    assert pgl.execute_external_command.call_count == 1
