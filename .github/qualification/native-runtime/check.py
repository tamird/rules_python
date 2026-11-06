"""Qualify the original and corrected supplier bootstrap through Bazel."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile

old = "cbbf94eee74ac2087d565a247a724e52596e66b9"
fixed = "ed9fe9ff2db20bacf42dadbd8d80b2ac36867128"
base = "gcr.io/distroless/base-debian13@sha256:075a33d9c49026ca7ee72738b4c4a1caea55d0f44227356e46a611a211050e20"
libc = "gcr.io/distroless/cc-debian13@sha256:58e12be899275afaa05f1b03051ab19037ed7f252528e57a3a9ad7e220a62578"
stage = Path(os.environ["RUNNER_TEMP"]) / "native-runtime"
arch = platform.machine()
bazel = str(Path(os.environ["RUNNER_TEMP"]) / "bazelisk")
pkg = "//tests/native_runtime_witness"
label = pkg + ":probe"
native = ["--lockfile_mode=off","--@rules_python//python/config_settings:bootstrap_impl=native"]
report = {"old":old,"fixed":fixed,"arch":arch}

def save():
    (stage / "receipt.json").write_text(json.dumps(report,indent=2) + "\n")

def run(cmd, *args, **kw):
    return subprocess.run([cmd,*args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, **kw)

def diagnose(p):
    # The job has no service credentials. Include bounded diagnostics so a
    # failed fixture can be distinguished from toolchain selection or startup.
    return {"exit":p.returncode,"tail":p.stdout.decode(errors="replace")[-1800:]}

def switch(commit):
    subprocess.run(["git","switch","--quiet","--detach",commit], check=True)
    assert subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip() == commit

def config():
    switch(old)
    q = 'filter("finalize_stub_|runfiles_stub_", deps(' + label + '))'
    flags = ["--lockfile_mode=off","--toolchain_resolution_debug=.*(finalizer|template)_toolchain_type"]
    p = run(bazel, "cquery", *flags, q)
    log = p.stdout.decode(errors="replace").splitlines()
    result = {
        "exit":p.returncode,
        "dependency_labels":[l for l in log if l.startswith("@") and ("finalize_stub_" in l or "runfiles_stub_" in l)][:12],
        "selected_toolchains":[l.strip()[-400:] for l in log if ("finalizer" in l or "template" in l) and "Selected" in l][:12],
    }
    if p.returncode: result["error"]=diagnose(p)
    ob = subprocess.check_output([bazel, "info", "output_base"],text=True).strip()
    assert Path(ob).is_absolute()
    external = Path(ob)/"external"
    result["fetched_release_repo_dirs"] = sorted(p.name for p in external.iterdir() if p.is_dir() and
                                                 ("+finalize_stub_" in p.name or "+runfiles_stub_" in p.name))
    report["cold_ordinary_mode"]=result
    save()

def simulate(commit):
    switch(commit)
    d=report[commit]={}
    for tool in ("custom","path"):
        flags=[*native,"--extra_toolchains="+pkg+":"+tool+"_toolchain"]
        if tool=="path":
            p=run(bazel,"cquery",*flags,label)
            d[tool]={"exit":p.returncode,"diagnostic":"Native startup requires a py_runtime with an interpreter file" in p.stdout.decode(errors="replace")}
            if p.returncode and not d[tool]["diagnostic"]:d[tool]["error"]=diagnose(p)
            save()
        else:
            p=run(bazel,"build",*flags,label)
            d[tool]={"build":p.returncode}
            save()
            if p.returncode:
                d[tool]["error"]=diagnose(p);save();continue
            entry=Path("bazel-bin/tests/native_runtime_witness/probe").absolute()
            p=run(entry, "literal space", "-Xargument", env={"PATH":os.defpath})
            d[tool]["native_exit"]=p.returncode
            d[tool]["observed"]=p.stdout.decode(errors="replace").strip()[-700:]
            save()
    p=run(bazel,"build",*native,label)
    d["default_build"]=p.returncode
    if p.returncode: d["default_error"]=diagnose(p);save();return
    entry=Path("bazel-bin/tests/native_runtime_witness/probe").absolute()
    for key,extra in (("plain",{}),("display_override",{"PYTHONEXECUTABLE":"/does-not-exist/declared-display-name"})):
        p=run(entry, "literal space","-Xargument",env={"PATH":os.defpath,**extra})
        d[key]={"exit":p.returncode,"observed":p.stdout.decode(errors="replace").strip()[-700:]}
    save()

def packaging():
    switch(fixed)
    d=report["package"]={}
    tar_label="//tests/bootstrap_impls:external_native_binary"
    p=run(bazel,"build","--lockfile_mode=off",tar_label)
    d["build_exit"]=p.returncode
    if p.returncode:d["error"]=diagnose(p);save();return
    out=stage/"installed";out.mkdir()
    fn=Path("bazel-bin/tests/bootstrap_impls/external_native_binary.tar")
    with fn.open("rb") as f:d["tar_sha256"]=hashlib.file_digest(f,"sha256").hexdigest()
    with tarfile.open(fn) as tar:tar.extractall(out,filter="data")
    with (out/"external_native_main").open("rb") as f:elf=f.read(20)
    assert elf[:6]==b"\x7fELF\x02\x01"
    d["machine"]=int.from_bytes(elf[18:20],"little")
    subprocess.run(["docker","pull",base],check=True,stdout=subprocess.DEVNULL)
    args=["docker","run","--rm","--network=none","--read-only","--cap-drop=ALL","--security-opt=no-new-privileges",
          "--workdir=/app","--env","PATH=","--volume",f"{out}:/app:ro","--entrypoint","/app/external_native_main"]
    p=run(*args,base);d["base_exit"]=p.returncode
    if arch=="aarch64" and p.returncode and b"libgcc_s.so.1: cannot open shared object file" in p.stdout:
        libdir=stage/"declared";libdir.mkdir()
        subprocess.run(["docker","pull",libc],check=True,stdout=subprocess.DEVNULL)
        cid=subprocess.check_output(["docker","create","--entrypoint","/unused",libc],text=True).strip()
        try:subprocess.run(["docker","cp","-L",cid+":/usr/lib/aarch64-linux-gnu/libgcc_s.so.1",str(libdir/"libgcc_s.so.1")],check=True)
        finally:subprocess.run(["docker","rm",cid],check=True,stdout=subprocess.DEVNULL)
        d["libgcc_sha256"]=hashlib.sha256((libdir/"libgcc_s.so.1").read_bytes()).hexdigest()
        p=run(*args,"--volume",f"{libdir}:/declared:ro","--env","LD_LIBRARY_PATH=/declared",base)
    d.update(exit=p.returncode,token=p.stdout.strip()==b"token")
    if p.returncode:d["error"]=diagnose(p)
    save()
    labels=["//tests/bootstrap_impls:external_native_binary_test","//tests/bootstrap_impls:external_binary_test"]
    bep=stage/"test.bep"
    p=run(bazel,"test","--lockfile_mode=off","--config=fast-tests","--nocache_test_results","--build_event_json_file="+str(bep),*labels)
    d["tests"]={}
    if bep.is_file():
        for line in bep.open():
            e=json.loads(line)
            if "testResult" in e: d["tests"][e["id"]["testResult"]["label"]]=e["testResult"]["status"]
    d["test_exit"]=p.returncode
    if p.returncode:d["test_error"]=diagnose(p)
    save()

fixture=Path("tests/native_runtime_witness");fixture.mkdir()
for name in ("BUILD.bazel","custom.py","probe.py"):shutil.copy(stage/name,fixture/name)
try:
    if arch=="x86_64": config()
    for commit in (old,fixed):simulate(commit)
    packaging()
finally:save()
assert report[old]["path"]["exit"]==0 and report[fixed]["path"]["diagnostic"]
assert report[old]["display_override"]["exit"]!=0,report[old]["display_override"]
expected=["default","literal space","-Xargument"]
assert json.loads(report[fixed]["display_override"]["observed"])==expected and report[fixed]["display_override"]["exit"]==0
assert json.loads(report[old]["custom"]["observed"])==expected
assert json.loads(report[fixed]["custom"]["observed"])==["configured",*expected[1:]]
assert report["package"]["token"] and report["package"]["test_exit"]==0 and set(report["package"]["tests"].values())=={"PASSED"}
if arch=="x86_64":assert report["cold_ordinary_mode"]["exit"]==0
