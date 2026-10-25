"""Cross-file edge resolution for PHP (implicit class refs), Ruby (autoload, require_all, load paths) and C/C++ (include dirs)."""
import json
from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def imports_of(root: Path) -> dict[str, set[str]]:
    modules, _graph, _bad = build_module_graph(root)
    return {m["path"]: set(m["imports"]) for m in modules}


# ---------------- PHP ----------------

def php_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "php"
    write(repo, "composer.json", json.dumps({"autoload": {"psr-4": {"App\\": "src/"}}}))
    write(repo, "src/Options.php", "<?php\nnamespace App;\nclass Options {}\n")
    write(repo, "src/Contracts/Thing.php", "<?php\nnamespace App\\Contracts;\ninterface Thing {}\n")
    write(repo, "src/Concerns/Loud.php", "<?php\nnamespace App\\Concerns;\ntrait Loud {}\n")
    write(repo, "src/Client.php", (
        "<?php\nnamespace App;\n\nuse App\\Contracts as C;\nuse Psr\\Http\\Message\\Foo;\n\n"
        "class Client implements C\\Thing\n{\n    use \\App\\Concerns\\Loud;\n"
        "    public function go(): Options { return new Options(); }\n"
        "    public function other() { return \\App\\Contracts\\Thing::class; }\n}\n"
    ))
    write(repo, "src/Quiet.php", (
        "<?php\nnamespace App;\n// new Options() in a comment\n/* Options */\n"
        "class Quiet { public $x = 'Options'; public function f($o) { return $o->Options; } }\n"
    ))
    return repo


def test_php_same_namespace_reference_makes_an_edge(tmp_path):
    imp = imports_of(php_repo(tmp_path))
    assert "src/Options.php" in imp["src/Client.php"]


def test_php_alias_subnamespace_and_inline_fqn_make_edges(tmp_path):
    imp = imports_of(php_repo(tmp_path))
    assert "src/Contracts/Thing.php" in imp["src/Client.php"]
    assert "src/Concerns/Loud.php" in imp["src/Client.php"]


def test_php_comments_strings_and_member_access_make_no_edge(tmp_path):
    imp = imports_of(php_repo(tmp_path))
    assert "src/Options.php" not in imp["src/Quiet.php"]


def test_php_unknown_names_and_self_are_not_edges(tmp_path):
    imp = imports_of(php_repo(tmp_path))
    assert "src/Client.php" not in imp["src/Client.php"]
    assert all(t.startswith("src/") for t in imp["src/Client.php"])


def test_php_ambiguous_declaration_is_skipped(tmp_path):
    repo = tmp_path / "amb"
    write(repo, "a/Dup.php", "<?php\nnamespace N;\nclass Dup {}\n")
    write(repo, "b/Dup.php", "<?php\nnamespace N;\nclass Dup {}\n")
    write(repo, "c/User.php", "<?php\nnamespace N;\nclass User { function f() { return new Dup(); } }\n")
    assert imports_of(repo)["c/User.php"] == set()


def test_php_other_namespace_same_short_name_not_matched(tmp_path):
    repo = tmp_path / "ns"
    write(repo, "a/Item.php", "<?php\nnamespace A;\nclass Item {}\n")
    write(repo, "b/User.php", "<?php\nnamespace B;\nclass User { function f() { return new Item(); } }\n")
    assert imports_of(repo)["b/User.php"] == set()


# ---------------- Ruby ----------------

def ruby_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "rb"
    write(repo, "lib/lib_main.rb", (
        'autoload :Widget, "lib_main/widget"\n'
        'require_all "lib_main/cmds"\n'
        "module LibMain; end\n"
    ))
    write(repo, "lib/lib_main/widget.rb", "class Widget; end\n")
    write(repo, "lib/lib_main/cmds/a.rb", "class A; end\n")
    write(repo, "lib/lib_main/cmds/deep/b.rb", "class B; end\n")
    write(repo, "test/helper.rb", "require 'lib_main'\n")
    write(repo, "test/test_a.rb", 'require "helper"\n')
    write(repo, "spec/spec_helper.rb", "x = 1\n")
    write(repo, "spec/a_spec.rb", 'require "spec_helper"\nrequire "net/http"\n')
    write(repo, "rootfile.rb", "y = 1\n")
    write(repo, "script.rb", 'require "rootfile"\n')
    return repo


def test_ruby_autoload_makes_an_edge(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert "lib/lib_main/widget.rb" in imp["lib/lib_main.rb"]


def test_ruby_require_all_expands_directory_recursively(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert {"lib/lib_main/cmds/a.rb", "lib/lib_main/cmds/deep/b.rb"} <= imp["lib/lib_main.rb"]


def test_ruby_test_spec_and_root_load_paths(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert imp["test/test_a.rb"] == {"test/helper.rb"}
    assert imp["spec/a_spec.rb"] == {"spec/spec_helper.rb"}
    # the bare repo root is deliberately NOT a load path (a root json.rb must not capture `require "json"`)
    assert imp["script.rb"] == set()
    assert imp["test/helper.rb"] == {"lib/lib_main.rb"}


def test_ruby_lib_wins_over_test_load_path(tmp_path):
    repo = tmp_path / "prio"
    write(repo, "lib/thing.rb", "")
    write(repo, "test/thing.rb", "")
    write(repo, "app.rb", "require 'thing'\n")
    assert imports_of(repo)["app.rb"] == {"lib/thing.rb"}


# ---------------- C / C++ ----------------

def c_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "c"
    write(repo, "include/lib/api.h", "#pragma once\n")
    write(repo, "include/lib/util.h", '#pragma once\n#include "api.h"\n')
    write(repo, "src/impl.c", '#include "lib/api.h"\n#include <lib/util.h>\n#include <stdio.h>\n#include "missing.h"\n')
    write(repo, "src/local.h", "#pragma once\n")
    write(repo, "src/local.c", '#include "local.h"\n')
    write(repo, "tests/t.c", '#include "lib/api.h"\n')
    write(repo, "src/table.c", 'static const int t[] = {\n#include "rows.h"\n};\n')
    write(repo, "src/rows.h", "1, 2\n")
    write(repo, "src/stdio.h", "#pragma once\n")
    write(repo, "deep/pkg/names.h", "#pragma once\n")
    write(repo, "src/uses_deep.c", '#include "pkg/names.h"\n')
    return repo


def test_c_quoted_include_resolved_against_include_and_src_dirs(tmp_path):
    imp = imports_of(c_repo(tmp_path))
    assert "include/lib/api.h" in imp["src/impl.c"]
    assert imp["tests/t.c"] == {"include/lib/api.h"}


def test_c_angle_include_in_repo_resolves_system_header_dropped(tmp_path):
    imp = imports_of(c_repo(tmp_path))
    assert "include/lib/util.h" in imp["src/impl.c"]
    assert "src/stdio.h" not in imp["src/impl.c"]  # <stdio.h> never binds to src/stdio.h
    assert "missing.h" not in " ".join(imp["src/impl.c"])


def test_c_includer_directory_still_first(tmp_path):
    imp = imports_of(c_repo(tmp_path))
    assert imp["src/local.c"] == {"src/local.h"}
    assert imp["include/lib/util.h"] == {"include/lib/api.h"}


def test_c_include_inside_initializer_is_found(tmp_path):
    # the tree-sitter walk loses #include inside an initializer list; the line scan finds it
    imp = imports_of(c_repo(tmp_path))
    assert imp["src/table.c"] == {"src/rows.h"}


def test_c_unique_suffix_match_in_header_directory(tmp_path):
    imp = imports_of(c_repo(tmp_path))
    assert imp["src/uses_deep.c"] == {"deep/pkg/names.h"}


def test_c_ambiguous_suffix_picks_nearest_directory_or_nothing(tmp_path):
    repo = tmp_path / "amb"
    write(repo, "a/x/cfg/opts.h", "")
    write(repo, "b/y/cfg/opts.h", "")
    write(repo, "a/x/main.c", '#include "cfg/opts.h"\n')
    write(repo, "z/other.c", '#include "cfg/opts.h"\n')
    imp = imports_of(repo)
    assert imp["a/x/main.c"] == {"a/x/cfg/opts.h"}
    assert imp["z/other.c"] == set()
