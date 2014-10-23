"""
pglookout

Copyright (c) 2014 F-Secure
See LICENSE for details
"""

import json
import os
import sys
import time

def main():
    if len(sys.argv) != 2:
        print("Usage, pglookout_current_master <path_to_pglookout.json>")
        sys.exit(-1)
    if not os.path.exists(sys.argv[1]):
        sys.exit(-1)
    try:
        config = json.loads(open(sys.argv[1], "r").read())
        state_file_path = config.get("json_state_file_path", "/tmp/pglookout_state.json")
        if time.time() - os.stat(state_file_path).st_mtime > 60.0:
            # file older than one minute, pglookout probably dead, exit with minus one
            sys.exit(-1)
        state_dict = json.loads(open(state_file_path, "r").read())
        current_master = state_dict['current_master']
        print(current_master)
    except:
        sys.exit(-1)
    sys.exit(0)

if __name__ == "__main__":
    main()
