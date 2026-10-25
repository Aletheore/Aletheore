"""Round 2: Ruby constant references, per-header C/C++ language tagging, extra C++ extensions."""
from pathlib import Path

from aletheore.scanner.graph import build_module_graph


def write(root: Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def scan(root: Path) -> dict[str, dict]:
    modules, _graph, _bad = build_module_graph(root)
    return {m["path"]: m for m in modules}


def imports_of(root: Path) -> dict[str, set[str]]:
    return {p: set(m["imports"]) for p, m in scan(root).items()}


# ---------------- Ruby constants ----------------

def ruby_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "rb"
    write(repo, "lib/shop/cart.rb", "module Shop\n  class Cart\n    LIMIT = 3\n  end\nend\n")
    write(repo, "lib/shop/order.rb", (
        "module Shop\n  class Order\n    def total\n      Cart.new\n    end\n"
        "    def limit\n      Cart::LIMIT\n    end\n  end\nend\n"
    ))
    write(repo, "lib/shop/pay.rb", "module Shop\n  class Pay\n    def go(o)\n      o.class::LIMIT\n    end\n  end\nend\n")
    write(repo, "lib/shop/util.rb", "module Shop\n  module Util; end\nend\n")
    write(repo, "lib/shop/util2.rb", "module Shop\n  module Util; end\nend\n")
    write(repo, "lib/shop/user_of_util.rb", "module Shop\n  class UserOfUtil\n    include Util\n  end\nend\n")
    write(repo, "lib/shop/core_ext.rb", "class Hash\n  def deep; end\nend\n")
    write(repo, "lib/shop/uses_hash.rb", "module Shop\n  class UsesHash\n    def f\n      Hash.new\n    end\n  end\nend\n")
    write(repo, "lib/shop/own.rb", "module Shop\n  class Own\n    class Cart; end\n    def f\n      Cart.new\n    end\n  end\nend\n")
    write(repo, "lib/shop/top.rb", "class Top\n  def f\n    ::Shop::Cart\n  end\nend\n")
    return repo


def test_ruby_constant_resolves_through_lexical_nesting(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert "lib/shop/cart.rb" in imp["lib/shop/order.rb"]


def test_ruby_absolute_scope_reference(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert "lib/shop/cart.rb" in imp["lib/shop/top.rb"]


def test_ruby_expression_scope_name_is_not_a_lexical_reference(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert "lib/shop/cart.rb" not in imp["lib/shop/pay.rb"]  # o.class::LIMIT


def test_ruby_constant_defined_in_two_files_is_skipped(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert imp["lib/shop/user_of_util.rb"] == set()


def test_ruby_builtin_constant_reopened_in_repo_adds_no_edge(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert "lib/shop/core_ext.rb" not in imp["lib/shop/uses_hash.rb"]


def test_ruby_constant_also_defined_in_same_file_adds_no_edge(tmp_path):
    imp = imports_of(ruby_repo(tmp_path))
    assert "lib/shop/cart.rb" not in imp["lib/shop/own.rb"]


def test_ruby_autoload_counts_as_a_definition(tmp_path):
    repo = tmp_path / "al"
    write(repo, "lib/app.rb", 'module App\n  autoload :Engine, "app/engine"\nend\n')
    write(repo, "lib/app/engine.rb", "class Motor; end\n")
    write(repo, "lib/app/user.rb", "module App\n  class User\n    def f\n      Engine\n    end\n  end\nend\n")
    imp = imports_of(repo)
    assert "lib/app/engine.rb" in imp["lib/app/user.rb"]


# ---------------- C header tagging ----------------

def test_c_only_repo_headers_are_tagged_c(tmp_path):
    repo = tmp_path / "conly"
    write(repo, "src/a.h", "int a(void);\n")
    write(repo, "src/a.c", '#include "a.h"\nint a(void){return 1;}\n')
    write(repo, "src/types.h", "typedef int t;\n")
    write(repo, "src/b.c", '#include "types.h"\n#include "a.h"\n')
    write(repo, "src/only_in_headers.h", '#include "types.h"\n')
    write(repo, "src/c.c", '#include "only_in_headers.h"\n')
    mods = scan(repo)
    assert mods["src/a.h"]["language"] == "c"
    assert mods["src/types.h"]["language"] == "c"
    assert mods["src/only_in_headers.h"]["language"] == "c"


def test_cpp_repo_headers_stay_cpp(tmp_path):
    repo = tmp_path / "cppo"
    write(repo, "include/lib/x.h", "class X {};\n")
    write(repo, "src/x.cc", '#include "lib/x.h"\n')
    write(repo, "include/lib/lonely.h", "class L {};\n")
    mods = scan(repo)
    assert mods["include/lib/x.h"]["language"] == "cpp"
    assert mods["include/lib/lonely.h"]["language"] == "cpp"  # no evidence keeps the shared default


def test_mixed_repo_header_follows_its_own_users(tmp_path):
    repo = tmp_path / "mixed"
    write(repo, "src/legacy.h", "int legacy(void);\n")
    write(repo, "src/legacy.c", '#include "legacy.h"\n')
    write(repo, "src/modern.h", "class M {};\n")
    write(repo, "src/modern.cpp", '#include "modern.h"\n')
    write(repo, "src/modern2.cpp", '#include "modern.h"\n')
    mods = scan(repo)
    assert mods["src/legacy.h"]["language"] == "c"
    assert mods["src/modern.h"]["language"] == "cpp"


def test_hh_hxx_cxx_extensions_are_modules(tmp_path):
    repo = tmp_path / "ext"
    write(repo, "a.hh", "class A {};\n")
    write(repo, "b.hxx", '#include "a.hh"\n')
    write(repo, "c.cxx", '#include "b.hxx"\n')
    imp = imports_of(repo)
    assert imp["b.hxx"] == {"a.hh"}
    assert imp["c.cxx"] == {"b.hxx"}


# ---------------- PHP review fixes ----------------

def php_names_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "phpn"
    write(repo, "src/Foo.php", "<?php\nnamespace A;\nclass Foo { const BAR = 1; }\n")
    write(repo, "src/Bar.php", "<?php\nnamespace A;\nclass Bar {}\n")
    write(repo, "src/Hearts.php", "<?php\nnamespace A;\nclass Hearts {}\n")
    write(repo, "src/Suit.php", "<?php\nnamespace A;\nenum Suit { case Hearts; }\n")
    write(repo, "src/Some.php", "<?php\nnamespace A;\nclass SomeConst {}\n")
    write(repo, "src/User.php", (
        "<?php\nnamespace A;\nclass User {\n"
        "    function f() { return [Foo::Bar, Suit::Hearts, Foo::SomeConst]; }\n}\n"
    ))
    return repo


def test_php_name_before_double_colon_is_a_reference(tmp_path):
    imp = imports_of(php_names_repo(tmp_path))
    assert {"src/Foo.php", "src/Suit.php"} <= imp["src/User.php"]


def test_php_name_after_double_colon_creates_no_edge(tmp_path):
    # review finding was a false alarm: the lookbehind already excludes a name that follows `::`
    imp = imports_of(php_names_repo(tmp_path))
    assert "src/Bar.php" not in imp["src/User.php"]
    assert "src/Hearts.php" not in imp["src/User.php"]
    assert "src/Some.php" not in imp["src/User.php"]


def test_php_heredoc_and_nowdoc_bodies_are_not_scanned(tmp_path):
    repo = tmp_path / "phph"
    write(repo, "src/Widget.php", "<?php\nnamespace A;\nclass Widget {}\n")
    write(repo, "src/Gadget.php", "<?php\nnamespace A;\nclass Gadget {}\n")
    write(repo, "src/Gizmo.php", "<?php\nnamespace A;\nclass Gizmo {}\n")
    write(repo, "src/Real.php", "<?php\nnamespace A;\nclass Real {}\n")
    write(repo, "src/Doc.php", (
        "<?php\nnamespace A;\nclass Doc {\n"
        "    function f() {\n"
        "        $a = <<<EOT\n  Widget is mentioned here, and 'quotes\nEOT;\n"
        "        $b = <<<'RAW'\nGadget // not a comment\nRAW;\n"
        "        $c = <<<  \"IND\"\n    Gizmo\n    IND;\n"
        "        return new Real();\n    }\n}\n"
    ))
    imp = imports_of(repo)
    assert imp["src/Doc.php"] == {"src/Real.php"}
