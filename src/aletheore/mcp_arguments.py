"""One-line descriptions for every argument of every MCP tool.

Shown to the calling agent in each tool's JSON schema (the `description` of each
property), so it can tell, for example, that `target` is a repo-relative file path
for one tool and a branch name for another. Applied when a tool is registered (see
`describe_arguments`); a test fails when a tool gains an argument without an entry
here. Argument names are the tools' public interface and are never renamed.
"""

import inspect
from collections.abc import Callable
from typing import Annotated, Any

from pydantic import Field

_FILE_PATH = "Repo-relative file path as in the evidence, e.g. src/app.py (aletheore_list kind=modules lists them)."

# The paging arguments the evidence-query tools share.
_PAGING = {
    # Short on purpose: these four are repeated on every evidence-query tool, so each word is paid for 18 times per turn.
    # A truncated result names the sections it cut, which is where an agent learns what to pass for `section`.
    "section": "List to page through: dotted path from the result ('$' for the root). Empty returns the summary.",
    "offset": "Items to skip in `section` (default 0).",
    "limit": "Max items to return from `section`.",
    "filter": "With `section`: keep only items containing this text (case-insensitive).",
}

_TARGET_FILE = {"target": _FILE_PATH}

ARGUMENT_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "aletheore_ask": {
        "question": (
            "The question in plain words, e.g. 'which modules under src/api does nothing import' or "
            "'what breaks if I change src/core/db.py'. It is routed to the matching exact query."
        ),
        "target": (
            "Optional file or directory the question is about, repo-relative as listed in the evidence "
            "(a file for impact, tests, owner and dependency questions; a directory to limit a list). "
            "Leave empty when the question names it or covers the whole repo."
        ),
    },
    "aletheore_locate": {
        "query": "The names to find, as plain words: function, class or variable names, e.g. 'write_usage HelpFormatter'.",
    },
    "aletheore_imports": {**_TARGET_FILE, **_PAGING},
    "aletheore_imported_by": {**_TARGET_FILE, **_PAGING},
    "aletheore_symbols": {**_TARGET_FILE, **_PAGING},
    "aletheore_branch": {"target": "Git branch name exactly as listed in the evidence, e.g. main (aletheore_list kind=branches).", **_PAGING},
    "aletheore_ownership": {
        "target": "Optional repo-relative file path for that file's owners; leave empty for the repo-wide breakdown.",
        **_PAGING,
    },
    "aletheore_secrets": {
        "target": "Repo-relative file path to filter the findings by, e.g. src/config.py.",
        **_PAGING,
    },
    "aletheore_vulnerabilities": dict(_PAGING),
    "aletheore_licenses": dict(_PAGING),
    "aletheore_static_analysis": dict(_PAGING),
    "aletheore_endpoints": dict(_PAGING),
    "aletheore_cluster": {
        "target": "Repo-relative file path; returns the architecture cluster that file belongs to.",
        **_PAGING,
    },
    "aletheore_layer_violations": dict(_PAGING),
    "aletheore_dead_code": dict(_PAGING),
    "aletheore_hotspots": dict(_PAGING),
    "aletheore_database": dict(_PAGING),
    "aletheore_infrastructure": dict(_PAGING),
    "aletheore_environment_variables": dict(_PAGING),
    "aletheore_error_handling": dict(_PAGING),
    "aletheore_changes": {
        "full": "True for the raw path-level diff of the last two scans; False (default) for the curated summary.",
        "max_items": "Cap on each findings list (default 200); 0 returns everything.",
    },
    "aletheore_neighborhood": {
        "target": _FILE_PATH,
        "max_items": "Cap on imports and imported_by (default 20); 0 returns everything.",
    },
    "aletheore_get_blast_radius": {
        "target": "Repo-relative file path whose change you want to assess, e.g. src/app.py.",
        "symbol": "Optional function or class name defined in target; adds the dependents that actually call it.",
    },
    "aletheore_symbol_path": {
        "source": "Repo-relative file path that defines source_symbol.",
        "source_symbol": "Function or class name defined in source where the path starts.",
        "target": "Repo-relative file path that defines target_symbol.",
        "target_symbol": "Function or class name defined in target where the path ends.",
    },
    "aletheore_list": {
        "kind": "What to list: 'modules' (file paths), 'clusters' (architecture cluster ids) or 'branches' (git branch names).",
        "filter": "Optional case-insensitive substring that narrows 'modules'; ignored for the other kinds.",
        "max_items": "Cap on the result (default 200); 0 returns everything.",
    },
    "aletheore_find_skill": {
        "task": "One sentence describing what you are about to do: the technology and the kind of work.",
        "category": "Optional Hallbook category to search within, e.g. 'debugging' or 'security'.",
    },
    "aletheore_find_external_skills": {
        "task": "Short task description, shared with skills.sh only with user authorization; exclude private context, code, logs and secrets. Limited to 200 characters.",
        "limit": "Maximum external recommendations to return (default 5, clamped to 1–8).",
    },
    "aletheore_search_skills": {
        "query": "What you are about to do, in keywords, e.g. 'flaky test' or 'stripe webhooks'.",
        "category": "Optional Hallbook category to search within.",
        "limit": "Maximum matches to return (1 to 25, default 8).",
    },
    "aletheore_list_skills": {
        "category": "Category to list, e.g. 'debugging' or 'custom' for your own skills; leave empty for the library overview.",
    },
    "aletheore_get_skill": {"skill_id": "Skill id as returned by the search and list tools, e.g. debugging/systematic-debugging."},
    "aletheore_get_skill_file": {
        "skill_id": "Skill id as returned by the search and list tools.",
        "path": "File path relative to the skill, as listed in the `files` field of aletheore_get_skill.",
    },
    "aletheore_search": {
        "pattern": "Text to find (literal by default, a regular expression when regex is true).",
        "regex": "True to treat pattern as a regular expression instead of literal text.",
        "path_glob": "Optional glob that limits the search to matching repo-relative paths, e.g. 'src/*.py'.",
    },
    "aletheore_ast_pattern": {
        "language": "Language of the files to search: python, javascript, typescript, go, rust, java, kotlin, ruby, php, c, cpp, csharp or swift.",
        "query": "Tree-sitter S-expression query with at least one @capture naming what to return.",
    },
    "aletheore_symbol_source": {
        "module": "Repo-relative file path exactly as listed in the evidence, e.g. src/app.py.",
        "symbol": "Function or class name alone, e.g. my_function (not path::name).",
        "max_lines": "Lines of source to return before cutting a long symbol (default 120); 0 returns everything.",
    },
    "aletheore_verify_citations": {
        "report_text": "The full report text; every file:line citation in it is checked against the evidence and file lengths.",
    },
    "aletheore_find_evidence_for_endpoint": {
        "method": "HTTP verb of the endpoint, e.g. GET.",
        "path": "Route path exactly as listed in the evidence, e.g. /users/{id}.",
    },
    "aletheore_find_evidence_for_symbol": {"symbol": "Function or class name to resolve to its source evidence."},
    "aletheore_find_evidence_for_dependency": {"dependency": "Import or package name to resolve, e.g. flask."},
    "aletheore_scan": {
        "check_vulnerabilities": "Check dependencies against the OSV.dev vulnerability database (needs network).",
        "scan_git_history": "Walk git history for secrets as well as the working tree.",
        "check_licenses": "Check dependency licenses (needs network).",
        "map_endpoints": "Map the API endpoints the code declares.",
    },
    "aletheore_healthcheck": {"base_url": "Base URL of a running instance, e.g. http://localhost:8000; mapped GET endpoints are requested against it."},
    "aletheore_search_codebase": {
        "query": "What you are looking for, in the repository's own terms (identifiers, file names, concepts).",
        "k": "Number of results to return (default 10).",
        "language": "Optional language filter, matching repository.languages in the evidence, e.g. python.",
    },
    "aletheore_managed_audit": {
        # Description text only, not a credential.
        "token": "Optional Aletheore API token; by default the saved login or ALETHEORE_API_TOKEN is used.",  # nosec B105
    },
    "aletheore_add_custom_skill": {
        "name": "Name for the skill: letters, digits, '-' and '_' only, at most 64 characters.",
        "content": "The skill text (Markdown, at most 1 MB).",
    },
    "aletheore_remove_custom_skill": {"name": "Name of the custom skill to delete, without the 'custom/' prefix."},
    "aletheore_answer": {
        "question": "A natural-language question about this repository.",
        "k": "Number of retrieved code chunks to answer from (default 5).",
    },
}


def describe_arguments(tool_name: str, fn: Callable[..., Any]) -> Callable[..., Any]:
    """`fn` with its signature annotated so each argument carries its description in the tool schema.

    Arguments with no entry are left alone (the test suite fails on those); entries naming an
    argument the function does not have are ignored, so one shared table can serve a family of tools.
    """
    descriptions = ARGUMENT_DESCRIPTIONS.get(tool_name)
    if not descriptions:
        return fn
    signature = inspect.signature(fn)
    parameters = []
    for parameter in signature.parameters.values():
        text = descriptions.get(parameter.name)
        if text:
            annotation = Any if parameter.annotation is inspect.Parameter.empty else parameter.annotation
            parameter = parameter.replace(annotation=Annotated[annotation, Field(description=text)])
        parameters.append(parameter)
    fn.__signature__ = signature.replace(parameters=parameters)  # type: ignore[attr-defined]
    return fn
