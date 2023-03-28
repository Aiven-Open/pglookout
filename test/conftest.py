"""
pglookout - test configuration

Copyright (c) 2016 Ohmu Ltd
See LICENSE for details
"""
# pylint: disable=protected-access
from __future__ import annotations

from pathlib import Path
from pglookout import logutil
from pglookout.pglookout import PgLookout
from pglookout.pgutil import DsnDict
from textwrap import dedent
from typing import cast, Final, Generator
from unittest.mock import Mock, patch

import os
import pytest
import signal
import subprocess
import time

PG_VERSIONS: Final[list[str]] = ["15", "14", "13", "12", "11", "10"]

logutil.configure_logging()


@pytest.fixture
def pgl() -> Generator[PgLookout, None, None]:
    pgl_ = PgLookout("pglookout.json")
    assert pgl_.cluster_monitor is not None
    pgl_.config["remote_conns"] = {}
    pgl_.check_for_maintenance_mode_file = Mock(return_value=False)  # type: ignore[method-assign]
    pgl_.cluster_monitor._connect_to_db = Mock()  # type: ignore[method-assign]
    pgl_.create_alert_file = Mock()  # type: ignore[method-assign]
    pgl_.execute_external_command = Mock()  # type: ignore[method-assign]
    try:
        yield pgl_
    finally:
        pgl_.quit()


class TestPG:
    def __init__(self, pgdata: Path) -> None:
        self.pgbin: Path = self.find_pgbin()
        self.pgdata: Path = pgdata
        self.pg: subprocess.Popen[bytes] | None = None

    @staticmethod
    def find_pgbin(versions: list[str] | None = None) -> Path:
        pathformats = ["/usr/pgsql-{ver}/bin", "/usr/lib/postgresql/{ver}/bin"]
        for ver in versions or PG_VERSIONS:
            for pathfmt in pathformats:
                pgbin = Path(pathfmt.format(ver=ver))
                if pgbin.is_dir():
                    return pgbin
        return Path("/usr/bin")

    @property
    def pgver(self) -> str:
        return (self.pgdata / "PG_VERSION").read_text().strip()

    def connection_dict(self, user: str = "testuser", dbname: str = "postgres") -> DsnDict:
        return cast(
            DsnDict,
            {
                "dbname": dbname,
                "host": self.pgdata,
                "port": 5432,
                "user": user,
            },
        )

    def createuser(self, user: str = "testuser") -> None:
        self.run_cmd("createuser", "-h", str(self.pgdata), "-p", "5432", "-s", user)

    def run_cmd(self, cmd: str, *args: str) -> None:
        argv = [str(self.pgbin / cmd), *args]
        subprocess.check_call(argv)

    def run_pg(self) -> None:
        self.pg = subprocess.Popen(  # pylint: disable=consider-using-with
            [
                str(self.pgbin / "postgres"),
                "-D",
                str(self.pgdata),
                "-k",
                str(self.pgdata),
                "-p",
                "5432",
                "-c",
                "listen_addresses=",
            ]
        )
        time.sleep(1.0)  # let pg start

    def kill(self, force: bool = True, immediate: bool = True) -> None:
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
            raise TimeoutError(f"PG pid {self.pg.pid} not dead")


@pytest.fixture(scope="session")
def db(tmp_path_factory: pytest.TempPathFactory) -> Generator[TestPG, None, None]:
    tmpdir = tmp_path_factory.mktemp(basename="pglookout_dbtest_")
    # try to find the binaries for these versions in some path
    pgdata = tmpdir / "pgdata"
    db = TestPG(pgdata)  # pylint: disable=redefined-outer-name
    db.run_cmd("initdb", "-D", str(pgdata), "--encoding", "utf-8")

    # allow replication connections
    (pgdata / "pg_hba.conf").write_text(
        dedent(
            """\
        local all all trust
        local replication all trust
    """
        )
    )

    with (pgdata / "postgresql.conf").open("a") as fp:
        fp.write(
            "max_wal_senders = 2\n"
            "wal_level = logical\n"
            # disable fsync and synchronous_commit to speed up the tests a bit
            "fsync = off\n"
            "synchronous_commit = off\n"
            # don't need to wait for autovacuum workers when shutting down
            "autovacuum = off\n"
        )
        if db.pgver < "13":
            fp.write("wal_keep_segments = 100\n")

    # NOTE: point $HOME to tmpdir - $HOME shouldn't affect most tests, but
    # psql triest to find .pgpass file from there as do our functions that
    # manipulate pgpass.  By pointing $HOME there we make sure we're not
    # making persistent changes to the environment.
    with patch.dict(os.environ, {"HOME": str(tmpdir)}):
        db.run_pg()
        try:
            db.createuser()
            db.createuser("otheruser")
            yield db
        finally:
            db.kill()
