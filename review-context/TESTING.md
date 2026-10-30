# Independent testing handoff

This branch adds the standalone `review-context/` package. Production AIRview and Flash Review are unchanged. Generation requires Python 3.10+ and Git, with no model, credentials or network connection.

## Tests

From the repository root:

```sh
cd review-context
python3 -m unittest discover -s tests -v
```

Expected: **22 passing tests**. They use disposable Git repositories and cover source pinning, missing coverage, removed-import consumers, callbacks/result consumers, scope resolution, claim provenance, budget omissions, workflow validation, routing and deterministic serialization.

## Generate documents

From `review-context/`, choose an empty output directory:

```sh
python3 -m review_context build --repo .. --revision HEAD \
  --include src --include github-app \
  --file src/aletheore/secrets.py --file src/aletheore/file_cache.py \
  --output /tmp/review-context-independent-docs
```

Inspect the generated README, file pages, structured contracts, relationships and workflows. All six Change safety sections must appear, including explicit unknown coverage. Check sampled source revisions, blobs, line intervals and hashes. Workflows are bounded to five; their edges must be supported by the graph.

## Generate PR evidence

```sh
python3 -m review_context pr --repo .. --base BASE_SHA --head HEAD_SHA \
  --include src --include github-app --output /tmp/review-context-independent-pr
```

`packets.json` maps changed files to bounded `text` for the review prompt and structured evidence for auditing. Check complete excerpts, explicit budget omissions, and relevance to changed symbols.

Known integration cases (fetch these commits if absent):

- Removed import: base `b9bb9565ba623310d1505f5bbb84172a831a5495`, head `8bf8aea2f8b339a7e034c1cc7d23b23c5d54d5dc`. Look for unchanged tests patching the removed module attribute.
- Empty scan results: base `b3fc4796c90f041f40156ea8f4426da88efeddb5`, head `60e6355b0b40899d0d0746fed4f46de69456d6bf`. Look for callback callers and the `cached_per_file` storage implementation.

These cases informed the design. Please test unrelated repositories and cases before drawing general quality conclusions. Python is the only current adapter; static dispatch, receiver types, aliases/rebinding and some module-level changes remain incomplete. Provenance validation is not semantic proof. This branch does not enable production integration or claim a held-out precision/recall gain.
