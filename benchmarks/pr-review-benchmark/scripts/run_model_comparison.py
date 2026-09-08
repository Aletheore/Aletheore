"""One-off model-comparison runner: reruns the Flash Review arm (generation
only, no second-model verification) of the pr-review-benchmark corpus with
a different model than whatever was used for a saved baseline, holding the
review inputs as close to production as this script can reconstruct.

Built for a real comparison: GPT-5 nano vs the saved gpt-5.6-luna run
(results/token_usage/*_aletheore_flash_luna.json, results/raw/*/aletheore_flash.json).
Not a permanent benchmark-suite script - the corpus, scoring, and 3-way
tool comparison machinery live in the other scripts/ modules; this one
only swaps the model and replays review_diff() with real inputs.

Known, documented deviations from the original saved run:
- file_context is "" (compact mode) by default - matches the CURRENT
  production default (github-app/scan_worker/jobs.py), confirmed by a
  real 3-run benchmark that found compact matches or beats full-context
  inclusion for gpt-5.6-luna specifically. --full-context opts into the
  real production "full content" format instead (fetch_review_file_context's
  own "--- full/test file content: {path} ---\n{content}" shape, capped
  the same way: MAX_CONTEXT_FILES files, MAX_CONTEXT_TOTAL_BYTES total) -
  added to test whether a cheaper/weaker model benefits from more context
  where Luna didn't.
- pr_context is always "" - the corpus's reconstructed diffs have no live
  PR for fetch_pr_context() to call. The original saved Luna run's own
  methodology for this isn't preserved (its runner script wasn't kept),
  so exact parity here can't be verified either way.
- diff_patches is derived by parsing pr.diff's own "diff --git" boundaries
  rather than GitHub's PR-files API response - close enough for
  build_hunk_scope_correction_context's own hunk-boundary logic, which
  only needs (file_path, patch_text) pairs.
- --seed passes an OpenAI `seed` value via extra_body for best-effort
  determinism (OpenAI's own documented caveat: "best effort," not
  guaranteed - a system/model update can still change output even with
  the same seed). Not a production code path - OpenAICompatibleAdapter's
  shared simple_completion() doesn't expose seed as a first-class param,
  so this goes through extra_body instead of touching that shared adapter.

Usage (from github-app/, so scan_worker/aletheore are both importable):
    cd github-app
    OPENAI_API_KEY=... python3 ../benchmarks/pr-review-benchmark/scripts/run_model_comparison.py --model gpt-5-nano --seed 42
"""
import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

BENCHMARK_ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = BENCHMARK_ROOT / "cases"
RESULTS_DIR = BENCHMARK_ROOT / "results"

sys.path.insert(0, str(BENCHMARK_ROOT))
from scripts.build_case_repo import prepare_case_checkout  # noqa: E402

REPO_ROOT = BENCHMARK_ROOT.parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "github-app"))

from aletheore.dead_code import is_test_file  # noqa: E402
from aletheore.evidence import scan_repository  # noqa: E402
from scan_worker.flash_review import (  # noqa: E402
    FLASH_REVIEW_FALLBACK_MODEL,
    build_code_evidence_context,
    build_dependency_impact_context,
    build_referenced_symbol_context,
    review_diff,
)
from scan_worker.flash_review_hunk_scope import build_hunk_scope_correction_context  # noqa: E402
from scan_worker.flash_review_schema_context import build_schema_endpoint_context  # noqa: E402
from scan_worker.github_api import MAX_CONTEXT_FILES, MAX_CONTEXT_TOTAL_BYTES  # noqa: E402
from scan_worker.model_tiers import VERIFICATION_MODEL  # noqa: E402
from aletheore.adapters.openai_compatible import OpenAICompatibleAdapter  # noqa: E402

MAX_CONTEXT_FILE_BYTES = 100_000

# Cases 001-025 are the real PR-review corpus (matches METHODOLOGY.md's
# "24 of 25" scope); the swebench-* directories are a separate corpus this
# script doesn't touch. Case 020 excluded - same corpus-fixture issue the
# saved Luna run also excluded it for (a placeholder secret that isn't
# meant to be reviewed literally).
CASE_IDS = sorted(
    p.name for p in CASES_DIR.iterdir()
    if p.is_dir() and re.match(r"^\d{3}-", p.name) and "020-" not in p.name
)


def _read_repo_pointer(case_dir: Path) -> dict:
    pointer = {}
    for line in (case_dir / "repo.txt").read_text().splitlines():
        if "=" in line:
            key, _, value = line.partition("=")
            pointer[key.strip()] = value.strip()
    return pointer


def _changed_files_from_diff(diff_text: str) -> list[str]:
    # "diff --git a/<path> b/<path>" - the "b/" path is the post-change
    # path, which is what review_diff's file_contents/changed_files expect
    # (matches changed_files elsewhere in this codebase: the new-side path).
    files = []
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff_text, re.MULTILINE):
        files.append(match.group(2))
    return files


def _diff_patches_from_diff(diff_text: str) -> tuple[tuple[str, str], ...]:
    # Splits pr.diff into (file_path, patch_text) pairs at each "diff --git"
    # boundary - the shape build_hunk_scope_correction_context expects,
    # without needing GitHub's PR-files API response this script has no
    # live PR to fetch.
    sections = re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE)
    patches = []
    for section in sections:
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", section, re.MULTILINE)
        if match:
            patches.append((match.group(2), section))
    return tuple(patches)


def _file_contents_for(checkout_dir: Path, changed_files: list[str]) -> dict[str, str]:
    contents = {}
    for path in changed_files:
        full_path = checkout_dir / path
        if not full_path.is_file():
            continue
        try:
            text = full_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if len(text.encode("utf-8")) <= MAX_CONTEXT_FILE_BYTES:
            contents[path] = text
    return contents


def _build_full_file_context(changed_files: list[str], file_contents: dict[str, str]) -> str:
    # Mirrors fetch_review_file_context's real prompt-blob format
    # (github-app/scan_worker/flash_review.py) - same file order, same
    # per-file label, same MAX_CONTEXT_FILES/MAX_CONTEXT_TOTAL_BYTES caps -
    # so switching this on tests only the compact-vs-full question, not a
    # different context shape too.
    parts = []
    total_bytes = 0
    for path in changed_files[:MAX_CONTEXT_FILES]:
        content = file_contents.get(path)
        if content is None:
            continue
        encoded_len = len(content.encode("utf-8"))
        if total_bytes + encoded_len > MAX_CONTEXT_TOTAL_BYTES:
            break
        label = "test file content" if is_test_file(path) else "full content"
        parts.append(f"--- {label}: {path} ---\n{content}")
        total_bytes += encoded_len
    return "\n\n".join(parts)


def run_case(
    case_id: str, model: str, workdir: Path, *,
    seed: int | None = None, full_context: bool = False, verify: bool = False,
) -> dict:
    case_dir = CASES_DIR / case_id
    diff_path = case_dir / "pr.diff"
    diff_text = diff_path.read_text()
    repo_pointer = _read_repo_pointer(case_dir)

    case_workdir = workdir / case_id
    case_workdir.mkdir(parents=True, exist_ok=True)
    checkout_dir = prepare_case_checkout(repo_pointer, diff_path, case_workdir)

    changed_files = _changed_files_from_diff(diff_text)
    diff_patches = _diff_patches_from_diff(diff_text)
    file_contents = _file_contents_for(checkout_dir, changed_files)

    print(f"  scanning {checkout_dir} ...", file=sys.stderr)
    evidence = scan_repository(
        checkout_dir,
        check_vulnerabilities=False,
        scan_git_history=False,
        check_licenses=False,
        map_endpoints=True,
        map_schema=True,
        progress=None,
    )

    code_evidence_context = build_code_evidence_context(evidence, changed_files)
    dependency_impact_context = build_dependency_impact_context(evidence, changed_files)
    if dependency_impact_context:
        code_evidence_context = "\n\n".join(
            part for part in (code_evidence_context, dependency_impact_context) if part
        )
    schema_endpoint_context = build_schema_endpoint_context(evidence, changed_files, file_contents)
    if schema_endpoint_context:
        code_evidence_context = "\n\n".join(
            part for part in (code_evidence_context, schema_endpoint_context) if part
        )
    hunk_scope_context = build_hunk_scope_correction_context(file_contents, diff_patches)
    if hunk_scope_context:
        code_evidence_context = "\n\n".join(
            part for part in (code_evidence_context, hunk_scope_context) if part
        )

    def _fetch_symbol_source(file_path: str, start_line: int, end_line: int) -> str | None:
        full_path = checkout_dir / file_path
        if not full_path.is_file():
            return None
        try:
            lines = full_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return None
        return "\n".join(lines[start_line - 1:end_line])

    referenced_symbol_context = build_referenced_symbol_context(
        evidence, changed_files, diff_text, _fetch_symbol_source
    )

    usage_records: list[dict] = []
    verification_usage_records: list[dict] = []

    def _on_usage(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
        usage_records.append({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
        })

    def _on_verification_usage(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
        verification_usage_records.append({
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "cached_tokens": cached_tokens,
        })

    adapter = OpenAICompatibleAdapter(
        name="OpenAI",
        base_url="https://api.openai.com/v1",
        api_key_env_var="OPENAI_API_KEY",
        model=model,
        on_usage=_on_usage,
        extra_body={"seed": seed} if seed is not None else None,
    )

    file_context = _build_full_file_context(changed_files, file_contents) if full_context else ""

    print(
        f"  calling {model} (seed={seed}, full_context={full_context}, "
        f"verify={verify} [{VERIFICATION_MODEL if verify else '-'}]) ...",
        file=sys.stderr,
    )
    findings = review_diff(
        diff_text,
        file_context=file_context,
        code_evidence_context=code_evidence_context,
        referenced_symbol_context=referenced_symbol_context,
        pr_context="",
        model_used=model,
        file_contents=file_contents,
        diff_patches=diff_patches,
        adapter=adapter,
        # AIR tier's real structure: second-model verification only ever
        # re-checks an LLM finding that lacks a verifiable content citation
        # (review_diff's own needs_recheck gate) - this is the exact
        # production path (_verify_findings_with_second_model), always
        # against VERIFICATION_MODEL (deepseek-v4-flash), not a
        # benchmark-only substitute.
        verify_with_second_model=verify,
        on_verification_usage=_on_verification_usage,
    )

    shutil.rmtree(checkout_dir, ignore_errors=True)

    usage = {"model": model, "generation_usage": usage_records}
    if verify:
        usage["verification_model"] = VERIFICATION_MODEL
        usage["verification_usage"] = verification_usage_records

    return {
        "findings": findings,
        "usage": usage,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True, help="model name to pass to the OpenAI-compatible adapter")
    parser.add_argument("--cases", nargs="*", default=None, help="specific case ids to run (default: all)")
    parser.add_argument(
        "--seed", type=int, default=None,
        help="OpenAI seed for best-effort determinism (passed via extra_body)",
    )
    parser.add_argument(
        "--full-context", action="store_true",
        help="use production's real full-file-content prompt shape instead of compact/empty",
    )
    parser.add_argument(
        "--verify", action="store_true",
        help="AIR tier's real structure: second-model verification via deepseek-v4-flash "
        "(review_diff's verify_with_second_model=True) over findings lacking a verifiable citation",
    )
    parser.add_argument(
        "--tag", default="",
        help="suffix for the output directory name, so repeat/variant runs don't overwrite each other "
        "(e.g. --tag run1, --tag fullctx)",
    )
    args = parser.parse_args()

    model_slug = args.model.replace(".", "").replace("-", "") + (f"_{args.tag}" if args.tag else "")
    raw_dir = RESULTS_DIR / f"raw_{model_slug}"
    token_dir = RESULTS_DIR / "token_usage"
    raw_dir.mkdir(parents=True, exist_ok=True)
    token_dir.mkdir(parents=True, exist_ok=True)

    case_ids = args.cases or CASE_IDS
    print(
        f"Running {len(case_ids)} cases with model={args.model} seed={args.seed} "
        f"full_context={args.full_context} verify={args.verify} tag={args.tag!r}",
        file=sys.stderr,
    )

    total_prompt = 0
    total_completion = 0
    total_verify_prompt = 0
    total_verify_completion = 0

    with tempfile.TemporaryDirectory(prefix="model-comparison-") as tmp:
        workdir = Path(tmp)
        for case_id in case_ids:
            print(f"=== {case_id} ===", file=sys.stderr)
            try:
                result = run_case(
                    case_id, args.model, workdir,
                    seed=args.seed, full_context=args.full_context, verify=args.verify,
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  FAILED: {type(exc).__name__}: {exc}", file=sys.stderr)
                continue

            case_raw_dir = raw_dir / case_id
            case_raw_dir.mkdir(parents=True, exist_ok=True)
            (case_raw_dir / "aletheore_flash.json").write_text(
                json.dumps(result["findings"], indent=2)
            )
            (token_dir / f"{case_id}_aletheore_flash_{model_slug}.json").write_text(
                json.dumps(result["usage"], indent=2)
            )

            for u in result["usage"]["generation_usage"]:
                total_prompt += u["prompt_tokens"]
                total_completion += u["completion_tokens"]
            for u in result["usage"].get("verification_usage", []):
                total_verify_prompt += u["prompt_tokens"]
                total_verify_completion += u["completion_tokens"]

            print(
                f"  {len(result['findings'])} finding(s), "
                f"{sum(u['prompt_tokens'] for u in result['usage']['generation_usage'])} prompt / "
                f"{sum(u['completion_tokens'] for u in result['usage']['generation_usage'])} completion "
                f"gen tokens, "
                f"{sum(u['prompt_tokens'] for u in result['usage'].get('verification_usage', []))} prompt / "
                f"{sum(u['completion_tokens'] for u in result['usage'].get('verification_usage', []))} "
                f"completion verify tokens",
                file=sys.stderr,
            )

    print(
        f"\nTotal generation: {total_prompt} prompt / {total_completion} completion tokens\n"
        f"Total verification: {total_verify_prompt} prompt / {total_verify_completion} completion tokens",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
