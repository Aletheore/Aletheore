import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from aletheore.static_analysis._exclusions import excluded_dir_names, filter_findings, has_real_file

# Real cost profile, measured live tonight, is why this is opt-in like
# Bearer (see static_analysis/__init__.py): a CPG build is JVM startup
# plus real parsing work, not a fast stateless subprocess call - building
# a CPG for a single mid-sized real Go package took several real seconds,
# before the query itself even runs. Currently implements exactly one
# custom CFG-based query (asymmetric-cache-trust-go), the Joern sibling of
# semantic_checks.py's _asymmetric_cache_trust_findings_go - see
# docs/audits/deterministic_scanner_evaluation.md for why this exists and
# joern_queries/asymmetric_cache_trust_go.sc for the real, live-validated
# query itself (fires exactly on grafana/grafana#103633's Service.Check,
# zero false positives across three other real Go repos in the same
# corpus).
DEFAULT_JOERN_TIMEOUT_SECONDS = 300
_QUERY_SCRIPT = Path(__file__).parent / "joern_queries" / "asymmetric_cache_trust_go.sc"


def check_joern(repo_path: Path, timeout: int = DEFAULT_JOERN_TIMEOUT_SECONDS) -> dict:
    if not has_real_file(repo_path, "*.go"):
        return {"checked": True, "reason": None, "findings": []}

    gosrc2cpg = shutil.which("gosrc2cpg")
    joern = shutil.which("joern")
    if gosrc2cpg is None or joern is None:
        return {"checked": False, "reason": "joern not installed", "findings": []}

    # Real requirement confirmed live: gosrc2cpg needs a go.mod at (or
    # above) the parse target to resolve module context - pointing it at a
    # bare subdirectory with no go.mod of its own fails immediately.
    if not (repo_path / "go.mod").exists():
        return {
            "checked": False,
            "reason": "joern requires a go.mod at the scanned root (Go module context)",
            "findings": [],
        }

    exclude_args = []
    for name in excluded_dir_names(repo_path):
        exclude_args.extend(["--exclude", name])

    # Real bug found live building this: running `joern`/`gosrc2cpg` with
    # cwd left at its default writes a `workspace/<project>/` directory
    # wherever the calling process happened to be running from - including,
    # confirmed directly, this repo's own root. Both steps below run with
    # an explicit throwaway tmp_path as cwd so nothing ever lands in
    # repo_path or the caller's own working directory.
    with tempfile.TemporaryDirectory(prefix="aletheore-joern-") as tmp:
        tmp_path = Path(tmp)
        cpg_path = tmp_path / "cpg.bin"
        output_path = tmp_path / "findings.json"

        try:
            build_result = subprocess.run(
                [gosrc2cpg, str(repo_path), "-o", str(cpg_path), *exclude_args],
                capture_output=True, text=True, timeout=timeout, cwd=tmp_path,
            )
        except subprocess.TimeoutExpired:
            return {"checked": False, "reason": f"joern CPG build timed out after {timeout}s", "findings": []}
        except OSError as exc:
            return {"checked": False, "reason": f"joern CPG build failed to run: {exc}", "findings": []}

        if build_result.returncode != 0 or not cpg_path.exists():
            return {
                "checked": False,
                "reason": f"joern CPG build failed: {(build_result.stderr or build_result.stdout)[-500:]}",
                "findings": [],
            }

        try:
            query_result = subprocess.run(
                [
                    joern, "--script", str(_QUERY_SCRIPT),
                    "--param", f"cpgPath={cpg_path}",
                    "--param", f"outputPath={output_path}",
                ],
                capture_output=True, text=True, timeout=timeout, cwd=tmp_path,
            )
        except subprocess.TimeoutExpired:
            return {"checked": False, "reason": f"joern query timed out after {timeout}s", "findings": []}
        except OSError as exc:
            return {"checked": False, "reason": f"joern query failed to run: {exc}", "findings": []}

        if not output_path.exists():
            return {
                "checked": False,
                "reason": f"joern query produced no output: {(query_result.stderr or query_result.stdout)[-500:]}",
                "findings": [],
            }

        try:
            findings = json.loads(output_path.read_text())
        except json.JSONDecodeError:
            return {"checked": False, "reason": "joern query produced unparseable output", "findings": []}

    # Real bug found via audit (2026-09-21): every other scanner in this
    # package (bandit, bearer, gosec, semgrep, sonarqube) ends its return
    # with filter_findings(findings, repo_path) - the "authoritative
    # correctness backstop, independent of whether a given tool's own
    # native exclude flag actually honored excluded_dir_names" (see that
    # function's own docstring). check_joern above DOES pass exclude_args
    # to gosrc2cpg at CPG-build time (a native exclusion attempt, same as
    # the other tools' own flags), but never applied this same backstop -
    # a finding under .worktrees/, .repowise/, or a user's ignored_paths
    # could reach the caller unfiltered if that native exclusion didn't
    # hold, exactly the gap filter_findings exists to close for every
    # other scanner here.
    return {"checked": True, "reason": None, "findings": filter_findings(findings, repo_path)}
