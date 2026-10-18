import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

from aletheore.user_paths import user_home

_SAFE_SKILL_NAME = re.compile(r"[A-Za-z0-9_-]+")
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


@dataclass
class CuratedSkill:
    skill_id: str
    title: str
    description: str
    content: str
    source_url: str | None
    license: str | None


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
        return CuratedSkill(
            skill_id=frontmatter["skill_id"],
            title=frontmatter["title"],
            description=frontmatter["description"],
            content=body,
            source_url=frontmatter["source_url"],
            license=frontmatter["license"],
        )
    except Exception as exc:
        logger.warning("Skipping malformed bundled skill file %s: %s", path, exc)
        return None


def list_bundled_categories() -> list[str]:
    categories = set()
    for path in _iter_bundled_skill_dirs():
        skill = _parse_skill_file(path)
        if skill is not None:
            categories.add(skill.skill_id.split("/", 1)[0])
    return sorted(categories)


def list_bundled_skills(category: str | None = None) -> list[CuratedSkillMeta]:
    metas = []
    for path in _iter_bundled_skill_dirs():
        skill = _parse_skill_file(path)
        if skill is None:
            continue
        skill_category = skill.skill_id.split("/", 1)[0]
        if category is not None and skill_category != category:
            continue
        metas.append(
            CuratedSkillMeta(
                skill_id=skill.skill_id,
                category=skill_category,
                title=skill.title,
                description=skill.description,
            )
        )
    return metas


def get_bundled_skill(skill_id: str) -> CuratedSkill | None:
    for path in _iter_bundled_skill_dirs():
        if f"{path.parent.parent.name}/{path.parent.name}" == skill_id:
            return _parse_skill_file(path)
    return None


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


@dataclass
class Skill:
    skill_id: str
    title: str
    description: str
    content: str
    source_url: str | None
    license: str | None


def list_skills(category: str | None = None) -> list[SkillMeta]:
    metas = [
        SkillMeta(
            skill_id=meta.skill_id,
            category=meta.category,
            title=meta.title,
            description=meta.description,
        )
        for meta in list_bundled_skills(category)
    ]
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
    return Skill(
        skill_id=curated.skill_id,
        title=curated.title,
        description=curated.description,
        content=curated.content,
        source_url=curated.source_url,
        license=curated.license,
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
