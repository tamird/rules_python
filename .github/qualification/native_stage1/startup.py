import glob
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import subprocess
import time

CASE = Path(__file__).resolve().parent
SOURCE = {"script": "943362ffab1a2e8488f50b8554256f9330d3837e", "prototype": "804470ef813ec3a25df2432d374f84bac6ec2d76",
          "native": "c3ab160ae7c57e79e1847bb18fc2940e49295039"}
KEY = "RULES_PYTHON_ADDITIONAL_INTERPRETER_ARGS"
ROW = {"sources": SOURCE, "cases": {}}
ENV = os.environ.copy()
ENV.pop(KEY, None)
REPORT = CASE/"result.json"

def write():
    REPORT.write_text(json.dumps(ROW, indent=2)+"\n")

def copy_tree(source, target):
    def copy(src, dst):
        if src.is_symlink():
            link = Path(os.path.normpath(src.parent/os.readlink(src)))
            if link.is_relative_to(source):
                dst.symlink_to(os.path.relpath(target/link.relative_to(source),dst.parent))
                return
        if src.is_dir():
            dst.mkdir()
            for child in src.iterdir(): copy(child,dst/child.name)
        else:
            shutil.copy2(src,dst)
    copy(source,target)

def prep(mode, source):
    subprocess.run(["git","checkout","--detach",source],check=True)
    assert subprocess.check_output(["git","rev-parse","HEAD"],text=True).strip()==source
    dest=Path("tests/native_stage1")
    dest.mkdir(exist_ok=True)
    for src, out in [("startup.BUILD","BUILD.bazel"),("hello.py","hello.py"),("show.py","show.py"),("python.BUILD","python.BUILD")]:
        shutil.copy2(CASE/src,dest/out)
    with Path("MODULE.bazel").open("a") as f: f.write((CASE/"runtime.MODULE").read_text())
    flags=["--ignore_dev_dependency","--extra_toolchains=//tests/native_stage1:runtime","--//python/config_settings:bootstrap_impl="+("script" if mode=="script" else "native")]
    subprocess.run([CASE/"bazelisk","--ignore_all_rc_files","build",*flags,"//tests/native_stage1:hello","//tests/native_stage1:show"],check=True)
    staging=CASE/mode
    staging.mkdir()
    for name in ("hello","show"):
        p=Path("bazel-bin/tests/native_stage1",name).absolute()
        shutil.copy2(p,staging/name)
        copy_tree((p.parent.resolve()/(name+".runfiles")).resolve(), staging/(name+".runfiles"))
        data=(staging/name).read_bytes()
        assert data.startswith(b"#!/usr/bin/env bash" if mode=="script" else b"\x7fELF")
    binaries=list((staging/"hello.runfiles").glob("*stage1_cpython/bin/python3.11"))
    assert len(binaries)==1
    runtime=hashlib.sha256(binaries[0].read_bytes()).hexdigest()
    assert runtime=="02939080d36c0fd2dc2507ff146a4c86360fa3968f30e1e42a6424d352629394", runtime
    ROW["interpreter_sha256"]=runtime
    subprocess.run(["git","restore","MODULE.bazel"],check=True)
    return staging

def run_option(binary, value):
    env=ENV.copy()
    if value is not None: env[KEY]=value
    p=subprocess.run([binary,"an argument"],env=env,cwd=CASE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    return {"exit":p.returncode,"observed":json.loads(p.stdout) if p.returncode==0 else None}

def launch(path):
    p=subprocess.run([path],env=ENV,cwd=CASE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL)
    if p.returncode or p.stdout!=b"ready\n":raise RuntimeError("bad benchmark process")

def main():
    write()
    debugger=CASE/"debugger with space.py"
    debugger.write_text('import json,sys\nprint(json.dumps(["debugger",sys.argv[1],sys.argv[2],sys.argv[-1]]))\n')
    cases={"unset":None,"empty":"","escaped_space":r"-Xtag=two\ words", "line":"-Xone=1\n-Xtwo=2",
           "continuation":"-Xtag=two\\\nparts", "literal_quotes":'-Xtag="literal"', "order":"-Xdupe=env",
           "debugger_path":str(debugger).replace(" ",r"\ ")+" --port 8123", "trailing_escape":"-Xfoo"+"\\",
           "vertical_tab":"-Xtag=one\vtwo"}
    apps={}
    for mode,source in SOURCE.items():
        stage=prep(mode,source)
        apps[mode]=stage/"hello"
        ROW["cases"][mode]={k:run_option(stage/"show",v) for k,v in cases.items()}
        write()
    baseline=ROW["cases"]["script"]
    assert baseline["empty"]["observed"][-1]==""
    assert baseline["debugger_path"]=={"exit":0,"observed":["debugger","--port","8123","an argument"]}
    assert baseline["order"]["observed"][0]["dupe"]=="target"
    ROW["prior_mismatches"]=[k for k in cases if baseline[k]!=ROW["cases"]["prototype"][k]]
    ROW["corrected_mismatches"]=[k for k in cases if baseline[k]!=ROW["cases"]["native"][k]]
    write()
    assert {"empty","escaped_space","debugger_path","line"} <= set(ROW["prior_mismatches"])
    assert not ROW["corrected_mismatches"]
    cpu=min(os.sched_getaffinity(0))
    os.sched_setaffinity(0,{cpu})
    modes=["script","native"]
    for _ in range(5):
        for mode in modes: launch(apps[mode])
    order=[0,1]*10
    random.Random(1741).shuffle(order)
    rows={m:[] for m in modes}
    batch=25
    for first in order:
        for i in (first,1-first):
            mode=modes[i]
            start=time.perf_counter_ns()
            for _ in range(batch): launch(apps[mode])
            rows[mode].append((time.perf_counter_ns()-start)/batch)
    for mode,file in (("script","stock.txt"),("native","native.txt")):
        (CASE/file).write_text("goos: linux\ngoarch: amd64\n\n"+"".join(
            f"BenchmarkPythonStartup-1 {batch} {n:.0f} ns/op\n" for n in rows[mode]))
    ROW.update(pairs=len(order),batch_size=batch,cpu=cpu,first_mode=order)
    write()

main()
