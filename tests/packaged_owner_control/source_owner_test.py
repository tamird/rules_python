import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile


def unpack(archive_path, name, directory):
    directory.mkdir()
    with tarfile.open(archive_path) as archive:
        paths = [p for p in archive.getnames() if Path(p).name == name and ".runfiles/" not in p]
        assert len(paths) == 1, paths
        archive.extractall(directory, filter="data")
    exe = directory / paths[0]
    assert exe.is_file()
    return exe


def main():
    child_tar, parent_tar, mode = sys.argv[1:]
    results = {"mode": mode, "supplier": "BCR 0.0.17"}
    tmp = Path(os.environ["TEST_TMPDIR"]).resolve()
    child = unpack(child_tar, "child", tmp / "installed-child")
    parent = unpack(parent_tar, "parent", tmp / "installed-parent")
    child_tree = Path(str(child) + ".runfiles")
    parent_tree = Path(str(parent) + ".runfiles")
    package = "_main/tests/packaged_owner_control"
    parent_package = parent_tree / package
    marker = parent_package / "_child_stage2_bootstrap.py"
    venv_bin = Path("_child.venv/bin")
    child_interpreters = [p.name for p in (child_tree / package / venv_bin).glob("python*") if p.is_file()]
    def parent_owns_interpreter():
        return bool(child_interpreters) and all(
            (parent_package / venv_bin / name).is_file() and os.access(parent_package / venv_bin / name, os.X_OK)
            for name in child_interpreters
        )
    try:
        results["inputs"] = {
            "child_header": child.read_bytes()[:4].hex(),
            "parent_has_stage2": marker.is_file(),
            "parent_has_interpreter": parent_owns_interpreter(),
            "child_has_stage2": (child_tree / package / marker.name).is_file(),
            "interpreter_names": child_interpreters,
            "files": sorted(p.name for p in parent_package.iterdir()) if parent_package.is_dir() else [],
        }

        def owner(path):
            if not path:
                return None
            path = Path(path).resolve()
            for name, root in (("parent", parent_tree), ("child", child_tree)):
                if path == root or path.is_relative_to(root):
                    return name
            return "other"

        def run(case, *argv):
            proc = subprocess.run(argv, cwd=tmp, env={"PATH": "/usr/bin:/bin"}, capture_output=True, timeout=40, text=True)
            records = {}
            for line in proc.stdout.splitlines():
                if line.startswith("{"):
                    records.update(json.loads(line))
            results[case] = {
                "exit": proc.returncode,
                "parent_exported": owner(records.get("parent_root")),
                "child_exported": owner(records.get("child_root")),
                "application": owner(records.get("main")),
                "error": proc.stderr.strip()[-700:].replace(str(tmp), "<tmp>"),
            }

        assert all(results["inputs"][k] for k in ("child_has_stage2","parent_has_interpreter","parent_has_stage2"))
        run("direct", child)
        run("complete_parent", parent, child)
        marker.unlink()
        results["negative_inputs"] = {
            "parent_has_stage2": marker.exists(),
            "parent_has_interpreter": parent_owns_interpreter(),
            "child_has_stage2": (child_tree / package / marker.name).is_file(),
        }
        assert results["negative_inputs"] == {"parent_has_stage2": False, "parent_has_interpreter": True, "child_has_stage2": True}
        run("parent_without_stage2", parent, child)
        bindir = tmp / "bin"
        bindir.mkdir()
        alias = bindir / "my-command"
        alias.symlink_to(child)
        run("absolute_alias", alias)
        Path(str(alias) + ".runfiles").symlink_to(child_tree, target_is_directory=True)
        run("paired_alias", alias)

        assert results["direct"]["child_exported"] == "child" and results["direct"]["application"] == "child"
        assert all(results[k]["parent_exported"] == "parent" for k in ("complete_parent","parent_without_stage2"))
        assert results["complete_parent"]["child_exported"] == "parent" and results["complete_parent"]["application"] == "parent"
        assert results["paired_alias"]["child_exported"] == "child" and results["paired_alias"]["application"] == "child"
        if mode == "script":
            assert results["parent_without_stage2"]["child_exported"] == "child"
            assert results["parent_without_stage2"]["application"] == "child"
            assert results["absolute_alias"]["exit"] == 0
        elif mode == "native":
            assert results["parent_without_stage2"]["exit"] and "_child_stage2_bootstrap.py" in results["parent_without_stage2"]["error"]
            assert results["absolute_alias"]["exit"]
        else:
            raise ValueError(mode)
    finally:
        print("OWNER_RESULT " + json.dumps(results, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
