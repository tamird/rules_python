import bz2
import hashlib
import io
import os
import sys
import tarfile

message = b"rules_python tarfile and bz2"
output = io.BytesIO()
with tarfile.open(fileobj=output, mode="w:bz2") as archive:
    info = tarfile.TarInfo("message")
    info.size = len(message)
    archive.addfile(info, io.BytesIO(message))
compressed = output.getvalue()
if not bz2.decompress(compressed).startswith(b"message"):
    raise ValueError("missing tar header")
with tarfile.open(fileobj=io.BytesIO(compressed), mode="r:bz2") as archive:
    if archive.extractfile("message").read() != message:
        raise ValueError("archive content mismatch")
if len(sys.argv) > 1:
    if sys.argv[1:] != ["space here", "--arg"]:
        raise ValueError(sys.argv)
    if not sys.flags.dont_write_bytecode or sys.flags.optimize != 1:
        raise ValueError(sys.flags)
    if sys.prefix == sys.base_prefix or not sys.flags.safe_path:
        raise ValueError(("venv", sys.prefix, sys.base_prefix, sys.flags))
    if "RULES_PYTHON_ADDITIONAL_INTERPRETER_ARGS" in os.environ:
        raise ValueError("launcher option leaked")
print(hashlib.sha256(compressed).hexdigest())
