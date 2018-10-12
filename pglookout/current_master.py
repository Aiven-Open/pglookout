"""
pglookout_current_master - display the current cluster master

Copyright (c) 2015 Ohmu Ltd
Copyright (c) 2014 F-Secure

This file is under the Apache License, Version 2.0.
See the file `LICENSE` for details.
"""

from __future__ import print_function
from . import version
import argparse
import json
import os
import sys
import time


def main(args=None):
    if args is None:
        args = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="pglookout_current_master",
        description="postgresql replication monitoring and failover daemon")
    parser.add_argument("--version", action="version", help="show program version",
                        version=version.__version__)
    parser.add_argument("state", help="pglookout state file")
    arg = parser.parse_args(args)

    if not os.path.exists(arg.state):
        print("pglookout_current_master: {!r} doesn't exist".format(arg.state))
        return 1

    try:
        with open(arg.state, "r") as fp:
            config = json.load(fp)
        state_file_path = config.get("json_state_file_path", "/tmp/pglookout_state.json")  # pylint: disable=no-member
        if time.monotonic() - os.stat(state_file_path).st_mtime > 60.0:
            # file older than one minute, pglookout probably dead, exit with minus one
            return -1
        with open(state_file_path, "r") as fp:
            state_dict = json.load(fp)
        current_master = state_dict['current_master']
        print(current_master)
    except:  # pylint: disable=bare-except
        return -1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
