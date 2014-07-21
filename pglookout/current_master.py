import json
import os
import sys

def main():
    if len(sys.argv) != 2:
        print("Usage, pglookout_current_master <path_to_pglookout.json>")
        sys.exit(-1)
    if not os.path.exists(sys.argv[1]):
        sys.exit(-1)
    try:
        config = json.loads(open(sys.argv[1], "r").read())
        state_file_path = config.get("json_state_file_path", "/tmp/json_state_file")
        state_dict = json.loads(open(state_file_path, "r").read())
        current_master = state_dict['current_master']
        print(current_master)
    except:
        sys.exit(-1)
    sys.exit(0)

if __name__ == "__main__":
    main()
