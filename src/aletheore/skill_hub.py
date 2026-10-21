import dataclasses
import functools
import json
import logging
import math
import re
import time
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

try:  # optional: only the hook path benefits, and json is the fallback
    import orjson as _orjson
except ImportError:  # pragma: no cover - exercised only where orjson is absent
    _orjson = None


def _loads_json(raw: bytes | str):
    return _orjson.loads(raw) if _orjson is not None else json.loads(raw)

def user_home() -> Path:
    """Path.home() with the package's container-safe fallback, imported lazily.

    Kept as a module-level function (not an import) so tests can still monkeypatch
    `skill_hub.user_home`, while the hook avoids the ~20 ms user_paths import.
    """
    from aletheore.user_paths import user_home as _user_home

    return _user_home()


_SAFE_SKILL_NAME = re.compile(r"[A-Za-z0-9_-]+")
# One path segment of a bundled skill id ("category" or "skill-name"). Must start with a
# letter or digit, which also rules out "." and "..".
_SAFE_ID_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")

# The shelves the curated catalog is organised into, in display order. A skill's shelf is
# the `shelf` key in its frontmatter; the two hand-written seed skills predate shelves and
# are placed by category.
SHELF_TITLES = {
    "build": "Build",
    "quality": "Quality",
    "ship-operate": "Ship & Operate",
    "ai-agents": "AI & Agents",
    "product-design": "Product & Design",
    "content-media": "Content & Media",
}
_SEED_SHELF = {"brainstorming": "build", "code-review": "quality"}

# The instruction that makes an agent actually use Hallbook. Tested end to end with a real agent
# session: with this in the project's agent instructions file the agent searched, loaded a skill
# and followed it; without it the same agent never touched the tools. Printed by
# `aletheore hallbook snippet` for the user to paste; Aletheore never writes it into a project.
AGENT_INSTRUCTION_SNIPPET = (
    "## Hallbook skills\n"
    "\n"
    "For a non-trivial task that a specialist skill might cover (debugging, review, testing, planning, "
    "security, an API or platform integration, SEO, design), call `aletheore_find_skill` once with a "
    "one-sentence task description.\n"
    "If it returns a skill, follow it and adapt it to this project; if the match is low or none, carry on "
    "without it and do not keep searching. Skip it for trivial edits.\n"
    "Load a listed candidate with `aletheore_get_skill`; use `aletheore_search_skills` only to browse more "
    "matches. Fetch files the skill points at with `aletheore_get_skill_file`.\n"
)

# find_skill confidence thresholds, calibrated by search-lab/calibrate_confidence.py (on the hallbook/search-lab branch) on the DEV
# half of data/queries_stage4.jsonl (skill ids whose md5 hash is even). A top result is "high"
# only when its score clears FIND_SKILL_MIN_SCORE and it is clearly ahead of the runner-up, by
# either an absolute gap or a ratio. These values measured 87.7% top-1 precision (target or an
# acceptable duplicate) at 45.8% coverage on DEV.
FIND_SKILL_MIN_SCORE = 34.0
FIND_SKILL_MIN_GAP = 10.0
FIND_SKILL_MIN_RATIO = 1.3
FIND_SKILL_MAX_CONTENT_CHARS = 14_000
# UserPromptSubmit hook limits. A prompt shorter than HOOK_MIN_WORDS is a fragment, not a task;
# a prompt longer than HOOK_MAX_PROMPT_CHARS is usually a pasted log, so only its first
# HOOK_TRUNCATE_CHARS characters are searched (the task is normally stated at the top).
HOOK_MODES = ("hint", "inject")
HOOK_MIN_WORDS = 5
HOOK_MAX_PROMPT_CHARS = 6_000
HOOK_TRUNCATE_CHARS = 800
# The medium hint tier is deliberately looser than find_skill's "high" bar: a hint is about
# 180 characters, so a plausible second-place match is worth showing. The score floor drops
# near-zero keyword accidents; 1.05 is the smallest non-vacuous runner-up guard (at 1.0 the
# second result can tie or nearly tie and the comparison says nothing). Calibrated by
# search-lab/calibrate_hint.py (on the hallbook/search-lab branch) on the DEV half of data/queries_stage4.jsonl: hit@2 82.4% at an
# 86.1% fire rate, with 10% of the non-task chatty prompts firing.
HINT_MIN_SCORE = 10.0
HINT_MIN_RATIO = 1.05
HOOK_ACKNOWLEDGEMENTS = frozenset(
    {
        "ok", "okay", "k", "thanks", "thank", "thx", "yes", "yeah", "yep", "sure",
        "continue", "go", "ahead", "proceed", "do", "it", "please", "cool", "great",
        "perfect", "awesome", "nice", "got", "sounds", "good", "fine", "alright",
        "right", "no", "nope", "stop", "wait", "cancel", "now", "that", "worked",
    }
)

FIND_SKILL_HIGH_NOTE = (
    "Apply this guidance to the current project. Examples use sample values; do not copy them blindly. "
    "Skip steps that do not apply."
)
FIND_SKILL_LOW_NOTE = (
    "No clear match. If none of these fits, continue without a skill and do not search again for the same need."
)
FIND_SKILL_NONE_NOTE = "No relevant skill. Continue without one."

# Companion files (scripts, references, assets) are returned by get_skill_file one at a time.
_MAX_LISTED_FILES = 200
_MAX_FILE_BYTES = 400_000
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n(.*)\Z", re.DOTALL)
_REQUIRED_FRONTMATTER_KEYS = ("skill_id", "title", "description", "source_url", "license")

logger = logging.getLogger(__name__)


def custom_skills_dir() -> Path:
    return user_home() / ".config" / "aletheore" / "custom_skills"


def sanitize_skill_name(name: str) -> str:
    """Return `name` unchanged if it is a safe skill name, else raise ValueError.

    Path separators and null bytes are rejected before the regex check so they
    can never reach a filesystem path. The regex excludes '.', so '.' and '..'
    are rejected too.
    """
    if "/" in name or "\\" in name or "\0" in name or not _SAFE_SKILL_NAME.fullmatch(name):
        raise ValueError(f"Invalid skill name: {name!r}")
    return name


def write_custom_skill_file(name: str, content: str) -> Path:
    safe = sanitize_skill_name(name)
    directory = custom_skills_dir()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{safe}.md"
    path.write_text(content, encoding="utf-8")
    return path


def read_custom_skill_file(name: str) -> str | None:
    path = custom_skills_dir() / f"{sanitize_skill_name(name)}.md"
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8")


def list_custom_skill_names() -> list[str]:
    directory = custom_skills_dir()
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.md"))


@dataclass
class CuratedSkillMeta:
    skill_id: str
    category: str
    title: str
    description: str
    shelf: str = ""
    license: str = ""
    # What the skill needs from the agent beyond reading it, e.g. "subagents",
    # "mcp:figma", "browser", "scripts", "tool:ffmpeg". Empty means nothing special.
    requires: list[str] = field(default_factory=list)
    quality: int = 0
    # Search-only tokens, stored in _index.json so a cold search never has to read or
    # re-tokenise every body: one space-joined token string per weighted field.
    search_tokens: dict[str, str] = field(default_factory=dict)


@dataclass
class CuratedSkill:
    skill_id: str
    title: str
    description: str
    content: str
    source_url: str | None
    license: str | None
    shelf: str = ""
    requires: list[str] = field(default_factory=list)
    quality: int = 0


def _provenance(frontmatter: dict) -> dict:
    # Curated skills carry an `aletheore:` provenance mapping (origin, vetting, requires...).
    provenance = frontmatter.get("aletheore")
    return provenance if isinstance(provenance, dict) else {}


def bundled_skills_dir() -> Path:
    return Path(__file__).parent / "skills"


def _iter_bundled_skill_dirs() -> Iterator[Path]:
    return bundled_skills_dir().glob("*/*/SKILL.md")


def _parse_skill_file(path: Path) -> CuratedSkill | None:
    # Imported here, not at module scope: the hook path never parses SKILL.md files, and
    # yaml is one of the heavier imports on a cold `python -m aletheore.hallbook_hook`.
    import yaml

    expected_skill_id = f"{path.parent.parent.name}/{path.parent.name}"
    try:
        text = path.read_text(encoding="utf-8")
        match = _FRONTMATTER.match(text)
        if not match:
            raise ValueError(f"no frontmatter block found in {path}")
        frontmatter = yaml.safe_load(match.group(1))
        body = match.group(2)
        if not isinstance(frontmatter, dict):
            raise ValueError(f"frontmatter in {path} is not a mapping")
        missing = [key for key in _REQUIRED_FRONTMATTER_KEYS if key not in frontmatter]
        if missing:
            raise ValueError(f"frontmatter in {path} is missing required keys: {missing}")
        if frontmatter["skill_id"] != expected_skill_id:
            raise ValueError(
                f"skill_id {frontmatter['skill_id']!r} in {path} does not match "
                f"expected {expected_skill_id!r}"
            )
        provenance = _provenance(frontmatter)
        requires = provenance.get("requires")
        quality = provenance.get("quality")
        category = frontmatter["skill_id"].split("/", 1)[0]
        return CuratedSkill(
            skill_id=frontmatter["skill_id"],
            title=frontmatter["title"],
            description=frontmatter["description"],
            content=body,
            source_url=frontmatter["source_url"],
            license=frontmatter["license"],
            shelf=str(frontmatter.get("shelf") or _SEED_SHELF.get(category, "other")),
            requires=[str(item) for item in requires] if isinstance(requires, list) else [],
            quality=quality if isinstance(quality, int) else 0,
        )
    except Exception as exc:
        logger.warning("Skipping malformed bundled skill file %s: %s", path, exc)
        return None


_INDEX_FILE = "_index.json"
_INTENTS_FILE = "_intents.json"
_INDEX_VERSION = 3


def _load_intents(directory: Path) -> dict[str, list[str]]:
    """Everyday phrasings per skill, written by `gen_intents.py` (S1). Absent is fine."""
    try:
        data = json.loads((directory / _INTENTS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {key: [str(item) for item in value] for key, value in data.items() if isinstance(value, list)}


def _scan_index(directory: Path) -> tuple[CuratedSkillMeta, ...]:
    intents = _load_intents(directory)
    metas = []
    for path in sorted(directory.glob("*/*/SKILL.md")):
        skill = _parse_skill_file(path)
        if skill is None:
            continue
        texts = {
            "name": skill.skill_id.split("/", 1)[-1].replace("-", " ").replace("_", " "),
            "title": skill.title,
            "description": skill.description,
            "intents": " ".join(intents.get(skill.skill_id, [])),
            "headings": "\n".join(line for line in skill.content.splitlines() if line.lstrip().startswith("#")),
            "body": skill.content[:1500],
            "category": skill.skill_id.split("/", 1)[0].replace("-", " "),
        }
        metas.append(
            CuratedSkillMeta(
                skill_id=skill.skill_id,
                category=skill.skill_id.split("/", 1)[0],
                title=skill.title,
                description=skill.description,
                shelf=skill.shelf,
                license=skill.license or "",
                requires=skill.requires,
                quality=skill.quality,
                search_tokens={name: " ".join(_tokens(text)) for name, text in texts.items()},
            )
        )
    return tuple(metas)


def _read_index_file(path: Path) -> tuple[CuratedSkillMeta, ...] | None:
    try:
        data = _loads_json(path.read_bytes())
        if data.get("version") != _INDEX_VERSION:
            return None
        return tuple(CuratedSkillMeta(**entry) for entry in data["skills"])
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


@functools.lru_cache(maxsize=8)
def _bundled_index(skills_dir: str) -> tuple[CuratedSkillMeta, ...]:
    """Metadata for every bundled skill, loaded once per process per directory.

    With over a thousand skills, parsing every SKILL.md's frontmatter on each cold start costs
    about a second and a half, nearly all of it YAML. The catalog therefore ships a prebuilt
    `_index.json` (written by rebuild_bundled_index, checked against the files by a test);
    a directory without one (or with an unreadable one) is scanned instead. The result is
    cached because bundled files never change while the process runs; tests that swap the
    directory get their own entry because the key is the directory path.
    """
    directory = Path(skills_dir)
    indexed = _read_index_file(directory / _INDEX_FILE)
    return indexed if indexed is not None else _scan_index(directory)


def rebuild_bundled_index(skills_dir: Path | None = None) -> int:
    """Rescan the skill folders and rewrite `_index.json`. Returns the number of skills indexed.

    Run this after adding, removing or editing a bundled skill; a test fails if the file is stale.
    """
    directory = skills_dir if skills_dir is not None else bundled_skills_dir()
    metas = _scan_index(directory)
    payload = {"version": _INDEX_VERSION, "skills": [dataclasses.asdict(meta) for meta in metas]}
    (directory / _INDEX_FILE).write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    _bundled_index.cache_clear()
    _search_index.cache_clear()
    return len(metas)


def _index() -> tuple[CuratedSkillMeta, ...]:
    return _bundled_index(str(bundled_skills_dir()))


def list_bundled_categories() -> list[str]:
    return sorted({meta.category for meta in _index()})


def list_bundled_skills(category: str | None = None) -> list[CuratedSkillMeta]:
    return [meta for meta in _index() if category is None or meta.category == category]


def _bundled_skill_md_path(skill_id: str) -> Path | None:
    """The SKILL.md for a bundled skill id, or None. Never builds a path from unvalidated text."""
    parts = skill_id.split("/")
    if len(parts) != 2 or not all(_SAFE_ID_SEGMENT.fullmatch(part) for part in parts):
        return None
    path = bundled_skills_dir() / parts[0] / parts[1] / "SKILL.md"
    return path if path.is_file() else None


def get_bundled_skill(skill_id: str) -> CuratedSkill | None:
    path = _bundled_skill_md_path(skill_id)
    return _parse_skill_file(path) if path is not None else None


class SkillFileError(Exception):
    """Raised by read_skill_file for a path that is unsafe, missing, binary or not a file."""


def list_skill_files(skill_id: str) -> tuple[list[str], int]:
    """Companion files of a bundled skill (scripts, references, assets) as relative paths.

    Returns (first _MAX_LISTED_FILES paths in sorted order, total count). SKILL.md itself and
    the top-level license files are not listed: the first is what get_skill returns, and the
    license text is attribution, not working material.
    """
    skill_md = _bundled_skill_md_path(skill_id)
    if skill_md is None:
        return [], 0
    root = skill_md.parent
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue  # wheels do not ship dotfiles, so never advertise one
        if rel == "SKILL.md" or (("/" not in rel) and rel.upper().startswith("LICENSE")):
            continue
        files.append(rel)
    return files[:_MAX_LISTED_FILES], len(files)


def read_skill_file(skill_id: str, rel_path: str) -> str:
    """Text of one companion file of a bundled skill, by path relative to the skill folder.

    The path is confined to the skill's own folder: absolute paths, '..' segments, NUL bytes
    and anything that resolves outside it (including through a symlink) are refused. Binary
    files are refused. Files over _MAX_FILE_BYTES are returned truncated with a note.
    """
    if skill_id.startswith(_CUSTOM_PREFIX):
        raise SkillFileError("custom skills are a single SKILL.md and have no companion files")
    skill_md = _bundled_skill_md_path(skill_id)
    if skill_md is None:
        raise SkillNotFoundError(skill_id)
    if not rel_path or "\0" in rel_path or rel_path.startswith(("/", "\\")):
        raise SkillFileError(f"invalid file path: {rel_path!r}")
    posix = PurePosixPath(rel_path.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or re.match(r"^[A-Za-z]:", rel_path):
        raise SkillFileError(f"invalid file path: {rel_path!r}")
    root = skill_md.parent.resolve()
    target = (root / posix).resolve()
    if not target.is_relative_to(root) or not target.is_file():
        raise SkillFileError(f"no such file in {skill_id}: {rel_path}")
    data = target.read_bytes()[: _MAX_FILE_BYTES + 1]
    truncated = len(data) > _MAX_FILE_BYTES
    if b"\0" in data[:8192]:
        raise SkillFileError(f"{rel_path} is not a text file")
    try:
        # A truncated read can cut a multi-byte character in half; that is not "binary".
        text = data[:_MAX_FILE_BYTES].decode("utf-8", errors="ignore" if truncated else "strict")
    except UnicodeDecodeError as exc:
        raise SkillFileError(f"{rel_path} is not a text file") from exc
    if truncated:
        text += f"\n\n[truncated: file is larger than {_MAX_FILE_BYTES} bytes]"
    return text


# --- Unified public API: the only surface callers (MCP tools, CLI) should use. ---

_CUSTOM_PREFIX = "custom/"
_CUSTOM_CATEGORY = "custom"


class SkillNotFoundError(Exception):
    """Raised by get_skill when no curated or custom skill matches the id."""


@dataclass
class SkillMeta:
    skill_id: str
    category: str
    title: str
    description: str
    shelf: str = ""
    license: str = ""
    requires: list[str] = field(default_factory=list)
    quality: int = 0
    # Skills collapsed under this one by the curation file (same job, different source). Empty for most.
    alternates: list[str] = field(default_factory=list)


@dataclass
class Skill:
    skill_id: str
    title: str
    description: str
    content: str
    source_url: str | None
    license: str | None
    shelf: str = ""
    requires: list[str] = field(default_factory=list)
    # Companion files (relative paths, fetch each with read_skill_file): at most
    # _MAX_LISTED_FILES are listed; files_total is the real count.
    files: list[str] = field(default_factory=list)
    files_total: int = 0


def _meta(curated: CuratedSkillMeta) -> SkillMeta:
    return SkillMeta(
        skill_id=curated.skill_id,
        category=curated.category,
        title=curated.title,
        description=curated.description,
        shelf=curated.shelf,
        license=curated.license,
        requires=list(curated.requires),
        quality=curated.quality,
    )


def list_overview() -> list[dict]:
    """The catalog's shape without its contents: shelves, their categories, skill counts.

    With over a thousand skills a flat list is not something an agent can read, so this is
    the entry point: pick a category here, then list_skills(category) or search_skills.
    """
    by_shelf: dict[str, Counter] = defaultdict(Counter)
    for meta in _index():
        by_shelf[meta.shelf][meta.category] += 1
    order = [shelf for shelf in SHELF_TITLES if shelf in by_shelf]
    order += sorted(shelf for shelf in by_shelf if shelf not in SHELF_TITLES)
    overview = [
        {
            "shelf": shelf,
            "title": SHELF_TITLES.get(shelf, shelf.replace("-", " ").title()),
            "skills": sum(by_shelf[shelf].values()),
            "categories": [{"category": name, "skills": count} for name, count in sorted(by_shelf[shelf].items())],
        }
        for shelf in order
    ]
    custom_count = len(list_custom_skill_names())
    if custom_count:
        overview.append(
            {
                "shelf": _CUSTOM_CATEGORY,
                "title": "Your skills",
                "skills": custom_count,
                "categories": [{"category": _CUSTOM_CATEGORY, "skills": custom_count}],
            }
        )
    return overview


# Words that carry no signal in a task description ("fix the flaky test for my checkout").
_STOPWORDS = frozenset(
    "a an and are as at be by can could do does for from had has have how i if in into is it its me "
    "my of on or our should so some that the their them then there these this to up us use using want was "
    "we were what when where which while who why will with you your".split()
)

# Longest suffixes first, so "optimization"/"optimize"/"optimizing" all fold to "optimiz".
_STEM_SUFFIXES = (
    ("ization", "iz"),
    ("isation", "iz"),
    ("ational", "at"),
    ("ations", "at"),
    ("ation", "at"),
    ("ition", "it"),
    ("tion", "t"),
    ("ments", ""),
    ("ment", ""),
    ("ings", ""),
    ("ing", ""),
    ("edly", ""),
    ("ies", "y"),
    ("sses", "ss"),
    ("ize", "iz"),
    ("ise", "iz"),
    ("en", ""),
    ("ed", ""),
    ("ly", ""),
)


def _stem(token: str) -> str:
    # Applied to both sides so related forms agree: write/writing, debug/debugging,
    # test/tests/tested, plan/plans, deploy/deployment/deploying all fold together.
    # It does not need to produce real words, only the same string for related ones.
    word = token
    if len(word) <= 3:
        return word
    for suffix, replacement in _STEM_SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            word = word[: -len(suffix)] + replacement
            break
    if len(word) > 4 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    if len(word) > 3 and word.endswith("e"):
        word = word[:-1]
    if len(word) > 3 and word[-1] == word[-2] and word[-1] not in "lsz":
        word = word[:-1]
    return word


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1 and t not in _STOPWORDS]


_SEARCH_FIELDS = ("name", "title", "description", "intents", "headings", "body", "category")

# Tuned by coordinate ascent on the DEV half of the benchmark in search-lab/lab_rankers.py (on the hallbook/search-lab branch).
_RANK_WEIGHTS = {
    "name": 3.0,
    "title": 1.08,
    "description": 2.94,
    "intents": 4.9,
    "headings": 0.6,
    "body": 0.7,
    "category": 2.1,
    "coverage": 3.92,
    "name_hit": 2.8,
    "name_cov": 2.0,
    "phrase": 1.26,
    "prefix": 0.6,
    "quality": 0.07,
    "k1": 1.2,
    "b": 0.75,
}


class _RankIndex:
    """BM25F over seven weighted fields, built from the metadata in _index.json.

    The full inverted index used to be built eagerly, which cost ~1.2 s on a 1,100-skill
    catalog. A UserPromptSubmit hook cannot afford that. The index now keeps the per-document
    token strings and computes postings only for the tokens (and prefix expansions) in the query:
    the ranking is identical, but a cold search pays for one query, not for every token in the
    catalog. The old eager structure is kept behind the same attribute names where it is cheap.
    """

    def __init__(self, metas: tuple[CuratedSkillMeta, ...]) -> None:
        self.metas = metas
        self.ids = [meta.skill_id for meta in metas]
        self.categories = [meta.category for meta in metas]
        self.quality = [meta.quality for meta in metas]
        self.texts: list[tuple[str, ...]] = []
        self.joined: list[str] = []
        self.doc_len: list[dict[str, int]] = []
        self.name_sizes: list[int] = []
        self.prefix_tokens: dict[str, set[str]] | None = None
        for meta in metas:
            texts = meta.search_tokens or {
                "name": meta.skill_id.split("/", 1)[-1].replace("-", " ").replace("_", " "),
                "title": meta.title,
                "description": meta.description,
                "intents": "",
                "headings": "",
                "body": "",
                "category": meta.category.replace("-", " "),
            }
            fields = tuple(texts[field] for field in _SEARCH_FIELDS)
            self.texts.append(fields)
            # Token-boundary-safe concatenation for exact-token presence checks.
            self.joined.append(" " + " ".join(fields) + " ")
            self.doc_len.append(
                {field: (0 if not text else text.count(" ") + 1) for field, text in zip(_SEARCH_FIELDS, fields)}
            )
            name = fields[0]
            self.name_sizes.append(len(set(name.split())) if name else 0)
        self.n = len(metas)
        self.avg_len = {
            field: (sum(lengths[field] for lengths in self.doc_len) / self.n if self.n else 0.0)
            for field in _SEARCH_FIELDS
        }
        b = _RANK_WEIGHTS["b"]
        self.norms = [
            [
                1 - b + b * (lengths[field] / self.avg_len[field] if self.avg_len[field] else 1.0)
                for field in _SEARCH_FIELDS
            ]
            for lengths in self.doc_len
        ]

    def _ensure_prefix_tokens(self) -> None:
        """Build the prefix map on first use; the hook path (prefix=False) never needs it."""
        if self.prefix_tokens is not None:
            return
        vocab: set[str] = set()
        for texts in self.texts:
            for text in texts:
                vocab.update(text.split())
        prefix: dict[str, set[str]] = defaultdict(set)
        for token in vocab:
            if len(token) >= 5:
                prefix[token[:5]].add(token)
        self.prefix_tokens = prefix

    def rank_scored(
        self,
        query_tokens: list[str],
        category: str | None,
        limit: int,
        min_matches: int = 1,
        prefix: bool = True,
        phrase: bool = True,
    ) -> list[tuple[CuratedSkillMeta, float]]:
        weights = _RANK_WEIGHTS
        field_weights = [weights[field] for field in _SEARCH_FIELDS]
        k1 = weights["k1"]
        needed = set(query_tokens)
        if prefix:
            self._ensure_prefix_tokens()
            assert self.prefix_tokens is not None
            for token in query_tokens:
                if len(token) >= 5:
                    for other in self.prefix_tokens.get(token[:5], ()):
                        if other != token and other.startswith(token):
                            needed.add(other)
        needed_tokens = tuple(needed)
        needles = {token: f" {token} " for token in needed_tokens}
        candidates: list[int] = []
        document_frequency = {token: 0 for token in needed_tokens}
        for doc_index, joined in enumerate(self.joined):
            matched_tokens = 0
            for token in needed_tokens:
                if needles[token] in joined:
                    document_frequency[token] += 1
                    matched_tokens += 1
            if matched_tokens >= min_matches:
                candidates.append(doc_index)
        if not candidates:
            return []
        idf = {
            token: math.log(1 + self.n / (1 + document_frequency[token]))
            for token in needed_tokens
        }
        postings: dict[str, dict[int, tuple[int, ...]]] = {token: {} for token in needed_tokens}
        doc_tokens: dict[int, list[list[str]]] = {}
        for doc_index in candidates:
            fields = [text.split() for text in self.texts[doc_index]]
            doc_tokens[doc_index] = fields
            for token in needed_tokens:
                row = tuple(tokens.count(token) for tokens in fields)
                if any(row):
                    postings[token][doc_index] = row
        term_maps: list[tuple[str, dict[int, list[float]]]] = []
        matching: set[int] = set()
        for token in query_tokens:
            merged: dict[int, list[float]] = {}
            for doc_index, tfs in postings[token].items():
                merged[doc_index] = list(tfs)
            if prefix and len(token) >= 5:
                for other in needed_tokens:
                    if other == token or not other.startswith(token):
                        continue
                    for doc_index, tfs in postings[other].items():
                        current = merged.get(doc_index)
                        if current is None:
                            current = merged[doc_index] = [0.0] * len(_SEARCH_FIELDS)
                        for position, value in enumerate(tfs):
                            current[position] += weights["prefix"] * value
            if merged:
                matching.update(merged)
            term_maps.append((token, merged))
        if not matching:
            return []
        query_pairs = list(zip(query_tokens, query_tokens[1:]))
        n_terms = len(query_tokens)
        scored: list[tuple[CuratedSkillMeta, float]] = []
        for doc_index in matching:
            if category is not None and self.categories[doc_index] != category:
                continue
            score = 0.0
            matched = 0
            norms = self.norms[doc_index]
            for token, merged in term_maps:
                tfs = merged.get(doc_index)
                if not tfs:
                    continue
                matched += 1
                weighted_tf = 0.0
                for position, value in enumerate(tfs):
                    if value:
                        weighted_tf += field_weights[position] * value / norms[position]
                if weighted_tf:
                    score += idf.get(token, 0.0) * weighted_tf * (k1 + 1.0) / (k1 + weighted_tf)
            score += weights["coverage"] * (matched / n_terms)
            name_tokens = doc_tokens[doc_index][0]
            name_hits = [token for token in query_tokens if token in name_tokens]
            if name_hits:
                score += weights["name_hit"] * sum(idf.get(token, 0.0) for token in name_hits)
                if self.name_sizes[doc_index]:
                    score += weights["name_cov"] * (len(set(name_hits)) / self.name_sizes[doc_index])
            if phrase and query_pairs:
                bigrams: set[tuple[str, str]] = set()
                for tokens in doc_tokens[doc_index]:
                    bigrams.update(zip(tokens, tokens[1:]))
                for left, right in query_pairs:
                    if (left, right) in bigrams:
                        score += weights["phrase"] * (
                            idf.get(left, 0.0) + idf.get(right, 0.0)
                        ) / 2.0
            score += weights["quality"] * self.quality[doc_index]
            scored.append((self.metas[doc_index], score))
        return scored


@functools.lru_cache(maxsize=8)
def _search_index(skills_dir: str) -> _RankIndex:
    """The BM25F index, built once per process per skills directory."""
    return _RankIndex(_bundled_index(skills_dir))


_CURATION_FILE = "_curation.json"


@functools.lru_cache(maxsize=4)
def _curation(skills_dir: str) -> tuple[dict[str, str], dict[str, list[str]]]:
    """(redirect, alternates_of): every hidden or collapsed skill id -> its canonical id, and canonical -> its collapsed ids.

    The file is optional. An entry is ignored unless both ids exist, so a stale file can never hide a real skill by mistake.
    Hidden duplicates are left out of search and lookup results; collapsed alternates are shown under their canonical skill.
    Every skill stays loadable by id with get_skill.
    """
    try:
        data = json.loads((Path(skills_dir) / _CURATION_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}, {}
    known = {meta.skill_id for meta in _bundled_index(skills_dir)}
    redirect: dict[str, str] = {}
    alternates_of: dict[str, list[str]] = {}
    for kind in ("alternates", "hidden"):
        for skill_id, canonical in (data.get(kind) or {}).items():
            if skill_id in known and canonical in known and skill_id != canonical:
                redirect[skill_id] = canonical
                if kind == "alternates":
                    alternates_of.setdefault(canonical, []).append(skill_id)
    return redirect, alternates_of


def search_skills_scored(
    query: str,
    category: str | None = None,
    limit: int = 10,
    min_matches: int = 1,
    prefix: bool = True,
    phrase: bool = True,
) -> list[tuple[SkillMeta, float]]:
    """The same ranking as search_skills, with the raw score for each result.

    `min_matches`, `prefix` and `phrase` are candidate-pruning aids for callers that
    only need the head of the ranking (the prompt hook). Their defaults are the full
    ranking.
    """
    query_tokens = list(dict.fromkeys(_tokens(query)))
    if not query_tokens or limit <= 0:
        return []
    min_matches = min(min_matches, len(query_tokens))
    index = _search_index(str(bundled_skills_dir()))
    custom_names = list_custom_skill_names() if (category is None or category == _CUSTOM_CATEGORY) else []
    scored: list[tuple[float, str, SkillMeta]] = []
    for meta, score in index.rank_scored(
        query_tokens,
        category,
        limit * 3 + len(custom_names),
        min_matches=min_matches,
        prefix=prefix,
        phrase=phrase,
    ):
        if score:
            scored.append((score, meta.skill_id, _meta(meta)))
    if category is None or category == _CUSTOM_CATEGORY:
        for name in custom_names:
            name_tokens = _tokens(name.replace("-", " ").replace("_", " "))
            matched = {token for token in query_tokens if token in name_tokens}
            # A custom skill is the user's own, unvetted text. It can be found by
            # name, but its score is capped under the "high confidence" bar so it
            # is never handed over as a confident answer or injected by the hook.
            score = min(20.0 * len(matched), FIND_SKILL_MIN_SCORE - 1.0)
            if score:
                skill_id = f"{_CUSTOM_PREFIX}{name}"
                scored.append(
                    (score, skill_id, SkillMeta(skill_id=skill_id, category=_CUSTOM_CATEGORY, title=name, description=""))
                )
    scored.sort(key=lambda item: (-item[0], item[1]))
    redirect, alternates_of = _curation(str(bundled_skills_dir()))
    if redirect:
        by_id = {meta.skill_id: meta for meta in _bundled_index(str(bundled_skills_dir()))}
        merged: dict[str, tuple[float, SkillMeta]] = {}
        for score, skill_id, meta in scored:
            canonical = redirect.get(skill_id, skill_id)
            if canonical != skill_id and canonical in by_id:
                meta = _meta(by_id[canonical])
            if canonical not in merged:  # scored is best first, so the first hit carries the best score
                merged[canonical] = (score, meta)
        scored = [(score, canonical, meta) for canonical, (score, meta) in merged.items()]
        for _, canonical, meta in scored:
            meta.alternates = sorted(alternates_of.get(canonical, []))
    return [(meta, score) for score, _, meta in scored[:limit]]


def search_skills(query: str, category: str | None = None, limit: int = 10) -> list[SkillMeta]:
    """Ranked search over name, title, description, intents, headings and body opening.

    A BM25F-style score (offline, deterministic) combines the fields with separate weights,
    term rarity, length normalisation, query coverage, adjacent-word phrases and prefix matching.
    A skill needs at least one matching word; custom skills are matched on their name only.
    """
    return [meta for meta, _ in search_skills_scored(query, category, limit)]


def list_skills(category: str | None = None) -> list[SkillMeta]:
    metas = [_meta(meta) for meta in list_bundled_skills(category)]
    if category is None or category == _CUSTOM_CATEGORY:
        metas.extend(
            SkillMeta(
                skill_id=f"{_CUSTOM_PREFIX}{name}",
                category=_CUSTOM_CATEGORY,
                title=name,
                description="",
            )
            for name in list_custom_skill_names()
        )
    return metas


def get_skill(skill_id: str) -> Skill:
    if skill_id.startswith(_CUSTOM_PREFIX):
        name = skill_id[len(_CUSTOM_PREFIX):]
        try:
            text = read_custom_skill_file(name)
        except ValueError as exc:
            raise SkillNotFoundError(skill_id) from exc
        if text is None:
            raise SkillNotFoundError(skill_id)
        return Skill(
            skill_id=skill_id,
            title=name,
            description="",
            content=text,
            source_url=None,
            license=None,
        )
    curated = get_bundled_skill(skill_id)
    if curated is None:
        raise SkillNotFoundError(skill_id)
    files, files_total = list_skill_files(skill_id)
    return Skill(
        skill_id=curated.skill_id,
        title=curated.title,
        description=curated.description,
        content=curated.content,
        source_url=curated.source_url,
        license=curated.license,
        shelf=curated.shelf,
        requires=list(curated.requires),
        files=files,
        files_total=files_total,
    )


def _one_line(text: str, limit: int) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) > limit:
        collapsed = collapsed[: limit - 1].rstrip() + "…"
    return collapsed


def _find_skill_identity(meta: SkillMeta) -> dict:
    return {"skill_id": meta.skill_id, "title": meta.title, "description": _one_line(meta.description, 300)}


def _find_skill_candidate(meta: SkillMeta) -> dict:
    return {"skill_id": meta.skill_id, "title": meta.title, "description": _one_line(meta.description, 140)}


def _is_high_confidence(results: list[tuple[SkillMeta, float]]) -> bool:
    """The strict find_skill bar: strong top score and clearly ahead of the runner-up."""
    if not results:
        return False
    top_score = results[0][1]
    second_score = results[1][1] if len(results) > 1 else 0.0
    gap = top_score - second_score
    ratio = top_score / second_score if second_score > 0 else math.inf
    return top_score >= FIND_SKILL_MIN_SCORE and (
        gap >= FIND_SKILL_MIN_GAP or ratio >= FIND_SKILL_MIN_RATIO
    )


def find_skill(task: str, category: str | None = None) -> dict:
    """One call that either hands over the right skill or says to move on.

    Returns "high" confidence with the full skill only when the top result is strong and
    clearly ahead of the runner-up; otherwise "low" with compact candidates, or "none"
    when nothing matched. The thresholds are calibrated offline on the search benchmark.
    """
    results = search_skills_scored(task, category=category, limit=5)
    if not results:
        return {"confidence": "none", "note": FIND_SKILL_NONE_NOTE}

    top_meta, top_score = results[0]
    confident = _is_high_confidence(results)

    result = {
        "confidence": "high" if confident else "low",
        "top": _find_skill_identity(top_meta),
        "alternatives": [_find_skill_candidate(meta) for meta, _ in results[1:4]],
    }
    if not confident:
        result["candidates"] = [_find_skill_candidate(meta) for meta, _ in results[:5]]
        result["note"] = FIND_SKILL_LOW_NOTE
        return result

    try:
        skill = get_skill(top_meta.skill_id)
    except SkillNotFoundError:
        return {"confidence": "none", "note": FIND_SKILL_NONE_NOTE}
    skill_dict = dataclasses.asdict(skill)
    content = skill_dict.get("content") or ""
    if len(content) > FIND_SKILL_MAX_CONTENT_CHARS:
        skill_dict["content"] = content[:FIND_SKILL_MAX_CONTENT_CHARS]
        skill_dict["truncated"] = True
        skill_dict["truncated_note"] = (
            f"Content truncated at {FIND_SKILL_MAX_CONTENT_CHARS} characters; "
            "fetch the rest with aletheore_get_skill."
        )
    result["skill"] = skill_dict
    result["note"] = FIND_SKILL_HIGH_NOTE
    return result


def _hook_prompt_text(prompt: object) -> str | None:
    """The searchable text of a hook prompt, or None when the hook should stay silent."""
    if not isinstance(prompt, str):
        return None
    text = prompt.strip()
    if not text or text.startswith("/"):
        return None
    if len(text) > HOOK_MAX_PROMPT_CHARS:
        text = text[:HOOK_TRUNCATE_CHARS].strip()
    if len(text.split()) < HOOK_MIN_WORDS:
        return None
    words = re.findall(r"[a-z']+", text.lower())
    if words and all(word in HOOK_ACKNOWLEDGEMENTS for word in words):
        return None
    return text


def hook_suggestion(prompt: str, mode: str = "hint") -> str | None:
    """Suggest a Hallbook skill for a Claude Code UserPromptSubmit hook, or None to add nothing.

    Silent by design: only a clear, high-confidence match produces output, so the hook never nudges
    the agent for small talk, slash commands, fragments or ambiguous requests. "hint" prints one short
    pointer; "inject" prints the skill content itself. The caller is responsible for swallowing errors.
    """
    if mode not in HOOK_MODES:
        raise ValueError(f"unknown hook mode: {mode!r}")
    text = _hook_prompt_text(prompt)
    if text is None:
        return None
    if mode == "hint":
        # One search serves both the strict high bar and the medium fallback. For a
        # high match the hint only needs the title, not the whole skill body, so the
        # hook avoids find_skill's extra file read and second search.
        results = search_skills_scored(text, limit=5, min_matches=2, prefix=False, phrase=False)
        if not results:
            return None
        if results[0][1] >= FIND_SKILL_MIN_SCORE:
            # The fast pass only prunes candidates; a near-high top score may still
            # clear the strict bar with the full ranking (prefix and phrase terms).
            full_results = search_skills_scored(text, limit=5)
            if _is_high_confidence(full_results):
                top_meta = full_results[0][0]
                return (
                    f"Hallbook skill that fits this task: {top_meta.skill_id} ({top_meta.title}).\n"
                    "If it applies, load it with aletheore_get_skill and follow it; adapt it to this project."
                )
            results = full_results
        if len(results) < 2:
            return None
        (first, first_score), (second, second_score) = results[0], results[1]
        if first_score < HINT_MIN_SCORE:
            return None
        if second_score > 0 and first_score < HINT_MIN_RATIO * second_score:
            return None
        return (
            f"Hallbook may have a skill for this: {first.skill_id} ({first.title}) "
            f"or {second.skill_id} ({second.title}). "
            "If one applies, load it with aletheore_get_skill; otherwise ignore this."
        )
    result = find_skill(text)
    if result.get("confidence") != "high":
        return None
    top = result.get("top") or {}
    skill = result.get("skill") or {}
    skill_id = top.get("skill_id") or skill.get("skill_id")
    if not skill_id:
        return None
    content = skill.get("content") or ""
    if len(content) > FIND_SKILL_MAX_CONTENT_CHARS:
        content = content[:FIND_SKILL_MAX_CONTENT_CHARS]
    return (
        f"Hallbook skill for this task ({skill_id}), apply it to this project and ignore parts that do not fit:\n\n"
        f"{content}"
    )


def unique_backup_path(name: str, kind: str) -> Path:
    """A fresh, never-overwritten backup path next to a custom skill: <name>.md.<kind>-<timestamp>.bak.

    Backups made by sync and by remove must not clobber one another (a second conflict on the same skill would
    otherwise destroy the first backup), so each gets its own name. `kind` is e.g. "server", "synced", "deleted".
    """
    directory = custom_skills_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    path = directory / f"{name}.md.{kind}-{stamp}.bak"
    counter = 2
    while path.exists():
        path = directory / f"{name}.md.{kind}-{stamp}-{counter}.bak"
        counter += 1
    return path


def add_custom_skill(name: str, content: str) -> str:
    # Local-only safety net, separate from skill_hub_sync's pull-merge
    # backup: if a skill already exists under this name (a user re-adding it,
    # or an agent calling aletheore_add_custom_skill on a name the user
    # already has), keep its current content in <name>.md.bak before
    # overwriting. Same convention as the sync side: one file, overwritten
    # each time, not a history. Skipped when the content is identical, so a
    # repeated add can't replace a meaningful backup with a duplicate.
    existing = read_custom_skill_file(name)
    if existing is not None and existing != content:
        backup_path = custom_skills_dir() / f"{sanitize_skill_name(name)}.md.bak"
        backup_path.write_text(existing, encoding="utf-8")
    write_custom_skill_file(name, content)
    # Local import: skill_hub_sync imports this module, so a top-level import
    # here would be circular. The push never raises, so the return value is
    # unaffected by whether the sync succeeded.
    from aletheore import skill_hub_sync

    # Re-adding a name the user removed offline: the remove left a pending
    # server delete behind, and that must not fire at the next login and then
    # make the sync treat the re-added skill as deleted elsewhere.
    skill_hub_sync._clear_pending_delete(sanitize_skill_name(name))
    skill_hub_sync.push_custom_skill_best_effort(name, content)
    return f"{_CUSTOM_PREFIX}{name}"


def remove_custom_skill(name: str) -> bool:
    """Deletes a local custom skill by name.

    Returns True if a local file was actually deleted, False if there was
    nothing to remove - "already gone" is a valid outcome for a delete, not
    an error, so this never raises SkillNotFoundError the way get_skill does.
    Still raises ValueError for an unsafe name, same as add_custom_skill.
    """
    safe = sanitize_skill_name(name)
    path = custom_skills_dir() / f"{safe}.md"
    existed = path.is_file()
    if existed:
        # Keep a copy: the server delete below cannot be undone from here, and
        # an agent can call this through MCP.
        unique_backup_path(safe, "deleted").write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.unlink()
    # Local import: skill_hub_sync imports this module, so a top-level import
    # here would be circular. Called unconditionally, even when nothing was
    # deleted locally: the server might still hold a stale copy from an
    # earlier, now-forgotten local delete, and delete_custom_skill_best_effort
    # itself handles "nothing to do" cheaply and never raises.
    from aletheore import skill_hub_sync

    skill_hub_sync.delete_custom_skill_best_effort(name)
    return existed
