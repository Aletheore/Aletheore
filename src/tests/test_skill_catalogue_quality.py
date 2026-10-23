"""Quality gates for the bundled Hallbook catalogue (data defects a QA pass found in 0.10.2).

These read the shipped skill folders, so a regression in any skill fails here with the skill id.
"""
import json
import os
import re
from pathlib import Path

import pytest
import yaml

from aletheore import skill_hub
from aletheore.skill_titles import (
    AUTHORED_TITLES,
    TITLE_OVERRIDES,
    WORDS,
    expected_title,
    humanize_title,
)

SKILLS_DIR = skill_hub.bundled_skills_dir()
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_PROVENANCE = re.compile(r"(?m)^aletheore: (\{.*\})$")
_COMMIT_URL = re.compile(r"^https://github\.com/[^/]+/[^/]+/(?:tree|blob)/[0-9a-f]{40}/")
REQUIRED_KEYS = ("name", "description", "skill_id", "title", "source_url", "license", "shelf")
MIN_DESCRIPTION_CHARS = 40

# Files that count as scripts for the `has_scripts` flag: anything with one of these suffixes, or
# anything under a `scripts/` folder, anywhere inside the skill folder (SKILL.md itself excluded).
SCRIPT_SUFFIXES = {".sh", ".bash", ".zsh", ".ps1", ".py", ".js", ".mjs", ".cjs", ".ts", ".rb", ".pl", ".bat", ".cmd"}

# Skills whose frontmatter `name` differs from the folder name because the folder carries an author or
# repository suffix. Ids are public so the folders are not renamed; a new entry here needs a decision.
SUFFIXED_NAMES = {
    "ai-engineering/harness-engineering-github": "harness-engineering",
    "ai-engineering/prompt-optimizer-github": "prompt-optimizer",
    "code-review/code-review-phuryn": "code-review",
    "marketing/ads-agricidaniel": "ads",
    "marketing/marketing-ideas-phuryn": "marketing-ideas",
    "marketing/seo-audit-coreyhaines31": "seo-audit",
    "testing/webapp-testing-github": "webapp-testing",
}


def _skill_dirs():
    return sorted(path.parent for path in SKILLS_DIR.glob("*/*/SKILL.md"))


def _skill_id(skill_dir: Path) -> str:
    return f"{skill_dir.parent.name}/{skill_dir.name}"


def _parse(skill_dir: Path):
    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    match = _FRONTMATTER.match(text)
    assert match, f"{_skill_id(skill_dir)} has no frontmatter block"
    return yaml.safe_load(match.group(1)), match.group(1), match.group(2)


def _provenance(head: str):
    match = _PROVENANCE.search(head)
    return json.loads(match.group(1)) if match else None


def _companion_files(skill_dir: Path):
    for root, _dirs, files in os.walk(skill_dir):
        for name in files:
            rel = Path(root, name).relative_to(skill_dir)
            if rel.as_posix() != "SKILL.md" and not name.startswith("."):
                yield rel


def _has_script_files(skill_dir: Path) -> bool:
    return any(rel.suffix.lower() in SCRIPT_SUFFIXES or "scripts" in rel.parts[:-1] for rel in _companion_files(skill_dir))


def fence_problems(text: str) -> list[str]:
    """Code fences that are left open, or that a block quote ends inside of (so a renderer closes them early)."""
    problems: list[str] = []
    opened: tuple[str, int, int, int] | None = None  # (char, length, quote depth, opening line)
    for number, line in enumerate(text.split("\n"), start=1):
        prefix = re.match(r"^(?: {0,3}> ?)*", line)
        depth = prefix.group(0).count(">")
        rest = line[prefix.end():]
        if opened is not None:
            char, length, quote_depth, start = opened
            if depth < quote_depth:
                problems.append(f"line {number}: block quote ends inside the fence opened on line {start}")
                opened = None
            else:
                closing = re.match(r"^ {0,3}(`{3,}|~{3,})\s*$", rest)
                if closing and closing.group(1)[0] == char and len(closing.group(1)) >= length:
                    opened = None
                continue
        fence = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", rest)
        if fence and not (fence.group(1)[0] == "`" and "`" in fence.group(2)):
            opened = (fence.group(1)[0], len(fence.group(1)), depth, number)
    if opened is not None:
        problems.append(f"the fence opened on line {opened[3]} is never closed")
    return problems


@pytest.mark.parametrize(
    "text,expected",
    [
        ("```bash\nx\n```\n", 0),
        ("````md\n```bash\nx\n```\n````\n", 0),
        ("```md\n```bash\nx\n```\n```\n", 1),  # inner fence ends the outer one early; the last opens a new one
        ("```\nx\n", 1),
        ("> ```bash\n>   x\n\n> ```\n", 2),  # a blank line without the marker ends the quote inside the fence
        ("~~~\nx\n~~~\n", 0),
        ("Use ```` ```textproto```` blocks\n", 0),
    ],
)
def test_fence_checker_catches_the_known_failure_shapes(text, expected):
    assert len(fence_problems(text)) == expected


def test_no_replacement_characters_anywhere_in_the_catalogue():
    offenders = []
    for path in SKILLS_DIR.rglob("*"):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if "�" in text:
            offenders.append(path.relative_to(SKILLS_DIR).as_posix())
    assert not offenders, f"U+FFFD (a garbled character) in: {offenders}"


def test_every_skill_md_has_balanced_code_fences():
    offenders = {}
    for skill_dir in _skill_dirs():
        _head, _fm_text, body = _parse(skill_dir)
        problems = fence_problems(body)
        if problems:
            offenders[_skill_id(skill_dir)] = problems
    assert not offenders, offenders


def test_every_skill_has_complete_frontmatter():
    offenders = {}
    for skill_dir in _skill_dirs():
        frontmatter, _head, _body = _parse(skill_dir)
        missing = [key for key in REQUIRED_KEYS if not str(frontmatter.get(key) or "").strip()]
        if missing:
            offenders[_skill_id(skill_dir)] = missing
    assert not offenders, offenders


def test_every_source_url_is_pinned_to_a_commit():
    offenders = [
        _skill_id(skill_dir)
        for skill_dir in _skill_dirs()
        if not _COMMIT_URL.match(str(_parse(skill_dir)[0]["source_url"]))
    ]
    assert not offenders, f"source_url is not pinned to a 40 character commit: {offenders}"


def test_skill_names_match_their_folders_except_the_known_suffixed_ones():
    mismatched = {}
    for skill_dir in _skill_dirs():
        name = str(_parse(skill_dir)[0]["name"])
        if name != skill_dir.name:
            mismatched[_skill_id(skill_dir)] = name
    assert mismatched == SUFFIXED_NAMES


def test_descriptions_say_something():
    short = {}
    for skill_dir in _skill_dirs():
        description = str(_parse(skill_dir)[0]["description"]).strip()
        if len(description) < MIN_DESCRIPTION_CHARS:
            short[_skill_id(skill_dir)] = description
    assert not short, short


def test_has_scripts_flag_matches_the_script_files_a_skill_ships():
    wrong = {}
    for skill_dir in _skill_dirs():
        head = _parse(skill_dir)[1]
        provenance = _provenance(head)
        if provenance is None:
            continue
        actual = _has_script_files(skill_dir)
        flagged = provenance["vet"]["has_scripts"]
        requires_scripts = "scripts" in provenance.get("requires", [])
        if flagged != actual or requires_scripts != actual:
            wrong[_skill_id(skill_dir)] = {"ships_scripts": actual, "has_scripts": flagged, "requires_scripts": requires_scripts}
    assert not wrong, wrong


def test_titles_follow_the_derivation_rules():
    wrong = {}
    for skill_dir in _skill_dirs():
        skill_id = _skill_id(skill_dir)
        if skill_id in AUTHORED_TITLES:
            continue
        frontmatter = _parse(skill_dir)[0]
        want = expected_title(skill_id, str(frontmatter["name"]))
        if frontmatter["title"] != want:
            wrong[skill_id] = (frontmatter["title"], want)
    assert not wrong, wrong


def test_no_title_leaves_a_mapped_acronym_in_slug_case():
    # "Sql Code Review" is the failure this guards: a title word that is a mapped acronym or product name
    # but is capitalised like an ordinary word.
    plain = {key: key.capitalize() for key in WORDS if WORDS[key] != key.capitalize()}
    offenders = {}
    for skill_dir in _skill_dirs():
        skill_id = _skill_id(skill_dir)
        if skill_id in AUTHORED_TITLES or skill_id in TITLE_OVERRIDES:
            continue
        words = str(_parse(skill_dir)[0]["title"]).split()
        bad = [word for word in words if plain.get(word.lower()) == word]
        if bad:
            offenders[skill_id] = bad
    assert not offenders, offenders


@pytest.mark.parametrize(
    "name,title",
    [
        ("aspnet-core", "ASP.NET Core"),
        ("api-breaking-change-detector", "API Breaking Change Detector"),
        ("sql-code-review", "SQL Code Review"),
        ("gha-security-review", "GitHub Actions Security Review"),
        ("mcp-implementation-security-review", "MCP Implementation Security Review"),
        ("oracle-to-postgres", "Oracle to Postgres"),
        ("to-do", "To Do"),
        ("ios-release-notes", "iOS Release Notes"),
        ("grpc_api_review", "gRPC API Review"),
        ("debugging", "Debugging"),
    ],
)
def test_humanize_title_examples(name, title):
    assert humanize_title(name) == title


def test_title_derivation_is_deterministic():
    assert humanize_title("k8s-manifest-generator") == humanize_title("K8S-Manifest-Generator") == "K8s Manifest Generator"


def test_curation_entries_point_at_real_skills_and_never_chain():
    data = json.loads((SKILLS_DIR / "_curation.json").read_text(encoding="utf-8"))
    known = {_skill_id(skill_dir) for skill_dir in _skill_dirs()}
    redirects = {**data["alternates"], **data["hidden"]}
    for skill_id, canonical in redirects.items():
        assert skill_id in known and canonical in known, (skill_id, canonical)
        assert canonical not in redirects, f"{skill_id} -> {canonical} which is itself collapsed"
    assert not set(data["alternates"]) & set(data["hidden"])


@pytest.mark.parametrize(
    "duplicate,canonical",
    [
        ("infrastructure/azure-resource-visualizer", "platforms/azure-resource-visualizer"),
        ("platforms/qdrant-scaling-data-volume", "platforms/qdrant-scaling"),
        ("platforms/qdrant-vertical-scaling", "platforms/qdrant-scaling"),
        ("platforms/qdrant-tenant-scaling", "platforms/qdrant-scaling"),
        ("platforms/qdrant-sliding-time-window", "platforms/qdrant-scaling"),
        ("platforms/qdrant-horizontal-scaling", "platforms/qdrant-scaling"),
        ("data/migrating-oracle-to-postgres-stored-procedures", "data/creating-oracle-to-postgres-master-migration-plan"),
        ("marketing/marketing-ideas-phuryn", "marketing/marketing-ideas"),
    ],
)
def test_known_duplicates_are_collapsed_but_still_loadable(duplicate, canonical):
    redirect, _alternates = skill_hub._curation(str(SKILLS_DIR))
    assert redirect.get(duplicate) == canonical
    assert skill_hub.get_skill(duplicate).skill_id == duplicate


def test_notice_links_only_to_pinned_commits():
    notice = (SKILLS_DIR / "NOTICE.md").read_text(encoding="utf-8")
    assert "/blob/main/" not in notice and "/tree/main/" not in notice
