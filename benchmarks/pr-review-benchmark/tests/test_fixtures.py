from pathlib import Path

from scripts.fixtures import (
    BENCHMARK_SECRET_PLACEHOLDER,
    contains_placeholder,
    expand_placeholders,
    expand_placeholders_in_tree,
    unknown_placeholders,
)


def test_the_repository_itself_never_stores_a_scannable_key():
    # The whole point: no source file in this package may contain a
    # contiguous "sk_live_" + alphanumerics literal, or GitHub's push
    # protection blocks every push of this branch again.
    scannable = "sk_" + "live_" + "51Hc9f2K8sJ3xN0pQzT7yV6bW9dR4eA1"
    package_root = Path(__file__).resolve().parent.parent
    offenders = []
    for path in package_root.rglob("*"):
        if not path.is_file() or ".git" in path.parts or "__pycache__" in path.parts:
            continue
        if "results" in path.relative_to(package_root).parts:
            continue  # local-only working state, gitignored
        try:
            if scannable in path.read_text():
                offenders.append(str(path.relative_to(package_root)))
        except (OSError, UnicodeDecodeError):
            continue
    assert offenders == []


def test_expand_placeholders_produces_a_realistic_secret():
    expanded = expand_placeholders(f"var K = '{BENCHMARK_SECRET_PLACEHOLDER}'")

    assert BENCHMARK_SECRET_PLACEHOLDER not in expanded
    assert expanded.startswith("var K = 'sk_live_")
    # Long enough that a secret scanner would actually match it - a short
    # token would let pattern-based tools off the hook and stop case 020
    # testing anything.
    assert len(expanded.split("'")[1]) >= 32


def test_contains_placeholder_detects_and_ignores_correctly():
    assert contains_placeholder(f"x = {BENCHMARK_SECRET_PLACEHOLDER}") is True
    assert contains_placeholder("x = 'ordinary string'") is False


def test_unknown_placeholders_flags_an_unregistered_token():
    assert unknown_placeholders("a = __BENCHMARK_NOT_REGISTERED__") == {
        "__BENCHMARK_NOT_REGISTERED__"
    }
    assert unknown_placeholders(f"a = {BENCHMARK_SECRET_PLACEHOLDER}") == set()


def test_expand_placeholders_in_tree_rewrites_matching_files_only(tmp_path):
    (tmp_path / "lib").mkdir()
    (tmp_path / "lib" / "utils.js").write_text(
        f"var SECRET = '{BENCHMARK_SECRET_PLACEHOLDER}'\n"
    )
    (tmp_path / "untouched.js").write_text("var x = 1\n")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text(
        f"var S = '{BENCHMARK_SECRET_PLACEHOLDER}'\n"
    )

    changed = expand_placeholders_in_tree(tmp_path)

    assert changed == ["lib/utils.js"]
    assert BENCHMARK_SECRET_PLACEHOLDER not in (tmp_path / "lib" / "utils.js").read_text()
    assert "sk_live_" in (tmp_path / "lib" / "utils.js").read_text()
    # Vendored trees are skipped deliberately - nothing reviews them.
    assert BENCHMARK_SECRET_PLACEHOLDER in (tmp_path / "node_modules" / "dep.js").read_text()


def test_expand_placeholders_in_tree_is_a_noop_without_placeholders(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n")

    assert expand_placeholders_in_tree(tmp_path) == []


def test_case_020_diff_still_plants_a_scannable_secret_after_expansion():
    # End-to-end guarantee for the case's validity: the corpus stores a
    # placeholder, but what a tool reads must still look like a live key.
    case_dir = (
        Path(__file__).resolve().parent.parent
        / "cases"
        / "020-express-hardcoded-webhook-secret"
    )
    diff = case_dir / "pr.diff"
    assert BENCHMARK_SECRET_PLACEHOLDER in diff.read_text()

    expanded = expand_placeholders(diff.read_text())
    assert "DEFAULT_WEBHOOK_SECRET = 'sk_live_" in expanded


def test_case_020_ground_truth_is_expanded_at_load_time():
    # The judge must be told about the same secret the tools saw.
    from scripts.cases import load_ground_truth

    case_dir = (
        Path(__file__).resolve().parent.parent
        / "cases"
        / "020-express-hardcoded-webhook-secret"
    )
    ground_truth = load_ground_truth(case_dir)

    assert BENCHMARK_SECRET_PLACEHOLDER not in ground_truth["description"]
    assert "sk_live_" in ground_truth["description"]
