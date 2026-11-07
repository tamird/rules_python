import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any


def unpack(archive_path: str, name: str, directory: Path) -> Path:
    directory.mkdir()
    with tarfile.open(archive_path) as archive:
        paths = [p for p in archive.getnames() if Path(p).name == name and ".runfiles/" not in p]
        assert len(paths) == 1, paths
        archive.extractall(directory, filter="data")
    exe = directory / paths[0]
    assert exe.is_file()
    return exe


def main() -> None:
    child_tar, parent_tar, independent_tar, mode = sys.argv[1:]
    arm = os.environ["PACKAGED_OWNER_ARM"]
    results: dict[str, Any] = {"mode": mode, "arm": arm}
    tmp = Path(os.environ["TEST_TMPDIR"]).resolve()
    child = unpack(child_tar, "child", tmp / "installed-child")
    parent = unpack(parent_tar, "parent", tmp / "installed-parent")
    independent = unpack(independent_tar, "independent_parent", tmp / "installed-independent-parent")
    independent_tree = Path(str(independent) + ".runfiles")
    child_tree = Path(str(child) + ".runfiles")
    parent_tree = Path(str(parent) + ".runfiles")
    package = "_main/tests/packaged_owner_control"
    parent_package = parent_tree / package
    marker = parent_package / "_child_stage2_bootstrap.py"
    venv_bin = Path("_child.venv/bin")
    child_interpreters = [p.name for p in (child_tree / package / venv_bin).glob("python*") if p.is_file()]
    def tree_owns_interpreter(tree: Path) -> bool:
        return bool(child_interpreters) and all(
            (tree / package / venv_bin / name).is_file() and os.access(tree / package / venv_bin / name, os.X_OK)
            for name in child_interpreters
        )
    try:
        def digest(path: Path) -> str:
            return hashlib.sha256(path.read_bytes()).hexdigest()

        results["hashes"] = {
            "archives": [digest(Path(a)) for a in (child_tar, parent_tar, independent_tar)],
            "launchers": {p.name: digest(p) for p in (child, parent, independent)},
            "stage2": digest(child_tree / package / marker.name),
            "declared_stage2": digest(marker),
            "main": digest(child_tree / package / "child.py"),
            "declared_main": digest(parent_package / "child.py"),
        }
        assert results["hashes"]["stage2"] == results["hashes"]["declared_stage2"]
        assert results["hashes"]["main"] == results["hashes"]["declared_main"]
        results["inputs"] = {
            "child_header": child.read_bytes()[:4].hex(),
            "parent_has_stage2": marker.is_file(),
            "independent_has_stage2": (independent_tree / package / marker.name).is_file(),
            "independent_has_child_venv_interpreter": tree_owns_interpreter(independent_tree),
            "independent_has_child_launcher": (independent_tree / package / "child").exists(),
            "independent_has_own_interpreter": all(
                (independent_tree / package / "_independent_parent.venv/bin" / name).is_file()
                for name in child_interpreters
            ),
            "parent_has_interpreter": tree_owns_interpreter(parent_tree),
            "child_has_stage2": (child_tree / package / marker.name).is_file(),
            "interpreter_names": child_interpreters,
            "stage2_key": str(marker.relative_to(parent_tree)),
            "files": sorted(p.name for p in parent_package.iterdir()) if parent_package.is_dir() else [],
        }

        def owner(value: str | None) -> str | None:
            if not value:
                return None
            path = Path(value.removesuffix("_manifest") if value.endswith(".runfiles_manifest") else value).resolve()
            for name, root in (("independent", independent_tree), ("parent", parent_tree), ("child", child_tree)):
                if path == root or path.is_relative_to(root):
                    return name
            return "other"

        def run(case: str, *argv: Path) -> None:
            proc = subprocess.run(argv, cwd=tmp, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=40, text=True)
            records: dict[str, str | None] = {}
            for line in proc.stdout.splitlines():
                if line.startswith("{"):
                    records.update(json.loads(line))
            results[case] = {
                "exit": proc.returncode,
                "stdout_sha256": hashlib.sha256(proc.stdout.encode()).hexdigest(),
                "stderr_sha256": hashlib.sha256(proc.stderr.encode()).hexdigest(),
                "parent_exported": owner(records.get("parent_root") or records.get("parent_manifest")),
                "child_exported": owner(records.get("child_root") or records.get("child_manifest")),
                "application": owner(records.get("main")),
                "child_variable": "directory" if records.get("child_root") else ("manifest" if records.get("child_manifest") else None),
                "error": proc.stderr.strip()[-700:].replace(str(tmp), "<tmp>"),
            }

        assert all(results["inputs"][k] for k in (
            "child_has_stage2", "parent_has_interpreter", "parent_has_stage2", "independent_has_own_interpreter"
        )), results["inputs"]
        assert not any(results["inputs"][k] for k in (
            "independent_has_stage2", "independent_has_child_venv_interpreter", "independent_has_child_launcher"
        )), results["inputs"]
        run("direct", child)
        run("declared_parent", parent, child)
        run("independent_parent", independent, child)
        assert results["direct"]["exit"] == 0 and results["direct"]["application"] == "child"
        assert results["declared_parent"]["exit"] == 0 and results["declared_parent"]["application"] == "parent"
        assert results["declared_parent"]["parent_exported"] == results["declared_parent"]["child_exported"] == "parent"
        assert results["independent_parent"]["parent_exported"] == "independent", results["independent_parent"]
        if arm == "fixed":
            assert results["independent_parent"]["exit"] == 0
            assert results["independent_parent"]["application"] == results["independent_parent"]["child_exported"] == "child"
        else:
            assert arm in ("stock", "unset") and mode == "native"
            assert results["independent_parent"]["exit"] == 1
            assert results["independent_parent"]["application"] is None
    finally:
        print("OWNER_RESULT " + json.dumps(results, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
