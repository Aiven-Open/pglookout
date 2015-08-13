"""
pglookout tests

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from pglookout.common import get_connection_info, get_connection_info_from_config_line
from pglookout.pglookout import PgLookout, parse_iso_datetime, get_iso_timestamp
try:
    from mock import Mock  # pylint: disable=F0401
except:  # py3k import location
    from unittest.mock import Mock  # pylint: disable=F0401,E0611
from unittest import TestCase
import datetime
import os
import tempfile


def _create_db_node_state(pg_last_xlog_receive_location=None, pg_is_in_recovery=True,
                          connection=True, replication_time_lag=None, fetch_time=None,
                          db_time=None):
    return {
        "fetch_time": get_iso_timestamp(fetch_time),
        "pg_last_xlog_receive_location": pg_last_xlog_receive_location,
        "pg_is_in_recovery": pg_is_in_recovery,
        "pg_last_xact_replay_timestamp": None,
        "connection": connection,
        "pg_last_xlog_replay_location": None,
        "replication_time_lag": replication_time_lag,
        "db_time": get_iso_timestamp(db_time),
        }


class TestClusterMonitor(TestCase):
    def setUp(self):
        self.pglookout = PgLookout("pglookout.json")
        self.original_connect_to_db = self.pglookout.cluster_monitor._connect_to_db   # pylint: disable=W0212
        self.pglookout.cluster_monitor._connect_to_db = Mock()  # pylint: disable=W0212

    def test_connect_to_cluster_nodes_and_cleanup_old_nodes(self):
        self.pglookout.cluster_monitor.db_conns = {"1.2.3.4": "bar", "2.3.4.5": "foo", "3.4.5.6": "foo", None: "foo"}
        self.pglookout.cluster_monitor.connect_to_cluster_nodes_and_cleanup_old_nodes()
        self.assertEqual(self.pglookout.cluster_monitor.db_conns, {"1.2.3.4": "bar", "2.3.4.5": "foo"})

    def tearDown(self):
        self.pglookout.cluster_monitor._connect_to_db = self.original_connect_to_db   # pylint: disable=W0212


class TestPgLookout(TestCase):
    def setUp(self):
        self.pglookout = PgLookout("pglookout.json")
        self.pglookout.execute_external_command = Mock()
        self.pglookout.create_alert_file = Mock()
        self.pglookout.check_for_maintenance_mode_file = Mock()
        self.pglookout.check_for_maintenance_mode_file.return_value = False
        self.temp_dir = tempfile.mkdtemp()
        self.state_file_path = os.path.join(self.temp_dir, "state_file")

    def test_parse_iso_datetime(self):
        date = datetime.datetime.utcnow()
        date.replace(microsecond=0)
        self.assertEqual(date, parse_iso_datetime(date.isoformat() + "Z"))

    def test_state_file_write(self):
        self.pglookout.config['json_state_file_path'] = self.state_file_path
        self.pglookout.write_cluster_state_to_json_file()
        self.assertTrue(os.path.exists(self.state_file_path))
        self.assertTrue(os.path.getsize(self.state_file_path), 2)
        os.unlink(self.state_file_path)

    def test_load_config(self):
        self.pglookout.own_db = "old_value"
        self.pglookout.load_config()
        self.assertEqual(self.pglookout.own_db, "1.2.3.4")

    def _add_to_observer_state(self, observer_name, db_name, pg_last_xlog_receive_location=None,
                               pg_is_in_recovery=True, connection=True, replication_time_lag=None,
                               fetch_time=None, db_time=None):
        db_node_state = _create_db_node_state(pg_last_xlog_receive_location, pg_is_in_recovery,
                                              connection, replication_time_lag, fetch_time=fetch_time,
                                              db_time=db_time)
        update_dict = {"fetch_time": get_iso_timestamp(),
                       "connection": True, db_name: db_node_state}
        if observer_name in self.pglookout.observer_state:
            self.pglookout.observer_state[observer_name].update(update_dict)
        else:
            self.pglookout.observer_state[observer_name] = update_dict

    def _add_db_to_cluster_state(self, db_name, pg_last_xlog_receive_location=None,
                                 pg_is_in_recovery=True, connection=True, replication_time_lag=None,
                                 fetch_time=None, db_time=None):
        db_node_state = _create_db_node_state(pg_last_xlog_receive_location, pg_is_in_recovery,
                                              connection, replication_time_lag, fetch_time=fetch_time,
                                              db_time=db_time)
        self.pglookout.cluster_state[db_name] = db_node_state

    def test_check_cluster_state_warning(self):
        self._add_db_to_cluster_state("kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=40.0)
        self.pglookout.own_db = "kuu"
        self.pglookout.over_warning_limit_command = "fake_command"
        self.pglookout.execute_external_command.return_value = 0
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 1)
        self.assertEqual(self.pglookout.create_alert_file.call_count, 1)
        self.pglookout.check_cluster_state()

        # call count does not change when we have sent a single warning
        self.assertEqual(self.pglookout.execute_external_command.call_count, 1)
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)
        self.assertEqual(self.pglookout.create_alert_file.call_count, 1)

        # and then the replication catches up
        self._add_db_to_cluster_state("kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=5.0)
        self.pglookout.check_cluster_state()
        self.assertFalse(os.path.exists("replication_delay_warning"))
        self.assertFalse(self.pglookout.replication_lag_over_warning_limit)

    def test_check_cluster_do_failover_one_slave(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False,
                                      db_time=datetime.datetime(year=2014, month=1, day=1))

        self._add_db_to_cluster_state("own_db", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)

        self.pglookout.own_db = "own_db"
        self.pglookout.execute_external_command.return_value = 0
        self.pglookout.replication_lag_over_warning_limit = False
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 1)
        self.assertFalse(self.pglookout.replication_lag_over_warning_limit)

    def test_check_cluster_do_failover_one_slave_one_observer(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False,
                                      db_time=datetime.datetime(year=2014, month=1, day=1))

        self._add_db_to_cluster_state("own_db", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.own_db = "own_db"
        self._add_to_observer_state("observer", "old_master", pg_is_in_recovery=False, connection=False,
                                    db_time=datetime.datetime(year=2014, month=1, day=1))
        self._add_to_observer_state("observer", "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                                    pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)

        self.pglookout.execute_external_command.return_value = 0
        self.pglookout.replication_lag_over_warning_limit = False
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 1)
        self.assertFalse(self.pglookout.replication_lag_over_warning_limit)

    def test_check_cluster_do_failover_with_a_node_which_is_is_maintenance(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False,
                                      db_time=datetime.datetime(year=2014, month=1, day=1))

        self._add_db_to_cluster_state("kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)

        self.pglookout.never_promote_these_nodes = []
        self.pglookout.own_db = "kuu"
        self.pglookout.execute_external_command.return_value = 0
        self.pglookout.replication_lag_over_warning_limit = True
        self.pglookout.check_for_maintenance_mode_file.return_value = True
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)
        self.assertEqual(self.pglookout.check_for_maintenance_mode_file.call_count, 1)

    def test_check_cluster_do_failover_with_a_node_which_should_never_be_promoted(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False)

        self._add_db_to_cluster_state("kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.never_promote_these_nodes = ["kuu"]
        self.pglookout.own_db = "kuu"
        self.pglookout.execute_external_command.return_value = 0
        self.pglookout.replication_lag_over_warning_limit = True
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)

    def test_check_cluster_do_failover_two_slaves(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False)

        self._add_db_to_cluster_state("kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.own_db = "kuu"
        # we put the second slave _WELL_ ahead
        self._add_db_to_cluster_state("puu", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)

        self.pglookout.execute_external_command.return_value = 0
        self.pglookout.replication_lag_over_warning_limit = True
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)  # we keep the warning on

    def test_check_cluster_do_failover_two_slaves_when_the_one_ahead_can_never_be_promoted(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False,
                                      db_time=datetime.datetime(year=2014, month=1, day=1))

        self._add_db_to_cluster_state("kuu", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.own_db = "kuu"
        # we put the second slave _WELL_ ahead
        self._add_db_to_cluster_state("puu", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.never_promote_these_nodes = ["puu"]
        self.pglookout.execute_external_command.return_value = 0
        self.pglookout.replication_lag_over_warning_limit = True
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 1)
        self.assertFalse(self.pglookout.replication_lag_over_warning_limit)

    def test_failover_over_replication_lag_when_still_connected_to_master(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False)

        # We will make our own node to be the furthest along so we get considered for promotion
        self._add_db_to_cluster_state("kuu", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.own_db = "kuu"

        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)  # we keep the warning on

    def test_failover_over_replication_lag_with_one_observer_one_slave_no_connections(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False)

        # We will make our own node to be the furthest along so we get considered for promotion
        self._add_db_to_cluster_state("own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.own_db = "own_db"

        self._add_to_observer_state("observer", "old_master", pg_is_in_recovery=False, connection=False,
                                    db_time=datetime.datetime(year=2014, month=1, day=1))
        self._add_to_observer_state("observer", "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                                    pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
        self.pglookout.observer_state["observer"]['connection'] = False
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)  # we keep the warning on

    def test_cluster_state_when_observer_has_also_non_members_of_our_current_cluster(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=True)

        # We will make our own node to be the furthest along so we get considered for promotion
        self._add_db_to_cluster_state("own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.own_db = "own_db"

        self._add_to_observer_state("observer", "old_master", pg_is_in_recovery=False, connection=False,
                                    db_time=datetime.datetime(year=2014, month=1, day=1))
        self._add_to_observer_state("observer", "own_db", pg_last_xlog_receive_location="2/aaaaaaaa",
                                    pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
        self._add_to_observer_state("observer", "some_other_cluster", pg_last_xlog_receive_location="3/aaaaaaaa",
                                    pg_is_in_recovery=False, connection=True, replication_time_lag=0.0)
        self.pglookout.check_cluster_state()
        self.assertEqual(len(self.pglookout.connected_master_nodes), 1)
        assert 'old_master' in self.pglookout.connected_master_nodes

    def test_failover_no_connections(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False)

        # We will make our own node to be the furthest along so we get considered for promotion
        self._add_db_to_cluster_state("kuu", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.own_db = "kuu"

        # we put the second slave _WELL_ ahead
        self._add_db_to_cluster_state("puu", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)  # we keep the warning on

    def test_failover_master_two_slaves_one_observer_no_connection_between_slaves(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False,
                                      db_time=datetime.datetime(year=2014, month=1, day=1))
        # We will make our own node to be the furthest along so we get considered for promotion
        self._add_db_to_cluster_state("own", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.own_db = "own"

        self._add_db_to_cluster_state("other", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)

        # Add observer state
        self._add_to_observer_state("observer", "old_master", pg_is_in_recovery=False, connection=False,
                                    db_time=datetime.datetime(year=2014, month=1, day=1))
        self._add_to_observer_state("observer", "other", pg_last_xlog_receive_location="1/aaaaaaaa",
                                    pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self._add_to_observer_state("observer", "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                                    pg_is_in_recovery=True, connection=True, replication_time_lag=130.0)
        self.pglookout.execute_external_command.return_value = 0
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 1)

        self.assertFalse(self.pglookout.replication_lag_over_warning_limit)  # we keep the warning on

    def test_failover_master_one_slave_one_observer_no_connections(self):
        self.pglookout.own_db = "own"

        # Add observer state
        self._add_to_observer_state("observer", "old_master", pg_is_in_recovery=False, connection=True)

        # add db state
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=True)
        self._add_db_to_cluster_state("own", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=40.0)

        self.pglookout.check_cluster_state()
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)  # we keep the warning on
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)

        # Add observer state
        self._add_to_observer_state("observer", "old_master", pg_is_in_recovery=False, connection=True)
        self._add_to_observer_state("observer", "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                                    pg_is_in_recovery=True, connection=True, replication_time_lag=9.0)

        self._add_db_to_cluster_state("own", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=140.0)

        self.pglookout.check_cluster_state()

        # No failover yet
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)  # we keep the warning on

        # observer state
        self._add_to_observer_state("observer", "old_master", pg_is_in_recovery=False, connection=False,
                                    db_time=datetime.datetime(year=2014, month=1, day=1))
        self._add_to_observer_state("observer", "own", pg_last_xlog_receive_location="2/aaaaaaaa",
                                    pg_is_in_recovery=True, connection=False, replication_time_lag=140.0)
        # lose own connection to master
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False,
                                      db_time=datetime.datetime(year=2014, month=1, day=1))
        # now do failover
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 1)

    def test_find_current_master(self):
        self._add_db_to_cluster_state("master", pg_is_in_recovery=False, connection=True)
        # We will make our own node to be the furthest along so we get considered for promotion
        self._add_db_to_cluster_state("own", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=True, replication_time_lag=0.1)
        self.pglookout.own_db = "master"
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.current_master, "master")

    def test_two_slave_failover_and_autofollow(self):
        self._add_db_to_cluster_state("old_master", pg_is_in_recovery=False, connection=False,
                                      fetch_time=datetime.datetime(year=2014, month=1, day=1))
        # We will make our own node to be the furthest from master so we don't get considered for promotion
        self._add_db_to_cluster_state("own", pg_last_xlog_receive_location="1/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)
        self.pglookout.own_db = "own"
        self._add_db_to_cluster_state("other", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=True, connection=False, replication_time_lag=130.0)

        self.pglookout.check_cluster_state()
        self.assertTrue(self.pglookout.replication_lag_over_warning_limit)  # we keep the warning on
        self.assertEqual(self.pglookout.execute_external_command.call_count, 0)
        self.assertEqual(self.pglookout.current_master, "old_master")

        self._add_db_to_cluster_state("other", pg_last_xlog_receive_location="2/aaaaaaaa",
                                      pg_is_in_recovery=False, connection=True, replication_time_lag=0.0)

        pg_data_dir = os.path.join(self.temp_dir + os.sep + "test_pgdata")
        os.makedirs(pg_data_dir)
        primary_conninfo = "user=replication password=vjsh8l7sv4a902y1tsdz host=old_master port=5432 sslmode=prefer sslcompression=1 krbsrvname=postgres"
        old_recovery_conf = "standby_mode = 'on'\nprimary_conninfo = '{0}'\n".format(primary_conninfo)
        with open(os.path.join(pg_data_dir, "recovery.conf"), "w") as fp:
            fp.write(old_recovery_conf)

        self.pglookout.config['pg_data_directory'] = pg_data_dir
        self.pglookout.config['autofollow'] = True
        self.pglookout.primary_conninfo_template = get_connection_info(primary_conninfo)

        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.current_master, "other")

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
        assert new_conn_info == dict(old_conn_info, host="other")

    def test_replication_positions(self):
        standby_nodes = {
            '10.255.255.10': {
                'connection': True,
                'db_time': '2014-08-28T14:09:57.919301+00:00Z',
                'fetch_time': '2014-08-28T14:09:57.918753Z',
                'pg_is_in_recovery': True,
                'pg_last_xlog_receive_location': '0/9000090',
                'pg_last_xlog_replay_location': '0/9000090',
                'pg_last_xact_replay_timestamp': '2014-08-28T14:05:43.577357+00:00Z',
                'replication_time_lag': 254.341944,
            },
        }
        # the above node shouldn't show up as it's fetch_time is (way) older than 20 seconds
        positions = {}
        assert self.pglookout.get_replication_positions(standby_nodes) == positions
        standby_nodes['10.255.255.10']['fetch_time'] = get_iso_timestamp()
        positions[0x9000090] = set(['10.255.255.10'])
        assert self.pglookout.get_replication_positions(standby_nodes) == positions
        # add another standby, further ahead
        standby_nodes['10.255.255.11'] = dict(standby_nodes['10.255.255.10'], pg_last_xlog_receive_location='1/0000AAAA')
        positions[1 << 32 | 0xAAAA] = set(['10.255.255.11'])
        assert self.pglookout.get_replication_positions(standby_nodes) == positions
        # add another standby which hasn't received anything
        standby_nodes['10.255.255.12'] = dict(standby_nodes['10.255.255.10'], pg_last_xlog_receive_location=None)
        positions[0x9000090].add('10.255.255.12')
        assert self.pglookout.get_replication_positions(standby_nodes) == positions

    def test_node_map(self):
        cluster_state = {'10.255.255.10': {'fetch_time': '2014-08-28T14:26:51.066368Z',
                                           'pg_last_xlog_receive_location': '0/9000090',
                                           'pg_is_in_recovery': False,
                                           'pg_last_xact_replay_timestamp': '2014-08-28T14:05:43.577357+00:00Z',
                                           'connection': True, 'pg_last_xlog_replay_location': '0/9000090',
                                           'replication_time_lag': 1267.489727,
                                           'db_time': '2014-08-28T14:26:51.067084+00:00Z'},
                         '10.255.255.9': {'connection': False, 'fetch_time': '2014-08-28T14:26:51.068151Z'}}
        observer_state = {'10.255.255.11':
                          {'connection': True, 'fetch_time': '2014-08-28T14:26:51.069891Z',
                           '10.255.255.10': {'fetch_time': '2014-08-28T14:26:47.104849Z',
                                             'pg_last_xlog_receive_location': '0/9000090',
                                             'pg_is_in_recovery': False,
                                             'pg_last_xact_replay_timestamp': '2014-08-28T14:05:43.577357+00:00Z',
                                             'connection': True, 'pg_last_xlog_replay_location': '0/9000090',
                                             'replication_time_lag': 1263.528544,
                                             'db_time': '2014-08-28T14:26:47.105901+00:00Z'},
                           '10.255.255.9': {'fetch_time': '2014-08-28T14:26:47.107115Z',
                                            'pg_last_xlog_receive_location': None,
                                            'pg_is_in_recovery': False, 'pg_last_xact_replay_timestamp': None,
                                            'connection': False, 'pg_last_xlog_replay_location': None,
                                            'db_time': '2014-08-28T14:06:15.172820+00:00Z'}}}
        master_host, _, standby_nodes = self.pglookout.create_node_map(cluster_state, observer_state)
        self.assertEqual(master_host, "10.255.255.10")
        self.assertEqual(standby_nodes, {})

    def test_node_map_disconnected_current_master(self):
        self.pglookout.current_master = "10.255.255.7"
        cluster_state = {'10.255.255.7': {'fetch_time': '2014-09-07T15:26:34.736495Z', 'pg_last_xlog_receive_location': None,
                                          'pg_is_in_recovery': False, 'pg_last_xact_replay_timestamp': None, 'connection': False,
                                          'pg_last_xlog_replay_location': None, 'db_time': '2014-09-07T15:26:23.957151+00:00Z'},
                         '10.255.255.8': {'fetch_time': '2014-09-07T15:26:23.919281Z',
                                          'pg_last_xlog_receive_location': '0/74713D8',
                                          'pg_is_in_recovery': True,
                                          'pg_last_xact_replay_timestamp': '2014-09-07T15:25:40.372936+00:00Z',
                                          'connection': True, 'pg_last_xlog_replay_location': '0/74713D8',
                                          'replication_time_lag': 43.586525000000002,
                                          'db_time': '2014-09-07T15:26:23.959461+00:00Z'}}
        observer_state = {}
        master_host, _, standby_nodes = self.pglookout.create_node_map(cluster_state, observer_state)
        self.assertEqual(master_host, "10.255.255.7")
        self.assertEqual(list(standby_nodes.keys())[0], "10.255.255.8")

    def test_standbys_failover_equal_replication_positions(self):
        self.pglookout.current_master = "192.168.57.180"
        self.pglookout.own_db = "192.168.54.183"
        now = get_iso_timestamp(datetime.datetime.utcnow())
        self.pglookout.cluster_state = {'192.168.57.180': {'connection': False, 'fetch_time': now,
                                                           'pg_last_xlog_receive_location': None, 'db_time': '2015-04-28T11:21:55.830432Z',
                                                           'pg_is_in_recovery': False, 'replication_time_lag': 0.0,
                                                           'pg_last_xlog_replay_location': None, 'pg_last_xact_replay_timestamp': None},
                                        '192.168.63.4': {'connection': True, 'fetch_time': now,
                                                         'pg_last_xlog_receive_location': '0/70004D8', 'db_time': now,
                                                         'pg_is_in_recovery': True, 'replication_time_lag': 401.104655,
                                                         'pg_last_xlog_replay_location': '0/70004D8',
                                                         'pg_last_xact_replay_timestamp': '2015-04-28T11:21:56.098946+00:00Z'},
                                        '192.168.54.183': {'connection': True, 'fetch_time': now,
                                                           'pg_last_xlog_receive_location': '0/70004D8', 'db_time': now,
                                                           'pg_is_in_recovery': True, 'replication_time_lag': 400.435871,
                                                           'pg_last_xlog_replay_location': '0/70004D8',
                                                           'pg_last_xact_replay_timestamp': '2015-04-28T11:21:56.098946+00:00Z'}}
        self.pglookout.check_cluster_state()
        self.assertEqual(self.pglookout.execute_external_command.call_count, 1)

    def tearDown(self):
        if os.path.exists(self.state_file_path):
            os.unlink(self.state_file_path)
