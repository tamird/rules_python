load("@rules_python//python:defs.bzl", "py_runtime", "py_runtime_pair")

package(default_visibility = ["//visibility:public"])

filegroup(name = "interpreter", srcs = ["bin/python3.11"])
py_runtime(
    name = "python",
    files = glob(["lib/python3.11/**/*.py"], exclude = ["lib/python3.11/test/**"]),
    interpreter = "bin/python3.11",
    python_version = "PY3",
)
py_runtime_pair(name = "runtime", py3_runtime = ":python")
