"""Cloud Run Serverless NFS Bridge API for Google Cloud Filestore.

Provides safe, read-only HTTP endpoints to inspect directories, search patterns,
read file slices, and check POSIX metadata for mounted NFS shares.
"""

import collections
import fnmatch
import os
import re
import shutil
import stat as stat_mod
from typing import Any, Dict, Optional
from fastapi import FastAPI
from fastapi import HTTPException
from fastapi import Query
from fastapi import status as http_status

app = FastAPI(
    title="Filestore NFS Bridge Service",
    description="Serverless read-only HTTP bridge for Filestore NFS shares",
    version="1.0.0",
)

MOUNT_ROOT = os.environ.get("FILESTORE_MOUNT_POINT", "/mnt/share")

BINARY_CHECK_BYTES = 1024
DEFAULT_DEPTH = 2
MIN_DEPTH = 1
MAX_DEPTH = 5
DEFAULT_MAX_ENTRIES = 100
MIN_MAX_ENTRIES = 1
MAX_MAX_ENTRIES = 500
DEFAULT_MAX_RESULTS = 25
MIN_MAX_RESULTS = 1
MAX_MAX_RESULTS = 100
MAX_SEARCH_FILE_SIZE_BYTES = 50 * 1024 * 1024
MAX_CONTEXT_SNIPPET_LENGTH = 200
DEFAULT_HEAD_LINES = 100
MIN_HEAD_LINES = 1
MAX_HEAD_LINES = 500
MIN_TAIL_LINES = 1
MAX_TAIL_LINES = 500


def safe_path(rel_path: str) -> str:
  """Ensures path does not traverse outside the mount root, resolving symlinks."""
  mount_root_real = os.path.realpath(MOUNT_ROOT)
  resolved = os.path.realpath(
      os.path.join(mount_root_real, rel_path.lstrip("/"))
  )
  if os.path.commonpath([mount_root_real, resolved]) != mount_root_real:
    raise HTTPException(
        status_code=http_status.HTTP_403_FORBIDDEN,
        detail="Access denied: path traversal",
    )
  return resolved


def is_binary(filepath: str) -> bool:
  try:
    with open(filepath, "rb") as fh:
      return b"\x00" in fh.read(BINARY_CHECK_BYTES)
  except Exception:
    return False


@app.get("/api/v1/health")
def health() -> Dict[str, Any]:
  is_mounted = os.path.exists(MOUNT_ROOT)
  usage = shutil.disk_usage(MOUNT_ROOT) if is_mounted else None
  return {
      "status": "healthy",
      "mount_point": MOUNT_ROOT,
      "is_mounted": is_mounted,
      "total_bytes": usage.total if usage else 0,
      "free_bytes": usage.free if usage else 0,
  }


@app.get("/api/v1/tree")
def list_tree(
    path: str = Query("/", description="Relative path in share"),
    depth: int = Query(DEFAULT_DEPTH, ge=MIN_DEPTH, le=MAX_DEPTH),
    max_entries: int = Query(
        DEFAULT_MAX_ENTRIES, ge=MIN_MAX_ENTRIES, le=MAX_MAX_ENTRIES
    ),
) -> Dict[str, Any]:
  target = safe_path(path)
  if not os.path.exists(target):
    raise HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=f"Path not found: {path}",
    )
  entries = []
  for r, dirs, files in os.walk(target):
    d = 0 if r == target else os.path.relpath(r, target).count(os.sep) + 1
    if d >= depth:
      dirs.clear()
    else:
      for name in sorted(dirs):
        entries.append({
            "name": name,
            "type": "directory",
            "path": "/" + os.path.relpath(os.path.join(r, name), MOUNT_ROOT),
        })
        if len(entries) >= max_entries:
          break
    if len(entries) >= max_entries:
      break
    if d < depth:
      for name in sorted(files):
        st = os.stat(os.path.join(r, name))
        entries.append({
            "name": name,
            "type": "file",
            "size_bytes": st.st_size,
            "path": "/" + os.path.relpath(os.path.join(r, name), MOUNT_ROOT),
        })
        if len(entries) >= max_entries:
          break
    if len(entries) >= max_entries:
      break
  return {"path": path, "entries": entries, "total_entries": len(entries)}


@app.get("/api/v1/search")
def search_files(
    path: str = Query("/", description="Search base path"),
    pattern: Optional[str] = Query(None, description="Filename glob"),
    grep: Optional[str] = Query(None, description="Regex content pattern"),
    max_results: int = Query(
        DEFAULT_MAX_RESULTS, ge=MIN_MAX_RESULTS, le=MAX_MAX_RESULTS
    ),
) -> Dict[str, Any]:
  target = safe_path(path)
  if not os.path.exists(target):
    raise HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=f"Path not found: {path}",
    )
  if grep:
    try:
      rgx = re.compile(grep)
    except re.error as e:
      raise HTTPException(
          status_code=http_status.HTTP_400_BAD_REQUEST,
          detail=f"Invalid regex pattern: {e}",
      )
  else:
    rgx = None
  matches = []
  for r, _, files in os.walk(target):
    for f in sorted(files):
      if pattern and not fnmatch.fnmatch(f, pattern):
        continue
      fp = os.path.join(r, f)
      rel = "/" + os.path.relpath(fp, MOUNT_ROOT)
      if rgx:
        try:
          if os.path.getsize(fp) > MAX_SEARCH_FILE_SIZE_BYTES:
            continue
          if is_binary(fp):
            continue
          with open(fp, "r", encoding="utf-8", errors="replace") as fh:
            for idx, line in enumerate(fh, 1):
              if rgx.search(line):
                matches.append({
                    "file": rel,
                    "line": idx,
                    "content": line.strip()[:MAX_CONTEXT_SNIPPET_LENGTH],
                })
                if len(matches) >= max_results:
                  break
        except Exception:
          pass
      else:
        matches.append({"file": rel})
      if len(matches) >= max_results:
        break
    if len(matches) >= max_results:
      break
  return {"matches": matches}


@app.get("/api/v1/read")
def read_file(
    path: str = Query(..., description="File path inside share"),
    head: Optional[int] = Query(None, ge=MIN_HEAD_LINES, le=MAX_HEAD_LINES),
    tail: Optional[int] = Query(None, ge=MIN_TAIL_LINES, le=MAX_TAIL_LINES),
    start_line: Optional[int] = Query(None, ge=1),
    end_line: Optional[int] = Query(None, ge=1),
) -> Dict[str, Any]:
  target = safe_path(path)
  if not os.path.exists(target):
    raise HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=f"File not found: {path}",
    )
  if is_binary(target):
    return {
        "file": path,
        "is_binary": True,
        "size_bytes": os.path.getsize(target),
        "message": "Binary file suppressed.",
    }
  s, e, tot, sel = 1, 0, 0, []
  with open(target, "r", encoding="utf-8", errors="replace") as fh:
    if tail:
      ring = collections.deque(enumerate(fh, 1), maxlen=tail)
      if ring:
        s = ring[0][0]
        e = ring[-1][0]
        tot = e
        sel = [line for _, line in ring]
      else:
        s, e, tot, sel = 1, 0, 0, []
    elif start_line and end_line:
      s = start_line
      for idx, line in enumerate(fh, 1):
        if idx >= start_line and idx <= end_line:
          sel.append(line)
        elif idx > end_line:
          break
      e = start_line + len(sel) - 1 if sel else start_line
      tot = max(e, end_line)
    else:
      limit = head if head else DEFAULT_HEAD_LINES
      for idx, line in enumerate(fh, 1):
        if idx <= limit:
          sel.append(line)
        else:
          break
      e = len(sel)
      tot = e

  return {
      "file": path,
      "start_line": s,
      "end_line": e,
      "total_lines": tot,
      "content": "".join(sel),
      "is_binary": False,
  }


@app.get("/api/v1/stat")
def stat_file(
    path: str = Query(..., description="File or folder path")
) -> Dict[str, Any]:
  target = safe_path(path)
  if not os.path.exists(target):
    raise HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=f"Path not found: {path}",
    )
  st = os.stat(target)
  return {
      "path": path,
      "size_bytes": st.st_size,
      "mode": oct(stat_mod.S_IMODE(st.st_mode)),
      "is_dir": stat_mod.S_ISDIR(st.st_mode),
      "uid": st.st_uid,
      "gid": st.st_gid,
      "mtime": int(st.st_mtime),
  }
