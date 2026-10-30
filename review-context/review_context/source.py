from __future__ import annotations
import hashlib
import subprocess
from pathlib import Path

class GitSource:
    def __init__(self, repo: str | Path, revision: str):
        self.repo = Path(repo).resolve()
        self.revision = self.git('rev-parse', '--verify', revision + '^{commit}').strip()
        self._texts: dict[str, str] = {}
        self.blobs: dict[str, str] = {}
        for line in self.git('ls-tree', '-r', self.revision).splitlines():
            meta, path = line.split('\t', 1)
            mode, kind, blob = meta.split()
            # Never follow repository symlinks or submodules.
            if kind == 'blob' and mode in ('100644', '100755'):
                self.blobs[path] = blob

    def git(self, *args: str) -> str:
        return subprocess.check_output(['git', '-C', str(self.repo), *args], text=True, errors='replace')

    def read(self, path: str) -> str:
        if path not in self.blobs:
            raise ValueError(f'Not a regular file at {self.revision}: {path}')
        if path not in self._texts:
            self._texts[path] = self.git('show', self.revision + ':' + path)
        return self._texts[path]

    def excerpt(self, path: str, start: int, end: int) -> dict:
        text = '\n'.join(self.read(path).splitlines()[start-1:end])
        return {'path': path, 'start_line': start, 'end_line': end,
                'revision': self.revision, 'blob': self.blobs[path], 'source': text,
                'sha256': hashlib.sha256(text.encode()).hexdigest()}
