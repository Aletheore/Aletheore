# Context Window Safety, Chunking & LLM Guardrails

When inspecting remote filesystems, large files (e.g. 50 GB log files or
500k-file directory structures) can easily blow up an LLM agent's context window
or cause timeout errors. This document outlines the safeguards enforced by
`google-cloud-filestore-nfs-browser`.

--------------------------------------------------------------------------------

## 1. Directory Tree Pagination & Depth Limits

*   **Default Depth Limit:** `depth=2` by default. Max allowed depth is 5.
*   **Max Entries Safety Cap:** 100 entries per listing call.
*   **Truncation Warning:** When a directory contains >100 entries, the tool
    flags `truncated=true` and prints a clear notice:

    ```text
    ⚠️ [TRUNCATED]: Max entry limit reached. Specify subpaths for more.
    ```

--------------------------------------------------------------------------------

## 2. Chunked File Reading Protocols

Agents should never attempt to read an entire unverified file.

### Reading Best Practices:

1.  **Log Inspection:** Always read the tail (`--tail=50` or `--tail=100`) to
    check recent errors.
2.  **Config Inspection:** Read line slices (`--lines=1:100`).
3.  **Large Dumps / Binary Assets:** Inspect metadata first (`stat`) before
    reading.

--------------------------------------------------------------------------------

## 3. Binary File Protection

The bridge service and client script inspect the initial 1024 bytes of any file
for null bytes (`\x00`). If a binary file is detected:

*   Raw content dumping is strictly blocked.
*   The tool returns metadata instead of raw bytes:

    ```json
    {
      "file": "/backups/db.tar.gz",
      "is_binary": true,
      "size_bytes": 1073741824,
      "message": "Binary file detected. Raw content suppressed to protect LLM context."
    }
    ```
