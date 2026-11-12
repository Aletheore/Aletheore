"""A path checked to be inside a directory by string prefix without a trailing separator accepts a sibling directory (`/srv/upload-evil`)."""
import pytest

from aletheore.flow.weak_checks import scan

CASES = {
    "ruby": ("a.rb", "def extract(dest_dir, name)\n  extract_path = ::File.absolute_path(name, dest_dir)\n  unless extract_path.start_with?(dest_dir)\n    raise 'outside'\n  end\nend\n",
             "unless extract_path.start_with?(dest_dir + ::File::SEPARATOR)"),
    "python": ("a.py", "import os\ndef f(root, name):\n    path = os.path.abspath(os.path.join(root, name))\n    if not path.startswith(root):\n        raise ValueError('outside')\n",
               "if not path.startswith(os.path.join(root, '')):"),
    "go": ("a.go", "package x\nimport (\"path/filepath\";\"strings\";\"errors\")\nfunc f(root, name string) error {\n\tfull := filepath.Clean(filepath.Join(root, name))\n\tif !strings.HasPrefix(full, root) {\n\t\treturn errors.New(\"outside\")\n\t}\n\treturn nil\n}\n",
           "if !strings.HasPrefix(full, root+string(filepath.Separator)) {"),
    "java": ("A.java", "class A {\n  void f(java.io.File base, String name) throws Exception {\n    String full = new java.io.File(base, name).getCanonicalPath();\n    if (!full.startsWith(base.getCanonicalPath())) {\n      throw new Exception(\"outside\");\n    }\n  }\n}\n",
             "if (!full.startsWith(base.getCanonicalPath() + File.separator)) {"),
    "javascript": ("a.js", "const path=require('path');\nfunction f(root, name){\n  const full = path.resolve(root, name);\n  if (!full.startsWith(root)) { throw new Error('outside'); }\n}\n",
                  "if (!full.startsWith(root + path.sep)) {"),
    "csharp": ("A.cs", "class A { void F(string root, string name) {\n  var full = System.IO.Path.GetFullPath(System.IO.Path.Combine(root, name));\n  if (!full.StartsWith(root)) {\n    throw new System.Exception(\"outside\");\n  }\n} }\n",
               "if (!full.StartsWith(root + System.IO.Path.DirectorySeparatorChar)) {"),
    "php": ("a.php", "<?php\nfunction f($root, $name) {\n  $full = realpath($root . '/' . $name);\n  if (strpos($full, $root) !== 0) { throw new Exception('outside'); }\n}\n",
            "if (strpos($full, $root . DIRECTORY_SEPARATOR) !== 0) {"),
}


@pytest.mark.parametrize("lang", list(CASES))
def test_prefix_check_without_separator_is_reported(tmp_path, lang):
    name, src, _ = CASES[lang]
    (tmp_path / name).write_text(src)
    got = scan(tmp_path)
    assert len(got) == 1 and got[0]["rule_id"] == f"flow.{lang}.path-containment" and got[0]["cwe"] == ["CWE-22"], got


@pytest.mark.parametrize("lang", list(CASES))
def test_prefix_with_separator_is_fine(tmp_path, lang):
    name, src, fixed = CASES[lang]
    old = [line for line in src.splitlines() if ("start" in line.lower() or "strpos" in line or "HasPrefix" in line) and ("(" in line)][0]
    (tmp_path / name).write_text(src.replace(old.strip(), fixed.strip()) if old.strip() in src else src)
    assert scan(tmp_path) == []


def test_not_canonical_or_not_rejecting_is_not_reported(tmp_path):
    (tmp_path / "a.rb").write_text("def f(dest_dir, extract_path)\n  unless extract_path.start_with?(dest_dir)\n    raise 'x'\n  end\nend\n")  # no canonicalisation nearby
    assert scan(tmp_path) == []
    (tmp_path / "a.rb").write_text("def f(dest_dir, name)\n  extract_path = File.expand_path(name)\n  puts 'x' if extract_path.start_with?(dest_dir)\nend\n")  # does not reject
    assert scan(tmp_path) == []


def test_java_nio_path_is_compared_by_component(tmp_path):
    (tmp_path / "A.java").write_text("class A {\n  void f(java.nio.file.Path base, String n) throws Exception {\n    java.nio.file.Path p = base.resolve(n).normalize();\n    if (!p.startsWith(base)) {\n      throw new Exception(\"outside\");\n    }\n  }\n}\n")
    assert scan(tmp_path) == []
