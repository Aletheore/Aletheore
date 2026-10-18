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
