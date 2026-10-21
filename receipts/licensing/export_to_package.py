"""Export the staged, ready skills into the Aletheore package layout.

Usage: python3 export_to_package.py <worktree-or-repo-root> [--dry]

Reads docs/skill-hub-curation/skills/<shelf>/<category>/<name>/ (status "ready" only) and writes
<root>/src/aletheore/skills/<category>/<name>/ in the shape skill_hub.py loads:
  - frontmatter gains the keys the loader requires: skill_id, title, source_url, license
    (plus shelf and the existing `aletheore:` provenance line)
  - `license` is the per-skill SPDX id resolved during curation (the allowlisted value),
    replacing any free-text license string in the upstream frontmatter
  - the body and every companion file are copied unchanged (OMITTED_COMPANIONS.txt and dotfiles are not: wheels skip dotfiles, so listing one would point at a file that is not there)
Also writes src/aletheore/skills/NOTICE.md (attribution table, generated).
Existing hand-written sample skills in the package are left alone; a collision aborts.
"""
import json, os, re, shutil, sys
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "skills")
FM = re.compile(r"\A---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?(.*)\Z", re.S)
PROV = re.compile(r"(?m)^aletheore: (\{.*\})\s*$")


LICENSE_FILE_PREFIXES = ("LICENSE", "LICENCE", "COPYING", "NOTICE")
_HOLDER = re.compile(r"(?im)^\s*(?:copyright\b.*)$")
_NOT_A_HOLDER = re.compile(r"copyright (?:notice|license|owner|holder|statement|\(c\) \[|\[yyyy\])|\bcopyright\s+owner\b|\byyyy\b", re.I)


def license_files(skill_dir: str) -> list[str]:
    return sorted(f for f in os.listdir(skill_dir) if f.upper().startswith(LICENSE_FILE_PREFIXES) and os.path.isfile(os.path.join(skill_dir, f)))


def holders_of(skill_dir: str) -> str:
    """Copyright lines named in the licence files shipped in a skill's folder (not the Apache boilerplate)."""
    found: list[str] = []
    for name in license_files(skill_dir):
        text = open(os.path.join(skill_dir, name), encoding="utf-8", errors="replace").read()
        for line in _HOLDER.findall(text):
            line = " ".join(line.split())
            if _NOT_A_HOLDER.search(line) or len(line) > 140 or line in found:
                continue
            found.append(line)
    return "; ".join(found[:2]) if found else ("not named by upstream" if license_files(skill_dir) else "no licence file")


def fill_upstream_license(skill_dir: str, repo_ref: str, repo_licenses: dict) -> bool:
    """Ship the upstream repository's licence text (at the pinned commit) when the skill's own folder has none."""
    if license_files(skill_dir):
        return False
    entry = repo_licenses.get(repo_ref)
    if not entry or not entry.get("text"):
        return False
    with open(os.path.join(skill_dir, "LICENSE.upstream"), "w", encoding="utf-8", newline="\n") as f:
        f.write(entry["text"] if entry["text"].endswith("\n") else entry["text"] + "\n")
    return True


def load_repo_licenses() -> dict:
    path = os.path.join(ROOT, "licenses", "repo_licenses.json")
    return json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else {}


def load_license_notes() -> dict:
    path = os.path.join(ROOT, "licenses", "license_notes.json")
    return json.load(open(path, encoding="utf-8")) if os.path.isfile(path) else {}


def source_repo_ref(origin: str):
    m = re.match(r"https://github\.com/([^/]+/[^/]+)/(?:tree|blob)/([^/]+)/", origin or "")
    return (m.group(1), m.group(2)) if m else (None, None)


def _license_kind(text: str) -> str:
    head = text[:3000]
    if "Apache License" in head and "Version 2.0" in head:
        return "Apache-2.0"
    if re.search(r"\bMIT License\b|Permission is hereby granted, free of charge", head):
        return "MIT"
    return "other"


def ensure_license_texts(out: str) -> int:
    """For every skill in the package: if its folder has no licence file, ship the upstream repository's licence
    (at the pinned commit) as LICENSE.upstream; if the folder's own licence text is a different licence from the
    declared one and the repository's matches the declared one, ship the repository's too, so both are present."""
    repo_licenses = load_repo_licenses()
    written = 0
    for c in sorted(os.listdir(out)):
        cdir = os.path.join(out, c)
        if not os.path.isdir(cdir) or c.startswith("_"):
            continue
        for sname in sorted(os.listdir(cdir)):
            sdir = os.path.join(cdir, sname)
            md = os.path.join(sdir, "SKILL.md")
            if not os.path.isfile(md):
                continue
            fm = yaml.safe_load(FM.match(open(md, encoding="utf-8").read()).group(1)) or {}
            repo, ref = source_repo_ref(str(fm.get("source_url") or ""))
            entry = repo_licenses.get(f"{repo}@{ref}") if repo else None
            if not entry:
                continue
            if fill_upstream_license(sdir, f"{repo}@{ref}", repo_licenses):
                written += 1
                continue
            declared = str(fm.get("license") or "")
            if "LICENSE.upstream" not in license_files(sdir) and entry.get("kind") == declared:
                kinds = {_license_kind(open(os.path.join(sdir, f), encoding="utf-8", errors="replace").read()) for f in license_files(sdir)}
                if kinds and declared not in kinds and "other" not in kinds:
                    with open(os.path.join(sdir, "LICENSE.upstream"), "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(entry["text"] if entry["text"].endswith("\n") else entry["text"] + "\n")
                    written += 1
    return written


def write_notice(out: str) -> int:
    """NOTICE.md from what is actually in the package (hand-written samples included), with copyright holders."""
    notes = load_license_notes()
    rows = []
    for c in sorted(os.listdir(out)):
        cdir = os.path.join(out, c)
        if not os.path.isdir(cdir):
            continue
        for sname in sorted(os.listdir(cdir)):
            sdir = os.path.join(cdir, sname)
            md = os.path.join(sdir, "SKILL.md")
            if not os.path.isfile(md):
                continue
            fm = yaml.safe_load(FM.match(open(md, encoding="utf-8").read()).group(1)) or {}
            origin = str(fm.get("source_url") or "")
            m = re.search(r"github\.com/([^/]+/[^/]+)", origin)
            rows.append((f"{c}/{sname}", str(fm.get("license") or ""), holders_of(sdir), m.group(1) if m else origin, origin))
    lines = [
        "# Third-party skills in this directory",
        "",
        "Each skill below was written by its original author and is redistributed under that author's license",
        "(MIT or Apache-2.0). Every skill's folder carries the license text that applies to it (the license file the",
        "upstream skill ships, or `LICENSE.upstream`: the upstream repository's license at the pinned commit).",
        "The copyright holder column quotes what those files name; where upstream names none it says so.",
        "Skills were adapted only so they read correctly in any coding agent; `source_url` in each `SKILL.md` links to the",
        "exact upstream commit (two skills link to a branch because no commit matching the vendored copy could be found).",
        "",
        "This file is generated by the curation tooling. Do not edit it by hand.",
        "",
        "| Skill | License | Copyright holder (as named upstream) | Upstream repository | Source |",
        "|---|---|---|---|---|",
    ]
    for sid, lic, holder, repo, origin in rows:
        lines.append(f"| `{sid}` | {lic} | {holder} | {repo} | {origin} |")
    present = {r[0] for r in rows}
    special = [(k, v) for k, v in sorted(notes.items()) if k in present]
    if special:
        lines += ["", "## License notes", ""]
        lines += [f"- `{k}`: {v}" for k, v in special]
    with open(os.path.join(out, "NOTICE.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    return len(rows)



def humanize(name: str) -> str:
    return re.sub(r"\s+", " ", name.replace("-", " ").replace("_", " ")).strip().title()


def convert(text: str, a: dict) -> str:
    m = FM.match(text)
    if not m:
        raise ValueError("no frontmatter")
    head, body = m.group(1), m.group(2)
    pm = PROV.search(head)
    prov_line = pm.group(0) if pm else "aletheore: " + json.dumps(a, ensure_ascii=False)
    head_wo = PROV.sub("", head)
    fm = yaml.safe_load(head_wo) or {}
    if not isinstance(fm, dict):
        raise ValueError("frontmatter is not a mapping")
    name = str(fm.get("name") or a["id"].split("/", 1)[1])
    fm["skill_id"] = a["id"]
    fm["title"] = humanize(name)
    fm["description"] = str(fm.get("description") or "").strip()
    fm["source_url"] = a["origin"]
    fm["license"] = a["license"]
    fm["shelf"] = a["shelf"]
    dumped = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, width=10_000, default_flow_style=False).rstrip("\n")
    return f"---\n{dumped}\n{prov_line}\n---\n{body}"


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    root = os.path.abspath(sys.argv[1])
    dry = "--dry" in sys.argv
    out = os.path.join(root, "src", "aletheore", "skills")
    def _is_hand_written(skill_md: str) -> bool:
        # Exported skills carry a `shelf:` key in their frontmatter; the hand-written samples do not.
        with open(skill_md, encoding="utf-8") as fh:
            return "\nshelf:" not in fh.read(4000)

    existing = {c + "/" + s for c in os.listdir(out) if os.path.isdir(os.path.join(out, c)) and not c.startswith("_")
                for s in os.listdir(os.path.join(out, c))
                if os.path.isfile(os.path.join(out, c, s, "SKILL.md")) and _is_hand_written(os.path.join(out, c, s, "SKILL.md"))
                } if os.path.isdir(out) else set()
    plan, seen, problems = [], {}, []
    for r, ds, fs in os.walk(SRC):
        parts = os.path.relpath(r, SRC).split(os.sep)
        if "SKILL.md" in fs and len(parts) == 3 and parts[0] != "_hold":
            text = open(os.path.join(r, "SKILL.md"), encoding="utf-8").read()
            a = json.loads(PROV.search(text).group(1))
            if a.get("status") != "ready":
                continue
            sid = a["id"]
            if sid in seen:
                problems.append(f"duplicate id {sid}: {seen[sid]} and {r}")
                continue
            if sid in existing:
                problems.append(f"collides with an existing packaged skill: {sid}")
                continue
            seen[sid] = r
            plan.append((r, a, text))
    print(f"ready skills to export: {len(plan)}; problems: {len(problems)}")
    for p in problems:
        print("  PROBLEM", p)
    if problems:
        sys.exit(1)
    if dry:
        return
    # Only skills that pass the package's own vetting ship. Run it from the target tree so the
    # exact gate that CI enforces (skill_vetting.vet_skill_directory) is the one applied here.
    sys.path.insert(0, os.path.join(root, "src"))
    from pathlib import Path
    from aletheore.skill_vetting import vet_skill_directory

    REPO_LICENSES = load_repo_licenses()
    notice, held = [], []
    for r, a, text in sorted(plan, key=lambda x: x[1]["id"]):
        dest = os.path.join(out, *a["id"].split("/"))
        if os.path.exists(dest):
            shutil.rmtree(dest)
        shutil.copytree(r, dest, ignore=shutil.ignore_patterns("OMITTED_COMPANIONS.txt", ".*"))
        with open(os.path.join(dest, "SKILL.md"), "w", encoding="utf-8", newline="\n") as f:
            f.write(convert(text, a))
        repo, ref = source_repo_ref(a["origin"])
        fill_upstream_license(dest, f"{repo}@{ref}", REPO_LICENSES)
        failures = vet_skill_directory(Path(dest))
        if failures:
            shutil.rmtree(dest)
            held.append({"id": a["id"], "origin": a["origin"], "failures": [{"reason": x.reason, "detail": x.detail} for x in failures]})
            continue
        m = re.search(r"github\.com/([^/]+/[^/]+)", a["origin"])
        notice.append((a["id"], a["license"], m.group(1) if m else a["origin"], a["origin"]))
    with open(os.path.join(ROOT, "vetting_held.json"), "w") as f:
        json.dump(held, f, indent=1)
    print(f"held back by the package's vetting: {len(held)} -> {[h['id'] for h in held]}")
    print(f"licence texts added: {ensure_license_texts(out)}")
    rows = [None] * write_notice(out)
    from aletheore import skill_hub

    indexed = skill_hub.rebuild_bundled_index(Path(out))
    print(f"exported {len(notice)} skills; NOTICE.md lists {len(rows)} (includes the hand-written samples); _index.json lists {indexed}")


if __name__ == "__main__":
    main()
