from pathlib import Path
import hashlib
import json
import os
import subprocess

root = Path(os.environ["RUNNER_TEMP"])
bazel = root / "bazelisk"
receipt = root / "owner-receipt.json"
d = {"supplier": "BCR hermetic_launcher 0.0.17", "product_parent": "ed9fe9ff2db20bacf42dadbd8d80b2ac36867128"}
target = "//tests/packaged_owner_control:source_owner_test"
def save():
    receipt.write_text(json.dumps(d, indent=2)+"\n")
save()
for mode in ("script","native"):
    event = root / (mode+".bep")
    log = root / (mode+".log")
    with log.open("wb") as f:
        result = subprocess.run([bazel, "test", "--lockfile_mode=off", "--jobs=4", "--nocache_test_results",
                                 "--@rules_python//python/config_settings:bootstrap_impl="+mode,
                                 "--@rules_python//python/config_settings:build_python_zip=false",
                                 "--build_event_json_file="+str(event),"--test_output=errors","--test_arg="+mode,target],
                                stdout=f,stderr=subprocess.STDOUT)
    row = d[mode] = {"exit":result.returncode,"status":[],"log_sha256":hashlib.sha256(log.read_bytes()).hexdigest()}
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
            row["test_tail"]=text[-2300:]
    if result.returncode:
        row["diagnostic"]=[line[-400:] for line in log.read_text(errors="replace").splitlines()
                           if "ERROR:" in line or "AssertionError" in line or "Error:" in line or "FAILED" in line][-12:]
    save()
assert all(d[m]["status"]==["PASSED"] and d[m]["exit"]==0 and d[m]["cases"]["mode"]==m for m in ("script","native"))
