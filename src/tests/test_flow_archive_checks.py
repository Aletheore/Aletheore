"""Archive extraction that joins an entry name into an output path with no containment check anywhere in the file ("Zip Slip")."""
import pytest

from aletheore.flow.weak_checks import scan

BAD = {
    "java": ("A.java", "import java.util.zip.*;\nclass A { void f(ZipInputStream zis, java.io.File dest) throws Exception {\n  ZipEntry entry = zis.getNextEntry();\n"
                       "  java.io.File out = new java.io.File(dest, entry.getName());\n  try (java.io.FileOutputStream o = new java.io.FileOutputStream(out)) { }\n} }\n",
             "  java.io.File out = new java.io.File(dest, entry.getName());\n  if (!out.getCanonicalPath().startsWith(dest.getCanonicalPath() + java.io.File.separator)) throw new Exception();\n"),
    "python": ("a.py", "import tarfile\ndef f(p):\n    tar = tarfile.open(p)\n    tar.extractall('/tmp/x')\n",
              "    tar.extractall('/tmp/x', filter='data')\n"),
    "go": ("a.go", "package x\nimport \"archive/zip\"\nfunc f(d string) {\n\tr, _ := zip.OpenReader(\"a.zip\")\n\tfor _, f := range r.File {\n\t\tp := filepath.Join(d, f.Name)\n\t\tos.MkdirAll(p, 0o755)\n\t}\n}\n",
           "\t\tp := filepath.Join(d, f.Name)\n\t\tif !strings.HasPrefix(p, d+string(os.PathSeparator)) { continue }\n\t\tos.MkdirAll(p, 0o755)\n"),
    "javascript": ("a.js", "const yauzl = require('yauzl'); const path = require('path'); const fs = require('fs');\nfunction f(entry, dest){\n  const out = path.join(dest, entry.fileName);\n  fs.writeFileSync(out, '');\n}\n",
                   "  const out = path.join(dest, entry.fileName);\n  if (!out.startsWith(dest + path.sep)) throw new Error('x');\n  fs.writeFileSync(out, '');\n"),
    "csharp": ("A.cs", "using System.IO.Compression;\nclass A { void F(ZipArchive a, string d) {\n  foreach (var entry in a.Entries) {\n    var p = Path.Combine(d, entry.FullName);\n    Directory.CreateDirectory(p);\n  }\n} }\n",
              "    var p = Path.GetFullPath(Path.Combine(d, entry.FullName));\n    if (!p.StartsWith(d)) throw new System.Exception();\n    Directory.CreateDirectory(p);\n"),
}


@pytest.mark.parametrize("lang", list(BAD))
def test_unguarded_extraction_is_reported(tmp_path, lang):
    name, src, _ = BAD[lang]
    (tmp_path / name).write_text(src)
    got = [f for f in scan(tmp_path) if f["rule_id"].endswith("archive-path")]
    assert len(got) == 1 and got[0]["rule_id"] == f"flow.{lang}.archive-path" and got[0]["cwe"] == ["CWE-22"], got


@pytest.mark.parametrize("lang", list(BAD))
def test_a_containment_guard_in_the_file_keeps_it_quiet(tmp_path, lang):
    name, src, fixed = BAD[lang]
    first = {"java": "  java.io.File out = new java.io.File(dest, entry.getName());\n", "python": "    tar.extractall('/tmp/x')\n",
             "go": "\t\tp := filepath.Join(d, f.Name)\n\t\tos.MkdirAll(p, 0o755)\n",
             "javascript": "  const out = path.join(dest, entry.fileName);\n  fs.writeFileSync(out, '');\n",
             "csharp": "    var p = Path.Combine(d, entry.FullName);\n    Directory.CreateDirectory(p);\n"}[lang]
    assert first in src
    (tmp_path / name).write_text(src.replace(first, fixed))
    assert [f for f in scan(tmp_path) if f["rule_id"].endswith("archive-path")] == []


def test_building_an_archive_is_not_extraction(tmp_path):
    (tmp_path / "w.go").write_text("package x\nimport \"archive/zip\"\nfunc f(d string, entries []os.DirEntry, z *zip.Writer) {\n\tfor _, e := range entries {\n\t\tz.AddFile(filepath.Join(d, e.Name()))\n\t}\n}\n")
    assert [f for f in scan(tmp_path) if f["rule_id"].endswith("archive-path")] == []


def test_reading_entry_names_without_writing_is_not_reported(tmp_path):
    (tmp_path / "r.cs").write_text("using System.IO.Compression;\nclass A { bool F(ZipArchive a, string d) {\n  foreach (var entry in a.Entries) {\n    var p = Path.Combine(d, entry.FullName);\n    return p.Length > 3;\n  }\n  return false;\n} }\n")
    assert [f for f in scan(tmp_path) if f["rule_id"].endswith("archive-path")] == []
