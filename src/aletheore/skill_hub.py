import re
from pathlib import Path

from aletheore.user_paths import user_home

_SAFE_SKILL_NAME = re.compile(r"[A-Za-z0-9_-]+")


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
