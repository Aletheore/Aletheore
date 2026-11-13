"""Regular expressions with a repeated group whose body can match the same text several ways backtrack exponentially."""
import pytest

from aletheore.flow import redos
from aletheore.flow.weak_checks import scan


@pytest.mark.parametrize("pat", [r"(a+)+", r"(\s*\w+\s*)*", r"(\d+,?)+", r"(x+x+)+y", r"((a)+)+", r"^(a+)+$"])
def test_nested_repetition_is_exponential(pat):
    assert redos.weakness(pat) == "exponential"


@pytest.mark.parametrize("pat", [r"(\d+\.)+", r"(\.\d+)*", r"[a-z]+", r"\s+$", r"^\s+|\s+$", r"(?:a|b)+", r"[a-z0-9_]+([.-]+[a-z0-9_]+)*", r"(a++)+", r"(?:\d{1,3}\.){3}\d+"])
def test_unambiguous_or_linear_patterns_are_not_reported(pat):
    assert redos.weakness(pat) is None


LANG = {
    "a.js": "const re = /^(a+)+$/;\n",
    "b.js": "const re = new RegExp('(a+)+b');\n",
    "c.py": "import re\nX = re.compile(r'(\\w+\\s?)*$')\n",
    "D.java": "class D { static final java.util.regex.Pattern P = java.util.regex.Pattern.compile(\"(a+)+\"); }\n",
    "e.rb": "X = /^(a+)+$/\n",
    "f.php": "<?php\npreg_match('/(a+)+b/', $s);\n",
    "G.cs": "class G { static System.Text.RegularExpressions.Regex R = new System.Text.RegularExpressions.Regex(\"(a+)+\"); }\n",
}


@pytest.mark.parametrize("name", list(LANG))
def test_reported_in_each_language(tmp_path, name):
    (tmp_path / name).write_text(LANG[name])
    got = [f for f in scan(tmp_path) if f["rule_id"].endswith(".redos")]
    assert len(got) == 1 and got[0]["cwe"] == ["CWE-1333", "CWE-730"], got


def test_vendored_and_go_are_skipped(tmp_path):
    (tmp_path / "public").mkdir()
    (tmp_path / "public" / "lib.js").write_text("const re = /^(a+)+$/;\n")
    (tmp_path / "x.go").write_text("package x\nimport \"regexp\"\nvar r = regexp.MustCompile(`(a+)+`)\n")
    assert [f for f in scan(tmp_path) if f["rule_id"].endswith(".redos")] == []


@pytest.mark.parametrize("pat", [r"^(.*,)+(.+)?$", r"^(?:\.?[a-zA-Z_][a-zA-Z_0-9]*)+$", r"(?:\.?\d+)+"])
def test_group_that_ends_in_what_the_next_round_starts_with_is_exponential(pat):
    assert redos.weakness(pat) == "exponential"


@pytest.mark.parametrize("pat", [r"\s*\n\s*", r"\S+@\S+\.\S+", r"^(\-|\+)?([1-9]+[0-9]*)$"])
def test_adjacent_overlapping_repeats_are_polynomial(pat):
    assert redos.weakness(pat) == "polynomial"


@pytest.mark.parametrize("pat", [r"\s*,\s*", r" *, *", r"^.*a.*b", r"(\w+,)+", r"([^,]*,)+", r"[0-9]+-[0-9]+", r"\d+\.\d+"])
def test_separator_and_dotstar_patterns_stay_quiet(pat):
    assert redos.weakness(pat) is None
