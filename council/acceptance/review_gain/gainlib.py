"""Small helpers for the review-gain runner: file groups, caps, resuming."""
import json
import math
from pathlib import Path


def groups(files, lines, k):
    """k groups of about equal line count (greedy, biggest file first). `lines` maps file -> line count."""
    g = [[] for _ in range(k)]
    sizes = [0] * k
    for f in sorted(files, key=lambda f: -lines[f]):
        i = sizes.index(min(sizes))
        g[i].append(f)
        sizes[i] += lines[f]
    return g


def cap_for(n_files, mode, fixed=25, rate=1.5):
    """The most findings a condition may report for `n_files` files: `rate` (1.5) per file ("per-file", so the executors' caps add up to the single agent's), or a fixed number."""
    return math.ceil(rate * n_files) if mode == "per-file" else fixed


def already_done(out_dir, condition):
    """A condition recorded as run, judged, and ok is not run again on resume. One whose judge failed or whose run failed is."""
    f = Path(out_dir) / "results.jsonl"
    if not f.exists():
        return False
    for line in f.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        if r.get("condition") == condition and r.get("ok") and not r.get("judge_failed"):
            return True
    return False


# ---- v3 safeguards ------------------------------------------------------------------------------------------------------------------------------------
import re

_ORACLE = [
    (re.compile(r"(^|[\s;|&])find\s+/(?!tmp\b)"), "searches the whole disk"),
    (re.compile(r"git\s+-C\s+\S+"), "looks at another repository"),
    (re.compile(r"manifest"), "reaches for the answer key"),
    (re.compile(r"site-packages|dist-packages"), "looks for an installed copy"),
    (re.compile(r"pip(3)?\s+(download|install|show|list)|\buv\s+pip|npm\s+(view|pack|install)"), "fetches or lists packages"),
    (re.compile(r"\b(curl|wget)\b|https?://"), "uses the network"),
    (re.compile(r"\.codex|\.claude|memories|MEMORY\.md"), "reads the agent's own memory or settings"),
    (re.compile(r"council-gain-data|~/dev\b|/Users/[^/\s]+/dev\b"), "reads the experiment's data folder"),
]


_OWN_TOOL_RESULT = re.compile(r"^Read \S*/\.claude/projects/[^/\s]+/[0-9a-f-]{36}/tool-results/[\w.-]+$")


_NOTES = [(re.compile(r"git\s+remote"), "asks where the clone came from (the clones have no remote, so nothing is revealed)")]


def audit_notes(commands):
    """Habits worth logging that cannot reveal anything in this setup, so they do not make a run contaminated."""
    return [(c, why) for c in commands for rx, why in _NOTES if rx.search(c)]


_CD = re.compile(r"\bcd\s+([^\s;&|]+)")
_TOKENS = re.compile(r"[^\s;&|<>()]+")
_RUN_TREE = re.compile(r"^/(private/)?var/folders/[^/]+/[^/]+/T/gain-(claude|codex|split|council)[\w.-]*(/|$)")   # a run's own throwaway worktree
_SAFE_ABS = ("/dev/null", "/dev/stdin", "/dev/stdout", "/dev/stderr", "/usr/bin/", "/bin/", "/usr/local/bin/", "/opt/homebrew/bin/")


def _leaves_corpus(command, root):
    """Does any path in the command leave the corpus? Relative paths are resolved from the corpus root (or from the folder a `cd` moved to); absolute ones must be under `root`
    when it is known. Paths to system programs and /dev/null are ignored."""
    import posixpath
    cwd = "."
    m = _CD.search(command)
    if m and not m.group(1).startswith(("/", "~")):
        cwd = posixpath.normpath(m.group(1))
    for tok in _TOKENS.findall(command):
        tok = tok.strip("\"'`,")
        if tok.startswith(_SAFE_ABS):
            continue
        if tok.startswith("~/") or tok == "~":
            return True
        if tok.startswith("/"):
            if _RUN_TREE.match(tok):
                continue
            if root is None or not (tok.rstrip("/") == root.rstrip("/") or tok.startswith(root.rstrip("/") + "/")):
                return True
            continue
        if ".." in tok.split("/"):
            full = posixpath.normpath(posixpath.join(cwd, tok))
            if full == ".." or full.startswith("../"):
                return True
    return False


def audit_commands(commands, root=None):
    """Commands (shell lines, or 'Read path' / 'Grep pattern path') that look like a search for an oracle: another copy of the code, the answer key, the clone's origin,
    the network, or anything outside the corpus. `root` is the corpus folder when known (then absolute paths must stay under it). Returns [(command, reason)]."""
    out = []
    for c in commands:
        if _OWN_TOOL_RESULT.search(c):
            continue   # Claude Code saves a large tool output to a file in its own session folder and reads it back: that is not a search for anything
        why = next((w for rx, w in _ORACLE if rx.search(c)), None)
        if why is None and _leaves_corpus(c, root):
            why = "reads outside the corpus"
        if why:
            out.append((c, why))
    return out


def commands_of(events):
    """What an agent ran or read, from its raw stream events: Codex command_execution items, Claude tool_use blocks."""
    out = []
    for e in events or []:
        it = e.get("item") or {}
        if e.get("type") == "item.completed" and it.get("type") == "command_execution" and it.get("command"):
            out.append(str(it["command"]))
        elif e.get("type") == "assistant":
            for part in (e.get("message") or {}).get("content") or []:
                if isinstance(part, dict) and part.get("type") == "tool_use":
                    inp = part.get("input") or {}
                    arg = " ".join(str(v) for k, v in inp.items() if k in ("command", "file_path", "pattern", "path", "glob") and v)
                    out.append(f"{part.get('name')} {arg}".strip())
    return out


class Recording:
    """Wraps a runner and keeps what each run did, for the audit: log gets (agent id, commands) after every run."""

    def __init__(self, inner, log, agent, env=None):
        self.inner, self.log, self.agent, self.env = inner, log, agent, env   # env: called before every run, returns extra environment for that run only
        self.name = getattr(inner, "name", agent)

    def run(self, *args, **kwargs):
        if self.env is not None and len(args) >= 4:
            args = (*args[:3], {**args[3], **self.env()}, *args[4:])
        r = self.inner.run(*args, **kwargs)
        self.log.append((self.agent, commands_of(getattr(r, "raw_events", []))))
        return r


def new_key():
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def save_manifest(folder, data, key):
    """The answer key is written encrypted (manifest.enc). The key is not stored beside it."""
    from cryptography.fernet import Fernet
    Path(folder).mkdir(parents=True, exist_ok=True)
    (Path(folder) / "manifest.enc").write_bytes(Fernet(key.encode()).encrypt(json.dumps(data).encode()))


def load_manifest(folder, key):
    """The manifest, decrypted with `key` if there is an encrypted one, else the plain manifest.json (the first, networkx runs)."""
    enc = Path(folder) / "manifest.enc"
    if enc.exists():
        from cryptography.fernet import Fernet
        return json.loads(Fernet(key.encode()).decrypt(enc.read_bytes()))
    return json.loads((Path(folder) / "manifest.json").read_text())


def fresh_codex_home():
    """A new, empty Codex config folder holding only a link to the sign-in file. Codex keeps notes from its own sessions in ~/.codex/memories; with this, a run
    cannot read what an earlier run (or an earlier replicate) left there."""
    import os
    import tempfile
    d = Path(tempfile.mkdtemp(prefix="codex-home-"))
    auth = Path(os.path.expanduser("~")) / ".codex" / "auth.json"
    if auth.exists():
        (d / "auth.json").symlink_to(auth)
    return d
