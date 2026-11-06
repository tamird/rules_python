load("//python:defs.bzl", "py_binary")

package(default_visibility = ["//visibility:public"])
exports_files(["python.BUILD"])
toolchain(
    name = "runtime",
    exec_compatible_with = ["@platforms//cpu:x86_64", "@platforms//os:linux"],
    target_compatible_with = ["@platforms//cpu:x86_64", "@platforms//os:linux"],
    toolchain = "@stage1_cpython//:runtime",
    toolchain_type = "@bazel_tools//tools/python:toolchain_type",
)
py_binary(name = "hello", srcs = ["hello.py"])
py_binary(name = "show", srcs = ["show.py"], interpreter_args = ["-Xdupe=target"])
