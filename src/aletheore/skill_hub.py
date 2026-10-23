import dataclasses
import functools
import json
import logging
import math
import os
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
# Below this score even the best result is a keyword accident. The dev half of the
# benchmark has no task query under 14.2, so a floor here turns weak, scattered
# matches into an honest "none" without losing a real task.
FIND_SKILL_NONE_SCORE = 14.0
# A strong top score still is not a confident answer when the skill covers only a few
# of the task's content words. Requiring four in five keeps the genuinely scattered
# matches at "low" (dev precision 0.851, 63 of 66 correct highs kept).
FIND_SKILL_MIN_COVERAGE = 0.8
FIND_SKILL_MAX_CONTENT_CHARS = 14_000
# UserPromptSubmit hook limits. A prompt shorter than HOOK_MIN_WORDS is a fragment, not a task;
# a prompt longer than HOOK_MAX_PROMPT_CHARS is usually a pasted log, so only its first
# HOOK_TRUNCATE_CHARS characters are searched (the task is normally stated at the top).
HOOK_MODES = ("hint", "inject")
HOOK_MIN_WORDS = 6
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
# A medium hint is only worth showing when the first skill covers half of the prompt's
# content words. Without a floor, a conversational prompt can share one broad word with
# a skill and still produce a hint.
HOOK_MIN_COVERAGE = 0.5
HOOK_ACKNOWLEDGEMENTS = frozenset(
    {
        "ok", "okay", "k", "thanks", "thank", "thx", "yes", "yeah", "yep", "sure",
        "continue", "go", "ahead", "proceed", "do", "it", "please", "cool", "great",
        "perfect", "awesome", "nice", "got", "sounds", "good", "fine", "alright",
        "right", "no", "nope", "stop", "wait", "cancel", "now", "that", "worked",
    }
)

# Prompts that are small talk, one-word replies, trivial local edits or questions about the
# immediate code or chat rather than a task. They never need a skill, whatever the ranker finds.
_SMALL_TALK_WORDS = frozenset(
    "hi hello hey there bye goodbye cheers thanks thank much appreciated so cool great perfect nice "
    "ok okay sure yeah yep yes no nope done agreed lgtm see later going good morning night well hmm "
    "sounds fine alright right now then please go ahead continue next stop wait awesome got worked "
    "cancel proceed thx".split()
)
_CONVERSATIONAL_PATTERNS = (
    re.compile(r"\b(?:what|why|how) (?:did|do|does) you (?:do|mean|choose|say|use|think)\b"),
    re.compile(r"\bwhat you (?:meant|said|were saying|are saying)\b"),
    re.compile(
        r"\b(?:previous|last|earlier) (?:message|paragraph|step|change|answer|response|diff|output|"
        r"command|approach|explanation)\b"
    ),
    re.compile(r"\b(?:show|give) me (?:the|that) (?:diff|output|last|previous)\b"),
    re.compile(r"\b(?:thanks|thank you)\b[^.!?]*\b(?:works?|worked|is working|looks? (?:good|correct|right))\b"),
    re.compile(r"^(?:sorry|i meant|i was referring)\b"),
    re.compile(r"^(?:please )?(?:continue|go on|keep going)\b"),
    re.compile(r"^(?:that|this) (?:output|looks|sounds|makes sense)\b"),
    re.compile(r"^(?:can you )?(?:repeat|say) (?:that|this)\b"),
)
_TRIVIAL_PATTERNS = (
    re.compile(
        r"^(?:rename|delete|remove|change|fix|make|add|move|update|reword|shorten|capitalize)\s+"
        r"(?:this|that|the)\s+(?:variable|line|comment|typo|semicolon|word|sentence|text|label|"
        r"string|import|function|method|name|value|color|colour|spelling|indentation|whitespace|"
        r"bracket|quote|comma|dot|space|character|number)\b"
    ),
    re.compile(r"^fix the (?:typo|spelling|wording|grammar)\b"),
    re.compile(r"^change (?:this|that|it) to (?:lowercase|uppercase|camelcase|title case|snake case)\b"),
    re.compile(r"^(?:make|change) (?:this|that) (?:bold|italic|bigger|smaller|red|blue|green)\b"),
    re.compile(r"^add (?:a|an) (?:semicolon|comma|period|dot|space|bracket|quote)\b"),
    re.compile(r"^(?:what does|what is|what's) (?:this|that|it)\b"),
    re.compile(r"^(?:why is|why does) (?:this|that|it)\b"),
    re.compile(r"^can you (?:explain|show|repeat|say)(?: me)? (?:this|that|it|the line|the code|the function|the error)\b"),
    re.compile(r"^is this (?:working|on|ready|right|correct)\b"),
    re.compile(r"^are you (?:there|ok|okay|sure|ready)\b"),
    re.compile(r"^can you hear me\b"),
    re.compile(r"^(?:repeat|say) (?:that|this)\b"),
    re.compile(r"^what (?:model|version) are you\b"),
    re.compile(r"^what is the (?:date|time|weather|day)\b"),
)


def _is_conversational_prompt(text: str) -> bool:
    """True for a follow-up about the chat itself rather than a new task for a skill."""
    lowered = text.strip().lower()
    return any(pattern.search(lowered) for pattern in _CONVERSATIONAL_PATTERNS)


def _is_trivial_prompt(text: str) -> bool:
    """True for small talk, one-word replies and local edits that deserve no skill suggestion."""
    lowered = text.strip().lower()
    if not lowered:
        return True
    words = re.findall(r"[a-z]+", lowered)
    if len(words) <= 2:
        return True
    if all(word in _STOPWORDS or word in _SMALL_TALK_WORDS for word in words):
        return True
    return any(pattern.search(lowered) for pattern in _TRIVIAL_PATTERNS)


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


MAX_SKILL_NAME_LENGTH = 64
SKILL_NAME_RULES = "letters, digits, - and _, up to 64 characters"
# Names saved by earlier versions could be longer than today's cap (up to what the filesystem allows once ".md" is
# added). They stay readable and removable; only a new name is held to MAX_SKILL_NAME_LENGTH.
_MAX_EXISTING_NAME_LENGTH = 251
MAX_CUSTOM_SKILL_BYTES = 1024 * 1024
MAX_BACKUPS_PER_SKILL = 5
_MAX_BACKUP_STEM = 64
# Device names Windows refuses as a file name, with or without an extension.
_WINDOWS_RESERVED_NAMES = frozenset(
    ["con", "prn", "aux", "nul"] + [f"com{n}" for n in range(1, 10)] + [f"lpt{n}" for n in range(1, 10)]
)


class CustomSkillError(ValueError):
    """A custom-skill operation that cannot go ahead. The message is one line that says what is wrong and what to do."""


def name_key(name: str) -> str:
    """The form of a custom skill name used to decide whether two names are the same file on a case-insensitive
    filesystem (macOS, Windows). Local adds, pulls and pushes all compare names through this one function."""
    return name.casefold()


def custom_skills_dir() -> Path:
    return user_home() / ".config" / "aletheore" / "custom_skills"


def _shown(name: object) -> str:
    text = repr(name)
    return text if len(text) <= 40 else text[:37] + "...'"


def _reason(exc: OSError) -> str:
    return (exc.strerror or str(exc) or exc.__class__.__name__).lower()


def sanitize_skill_name(name: str, *, existing: bool = False) -> str:
    """Return `name` unchanged if it is a safe skill name, else raise ValueError.

    Path separators and null bytes are rejected before the regex check so they
    can never reach a filesystem path. The regex excludes '.', so '.' and '..'
    are rejected too. A new name is also capped at MAX_SKILL_NAME_LENGTH and may not be a Windows
    device name; `existing=True` (looking up, reading or removing a skill that is already saved)
    skips those two rules so a skill saved by an older version can still be reached.
    """
    if (
        not isinstance(name, str)
        or "/" in name
        or "\\" in name
        or "\0" in name
        or not _SAFE_SKILL_NAME.fullmatch(name)
    ):
        raise ValueError(f"Invalid skill name {_shown(name)}: use only {SKILL_NAME_RULES}")
    limit = _MAX_EXISTING_NAME_LENGTH if existing else MAX_SKILL_NAME_LENGTH
    if len(name) > limit:
        raise ValueError(f"Invalid skill name: it is {len(name)} characters long. Use only {SKILL_NAME_RULES}")
    if not existing and name.lower() in _WINDOWS_RESERVED_NAMES:
        raise ValueError(
            f"Invalid skill name {name!r}: it is a reserved device name on Windows. Pick another ({SKILL_NAME_RULES})"
        )
    return name


def validate_custom_skill_content(content: str) -> None:
    """Raise CustomSkillError for skill text that must not be saved: empty, binary or over 1 MB."""
    if not isinstance(content, str) or not content.strip():
        raise CustomSkillError("the skill text is empty. Write the instructions in the file first")
    if "\0" in content:
        raise CustomSkillError("the skill text contains NUL bytes, so it is not a text file. Use a markdown file")
    size = len(content.encode("utf-8", errors="replace"))
    if size > MAX_CUSTOM_SKILL_BYTES:
        raise CustomSkillError(
            f"the skill text is {size / (1024 * 1024):.1f} MB and the limit is 1 MB. Trim it or split it into several skills"
        )


def custom_skill_warnings(content: str) -> list[str]:
    """Warnings (not errors) about text that is still saved: it looks like it holds a credential.

    Uses the scanner's own secret patterns, and skips values it would treat as placeholders.
    """
    from aletheore import secrets

    found: list[str] = []
    for pattern_name, pattern, value_group in secrets.SECRET_PATTERNS:
        for match in pattern.finditer(content):
            value = match.group(value_group)
            if secrets._is_likely_placeholder("skill.md", value, pattern_name):
                continue
            label = pattern_name.replace("_", " ")
            if label not in found:
                found.append(label)
            break
    if not found:
        return []
    return [
        f"this text looks like it contains a credential ({', '.join(found)}). Custom skills are stored in plain "
        "text on this machine and uploaded to your Aletheore account when you are logged in. Remove the secret "
        "from the skill if you did not mean to share it"
    ]


def read_custom_skill_source(path: Path) -> str:
    """The text of a file given to `hallbook add`, or CustomSkillError saying what is wrong with it."""
    try:
        if Path(path).is_dir():  # Windows reports PermissionError, not IsADirectoryError, when opening a folder
            raise IsADirectoryError(str(path))
        with open(path, "rb") as handle:
            data = handle.read(MAX_CUSTOM_SKILL_BYTES + 1)
    except FileNotFoundError:
        raise CustomSkillError(f"cannot read {path}: no such file. Check the path") from None
    except IsADirectoryError:
        raise CustomSkillError(f"{path} is a directory, not a file. Give the path of a markdown file") from None
    except PermissionError:
        raise CustomSkillError(f"cannot read {path}: permission denied. Check the file's permissions") from None
    except OSError as exc:
        raise CustomSkillError(f"cannot read {path}: {_reason(exc)}") from None
    if b"\0" in data:
        raise CustomSkillError(f"{path} contains NUL bytes, so it is not a text file. Use a markdown file")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        if len(data) > MAX_CUSTOM_SKILL_BYTES:
            raise CustomSkillError(f"{path} is over 1 MB. Trim it or split it into several skills") from None
        raise CustomSkillError(
            f"{path} is not valid UTF-8 text (a binary or latin-1 file?). Save it as UTF-8 markdown and try again"
        ) from None
    return text


def _ensure_custom_dir() -> Path:
    directory = custom_skills_dir()
    try:
        directory.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CustomSkillError(
            f"cannot use the custom skills folder {directory}: {_reason(exc)}. "
            "Fix the folder (or remove whatever is in its place) and try again"
        ) from None
    try:
        # Private to this user. Only tightened, never loosened: a folder the user made read-only stays read-only.
        if directory.stat().st_mode & 0o077:
            os.chmod(directory, 0o700)
    except OSError:
        pass  # best effort: not every filesystem has permission bits
    return directory


def _atomic_write(path: Path, text: str) -> None:
    """Write `text` to `path` through a temp file in the same folder, so a failure never leaves half a file."""
    import tempfile  # lazy: the prompt hook imports this module and never writes

    tmp_name = None
    try:
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".skill-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:  # same bytes on every OS
            handle.write(text)
        try:
            os.chmod(tmp_name, 0o600)
        except OSError:
            pass
        os.replace(tmp_name, path)
        tmp_name = None
    except UnicodeEncodeError:
        raise CustomSkillError("the skill text has characters that cannot be saved as UTF-8. Remove them") from None
    except OSError as exc:
        raise CustomSkillError(
            f"cannot write {path.name} in {path.parent}: {_reason(exc)}. Check the folder's permissions"
        ) from None
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def write_custom_skill_file(name: str, content: str) -> Path:
    safe = sanitize_skill_name(name, existing=True)
    directory = _ensure_custom_dir()
    path = directory / f"{safe}.md"
    _atomic_write(path, content)
    return path


def read_custom_skill_file(name: str) -> str | None:
    path = custom_skills_dir() / f"{sanitize_skill_name(name, existing=True)}.md"
    try:
        if not path.is_file():
            return None
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CustomSkillError(f"cannot read {path.name}: {_reason(exc)}. Check the file's permissions") from None


def list_custom_skill_names() -> list[str]:
    directory = custom_skills_dir()
    try:
        if not directory.is_dir():
            return []
        return sorted(p.stem for p in directory.glob("*.md"))
    except OSError:
        return []


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
    root = bundled_skills_dir()
    path = root / parts[0] / parts[1] / "SKILL.md"
    if not path.is_file():
        return None
    # On a case-insensitive filesystem "DEBUGGING/Systematic-Debugging" opens the real file; an id is only
    # valid in the case it is spelled in, so check the folder names as the filesystem lists them.
    try:
        if parts[0] not in os.listdir(root) or parts[1] not in os.listdir(root / parts[0]):
            return None
    except OSError:
        return None
    return path


def get_bundled_skill(skill_id: str) -> CuratedSkill | None:
    path = _bundled_skill_md_path(skill_id)
    return _parse_skill_file(path) if path is not None else None


class SkillFileError(Exception):
    """Raised by read_skill_file for a path that is unsafe, missing, binary or not a file."""


def _is_compiled_python(rel_path: str) -> bool:
    parts = rel_path.replace("\\", "/").split("/")
    return "__pycache__" in parts[:-1] or parts[-1].lower().endswith((".pyc", ".pyo"))


def list_skill_files(skill_id: str) -> tuple[list[str], int]:
    """Companion files of a bundled skill (scripts, references, assets) as relative paths.

    Returns (first _MAX_LISTED_FILES paths in sorted order, total count). SKILL.md itself and
    the top-level license files are not listed: the first is what get_skill returns, and the
    license text is attribution, not working material.
    """
    skill_md = _bundled_skill_md_path(skill_id)
    if skill_md is None:
        return [], 0
    files = _companion_files(skill_md.parent)
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
    if _is_compiled_python(posix.as_posix()):
        raise SkillFileError(f"{rel_path} is a compiled Python file, not part of the skill")
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
    # Who holds the copyright, as named in the shipped NOTICE.md ("not recorded" when it does not say).
    # None for a custom skill, which carries no licence information.
    copyright: str | None = None


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


def _collapse_repeats(token: str) -> str:
    """Fold runs of the same letter, so a doubled-letter typo still finds its word."""
    return re.sub(r"(.)\1+", r"\1", token)


def _within_one_edit(left: str, right: str) -> bool:
    """True when two tokens differ by one insert, delete, substitute or adjacent swap.

    This is a narrow, allocation-light check rather than a full edit-distance matrix: the
    query path runs it against a small bucket of candidate vocabulary words.
    """
    if left == right:
        return True
    left_len, right_len = len(left), len(right)
    if abs(left_len - right_len) > 1:
        return False
    if left_len == right_len:
        differing = [index for index in range(left_len) if left[index] != right[index]]
        if len(differing) == 1:
            return True
        if len(differing) == 2:
            first, second = differing
            return (
                second == first + 1
                and left[first] == right[second]
                and left[second] == right[first]
            )
        return False
    short, long = (left, right) if left_len < right_len else (right, left)
    index = 0
    while index < len(short) and short[index] == long[index]:
        index += 1
    return short[index:] == long[index + 1:]


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
    # An additive name bonus bypasses BM25's field-length normalisation, so a long
    # name that repeats the query can outrank a shorter, more general skill. Penalise
    # each name token the query did not ask for; tuned on the dev half.
    "name_extra": 3.0,
    "phrase": 1.26,
    "prefix": 0.6,
    # A token recovered by one edit is a guess, so its term frequency counts for less
    # than an exact hit. It only applies to query tokens with no exact index match.
    "fuzzy": 0.55,
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
        self.id_to_index = {skill_id: index for index, skill_id in enumerate(self.ids)}
        self.categories = [meta.category for meta in metas]
        self.quality = [meta.quality for meta in metas]
        self.texts: list[tuple[str, ...]] = []
        self.joined: list[str] = []
        self.doc_len: list[dict[str, int]] = []
        self.name_sizes: list[int] = []
        self.prefix_tokens: dict[str, tuple[str, ...]] | None = None
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
        # The vocabulary is grouped by first letter and length. A one-edit match must share the
        # first letter and is at most one character longer or shorter, so this keeps the fuzzy
        # lookup to a few dozen words instead of scanning every token in the catalog.
        self.vocab: set[str] = set()
        for texts in self.texts:
            for text in texts:
                self.vocab.update(text.split())
        self.vocab_by_first: dict[str, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
        for token in sorted(self.vocab):
            if len(token) >= 4:
                self.vocab_by_first[token[0]][len(token)].append(token)

    def coverage(self, query_tokens: list[str], skill_id: str) -> float:
        """The share of query tokens the skill's text contains, counting a typo recovery."""
        index = self.id_to_index.get(skill_id)
        if index is None:
            return 1.0
        joined = self.joined[index]
        matched = 0
        for token in query_tokens:
            if f" {token} " in joined:
                matched += 1
                continue
            recovered = self._fuzzy_match(token)
            if recovered is not None and f" {recovered} " in joined:
                matched += 1
        return matched / len(query_tokens) if query_tokens else 0.0

    def _fuzzy_match(self, token: str) -> str | None:
        """The closest vocabulary token to an unmatched query token, or None.

        A doubled-letter typo is folded first ("postgress" -> "postgres") because the
        stemmer keeps "ss" and would otherwise leave it several edits from "postgr".
        """
        if len(token) < 5:
            return None
        forms = [token]
        collapsed = _collapse_repeats(token)
        if collapsed != token:
            forms.append(collapsed)
            forms.append(_stem(collapsed))
        for form in dict.fromkeys(forms):
            if form in self.vocab:
                return form
            if len(form) < 4:
                continue
            buckets = self.vocab_by_first.get(form[0], {})
            for length in (len(form) - 1, len(form), len(form) + 1):
                for candidate in buckets.get(length, ()):
                    if _within_one_edit(form, candidate):
                        return candidate
        return None

    def _ensure_prefix_tokens(self) -> None:
        """Build the prefix map on first use; the hook path (prefix=False) never needs it."""
        if self.prefix_tokens is not None:
            return
        vocab: set[str] = set()
        for texts in self.texts:
            for text in texts:
                vocab.update(text.split())
        prefix: dict[str, list[str]] = defaultdict(list)
        for token in sorted(vocab):
            if len(token) >= 5:
                prefix[token[:5]].append(token)
        self.prefix_tokens = {key: tuple(value) for key, value in prefix.items()}

    def rank_scored(
        self,
        query_tokens: list[str],
        category: str | None,
        limit: int,
        min_matches: int = 1,
        prefix: bool = True,
        phrase: bool = True,
        fuzzy: bool = True,
    ) -> list[tuple[CuratedSkillMeta, float]]:
        weights = _RANK_WEIGHTS
        field_weights = [weights[field] for field in _SEARCH_FIELDS]
        k1 = weights["k1"]
        # (token used for term frequency, weight applied to it) for each query token.
        # An exact hit is the token itself at full weight. A typo is the recovered
        # vocabulary word, down-weighted so it can only help an otherwise weak match.
        sources: dict[str, tuple[str, float]] = {}
        needed: set[str] = set()
        for token in query_tokens:
            if token in self.vocab:
                sources[token] = (token, 1.0)
                needed.add(token)
            elif fuzzy:
                recovered = self._fuzzy_match(token)
                if recovered is not None:
                    sources[token] = (recovered, weights["fuzzy"])
                    needed.add(recovered)
        if prefix:
            self._ensure_prefix_tokens()
            assert self.prefix_tokens is not None
            for token in query_tokens:
                if token in self.vocab and len(token) >= 5:
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
            source = sources.get(token)
            if source is None:
                term_maps.append((token, {}))
                continue
            source_token, source_weight = source
            merged: dict[int, list[float]] = {}
            for doc_index, tfs in postings[source_token].items():
                merged[doc_index] = [source_weight * value for value in tfs]
            if prefix and token in self.vocab and len(token) >= 5:
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
            term_maps.append((source_token, merged))
        if not matching:
            return []
        # Recovered typo tokens stand in for the query token in the name and phrase checks,
        # so "kuberentes deployment" still gets the same name and adjacent-word credit as
        # "kubernetes deployment". Coverage still counts each query token once.
        scoring_tokens = [sources.get(token, (token, 0.0))[0] for token in query_tokens]
        query_pairs = list(zip(scoring_tokens, scoring_tokens[1:]))
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
            name_hits = [token for token in scoring_tokens if token in name_tokens]
            if name_hits:
                score += weights["name_hit"] * sum(idf.get(token, 0.0) for token in name_hits)
                if self.name_sizes[doc_index]:
                    score += weights["name_cov"] * (len(set(name_hits)) / self.name_sizes[doc_index])
                score -= weights["name_extra"] * (self.name_sizes[doc_index] - len(set(name_hits)))
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


# Custom skills are read from disk on demand. Each file is cached by (path, mtime, size), so
# a search only pays for the files that changed since the previous one, and an add, edit or
# removal shows up on the next search without restarting the process.
_CUSTOM_DOC_CACHE: dict[str, tuple[int, int, CuratedSkillMeta]] = {}
_CUSTOM_INDEX_CACHE: tuple[tuple[tuple[str, int, int], ...], _RankIndex | None] = ((), None)


def _custom_frontmatter(text: str) -> tuple[str, str, str]:
    """(title, description, body) from a custom skill's optional frontmatter."""
    if not text.startswith("---\n"):
        return "", "", text
    end = text.find("\n---\n", 4)
    if end == -1:
        return "", "", text
    title = description = ""
    for line in text[4:end].splitlines():
        key, separator, value = line.partition(":")
        if not separator:
            continue
        key = key.strip().lower()
        value = value.strip().strip("'\"")
        if key == "title":
            title = value
        elif key == "description":
            description = value
    return title, description, text[end + 5:]


def _custom_meta(path: Path) -> CuratedSkillMeta | None:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    title, description, body = _custom_frontmatter(text)
    name = path.stem
    headings = " ".join(line.lstrip("#").strip() for line in body.splitlines() if line.lstrip().startswith("#"))

    def tokens(value: str) -> str:
        return " ".join(_tokens(value))

    return CuratedSkillMeta(
        skill_id=f"{_CUSTOM_PREFIX}{name}",
        category=_CUSTOM_CATEGORY,
        title=title or name,
        description=description,
        search_tokens={
            "name": tokens(name.replace("-", " ").replace("_", " ")),
            "title": tokens(title or name),
            "description": tokens(description),
            "intents": "",
            "headings": tokens(headings),
            "body": tokens(body),
            "category": tokens(_CUSTOM_CATEGORY),
        },
    )


def _custom_index() -> _RankIndex | None:
    """The custom-skill index, rebuilt only when a file's path, mtime or size changed."""
    global _CUSTOM_INDEX_CACHE
    directory = custom_skills_dir()
    try:
        paths = sorted(directory.glob("*.md"))
    except OSError:
        return None
    signature: list[tuple[str, int, int]] = []
    metas: list[CuratedSkillMeta] = []
    live_paths = set()
    for path in paths:
        try:
            stat = path.stat()
        except OSError:
            continue
        key = (str(path), stat.st_mtime_ns, stat.st_size)
        signature.append(key)
        live_paths.add(str(path))
        cached = _CUSTOM_DOC_CACHE.get(str(path))
        if cached is not None and cached[0] == key[1] and cached[1] == key[2]:
            meta = cached[2]
        else:
            meta = _custom_meta(path)
            if meta is not None:
                _CUSTOM_DOC_CACHE[str(path)] = (key[1], key[2], meta)
        if meta is not None:
            metas.append(meta)
    for stale in set(_CUSTOM_DOC_CACHE) - live_paths:
        del _CUSTOM_DOC_CACHE[stale]
    signature_tuple = tuple(signature)
    if _CUSTOM_INDEX_CACHE[0] == signature_tuple:
        return _CUSTOM_INDEX_CACHE[1]
    index = _RankIndex(tuple(metas)) if metas else None
    _CUSTOM_INDEX_CACHE = (signature_tuple, index)
    return index


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
    fuzzy: bool = True,
) -> list[tuple[SkillMeta, float]]:
    """The same ranking as search_skills, with the raw score for each result.

    `min_matches`, `prefix`, `phrase` and `fuzzy` are candidate-pruning aids for
    callers that only need the head of the ranking (the prompt hook). Their defaults
    are the full ranking.
    """
    query_tokens = list(dict.fromkeys(_tokens(query)))
    if not query_tokens or limit <= 0:
        return []
    min_matches = min(min_matches, len(query_tokens))
    index = _search_index(str(bundled_skills_dir()))
    custom_index = _custom_index() if (category is None or category == _CUSTOM_CATEGORY) else None
    scored: list[tuple[float, str, SkillMeta]] = []
    for meta, score in index.rank_scored(
        query_tokens,
        category,
        limit * 3 + (len(custom_index.metas) if custom_index is not None else 0),
        min_matches=min_matches,
        prefix=prefix,
        phrase=phrase,
        fuzzy=fuzzy,
    ):
        if score:
            scored.append((score, meta.skill_id, _meta(meta)))
    if custom_index is not None:
        # A custom skill is the user's own, unvetted text. It is searched by its name,
        # title, description and body like a bundled skill, but its score is capped
        # under the "high confidence" bar so it is never handed over as a confident
        # answer or injected by the hook. It also never counts as the runner-up.
        for meta, score in custom_index.rank_scored(
            query_tokens,
            None,
            limit * 3,
            min_matches=min_matches,
            prefix=prefix,
            phrase=phrase,
            fuzzy=fuzzy,
        ):
            if score:
                # The custom corpus is tiny, so its IDF makes an otherwise good match
                # score far below the bundled results. A coverage floor keeps a custom
                # skill that covers much of the query visible next to them, while the
                # cap still keeps every custom result under the high-confidence bar.
                cap = FIND_SKILL_MIN_SCORE - 1.0
                coverage = custom_index.coverage(query_tokens, meta.skill_id)
                custom_score = min(cap, max(score, cap * (0.4 + 0.6 * coverage)))
                scored.append(
                    (
                        custom_score,
                        meta.skill_id,
                        SkillMeta(
                            skill_id=meta.skill_id,
                            category=_CUSTOM_CATEGORY,
                            title=meta.title,
                            description=meta.description,
                        ),
                    )
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


def skill_alternates(skill_id: str) -> list[str]:
    """Ids of the skills collapsed under this canonical skill (empty when it has none)."""
    return sorted(_curation(str(bundled_skills_dir()))[1].get(skill_id, []))


# A longer query only slows the search down (a 5,000-word one takes seconds) and ranks no better.
MAX_QUERY_CHARS = 500


def cap_query(query: str) -> str:
    """The query cut to MAX_QUERY_CHARS, at a word boundary where there is one."""
    if len(query) <= MAX_QUERY_CHARS:
        return query
    cut = query[:MAX_QUERY_CHARS]
    head, _, tail = cut.rpartition(" ")
    return head if head and tail and not query[MAX_QUERY_CHARS].isspace() else cut


def search_skills(query: str, category: str | None = None, limit: int = 10) -> list[SkillMeta]:
    """Ranked search over name, title, description, intents, headings and body opening.

    A BM25F-style score (offline, deterministic) combines the fields with separate weights,
    term rarity, length normalisation, query coverage, adjacent-word phrases and prefix matching.
    A skill needs at least one matching word; custom skills are matched on their name only.
    """
    return [meta for meta, _ in search_skills_scored(cap_query(query), category, limit)]


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
        copyright=skill_copyright(curated.skill_id),
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


def _query_coverage(task: str, skill_id: str) -> float:
    tokens = list(dict.fromkeys(_tokens(task)))
    return _search_index(str(bundled_skills_dir())).coverage(tokens, skill_id)


def _is_high_confidence(results: list[tuple[SkillMeta, float]], task: str | None = None) -> bool:
    """The strict find_skill bar: strong top score, ahead of the runner-up and on-topic.

    Coverage is checked last and only when the task is known, so callers that only
    have scores (and tests with synthetic results) keep the score-only behaviour.
    """
    if not results or results[0][0].skill_id.startswith(_CUSTOM_PREFIX):
        return False
    top_score = results[0][1]
    # A custom skill is the user's own unvetted text and can never be the confident answer, so it must not
    # count as the runner-up that a vetted top hit has to beat either: a custom skill named after the task
    # would otherwise turn a correct "high" answer into "low". It is still listed as an alternative.
    second_score = next((score for meta, score in results[1:] if not meta.skill_id.startswith(_CUSTOM_PREFIX)), 0.0)
    gap = top_score - second_score
    ratio = top_score / second_score if second_score > 0 else math.inf
    if not (top_score >= FIND_SKILL_MIN_SCORE and (gap >= FIND_SKILL_MIN_GAP or ratio >= FIND_SKILL_MIN_RATIO)):
        return False
    if task is not None and _query_coverage(task, results[0][0].skill_id) < FIND_SKILL_MIN_COVERAGE:
        return False
    return True


def find_skill(task: str, category: str | None = None) -> dict:
    """One call that either hands over the right skill or says to move on.

    Returns "high" confidence with the full skill only when the top result is strong and
    clearly ahead of the runner-up; otherwise "low" with compact candidates, or "none"
    for small talk, trivial edits and matches too weak to be about a real task. The
    thresholds are calibrated offline on the search benchmark.
    """
    if _is_trivial_prompt(task):
        return {"confidence": "none", "note": FIND_SKILL_NONE_NOTE}
    results = search_skills_scored(task, category=category, limit=5)
    if not results:
        return {"confidence": "none", "note": FIND_SKILL_NONE_NOTE}

    top_meta, top_score = results[0]
    if top_score < FIND_SKILL_NONE_SCORE:
        return {"confidence": "none", "note": FIND_SKILL_NONE_NOTE}
    confident = _is_high_confidence(results, task)

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


# Per-session hook state: one small JSON file per Claude Code session under the user's
# Aletheore config directory. A skill is suggested at most once per session. Every
# operation is best effort: a missing, unreadable or unwritable file only means the hook
# may repeat a suggestion, never that it crashes or prints a traceback.
_SESSION_DIRNAME = "hallbook_sessions"
_SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_SESSION_HINT_GAP = 3
_SESSION_ID = re.compile(r"[A-Za-z0-9_-]{1,128}")


def session_state_dir() -> Path:
    return user_home() / ".config" / "aletheore" / _SESSION_DIRNAME


def _session_state_path(session_id: object) -> Path | None:
    if not isinstance(session_id, str) or not _SESSION_ID.fullmatch(session_id):
        return None
    return session_state_dir() / f"{session_id}.json"


def _prune_session_state() -> None:
    directory = session_state_dir()
    cutoff = time.time() - _SESSION_MAX_AGE_SECONDS
    try:
        for entry in directory.iterdir():
            try:
                if entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                continue
    except OSError:
        return


def _load_session_state(path: Path) -> tuple[set[str], int, int]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set(), 0, 0
    if not isinstance(data, dict):
        return set(), 0, 0
    values = data.get("suggested")
    skills = {value for value in values if isinstance(value, str)} if isinstance(values, list) else set()
    prompts = data.get("prompts")
    last_suggest = data.get("last_suggest")
    return (
        skills,
        prompts if isinstance(prompts, int) and prompts >= 0 else 0,
        last_suggest if isinstance(last_suggest, int) and last_suggest >= 0 else 0,
    )


def _write_session_state(path: Path, skills: set[str], prompts: int, last_suggest: int) -> None:
    import tempfile

    tmp_name = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".session-", suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump({"suggested": sorted(skills), "prompts": prompts, "last_suggest": last_suggest}, handle)
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError:
        return
    finally:
        if tmp_name is not None:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass


def _begin_session_prompt(session_id: object) -> tuple[Path, set[str], int, int, bool] | None:
    """Count this prompt and say whether the 3-prompt hint gap still applies.

    Returns None when there is no usable session id. Every read and write is best
    effort, so a broken state file can allow an extra hint but never break the hook.
    """
    path = _session_state_path(session_id)
    if path is None:
        return None
    _prune_session_state()
    seen, prompts, last_suggest = _load_session_state(path)
    prompts += 1
    throttled = bool(last_suggest and prompts - last_suggest < _SESSION_HINT_GAP)
    _write_session_state(path, seen, prompts, last_suggest)
    return path, seen, prompts, last_suggest, throttled


def _new_session_suggestions(
    session_id: object,
    skill_ids: list[str],
    session: tuple[Path, set[str], int, int, bool] | None = None,
) -> list[str]:
    """The ids not already suggested in this session, recorded as suggested now.

    Returns every id when there is no usable session id, so a direct caller such as
    `aletheore hallbook suggest` keeps working without state.
    """
    if session is None:
        path = _session_state_path(session_id)
        if path is None:
            return list(skill_ids)
        _prune_session_state()
        seen, prompts, _ = _load_session_state(path)
    else:
        path, seen, prompts, _, _ = session
    new = [skill_id for skill_id in skill_ids if skill_id not in seen]
    if new:
        _write_session_state(path, seen | set(new), prompts, prompts)
    return new


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
    if _is_trivial_prompt(text) or _is_conversational_prompt(text):
        return None
    return text


def hook_suggestion(prompt: str, mode: str = "hint", session_id: str | None = None) -> str | None:
    """Suggest a Hallbook skill for a Claude Code UserPromptSubmit hook, or None to add nothing.

    Silent by design: only a clear, high-confidence match produces output, so the hook never nudges
    the agent for small talk, follow-up questions, slash commands, fragments or ambiguous requests.
    "hint" prints one short pointer; "inject" prints the skill content itself. A skill is suggested
    at most once per session when the caller passes the hook payload's `session_id`. The caller is
    responsible for swallowing errors.
    """
    if mode not in HOOK_MODES:
        raise ValueError(f"unknown hook mode: {mode!r}")
    session = _begin_session_prompt(session_id)
    if session is not None and session[4]:
        return None
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
            if _is_high_confidence(full_results, text):
                top_meta = full_results[0][0]
                if not _new_session_suggestions(session_id, [top_meta.skill_id], session):
                    return None
                return (
                    f"Hallbook skill that fits this task: {top_meta.skill_id} ({top_meta.title}).\n"
                    "If it applies, load it with aletheore_get_skill (or run: aletheore hallbook get "
                    f"{top_meta.skill_id}) and follow it; adapt it to this project."
                )
            results = full_results
        if len(results) < 2:
            return None
        (first, first_score), (second, second_score) = results[0], results[1]
        if first_score < HINT_MIN_SCORE:
            return None
        if second_score > 0 and first_score < HINT_MIN_RATIO * second_score:
            return None
        if _query_coverage(text, first.skill_id) < HOOK_MIN_COVERAGE:
            return None
        by_id = {first.skill_id: first, second.skill_id: second}
        suggested = _new_session_suggestions(session_id, [first.skill_id, second.skill_id], session)
        if not suggested:
            return None
        names = " or ".join(f"{by_id[skill_id].skill_id} ({by_id[skill_id].title})" for skill_id in suggested)
        return (
            f"Hallbook may have a skill for this: {names}. "
            "If one applies, load it with aletheore_get_skill (or run: aletheore hallbook get <id>); otherwise ignore this."
        )
    result = find_skill(text)
    if result.get("confidence") != "high":
        return None
    top = result.get("top") or {}
    skill = result.get("skill") or {}
    skill_id = top.get("skill_id") or skill.get("skill_id")
    if not skill_id:
        return None
    if not _new_session_suggestions(session_id, [skill_id], session):
        return None
    content = skill.get("content") or ""
    if len(content) > FIND_SKILL_MAX_CONTENT_CHARS:
        content = content[:FIND_SKILL_MAX_CONTENT_CHARS]
    return (
        f"Hallbook skill for this task ({skill_id}), apply it to this project and ignore parts that do not fit:\n\n"
        f"{content}"
    )


# --- Backups of custom skills ---------------------------------------------------------------------------------------
#
# Every change that replaces or removes a custom skill first keeps the old text next to it:
#   <name>.md.bak-<UTC time>              the version an add replaced
#   <name>.md.deleted-<UTC time>.bak      the version a remove (or a sync that mirrors a remote delete) deleted
#   <name>.md.server-<UTC time>.bak       the account's copy, saved before a local-wins push (see skill_hub_sync)
#   <name>.md.synced-<UTC time>.bak       the local copy, saved before a pull from the account
# The five newest per skill are kept. The single-file <name>.md.bak that earlier versions wrote is still
# recognised: it counts as the oldest.


def _backup_stem(name: str) -> str:
    """The name as used in a backup file name: long names are cut and hashed so the file name stays short."""
    if len(name) <= _MAX_BACKUP_STEM:
        return name
    import hashlib

    return f"{name[:48]}~{hashlib.sha1(name.encode(), usedforsecurity=False).hexdigest()[:10]}"


def _backup_regex(name: str) -> re.Pattern:
    # Backups written by earlier versions carry the full name even when it is long; today's carry the cut-and-hashed
    # stem. Recognise both, so a legacy long-named skill's old backups are listed and pruned like any other.
    stems = dict.fromkeys([_backup_stem(name), name])
    stem = "(?:" + "|".join(re.escape(item) for item in stems) + ")"
    return re.compile(
        rf"{stem}\.md\.(?:bak(?:-(?P<s1>\d{{8}}-\d{{6}})(?:-(?P<c1>\d+))?)?"
        rf"|[a-z]+-(?P<s2>\d{{8}}-\d{{6}})(?:-(?P<c2>\d+))?\.bak)"
    )


def _backup_entries(name: str) -> list[tuple[tuple[str, int], Path]]:
    """(sort key, path) for each backup of a custom skill, oldest first. The key is (UTC stamp, counter)."""
    directory = custom_skills_dir()
    pattern = _backup_regex(name)
    found = []
    try:
        entries = list(directory.iterdir())
    except OSError:
        return []
    for entry in entries:
        match = pattern.fullmatch(entry.name)
        if match is None:
            continue
        stamp = match.group("s1") or match.group("s2") or ""
        counter = int(match.group("c1") or match.group("c2") or (1 if stamp else 0))
        found.append(((stamp, counter), entry))
    return sorted(found, key=lambda item: (item[0], item[1].name))


def list_backups(name: str) -> list[Path]:
    """The backups kept for a custom skill, oldest first."""
    return [entry for _, entry in _backup_entries(name)]


def unique_backup_path(name: str, kind: str) -> Path:
    """A fresh, never-overwritten backup path next to a custom skill (see the table above).

    Backups made by add, remove and sync must not clobber one another, and a new one must always sort as the newest
    (so pruning never drops it), so a second backup in the same second gets a counter above the existing ones.
    `kind` is "bak" for a replaced version, or e.g. "server", "synced", "deleted". The name part is cut and hashed
    when it is long, so a backup name can never exceed the filesystem's limit, whatever the skill is called.
    """
    directory = custom_skills_dir()
    stem = _backup_stem(name)
    stamp = time.strftime("%Y%m%d-%H%M%S", time.gmtime())
    counter = 1
    entries = _backup_entries(name)
    if entries:
        (last_stamp, last_counter), _ = entries[-1]
        if last_stamp >= stamp:  # same second (or a clock that went back): sort after what is there
            stamp, counter = last_stamp, last_counter + 1
    while True:
        suffix = f"-{counter}" if counter > 1 else ""
        path = (
            directory / f"{stem}.md.bak-{stamp}{suffix}"
            if kind == "bak"
            else directory / f"{stem}.md.{kind}-{stamp}{suffix}.bak"
        )
        if not path.exists():
            return path
        counter += 1


def prune_backups(name: str, keep: int = MAX_BACKUPS_PER_SKILL) -> None:
    """Delete the oldest backups of a custom skill beyond `keep`. Never raises."""
    backups = list_backups(name)
    for old in backups[: max(0, len(backups) - keep)]:
        try:
            old.unlink()
        except OSError:
            pass


def write_backup(name: str, kind: str, text: str) -> Path:
    """Save `text` as a new backup of the custom skill `name`, then prune the oldest beyond five."""
    _ensure_custom_dir()
    path = unique_backup_path(name, kind)
    _atomic_write(path, text)
    prune_backups(name)
    return path


def add_custom_skill_with_sync(name: str, content: str) -> tuple[str, str | None]:
    """Save a custom skill. Returns (skill id, a one-line note when the account sync did not happen, else None).

    Raises ValueError (CustomSkillError for the storage and content problems) with a one-line message.
    """
    safe = sanitize_skill_name(name)
    # One line-ending convention on every OS (the file is read back with universal newlines), so the text that is
    # pushed and hashed is the text that is read later.
    if isinstance(content, str):
        content = content.replace("\r\n", "\n")
    validate_custom_skill_content(content)
    for other in list_custom_skill_names():
        if other != safe and name_key(other) == name_key(safe):
            raise CustomSkillError(
                f"a custom skill named {other!r} already exists and differs from {safe!r} only in capital letters "
                f"(many systems treat the two as one file). Use {other!r}, or remove it first"
            )
    # Local-only safety net, separate from skill_hub_sync's pull-merge backup: if a skill already exists under
    # this name (a user re-adding it, or an agent calling aletheore_add_custom_skill on a name the user already
    # has), keep its current text as a timestamped backup (five are kept) before replacing it. Skipped when the
    # content is identical, so a repeated add can't push a meaningful backup out with a duplicate.
    existing = read_custom_skill_file(safe)
    if existing is not None and existing != content:
        write_backup(safe, "bak", existing)
    write_custom_skill_file(safe, content)
    # Local import: skill_hub_sync imports this module, so a top-level import
    # here would be circular. The push never raises, so the return value is
    # unaffected by whether the sync succeeded.
    from aletheore import skill_hub_sync

    # Re-adding a name the user removed offline: the remove left a pending
    # server delete behind, and that must not fire at the next login and then
    # make the sync treat the re-added skill as deleted elsewhere.
    skill_hub_sync._clear_pending_delete(safe)
    outcome = skill_hub_sync.push_custom_skill_best_effort(name, content)
    return f"{_CUSTOM_PREFIX}{name}", skill_hub_sync.push_note(outcome)


def add_custom_skill(name: str, content: str) -> str:
    return add_custom_skill_with_sync(name, content)[0]


def remove_custom_skill_with_sync(name: str) -> tuple[bool, str | None]:
    """Delete a local custom skill. Returns (whether a local file was deleted, a one-line sync note or None)."""
    safe = sanitize_skill_name(name, existing=True)
    path = custom_skills_dir() / f"{safe}.md"
    try:
        existed = path.is_file()
    except OSError:
        existed = False
    if existed:
        # Keep a copy: the server delete below cannot be undone from here, and
        # an agent can call this through MCP.
        write_backup(safe, "deleted", read_custom_skill_file(safe) or "")
        try:
            path.unlink()
        except OSError as exc:
            raise CustomSkillError(
                f"cannot remove {path.name} from {path.parent}: {_reason(exc)}. Check the folder's permissions"
            ) from None
    # Local import: skill_hub_sync imports this module, so a top-level import
    # here would be circular. Called unconditionally, even when nothing was
    # deleted locally: the server might still hold a stale copy from an
    # earlier, now-forgotten local delete, and delete_custom_skill_best_effort
    # itself handles "nothing to do" cheaply and never raises.
    from aletheore import skill_hub_sync

    outcome = skill_hub_sync.delete_custom_skill_best_effort(name)
    return existed, skill_hub_sync.delete_note(outcome)


def remove_custom_skill(name: str) -> bool:
    """Deletes a local custom skill by name.

    Returns True if a local file was actually deleted, False if there was
    nothing to remove - "already gone" is a valid outcome for a delete, not
    an error, so this never raises SkillNotFoundError the way get_skill does.
    Still raises ValueError for an unsafe name, same as add_custom_skill.
    """
    return remove_custom_skill_with_sync(name)[0]


# --- Provenance: the licence, copyright holder and source of a skill, and the catalogue as a whole ---------------

NOT_RECORDED = "not recorded"
_NOTICE_FILE = "NOTICE.md"
_SCRIPT_SUFFIXES = (".py", ".sh", ".bash", ".zsh", ".ps1", ".rb", ".pl")


def notice_path() -> Path:
    """The generated NOTICE.md that ships with the bundled skills: one row per skill with its licence and copyright holder."""
    return bundled_skills_dir() / _NOTICE_FILE


@functools.lru_cache(maxsize=4)
def _notice_table(path: str) -> dict[str, str]:
    """skill id -> copyright holder, parsed once per process from the table in NOTICE.md (empty when it is absent).

    Rows read: | `category/name` | LICENSE | Copyright holder | upstream repository | source URL |
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return {}
    table: dict[str, str] = {}
    for line in text.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells) < 5:
            continue
        holder = " | ".join(cells[2:-2]).strip()
        table[cells[0].strip("`")] = holder
    return table


def skill_copyright(skill_id: str) -> str:
    """The copyright holder named for a bundled skill in NOTICE.md, or "not recorded"."""
    return _notice_table(str(notice_path())).get(skill_id) or NOT_RECORDED


def _companion_files(root: Path) -> list[str]:
    files = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue  # wheels do not ship dotfiles, so never advertise one
        if _is_compiled_python(rel):
            continue  # pip writes these when it byte-compiles a script; their names vary by Python version
        if rel == "SKILL.md" or (("/" not in rel) and rel.upper().startswith("LICENSE")):
            continue
        files.append(rel)
    return files


def _is_script(rel_path: str) -> bool:
    return rel_path.startswith("scripts/") or rel_path.lower().endswith(_SCRIPT_SUFFIXES)


def _shelf_title(shelf: str) -> str:
    if shelf == _CUSTOM_CATEGORY:
        return "Your skills"
    return SHELF_TITLES.get(shelf, shelf.replace("-", " ").title()) if shelf else ""


def skill_info(skill_id: str) -> dict:
    """What is known about where a skill comes from: pure data, raises SkillNotFoundError for an unknown id.

    A custom skill is the user's own text: it is marked as custom and carries no licence information.
    """
    if skill_id.startswith(_CUSTOM_PREFIX):
        skill = get_skill(skill_id)  # raises SkillNotFoundError
        return {
            "skill_id": skill.skill_id, "title": skill.title, "custom": True, "shelf": _CUSTOM_CATEGORY,
            "shelf_title": _shelf_title(_CUSTOM_CATEGORY), "category": _CUSTOM_CATEGORY, "license": None,
            "copyright": None, "source_url": None, "requires": [], "files_total": 0, "scripts": 0,
            "license_file": False,
        }
    curated = get_bundled_skill(skill_id)
    skill_md = _bundled_skill_md_path(skill_id)
    if curated is None or skill_md is None:
        raise SkillNotFoundError(skill_id)
    files = _companion_files(skill_md.parent)
    return {
        "skill_id": curated.skill_id, "title": curated.title, "custom": False, "shelf": curated.shelf,
        "shelf_title": _shelf_title(curated.shelf), "category": curated.skill_id.split("/", 1)[0],
        "license": curated.license or "", "copyright": skill_copyright(curated.skill_id),
        "source_url": curated.source_url or "", "requires": list(curated.requires), "files_total": len(files),
        "scripts": sum(1 for rel in files if _is_script(rel)),
        "license_file": any(skill_md.parent.glob("LICENSE*")),
    }


def skill_license_text(skill_id: str) -> str | None:
    """The licence text the skill ships (LICENSE.upstream, else another top-level LICENSE file), or None if it has none."""
    skill_md = _bundled_skill_md_path(skill_id)
    if skill_md is None:
        raise SkillNotFoundError(skill_id)
    root = skill_md.parent
    candidates = [root / "LICENSE.upstream", *sorted(p for p in root.glob("LICENSE*") if p.name != "LICENSE.upstream")]
    for path in candidates:
        if path.is_file() and not path.is_symlink():
            return path.read_bytes()[:_MAX_FILE_BYTES].decode("utf-8", errors="replace")
    return None


def catalogue_summary() -> dict:
    """The bundled catalogue in numbers, computed from the skill index: total, skills per licence, per shelf."""
    metas = _index()
    licences = Counter(meta.license or "unknown" for meta in metas)
    shelves = [
        {"shelf": entry["shelf"], "title": entry["title"], "skills": entry["skills"]}
        for entry in list_overview()
        if entry["shelf"] != _CUSTOM_CATEGORY
    ]
    return {
        "total": len(metas),
        "licenses": sorted(licences.items(), key=lambda item: (-item[1], item[0])),
        "shelves": shelves,
        "notice_path": str(notice_path()),
        "notice_present": notice_path().is_file(),
    }


def known_categories() -> list[str]:
    """Every category a search or list can be limited to, custom skills' included."""
    return sorted({*list_bundled_categories(), _CUSTOM_CATEGORY})


def check_category(category: str | None) -> None:
    """Raise ValueError naming the valid categories (and the right capitalisation, if that was the slip) when unknown."""
    if category is None:
        return
    valid = known_categories()
    if category in valid:
        return
    folded = [name for name in valid if name.lower() == category.lower()]
    hint = f" Did you mean '{folded[0]}'?" if folded else ""
    raise ValueError(f"unknown category '{category}'.{hint} Valid categories: {', '.join(valid)}")


def _bundled_ids() -> list[str]:
    return [meta.skill_id for meta in _index()]


def suggest_skill_ids(text: str, limit: int = 5) -> list[str]:
    """Up to `limit` skill ids that the (mistyped, wrongly cased or partial) `text` may have meant, best first."""
    import difflib

    ids = _bundled_ids()
    low = text.strip().lower()
    bare = low.rsplit("/", 1)[-1]
    if not bare:
        return []
    names = {sid: sid.split("/", 1)[1].lower() for sid in ids}
    exact = [sid for sid in ids if sid.lower() == low or names[sid] == bare]
    contained = [sid for sid in ids if sid not in exact and len(bare) >= 3 and (bare in names[sid] or names[sid] in bare)]
    by_name: dict[str, list[str]] = defaultdict(list)
    for sid, name in names.items():
        by_name[name].append(sid)
    close = []
    for name in difflib.get_close_matches(bare, list(by_name), n=limit, cutoff=0.6):
        close.extend(by_name[name])
    ordered = list(dict.fromkeys([*exact, *close, *sorted(contained)]))
    return ordered[:limit]


def resolve_skill_id(text: str) -> tuple[str | None, list[str]]:
    """Turn what the user typed into a skill id: (id, []) when it is exact or an unambiguous bare name.

    Otherwise (None, suggestions): the ids a bare name could stand for when it is ambiguous, or the closest ids when
    nothing matches. Matching is exact (including case); a near miss is only suggested.
    """
    text = text.strip()
    if text.startswith(_CUSTOM_PREFIX):
        name = text[len(_CUSTOM_PREFIX):]
        try:
            if read_custom_skill_file(name) is not None:
                return text, []
        except ValueError:
            pass
        return None, [f"{_CUSTOM_PREFIX}{other}" for other in list_custom_skill_names() if name_key(other) == name_key(name)][:5]
    ids = _bundled_ids()
    if text in ids:
        return text, []
    if text and "/" not in text:
        matches = [sid for sid in ids if sid.split("/", 1)[1] == text]
        if len(matches) == 1:
            return matches[0], []
        if matches:
            return None, matches[:5]
    return None, suggest_skill_ids(text)
