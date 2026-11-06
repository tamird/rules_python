import os
import sys

exe, stage2, *args = sys.argv[1:]
os.environ["NATIVE_CUSTOM_PREPARER"] = "configured"
os.execv(exe, [exe, *["%interpreter_args%"], stage2, *args])
