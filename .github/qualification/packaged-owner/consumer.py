"""Compare the installed child across released and source-built launchers."""
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from urllib.parse import unquote, urlsplit
import xml.etree.ElementTree as ET

CONSUMER = Path.cwd()
WORK = Path(os.environ["RUNNER_TEMP"]) / "packaged-owner"
SUPPLIER = WORK / "supplier"
SOURCE = "cb36641e50cd8e858f42aa4e04c50b8532138763"
BASELINE = "5bf01e6babef432b729a72ce7b022fe29c908101"
FIXTURE = "//tests/packaged_owner_control:source_owner_test"
CHILD = "//tests/packaged_owner_control:child"
BINARIES = {
    "finalizer": ("//launcher/private/source_built:finalizer_file_x86_64_linux_source_built", "finalize_stub_x86_64_linux"),
    "template": ("//runfiles-stub:runfiles-stub_x86_64-unknown-linux-musl", "runfiles_stub_x86_64_linux"),
}
BAZEL = [str(WORK / "bazelisk"), "--nosystem_rc", "--nohome_rc"]
NATIVE = ["--@rules_python//python/config_settings:bootstrap_impl=native",
          "--@rules_python//python/config_settings:build_python_zip=false"]
LOCAL = ["--jobs=4", "--remote_executor=", "--remote_cache=", "--bes_backend=", "--disk_cache=",
         "--color=no","--curses=no","--noshow_progress","--lockfile_mode=off"]
RECEIPT = {"source": SOURCE, "baseline": BASELINE, "runs": {}, "verified": False}


def save():
    (WORK / "consumer.json").write_text(json.dumps(RECEIPT, indent=2) + "\n")


def output(cwd, *argv):
    return subprocess.check_output(argv, cwd=cwd, text=True).strip()


def digest(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def bazel(cwd, phase, *args, content=False, test_run=False):
    RECEIPT["phase"] = phase
    save()
    stdout = WORK / (phase+".stdout")
    stderr = WORK / (phase+".stderr")
    with stdout.open("wb") as out, stderr.open("wb") as err:
        code = subprocess.run([*BAZEL,*args],cwd=cwd,stdout=out,stderr=err,timeout=3600).returncode
    if code:
        # Diagnostics originate in a public source-only job with no credentials.
        lines = stderr.read_text(errors="replace").splitlines()
        RECEIPT.setdefault("failures", {})[phase] = {"phase":phase,"exit":code,"stderr_sha256":digest(stderr),
                              "diagnostics":[line[:420] for line in lines if line.startswith("ERROR:")][:4]}
        save()
        if not test_run:
            raise RuntimeError(f"{phase} exited {code}")
    return stdout.read_text() if content else code


def sources():
    SUPPLIER.mkdir()
    subprocess.run(["git","init","--quiet"],cwd=SUPPLIER,check=True)
    subprocess.run(["git","fetch","--quiet","--depth=1","https://github.com/tamird/hermetic-launcher.git",SOURCE],
                   cwd=SUPPLIER,check=True)
    subprocess.run(["git","checkout","--quiet","--detach","FETCH_HEAD"],cwd=SUPPLIER,check=True)
    assert output(SUPPLIER,"git","rev-parse","HEAD")==SOURCE
    assert output(SUPPLIER,"git","cat-file","-p",SOURCE).splitlines()[1] == "parent b0d103f65a4d2a14c6407d2ffb7208aa36cd745f"
    observe("supplier_integration", integration_test)
    labels = [v[0] for v in BINARIES.values()]
    bazel(SUPPLIER,"build-supplier","build","--config=release",*LOCAL,*labels)
    rows = RECEIPT["source_tools"] = {}
    module = []
    for kind,(label,alias) in BINARIES.items():
        paths = bazel(SUPPLIER,"supplier-"+kind,"cquery","--config=release",*LOCAL,"--output=files",label,content=True).splitlines()
        path, = (SUPPLIER / s for s in paths if s)
        assert path.is_file() and os.access(path,os.X_OK) and path.stat().st_size < 32<<20
        with path.open("rb") as f:
            header = f.read(20)
        assert header[:6]==b"\x7fELF\x02\x01" and int.from_bytes(header[18:20],"little")==62
        repo = WORK / ("input_"+kind)
        directory=repo/"file"
        directory.mkdir(parents=True)
        (repo/"WORKSPACE").write_text("")
        name = kind+".bin"
        staged=directory/name
        shutil.copy2(path,staged)
        (directory/"BUILD.bazel").write_text(
            'filegroup(name = "file", srcs = ["'+name+'"], visibility = ["//visibility:public"])\n')
        rows[kind] = {"label":label,"output":paths[0],"sha256":digest(path),"size":path.stat().st_size}
        assert digest(staged) == rows[kind]["sha256"]
        module.append('consumer_repo(name="input_'+kind+'",path='+json.dumps(str(repo))+')')
    return ('\nconsumer_repo = use_repo_rule("@bazel_tools//tools/build_defs/repo:local.bzl", "local_repository")\n'
            +'\n'.join(module)+'\nconsumer_stubs = use_extension("@hermetic_launcher//launcher/private:extensions.bzl",'
            +'"non_module_dependencies")\noverride_repo(consumer_stubs, finalize_stub_x86_64_linux="input_finalizer",'
            +' runfiles_stub_x86_64_linux="input_template")\n')


def member(item, name, root, label):
    url=urlsplit(item["uri"])
    assert url.scheme=="file" and not url.netloc
    path=Path(unquote(url.path)).resolve(strict=True)
    expect=root/"bazel-testlogs"/label[2:].replace(":", "/")/name
    assert path==expect.resolve(strict=True) and path.stat().st_size<1<<20
    h=digest(path)
    assert not item.get("digest") or item["digest"]==h
    assert "length" not in item or int(item["length"])==path.stat().st_size
    return path,h


def integration_test():
    label="//integration-tests:integration_test"
    bep=WORK/"supplier-test.bep"
    rc=bazel(SUPPLIER,"supplier-test","test","--config=release",*LOCAL,"--strategy=TestRunner=standalone",
          "--nocache_test_results","--flaky_test_attempts=1","--build_event_json_file="+str(bep),label,test_run=True)
    events=[json.loads(line) for line in bep.read_text().splitlines()]
    result,=(e["testResult"] for e in events if e.get("id",{}).get("testResult",{}).get("label")==label)
    files={i["name"]:member(i,i["name"],SUPPLIER,label) for i in result["testActionOutput"] if i["name"] in ("test.log","test.xml")}
    log=files["test.log"][0].read_text()
    xml=ET.parse(files["test.xml"][0])
    runner=SUPPLIER/"integration-tests/src/main.rs"
    source=runner.read_text()
    registry=source.split("let tests: Vec<",1)[1].split("];",1)[0]
    names=re.findall(r'\(\s*"([a-z0-9_]+)"\s*,\s*test_[a-z0-9_]+\s*,?\s*\)',registry)
    assert len(names)==len(set(names)) and {"required_runfile","runfiles_source_precedence"} <= set(names)
    info=result.get("executionInfo",{})
    row=RECEIPT["supplier_integration"]={"exit":rc,"label":label,"config":"release","registered":names,
            "registered_source_sha256":digest(runner),"status":result["status"],"strategy":info.get("strategy"),
            "cached":bool(result.get("cachedLocally")),"remote_cached":bool(info.get("cachedRemotely")),
            "original":{name:sha for name,(_,sha) in files.items()},
            "announced":re.findall(r"(?m)^Running (\d+) tests\.\.\.$",log),
            "passed":re.findall(r"(?m)^Passed: (\d+)$",log),"failed":re.findall(r"(?m)^Failed: (\d+)$",log),
            "named":{name:log.splitlines().count("  Running test: "+name)
                     for name in ("required_runfile","runfiles_source_precedence")}}
    save()
    assert rc==0 and row["status"]=="PASSED" and row["strategy"] in ("local","standalone") and not row["cached"] and not row["remote_cached"]
    assert row["announced"]==row["passed"]==[str(len(names))] and row["failed"]==["0"] and set(row["named"].values())=={1}
    assert "All tests passed!" in log and not any(el.tag in ("failure","error","skipped") for el in xml.iter())


def arm(key, overrides):
    bep=WORK/(key+".bep")
    row=RECEIPT["runs"][key]={"verified":False}
    common=[*LOCAL,*NATIVE,*overrides,"--output_groups=default","--nocache_test_results","--flaky_test_attempts=1",
            "--test_arg=native","--test_env=PACKAGED_OWNER_ARM="+key]
    rc=bazel(CONSUMER,key,"test",*common,"--strategy=TestRunner=standalone",
          "--build_event_publish_all_actions","--build_event_json_file="+str(bep),"--test_output=errors",FIXTURE,test_run=True)
    events=[json.loads(s) for s in bep.read_text().splitlines()]
    result,=(e["testResult"] for e in events if e.get("id",{}).get("testResult",{}).get("label")==FIXTURE)
    info=result.get("executionInfo",{})
    row.update(exit=rc,status=result["status"],strategy=info.get("strategy"),cached=bool(result.get("cachedLocally")),
               remote_cached=bool(info.get("cachedRemotely")))
    files={item["name"]:member(item,item["name"],CONSUMER,FIXTURE) for item in result["testActionOutput"] if item["name"] in ("test.log","test.xml")}
    row["test_outputs"]={n:h for n,(_,h) in files.items()}
    xml=ET.parse(files["test.xml"][0])
    log=files["test.log"][0].read_text()
    marker,=re.findall(r"(?m)^OWNER_RESULT (\{.*\})$",log)
    rec=json.loads(marker)
    assert rec["mode"]=="native" and rec["arm"]==key and rec["inputs"]["child_header"]=="7f454c46"
    row["cases"]={n:{k:v for k,v in rec[n].items() if k!="error"} for n in ("direct","declared_parent","independent_parent")}
    row.update(inputs=rec["inputs"],hashes=rec["hashes"])
    save()
    assert rc==0 and row["status"]=="PASSED" and row["strategy"] in ("local","standalone") and not row["cached"] and not row["remote_cached"]
    assert not any(t.tag in ("skipped","error","failure") for t in xml.iter())
    graph=json.loads(bazel(CONSUMER,key+"-action","aquery",*common,"--output=jsonproto",CHILD,content=True))
    action,=(a for a in graph["actions"] if "--template" in a.get("arguments",[]) and "-o" in a["arguments"])
    assert next(t["label"] for t in graph["targets"] if t["id"]==action["targetId"])==CHILD
    argv=action["arguments"]
    config=next(c["checksum"] for c in graph["configuration"] if c["id"]==action["configurationId"])
    path=argv[argv.index("-o")+1]
    completed,=(e["action"] for e in events if e.get("id",{}).get("actionCompleted",{}).get("label","").lstrip("@")==CHILD
                and e["id"]["actionCompleted"].get("primaryOutput")==path
                and e["id"]["actionCompleted"]["configuration"]["id"]==config)
    assert completed["success"] and completed["type"]==action["mnemonic"]
    execroot=Path(bazel(CONSUMER,key+"-execroot","info",*overrides,"execution_root",content=True).strip())
    verified={k:digest(execroot/v) for k,v in (("finalizer",argv[0]),("template",argv[argv.index("--template")+1]),("child",path))}
    assert verified["child"]==row["hashes"]["launchers"]["child"]
    row["action"]={"config":config,"action_key":action["actionKey"],"output":path,"tools_and_child":verified}
    if key!="stock":
        assert all(verified[k]==RECEIPT["source_tools"][k]["sha256"] for k in BINARIES)
    expected=row["inputs"]["stage2_key"]
    if key=="fixed":
        assert argv.count("--required-runfile")==1 and argv[argv.index("--required-runfile")+1]==expected
        row["action"]["required_runfile"]=expected
    else:
        assert "--required-runfile" not in argv
    row["verified"]=True
    save()


def observe(name, fn, *args):
    try:
        fn(*args)
    except (AssertionError, KeyError, ValueError, OSError, RuntimeError, subprocess.SubprocessError) as err:
        import traceback
        frame=traceback.extract_tb(err.__traceback__)[-1]
        RECEIPT.setdefault("incomplete", {})[name]={"kind":type(err).__name__,"line":frame.lineno}
        save()


def main():
    assert platform.machine()=="x86_64" and os.getuid()!=0
    assert output(CONSUMER,"git","rev-parse","HEAD^")==BASELINE
    module=CONSUMER/"MODULE.bazel"
    bootstrap=CONSUMER/"python/private/py_executable.bzl"
    original_module=module.read_bytes()
    original_bootstrap=bootstrap.read_bytes()
    try:
        observe("stock",arm,"stock",[])
        declarations=sources()
        module.write_bytes(original_module+declarations.encode())
        overrides=["--override_module=hermetic_launcher="+str(SUPPLIER)]
        observe("unset",arm,"unset",overrides)
        old=b"bootstrap.compile(ctx, output_file = executable)"
        assert original_bootstrap.count(old)==1
        bootstrap.write_bytes(original_bootstrap.replace(old,b"bootstrap.compile(ctx, output_file = executable, required_runfile = stage2_bootstrap)"))
        observe("fixed",arm,"fixed",overrides)
        assert not RECEIPT.get("incomplete")
        rows=RECEIPT["runs"]
        assert len({v["hashes"]["stage2"] for v in rows.values()})==1
        assert len({v["hashes"]["main"] for v in rows.values()})==1
        assert rows["unset"]["hashes"]["launchers"]["child"]!=rows["fixed"]["hashes"]["launchers"]["child"]
        RECEIPT["verified"]=True
    finally:
        module.write_bytes(original_module)
        bootstrap.write_bytes(original_bootstrap)
        save()


if __name__=="__main__":
    main()
