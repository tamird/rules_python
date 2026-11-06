import json
import os
import sys

print(json.dumps([os.getenv("NATIVE_CUSTOM_PREPARER", "default"), *sys.argv[1:]]))
