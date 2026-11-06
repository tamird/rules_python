"""Apply the application's interpreter options, then replace this process."""

import os
import sys

target_args = ["%interpreter_args%"]
os.environ.setdefault("PYTHONSAFEPATH", "1")
additional_args = os.environ.pop("RULES_PYTHON_ADDITIONAL_INTERPRETER_ARGS", "").split()
os.execv(
    sys.executable,
    [sys.executable, *additional_args, *target_args, *sys.argv[1:]],
)
