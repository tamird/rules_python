import json
import os

print(json.dumps({"child_root": os.environ.get("RUNFILES_DIR"), "main": __file__}))
