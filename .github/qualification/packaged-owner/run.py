from pathlib import Path
import hashlib
import json
import os
import re
import subprocess

root = Path(os.environ["RUNNER_TEMP"])
bazel = root / "bazelisk"
receipt = root / "owner-receipt.json"
d = {"supplier":"BCR hermetic_launcher 0.0.17","product_parent":"ed9fe9ff2db20bacf42dadbd8d80b2ac36867128"}
target = "//tests/packaged_owner_control:source_owner_test"
clean = re.compile(r"\x1b\[[0-9;]*m")
def save():
    receipt.write_text(json.dumps(d, indent=2)+"\n")
def invoke(mode, static=False):
    label="recorder_types" if static else mode
    event=root/(label+".bep")
    log=root/(label+".log")
    # Run the actual TestRunner independently of the code analysis aspect.
    # Check the private recorder's types separately after both observations.
    command=["build","--output_groups=pyrefly"] if static else ["test","--output_groups=default","--nocache_test_results","--test_output=errors","--test_arg="+mode]
    with log.open("wb") as f:
        result=subprocess.run([bazel,*command,"--lockfile_mode=off","--jobs=4","--color=no","--curses=no",
                               "--@rules_python//python/config_settings:bootstrap_impl="+mode,
                               "--@rules_python//python/config_settings:build_python_zip=false",
                               "--build_event_json_file="+str(event),target], stdout=f,stderr=subprocess.STDOUT)
    row=d[label]={"exit":result.returncode,"status":[],"log_sha256":hashlib.sha256(log.read_bytes()).hexdigest()}
    if event.exists():
        for l in event.open():
            e=json.loads(l)
            if e.get("id",{}).get("testResult",{}).get("label")==target and "testResult" in e:
                row["status"].append(e["testResult"].get("status"))
    testlog=Path("bazel-testlogs/tests/packaged_owner_control/source_owner_test/test.log")
    if row["status"] and testlog.exists():
        text=testlog.read_text(errors="replace")
        row["test_log_sha256"]=hashlib.sha256(testlog.read_bytes()).hexdigest()
        for line in text.splitlines():
            if line.startswith("OWNER_RESULT "):
                row["cases"]=json.loads(line.removeprefix("OWNER_RESULT "))
        if row["status"]!=["PASSED"]:
            row["test_tail"]=text[-4200:]
    if result.returncode:
        lines=clean.sub("",log.read_text(errors="replace")).splitlines()
        keys=("error", "failed", "exception", "typing", "source_owner_test.py:", "assertion", "incompatible", "unsupported")
        row["diagnostic"]=[line[-700:] for line in lines if any(k in line.lower() for k in keys)][-30:]
        row["first_error_context"]=next(("\n".join(lines[i:i+30])[:6500] for i,l in enumerate(lines) if "ERROR:" in l),"")
    save()
save()
for mode in ("script","native"):
    invoke(mode)
invoke("script",static=True)
assert d["recorder_types"]["exit"]==0 and all(
    d[m]["status"]==["PASSED"] and d[m]["exit"]==0 and d[m].get("cases",{}).get("mode")==m for m in ("script","native"))
