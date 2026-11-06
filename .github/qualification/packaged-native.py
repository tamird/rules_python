"""Exercise the supplier's tar on a native Linux worker and in distroless."""
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tarfile


BASE = "943362ffab1a2e8488f50b8554256f9330d3837e"
SOURCE = "99afa238c84f62cb0b29e458b9a8db49d1288cf1"
IMAGE = "gcr.io/distroless/base-debian13@sha256:075a33d9c49026ca7ee72738b4c4a1caea55d0f44227356e46a611a211050e20"
PACKAGE = "//tests/bootstrap_impls:external_native_binary"
TESTS = [
    "//tests/bootstrap_impls:external_binary_test",
    "//tests/bootstrap_impls:external_native_binary_test",
]
FIXTURES = [
    "tests/bootstrap_impls/BUILD.bazel",
    "tests/bootstrap_impls/external_binary_test.sh",
    "tests/modules/other/BUILD.bazel",
]
stage = Path(os.environ["RUNNER_TEMP"]) / "packaged-launcher"
stage.mkdir()
bazel = str(Path(os.environ["RUNNER_TEMP"]) / "bazelisk")
arch = platform.machine()
report = {"source": SOURCE, "base": BASE, "image": IMAGE, "arch": arch}


def run(*cmd, **kwargs):
    return subprocess.run(cmd, check=True, **kwargs)


def save():
    (stage / "receipt.json").write_text(json.dumps(report, indent=2) + "\n")


def exercise(mode):
    result = report[mode] = {}
    src = BASE if mode == "script" else SOURCE
    run("git", "switch", "--detach", src)
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == src
    if mode == "script":
        # Only the test targets are borrowed. The archive uses the supplier's
        # original production rules and their existing shell bootstrap.
        run("git", "restore", "--source=" + SOURCE, "--worktree", "--", *FIXTURES)
        fixture = Path(FIXTURES[-1])
        orig = fixture.read_text()
        assert orig.count('bootstrap_impl = "native"') == 1
        fixture.write_text(orig.replace('bootstrap_impl = "native"', 'bootstrap_impl = "script"'))
    try:
        run(bazel, "build", "--lockfile_mode=off", PACKAGE)
        package = Path("bazel-bin/tests/bootstrap_impls/external_native_binary.tar")
        with package.open("rb") as f:
            result["tar_sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
        extracted = stage / mode
        extracted.mkdir()
        with tarfile.open(package) as tar:
            tar.extractall(extracted, filter="data")
        entry = extracted / "external_native_main"
        with entry.open("rb") as f:
            header = f.read(96)
        if mode == "script":
            result["shebang"] = header.splitlines()[0].decode()
            assert result["shebang"] == "#!/usr/bin/env bash"
        else:
            assert header[:6] == b"\x7fELF\x02\x01"
            result["elf_machine"] = int.from_bytes(header[18:20], "little")
            assert result["elf_machine"] == {"x86_64": 62, "aarch64": 183}[arch]
        child = subprocess.run([
            "docker", "run", "--rm", "--network=none", "--read-only", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--workdir=/app", "--env", "PATH=",
            "--volume", f"{extracted}:/app:ro", "--entrypoint", "/app/external_native_main", IMAGE,
        ], capture_output=True, text=True)
        result.update(exit=child.returncode, token=child.stdout.strip() == "token",
                      missing_executable="no such file or directory" in child.stderr.lower())
        if mode == "script":
            assert child.returncode != 0 and result["missing_executable"]
        else:
            assert child.returncode == 0 and result["token"], child.stderr[-1200:]
    finally:
        if mode == "script":
            run("git", "restore", "--worktree", "--", *FIXTURES)
        save()


save()
run("docker", "pull", IMAGE)
for mode in ("script", "native"):
    try:
        exercise(mode)
    except (AssertionError, OSError, tarfile.TarError, subprocess.CalledProcessError) as e:
        print(f"{mode}: {type(e).__name__}: {e}", flush=True)
        report[mode]["error_type"] = type(e).__name__
        save()

run("git", "switch", "--detach", SOURCE)
bep = stage / "tests.bep"
test = subprocess.run([bazel, "test", "--lockfile_mode=off", "--nocache_test_results",
                       "--test_output=errors", f"--build_event_json_file={bep}", *TESTS])
observed = {}
if bep.exists():
    for line in bep.read_text().splitlines():
        event = json.loads(line)
        if "testResult" in event:
            name = event["id"]["testResult"]["label"]
            observed[name] = event["testResult"]["status"]
report.update(test_exit=test.returncode, test_results=observed)
save()
assert test.returncode == 0 and observed == dict.fromkeys(TESTS, "PASSED")
assert all("error_type" not in report[mode] for mode in ("script", "native"))
