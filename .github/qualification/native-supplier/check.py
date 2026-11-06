"""Qualify source-built runfiles discovery and the opt-in Bazel action."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tarfile

workspace = Path(os.environ["GITHUB_WORKSPACE"])
supplier = workspace/"hermetic-launcher"
consumer = workspace/"rules_python"
stage = Path(os.environ["RUNNER_TEMP"])/"native-supplier"
bazel = Path(os.environ["RUNNER_TEMP"])/"bazelisk"
source = "1baa0a8d7f020031b489e752e715ec24281d61ab"
launcher_source = "b0d103f65a4d2a14c6407d2ffb7208aa36cd745f"
base = "gcr.io/distroless/base-debian13@sha256:075a33d9c49026ca7ee72738b4c4a1caea55d0f44227356e46a611a211050e20"
d = {"consumer":source,"supplier":launcher_source}
def save():
    (stage/"receipt.json").write_text(json.dumps(d,indent=2)+"\n")
def digest(path):
    with path.open("rb") as f:return hashlib.file_digest(f,"sha256").hexdigest()
def run(name,*cmd,cwd,required=True):
    file=stage/(name+".log")
    print("Starting",name,flush=True)
    with file.open("wb") as out:
        p=subprocess.run(cmd,cwd=cwd,stdout=out,stderr=subprocess.STDOUT)
    row=d[name]={"exit":p.returncode,"log_sha256":digest(file),"bytes":file.stat().st_size}
    if p.returncode:
        with file.open(errors="replace") as log:
            hints=[l.strip()[-780:] for l in log if any(k in l.lower() for k in
                   ("error", "cycle", "panicked","failed", "not found", "unresolved", "fatal:", "could not",
                    "no space left","expected", "not usable", "undefined", "killed", "not a directory"))]
            row["diagnostic"]=hints[-25:]
    save()
    if required and p.returncode:raise ValueError(name)
    return file
def beps(path,label):
    result=[]
    if path.exists():
        for l in path.open():
            e=json.loads(l)
            if e.get("id",{}).get("testResult",{}).get("label")==label and "testResult" in e:
                result.append(e["testResult"].get("status"))
    return result
def verify():
    for tree,pin in ((supplier,launcher_source),(consumer,source)):
        assert subprocess.check_output(["git","rev-parse","HEAD"],cwd=tree,text=True).strip()==pin
    for name in ("declared-finalizer","runfiles-discovery"):
        patch = stage/(name+".patch")
        run("apply_"+name,"git","apply","--check",patch,cwd=supplier)
        subprocess.run(["git","apply",patch],cwd=supplier,check=True)
        d[name+"_patch_sha256"]=digest(patch)
    save()
    subprocess.run(["git","diff","--check"],cwd=supplier,check=True)
    run("buildifier",Path(os.environ["RUNNER_TEMP"])/"buildifier","-mode=check",
        consumer/"python/private/py_executable.bzl",supplier/"launcher/private/rules/lib.bzl",cwd=consumer)
    # This is the supplier's own native integration binary and optimized
    # toolchain, including its manifest-only and logical argv[0] tests.
    label="//integration-tests:integration_test"
    bep=stage/"supplier.bep"
    run("integration",bazel,"test","--config=release","--jobs=4","--lockfile_mode=off","--nocache_test_results",
        "--test_output=errors","--build_event_json_file="+str(bep),label,cwd=supplier,required=False)
    d["integration"]["result"]=beps(bep,label)
    testlog=supplier/"bazel-testlogs/integration-tests/integration_test/test.log"
    if testlog.is_file():
        text=testlog.read_text(errors="replace")
        d["integration"]["cases"]=sorted(set(re.findall(r"Running test: ([a-z0-9_]+)",text)))
        d["integration"]["test_log_sha256"]=digest(testlog)
        if d["integration"]["result"]!=["PASSED"]:
            d["integration"]["test_end"]=text[-2500:]
    save()
    # Even a regression in another integration case must not hide whether
    # the actual original Python package now launches. Bazel reuses the two
    # products the integration test selected.
    labels={"runfiles-stub-x86_64-linux":"//integration-tests:release-template",
            "finalize-stub-x86_64-linux":"//integration-tests:release-finalizer"}
    run("supplier_binaries",bazel,"build","--config=release","--jobs=4","--lockfile_mode=off",*labels.values(),cwd=supplier)
    # Model a supplier release, replacing just its AMD64 artifacts with the
    # finalizer and template produced by the actual release targets above.
    ext=supplier/"launcher/private/extensions.bzl"
    text=ext.read_text()
    for name,target in labels.items():
        path_out=subprocess.check_output([bazel,"cquery","--config=release","--jobs=4","--lockfile_mode=off",
                                          "--output=files",target],cwd=supplier,text=True,stderr=subprocess.DEVNULL)
        paths=[supplier/l for l in path_out.splitlines() if l.startswith("bazel-out/")]
        assert len(paths)==1 and paths[0].is_file(),path_out[-400:]
        artifact=stage/name
        shutil.copy2(paths[0],artifact)
        sha=d[name+"_sha256"]=digest(artifact)
        with artifact.open("rb") as f:header=f.read(20)
        assert header[:6]==b"\x7fELF\x02\x01" and int.from_bytes(header[18:20],"little")==62
        d[name+"_size"]=artifact.stat().st_size
        pat=r'("'+name+r'": \{[^}]*?"url": ")[^"]+(",\s*"sha256": ")[0-9a-f]{64}(")'
        text,count=re.subn(pat,lambda m:m[1]+artifact.as_uri()+m[2]+sha+m[3],text)
        assert count==1
        save()
    ext.write_text(text)
    with (consumer/"MODULE.bazel").open("a") as f:
        f.write('\nlocal_path_override(module_name = "hermetic_launcher", path = "../hermetic-launcher")\n')
    # Check an ordinary py_binary first, with the same target used in the
    # prior default-mode evidence. The consumer has a separate canonical
    # output base from the supplier by virtue of being a different workspace.
    fixture=consumer/"tests/native_runtime_witness";fixture.mkdir()
    (fixture/"BUILD.bazel").write_text('load("//python:py_binary.bzl", "py_binary")\npy_binary(name="probe", srcs=["probe.py"])\n')
    (fixture/"probe.py").write_text('print("native probe")\n')
    probe="//tests/native_runtime_witness:probe"
    q='filter("finalize_stub_|runfiles_stub_", deps('+probe+'))'
    cold=run("default",bazel,"cquery","--lockfile_mode=off",q,cwd=consumer)
    d["default"]["stub_labels"]=[l[:280] for l in cold.read_text(errors="replace").splitlines() if l.startswith("@") and ("_stub_" in l)]
    cached=Path(subprocess.check_output([bazel,"info","output_base"],cwd=consumer,text=True).strip())/"external"
    d["default"]["stub_repositories"]=sorted(p.name for p in cached.iterdir() if p.is_dir() and
                                               ("+runfiles_stub_" in p.name or "+finalize_stub_" in p.name))
    save()
    labels=["//tests/bootstrap_impls:external_native_binary","//tests/bootstrap_impls:external_binary"]
    run("package",bazel,"build","--lockfile_mode=off","--jobs=4","--incompatible_auto_exec_groups",*labels,cwd=consumer)
    for key,target in zip(("inner","parent"),labels):
        path=consumer/"bazel-bin/tests/bootstrap_impls"/(target.split(":")[1]+".tar")
        tree=stage/key;tree.mkdir()
        with tarfile.open(path) as tar:tar.extractall(tree,filter="data")
        d[key+"_tar_sha256"]=digest(path)
    inner=stage/"inner"/"external_native_main"
    tree=stage/"parent"/"external_main.runfiles"
    assert tree.is_dir()
    # Relative link chained to an absolute link: neither has its own source.
    ab=stage/"absolute";ab.symlink_to(inner)
    rel=stage/"relative";rel.symlink_to("absolute")
    for name,entry,extra in (("direct",inner,{}),("foreign",inner,{"RUNFILES_DIR":str(tree)}),("absolute_link",ab,{}),("relative_link",rel,{})):
        p=subprocess.run([entry],capture_output=True,cwd=stage,env={"PATH":"",**extra})
        d[name]={"exit":p.returncode,"token":p.stdout.strip()==b"token","diagnostic":(p.stdout+p.stderr).decode(errors="replace")[-800:]}
        save()
    run("image", "docker","pull",base,cwd=consumer)
    result=run("distroless","docker","run","--rm","--read-only","--network=none","--cap-drop=ALL","--security-opt=no-new-privileges",
               "--env","PATH=","--volume",str(stage/"inner")+":/app:ro","--workdir=/app","--entrypoint","/app/external_native_main",base,cwd=consumer)
    d["distroless"]["token"]=result.read_bytes().strip()==b"token"
    save()
    assert d["integration"]["result"]==["PASSED"] and {"fallback_runfiles_manifest","symlinked_stub_finds_runfiles","runfiles_source_precedence"}<=set(d["integration"]["cases"])
    assert not d["default"]["stub_labels"] and not d["default"]["stub_repositories"]
    assert all(d[k]["exit"]==0 and d[k]["token"] for k in ("direct","foreign","absolute_link","relative_link","distroless"))
save()
try:
    verify()
finally:
    save()
