"""Apply the application's interpreter options, then replace this process."""

import os
import sys


def script_args(value):
    # The Bash bootstrap uses `read -a` on a here-string. An unescaped
    # newline ends the input; a backslash escapes the next character,
    # including a newline. Quotes are ordinary characters here.
    word = []
    chars = iter(value + "\n")
    for char in chars:
        if char == "\\":
            escaped = next(chars)
            if escaped != "\n":
                word.append(escaped)
        elif char in " \t\n":
            if word:
                yield "".join(word)
                word.clear()
            if char == "\n":
                return
        else:
            word.append(char)
    # A trailing backslash escaped the here-string's final newline. read
    # returns failure when it reaches EOF before the end of a logical line.
    raise SystemExit(1)


target_args = ["%interpreter_args%"]
os.environ.setdefault("PYTHONSAFEPATH", "1")
key = "RULES_PYTHON_ADDITIONAL_INTERPRETER_ARGS"
additional_args = script_args(os.environ.pop(key)) if os.environ.get(key) else ()
os.execv(
    sys.executable,
    [sys.executable, *additional_args, *target_args, *sys.argv[1:]],
)
