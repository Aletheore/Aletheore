import dataclasses
import functools
import json
import logging
import math
import re
from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import yaml

from aletheore.user_paths import user_home

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
    "Before starting a task that a specialist skill might cover (debugging, code review, testing, "
    "planning, security, an API or platform integration, SEO, design), call `aletheore_search_skills` "
    "with a short description of the task, load the best match with `aletheore_get_skill`, and follow it. "
    "If the skill points at other files, fetch them with `aletheore_get_skill_file`.\n"
)

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
_INDEX_VERSION = 1


def _scan_index(directory: Path) -> tuple[CuratedSkillMeta, ...]:
    metas = []
    for path in sorted(directory.glob("*/*/SKILL.md")):
        skill = _parse_skill_file(path)
        if skill is None:
            continue
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
            )
        )
    return tuple(metas)


def _read_index_file(path: Path) -> tuple[CuratedSkillMeta, ...] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
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
    _search_corpus.cache_clear()
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
    "a an and are as at be by do does for from how i if in into is it its me my of on or our so that the "
    "their then there these this to up use using want we what when where which while with you your".split()
)


def _stem(token: str) -> str:
    # A deliberately crude stemmer, applied to both the query and the skill text so the two
    # sides agree: write/writing, debug/debugging, test/tests/tested, plan/plans all fold
    # together. It does not need to produce real words, only the same string for related ones.
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and not token.endswith("ss") and len(token) - len(suffix) >= 3:
            token = token[: -len(suffix)]
            break
    if token.endswith("e") and len(token) > 3:
        token = token[:-1]
    if len(token) > 3 and token[-1] == token[-2] and token[-1] not in "lsz":
        token = token[:-1]
    return token


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 1 and t not in _STOPWORDS]


@functools.lru_cache(maxsize=8)
def _search_corpus(skills_dir: str):
    """Pre-tokenised skills plus an inverse-document-frequency weight per word, built once."""
    docs = []
    document_frequency: Counter = Counter()
    for meta in _bundled_index(skills_dir):
        name_tokens = _tokens(meta.skill_id.split("/", 1)[1].replace("-", " ").replace("_", " "))
        category_tokens = set(_tokens(meta.category.replace("-", " ")))
        text_tokens = set(_tokens(f"{meta.title} {meta.description}"))
        docs.append((meta, name_tokens, category_tokens, text_tokens))
        document_frequency.update(set(name_tokens) | category_tokens | text_tokens)
    total = len(docs)
    idf = {word: math.log(1 + total / (1 + count)) for word, count in document_frequency.items()}
    return tuple(docs), idf


def search_skills(query: str, category: str | None = None, limit: int = 10) -> list[SkillMeta]:
    """Ranked keyword search over skill name, category and description (offline, deterministic).

    Query words are matched against the skill's name (strongest), category and description.
    A skill needs at least one matching word; more matches, name matches and a higher curated
    quality rank it higher. Custom skills are matched on their name only.
    """
    query_tokens = list(dict.fromkeys(_tokens(query)))
    if not query_tokens or limit <= 0:
        return []
    scored: list[tuple[float, str, SkillMeta]] = []
    docs, idf = _search_corpus(str(bundled_skills_dir()))
    for meta, name_tokens, category_tokens, text_tokens in docs:
        if category is not None and meta.category != category:
            continue
        score = 0.0
        for token in query_tokens:
            # Rarer words count for more: "security" says far more than "code" does.
            weight = idf.get(token, 0.0)
            if token in name_tokens:
                score += 5 * weight
            elif any(token in name_token for name_token in name_tokens):
                score += 3 * weight
            if token in category_tokens:
                score += 2 * weight
            if token in text_tokens:
                score += 1 * weight
        if score:
            scored.append((score + 0.3 * meta.quality, meta.skill_id, _meta(meta)))
    if category is None or category == _CUSTOM_CATEGORY:
        for name in list_custom_skill_names():
            name_tokens = _tokens(name.replace("-", " ").replace("_", " "))
            score = sum(5 for token in query_tokens if token in name_tokens)
            if score:
                skill_id = f"{_CUSTOM_PREFIX}{name}"
                scored.append(
                    (score, skill_id, SkillMeta(skill_id=skill_id, category=_CUSTOM_CATEGORY, title=name, description=""))
                )
    scored.sort(key=lambda item: (-item[0], item[1]))
    return [meta for _, _, meta in scored[:limit]]


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
        path.unlink()
    # Local import: skill_hub_sync imports this module, so a top-level import
    # here would be circular. Called unconditionally, even when nothing was
    # deleted locally: the server might still hold a stale copy from an
    # earlier, now-forgotten local delete, and delete_custom_skill_best_effort
    # itself handles "nothing to do" cheaply and never raises.
    from aletheore import skill_hub_sync

    skill_hub_sync.delete_custom_skill_best_effort(name)
    return existed
