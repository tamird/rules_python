import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import traceback

CASE = Path(__file__).resolve().parent
IMAGE = "gcr.io/distroless/static-debian13@sha256:d03c145fad326bff275ec4106475ed8310426caf709decd9f8cbc494af59bc18"
SOURCES = {"script": "943362ffab1a2e8488f50b8554256f9330d3837e", "native": "804470ef813ec3a25df2432d374f84bac6ec2d76"}
receipt = {"image": IMAGE, "sources": SOURCES, "results": {}}
result_file = CASE / "results.json"

def save():
    result_file.write_text(json.dumps(receipt, indent=2) + "\n")

def run(args, **kw):
    return subprocess.run(args, text=True, **kw)

def capture(args):
    return subprocess.check_output(args, text=True).strip()

def compare(name, args, expected="", missing=False, **kw):
    proc = run(args, capture_output=True, **kw)
    sig = proc.stdout.strip()
    ok = (proc.returncode != 0 and "no such file or directory" in proc.stderr.lower()) if missing else (
        proc.returncode == 0 and re.fullmatch("[a-f0-9]{64}", sig) and (not expected or sig == expected)
    )
    receipt["results"][name] = {"exit": proc.returncode, "ok": bool(ok)}
    if re.fullmatch("[a-f0-9]{64}", sig):
        receipt["results"][name]["payload"] = sig
    if not ok or missing:
        receipt["results"][name]["diagnostic"] = proc.stderr[-1300:]
    save()
    return sig

def form(path):
    raw = path.read_bytes()
    if not raw.startswith(b"\x7fELF"):
        return {"shebang": raw.split(b"\n", 1)[0].decode()}
    elf = subprocess.check_output(["readelf", "-h", "-l", path], text=True)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "x86_64": "Advanced Micro Devices X86-64" in elf,
            "interp": "INTERP" in elf}

def copy_runfiles(source, target):
    def copy(src, dst):
        if src.is_symlink():
            link = Path(os.path.normpath(src.parent / os.readlink(src)))
            if link.is_relative_to(source):
                dst.symlink_to(os.path.relpath(target / link.relative_to(source), dst.parent))
                return
        if src.is_dir():
            dst.mkdir()
            for child in src.iterdir():
                copy(child, dst / child.name)
        else:
            shutil.copy2(src, dst)
    copy(source, target)

def docker_args(bind, entry, env=()):
    args = ["docker", "run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--tmpfs=/tmp:rw,nosuid,nodev,size=32m", "--workdir=/tmp"]
    for host, dest in bind:
        args += ["--volume", f"{host}:{dest}:ro"]
    for value in env:
        args += ["--env", value]
    return [*args, "--entrypoint", entry, IMAGE]

def main():
    save()
    run(["docker", "pull", IMAGE], check=True)
    bazel = [str(CASE / "bazelisk"), "--ignore_all_rc_files"]
    result_hash = ""
    for mode, source in SOURCES.items():
        run(["git", "checkout", "--detach", source], check=True)
        assert capture(["git", "rev-parse", "HEAD"]) == source
        package = Path("tests/native_stage1")
        package.mkdir(exist_ok=True)
        for before, after in [("fixture.BUILD", "BUILD.bazel"), ("python.BUILD", "python.BUILD"), ("probe.py", "probe.py")]:
            shutil.copy2(CASE / before, package / after)
        with Path("MODULE.bazel").open("a") as module:
            module.write((CASE / "runtime.MODULE").read_text())
        cfg = ["--ignore_dev_dependency", "--extra_toolchains=//tests/native_stage1:runtime",
               "--//python/config_settings:bootstrap_impl=" + mode]
        labels = ["//tests/native_stage1:app", "//tests/native_stage1:app_test"]
        run([*bazel, "build", *cfg, *labels, "@stage1_cpython//:interpreter"], check=True)
        out = Path(capture([*bazel, "info", "bazel-bin"])).resolve()
        if mode == "script":
            execroot = Path(capture([*bazel, "info", "execution_root"]))
            path = capture([*bazel, "cquery", *cfg, "@stage1_cpython//:interpreter", "--output=files"])
            interpreter = (execroot / path).resolve()
            receipt["interpreter"] = form(interpreter)
            save()
            assert receipt["interpreter"]["x86_64"] and not receipt["interpreter"]["interp"]
            args = docker_args([(interpreter.parent.parent, "/python"), (CASE / "probe.py", "/probe.py")],
                               "/python/bin/python3.11")
            result_hash = compare("direct_cpython_bzip2_tar", [*args, "-I", "-S", "/probe.py"])
        for name in ("app", "app_test"):
            # The Bazel output symlink reflects the exact just-built configured target.
            exe = Path("bazel-bin/tests/native_stage1", name).absolute()
            if not exe.exists():
                exe = out / "tests/native_stage1" / name
            meta = form(exe)
            receipt.setdefault("tools", {})[mode + "/" + name] = meta
            save()
            if mode == "script":
                assert meta["shebang"] == "#!/usr/bin/env bash"
            else:
                assert meta["x86_64"] and not meta["interp"]
            argv = ["space here", "--arg"]
            key = f"{mode}/{name}/"
            env = dict(os.environ, RULES_PYTHON_ADDITIONAL_INTERPRETER_ARGS="-O")
            compare(key + "ordinary_host", [exe, *argv], result_hash, env=env)
            staged = CASE / (mode + "-" + name)
            staged.mkdir()
            shutil.copy2(exe, staged / "entry")
            tree = staged / "entry.runfiles"
            # Resolve the Bazel output directory before interpreting its internal links.
            src = (exe.parent.resolve() / (name + ".runfiles")).resolve()
            copy_runfiles(src, tree)
            entry = docker_args([(staged, "/case")], "/case/entry",
                                ["RULES_PYTHON_ADDITIONAL_INTERPRETER_ARGS=-O"])
            compare(key + "tree", [*entry, *argv], result_hash, missing=(mode == "script"))
            if mode == "native":
                tree.rename(staged / "relocated.runfiles")
                manifest = staged / "prefix.manifest"
                manifest.write_text("".join(f"{p.name} /case/relocated.runfiles/{p.name}\n"
                                            for p in sorted((staged / "relocated.runfiles").iterdir())))
                entry = docker_args([(staged, "/case")], "/case/entry", [
                    "RUNFILES_MANIFEST_FILE=/case/prefix.manifest",
                    "RULES_PYTHON_ADDITIONAL_INTERPRETER_ARGS=-O",
                ])
                compare(key + "manifest", [*entry, *argv], result_hash)
        run(["git", "restore", "MODULE.bazel"], check=True)
    assert len(receipt["results"]) == 11
    assert all(v["ok"] for v in receipt["results"].values())

try:
    main()
except Exception as exc:
    receipt["exception_type"] = type(exc).__name__
    save()
    traceback.print_exc()
    raise SystemExit(1)
