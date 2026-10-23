"""One entry point that picks the right Aletheore evidence query from a plain-language question.

Aletheore exposes about thirty MCP tools. Every tool definition sits in the agent's context for the whole
session, and agents pick the wrong tool or none. `aletheore_ask` is the single tool an agent can carry instead:
it reads the question, decides which query answers it, runs that query and says which one it ran.

The router is deterministic, built the way `find_skill` was: keyword rules with a stated confidence, no model.

- high: an intent matched and everything it needs (usually a file) was found. The answer comes back directly.
- low: an intent matched but a required target is missing or ambiguous. Nothing is guessed; the reply names
  the tool that would answer and what it needs.
- none: nothing matched. The reply says so and lists the tools worth trying, so the agent does not loop on it.

`route` is pure (no I/O) so it can be measured on a labelled question set; `answer` runs the chosen query.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Callable

# Intent -> (keyword patterns, tool the answer comes from, whether a file target is required).
# Order matters only for ties; scoring decides. Patterns are written for how developers phrase a request to an
# agent, and were checked against the labelled set in src/tests/test_tool_router.py.


@dataclass(frozen=True)
class Intent:
    name: str
    tool: str
    patterns: tuple[str, ...]
    needs_file: bool = False
    weight: float = 1.0


INTENTS: tuple[Intent, ...] = (
    Intent(
        "impact",
        "aletheore_get_blast_radius",
        (
            r"\bblast radius\b",
            r"\bwhat (?:will|would|could|might) (?:break|be affected|change)\b",
            r"\b(?:break|breaks|breaking)\b",
            r"\bimpact\b",
            r"\baffect(?:s|ed)?\b",
            r"\bsafe to (?:change|rename|delete|remove|modify|refactor|edit)\b",
            r"\b(?:rename|refactor|delete|remove|change|modify) (?:it|this|the)\b",
            r"\bdepend(?:s|ent|ents|ency|encies)? on\b",
            r"\bwho (?:uses|calls|imports|depends on)\b",
            r"\b(?:callers?|dependents?|importers?|consumers?) of\b",
            r"\bused by\b",
        ),
        needs_file=True,
        weight=1.2,
    ),
    Intent(
        "tests",
        "tests_for",
        (r"\btests?\b", r"\bcovered\b", r"\bcoverage\b", r"\bwhich specs?\b", r"\bwhat should i run\b"),
        needs_file=True,
    ),
    Intent(
        "owner",
        "aletheore_ownership",
        (
            r"\bwho do i ask\b", r"\bmain person\b", r"\bwho knows\b", r"\bwhich (?:engineer|developer|dev|person|team)\b", r"\bknows the most\b", r"\bwho(?:\'s| is) responsible\b",
            r"\bwho (?:owns|wrote|maintains|authored|knows|should review)\b",
            r"\bowner(?:s|ship)?\b",
            r"\bmaintainers?\b",
            r"\bblame\b",
            r"\bcode ?owners?\b",
        ),
        weight=1.3,
    ),
    Intent(
        "hotspot",
        "aletheore_hotspots",
        (
            r"\bfrequently (?:changed|edited|modified|touched)\b", r"\bmost commits\b", r"\bkeep(?:s)? (?:breaking|coming back)\b", r"\bbug magnets?\b", r"\bchanged together\b", r"\bunstable\b",
            r"\bhot ?spots?\b",
            r"\bchurn\b",
            r"\bchang(?:es|ed) (?:often|a lot|frequently|most)\b",
            r"\bmost often\b",
            r"\bmost (?:changed|edited|modified|volatile)\b",
            r"\bfragile\b",
            r"\brisky files?\b",
            r"\bco-?chang\w+\b",
            r"\bedited together\b",
        ),
        weight=1.2,
    ),
    Intent(
        "secrets",
        "aletheore_secrets",
        (
            r"\b(?:tokens?|passwords?|private keys?|aws keys?)\b.*\b(?:commit\w*|checked in|in the (?:repo|code|source))\b", r"\b(?:commit\w*|checked in)\b.*\b(?:tokens?|passwords?|keys?|secrets?)\b", r"\baws keys?\b",r"\bsecrets?\b", r"\bleak(?:ed|s|ing)?\b", r"\bhard-?coded (?:key|token|password|credential)s?\b", r"\bapi keys?\b", r"\bcredentials?\b"),
    ),
    Intent(
        "vulnerabilities",
        "aletheore_vulnerabilities",
        (
            r"\bnpm audit\b", r"\bpip audit\b", r"\bsecurity (?:holes?|updates?|patches?) (?:in|for) (?:our |the )?(?:dependenc|package|librar)\w*\b", r"\bvulnerable (?:packages?|libs?|libraries|deps|dependencies)\b",r"\bvulnerab\w+\b", r"\bcves?\b", r"\badvisor(?:y|ies)\b", r"\boutdated (?:dependenc|package)\w*\b", r"\binsecure dependenc\w+\b"),
        weight=1.2,
    ),
    Intent(
        "static_analysis",
        "aletheore_static_analysis",
        (
            r"\bsql injection\b", r"\bxss\b", r"\bscanners?\b", r"\bcode smells?\b", r"\binjection\b",r"\bstatic analysis\b", r"\blint(?:er|ing)? (?:findings|issues|warnings)\b", r"\bsemgrep\b", r"\bbandit\b", r"\bsonar\w*\b", r"\bsecurity (?:findings|issues|scan)\b"),
        weight=1.1,
    ),
    Intent(
        "dead_code",
        "aletheore_dead_code",
        (
            r"\bnobody (?:imports|uses|calls|references)\b", r"\bno other (?:\w+ ){0,3}(?:modules?|files?)\b[^.?]*\bimports?\b",
            r"\b(?:un-?imported|no importers?|zero importers?|not imported by (?:any|anything))\b", r"\bsafely delete\b", r"\bcut the fat\b", r"\bdead (?:files?|modules?|functions?)\b", r"\borphan\w*\b",r"\bdead code\b", r"\bunused (?:code|files?|modules?|functions?)\b", r"\bunreachable\b", r"\bnever (?:used|called|imported)\b", r"\bsafe to delete\b"),
        weight=1.2,
    ),
    Intent(
        "layers",
        "aletheore_layer_violations",
        (
            r"\breach(?:es|ing)? into\b", r"\b(?:ui|view|controller|handler)s? (?:code )?(?:imports?|calls?|depends on)\b.*\b(?:db|database|persistence|storage|repository)\b", r"\bpersistence\b.*\b(?:ui|view)\b", r"\bcircular (?:import|dependenc)\w*\b",r"\blayer(?:s|ing)?\b", r"\barchitecture (?:violation|rule)s?\b", r"\bboundar(?:y|ies) (?:violation|rule)s?\b", r"\bimport (?:rule|direction)s?\b"),
    ),
    Intent(
        "endpoints",
        "aletheore_endpoints",
        (
            r"\burls?\b", r"\baccepts? (?:post|get|put|delete|patch)\b", r"\bexternal client\b", r"\bcan (?:an? )?(?:client|user|caller)s? call\b", r"\b(?:post|get|put|delete|patch) (?:routes?|endpoints?|handlers?)\b",r"\bendpoints?\b", r"\broutes?\b", r"\bapi (?:surface|routes?|paths?)\b", r"\bhttp handlers?\b"),
    ),
    Intent(
        "env",
        "aletheore_environment_variables",
        (
            r"\.env\b", r"(?-i:\b[A-Z][A-Z0-9]*_[A-Z0-9_]{2,}\b)", r"\bboot\w* (?:this|the)\b.*\benv\b",r"\benv(?:ironment)? (?:vars?|variables?)\b", r"\bconfig(?:uration)? (?:keys?|options?|settings?)\b", r"\bos\.environ\b", r"\bprocess\.env\b"),
    ),
    Intent(
        "database",
        "aletheore_database",
        (
            r"\bpersist\w*\b", r"\bcolumns?\b", r"\bsql tables?\b", r"\bdata ?model\b",r"\bdatabases?\b", r"\btables?\b", r"\bschema\b", r"\bmigrations?\b", r"\borm\b", r"\bmodels? (?:and|&) (?:tables|columns)\b"),
    ),
    Intent(
        "infrastructure",
        "aletheore_infrastructure",
        (
            r"\bgithub actions?\b", r"\bworkflows?\b", r"\bpipelines?\b", r"\bhelm\b", r"\bcontainers?\b",r"\binfra(?:structure)?\b", r"\bdocker(?:file)?\b", r"\bterraform\b", r"\bkubernetes\b", r"\bk8s\b", r"\bci(?:/cd)?\b", r"\bdeploy(?:ment)? (?:config|pipeline|workflow|setup|process)\b"),
        weight=0.9,
    ),
    Intent(
        "errors",
        "aletheore_error_handling",
        (
            r"\bhandle(?:s)? errors?\b", r"\bempty catch\b", r"\bcatch(?:es)? (?:exception|error)s?\b", r"\bexcept(?:ion)?\b.*\bpass\b", r"\bswallowed?\b",r"\berror[- ]handling\b", r"\bexception handling\b", r"\bswallow\w*\b", r"\bbare except\b", r"\bcatch(?:es)? all\b"),
    ),
    Intent(
        "licenses",
        "aletheore_licenses",
        (
            r"\bship commercially\b", r"\bcommercial use\b", r"\blibs? we pull in\b", r"\bopen[- ]source terms\b", r"\blegal\b",r"\blicen[sc]es?\b", r"\bgpl\b", r"\bcopyleft\b"),
    ),
    Intent(
        "neighborhood",
        "aletheore_neighborhood",
        (
            r"\bwhat imports\b", r"\bwhat (?:does|do) \S+ import\b", r"\bimport graph\b", r"\bimports? (?:around|of|for)\b", r"\bwhat(?:\'s| is) (?:next to|around|connected to)\b", r"\bimported by\b",
            r"\bwhat (?:does|do) \S+ (?:import|depend on)\b",
            r"\bimports? of\b",
            r"\bneighbou?rs?\b",
            r"\bcluster\b",
            r"\bsame (?:module|package|area)\b",
            r"\bnear(?:by)?\b",
        ),
        needs_file=True,
    ),
    Intent(
        "symbols",
        "aletheore_symbols",
        (
            r"\b(?:classes|functions|methods|symbols|exports|definitions) (?:are )?(?:defined )?(?:in|of|inside)\b", r"\boutline\b", r"\bevery (?:method|function|class)\b", r"\bsymbols? in\b", r"\bwhat(?:\'s| is) (?:exported|public)\b",r"\b(?:list|show|what are) (?:the )?(?:functions|classes|symbols|methods)\b", r"\bpublic (?:api|functions|symbols)\b", r"\bwhat(?:'s| is) (?:defined|in) (?:this|the) file\b"),
        needs_file=True,
    ),
    Intent(
        "overview",
        "aletheore_overview",
        (
            r"\bnew here\b", r"\bhow is the code organi[sz]ed\b", r"\bwalk me through\b", r"\bhigh[- ]level\b", r"\bwhat i(?:\'m| am) looking at\b", r"\bbefore i start\b", r"\blay of the land\b", r"\btour\b",
            r"\boverview\b",
            r"\bwhat is this (?:repo|repository|project|codebase)\b",
            r"\bhow is (?:this|the) (?:repo|repository|project|codebase) (?:organi[sz]ed|structured|laid out)\b",
            r"\bmain (?:modules?|areas?|parts?|components?)\b",
            r"\bget oriented\b",
            r"\bnew to (?:this|the) (?:repo|codebase|project)\b",
            r"\bcodebase (?:map|structure|layout)\b",
            r"\bwhere (?:do|should) i start\b",
        ),
        weight=1.2,
    ),
    Intent(
        "locate",
        "aletheore_locate",
        (
            r"\bpoint me to\b", r"\bfind where\b", r"\bwhere (?:\w+ ){0,3}(?:get|gets|is|are) \w+\b", r"\bwhich (?:module|class|function) (?:handles?|does|is responsible)\b", r"\bsearch for\b", r"\bwho handles\b",
            r"\bwhere (?:is|are|does|do|can i find|did)\b",
            r"\bfind (?:the )?(?:code|function|class|file|definition|implementation)\b",
            r"\blocate\b",
            r"\bwhich file\b",
            r"\bdefined\b",
            r"\bimplemented\b",
            r"\bhandles?\b",
            r"\bhow does \S+ work\b",
        ),
        weight=0.8,
    ),
    Intent(
        "skill",
        "aletheore_find_skill",
        (
            r"\btips? for\b", r"\b(?:right|recommended|proper|correct|idiomatic) way\b", r"\bpatterns? for\b", r"\bguide to\b", r"\bhow to\b",
            r"\bhow (?:do|should) i\b",
            r"\bbest practices?\b",
            r"\bsteps? to\b",
            r"\bis there a skill\b",
            r"\b(?:set up|setup|integrate|configure)\b",
            r"\bwrite a (?:plan|spec|test plan)\b",
        ),
        weight=0.6,
    ),
)

_ACTION_OK = frozenset({"impact", "tests", "owner", "locate", "symbols", "neighborhood"})
_INTENTS_BY_NAME = {intent.name: intent for intent in INTENTS}
_COMPILED = {intent.name: tuple(re.compile(p, re.IGNORECASE) for p in intent.patterns) for intent in INTENTS}

# A path-looking token: contains a slash or ends in a known source extension.
_PATH_TOKEN = re.compile(r"[\w@./+-]+\.(?:py|pyi|js|jsx|ts|tsx|mjs|cjs|go|rs|rb|php|java|kt|swift|cs|cpp|cc|c|h|hpp|scala|sql|sh|vue|svelte)\b|[\w@.+-]+(?:/[\w@.+-]+)+")
_IDENT_TOKEN = re.compile(r"[A-Za-z_][A-Za-z0-9_]{3,}")
_STOP = frozenset(
    "this that what which where when does should would could have with from there their about into your "
    "file files code function class module the and for are not but all any can how why who whom".split()
)


# --------------------------------------------------------------------------------------------------
# Phrasing bank: the semantic fallback, built the way Hallbook's search was. Each intent owns a few dozen
# generated ways of asking for it; BM25 over those catches questions that avoid every keyword rule above.
# --------------------------------------------------------------------------------------------------

_PHRASINGS_PATH = Path(__file__).parent / "data" / "router_phrasings.json"
_BM25_K1 = 1.2
_BM25_B = 0.75
# Calibrated on the tuning question set, reported on a held-out one (see docs/superpowers/specs).
BM25_MIN_SCORE = 5.0
BM25_MIN_MARGIN = 1.25
_WORD = re.compile(r"[a-z0-9]+")
_NOISE = frozenset("the a an of to in is are do does did i we you it this that my our for on and or with be can how what which".split())
_bank: dict | None = None


def _terms(text: str) -> list[str]:
    out = []
    for word in _WORD.findall(text.lower()):
        if word in _NOISE:
            continue
        if len(word) > 4 and word.endswith("ies"):
            word = word[:-3] + "y"
        elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
            word = word[:-1]
        out.append(word)
    return out


def _load_bank() -> dict:
    """{'docs': {intent: Counter-like dict}, 'df': {term: n}, 'avg': float}; empty when the file is absent."""
    global _bank
    if _bank is not None:
        return _bank
    docs: dict[str, dict[str, int]] = {}
    try:
        raw = json.loads(_PHRASINGS_PATH.read_text())
    except (OSError, ValueError):
        raw = {}
    for intent, phrases in raw.items():
        if intent not in _INTENTS_BY_NAME or not isinstance(phrases, list):
            continue
        counts: dict[str, int] = {}
        for phrase in phrases:
            for term in _terms(str(phrase)):
                counts[term] = counts.get(term, 0) + 1
        docs[intent] = counts
    df: dict[str, int] = {}
    for counts in docs.values():
        for term in counts:
            df[term] = df.get(term, 0) + 1
    lengths = [sum(c.values()) for c in docs.values()]
    _bank = {"docs": docs, "df": df, "avg": (sum(lengths) / len(lengths)) if lengths else 0.0, "n": len(docs)}
    return _bank


def bm25_rank(question: str) -> list[tuple[str, float]]:
    """Intents ranked by BM25 against their phrasing bank, best first. Empty when there is no bank."""
    bank = _load_bank()
    if not bank["n"]:
        return []
    terms = _terms(question)
    scores = []
    for intent, counts in bank["docs"].items():
        length = sum(counts.values())
        total = 0.0
        for term in set(terms):
            tf = counts.get(term, 0)
            if not tf:
                continue
            n = bank["df"][term]
            idf = math.log(1 + (bank["n"] - n + 0.5) / (n + 0.5))
            total += idf * tf * (_BM25_K1 + 1) / (tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * length / bank["avg"]))
        scores.append((intent, total))
    scores.sort(key=lambda pair: -pair[1])
    return scores


def bm25_intent(question: str) -> Intent | None:
    """The intent the phrasing bank is sure about, or None when it is not."""
    ranked = bm25_rank(question)
    if len(ranked) < 2:
        return None
    (best, top), (_, second) = ranked[0], ranked[1]
    if top < BM25_MIN_SCORE or top < BM25_MIN_MARGIN * max(second, 1e-9):
        return None
    return _INTENTS_BY_NAME[best]


@dataclass
class Route:
    confidence: str  # high | low | none
    intent: str | None = None
    tool: str | None = None
    target: str | None = None
    symbol: str | None = None
    reason: str = ""
    candidates: list[str] = field(default_factory=list)
    scope: str | None = None  # a directory (or file) the question limits itself to; set for repo-wide listings


# A test qualifier ("NON-TEST modules", "excluding tests") or a pasted definition of what a test file is ("Test
# files are any path containing a tests/ directory ...") describes how to filter the answer. It says nothing about
# what is being asked, but its "test" word used to win the "tests" intent over the real one.
_TEST_QUALIFIER = re.compile(
    r"\btest[- ]files?\s+(?:are|is|means?|include)\b.*?\.(?=\s|$)"
    r"|\bnon[- ]?tests?\b(?:\s+files?)?"
    r"|\b(?:excluding|excluded|ignoring|without|skipping|not counting)\s+(?:the\s+)?tests?(?:\s+files?)?\b",
    re.IGNORECASE | re.DOTALL,
)


def strip_test_qualifiers(question: str) -> str:
    return _TEST_QUALIFIER.sub(" ", question)


# A multi-segment path names where to look, not what to look for. Its words (a `setup/` directory, a `deploy_config`
# file) used to score as intents of their own.
_SLASH_PATH = re.compile(r"[\w@.+-]+(?:/[\w@.+-]+)+/?")


def intent_text_of(question: str) -> str:
    """The question with test qualifiers and directory-style paths removed, for intent scoring only."""
    return _SLASH_PATH.sub(" ", strip_test_qualifiers(question))


def resolve_scope(question: str, explicit: str | None, paths: list[str]) -> str | None:
    """The directory the question limits itself to: an explicit target or a path in the text that is a prefix
    of known modules. A path that is itself a module is a file target, handled by resolve_target, not a scope."""
    known = set(paths)
    candidates = ([explicit] if explicit else []) + _PATH_TOKEN.findall(question)
    for token in candidates:
        token = token.strip().strip("`'\"()[],;:").removeprefix("./").rstrip("/")
        if not token or token in known:
            continue
        if any(p.startswith(token + "/") for p in paths):
            return token
    return None


def resolve_languages(question: str, languages: set[str]) -> list[str]:
    """Languages the scan knows that the question names ("Python modules"). Short names like go are skipped
    because they are ordinary words."""
    return sorted(
        lang for lang in languages if len(lang) >= 4 and re.search(rf"\b{re.escape(lang)}\b", question, re.IGNORECASE)
    )


def _score(question: str) -> list[tuple[float, Intent]]:
    scored = []
    for intent in INTENTS:
        hits = sum(1 for pattern in _COMPILED[intent.name] if pattern.search(question))
        if hits:
            scored.append((hits * intent.weight, intent))
    scored.sort(key=lambda pair: -pair[0])
    return scored


def resolve_target(question: str, explicit: str | None, paths: list[str]) -> tuple[str | None, list[str]]:
    """The file the question is about: an explicit target, a path in the text, or a unique name match.

    Returns (path, ambiguous_candidates). A path is only returned when it names exactly one module the scan
    knows. Several matches come back as candidates and no path, because guessing which `utils.py` was meant
    is how a router earns a wrong answer.
    """
    known = set(paths)

    def lookup(token: str) -> tuple[str | None, list[str]]:
        token = token.strip().strip("`'\"()[],;:").removeprefix("./")
        if not token:
            return None, []
        if token in known:
            return token, []
        suffix = [p for p in paths if p.endswith("/" + token)]
        if len(suffix) == 1:
            return suffix[0], []
        if len(suffix) > 1:
            return None, sorted(suffix)[:5]
        return None, []

    if explicit:
        found, ambiguous = lookup(explicit)
        if found or ambiguous:
            return found, ambiguous
    for token in _PATH_TOKEN.findall(question):
        found, ambiguous = lookup(token)
        if found or ambiguous:
            return found, ambiguous
    # Bare stems like "query" or "jobs" are too ambiguous to trust; only whole-name tokens count.
    by_stem: dict[str, list[str]] = {}
    for path in paths:
        by_stem.setdefault(PurePosixPath(path).stem, []).append(path)
    for token in _IDENT_TOKEN.findall(question):
        if token.lower() in _STOP:
            continue
        matches = by_stem.get(token, [])
        if len(matches) == 1 and "_" in token:  # snake_case module names are distinctive enough to trust
            return matches[0], []
    return None, []


_ACTION = re.compile(
    r"^\s*(?:please\s+)?(?:run|execute|start|stop|restart|deploy|ship|push|merge|commit|build|install|write|create|add|implement|"
    r"fix|generate|delete|remove|rename|refactor|update|upgrade|tell me a|make|send|open|close|draft|translate|summari[sz]e|explain)\b",
    re.IGNORECASE,
)


def route(question: str, explicit_target: str | None, paths: list[str]) -> Route:
    """Decide which query answers `question`. Pure: takes the scan's module paths, touches nothing."""
    question = (question or "").strip()
    if not question:
        return Route("none", reason="empty question", candidates=_menu())
    intent_text = intent_text_of(question)
    scored = _score(intent_text)
    target, ambiguous = resolve_target(question, explicit_target, paths)
    if _ACTION.match(question) and not (scored and scored[0][1].name in _ACTION_OK):
        # "run the test suite", "deploy this": a request to act, which a read-only scan cannot do.
        return Route("none", reason="a request to act, not a question about the code", candidates=_menu())
    if not scored:
        fallback = bm25_intent(intent_text)
        if fallback is not None:
            scored = [(1.0, fallback)]
    if not scored:
        if target:
            # A file and no verb: the cheapest useful answer is what depends on it.
            return Route("high", "impact", "aletheore_get_blast_radius", target, reason="a file with no stated intent")
        return Route("none", reason="no intent matched", candidates=_menu())
    top_score, top = scored[0]
    # "tests" and "impact" often co-occur ("which tests break if I change x"): tests wins when it is named explicitly.
    if top.name == "impact" and any(i.name == "tests" for _, i in scored) and re.search(r"\btests?\b", intent_text, re.I):
        top = _INTENTS_BY_NAME["tests"]
    if top.name == "skill" and target:
        # A concrete file in the question means a code question, not a how-to.
        top = _INTENTS_BY_NAME["impact" if len(scored) == 1 else scored[1][1].name]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    if top.needs_file and target is None:
        reason = "needs a file path"
        if ambiguous:
            reason = "several files match: " + ", ".join(ambiguous)
        return Route("low", top.name, top.tool, None, reason=reason, candidates=ambiguous)
    if top_score - runner_up < 0.5 and len(scored) > 1 and top.name not in ("impact", "tests", "owner"):
        # A near tie between unrelated intents is not worth a confident wrong answer.
        return Route(
            "low", top.name, top.tool, target, reason="the question fits more than one query",
            candidates=[i.tool for _, i in scored[:3]],
        )
    scope = (resolve_scope(question, explicit_target, paths) or target) if top.name == "dead_code" else None
    return Route("high", top.name, top.tool, target, reason=f"matched {top.name}", scope=scope)


def _menu() -> list[str]:
    return [
        "aletheore_locate (where is X defined)",
        "aletheore_get_blast_radius (what breaks if I change a file)",
        "aletheore_overview (what is this repo)",
    ]


# --------------------------------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------------------------------

ANSWER_MAX_CHARS = 6000


def answer(question: str, target: str | None, evidence: dict, repo_path: Path) -> dict:
    """Route `question` and run the chosen query. Returns a dict describing what happened."""
    from aletheore import query as q

    paths = [m["path"] for m in evidence["repository"]["modules"] if m.get("path")]
    decision = route(question, target, paths)
    result: dict = {"routed_to": decision.tool, "confidence": decision.confidence}
    if decision.confidence == "none":
        result["answer"] = "No clear match for this question."
        result["try"] = decision.candidates
        return result
    if decision.confidence == "low":
        result["why"] = decision.reason
        if decision.target is None and decision.tool:
            result["next"] = f"call {decision.tool} with a file path, or ask again naming the file"
        if decision.candidates:
            result["candidates"] = decision.candidates
        return result

    result["target"] = decision.target
    if decision.scope:
        result["scope"] = decision.scope
    runner = _RUNNERS.get(decision.intent or "")
    if runner is None:
        result["answer"] = f"matched {decision.intent}; call {decision.tool} directly"
        return result
    try:
        # A repo-wide listing takes its directory limit as the target slot; the others take a single file.
        result["answer"] = runner(q, evidence, repo_path, decision.scope or decision.target, question)
        if decision.intent == "skill" and isinstance(result["answer"], dict):
            # find_skill carries its own calibrated confidence; the router must not overstate it.
            result["confidence"] = result["answer"].get("confidence", result["confidence"])
    except q.ModuleNotFoundInEvidenceError as exc:
        result["error"] = str(exc)
    return result


def _locate(q, evidence, repo_path, target, question):
    from aletheore.code_locator import locate_report

    return locate_report(evidence, repo_path, question)


def _tests_for(q, evidence, repo_path, target, question):
    from aletheore.tests_for import tests_for_report

    return tests_for_report(evidence, repo_path, target)


def _find_skill(q, evidence, repo_path, target, question):
    from aletheore import skill_hub

    return skill_hub.find_skill(question)


def _impact(q, evidence, repo_path, target, question):
    return q.find_blast_radius(evidence, repo_path, target)


def _neighborhood(q, evidence, repo_path, target, question):
    return {
        "imports": q.find_imports(evidence, target),
        "imported_by": q.find_imported_by(evidence, target),
    }


def _owner(q, evidence, repo_path, target, question):
    return q.find_ownership(evidence, target)


def _hotspot(q, evidence, repo_path, target, question):
    spots = q.find_hotspots(evidence, None)
    if target:
        mine = [s for s in spots if s.get("path") == target]
        if mine:
            return mine
    return spots[:10]


def _dead_code(q, evidence, repo_path, target, question):
    # `target` is the directory (or file) the question is limited to, or None for the whole repo.
    languages = {str(m.get("language") or "").lower() for m in evidence["repository"]["modules"]} - {""}
    cleaned = strip_test_qualifiers(question)
    # A "non-test" question counts only non-test importers: a file only its own tests import is still unimported.
    return q.find_unimported_modules(
        evidence, target, resolve_languages(cleaned, languages), ignore_test_importers=cleaned != question
    )


def _secrets(q, evidence, repo_path, target, question):
    return q.find_secrets_for_file(evidence, target) if target else q.find_secrets_for_file(evidence, None)


_RUNNERS: dict[str, Callable] = {
    "impact": _impact,
    "tests": _tests_for,
    "owner": _owner,
    "hotspot": _hotspot,
    "secrets": _secrets,
    "vulnerabilities": lambda q, e, r, t, qu: q.find_vulnerabilities(e, None),
    "static_analysis": lambda q, e, r, t, qu: q.find_static_analysis(e, None),
    "dead_code": _dead_code,
    "layers": lambda q, e, r, t, qu: q.find_layer_violations(e, None),
    "endpoints": lambda q, e, r, t, qu: q.find_endpoints(e, None),
    "env": lambda q, e, r, t, qu: q.find_environment_variables(e, None),
    "database": lambda q, e, r, t, qu: q.find_database(e, None),
    "infrastructure": lambda q, e, r, t, qu: q.find_infrastructure(e, None),
    "errors": lambda q, e, r, t, qu: q.find_error_handling(e, None),
    "licenses": lambda q, e, r, t, qu: q.find_licenses(e, None),
    "neighborhood": _neighborhood,
    "symbols": lambda q, e, r, t, qu: q.find_symbols(e, t),
    "overview": lambda q, e, r, t, qu: q.find_repo_overview(e),
    "locate": _locate,
    "skill": _find_skill,
}
