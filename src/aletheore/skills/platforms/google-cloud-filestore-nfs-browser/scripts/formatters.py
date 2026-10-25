"""Output formatting helpers for Filestore NFS File Browser CLI."""

import json
from typing import Any, Dict

WIDE_SEPARATOR_WIDTH = 60
NARROW_SEPARATOR_WIDTH = 40


def format_size(b: int) -> str:
  """Converts a byte count into a human-readable string with units."""
  f = float(b)
  for u in ["B", "KB", "MB", "GB", "TB"]:
    if f < 1024.0:
      return f"{f:.1f} {u}" if u != "B" else f"{int(f)} B"
    f /= 1024.0
  return f"{f:.1f} PB"


def format_tree_output(
    res: Dict[str, Any], requested_path: str, as_json: bool
) -> None:
  """Formats and displays directory tree results."""
  if as_json or "error" in res:
    print(json.dumps(res, indent=2))
    return
  print(f"\n📂 Filestore Directory Tree: {res.get('path', requested_path)}")
  print("-" * WIDE_SEPARATOR_WIDTH)
  for e in res.get("entries", []):
    if e.get("type") == "dir":
      print(f"📁 {e['name']}/")
    else:
      print(f"📄 {e['name']:<35} ({format_size(e.get('size_bytes', 0))})")
  print("-" * WIDE_SEPARATOR_WIDTH)
  print(f"Total entries listed: {len(res.get('entries', []))}\n")


def format_search_output(res: Dict[str, Any], as_json: bool) -> None:
  """Formats and displays search match results."""
  if as_json or "error" in res:
    print(json.dumps(res, indent=2))
    return
  matches = res.get("matches", [])
  print(f"\n🔍 Search Results ({len(matches)} matches):")
  print("-" * WIDE_SEPARATOR_WIDTH)
  for m in matches:
    if "line" in m:
      print(f"📄 {m['file']}:{m['line']} -> {m['content']}")
    else:
      print(f"📄 {m['file']}")
  print("-" * WIDE_SEPARATOR_WIDTH + "\n")


def format_read_output(res: Dict[str, Any], as_json: bool) -> None:
  """Formats and displays file content slices."""
  if as_json or "error" in res:
    print(json.dumps(res, indent=2))
    return
  if res.get("is_binary"):
    size_str = format_size(res.get("size_bytes", 0))
    print(f"\n⚠️  Binary File: {res['file']} ({size_str})")
    print("   Raw content suppressed to protect context window.\n")
  else:
    tot_info = f" of {res.get('total_lines')}" if res.get("total_lines") else ""
    print(
        f"\n📄 Content of {res['file']} (Lines"
        f" {res.get('start_line')}-{res.get('end_line')}{tot_info}):"
    )
    print("-" * WIDE_SEPARATOR_WIDTH)
    print(res.get("content", "").rstrip())
    print("-" * WIDE_SEPARATOR_WIDTH + "\n")


def format_stat_output(res: Dict[str, Any], as_json: bool) -> None:
  """Formats and displays POSIX metadata attributes."""
  if as_json or "error" in res:
    print(json.dumps(res, indent=2))
    return
  print(f"\n📊 Metadata for {res.get('path')}:")
  print("-" * NARROW_SEPARATOR_WIDTH)
  print(f"  Size:        {format_size(res.get('size_bytes', 0))}")
  print(f"  Permissions: {res.get('mode')}")
  print(f"  Type:        {'Directory' if res.get('is_dir') else 'File'}")
  print(f"  UID / GID:   {res.get('uid')} / {res.get('gid')}")
  print(f"  Modified:    {res.get('mtime')}")
  print("-" * NARROW_SEPARATOR_WIDTH + "\n")
