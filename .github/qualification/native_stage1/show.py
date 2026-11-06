import json
import os
import sys

print(json.dumps([sys._xoptions, sys.argv[1:], os.environ.get("RULES_PYTHON_ADDITIONAL_INTERPRETER_ARGS")], sort_keys=True))
