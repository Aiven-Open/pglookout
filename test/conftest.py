"""
pglookout - test configuration

Copyright (c) 2016 Ohmu Ltd
See LICENSE for details
"""
try:
    from mock import Mock  # pylint: disable=import-error
except ImportError:  # py3k import location
    from unittest.mock import Mock  # pylint: disable=import-error,no-name-in-module
from pglookout import logutil
from pglookout.pglookout import PgLookout
import pytest


logutil.configure_logging()


@pytest.yield_fixture
def pgl():
    pgl_ = PgLookout("pglookout.json")
    pgl_.check_for_maintenance_mode_file = Mock()
    pgl_.check_for_maintenance_mode_file.return_value = False
    pgl_.cluster_monitor._connect_to_db = Mock()  # pylint: disable=protected-access
    pgl_.create_alert_file = Mock()
    pgl_.execute_external_command = Mock()
    try:
        yield pgl_
    finally:
        pgl_.quit()
