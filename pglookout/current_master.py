"""
pglookout_current_master - display the current cluster master

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from __future__ import annotations

from . import version
from pathlib import Path
from pglookout.default import JSON_STATE_FILE_PATH

import argparse
import json
import sys
import time


def main(args: list[str] | None = None) -> int:
    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="pglookout_current_master",
        description="postgresql replication monitoring and failover daemon",
    )
    parser.add_argument(
        "--version",
        action="version",
        help="show program version",
        version=version.__version__,
    )
    parser.add_argument("state", type=Path, help="pglookout state file")
    arg = parser.parse_args(args)

    state_file: Path = arg.state
    if not state_file.is_file():
        print(f"pglookout_current_master: {arg.state!s} doesn't exist")
        return 1

    try:
        config = json.loads(state_file.read_text(encoding="utf-8"))
        state_file_path = Path(config.get("json_state_file_path", JSON_STATE_FILE_PATH))
        if time.monotonic() - state_file_path.stat().st_mtime > 60.0:
            # file older than one minute, pglookout probably dead, exit with minus one
            return -1
        state_dict = json.loads(state_file_path.read_text(encoding="utf-8"))
        current_master = state_dict["current_master"]
        print(current_master)
    except:  # pylint: disable=bare-except
        return -1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
