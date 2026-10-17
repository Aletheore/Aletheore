import subprocess
from pathlib import Path

from aletheore.code_locator import extract_identifiers, locate, render_locate


def make_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    return repo


def modules_for(files: dict[str, str]) -> list[dict]:
    mods = []
    for rel, text in files.items():
        lines = text.split("\n")
        funcs, classes = [], []
        for i, line in enumerate(lines, start=1):
            stripped = line.lstrip()
            if stripped.startswith("def "):
                funcs.append({"name": stripped[4:].split("(")[0], "start_line": i, "end_line": _end(lines, i)})
            elif stripped.startswith("class "):
                classes.append({"name": stripped[6:].split("(")[0].split(":")[0], "start_line": i, "end_line": _end(lines, i)})
        mods.append({"path": rel, "language": "python", "imports": [], "imported_by": [], "symbols": {"functions": funcs, "classes": classes}})
    return mods


def _end(lines, start):
    indent = len(lines[start - 1]) - len(lines[start - 1].lstrip())
    end = start
    for j in range(start, len(lines)):
        if not lines[j].strip():
            continue
        if len(lines[j]) - len(lines[j].lstrip()) <= indent:
            break
        end = j + 1
    return end


FILES = {
    "src/fmt.py": (
        "class HelpFormatter:\n"
        "    def write_usage(self, prog, args=''):\n"
        "        prefix = 'Usage: '\n"
        "        return prefix + prog + args\n"
        "\n"
        "    def write_text(self, text):\n"
        "        return text\n"
    ),
    "src/core.py": "def invoke(ctx):\n    return ctx\n\ndef parse_args(args):\n    return args\n",
    "src/utils.py": "def echo(msg):\n    print(msg)\n",
    "tests/test_fmt.py": "def test_write_usage():\n    HelpFormatter().write_usage('x')\n",
}


def test_all_words_turns_plain_query_words_into_identifiers():
    ids, _ = extract_identifiers("write_usage formatter prefix", all_words=True)
    assert {"write_usage", "formatter", "prefix"} <= set(ids)
    plain, _ = extract_identifiers("write_usage formatter prefix")
    assert set(plain) == {"write_usage"}


def test_backticked_identifier_weighs_exactly_two_even_when_it_also_looks_codey():
    # Real audit finding (Flash Review): the docstring promises a backticked
    # identifier weighs 2 per use, but the plain-word pass ran over the
    # whole query including backtick characters, so an identifier that also
    # qualified as "codey" (has an underscore, here) matched a second time
    # for an effective weight of 3.
    ids, _ = extract_identifiers("look at `write_usage` please")
    assert ids["write_usage"] == 2


def test_locate_finds_the_file_that_defines_the_named_symbol(tmp_path):
    repo = make_repo(tmp_path, FILES)
    cands = locate("HelpFormatter write_usage", repo, modules_for(FILES), all_words=True)
    assert cands and cands[0].path == "src/fmt.py"
    assert all("test" not in c.path for c in cands)


def test_locate_returns_nothing_for_a_query_that_matches_nothing(tmp_path):
    repo = make_repo(tmp_path, FILES)
    assert locate("nonexistent_function_name", repo, modules_for(FILES), all_words=True) == []


def test_locate_keeps_partial_results_when_the_deadline_expires_mid_loop(tmp_path, monkeypatch):
    # Real audit finding (Flash Review): hitting the deadline mid-loop used
    # to discard every score already accumulated from earlier identifiers
    # via `return []` unconditionally - a large repo whose first few git
    # grep calls are slow could get "no match" despite real matches already
    # computed. time.monotonic is patched to simulate: started at 0, still
    # under deadline before the first identifier, then past it before the
    # second - the first identifier's score must survive.
    import aletheore.code_locator as code_locator

    repo = make_repo(tmp_path, FILES)
    calls = iter([0.0, 0.0, 10.0])
    monkeypatch.setattr(code_locator.time, "monotonic", lambda: next(calls))

    cands = locate(
        "HelpFormatter bogus_other_identifier",
        repo,
        modules_for(FILES),
        all_words=True,
        deadline_s=3.0,
        require_confidence=False,
    )

    assert cands and cands[0].path == "src/fmt.py"


def test_render_locate_gives_a_header_and_the_raw_function_source(tmp_path):
    repo = make_repo(tmp_path, FILES)
    cands = locate("HelpFormatter write_usage", repo, modules_for(FILES), all_words=True)
    text = render_locate(cands, repo)
    assert "src/fmt.py::HelpFormatter.write_usage L2-4\n" in text
    assert text.count("def write_usage") == 1  # the class is a signature line, its methods are not repeated
    assert "        prefix = 'Usage: '" in text  # raw code, original indentation, no escaping or line numbers
    assert "write_text" not in text  # only the function that holds the match


def test_render_locate_cuts_long_functions_and_says_how_to_get_the_rest(tmp_path):
    body = "def big_function_name():\n" + "\n".join(f"    x{i} = {i}" for i in range(100)) + "\n"
    files = {"src/big.py": body, **{f"src/other{i}.py": "def other():\n    pass\n" for i in range(12)}}
    repo = make_repo(tmp_path, files)
    cands = locate("big_function_name", repo, modules_for(files), all_words=True, require_confidence=False)
    text = render_locate(cands, repo, max_lines=10)
    assert "more lines; use aletheore_symbol_source" in text and text.count("x") < 30


def test_render_locate_respects_the_character_budget(tmp_path):
    files = {f"src/m{i}.py": f"def shared_name_{i % 2}():\n    return shared_marker\n\ndef f{i}():\n    return shared_marker\n" for i in range(6)}
    repo = make_repo(tmp_path, files)
    cands = locate("shared_marker", repo, modules_for(files), all_words=True)
    text = render_locate(cands, repo, max_chars=120)
    assert len(text) < 400


def test_a_weak_single_match_is_dropped_unless_confidence_is_waived(tmp_path):
    files = {"src/big.py": "def big_function_name():\n    pass\n", "src/other.py": "def other():\n    pass\n"}
    repo = make_repo(tmp_path, files)
    assert locate("big_function_name", repo, modules_for(files), all_words=True) == []
    assert locate("big_function_name", repo, modules_for(files), all_words=True, require_confidence=False)
