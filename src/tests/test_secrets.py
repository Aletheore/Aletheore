import json

import aletheore.secrets as secrets_module
from aletheore.secrets import find_secrets, load_secrets_baseline


def test_find_secrets_detects_aws_key(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = find_secrets(repo)

    assert result["scanned_files"] == 1
    assert len(result["findings"]) == 1
    finding = result["findings"][0]
    assert finding["path"] == "config.py"
    assert finding["line"] == 1
    assert finding["pattern"] == "aws_access_key_id"
    assert finding["likely_placeholder"] is False


def test_find_secrets_respects_ignored_paths_from_config(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    vendor = repo / "vendor"
    vendor.mkdir()
    (vendor / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    (repo / "real.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    (repo / ".aletheore.json").write_text(json.dumps({"ignored_paths": ["vendor/**"]}))

    result = find_secrets(repo)

    paths = {finding["path"] for finding in result["findings"]}
    assert paths == {"real.py"}


def test_find_secrets_redacts_the_match(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = find_secrets(repo)

    preview = result["findings"][0]["match_preview"]
    assert "AKIAABCDEFGHIJKLMNOP" not in preview
    # Previously this asserted the preview started with "AKIA" and ended with
    # "MNOP" - i.e. that four real leading and four real trailing characters
    # of the credential were published. That is the leak the salted hash
    # replaced, so the assertion is now the inverse.
    assert preview.startswith("sha256:")
    assert "AKIA" not in preview
    assert "MNOP" not in preview


def test_find_secrets_flags_test_fixture_paths_as_likely_placeholder(tmp_path):
    repo = tmp_path / "repo"
    (repo / "tests" / "fixtures").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "sample.py").write_text(
        'STRIPE_KEY = "sk_test_00000000000000000000"\n'
    )

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is True


def test_find_secrets_does_not_downgrade_a_real_looking_secret_under_a_test_path(tmp_path):
    # A path substring used to be sufficient on its own - a genuine, random
    # high-entropy key committed under tests/fixtures/ (a plausible place to
    # accidentally leak a real one) was silently marked likely_placeholder
    # regardless of what the value actually looked like.
    repo = tmp_path / "repo"
    (repo / "tests" / "fixtures").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "sample.py").write_text(
        'AWS_KEY = "AKIAQZRJTMXPLDVWKNBS"\n'
    )

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is False


def test_find_secrets_flags_a_documented_example_value_under_a_test_path(tmp_path):
    # AWS's own docs use AKIAIOSFODNN7EXAMPLE - a value-shape marker should
    # still catch this even though it isn't a low-entropy repeated string.
    repo = tmp_path / "repo"
    (repo / "tests" / "fixtures").mkdir(parents=True)
    (repo / "tests" / "fixtures" / "sample.py").write_text(
        'AWS_KEY = "AKIAIOSFODNN7EXAMPLE"\n'
    )

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is True


def test_find_secrets_flags_a_documented_example_value_outside_a_test_path(tmp_path):
    # Same AKIAIOSFODNN7EXAMPLE value as above, but in a README at the repo
    # root - the single most common place a student README pastes AWS's own
    # setup-docs example key, and nowhere near a path containing
    # "test"/"example"/"fixture"/"mock". The module docstring for
    # PLACEHOLDER_VALUE_MARKERS claims this is caught "independent of where
    # the file lives" - this is the case that claim was never actually true
    # for, since _is_likely_placeholder gates every value-shape check behind
    # a path check first.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("Example AWS creds from the docs:\nAKIAIOSFODNN7EXAMPLE\n")

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is True


def test_find_secrets_flags_a_hand_typed_repeated_pattern_as_placeholder(tmp_path):
    # A student hand-typing a fake example key (or padding one out) tends to
    # repeat a short unit rather than produce true randomness - this value
    # contains no PLACEHOLDER_VALUE_MARKERS word and has high raw Shannon
    # entropy (the character alphabet is diverse), so neither existing check
    # catches it, even though "abcdefghij1234567890" repeated twice is about
    # as far from a real credential generator's output as a value can get.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text(
        "OPENAI_API_KEY=sk-proj-abcdefghij1234567890abcdefghij1234567890abcd\n"
    )

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is True


def test_find_secrets_does_not_flag_a_genuinely_random_secret_as_repeated(tmp_path):
    # Guards the new repetition check against the false-negative risk it
    # introduces: a real secret must never accidentally look "repeated"
    # just because it happens to contain some structure. This one repeats
    # no substring and isn't in any PLACEHOLDER_PATH_MARKERS-flagged path.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text(
        'API_KEY = "RL9hCO7ulHXlasHeRNJ24lFwlUgDIj86dJMMYTSu"\n'
    )

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is False


def test_find_secrets_recognizes_stripes_own_published_test_key(tmp_path):
    # sk_test_4eC39HqLyjWDarjtT1zdp7dc is Stripe's own documentation
    # example key (developer docs, countless tutorials) - genuinely
    # high-entropy and non-repeating, so neither the marker-word nor the
    # repetition check catches it. It's specific enough (an exact,
    # official, publicly known value) to recognize directly rather than
    # try to generalize a pattern for it.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "notes.md").write_text("stripe test key: sk_test_4eC39HqLyjWDarjtT1zdp7dc\n")

    result = find_secrets(repo)

    assert result["findings"][0]["likely_placeholder"] is True


def test_find_secrets_detects_github_token_and_private_key_header(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.env").write_text("TOKEN=ghp_" + "a" * 36 + "\n")
    (repo / "id_rsa").write_text("-----BEGIN RSA PRIVATE KEY-----\nMIIBogIBAAJ...\n")

    result = find_secrets(repo)

    patterns_found = {f["pattern"] for f in result["findings"]}
    assert "github_token" in patterns_found
    assert "private_key_header" in patterns_found


def test_find_secrets_detects_unquoted_generic_credentials_and_multiple_matches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text(
        "DB_PASSWORD=Tr0ub4dor4NoSpecialChars\n"
        "export API_KEY=abcdefghijklmnopqrstuvwx1234\n"
        "password: mysecretvalue1234567890\n"
        "old_key=AKIA1234567890ABCDEF new_key=AKIAABCDEFGHIJKLMNOP\n"
    )

    findings = find_secrets(repo)["findings"]

    assert sum(f["pattern"] == "generic_credential_assignment" for f in findings) == 3
    assert sum(f["pattern"] == "aws_access_key_id" for f in findings) == 2


def test_find_secrets_detects_credential_value_containing_a_dot(tmp_path):
    # Regression: newer Google AI Studio keys use a dotted shape
    # ("AQ.Ab8R...") rather than the older AIza-prefixed google_api_key
    # format - the generic value class didn't include ".", so the match
    # stopped after 2 characters, fell under the 16-char minimum, and the
    # whole credential went undetected rather than just unredacted.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".env").write_text(
        "GEMINI_API_KEY=AQ.NotARealKey0123456789ABCDEFGHIJKLMNOPqrstuv\n"
    )

    findings = find_secrets(repo)["findings"]

    assert sum(f["pattern"] == "generic_credential_assignment" for f in findings) == 1


def test_find_secrets_detects_dotted_attribute_credential_assignment(tmp_path):
    # Regression: the left-boundary class allowed whitespace, "_", and "-"
    # before the keyword but not ".", so a dotted attribute assignment
    # (self.PASSWORD=..., cfg.API_KEY=...) - one of the most common
    # hardcoded-credential shapes in object-oriented code - was silently
    # invisible to this pattern. MYPASSWORD= (no separator at all) must
    # still not match.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text(
        "self.PASSWORD = 'Tr0ub4dor4NoSpecialChars'\n"
        "cfg.API_KEY = 'abcdefghijklmnopqrstuvwx1234'\n"
        "MYPASSWORD = 'shouldnotmatchatall1234567890'\n"
    )

    findings = find_secrets(repo)["findings"]

    assert sum(f["pattern"] == "generic_credential_assignment" for f in findings) == 2


def test_find_secrets_detects_quoted_key_credential_assignment(tmp_path):
    # Regression: neither the left-boundary class nor the post-keyword gap
    # before ':'/'=' accounted for the keyword's own closing quote, and the
    # right-boundary lookahead didn't include '}' or ']' - so a JSON/YAML/
    # dict-literal quoted-key credential ("API_KEY": "...", 'password': '...')
    # was completely invisible, whether or not it was the last key in the
    # object. This is the single most common real shape a hardcoded secret
    # takes in config files (docker-compose environment blocks, terraform
    # .tfvars, settings.json, a Python/JS dict literal) - not an edge case.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.json").write_text(
        '{"API_KEY": "sk-abcdefghijklmnopqrstuv"}\n'
        '{"password": "abcdefghijklmnopqrstuv", "x": 1}\n'
        "{'API_KEY': 'sk-abcdefghijklmnopqrstuv'}\n"
        '["API_KEY=sk-abcdefghijklmnopqrstuv"]\n'
    )

    findings = find_secrets(repo)["findings"]

    assert sum(f["pattern"] == "generic_credential_assignment" for f in findings) == 4


def test_find_secrets_detects_bracket_subscript_key_credential_assignment(tmp_path):
    # Regression: the quoted-key fix above covers a plain dict literal
    # ("API_KEY": "...") but left an equally common real shape uncovered -
    # bracket-subscript key assignment (os.environ["API_KEY"] = "...",
    # config["SECRET"] = "...", JS process.env['API_KEY'] = '...'). The
    # keyword's closing quote is followed by "]" before "=", which the
    # post-keyword gap couldn't skip over either. Confirmed as a real,
    # silent false negative by direct testing before this fix.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "settings.py").write_text(
        'os.environ["API_KEY"] = "sk-abcdefghijklmnopqrstuv"\n'
        "config['SECRET'] = 'abcdefghijklmnopqrstuv'\n"
    )
    (repo / "config.js").write_text(
        "process.env['API_KEY'] = 'sk-abcdefghijklmnopqrstuv';\n"
    )

    findings = find_secrets(repo)["findings"]

    assert sum(f["pattern"] == "generic_credential_assignment" for f in findings) == 3


def test_find_secrets_detects_fine_grained_github_and_sts_aws_tokens(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tokens.txt").write_text(
        "github_pat_1234567890abcdefghijkl\nASIA1234567890ABCDEF\n"
    )

    patterns = {finding["pattern"] for finding in find_secrets(repo)["findings"]}
    assert patterns == {"github_token", "aws_access_key_id"}


def test_find_secrets_ignores_ignored_dirs_and_binary_extensions(tmp_path):
    repo = tmp_path / "repo"
    (repo / "node_modules" / "pkg").mkdir(parents=True)
    (repo / "node_modules" / "pkg" / "secret.js").write_text('KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    (repo / "logo.png").write_bytes(b"AKIAABCDEFGHIJKLMNOP" + b"\x89PNG")
    (repo / "clean.py").write_text("x = 1\n")

    result = find_secrets(repo)

    assert result["findings"] == []
    assert result["scanned_files"] == 1


def test_find_secrets_skips_files_over_the_size_cap(tmp_path, monkeypatch):
    # A single unusually large committed file (a data dump, a vendored
    # bundle) read in full would risk OOMing the shared scan-worker
    # container - files over the cap are excluded from the walk entirely,
    # the same way binary extensions already are.
    monkeypatch.setattr(secrets_module, "MAX_SCANNED_FILE_BYTES", 10)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "huge.py").write_text('KEY = "AKIAABCDEFGHIJKLMNOP"\n' * 5)
    (repo / "small.py").write_text("x = 1\n")

    result = find_secrets(repo)

    assert result["scanned_files"] == 1
    assert result["findings"] == []


def test_find_secrets_no_matches_in_ordinary_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "main.py").write_text("def add(a, b):\n    return a + b\n")

    result = find_secrets(repo)

    assert result["findings"] == []
    assert result["scanned_files"] == 1


def test_find_secrets_does_not_follow_a_symlinked_file_outside_the_repo(tmp_path):
    # Before this fix, a symlinked file was still is_file() == True and got
    # scanned/reported on even though it points outside the intended repo root.
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    (repo / "linked.py").symlink_to(tmp_path / "outside.py")

    result = find_secrets(repo)

    assert result["findings"] == []
    assert result["scanned_files"] == 0


def test_find_secrets_does_not_descend_into_a_symlinked_directory_outside_the_repo(tmp_path):
    # A symlinked directory isn't itself is_file(), so the first check alone
    # doesn't protect against it - Path.rglob("*") still recurses through a
    # symlinked directory's contents by default, scanning real files outside
    # the repo as if they were part of it.
    repo = tmp_path / "repo"
    repo.mkdir()
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    (repo / "linked_dir").symlink_to(tmp_path / "outside")

    result = find_secrets(repo)

    assert result["findings"] == []
    assert result["scanned_files"] == 0


def test_find_secrets_generic_credential_preview_previews_the_value_not_the_keyword(tmp_path):
    # The property under test is unchanged: the preview must be derived from
    # the credential VALUE, not from the "secret"/"password"/"api_key" keyword
    # that happens to precede it on the line. It used to be checked by
    # asserting the preview began with the value's own first four characters,
    # which is no longer true (and was itself the leak). Checked here instead
    # by holding the keyword fixed and varying only the value: a preview keyed
    # off the keyword would be identical across both files.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text('secret = "totallyrealvalue1234567890tailend"\n')
    (repo / "b.py").write_text('secret = "adifferentvalue0987654321tailend"\n')

    previews = {f["path"]: f for f in find_secrets(repo)["findings"]}

    assert previews["a.py"]["pattern"] == "generic_credential_assignment"
    assert "totallyrealvalue1234567890tailend" not in previews["a.py"]["match_preview"]
    assert previews["a.py"]["match_preview"] != previews["b.py"]["match_preview"]
    assert not previews["a.py"]["match_preview"].lower().startswith("secr")


def test_find_secrets_always_includes_accepted_key_defaulting_false(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    result = find_secrets(repo)

    assert result["findings"][0]["accepted"] is False


def test_find_secrets_marks_a_baselined_finding_as_accepted(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    preview = find_secrets(repo)["findings"][0]["match_preview"]

    baseline = [{"path": "config.py", "pattern": "aws_access_key_id", "match_preview": preview}]
    result = find_secrets(repo, baseline=baseline)

    assert result["findings"][0]["accepted"] is True


def test_find_secrets_baseline_does_not_accept_a_non_matching_finding(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "config.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    baseline = [{"path": "other.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}]
    result = find_secrets(repo, baseline=baseline)

    assert result["findings"][0]["accepted"] is False


def test_load_secrets_baseline_reads_a_valid_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    entry = {"path": "config.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}
    (repo / ".aletheore.json").write_text(json.dumps({"accepted_secrets": [entry]}))

    assert load_secrets_baseline(repo) == [entry]


def test_load_secrets_baseline_returns_empty_list_when_file_missing(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()

    assert load_secrets_baseline(repo) == []


def test_load_secrets_baseline_returns_empty_list_on_malformed_json(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".aletheore.json").write_text("{not valid json")

    assert load_secrets_baseline(repo) == []


def test_load_secrets_baseline_returns_empty_list_when_key_is_not_a_list(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".aletheore.json").write_text(json.dumps({"accepted_secrets": "not-a-list"}))

    assert load_secrets_baseline(repo) == []


def test_load_secrets_baseline_filters_out_non_dict_entries(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    entry = {"path": "config.py", "pattern": "aws_access_key_id", "match_preview": "AKIA****...MNOP"}
    (repo / ".aletheore.json").write_text(json.dumps({"accepted_secrets": [entry, "garbage", 5]}))

    assert load_secrets_baseline(repo) == [entry]


def test_match_preview_no_longer_leaks_characters_of_the_value(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    findings = find_secrets(repo)["findings"]

    assert findings, "expected the planted secret to be detected"
    preview = findings[0]["match_preview"]
    assert preview.startswith("sha256:")
    # The specific regression: the old format emitted the first four and last
    # four real characters, so these must not survive anywhere in the preview.
    assert "AKIA" not in preview
    assert "MNOP" not in preview


def test_match_preview_is_salted_so_the_same_value_differs_by_location(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    secret = 'AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n'
    (repo / "a.py").write_text(secret)
    (repo / "b.py").write_text(secret)

    previews = {f["path"]: f["match_preview"] for f in find_secrets(repo)["findings"]}

    assert len(previews) == 2
    assert previews["a.py"] != previews["b.py"]


def test_match_preview_is_stable_across_scans_of_the_same_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')

    first = find_secrets(repo)["findings"][0]["match_preview"]
    second = find_secrets(repo)["findings"][0]["match_preview"]

    assert first == second, "baseline matching depends on this being deterministic"


def test_a_baseline_written_in_the_old_preview_format_still_matches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    value = "AKIAABCDEFGHIJKLMNOP"
    (repo / "app.py").write_text(f'AWS_KEY = "{value}"\n')
    detected = find_secrets(repo)["findings"][0]
    legacy_baseline = [
        {
            "path": "app.py",
            "pattern": detected["pattern"],
            "match_preview": f"{value[:4]}{'*' * 4}...{value[-4:]}",
        }
    ]

    findings = find_secrets(repo, baseline=legacy_baseline)["findings"]

    assert findings[0]["accepted"] is True


def test_a_baseline_written_in_the_new_preview_format_matches(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text('AWS_KEY = "AKIAABCDEFGHIJKLMNOP"\n')
    detected = find_secrets(repo)["findings"][0]
    baseline = [
        {"path": "app.py", "pattern": detected["pattern"], "match_preview": detected["match_preview"]}
    ]

    findings = find_secrets(repo, baseline=baseline)["findings"]

    assert findings[0]["accepted"] is True
