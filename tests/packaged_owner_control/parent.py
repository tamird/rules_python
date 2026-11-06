import json
import os
import sys

print(json.dumps({"parent_root": os.environ.get("RUNFILES_DIR"), "parent_manifest": os.environ.get("RUNFILES_MANIFEST_FILE")}), flush=True)
os.execv(sys.argv[1], sys.argv[1:])
