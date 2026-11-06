import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile

s = Path(os.environ["RUNNER_TEMP"]) / "native-optional"
b = str(Path(os.environ["RUNNER_TEMP"]) / "bazelisk")
fixed = "4823082f5236fdc3d8af265bd2900d50f09182b6"
d = {"source":fixed}
assert subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()==fixed
fixture=Path("tests/native_runtime_witness")
fixture.mkdir()
for name in ("BUILD.bazel","probe.py","custom.py"):
    shutil.copy(s/name,fixture/name)
label = "//tests/native_runtime_witness:probe"
native = "--@rules_python//python/config_settings:bootstrap_impl=native"
def save():
    (s/"receipt.json").write_text(json.dumps(d,indent=2)+"\n")
def run(key, *args):
    p=subprocess.run([b,*args],capture_output=True)
    log=p.stdout+p.stderr
    d[key]={"exit":p.returncode,"output_sha256":hashlib.sha256(log).hexdigest()}
    if p.returncode:
        error=[l for l in log.decode(errors="replace").splitlines() if
               any(x in l.lower() for x in ("error","cycle","toolchain","visibility","mandatory","configuredtarget"))]
        d[key]["error_lines"]=[l[-800:] for l in error[-18:]]
    save()
    return p
def dirs():
    path=Path(subprocess.check_output([b,"info","output_base"],text=True).strip())/"external"
    return sorted(p.name for p in path.iterdir() if p.is_dir() and
                  ("+finalize_stub_" in p.name or "+runfiles_stub_" in p.name))
def query(kind, *flags):
    p=run(kind,"cquery","--lockfile_mode=off",*flags,'filter("finalize_stub_|runfiles_stub_", deps('+label+'))')
    d[kind]["labels"]=[l for l in p.stdout.decode(errors="replace").splitlines() if l.startswith("@") and ("finalize_stub_" in l or "runfiles_stub_" in l)][:12]
    d[kind]["repos"]=dirs()
    save()
def package():
    test="//tests/bootstrap_impls:external_native_binary_test"
    bep=s/"results.bep"
    p=run("test","test","--config=fast-tests","--lockfile_mode=off","--nocache_test_results",
          "--build_event_json_file="+str(bep),test)
    d["test"]["observed"]=[e["testResult"]["status"] for l in bep.read_text().splitlines()
                          if (e:=json.loads(l)).get("id",{}).get("testResult",{}).get("label")==test and e.get("testResult")] if bep.exists() else []
    save()
    if p.returncode or d["test"]["observed"]!=["PASSED"]:return
    # The original product and runtime already passed native ARM64. This
    # phase proves the changed execution-platform selection on native Linux.
    pkg="//tests/bootstrap_impls:external_native_binary"
    p=run("archive","build","--lockfile_mode=off",pkg)
    if p.returncode:return
    fn=Path("bazel-bin/tests/bootstrap_impls/external_native_binary.tar")
    tree=s/"installed";tree.mkdir()
    with tarfile.open(fn) as tf:tf.extractall(tree,filter="data")
    with fn.open("rb") as f: d["archive"]["sha256"]=hashlib.file_digest(f,"sha256").hexdigest()
    p=subprocess.run([tree/"external_native_main"],cwd=s,env={"PATH":""},capture_output=True)
    d["archive"].update(launch_exit=p.returncode,token=p.stdout.strip()==b"token")
    save()
save()
query("ordinary")
query("native",native)
p=run("auto_exec_groups","build","--lockfile_mode=off","--incompatible_auto_exec_groups",native,label)
if p.returncode==0:
    p=subprocess.run([Path("bazel-bin/tests/native_runtime_witness/probe").absolute(),"argument"],capture_output=True,env={"PATH":""})
    d["auto_exec_groups"].update(launch_exit=p.returncode,expected=p.stdout.strip()==b'["default", "argument"]')
    save()
package()
assert d["ordinary"]["exit"]==0 and not d["ordinary"]["labels"] and not d["ordinary"]["repos"]
assert d["native"]["exit"]==0 and len(d["native"]["repos"])==2 and d["native"]["labels"]
assert d["auto_exec_groups"]["exit"]==0 and d["auto_exec_groups"]["expected"]
assert d["test"]["observed"]==["PASSED"] and d["archive"]["token"]
