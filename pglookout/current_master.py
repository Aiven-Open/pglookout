"""
pglookout

Copyright (c) 2014 F-Secure
See LICENSE for details
"""

import json
import os
import sys
import time

def main(args):
    if len(args) != 1:
        print("Usage, pglookout_current_master <path_to_pglookout.json>")
        return -1
    if not os.path.exists(args[0]):
        return -1
    try:
        config = json.loads(open(args[0], "r").read())
        state_file_path = config.get("json_state_file_path", "/tmp/pglookout_state.json")
        if time.time() - os.stat(state_file_path).st_mtime > 60.0:
            # file older than one minute, pglookout probably dead, exit with minus one
            return -1
        state_dict = json.loads(open(state_file_path, "r").read())
        current_master = state_dict['current_master']
        print(current_master)
    except:
        return -1

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
