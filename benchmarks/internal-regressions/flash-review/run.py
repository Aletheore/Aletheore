#!/usr/bin/env python3
"""Internal Flash Review regression set: real PRs it reviewed and got wrong.

NOT part of the published PR-review benchmark and never compared with any other
tool (that corpus is fixed; changing it would mean re-running every competitor).
This only asks: does Flash Review, run exactly as production runs it for a paid
installation, catch these specific defects? Each case is a diff that Flash Review
reviewed on a real PR and called clean while a human-verifiable defect was in it.

It mirrors scan_worker/jobs.py's current paid-tier call to review_diff():
GLM-5.3-Flash via flash_review_generation_adapter, per_file_completeness and
rank_findings on, cross-file check off (its production default), shared PR
context on, sibling-file context off, no similarity cache. Inputs are built from a
checkout of the PR's head commit, like production builds them from the head SHA.

Usage (from github-app/, so scan_worker and aletheore are importable):
    cd github-app
    python3 ../benchmarks/internal-regressions/flash-review/run.py --dry-run
    INDIEROUTER_API_KEY=... python3 ../benchmarks/internal-regressions/flash-review/run.py --runs 3

    # Same prompts and inputs, Claude Haiku instead of GLM (needs an Anthropic key, read from
    # ANTHROPIC_API_KEY or the CLI's saved credentials; has a hard spend guard):
    python3 ../benchmarks/internal-regressions/flash-review/run.py --provider haiku --runs 3

--dry-run builds every input and the real prompts but uses a stub adapter that
returns an empty review, so it makes no model call and costs nothing. It prints the
prompt sizes so the real run's cost can be estimated first.
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
CASES_DIR = HERE / "cases"
RESULTS_DIR = HERE / "results"  # git-ignored

EMPTY_REVIEW_YAML = """review:
  estimated_effort_to_review_[1-5]: 1
  relevant_tests: 'no'
  key_issues_to_review: []
  security_concerns: 'No'
"""


def _changed_files_from_diff(diff_text: str) -> list[str]:
    return [m.group(2) for m in re.finditer(r"^diff --git a/(.+?) b/(.+)$", diff_text, re.MULTILINE)]


def _diff_patches_from_diff(diff_text: str) -> tuple[tuple[str, str], ...]:
    # (file, hunk body) pairs with git's header lines stripped: the shape GitHub's
    # PR-files API returns in its "patch" field, which production's diff_patches uses.
    patches = []
    for section in re.split(r"(?=^diff --git )", diff_text, flags=re.MULTILINE):
        match = re.match(r"^diff --git a/(.+?) b/(.+)$", section, re.MULTILINE)
        if not match:
            continue
        lines = section.splitlines()
        start = next((i for i, line in enumerate(lines) if line.startswith("@@")), None)
        patches.append((match.group(2), "\n".join(lines[start:]) if start is not None else ""))
    return tuple(patches)


def _production_diff_text(diff_patches) -> str:
    # production's github_api.fetch_pr_diff shape: "--- {file} ---\n{patch}" per file
    return "\n\n".join(f"--- {file} ---\n{patch}" for file, patch in diff_patches)


def _file_contents_for(checkout: Path, changed_files: list[str], max_bytes: int = 100_000) -> dict[str, str]:
    contents = {}
    for path in changed_files:
        full = checkout / path
        if not full.is_file():
            continue
        text = full.read_text(encoding="utf-8", errors="ignore")
        if len(text.encode("utf-8")) <= max_bytes:
            contents[path] = text
    return contents


def _checkout_at(repo: Path, commit: str, dest: Path) -> None:
    # --shared reads the source repo's objects in place (alternates) instead of hardlinking
    # them, so it also works when the source is mounted read-only (as in a container).
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(repo), str(dest)], check=True)
    subprocess.run(["git", "-C", str(dest), "checkout", "-q", "--detach", commit], check=True)


# Claude Haiku 4.5 list price, USD per million tokens. Used only for the spend guard.
HAIKU_MODEL = "claude-haiku-4-5-20251001"
HAIKU_USD_PER_M_INPUT = 1.00
HAIKU_USD_PER_M_OUTPUT = 5.00
PRODUCTION_TEMPERATURE = 0.2  # what flash_review_generation_adapter sets for GLM


def _with_temperature(create_fn, temperature: float):
    """Wrap a messages.create so every call carries the given temperature. The shared
    AnthropicAdapter sets none (API default 1.0); production's reviewer runs at 0.2."""
    def create(**kwargs):
        kwargs.setdefault("temperature", temperature)
        return create_fn(**kwargs)
    return create


def _anthropic_adapter(model: str, on_usage):
    import aletheore.adapters.anthropic_native as native

    real_client_cls = native.Anthropic

    class _TemperatureClient(real_client_cls):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.messages.create = _with_temperature(self.messages.create, PRODUCTION_TEMPERATURE)

    native.Anthropic = _TemperatureClient
    return native.AnthropicAdapter(model=model, on_usage=on_usage)


def _haiku_cost(usage_records) -> float:
    return sum(
        u["prompt"] * HAIKU_USD_PER_M_INPUT / 1e6 + u["completion"] * HAIKU_USD_PER_M_OUTPUT / 1e6
        for u in usage_records
    )


class StubAdapter:
    """Records the real prompts review_diff builds and answers with an empty review."""

    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def simple_completion(self, system_prompt, user_prompt, cwd="."):
        self.calls.append((len(system_prompt), len(user_prompt)))
        return EMPTY_REVIEW_YAML


def _finding_text(finding: dict) -> str:
    return " ".join(str(finding.get(k, "")) for k in ("issue", "suggestion", "file", "rationale")).lower()


def _catches(case: dict, findings: list[dict]) -> tuple[bool, list[dict]]:
    expected = case.get("expected_files") or []
    hits = []
    for finding in findings:
        text = _finding_text(finding)
        if expected and finding.get("file") not in expected:
            continue
        if all(any(word.lower() in text for word in group) for group in case["must_mention"]):
            hits.append(finding)
    return bool(hits), hits


def run_case(case_dir: Path, repo: Path, adapter_factory, runs: int, dry: bool, spend=None, max_usd=None) -> dict:
    case = yaml.safe_load((case_dir / "case.yaml").read_text())
    raw_diff = (case_dir / "pr.diff").read_text()
    changed_files = _changed_files_from_diff(raw_diff)
    diff_patches = _diff_patches_from_diff(raw_diff)
    diff_text = _production_diff_text(diff_patches)

    workdir = Path(tempfile.mkdtemp(prefix="flash-regression-"))
    try:
        checkout = workdir / "head"
        print(f"[{case['id']}] checking out {case['head_commit'][:8]} ...", file=sys.stderr)
        _checkout_at(repo, case["head_commit"], checkout)
        file_contents = _file_contents_for(checkout, changed_files)

        from aletheore.evidence import scan_repository
        from scan_worker.flash_review import build_referenced_symbol_context, review_diff
        from scan_worker.model_tiers import FLASH_REVIEW_GENERATION_MODEL

        print(f"[{case['id']}] scanning the head checkout for evidence ...", file=sys.stderr)
        evidence = scan_repository(
            checkout, check_vulnerabilities=False, scan_git_history=False, check_licenses=False,
            map_endpoints=True, map_schema=True, progress=None,
        )

        def fetch_symbol_source(file_path: str, start_line: int, end_line: int):
            full = checkout / file_path
            if not full.is_file():
                return None
            return "\n".join(full.read_text(encoding="utf-8", errors="ignore").split("\n")[start_line - 1:end_line])

        referenced_symbol_context = build_referenced_symbol_context(
            evidence, changed_files, diff_text, fetch_symbol_source
        )

        results = []
        for run in range(1, runs + 1):
            if spend is not None and max_usd is not None and spend["usd"] >= max_usd:
                print(f"[{case['id']}] stopping: estimated spend ${spend['usd']:.3f} reached the ${max_usd:.2f} cap",
                      file=sys.stderr)
                break
            usage: list[dict] = []

            def on_usage(prompt_tokens: int, completion_tokens: int, cached_tokens: int = 0) -> None:
                usage.append({"prompt": prompt_tokens, "completion": completion_tokens, "cached": cached_tokens})

            adapter = adapter_factory(on_usage)
            started = time.time()
            findings = review_diff(
                diff_text,
                on_usage=on_usage,
                pr_title=case["title"],
                referenced_symbol_context=referenced_symbol_context,
                sibling_file_context="",
                cache_lookup=None,
                cache_write=None,
                model_used=FLASH_REVIEW_GENERATION_MODEL,
                file_contents=file_contents,
                diff_patches=diff_patches,
                adapter=adapter,
                per_file_completeness=True,
                rank_findings=True,
                cross_file_check_runs=0,
                share_pr_context_per_file=True,
            )
            caught, hits = _catches(case, findings)
            if spend is not None:
                spend["usd"] += _haiku_cost(usage)
            record = {
                "case": case["id"], "run": run, "seconds": round(time.time() - started, 1),
                "caught": caught, "matching_findings": hits, "all_findings": findings,
                "usage": usage,
            }
            if dry:
                record["stub_prompt_chars"] = adapter.calls
            results.append(record)
            label = "caught" if caught else "MISSED"
            print(f"[{case['id']}] run {run}/{runs}: {label}  ({len(findings)} finding(s))", file=sys.stderr)
        return {"case": case, "results": results}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--case", action="append", help="case id (default: all)")
    parser.add_argument("--runs", type=int, default=1, help="independent runs per case (the model is not deterministic)")
    parser.add_argument("--dry-run", action="store_true", help="stub adapter: no model call, print prompt sizes")
    parser.add_argument("--provider", choices=["production", "haiku"], default="production",
                        help="production = GLM-5.3-Flash via IndieRouter (what Flash Review uses); haiku = Claude Haiku")
    parser.add_argument("--anthropic-model", default=HAIKU_MODEL, help="model id for --provider haiku")
    parser.add_argument("--max-usd", type=float, default=1.00,
                        help="spend cap for --provider haiku, from list-price token math; checked before each run")
    parser.add_argument("--repo", default=None, help="git repo to check out the head commits from (default: this repo)")
    parser.add_argument("--src-root", default=None, help="directory containing src/ and github-app/ (default: this repo)")
    args = parser.parse_args()

    repo_root = Path(args.src_root).resolve() if args.src_root else HERE.parents[2]
    sys.path.insert(0, str(repo_root / "src"))
    sys.path.insert(0, str(repo_root / "github-app"))
    repo = Path(args.repo).resolve() if args.repo else repo_root

    cases = sorted(p for p in CASES_DIR.iterdir() if p.is_dir())
    if args.case:
        cases = [p for p in cases if p.name in args.case]

    if args.dry_run:
        def adapter_factory(on_usage):
            return StubAdapter()
    elif args.provider == "haiku":
        def adapter_factory(on_usage):
            return _anthropic_adapter(args.anthropic_model, on_usage)
    else:
        from scan_worker.model_tiers import flash_review_generation_adapter

        def adapter_factory(on_usage):
            return flash_review_generation_adapter(on_usage=on_usage)

    spend = {"usd": 0.0} if args.provider == "haiku" and not args.dry_run else None
    max_usd = args.max_usd if spend is not None else None
    outcomes = [run_case(p, repo, adapter_factory, args.runs, args.dry_run, spend, max_usd) for p in cases]

    print("\n=== summary ===")
    for outcome in outcomes:
        results = outcome["results"]
        caught = sum(r["caught"] for r in results)
        print(f"{outcome['case']['id']}: caught {caught}/{len(results)} run(s)")
        if args.dry_run:
            calls = results[0].get("stub_prompt_chars", [])
            total_chars = sum(a + b for a, b in calls)
            print(f"   dry run: {len(calls)} model call(s) per review, ~{total_chars // 4:,} prompt tokens (chars/4)")

    if spend is not None:
        print(f"\nestimated Anthropic spend (list-price token math): ${spend['usd']:.3f}")
    if not args.dry_run:
        out = RESULTS_DIR / time.strftime("%Y%m%d-%H%M%S")
        out.mkdir(parents=True, exist_ok=True)
        (out / "results.json").write_text(json.dumps(outcomes, indent=2, default=str))
        print(f"\nfull results: {out / 'results.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
