"""Build the review-gain corpus: real networkx modules with subtle bugs injected, every one confirmed by the library's own tests.

    python make_corpus.py OUT_DIR [mutants-per-file (default 2)] [--auto N]

With --auto N, the modules are not the fixed twelve: N are drawn (seeded) from every networkx algorithms module that has at least 120 code lines and a
tests/test_<name>.py next to it whose tests all pass unmodified. That is the large version, built to exceed what one agent can read in one turn.

For each of 12 modules, many single-token mutations are tried (a comparison flipped, + and - swapped, and/or swapped, a `not` dropped, a small constant
changed, break turned into continue). A mutation is kept only if the module's own test file FAILS with it, so every kept bug is a real behaviour change
that a reader could find, not an equivalent edit. Two per module, in different functions. OUT_DIR gets `corpus/` (the mutated modules at their
networkx paths, as a git repo) and `manifest.json` (id, file, function, line, original line, mutated line, kind). The seed is fixed: the same networkx
version gives the same corpus."""
import ast
import io
import json
import keyword
import queue
import random
import shutil
import subprocess
import sys
import threading
import tokenize
from pathlib import Path

import networkx

NX = Path(networkx.__file__).parent
SEED = 20261116
WORKERS = 6
MODULES = {
    "algorithms/shortest_paths/unweighted.py": "algorithms/shortest_paths/tests/test_unweighted.py",
    "algorithms/shortest_paths/generic.py": "algorithms/shortest_paths/tests/test_generic.py",
    "algorithms/components/connected.py": "algorithms/components/tests/test_connected.py",
    "algorithms/components/strongly_connected.py": "algorithms/components/tests/test_strongly_connected.py",
    "algorithms/components/biconnected.py": "algorithms/components/tests/test_biconnected.py",
    "algorithms/components/weakly_connected.py": "algorithms/components/tests/test_weakly_connected.py",
    "algorithms/traversal/breadth_first_search.py": "algorithms/traversal/tests/test_bfs.py",
    "algorithms/traversal/depth_first_search.py": "algorithms/traversal/tests/test_dfs.py",
    "algorithms/bridges.py": "algorithms/tests/test_bridges.py",
    "algorithms/cuts.py": "algorithms/tests/test_cuts.py",
    "algorithms/dag.py": "algorithms/tests/test_dag.py",
    "algorithms/tree/mst.py": "algorithms/tree/tests/test_mst.py",
}
FLIP = {"<": "<=", "<=": "<", ">": ">=", ">=": ">", "==": "!=", "!=": "=="}


def function_lines(tree):
    """line -> qualified name of the innermost function whose BODY (docstring excluded) contains it."""
    spans = []

    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = f"{prefix}{child.name}"
                body = child.body
                if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) and isinstance(body[0].value.value, str):
                    body = body[1:]
                if body:
                    spans.append((body[0].lineno, child.end_lineno, name))
                walk(child, name + ".")
            elif isinstance(child, ast.ClassDef):
                walk(child, f"{prefix}{child.name}.")
            else:
                walk(child, prefix)

    walk(tree, "")
    out = {}
    for start, end, name in sorted(spans, key=lambda s: (s[1] - s[0]), reverse=True):  # outer first, inner overwrites
        for ln in range(start, end + 1):
            out[ln] = name
    return out


def candidates(src):
    tree = ast.parse(src)
    owner = function_lines(tree)
    toks = [t for t in tokenize.generate_tokens(io.StringIO(src).readline) if t.type not in (tokenize.NL, tokenize.COMMENT, tokenize.NEWLINE, tokenize.INDENT, tokenize.DEDENT)]
    lines = src.split("\n")
    out = []
    for i, t in enumerate(toks):
        row, col = t.start
        if row not in owner or t.start[0] != t.end[0]:
            continue
        prev = toks[i - 1] if i else None
        nxt = toks[i + 1] if i + 1 < len(toks) else None
        new, kind = None, None
        if t.type == tokenize.OP and t.string in FLIP:
            new, kind = FLIP[t.string], "comparison"
        elif t.type == tokenize.OP and t.string in "+-" and prev is not None and (
                prev.type == tokenize.NUMBER or prev.string in (")", "]") or (prev.type == tokenize.NAME and not keyword.iskeyword(prev.string))):
            new, kind = ("-" if t.string == "+" else "+"), "arithmetic"
        elif t.type == tokenize.NAME and t.string in ("and", "or"):
            new, kind = ("or" if t.string == "and" else "and"), "boolean"
        elif t.type == tokenize.NAME and t.string == "not" and not (prev is not None and prev.string == "is") and not (nxt is not None and nxt.string == "in"):
            new, kind = "", "negation"
        elif t.type == tokenize.NAME and t.string == "break":
            new, kind = "continue", "loop"
        elif t.type == tokenize.NUMBER and t.string in ("0", "1", "2"):
            new, kind = {"0": "1", "1": "0", "2": "1"}[t.string], "constant"
        if new is None:
            continue
        line = lines[row - 1]
        end = t.end[1] + (1 if kind == "negation" and line[t.end[1]:t.end[1] + 1] == " " else 0)  # drop the space after a removed `not`
        mutated_line = line[:col] + new + line[end:]
        mutated = "\n".join(lines[:row - 1] + [mutated_line] + lines[row:])
        try:
            ast.parse(mutated)
        except SyntaxError:
            continue
        out.append({"line": row, "func": owner[row], "kind": kind, "old_line": line.strip(), "new_line": mutated_line.strip(), "source": mutated})
    return out


def eligible_modules():
    """Every algorithms module with at least 120 code lines (docstrings not counted) and a test file named after it, sorted."""
    out = {}
    for p in sorted((NX / "algorithms").rglob("*.py")):
        if "tests" in p.parts or p.name == "__init__.py":
            continue
        t = p.parent / "tests" / f"test_{p.stem}.py"
        if not t.exists():
            continue
        src = p.read_text()
        tree = ast.parse(src)
        doc = 0
        for n in ast.walk(tree):
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) and ast.get_docstring(n, clean=False) is not None:
                d = n.body[0]
                doc += d.end_lineno - d.lineno + 1
        if len(src.split("\n")) - doc >= 120:
            out[str(p.relative_to(NX))] = str(t.relative_to(NX))
    return out


def pristine_passes(path, test):
    r = subprocess.run([sys.executable, "-m", "pytest", str(NX / test), "-q", "-p", "no:cacheprovider"], cwd="/tmp", capture_output=True, text=True, timeout=300,
                       env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
    return r.returncode == 0 and " passed" in r.stdout


def main():
    global MODULES
    args = [a for a in sys.argv[1:]]
    auto = None
    if "--auto" in args:
        i = args.index("--auto")
        auto = int(args[i + 1])
        del args[i:i + 2]
    out_dir = Path(args[0]).resolve()
    per_file = int(args[1]) if len(args) > 1 else 2
    if auto:
        pool_all = eligible_modules()
        names = sorted(pool_all)
        random.Random(SEED).shuffle(names)
        MODULES = {}
        for name in names:
            if len(MODULES) >= auto:
                break
            if pristine_passes(name, pool_all[name]):
                MODULES[name] = pool_all[name]
        print(f"{len(MODULES)} modules chosen from {len(names)} eligible", flush=True)
    work = out_dir / "work"
    shutil.rmtree(out_dir, ignore_errors=True)
    pool = queue.Queue()
    for w in range(WORKERS):
        d = work / f"w{w}"
        d.mkdir(parents=True)
        shutil.copytree(NX, d / "networkx", ignore=shutil.ignore_patterns("__pycache__"))
        pool.put(d)
    rng = random.Random(SEED)
    chosen, lock = {}, threading.Lock()

    def killed(path, source, test):
        d = pool.get()
        try:
            target = d / "networkx" / path
            original = target.read_text()
            target.write_text(source)
            try:
                r = subprocess.run([sys.executable, "-m", "pytest", str(d / "networkx" / test), "-x", "-q", "-p", "no:cacheprovider"], cwd=str(d), capture_output=True,
                                   text=True, timeout=120, env={"PYTHONPATH": str(d), "PATH": "/usr/bin:/bin", "HOME": str(d)})
                return r.returncode == 1
            except subprocess.TimeoutExpired:
                return False
            finally:
                target.write_text(original)
        finally:
            pool.put(d)

    def work_on(path):
        src = (NX / path).read_text()
        cands = candidates(src)
        rng_local = random.Random(f"{SEED}:{path}")
        rng_local.shuffle(cands)
        picked, tried = [], 0
        for c in cands:
            if len(picked) >= per_file or tried >= 80:
                break
            if any(p["func"] == c["func"] for p in picked):
                continue
            if picked and any(p["kind"] == c["kind"] for p in picked) and tried < 40:
                continue  # prefer a different kind of bug for the second one
            tried += 1
            if killed(path, c["source"], MODULES[path]):
                picked.append(c)
        with lock:
            chosen[path] = picked
        print(f"{path}: {len(picked)} kept of {tried} tried ({len(cands)} candidates)", flush=True)

    threads = [threading.Thread(target=work_on, args=(p,)) for p in MODULES]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    shutil.rmtree(work, ignore_errors=True)

    corpus = out_dir / "corpus"
    manifest = []
    for path in MODULES:
        src = (NX / path).read_text()
        lines = src.split("\n")
        for c in chosen.get(path, []):
            lines[c["line"] - 1] = c["source"].split("\n")[c["line"] - 1]
        dest = corpus / "networkx" / path
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text("\n".join(lines))
        ast.parse(dest.read_text())
    n = 0
    for path in MODULES:
        for c in sorted(chosen.get(path, []), key=lambda c: c["line"]):
            n += 1
            manifest.append({"id": f"M{n:02d}", "file": f"networkx/{path}", "function": c["func"], "line": c["line"], "kind": c["kind"],
                             "original": c["old_line"], "mutated": c["new_line"]})
    (out_dir / "manifest.json").write_text(json.dumps({"networkx": networkx.__version__, "seed": SEED, "bugs": manifest}, indent=1))
    subprocess.run("git init -q -b main && git add -A && git -c user.name=t -c user.email=t@t commit -q -m 'networkx modules under review'", shell=True, cwd=corpus, check=True)
    print(f"\n{len(manifest)} bugs in {len(chosen)} files -> {out_dir}")
    for b in manifest:
        print(f"  {b['id']} {b['file'].split('algorithms/')[-1]}:{b['line']} {b['function']} [{b['kind']}]   {b['original']}   ->   {b['mutated']}")


if __name__ == "__main__":
    main()
