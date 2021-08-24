"""
pglookout - test configuration

Copyright (c) 2016 Ohmu Ltd
See LICENSE for details
"""
from pglookout import logutil, pgutil
from pglookout.pglookout import PgLookout
from py import path as py_path  # pylint: disable=no-name-in-module
from unittest.mock import Mock
import os
import pytest
import signal
import subprocess
import tempfile
import time


PG_VERSIONS = ["13", "12", "11", "10", "9.6", "9.5", "9.4", "9.3", "9.2"]


logutil.configure_logging()


@pytest.fixture
def pgl():
    pgl_ = PgLookout("pglookout.json")
    pgl_.config["remote_conns"] = {}
    pgl_.check_for_maintenance_mode_file = Mock()
    pgl_.check_for_maintenance_mode_file.return_value = False
    pgl_.cluster_monitor._connect_to_db = Mock()  # pylint: disable=protected-access
    pgl_.create_alert_file = Mock()
    pgl_.execute_external_command = Mock()
    try:
        yield pgl_
    finally:
        pgl_.quit()


class TestPG:
    def __init__(self, pgdata):
        self.pgbin = self.find_pgbin()
        self.pgdata = pgdata
        self.pg = None

    @staticmethod
    def find_pgbin(versions=None):
        pathformats = ["/usr/pgsql-{ver}/bin", "/usr/lib/postgresql/{ver}/bin"]
        for ver in versions or PG_VERSIONS:
            for pathfmt in pathformats:
                pgbin = pathfmt.format(ver=ver)
                if os.path.exists(pgbin):
                    return pgbin
        return "/usr/bin"

    @property
    def pgver(self):
        with open(os.path.join(self.pgdata, "PG_VERSION"), "r") as fp:
            return fp.read().strip()

    def connection_string(self, user="testuser", dbname="postgres"):
        return pgutil.create_connection_string({
            "dbname": dbname,
            "host": self.pgdata,
            "port": 5432,
            "user": user,
        })

    def createuser(self, user="testuser"):
        self.run_cmd("createuser", "-h", self.pgdata, "-p", "5432", "-s", user)

    def run_cmd(self, cmd, *args):
        argv = [os.path.join(self.pgbin, cmd)]
        argv.extend(args)
        subprocess.check_call(argv)

    def run_pg(self):
        self.pg = subprocess.Popen([  # pylint: disable=bad-option-value,consider-using-with
            os.path.join(self.pgbin, "postgres"),
            "-D", self.pgdata, "-k", self.pgdata,
            "-p", "5432", "-c", "listen_addresses=",
        ])
        time.sleep(1.0)  # let pg start

    def kill(self, force=True, immediate=True):
        if self.pg is None:
            return
        if force:
            os.kill(self.pg.pid, signal.SIGKILL)
        elif immediate:
            os.kill(self.pg.pid, signal.SIGQUIT)
        else:
            os.kill(self.pg.pid, signal.SIGTERM)
        timeout = time.monotonic() + 10
        while (self.pg.poll() is None) and (time.monotonic() < timeout):
            time.sleep(0.1)
        if not force and self.pg.poll() is None:
            raise Exception("PG pid {} not dead".format(self.pg.pid))


# NOTE: cannot use 'tmpdir' fixture here, it only works in 'function' scope
@pytest.fixture(scope="session")
def db():
    tmpdir_obj = py_path.local(tempfile.mkdtemp(prefix="pglookout_dbtest_"))
    tmpdir = str(tmpdir_obj)
    # try to find the binaries for these versions in some path
    pgdata = os.path.join(tmpdir, "pgdata")
    db = TestPG(pgdata)  # pylint: disable=redefined-outer-name
    db.run_cmd("initdb", "-D", pgdata, "--encoding", "utf-8")
    # NOTE: point $HOME to tmpdir - $HOME shouldn't affect most tests, but
    # psql triest to find .pgpass file from there as do our functions that
    # manipulate pgpass.  By pointing $HOME there we make sure we're not
    # making persistent changes to the environment.
    os.environ["HOME"] = tmpdir
    # allow replication connections
    with open(os.path.join(pgdata, "pg_hba.conf"), "w") as fp:
        fp.write(
            "local all all trust\n"
            "local replication all trust\n"
        )
    with open(os.path.join(pgdata, "postgresql.conf"), "a") as fp:
        fp.write(
            "max_wal_senders = 2\n"
            "wal_level = archive\n"
            # disable fsync and synchronous_commit to speed up the tests a bit
            "fsync = off\n"
            "synchronous_commit = off\n"
            # don't need to wait for autovacuum workers when shutting down
            "autovacuum = off\n"
        )
        if db.pgver < "13":
            fp.write("wal_keep_segments = 100\n")
    db.run_pg()
    try:
        db.createuser()
        db.createuser("otheruser")
        yield db
    finally:
        db.kill()
        try:
            tmpdir_obj.remove(rec=1)
        except:  # pylint: disable=bare-except
            pass
