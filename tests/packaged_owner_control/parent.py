import json
import os
import sys

print(json.dumps({"parent_root": os.environ.get("RUNFILES_DIR")}), flush=True)
os.execv(sys.argv[1], sys.argv[1:])
