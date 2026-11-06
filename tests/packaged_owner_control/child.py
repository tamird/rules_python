import json
import os

print(json.dumps({"child_root": os.environ.get("RUNFILES_DIR"), "child_manifest": os.environ.get("RUNFILES_MANIFEST_FILE"), "main": __file__}))
