"""Build the v3 corpus from lesser-known libraries, so nothing can be diffed against memory or against a copy on this machine.

    python make_corpus_v3.py OUT_DIR SDIST_DIR lib1 lib2 ...        (each lib is the sdist's folder name, e.g. funcy-2.1; SDIST_DIR holds the .tar.gz files)

What it does, and why
- Each sdist is extracted into its OWN new folder under a temporary workspace; this script lives elsewhere and is never run from inside them.
- A library's tests must pass on the pristine code. Then single-token mutations (see make_corpus.candidates) are tried and one is kept only if the library's own tests FAIL
  with it, so every injected bug is real and checkable. Bugs per file scale with its size.
- The tests run with the network blocked (macOS sandbox-exec) and a stripped environment.
- OUT_DIR/corpus holds only the mutated files (one folder per library), in a fresh git repository with no remote and no history of the originals.
- The answer key is written encrypted (OUT_DIR/manifest.enc); the key goes into the macOS keychain (item gain-key) and is printed once. It is not stored beside the corpus.
- The workspace, which holds every pristine copy, is deleted at the end."""
import ast
import json
import queue
import random
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
from pathlib import Path

import gainlib
from make_corpus import candidates

SEED = 20261119
WORKERS = 4
SANDBOX = ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)"] if shutil.which("sandbox-exec") else []


def code_lines(src):
    tree, doc = ast.parse(src), 0
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and ast.get_docstring(n, clean=False) is not None:
            d = n.body[0]
            doc += d.end_lineno - d.lineno + 1
    return len(src.split("\n")) - doc


def run_tests(root, test_target, timeout=240):
    r = subprocess.run(SANDBOX + [sys.executable, "-m", "pytest", test_target, "-x", "-q", "-p", "no:cacheprovider"], cwd=str(root), capture_output=True, text=True, timeout=timeout,
                       env={"PYTHONPATH": str(root), "PATH": "/usr/bin:/bin", "HOME": str(root), "PYTHONDONTWRITEBYTECODE": "1"})
    return r.returncode, r.stdout


def main():
    out_dir, sdist_dir, libs = Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), sys.argv[3:]
    work = Path(tempfile.mkdtemp(prefix="gain-v3-work-"))
    shutil.rmtree(out_dir, ignore_errors=True)
    roots = {}
    for lib in libs:
        dest = work / "libs" / lib          # its own new, empty folder
        dest.mkdir(parents=True)
        with tarfile.open(sdist_dir / f"{lib}.tar.gz") as t:
            t.extractall(dest, filter="data")
        roots[lib] = dest / lib
    modules = {}                              # (lib, relative path) -> test target
    for lib, root in roots.items():
        tests = [p for p in (root / "tests", root / "test_schema.py", root / "test_parse.py", root / "test") if p.exists()]
        if not tests:
            print(f"{lib}: no tests found, skipped")
            continue
        target = str(tests[0].relative_to(root))
        code, out = run_tests(root, target)
        if code != 0:
            print(f"{lib}: its tests do not pass unmodified here, skipped\n{out[-400:]}")
            continue
        for p in sorted(root.rglob("*.py")):
            rel = p.relative_to(root)
            if any(x in rel.parts for x in ("tests", "test", "docs", "doc", "benchmarks", "examples")) or rel.name in ("setup.py", "conftest.py") or rel.name.startswith("test_"):
                continue
            if code_lines(p.read_text()) >= 60:
                modules[(lib, str(rel))] = target
    print(f"{len(modules)} modules in {len(roots)} libraries", flush=True)

    pool = queue.Queue()
    for w in range(WORKERS):
        copies = {}
        for lib, root in roots.items():
            c = work / f"w{w}" / lib
            shutil.copytree(root, c)
            copies[lib] = c
        pool.put(copies)
    chosen, lock = {}, threading.Lock()

    def killed(lib, rel, source, target):
        copies = pool.get()
        try:
            f = copies[lib] / rel
            original = f.read_text()
            f.write_text(source)
            try:
                return run_tests(copies[lib], target)[0] == 1
            except subprocess.TimeoutExpired:
                return False
            finally:
                f.write_text(original)
        finally:
            pool.put(copies)

    def work_on(key):
        lib, rel = key
        src = (roots[lib] / rel).read_text()
        want = max(1, min(4, round(code_lines(src) / 150)))
        cands = candidates(src)
        random.Random(f"{SEED}:{lib}:{rel}").shuffle(cands)
        picked, tried = [], 0
        for c in cands:
            if len(picked) >= want or tried >= 60:
                break
            if any(p["func"] == c["func"] for p in picked):
                continue
            tried += 1
            if killed(lib, rel, c["source"], modules[key]):
                picked.append(c)
        with lock:
            chosen[key] = picked
        print(f"{lib}/{rel}: {len(picked)}/{want} kept ({tried} tried)", flush=True)

    threads = [threading.Thread(target=work_on, args=(k,)) for k in modules]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    corpus = out_dir / "corpus"
    manifest, n = [], 0
    for (lib, rel), picks in sorted(chosen.items()):
        if not picks:
            continue
        lines = (roots[lib] / rel).read_text().split("\n")
        for c in picks:
            lines[c["line"] - 1] = c["source"].split("\n")[c["line"] - 1]
        dest = corpus / lib.rsplit("-", 1)[0] / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines))
        ast.parse(dest.read_text())
        for c in sorted(picks, key=lambda c: c["line"]):
            n += 1
            manifest.append({"id": f"M{n:02d}", "file": str(Path(lib.rsplit("-", 1)[0]) / rel), "function": c["func"], "line": c["line"], "kind": c["kind"],
                             "original": c["old_line"], "mutated": c["new_line"]})
    key = gainlib.new_key()
    gainlib.save_manifest(out_dir, {"libraries": libs, "seed": SEED, "bugs": manifest}, key)
    subprocess.run(["security", "add-generic-password", "-U", "-s", "gain-key", "-a", "gain", "-w", key], check=False, capture_output=True)
    subprocess.run("git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -q -m 'code under review'", shell=True, cwd=corpus, check=True)
    shutil.rmtree(work, ignore_errors=True)    # every pristine copy goes
    print(f"\n{len(manifest)} bugs in {len(set(m['file'] for m in manifest))} files -> {out_dir}  (answer key encrypted; key stored in the keychain as gain-key)")


if __name__ == "__main__":
    main()
