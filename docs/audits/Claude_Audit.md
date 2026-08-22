# Claude audit

File-by-file audit of `github-app/` and `src/aletheore/`, started 2026-08-15.
Findings are listed most severe first within each pass. Every claim cites
`file:line` and was verified by reading the code, not inferred.

**Status:** complete for the areas listed below. 35 findings (16 from the
first pass, 19 from the second).

**Second pass, 2026-08-22:** the areas the first pass explicitly left
uncovered were audited via 5 parallel agents (one per area below), each
instructed to read the full file(s), cite `file:line`, and verify every
claim against actual behavior rather than inference alone — most findings
were confirmed by parsing real code through the actual tree-sitter grammar
or by running the real function against constructed input, not just reading.
Every finding was then independently re-verified a second time (by reading
the cited source directly, and for the highest-severity ones, by
reproducing the failure) before being recorded here — the same discipline
the first pass used. Findings #17-35 below are from this pass.

**Covered in full:** the Paddle/Marketplace/push/installation/issue_comment
webhooks, `auth.py`, `managed_audit_api.py`, `embeddings_api.py`,
`demo_scan_api.py`, `ingest_limits.py`, `main.py` middleware, `affiliates.py`,
the Flash Review path (`flash_review.py`, `github_api.py`, `pull_request.py`),
every `installation_spend_lock` block in `jobs.py`, `credentials.py`,
`secrets.py`, `mcp_server.py`, `citation_verifier.py`, `vulnerabilities.py`,
`scanner/detect.py`, `scanner/graph.py` in full (pre-passes, main loop,
resolvers, all extractor bodies including per-language rules), `db.py` in
both services, `dashboard.py`, `frontend.py`'s route handlers, `cli.py`,
`endpoints.py`, and `search_index.py` in full.

**Swept mechanically:** all SQL in both services (no injection surface), all 54
CLI modules by AST (no bare excepts, no mutable defaults), every `innerHTML` sink
in `frontend.py`.

**Not covered:** nothing outstanding from the original scope (`github-app/`
and `src/aletheore/`). Not audited at all: `benchmarks/`, `website/`, test
files themselves, and anything under `docs/`.

| # | severity | area | finding |
|---|---|---|---|
| 17 | **critical** | scanner (all languages) | one invalid-UTF-8 byte anywhere in a repo aborts the entire scan with no partial results |
| 18 | high | search_index | RRF fusion drops the score field on any dual-retriever hit, crashing `answer_question` on its best-case match |
| 19 | high | endpoints | FastAPI router-prefix collection is scope-blind — a same-named local variable anywhere in the file corrupts the real router's prefix |
| 20 | high | endpoints | incremental endpoint caching doesn't invalidate when a *different* file's router-mount prefix changes |
| 21 | high | scanner/Rust | `pub use ...;` extracts the literal string `"pub"` as the import, losing the real re-export target |
| 22 | high | cli | `audit`'s `--map-schema`/`--no-map-schema` flag is parsed but never forwarded — always inert |
| 23 | medium-high | app_server/dashboard | AIRview's non-scanned-file fallback always raises `NameError`, caught silently — feature 100% broken |
| 24 | medium | app_server | health-check-target and API-token creation are check-then-act, unlike the seat-limit fix in the same file |
| 25 | medium | app_server | `generate_token` discards the real token id and re-derives it with a racy re-query |
| 26 | medium | cli | `_fetch_whoami`'s `response.json()` sits outside its own try/except, contradicting its documented graceful-degradation contract |
| 27 | medium | endpoints | Django `urlpatterns += [...]` is a different AST node type than `=` and is silently skipped entirely |
| 28 | medium | scanner/PHP | grouped `use Foo\Bar\{A, B};` produces zero imports |
| 29 | medium | scanner/Java | `import static Foo.*;` never resolves, even when the target exists locally |
| 30 | low-medium | app_server | advisory-lock namespace 3 is independently reused for two unrelated locks across the two `db.py` files |
| 31 | low-medium | scanner/PHP | `use Foo\Bar as Baz;` also emits a phantom import of the bare alias `Baz` |
| 32 | low-medium | scanner/TS | `import foo = require('./foo');` produces zero imports |
| 33 | low-medium | scanner/Rust | nested `use std::{fmt, io::{self, Write}}` drops everything below the first brace level |
| 34 | low | app_server/dashboard | ownership-check comment claims an ordering the code doesn't have — a repo-existence oracle for any signed-in user |
| 35 | low | scanner (Python/JS vs. 7 others) | Python and JS/TS don't filter self-import edges the way every other language branch does |

---

## First pass (2026-08-15), findings #1-16

| # | severity | area | finding |
|---|---|---|---|
| 1 | high | scan_worker | `installation_spend_lock` held across LLM work at **5 sites**, two of them on every push |
| 2 | medium | app_server | free→paid gate races across *different* webhook events |
| 3 | low | app_server | stale comment understates the signature replay window by 12x |
| 4 | low | app_server | `_is_safe_next_path` misses control chars (neutralised by Starlette quoting) |
| 5 | medium | app_server | `/v1/managed-audit` missing from the body-size map, and unthrottled |
| 6 | medium | app_server | embeddings rate limit partitioned by a caller-controlled `repo_id` |
| 7 | medium? | app_server | "administers" derived from an access-level GitHub endpoint (needs verification) |
| 8 | medium | app_server | affiliate commissions never reversed on refund or chargeback |
| 9 | low | cli | `clear_api_key` rewrites credentials without `_save_key`'s permission care |
| 10 | low | cli | extensionless citation pattern has no left boundary -> false-verified citations |
| 11 | low-med | app_server | hard crash mid-webhook permanently drops a plan change |
| 12 | low | app_server | audit ChatOps trigger is a bare substring match, no author filter |
| 13 | **medium** | scan_worker | a deleted `-- x ---` line breaks the diff parser and silently drops true positives |
| 14 | low | scanner | 19 full-tree traversals per scan, none pruning ignored dirs before descending |
| 15 | medium | scanner | Java/C# pre-passes hold every parsed tree in memory for the whole scan |
| 16 | low-med | scanner | no file-size guard before parsing, and no per-file symbol cap |

---

## 1. `installation_spend_lock` held across LLM work at five sites

**Severity: high.** Live in production. This is a pattern, not a one-off.

`installation_spend_lock` (`github-app/scan_worker/db.py:317`) is a Postgres
advisory lock with a 5-second `ADVISORY_LOCK_TIMEOUT`, intended only to make the
spend check-then-record cycle atomic. A caller that cannot acquire it within 5s
does not queue - it fails with `psycopg.errors.LockNotAvailable`.

I measured every `with installation_spend_lock(...)` block in `jobs.py` by
walking its indentation and listing the calls inside. Five hold it across work
that takes minutes:

| line | function | block | expensive work inside | fires |
|---|---|---|---|---|
| 3074 | `_maybe_update_live_wiki` | 59 lines | `generate_subsystems`, `_attach_wiki_file_pages`, `_store_wiki_generation` | **every push and PR** |
| 3501 | `_maybe_update_live_docs` | 40 lines | `_run_docs_build_for_modules` | **every push and PR** |
| 2936 | `run_live_wiki_full_build_job` | 57 lines | `generate_subsystems`, file pages | on upgrade, per repo |
| 3360 | `run_live_docs_full_build_job` | 42 lines | `_run_docs_build_for_modules` | on upgrade, per repo |
| 1841 | `_fix_suggestion_attachment` | 37 lines | `fetch_file_content` + `simple_completion` | up to `RUNTIME_EVENT_RATE_LIMIT`/hour |

The two already-correct sites for comparison: `run_flash_review_job:1406` (9
lines, check only) and `_run_flash_review:1526` (6 lines, record only).

### Why this matters more than it first looks

`_maybe_update_live_wiki` and `_maybe_update_live_docs` are called from
`run_pr_scan_job` (`jobs.py:885`, `895`) and `run_push_scan_job`
(`jobs.py:1052`, `1059`) - the highest-frequency LLM paths in the product.

That reframes the production incident the flash-review fix was chasing. Its
comment records the symptom as *"confirmed in production logs while opening 25
PRs on one installation in quick succession"*. Opening a PR enqueues both a scan
job and a Flash Review. **The scan job holds the lock across a full wiki+docs
update; Flash Review only needs it briefly.** Narrowing Flash Review's own hold
fixed the victim's side of the collision - the party actually holding the lock
for minutes was never touched. Under the same burst, the long holder still
starves everything else for that installation.

Comparable generation measured on a 513-module repository took roughly 50
minutes, against a 5-second lock timeout.

### Secondary consequences

1. `run_live_wiki_full_build_for_installation_job` (`jobs.py:3005-3018`) fans out
   one build per repo, and scan-worker now runs **two replicas** (#242) - so
   replica B picks up repo 2 while replica A holds the lock for repo 1, and repo
   2 fails on the lock. The fan-out is self-defeating.
2. At 2936 and 3074 the `except` path and its `set_wiki_build_status(...,
   "failed")` are also inside the lock.

### Fix

The pattern already in this file: keep the lock around the
`_llm_spend_cap_reached` check, release it, run the generation, then re-acquire
briefly around `record_llm_spend`. Note `monthly_cap` is read inside the locked
block and used at the record call - the flash-review fix threads that value out
of the block, and the same applies at each site.

Prioritise 3074 and 3501 (every push) over 2936 and 3360 (upgrade only).

---

## 2. free→paid gate races across *different* webhook events

**`github-app/app_server/webhooks/paddle.py:103-179`**
**and `github-app/app_server/webhooks/marketplace.py:73-90`**
**Severity: medium.**

Both handlers read `previous_plan`, then enqueue a full AIRview build and a full
Docs build if it was `"free"`. The comment at `paddle.py:311-320` claims
`claim_webhook_delivery` makes this gate hold under concurrency.

It does not. `claim_webhook_delivery` (`app_server/db.py:165-187`) dedupes on
`(source, delivery_id)` - per **event id**. Two *different* events for the same
installation (`subscription.created` + `subscription.updated`, commonly
delivered together on checkout) both pass the claim, both read `"free"`, and
both enqueue the builds.

`set_installation_plan` (`app_server/db.py:61-66`) is a bare `UPDATE` with no
conditional transition and no return value, so nothing makes the read-then-write
atomic. The claim is namespaced by `source` ("github" / "paddle"), so it cannot
dedupe a Marketplace event against a Paddle one either.

**Mitigating:** `run_live_wiki_full_build_job` returns early when
`_clusters_with_uncovered_wiki_work` is empty (`jobs.py:2924-2930`), so a
*sequential* duplicate is cheap. It costs real LLM spend only when the two run
concurrently - which two replicas make plausible.

### Fix

Make the transition atomic rather than read-then-write - e.g. have
`set_installation_plan` do `UPDATE ... WHERE plan = 'free' RETURNING plan` and
gate the enqueue on whether a row came back, or add a per-installation
build claim analogous to `claim_webhook_delivery`.

---

## 3. Stale comment understates the signature replay window

**`github-app/app_server/webhooks/paddle.py:315`**
**Severity: low (documentation).**

The comment reads *"The signature's own 5s timestamp tolerance already makes
captured payload replay a narrow window."* `verify_paddle_signature` defaults to
`tolerance_seconds: int = 60` (`app_server/paddle_webhook_verify.py:16`), and
the comment there explains it was deliberately widened from 5s because 5s was
tight enough that a customer could pay and never get upgraded.

No runtime effect, but it is load-bearing reasoning about a replay window, and
it now understates that window by 12x.

---

## 4. `_is_safe_next_path` misses control characters (not currently exploitable)

**`github-app/app_server/auth.py:309-317`**
**Severity: low.** Latent - protected only by a framework implementation detail.

The guard blocks `//evil.com` and `/\evil.com`, and its comment explains the
browser normalisation that makes the backslash form dangerous. It does not block
tab, LF or CR, which the WHATWG URL spec says browsers **strip** before parsing -
so `/<TAB>/evil.com` becomes `//evil.com` by the same mechanism the comment
already reasons about. Verified:

```
'/dashboard'      -> '/dashboard'
'//evil.com'      -> '/dashboard'      blocked
'/\evil.com'      -> '/dashboard'      blocked
'/\t/evil.com'    -> '/\t/evil.com'    ALLOWED
'/\n/evil.com'    -> '/\n/evil.com'    ALLOWED
```

The value also survives the cookie round-trip: `set_cookie` encodes the tab as
`\011` in a quoted string and it parses back as a real tab.

**Why it is not exploitable today:** Starlette's `RedirectResponse` percent-
encodes the location, emitting `Location: /%09/evil.com`. A browser treats that
as a literal same-origin path rather than stripping it, so the redirect stays on
our own domain.

That is defence by accident, not by design. It becomes live the moment
`next_path` reaches a browser through anything that does not percent-encode -
a `<meta http-equiv="refresh">`, a JS `location.assign`, or an `<a href>`.

**Fix:** reject any `next_path` containing a character below 0x20, alongside the
existing checks. One line, and it removes the dependency on Starlette's quoting.

---

## 5. `/v1/managed-audit` is missing from the body-size map, and unthrottled

**`github-app/app_server/ingest_limits.py:38-43`** (the omission)
**`github-app/app_server/managed_audit_api.py:96-141`** (the endpoint)
**Severity: medium.** Authenticated, but a single leaked API token is enough.

`limit_ingest_body_size` (`main.py:73-87`) runs before routing and rejects a body
on its declared Content-Length. It consults `MAX_BODY_BYTES_BY_PATH`, which lists
`/v1/telemetry`, `/v1/runtime-events`, `/v1/embeddings` and `/v1/demo-scan`.

**`/v1/managed-audit` is not in that map**, and `check_declared_body_size`
returns early when the path is absent (`ingest_limits.py:67-69`). So the one
endpoint that accepts a **25 MB** payload (`MAX_EVIDENCE_BYTES`) is the one with
no pre-routing cap.

Three consequences, worsening in order:

1. **Pydantic's `Field(max_length=MAX_EVIDENCE_BYTES)` only rejects after the
   body is materialised.** That is precisely the cost the module exists to
   avoid - its own docstring says so: *"a check there rejects a payload the
   process has fully materialized, which is the cost the cap exists to avoid."*
2. **No Content-Length requirement**, since that is only enforced for mapped
   paths. A chunked request therefore has no declared size at all, so the body
   is read to completion before any limit applies. Whether an upstream proxy
   caps this is outside this repository - I could not verify it here.
3. **The endpoint has no request rate limit.** `demo_scan_api.py:125` and
   `embeddings_api.py:139-145` both rate-limit; `managed_audit_api.py` has only
   business quotas. So the pattern is an inconsistency, not a deliberate
   exemption.

Compounding it, `_decode_and_validate_evidence` (TOON decode + full AIR schema
validation) runs at line 106, **before** either quota check at 108 and 121. A
caller who can never pass the cooldown can still force decode+validate on every
attempt.

### Fix

Add `/v1/managed-audit` to `MAX_BODY_BYTES_BY_PATH` (25 MB + JSON overhead), and
move `check_and_reserve_monthly_repo_scan_slot` above the decode - it needs no
evidence. The cooldown genuinely needs the decoded LOC, so it has to stay after.

---

## 6. Embeddings rate limit is partitioned by a caller-controlled key

**`github-app/app_server/embeddings_api.py:139-143`** (the key)
**`github-app/app_server/embeddings_api.py:74-80`** (the reasoning it defeats)
**Severity: medium.** Authenticated.

The rate-limit bucket is `ratelimit:embeddings:{installation_id}:{body.repo_id}`,
and `repo_id` is a caller-supplied string on the request body
(`EmbeddingsRequest`, max 128 chars). Varying it yields a **fresh 2000-req/hour
counter per value**, so a token holder can send unbounded requests by rotating it.

The comment at `embeddings_api.py:74-80` anticipates that `repo_id` is
caller-supplied and concludes *"nothing worse than a wasted Redis key results
from an odd value."* That inference is wrong in one respect: the field does not
affect authorization or billing, correctly, but it does select the rate-limit
counter - so an odd value costs a wasted key **and** a fresh budget.

This defeats the stated purpose of the limit, given three lines earlier: *"this
only stops a caller from turning one token into unbounded upstream volume."*

### Compounding: the spend check is not locked

`create_embeddings` reads spend at line 173, calls the provider at 194, and
records at 212 - with **no `installation_spend_lock`** anywhere. Every equivalent
cycle in `scan_worker/jobs.py` takes that lock precisely to make check-then-record
atomic. Here, concurrent requests for one installation all read the same
under-cap figure and all proceed.

Individually each is minor; together they remove both bounds at once - unlimited
request volume against an unlocked cap check. The monthly cap remains the only
backstop, and it is the one that can be overshot by concurrency.

### Fix

Derive the bucket from something the caller cannot choose, or keep `repo_id` for
fairness but add an installation-wide counter alongside it, so rotating the field
splits a fixed budget rather than multiplying it. Separately, wrap the cap check
and `record_llm_spend` the way `jobs.py` does.

---

## 7. "Administers this installation" is derived from an access-level GitHub endpoint

**`github-app/app_server/admin.py:188-198`** (the source)
**`github-app/app_server/admin.py:363-390`, `336-360`** (the consumers)
**Severity: medium, pending verification of GitHub's semantics.**

`_fetch_administered_installation_ids` builds the authorization set from
`GET /user/installations`. GitHub documents that endpoint as listing installations
the user "has explicit permission (`:read`, `:write`, or `:admin`) to access" -
so **read access to a single repository the app is installed on is enough** to
appear in it. The set is nonetheless named `administered_ids`, and
`_require_authorized_installation` rejects with *"you do not administer this
installation"*.

The design already compensates for this on the main path, and says so:
`_require_seat_if_paid` (336-348) gates paid installations on a **seat** rather
than GitHub rights, with the docstring noting GitHub's manage-set "in many orgs
is every Owner, so relying on it alone would let an unlimited number of people
ride free on one purchase." That reasoning is right.

Two paths are not covered by it:

1. **`/admin/{org}/{repo}/billing-portal` (582-583)** is gated by
   `_require_authorized_installation` alone - deliberately, so a customer whose
   card failed can still reach it. But that means anyone in the coarse set can
   mint a Paddle customer-portal session, which can view the subscription, change
   the payment method, or cancel it.
2. **`add_initial_installation_member_if_empty` (354)** - "the first verified
   GitHub admin to show up becomes seat one". If the set includes non-admins,
   the first *org member* to load the page after purchase claims seat one, which
   then satisfies `_require_admin_installation` and exposes `/admin/{org}/{repo}`
   - a page that lists **API tokens** (408) and team members (409).

### Confidence

This rests on GitHub's documented semantics for `/user/installations`, which I
could not exercise against the live API from here. **Verify before acting:** have
a non-admin org member with read access to one installed repository call
`GET /user/installations` with a user-to-server token and check whether the org's
installation appears. If it does not, this finding collapses and only the naming
is misleading. If it does, both paths above are live.

### Fix (if confirmed)

Gate the billing portal on seat-or-first-admin rather than the coarse set, and
make the initial-seat claim require something stronger than set membership -
e.g. the GitHub org admin check, or the account that completed checkout (which
is already known, via `installation_token` in the Paddle custom_data).

---

## 8. Affiliate commissions are never reversed on a refund or chargeback

**`github-app/app_server/webhooks/paddle.py:54-60`** (event routing)
**`github-app/app_server/affiliates.py:53-76`** (the write)
**Severity: medium.** Real money, unbounded, and nothing surfaces it.

`_handle_transaction_completed` records 15% of `details.totals.total` on every
`transaction.completed` for a referred installation. That row is then payable via
`list_affiliates_with_totals` and settled by `mark_commissions_paid`.

Nothing ever reverses it. Grepping the whole of `app_server/` and `scan_worker/`
for `refund`, `chargeback`, `adjustment` or `dispute` returns **no matches**, and
the webhook router handles exactly six event types: `transaction.completed` plus
the five subscription lifecycle events. Paddle signals a refund as
`adjustment.created` (and `transaction.updated`), neither of which is handled.

So if a customer pays, the affiliate accrues commission; if that payment is later
refunded or charged back, the commission remains on the books at full value and
is paid out on the next manual settlement. The loss is the refunded amount's 15%,
plus the refunded revenue itself.

Two things make it easy to miss rather than easy to notice:

1. Payouts are a manual admin action, so there is no automated reconciliation
   that would surface the discrepancy.
2. `list_affiliates_with_totals` reports `total_owed_usd` from
   `affiliate_commissions` alone - it has no notion of a transaction that was
   later reversed, so the admin page shows the inflated figure as authoritative.

### Fix

Handle `adjustment.created` (and refund-shaped `transaction.updated`): locate the
commission by `paddle_transaction_id` - already `UNIQUE` on that table - and
either delete it, negate it, or add a `reversed` flag that
`list_affiliates_with_totals` excludes. A negating row is probably better than a
delete, since it preserves the audit trail the rest of this module is careful
about.

---

## 9. `clear_api_key` rewrites the credentials file without the permission care `_save_key` takes

**`src/aletheore/credentials.py:105-116`** vs **`76-102`**
**Severity: low.**

`_save_key` is exemplary. It opens with `os.open(..., 0o600)` and calls
`os.fchmod(fd, 0o600)` *before* writing, and its comment explains exactly why:
`os.open`'s mode only applies on creation, so a file that "pre-dates this
restrictive-permissions fix, or was seeded some other way" keeps whatever
permissions it had - and `write_text()` then `chmod()` would leave the key
briefly world-readable in between.

`clear_api_key` writes the same file, containing every *remaining* provider's
key, with a bare `credentials_path.write_text(...)` (115). None of that reasoning
is applied.

In the common case this is harmless: the guard at 106 means the file already
exists, and truncating an existing file preserves its mode - so a 0600 file stays
0600. It matters in precisely the scenario `_save_key`'s own comment calls out: a
credentials file that is already 0644 gets its remaining keys rewritten into it
and left that way, where `_save_key` would have corrected it to 0600.

### Fix

Reuse the `_save_key` write path (extract the fd+fchmod+write into a helper both
call). It also makes the permission guarantee a property of the file rather than
of which function happened to touch it last.

---

## 10. Extensionless citation pattern has no left boundary, producing false-verified citations

**`src/aletheore/citation_verifier.py:54-75`**
**Severity: low, but in the grounding primitive.**

`_extensionless_citation_pattern` builds an alternation of real extensionless
paths from the scan inventory, so `Dockerfile:12` and `Makefile:8` are extractable
at all. The alternation has no left-hand boundary, so it also matches a **suffix
of a longer token**. Verified against real code:

```
'see TestMakefile:3 for details'  ->  [{'file': 'Makefile', 'line': 3}]
'see Makefile:3 for details'      ->  [{'file': 'Makefile', 'line': 3}]
'see docker/Dockerfile:5'         ->  [{'file': 'docker/Dockerfile', 'line': 5}]
'step 3:12 of the plan'           ->  []
'http://host:8080/x'              ->  []
```

Prose naming `TestMakefile:3` - a file that need not exist - yields a citation to
`Makefile:3`, which exists, so it is counted as **verified**. That is a false
verification: the one outcome this module exists to prevent, and it inflates the
"N of M citations verified" figure the report prints.

The docstring anticipates the adjacent cases and gets them right - it will not
match `http://host:8080` or `step 3:12`, both confirmed above. The prefix case
was the one not considered.

`_CITATION_PATTERN` does not share the bug: its `[\w./-]+` is greedy, so
`MyApp.py:3` is captured whole as `MyApp.py` rather than as `App.py`.

### Fix

Add a left boundary to the alternation, e.g. `rf"`?(?<![\w./-])({alternation}):(\d+)`?"`.
Worth a test case per row of the table above, since the neighbouring behaviours
are exactly what must not regress.

---

## 11. A hard crash mid-webhook permanently drops a plan change

**`github-app/app_server/webhooks/paddle.py:326-337`** (claim ordering)
**`github-app/app_server/webhooks/paddle.py:122-139`** (non-atomic writes)
**Severity: low-medium.** Narrow window, permanent consequence.

The event is **claimed before it is handled**, and the claim is only handed back
on a caught exception:

```
if not await claim_webhook_delivery(pool, "paddle", event_id, ...):   # claimed
    return Response(status_code=200)
try:
    await handle_paddle_webhook_event(...)                            # then handled
except Exception:
    await release_webhook_delivery(pool, "paddle", event_id)          # graceful only
    raise
```

The graceful path is correct and its comment says exactly why. What it does not
cover is a **non-exception** death between claim and completion - OOM kill, a
deploy rolling the pod, a SIGKILL. The claim row survives, so Paddle's retry of
that event hits the duplicate check at 327 and is discarded with a 200. The plan
change is then lost permanently, with no error anywhere.

Compounding it, the three writes inside the handler are **not transactional**:

```
await set_installation_plan(pool, installation_id, plan)              # 122
await add_paddle_ids_to_installation(pool, installation_id, ...)      # 124
await set_extra_seats(pool, installation_id, extra_seats)             # 139
```

So the same crash can also leave an installation on the new plan with stale
`extra_seats`, or upgraded with no Paddle IDs recorded - a state no retry will
ever correct, because the retry is discarded.

This is the failure the module already cares about elsewhere: the signature
tolerance was widened from 5s to 60s precisely because "5s was tight enough that
a customer could pay and never get upgraded"
(`paddle_webhook_verify.py:12-15`). This is the same outcome by a different route.

### Fix

Two independent improvements:

1. **Claim, handle and write in one transaction**, so a crash rolls back the
   claim along with the partial writes and Paddle's retry finds the event
   unclaimed. This also makes the three writes atomic for free.
2. Failing that, record the claim as *in-flight* with a timestamp and treat a
   stale in-flight claim as reclaimable, so a crashed delivery is retried rather
   than absorbed.

---

## 12. The audit ChatOps trigger is a bare substring match with no author filter

**`github-app/app_server/webhooks/issue_comment.py:31`**
**Severity: low.** One certain nuisance, one latent loop.

```python
if AUDIT_COMMAND not in payload.get("comment", {}).get("body", ""):
    return
```

`"/aletheore audit"` is matched **anywhere** in the body, with no anchoring to the
start of a line and no check on who authored the comment.

**Certain today:** any comment that merely *mentions* the command fires a real
audit - quoting a colleague's request in a reply, or writing "don't run
/aletheore audit on this branch". The commenter still needs write/admin and a
paid plan, so this is waste rather than a privilege issue, and the job's cooldown
bounds it. But `run_managed_audit_pr_job` only applies the *real*, LOC-scaled
cooldown **after** clone+scan (`jobs.py:1204-1210`), so a re-trigger past the
coarse minimum still pays for a clone and a full scan before being rejected.

**Latent:** with no author filter, any bot comment containing that string would
re-trigger the handler. Nothing does today - I grepped all of `github-app/` and
the only match is an email template (`aletheore audit . --managed`, no leading
slash). It would become live the moment someone adds help text like "comment
`/aletheore audit` to run one" to a PR comment the app itself posts. Whether it
would loop indefinitely also depends on whether
`get_repo_permission_for_user` resolves an app bot's login, which I did not
verify - a 404 there would fail closed and break the loop by accident.

### Fix

Anchor the match (command at the start of a line, optionally the whole comment),
and skip comments whose author is the app itself. Both are one line each, and the
second removes the dependency on a GitHub API behaviour nobody has checked.

---

## Verified as sound (no action)

Recorded so a later pass does not re-litigate them:

- **`paddle_webhook_verify.py`** - constant-time compare via
  `hmac.compare_digest`, rejects missing `ts`/`h1`, non-integer `ts`, and
  non-UTF-8 bodies. `abs()` on the clock delta accepts future-dated timestamps,
  which is intended for host clock drift.
- **`webhooks/paddle.py:72-80`** - installation identity comes from a
  server-signed token (`unsign_checkout_installation_id`), not a caller-supplied
  integer. The reasoning in the comment is correct: a Paddle signature proves
  only that Paddle sent the event, never that the payer was authorized to name
  that installation.
- **`webhooks/paddle.py:112-120`** - customer_id ownership check closes the
  billing-portal-hijack path as defence in depth.
- **`webhooks/paddle.py:331-337`** - `release_webhook_delivery` on exception, so
  a Paddle retry of a failed event is not discarded as a duplicate.
### `affiliates.py` (other than Finding 8)

- `record_referral` and `record_commission` are both `ON CONFLICT DO NOTHING`
  on their natural keys (`installation_id`, `paddle_transaction_id`), so a
  re-delivered webhook cannot re-attribute or double-count.
- `list_affiliates_with_totals` uses scalar subqueries rather than JOINs, with a
  recorded reproduction of the cartesian-product bug that inflated a $30 balance
  to $90. Correct, and the docstring notes a one-referral check would have hidden it.
- Commission arithmetic uses `Decimal` with explicit `ROUND_HALF_UP` quantisation
  (`webhooks/paddle.py:259-261`), not float.

- **`claim_webhook_delivery`** - single `INSERT ... ON CONFLICT DO NOTHING`, so
  the claim itself is genuinely atomic. The defect in Finding 2 is what it is
  being asked to cover, not how it works.

### `auth.py`

- **`_derive_key` (85-102)** - HKDF-derived independent subkeys per purpose, so
  cookie signing and token encryption never share raw key material.
- **Distinct salts per token type** (`sign_oauth_state` "oauth-state",
  `sign_checkout_installation_id` "checkout-installation-id") - a token minted
  for one purpose cannot be replayed against another.
- **`callback` (352-396)** - the missing-state case redirects back through
  `/auth/login` rather than exchanging the code, which closes the OAuth login-CSRF
  path where an attacker sends a victim a bare `?code=` link. The reasoning in
  that comment is correct and the mitigation is the right one.
- **State compared with `hmac.compare_digest`** (394), not `==`.
- **`get_current_session` (290-305)** - `InvalidToken` deletes the session rather
  than 500ing on every request from that user.
- **Cookies** - `httponly`, `secure`, `samesite="lax"` on all three.
- Rate limiting fails **open** on a Redis outage (70-75). Deliberate and
  documented, consistent with the other endpoints; noted as an accepted risk
  rather than a finding.

### SQL and client IP

- **No SQL injection surface.** Swept every `execute`/`fetch*` call in
  `app_server/` and `scan_worker/` for f-string or concatenated SQL: zero hits.
  All queries are parameterised.
- **`client_ip_from_forwarded_for`** (`paddle_ip_allowlist.py:61-67`) takes the
  **last** `X-Forwarded-For` entry, which Caddy appends as the real peer, so
  attacker-supplied earlier entries do not win. Every IP-based rate limit depends
  on this and it is correct. (It does assume traffic cannot reach the app
  bypassing Caddy - true for the deployed topology, worth preserving.)

### `frontend.py` - XSS posture is sound

2,458 lines with 61 `innerHTML` sinks, and I could not find a hole:

- **`escapeHtml` (446-450)** escapes `& < > " '` - all five, so it is safe in
  attribute context, which matters because `findingActionButtonHtml` (878-887)
  interpolates into `data-*="..."` attributes.
- Every user-controlled finding field (`f.path`, `f.pattern`, `f.summary`,
  `f.package`, `f.advisory_id`, `f.match_preview`, `f.ecosystem`) is escaped at
  every site checked (818-830, 918-929, 878-887).
- **`renderWikiMarkdown` (459-487)** is the highest-risk sink - it renders
  *model-written* text derived from repository content an attacker controls. It
  escapes the entire source **first**, then promotes a fixed set of markdown
  tokens to tags with no interpolated attributes. Escape-before-promote is the
  correct order and the implementation matches its comment.
- Server-side templates carry `{repo}`/`{org}` as literal placeholders
  substituted **client-side**, so user data never enters the server-rendered
  HTML at all.

### `embeddings_api.py` (other than Finding 6)

- `MAX_TEXTS_PER_REQUEST` 256, `MAX_CHARS_PER_TEXT` 8,000, 3 MB body cap enforced
  pre-routing - the request shape is properly bounded.
- Provider errors are caught and degraded to a 502 with the upstream message
  logged, never returned - correct, since that message can quote the caller's own
  source back.
- Spend is billed on the provider's reported token count, not a local estimate.

### `managed_audit_api.py` (other than Finding 5)

- Verification tokens are `secrets.token_hex(32)` - 256 bits, not enumerable.
- Job status enforces installation ownership and returns **404**, not 403
  (`managed_audit_api.py:229-230`), so it is not an existence oracle.
- `/v1/audit/{token}/verify` checks against the key recorded **on the report**,
  not the current one, so a rotation cannot retroactively invalidate certificates.

### `demo_scan_api.py`

- Public and unauthenticated, but IP rate-limited, cooldown-reserved, queue-depth
  capped, and body-capped at 4 KiB. Repo size is checked *before* the cooldown
  slot is reserved so a rejected repo does not cost the visitor their scan -
  deliberate and correct.

### `src/aletheore` (partial)

- **`credentials.py` `_save_key` (76-102)** - fd + `fchmod(0o600)` before any key
  content is written, closing the TOCTOU window a `write_text`-then-`chmod` would
  leave open. This is the reference implementation; see Finding 9 for the one
  place it is not reused.
- **`secrets.py` detection patterns (69-79)** - no nested quantifiers and no
  alternation inside repetition, so no catastrophic-backtracking shape. Bounded
  character classes throughout.
- **`mcp_server.py` `_search_files` (163-191)** - iterates files enumerated from
  the repo tree via `iter_all_files`, never a caller-resolved path, so
  `path_glob` can only filter that set and cannot escape it. No file in this
  module is opened from a caller-supplied path.
- **`mcp_server.py` regex search** - literal mode runs in-process (no backtracking
  risk); regex mode runs only in a **child process** with a 5s timeout
  (`_run_search`, `_SEARCH_TIMEOUT_SECONDS`). Process isolation is the correct
  mitigation for attacker-supplied patterns, rather than trying to validate them.
- **No shell surface** - no `subprocess`, `os.system`, `shell=True` or `Popen`
  anywhere in `mcp_server.py`.

### `src/aletheore` - AST sweep of all 54 non-test files

Parsed every module and walked the tree rather than grepping, for four defect
classes that tend to hide in large codebases:

| class | count | verdict |
|---|---|---|
| bare `except:` | **0** | clean |
| mutable default arguments | **0** | clean |
| silent `except ...: pass` | 6 | all deliberate, checked individually |
| runtime `assert` | 3 | all type-narrowing, not validation |

- The **6 silent handlers** are documented best-effort degradations, not swallowed
  errors: `search_index.py:935` (FTS index creation - "an index that fails to
  build costs the exact-identifier half of search, not the search itself"),
  `evidence.py:330` (scan-cache write - "only costs the next run its incremental
  speedup, not correctness"), `evidence.py:579` (appending to `.gitignore`),
  plus `licenses.py:288`, `telemetry.py:63`, `vulnerabilities.py:476`.
- The **3 asserts** are all `assert <proc>.stdout is not None` immediately after
  `Popen(stdout=PIPE)` (`git_intel/incremental.py:105`, `secrets.py:291`,
  `adapters/openai_compatible.py:55`). Type-narrowing for the checker, on a
  condition that cannot occur. Stripping them under `python -O` would not
  introduce a behavioural bug.

### `vulnerabilities.py`

- Only two outbound URLs, both hardcoded constants (`OSV_BATCH_URL`,
  `OSV_VULN_URL_TEMPLATE`). No caller-controlled destination, so no SSRF surface -
  consistent with the 2026-08-10 audit's SSRF fix still holding.

### `citation_verifier.py` (other than Finding 10)

The strongest module read so far, and worth preserving as-is:

- **Three explicit grounding levels** (file exists / line in bounds / content
  matches) with the module docstring stating that "reporting a level you didn't
  reach is the same defect as not checking at all" - and
  `citation_verification_section` genuinely varies its wording by the level
  actually reached, including admitting when nothing was bounds-checked.
- `line_bounds_checked` is incremented only when a real length came back, so a
  fetcher that fails cannot inflate the claim.
- `citation["line"] < 1` is rejected (`app.py:0` used to pass).
- `local_line_count_fetcher` resolves both sides and checks `is_relative_to(root)`,
  so a path escape or a symlink out of the tree returns `None` rather than reading.
- A `None` from the fetcher **skips** the bounds check rather than failing it, so
  a read error never manufactures a false "unverified".
- `load_verifiable_evidence` returns `None` when the evidence has no file
  inventory, so a placeholder blob is reported as "cannot check" rather than as
  "everything failed".

### `webhooks/issue_comment.py` (other than Finding 12)

- **Fails closed** on a permission-check error (63-73): an API hiccup drops a
  legitimate trigger rather than admitting an unverified commenter.
- Requires `write`/`admin` **and** a paid plan, with the paid gate added because
  the ChatOps path previously had no equivalent of the HTTP trigger's 402.
- Denials are **silent** and logged rather than replied to, so the bot does not
  narrate a repo's access model to whoever comments.
- Only `action == "created"` is handled, so editing a comment to insert the
  command does not fire it.

### `webhooks/push.py`, `installation.py`, `marketplace.py`

- Signature verification is centralised at the route layer rather than repeated
  per handler; these modules receive already-verified payloads.
- `marketplace.py` shares the free->paid enqueue shape covered by Finding 2.

---

## 13. A deleted `--` comment line breaks the diff parser, silently dropping findings

**`github-app/scan_worker/flash_review.py:353`** (the pattern)
**`github-app/scan_worker/github_api.py:97`** (the marker it collides with)
**Severity: medium.** Silent false negatives in the paid PR product.

`fetch_pr_diff` joins per-file patches with a custom marker:

```python
parts.append(f"--- {file['filename']} ---\n{patch}")
```

`_diff_valid_lines` finds those markers with `_FILE_MARKER_RE = r"^--- (.+) ---$"`.
In a unified diff a **deleted** line is prefixed with `-`, so a deleted line whose
content is `-- X ---` arrives as `--- X ---` and matches the marker exactly.

Reproduced against the real function:

```
--- db/schema.sql ---
@@ -10,6 +10,6 @@
 CREATE TABLE users (
--- users table ---          <- a deleted SQL comment
-  id INT,
+  id BIGINT,
   name TEXT
 );
```

```
file='db/schema.sql'   valid_lines=[10]    <- should cover ~10-15
file='users table'     valid_lines=[]      <- phantom file invented
```

Everything after the deleted comment is attributed to a phantom file, and because
the marker branch resets `current_line = None`, **nothing further is recorded at
all**. The actual change (`id INT` -> `id BIGINT`) is not in the valid set, so a
correct finding about it is classified `out_of_diff` by `_validate_findings` and
dropped - surfacing to the customer as *"No issues found in this diff."*

The trigger is broader than SQL: any deleted line matching `--`...` ---`. That
covers SQL, Lua, Haskell and Ada comments, and `--- section ---` dividers in any
language. Deleting a commented section header during a refactor is ordinary.

This is the exact failure class the function's own docstring was written about -
it records a previous incident where a deletion-only hunk "silently suppressed a
whole class of true positives" - reached by a different route.

### Fix

The marker and the diff body share a namespace, which is the root problem. Either:

1. **Only accept a file marker at a file boundary.** `fetch_pr_diff` joins with
   `"\n\n"`, so a real marker is always preceded by a blank line or starts the
   text. Requiring that makes a mid-hunk collision impossible.
2. **Use a delimiter that cannot appear in a diff line**, e.g. an ASCII unit
   separator, or carry the file list as structured data instead of re-parsing
   text that was structured to begin with (the GitHub response is already JSON,
   with `filename` and `patch` as separate fields).

Option 2 is the stronger fix: `fetch_pr_diff` flattens structured JSON into text
that `_diff_valid_lines` then re-parses, and the bug lives entirely in that
round-trip.

---

## 14. The scanner walks the whole tree 19 times per scan, pruning only afterwards

**`src/aletheore/scanner/detect.py`** — `_nested_git_roots` (218-234) and six
`rglob` detectors.
**Severity: low (performance).** Correctness is fine; the cost is not.

`detect.py` is careful about *what it reports* and careless about *what it walks*.
Every detector filters `IGNORED_DIRS` **after** the traversal has already
descended into them:

```python
for candidate in repo_path.rglob(name):
    rel_parts = candidate.relative_to(repo_path).parts
    if any(part in IGNORED_DIRS for part in rel_parts):   # filtered here...
        continue                                          # ...but already walked
```

And `_nested_git_roots` walks the entire tree with **no pruning at all**, despite
being called from `_iter_source_files`, which prunes carefully three lines later.

### Measured on this repository

| traversal | count | cost |
|---|---|---|
| `_detect_docker_compose_services` | 4 rglob | 255 ms |
| `_detect_declared_env_vars` | 4 rglob | 233 ms |
| `_detect_kubernetes_manifests` | 2 rglob (+ YAML-parses every hit) | 203 ms |
| `_detect_migration_directories` | 1 rglob | 137 ms |
| `_detect_terraform_files` | 1 rglob | 59 ms |
| `_detect_helm_charts` | 1 rglob | 58 ms |
| `_nested_git_roots` | 6 unpruned `os.walk` | 232 ms |
| **total** | **19 full-tree traversals** | **~1.18 s** |

- **91% of files in this repo sit inside `IGNORED_DIRS`** (8,113 of 8,879).
- `_nested_git_roots` alone is **55% of `_iter_source_files`'s** runtime while
  returning zero roots, and it runs **six times per scan** - once per call site
  (`endpoints.py:1220,1228`, `detect.py:278`, `graph.py:2167,2191,2207`).

This scales with exactly the content that gets large: `node_modules`, `.venv`,
`dist`. A JS repository with 100k files under `node_modules` pays all 19
traversals over it.

### Fix

1. **Prune `IGNORED_DIRS` inside `_nested_git_roots`** - it is an `os.walk`, so
   `dirnames[:] = [d for d in dirnames if d not in IGNORED_DIRS]` is the whole
   change.
2. **Cache `_nested_git_roots` per `repo_path`** - it cannot change during a
   scan, and it currently runs six times.
3. **Replace the `rglob` detectors with one pruned `os.walk`** that collects all
   six markers in a single pass. They are looking for disjoint filenames in the
   same tree.

### Checked and NOT a problem

- **Symlink escape.** `rglob` in Python 3.12 does **not** recurse into symlinked
  directories - tested with a symlink pointing outside the repo at a directory
  containing a `migrations/` folder: `rglob` returned `[]`. So the concern raised
  by `_iter_source_files`'s own comment ("a symlinked directory would otherwise
  have its contents walked and reported on as if they were part of this repo")
  does not apply to the `rglob` detectors on this version. Worth pinning with a
  test, since Python 3.13 made this configurable via `recurse_symlinks`.
- **Reporting correctness.** Every detector does filter `IGNORED_DIRS`, so nothing
  from `node_modules` reaches the evidence. The defect is cost, not output.
- **YAML parsing** uses `yaml.safe_load` throughout, so untrusted manifests cannot
  execute anything.

---

## 15. The Java and C# pre-passes hold every parsed tree in memory for the whole scan

**`src/aletheore/scanner/graph.py:2163-2175`** (Java), **`2112-2130`** (C#)
**Severity: medium.** Unbounded memory, proportional to repository size.

`build_module_graph` runs a pre-pass per language to infer source roots
(Java) and namespace prefixes (C#), and caches the result as
`dict[Path, tuple[bytes, Tree]]` - the **full source bytes and the parsed
tree-sitter tree** for every file of that language. The comment explains the
intent:

> Cached alongside the source root inference below so the main loop's own
> per-file parse doesn't read and re-parse every .java file a second time
> from scratch - this pre-pass already did the identical work once.

The saving is real. The cost is that both dicts are locals of
`build_module_graph` and stay alive until it returns, so peak memory scales with
the number of Java/C# files in the repository.

### Measured on AutoMapper (512 `.cs` files)

| | |
|---|---|
| source bytes held | 2.2 MB |
| **RSS growth (source + trees)** | **82 MB** |
| per file | 164 KB |
| extrapolated to a 10,000-file repo | **~1.6 GB held for the whole scan** |

Tree-sitter trees are roughly **37x** the size of the source they came from, so
the trees are ~97% of that figure.

### What the cache is actually buying

| approach | memory held | cost |
|---|---|---|
| current: cache `(bytes, Tree)` | **82 MB** | - |
| keep only the extracted strings, re-parse in the main loop | **13 KB** | **0.21 s** |

**The current design holds 82 MB to avoid 0.21 seconds of re-parsing** on this
repository. At 10,000 files that becomes ~1.6 GB against roughly 4 seconds.

This matters because the scan worker is a hosted, memory-bounded container, and a
large customer monorepo is exactly the input that triggers it. A scan that OOMs
is worse than a scan that takes four seconds longer.

### Fix

The pre-passes only need small values out of each tree - Java needs the package
declaration, C# needs the namespace plus (post-#250) the declared type names.
Extract those, discard the tree, and let the main loop parse each file once:

```python
java_packages: dict[Path, str | None] = {}       # instead of dict[Path, tuple[bytes, Tree]]
```

If the re-parse cost is judged too high, an intermediate option is to keep the
cache but bound it (LRU by file count or total bytes) so peak memory is capped
rather than proportional.

### Related

Both dicts can be live simultaneously in a polyglot repository, so a repo with
substantial Java **and** C# pays both at once.

---

## 16. No file-size guard before parsing, and no per-file symbol cap

**`src/aletheore/scanner/graph.py:2170, 2194, 2233`**
**Severity: low-medium.** Compounds Finding 15.

Every file selected by extension is read whole and parsed, with no size check:

```python
source = path.read_bytes()      # 2233, and the two pre-passes at 2170 / 2194
```

There is also no cap on how many symbols or imports one file may contribute. The
only bound anywhere in the module is `_CSHARP_MAX_TYPE_EDGES = 40`, added in #250
for the type-reference edges specifically.

Given tree-sitter trees measure roughly **37x their source** (Finding 15), one
5 MB vendored C amalgamation becomes a ~185 MB tree - and under the Java/C#
pre-passes that tree is then held for the entire scan.

### Why the usual protections do not cover it

`IGNORED_DIRS` prunes the common homes of enormous generated files - `node_modules`,
`dist`, `build`, `out`, `.next`, `obj`. It does **not** include `vendor`, so a Go
repository with a vendored dependency tree, or a C project carrying
`vendor/sqlite3.c` (~8 MB, ~250k lines), is parsed in full.

The module already knows large files are a hazard: the comment at line 337 notes
that deeply nested C source "(Linux kernel C source) can exceed Python's recursion
limit and crash" - and that hazard was addressed by making every walk iterative.
The *size* and *memory* dimension of the same input was not.

### Fix

A size guard before `read_bytes()` is the whole change - anything past a few MB is
generated or vendored, and its symbols are noise in the evidence regardless.
Record skipped files in `unparseable_files` with the reason, so the omission is
visible rather than silent (the same discipline `files_missing_from_review_context`
already applies on the review side).

Adding `vendor` to `IGNORED_DIRS` is worth considering separately, though it needs
the same care the `obj`/`bin` comment shows - `vendor` is meaningful source in
some ecosystems.

---

## Scanner extractors - verified

All ten `_extract_*` functions in `graph.py` walk the tree-sitter AST with an
**explicit stack**, never recursion:

`_extract_python`, `_extract_javascript`, `_extract_go`, `_extract_rust`,
`_extract_java`, `_extract_ruby`, `_extract_php`, `_extract_c_family`,
`_extract_csharp`, `_extract_module_constants`.

This matters: a recursive walk over a tree-sitter AST blows Python's recursion
limit on real inputs - minified bundles, generated files, and (per the comment at
line 337) Linux-kernel-style C. Each extractor carries a comment pointing back to
`_extract_python`'s explanation rather than restating it, and `_extract_c_family`
notes it pushes `reversed(children)` to preserve source order while using a stack.

Consistent across ten independently-written extractors, which is the kind of thing
that usually has one exception. It does not.

---

## Addendum: why the C# graph was empty (root cause, found after the fix)

Not a finding - the defect is fixed and merged in PR #250 - but the cause recorded
there ("C# needs no import in the same namespace") is incomplete, and the real one
is both more specific and more actionable.

**Measured:** `AutoMapper/AutoMapper` has 512 `.cs` files and **230 `using`
directives in total**, 156 of them `System.*`.

**Why:** its `Directory.Build.props` sets, repo-wide:

```xml
<ImplicitUsings>enable</ImplicitUsings>
...
<ItemGroup>
  <Using Include="System.Reflection"/>
  <Using Include="System.Diagnostics"/>
</ItemGroup>
```

The usings are declared **in the build file, not in source files**. The .NET SDK
injects them at compile time, so source files legitimately do not contain them.
The scanner parses source only, so it sees almost nothing to resolve. This is the
default for `net6.0`+ templates, so it is the common case for modern C#
repositories rather than an AutoMapper quirk.

That makes the type-reference approach shipped in #250 the right fix: it derives
edges from what the code *does*, independent of where usings are declared.
**A complementary improvement** would be to parse `<Using Include="..."/>` items
out of `Directory.Build.props` and `*.csproj` and treat them as file-level
imports - cheap, deterministic, and it recovers framework-level edges that type
references cannot see.

### Java was checked and is NOT affected

The obvious follow-up hypothesis - "Java has the same implicit-same-package
property, so it has the same blind spot" - was tested against `google/gson` and
**disconfirmed**:

| corpus | language | files with imports | edges/module |
|---|---|---|---|
| flask | Python | 90% | 3.80 |
| **gson** | **Java** | **75%** | **4.11** |
| jq | C | 65% | 1.87 |
| AutoMapper (before #250) | C# | 2% | 0.36 |
| AutoMapper (after #250) | C# | 77% | 4.18 |

Java is healthy and needs no equivalent fix. A second hypothesis - that C# differs
because of flatter namespaces - was also tested and disconfirmed: AutoMapper has
**58 distinct namespaces** against gson's **23 packages**, so it is more spread
out, not less. Java has no MSBuild-equivalent way to declare imports outside
source, which is the whole of the difference.

### `search_index.py` - the one manual-escaping site, tested against a live index

`_escape_sql_literal` (716-722) doubles single quotes for a value interpolated
into a LanceDB `where` clause at two sites (1054, 1168). Its docstring correctly
notes the value "reaches here from an MCP tool argument, so it is caller-
supplied".

Manual escaping is normally where injection hides, and the correctness of quote
doubling depends on the dialect not treating backslash as an escape - an
assumption nothing in the code states. **Tested against the real LanceDB index**
at `~/.aletheore-bench/multi-flask/.aletheore/index.lancedb`:

| probe value | rows | verdict |
|---|---|---|
| `python` | 5 | normal match |
| `python' OR '1'='1` | 0 | treated as a literal, no injection |
| `python\' OR 1=1 --` | 0 | backslash is **not** an escape here |

The escaping holds. Worth a one-line comment recording that the dialect does not
honour backslash escapes, since that is the property the doubling relies on and
it is currently only true by observation.

Auto-detected values are additionally safe by construction:
`_detect_query_language` (1093-1118) can only return a value from the fixed
`_UNAMBIGUOUS_QUERY_LANGUAGES` / `_CUED_QUERY_LANGUAGES` tables, and returns
`None` unless exactly one language is named.

---

## Second pass (2026-08-22), findings #17-35

### 17. One invalid-UTF-8 byte anywhere in a repo aborts the entire scan

**`src/aletheore/scanner/graph.py`** - systemic, 39 `.decode()` calls, only 2 of
which use `errors="ignore"` (lines 438, 466). Every other call site -
`_symbol_entry`, `_params_text`, `_leading_block_comment`, every per-language
extractor's import handling - decodes raw byte-slices unguarded.
**Severity: critical.**

`build_module_graph` reads every source file as raw bytes and hands byte-slices
to `.decode()` throughout. Reproduced directly against the real entry point
(the one `evidence.py:439` calls, with no surrounding try/except):

```python
bad = b'/* Copyright \xe9 2019 Some Author */\nint foo(int x) {\n    return x;\n}\n'
(d / "bad.c").write_bytes(bad)
(d / "good.py").write_text("def ok():\n    return 1\n")
build_module_graph(d)
# UnicodeDecodeError: 'utf-8' codec can't decode byte 0xe9 in position 13
```

Confirmed by direct reproduction (this audit ran the exact snippet above): a
single non-UTF-8 byte in a C-style comment - an ordinary occurrence in real
C/C++ code with non-English author names, since C predates UTF-8 - crashes the
parse of **every file in the repo**, including the unrelated `good.py`.
Confirmed `evidence.py:439-440` has no try/except around the call, unlike the
existing `MAX_SOURCE_FILE_BYTES` guard (lines 2223-2226) which handles
oversized files gracefully and records them in `unparseable_files` - there is
no encoding equivalent. `github-app/scan_worker/jobs.py` wraps the scan job in
broad `except Exception`, so this doesn't crash the worker process, but the
entire evidence-build for that repo still fails outright, with no partial
results and no indication of which file or byte caused it.

**Fix:** `source.decode(errors="ignore")` (or `"replace"`) everywhere, matching
the pattern `_extract_module_constants` already uses. A single shared helper
(e.g. `_text(source, node)`) would make this consistent instead of ~37
independent unguarded call sites.

---

### 18. RRF fusion silently drops the score on any dual-retriever hit, crashing `answer_question`

**`src/aletheore/search_index.py:1301-1326`** (`_rrf_fuse`), **`:1538`**
(`search_index`'s return), **`src/aletheore/answer.py:38`** (the crash site).
**Severity: high.**

`_rrf_fuse` merges vector and full-text hits into one dict keyed by
`(module_path, symbol_name, start_line)`, writing `by_key[key] = hit`
unconditionally on every iteration:

```python
for hits in (vector_hits, text_hits):
    for rank, hit in enumerate(hits):
        key = (...)
        scores[key] = scores.get(key, 0.0) + 1.0 / (_RRF_K + effective_rank + 1)
        by_key[key] = hit          # last write wins - text_hits processed second
```

Vector hits carry a `_distance` field; full-text hits (from
`table.search(query_text, query_type="fts")`, LanceDB's own FTS API) carry
`_score` instead - confirmed directly against a real LanceDB table, the two
never overlap. For any chunk found by **both** retrievers, `by_key[key]` ends
up holding the *text* hit's dict, which has no `_distance` key.
`search_index` then builds its return with `"score": result.get("_distance")`
(line 1538) - `None` for exactly this case. `answer_question` does
`results[0]["score"] > confidence_threshold` with no None-guard, raising
`TypeError: '>' not supported between instances of 'NoneType' and 'float'`.
Neither `mcp_server.py`'s `aletheore_answer` tool nor `cli.py`'s `query answer`
command catches `TypeError` - this is an unhandled crash.

The trigger condition (top result found by both retrievers) is not an edge
case - it's what a confident, correct match looks like, i.e. exactly the case
the confidence gate exists to pass cleanly. `test_search_index.py`'s existing
`test_rrf_fuse_ranks_a_chunk_found_by_both_retrievers_first` passes a single
shared dict with no field distinction, so it checks ranking order only and
never exercises the clobbering; `test_answer.py` mocks `search_index` with a
hardcoded numeric score throughout, so the real integration path is never
exercised either.

**Fix:** compute a real fused score (carry the RRF score itself, or normalize
`_distance`/`_score` under a name that survives the merge) instead of relying
on whichever raw LanceDB row wins the overwrite; and have `answer_question`
handle `score is None` explicitly rather than assuming a float.

---

### 19. FastAPI router-prefix collection is scope-blind

**`src/aletheore/endpoints.py:191-220`** (`collect_static_prefixes`).
**Severity: high.**

`collect_static_prefixes` walks the entire file's AST unconditionally
(`for child in n.children: collect_static_prefixes(child)`, no scope
tracking) looking for `<identifier> = APIRouter(prefix=...)`, storing the
result in a flat `dict[str, str]` keyed only by variable name text:

```python
router_prefixes[source[left.start_byte:left.end_byte].decode()] = prefix
```

Any later assignment to a variable of the same name - including one local to
an unrelated function - overwrites the entry, because there is no notion of
which scope an `identifier` node belongs to. Reproduced directly:

```python
router = APIRouter(prefix="/api")

@router.get("/x")
def handler(): pass

def make_test_router():
    router = APIRouter(prefix="/testing")   # unrelated local, different scope
    return router
```

produces `[{'method': 'GET', 'path': '/testing/x', ...}]` - should be `/api/x`.
This is a common real-world shape: FastAPI factory functions that build a
sub-router locally frequently reuse the idiomatic `router` name, in the same
file as the module-level router the decorators actually bind to.

**Fix:** track `router_prefixes` per-scope (keyed by the enclosing
function/module node, not just the bare name), or restrict this walk to
module-level statements only.

---

### 20. Incremental endpoint caching doesn't invalidate on a cross-file router-mount change

**`src/aletheore/endpoints.py:1288-1298`** (mount-prefix pre-pass, always
fresh) vs **`:1303-1305`** (per-file cache reuse, blind to it).
**`src/aletheore/evidence.py:284-292`** and **`github-app/scan_worker/jobs.py:436-463`**
(the two cache producers, both hash/diff-per-file). **Severity: high** - live
on the production per-push/per-PR scan path.

`map_api_endpoints` recomputes `cross_file_router_mounts` fresh every call,
but the extraction loop skips re-parsing entirely for any file present in
`unchanged_endpoints`:

```python
if unchanged_endpoints is not None and rel_path in unchanged_endpoints:
    endpoints.extend(unchanged_endpoints[rel_path])
    continue
```

That cached list has the FastAPI prefix already baked into `"path"`. Both
cache producers decide "unchanged" purely per-file (content hash locally, git
diff on the hosted worker) - neither has any notion that a router-defining
file's composed endpoint paths depend on a *different* file's
`include_router(..., prefix=...)` call. Reproduced end-to-end against the real
`map_api_endpoints`: changing `app.py`'s `include_router` prefix from
`/api/v1` to `/api/v2` while leaving `routers/users.py` byte-identical left
the cached, now-stale `/api/v1/users` path in place across the second scan.

Concretely: a PR that only touches `app.py` (changing an `include_router`
prefix) leaves the router-definition file out of the diff, so its previously-
persisted composed path is reused verbatim in the new evidence/endpoint
inventory - on every push/PR scan of that repo from then on, until the router
file itself changes for an unrelated reason. This regresses the deliberate,
otherwise-correct cross-file prefix composition work documented in
`_resolve_router_definition_file`/`_collect_fastapi_include_prefixes`'s own
docstrings - correct at scan time, invalidated by a caching layer added
afterward that nothing accounts for.

**Fix:** either detect when any *mounting* file changed and force re-parse of
every file whose router it targets even if that file itself is
hash/diff-unchanged, or don't cache per-file endpoint lists for files that
produced any entry attributable to `cross_file_router_mounts`.

---

### 21. Rust `pub use` extracts `"pub"` as the import path, not the re-exported target

**`src/aletheore/scanner/graph.py:993-999`**. **Severity: high** - complete,
silent loss of the target on one of Rust's most common statements.

```python
if n.type == "use_declaration":
    for child in n.children:
        if child.type not in ("use", ";"):
            imports.extend(_rust_use_paths(child, source))
            break
```

For `pub use crate::foo::Bar;`, tree-sitter-rust's `use_declaration` has
children `[visibility_modifier("pub"), use, scoped_identifier(...), ;]`. The
loop's first non-`use`/`;` child is `visibility_modifier`, not the path - and
`break` fires before the real path node is ever reached. `_rust_use_paths`
falls through to its default case and returns `["pub"]` (or `["pub(crate)"]`,
etc.). Reproduced directly against the real installed tree-sitter-rust
grammar (this audit parsed the snippet and called `_extract_rust` directly):

```
pub use crate::foo::Bar;        -> imports: ['pub']
pub(crate) use crate::foo::Bar; -> imports: ['pub(crate)']
```

`_resolve_rust_path` then fails to resolve `"pub"` (no `crate`/`self`/`super`
prefix, no file named `pub.rs`), so the edge silently vanishes - both the
garbage import and the real dependency are lost. Measured on the real `serde`
repo (`~/.aletheore-bench/multi-serde`): **108 `pub use` statements across 14
of 208 `.rs` files**, including `serde_core/src/private/mod.rs`, which builds
its entire public re-export surface this way. `pub use` is the standard Rust
idiom for constructing a crate's public API - not a corner case.

**Fix:** skip `visibility_modifier` when finding the path child, e.g.
`if child.type not in ("use", ";", "visibility_modifier")`.

---

### 22. `audit`'s `--map-schema`/`--no-map-schema` is parsed but never forwarded

**`src/aletheore/cli.py:1358-1374`** (call sites) vs **`:1340-1344`** (flag
definition), vs **`:1410-1411`** (`scan`'s correct forwarding, for
comparison). **Severity: high.**

`audit` defines the flag identically to `scan`, but neither of its two call
sites passes it through:

```python
raise typer.Exit(
    code=_managed_audit(
        path, token, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints,
    )   # map_schema never passed
)
...
raise typer.Exit(
    code=_audit(path, agent, check_vulnerabilities, scan_git_history, check_licenses, map_endpoints)
)   # same omission
```

`_scan`'s handling of `map_schema` is the only place that treats `None`
differently from `False`:

```python
if map_schema is False:
    resolved_schema, schema_reason = False, "skipped (--no-map-schema)"
else:
    resolved_schema, schema_reason = _resolve_schema_entitlement()
```

Because `audit()` never forwards its parsed value, this always takes the
`else` branch - `--no-map-schema` on `aletheore audit` (managed or BYOK) is
completely inert, with no way to opt out from `audit` despite the flag
existing, being documented, and appearing to work exactly like `scan`'s copy.
Confirmed no test coverage: `grep -rn "map_schema" src/tests/test_cli.py`
returns nothing.

**Concrete failure scenario:** a user runs `aletheore audit --no-map-schema`
to keep database schema out of what's sent to an external coding-agent API
(the command's own consent prompt exists precisely because evidence is sent
off-machine). Schema mapping runs anyway; the user's explicit opt-out is
silently ignored.

**Fix:** add `map_schema` to both call sites, matching `scan`'s pattern.

---

### 23. AIRview's non-scanned-file fallback always raises `NameError`, caught silently - feature permanently broken

**`github-app/app_server/dashboard.py:412-418`** (the bug) - **`:460-468`**
(where it's swallowed). **Severity: medium-high** - not a security bug, a
complete, deterministic functional break of a shipped feature, invisible to
monitoring.

```python
def _fetch_wiki_file_content_sync(installation_id, repo_full_name, path) -> str | None:
    settings = get_settings()
    app_jwt = generate_app_jwt(settings.github_app_id, settings.github_app_private_key)
    token = get_installation_token(installation_id, app_jwt)
    return fetch_file_content(get_github_api_client(), token, repo_full_name, path)
```

Confirmed via `grep -n "get_github_api_client\|_github_http_client"
dashboard.py`: `get_github_api_client` is called here but never imported or
defined anywhere in this file. The file imports `_github_http_client` (a
wrapper for the same real client, from `app_server.admin`) and uses it
correctly elsewhere at line 75 - line 418 is a copy/paste-shaped typo calling
the wrong, unimported name. This function is only reached for files the scan
didn't index as a module (the route's own docstring: "one bounded GitHub
Contents lookup" for docs/config/workflow files). The bare
`except Exception` two call-frames up catches the resulting `NameError` and
logs it at `info` level as a generic "fetch failed", indistinguishable from a
real network/auth failure - the route then always returns
`404 "file not found in scan or repository"`.

Confirmed this is unexercised, not just unlucky in prod:
`tests/test_dashboard.py:1531-1554` monkeypatches
`_fetch_wiki_file_content_sync` itself out with a lambda, so the real
function body - the one with the bug - is never run by the test suite.
Because the exception is caught locally rather than propagating to `main.py`'s
global handler, no error alert ever fires.

**Concrete failure scenario:** a paying customer opens the AIRview wiki and
clicks any file outside the LLM-selected page set (a workflow YAML, a
Dockerfile, a config file) expecting the documented structural fallback. They
get "file not found" instead, every time, with nothing surfacing in
monitoring.

**Fix:** one-line - `get_github_api_client()` -> `_github_http_client()` at
line 418 (already imported). Also worth exercising the real function body
against a stubbed HTTP client instead of monkeypatching the function under
test.

---

### 24. Health-check-target and API-token creation are check-then-act, unlike the seat-limit fix in the same file

**`github-app/app_server/db.py:877-899`** (`add_health_check_target`) and
**`:1273-1290`** (`create_api_token`) - no atomic guard.
**`github-app/app_server/admin.py:865-876`** (health targets), **`:741-747`**
(`generate_token`), **`:1186-1194`** (`create_cli_token`) - the racy call
sites. **Severity: medium.**

`add_installation_member_within_seat_limit` (`db.py:719-775`) exists
specifically because a route-level read/count/insert sequence is race-prone
under concurrent requests, and fixes it with a per-installation advisory
lock wrapping a single-transaction CTE. That fix was never extended to the
two sibling per-installation quotas in the same file - both are a plain
count-then-insert with no atomicity:

```python
# admin.py:869-876
if await count_health_check_targets(pool, installation_id, repo_full_name) >= limit:
    raise HTTPException(status_code=409, ...)
target_id = await add_health_check_target(...)   # unconditional INSERT
```

Confirmed identically shaped at both `generate_token` and `create_cli_token`
against `max_api_tokens`. **Concrete failure scenario:** two concurrent
requests for two different URLs/labels, both already one below the limit,
both read the same under-limit count, both pass, both insert - the
installation ends up one (or more) over its plan's health-check-target or
API-token limit.

**Fix:** apply the same advisory-lock-wrapped CTE pattern
`add_installation_member_within_seat_limit` already uses, to
`add_health_check_target`/`create_api_token`.

---

### 25. `generate_token` discards the real token id and re-derives it with a racy re-query

**`github-app/app_server/admin.py:735-754`**. **Severity: medium** - verified
by direct comparison with the sibling route that does it correctly.

`create_api_token` (`db.py:1273-1290`) already returns the new row's id via
`RETURNING id`, and `create_cli_token` (`admin.py:1192`) uses that correctly.
`generate_token` does not - it discards the return value and re-derives the
id from a second, separate query:

```python
await create_api_token(pool, installation_id, token_hash, body.label, session["github_login"])
token_id = (await list_api_tokens(pool, installation_id))[0]["id"]
```

`list_api_tokens` orders by `created_at DESC, id DESC` and this assumes row
`[0]` is the token just created - an assumption that breaks under concurrent
creation for the same installation. Confirmed exactly as described by reading
both functions directly.

**Concrete failure scenario:** two admins on the same installation (or one
admin, two tabs) each generate a token within the same request window. Each
response pairs the caller's own freshly-generated raw secret with an `id`
that may actually belong to the *other* caller's token - and that wrong id is
then written into `admin_action_log` via `record_admin_action`, corrupting
the audit trail. The raw secret itself is never wrong (generated locally, not
read back) - this is a data-integrity defect in the admin audit log, not a
token leak. `test_generate_token_returns_raw_value_once` only checks the
token string's length, not the returned id, so this is untested.

**Fix:** `token_id = await create_api_token(...)` - the one-line fix
`create_cli_token` already demonstrates in the same file.

---

### 26. `_fetch_whoami`'s `response.json()` sits outside its own try/except

**`src/aletheore/cli.py:621-634`**. **Severity: medium.**

```python
def _fetch_whoami(token, api_base_url=..., http_client=None) -> dict | None:
    client = http_client or httpx.Client(base_url=api_base_url)
    try:
        response = client.get("/v1/whoami", headers={...}, timeout=5.0)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return response.json()
```

`response.json()` is called after the `try` block exits, so
`json.JSONDecodeError` (a `ValueError` subclass) it can raise on a 200
response with a malformed body is not caught - confirmed by reading the
function directly. This contradicts `_resolve_schema_entitlement`'s (the
only non-`status` caller) documented contract: "any failure ... is treated
as unentitled rather than raised". A malformed-200 (plausible from a
captive portal, misconfigured proxy, or CDN error page returned with a 200
status) is exactly this class of failure, but propagates as an unhandled
exception instead of degrading. `status()` hits the same bug directly. The
neighboring `_check_for_update` (12 lines above, same file) correctly
includes `ValueError` alongside `httpx.HTTPError` in its own except clause -
this exact failure mode was already known and handled once in this file, and
simply missed here.

**Fix:** move `return response.json()` inside the `try`, and add `ValueError`
to the `except` clause.

---

### 27. Django `urlpatterns += [...]` is silently skipped entirely

**`src/aletheore/endpoints.py:339-354`** (`_extract_django_routes`).
**Severity: medium.**

```python
def walk(n: Node) -> None:
    if n.type == "assignment":
        ...
        if is_urlpatterns and right is not None and right.type == "list":
            for item in right.named_children:
                entry = _django_call_to_entry(item, source, rel_path)
```

Only `n.type == "assignment"` is handled - confirmed by reading the full
function. `urlpatterns += [...]` parses to a distinct node type,
`augmented_assignment`, which `walk` does recurse into (via the unconditional
`for child in n.children: walk(child)`) but nothing ever matches it for
extraction, since only a plain `assignment` node's `right.named_children` is
scanned. Splitting `urlpatterns` across an initial list plus one or more
`+=` extensions (e.g. a separate section of API routes, or debug-only
patterns) is an ordinary, documented Django organizing pattern - every route
declared this way is missing from the endpoint inventory with no indication
anything was skipped.

**Fix:** also match `n.type == "augmented_assignment"` with the same
`is_urlpatterns`/`right.type == "list"` check - the `left`/`right` field
names are the same for both node types in this grammar.

---

### 28. PHP grouped `use` imports are silently dropped entirely

**`src/aletheore/scanner/graph.py:1531-1536`**. **Severity: medium.**

```python
elif n.type == "namespace_use_declaration":
    for clause in n.children:
        if clause.type == "namespace_use_clause":
            for grandchild in clause.children:
                if grandchild.type in ("qualified_name", "name"):
                    imports.append(("use", text(grandchild)))
```

Confirmed by reading the block directly: it only looks at
`namespace_use_declaration`'s *direct* children. For
`use Foo\Bar\{ClassA, ClassB as B};`, tree-sitter-php nests the individual
clauses inside a `namespace_use_group` with the shared prefix as a sibling -
neither is a direct `namespace_use_clause` child, so the loop finds nothing,
and the walk's generic traversal has no other branch matching the nested
clauses either. The entire grouped statement is dropped with no signal
anywhere. Grouped `use` is valid PHP 7+ syntax; real-world frequency varies
by codebase formatting conventions (PHP-CS-Fixer often expands it back to
individual lines), but it's common enough that silently zeroing it out is a
real gap.

**Fix:** walk into `namespace_use_group`'s clauses too, combining each with
the declaration's own prefix - the same way the Rust `scoped_use_list`
branch already combines prefix + list for the analogous Rust syntax.

---

### 29. Java static wildcard import (`import static Foo.*;`) never resolves, even locally

**`src/aletheore/scanner/graph.py:1315-1329`**. **Severity: medium.**

```python
if is_wildcard:
    for root in source_roots:
        package_dir = root.joinpath(*segments)
        if package_dir.is_dir():
            return sorted(package_dir.glob("*.java"))
    return []

if is_static:
    ...
```

Confirmed the `is_wildcard` branch runs before `is_static` is checked. For
`import static a.b.C.*;`, extraction correctly produces
`("a.b.C", is_static=True, is_wildcard=True)`, but this branch treats every
segment (including the trailing class name `C`) as a package-directory
component, looking for a directory `a/b/C/` that doesn't exist - the real
file is `a/b/C.java`. A static wildcard's last segment is a class name, not
a package path component, so this always returns `[]` even when the target
is local to the repo. `import static X.*;` patterns are common in test code,
but those targets are typically external libraries anyway (unresolvable
regardless); the bug specifically bites internal constants/matcher-factory
classes, narrower in practice but a real, unconditional miss when it occurs.

**Fix:** check `is_static` before `is_wildcard`, and have static-wildcard
reuse the `is_static` branch's `segments[:-1]` + class-file lookup instead of
the plain-wildcard package-directory lookup.

---

### 30. Advisory-lock namespace 3 is independently reused for two unrelated locks

**`github-app/app_server/db.py:497`** (`SEAT_LOCK_NAMESPACE = 3`) and
**`github-app/scan_worker/db.py:27`** (`REPO_CHECKOUT_LOCK_NAMESPACE = 3`),
added two days apart. **Severity: low-medium** - real design defect, low
practical trigger probability.

Both files document that the advisory-lock keyspace is shared and namespace
values must be coordinated between them ("Keep this namespace value
identical in both files"). Namespace 1
(`SCAN_SLOT_LOCK_NAMESPACE`) is correctly kept identical in both, but
namespace 3 was independently claimed twice for unrelated purposes -
confirmed by grepping both files for `LOCK_NAMESPACE =`. Postgres advisory
locks with two int32 args key on the literal `(namespace, key)` pair
regardless of which function took them, so `pg_advisory_lock(3, hashtext(...))`
in scan-worker and `pg_advisory_xact_lock(3, installation_id)` in app-server
genuinely contend for the same lock whenever `hashtext(f"{installation_id}:{repo}")`
happens to equal a real installation id.

**Concrete failure scenario:** `repo_checkout_lock` is deliberately blocking
with no timeout and can be held for as long as a checkout+scan takes. If its
hashed key collides with another installation's numeric id, that
installation's seat-admission calls (which use a 5s `lock_timeout`) would
block for up to 5 seconds and then fail, surfacing as an inexplicable,
intermittent failure to add a team member with no discoverable connection to
the actual cause. A genuine 32-bit-hash-collision risk, not a certainty, but
not negligible as the product's number of `(installation, repo)` pairs
grows.

**Fix:** give one of the two its own unused namespace value (4), and add a
short cross-referencing comment in each file listing every namespace value
claimed in the other.

---

### 31. PHP aliased `use X as Y;` also emits a phantom import of the bare alias

**`src/aletheore/scanner/graph.py:1531-1536`** (same block as #28).
**Severity: low-medium.**

For `use Foo\Bar as Baz;`, `namespace_use_clause`'s children include both the
path (`qualified_name`) and the alias target, which has the same node
**type** (`"name"`) as a bare unaliased `use Foo;`'s import target - so the
existing loop (see #28's code block) matches both and appends two entries:
the real path and the bare alias string. Most aliased imports target
external namespaces that won't collide with a project's own PSR-4 prefixes,
so the phantom entry is usually harmless noise that fails to resolve - but a
short, common alias could coincide with a real local class name.

**Fix:** only take the clause's first matching child (the real path), or
explicitly exclude the node reachable via `clause.child_by_field_name("alias")`.

---

### 32. TypeScript `import foo = require('./foo');` produces zero imports

**`src/aletheore/scanner/graph.py:662-666`**. **Severity: low-medium.**

```python
if n.type == "import_statement":
    source_node = n.child_by_field_name("source")
    if source_node is not None:
        raw = source[source_node.start_byte:source_node.end_byte].decode()
        imports.append(raw.strip("'\""))
```

Import-equals (`import X = require('./y')`) is still an `import_statement`
node, but has no `"source"` field - the path lives inside a nested
`import_require_clause` two levels down. `child_by_field_name("source")`
returns `None`, so nothing is appended; this is a declaration form, distinct
from (and not caught by) the separate `call_expression`-based `require()`
handling elsewhere in the same function. Confirmed present in real code (3
occurrences in the `axios` repo).

**Fix:** add a branch checking for an `import_require_clause` child and
pulling the path string out of its own arguments, the same way `require()`
calls are handled.

---

### 33. Rust nested `use` groups drop everything below the first brace level

**`src/aletheore/scanner/graph.py:932-940`** (`_rust_use_paths`,
`scoped_use_list` branch). **Severity: low-medium.**

```python
if node.type == "scoped_use_list":
    prefix_node = node.children[0]
    list_node = node.children[-1]
    prefix = text(prefix_node)
    names = [text(c) for c in list_node.children if c.type in ("identifier", "self")]
    return [f"{prefix}::{name}" for name in names]
```

For `use std::{fmt, io::{self, Write}};`, the outer list's children are
`fmt` (an `identifier`) and `io::{self, Write}` (itself a nested
`scoped_use_list`). The comprehension only keeps `identifier`/`self`
children - confirmed by reading the function - so the nested
`scoped_use_list` doesn't match either type and is dropped along with
everything inside it (`io` and `io::Write` both vanish). Nested `use`
grouping is valid, idiomatic Rust that `rustfmt` produces by default in many
configurations.

**Fix:** recurse when a child is itself a `use_as_clause`/`scoped_use_list`/
`use_list`, prefixing the recursive results with the outer `prefix::`,
instead of only accepting leaf children.

---

### 34. Ownership-check comment claims an ordering the code doesn't have - a repo-existence oracle

**`github-app/app_server/dashboard.py:185-197`**. **Severity: low** - minor
information disclosure (tenant existence, not data), requires only a
self-service GitHub OAuth login.

```python
async def _require_dashboard_installation(request, org, repo):
    # Session + ownership check first, before any repo lookup - ...
    session = await get_current_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="login required")

    installation_id = await _repo_installation_id(pool, org, repo)          # repo lookup
    administered_ids = await _administered_installation_ids_for_session_or_401(pool, session)
    if installation_id not in administered_ids:                             # ownership check
        raise HTTPException(status_code=403, detail="you do not administer this installation")
```

The comment claims "Session + ownership check first, before any repo
lookup"; the code only does *session* first - the repo lookup runs before
the ownership check, confirmed by reading the function directly. Any
authenticated user (anyone who has completed the app's GitHub OAuth login,
not necessarily an admin of the target org) gets a status code
(`404` "no such repo" vs `403` "you do not administer this installation")
that reveals whether an arbitrary org/repo has ever been scanned by this
product, before ownership is checked. The codebase treats exactly this
pattern as worth engineering around elsewhere (`managed_audit_api.py`'s
job-status route deliberately returns 404 instead of 403 "so it is not an
existence oracle") - here the comment claims that care was applied but the
code doesn't do what the comment says. The same ordering also exists in
`admin.py:409-427`'s `_require_authorized_installation`, so it's a
repo-wide pattern, not unique to this function.

**Fix:** either reorder (not fully possible, since `_repo_installation_id`
is what maps org/repo to an installation_id in the first place), or correct
the comment to state the real, narrower guarantee it actually provides.

---

### 35. Python and JS/TS don't filter self-import edges the way every other language does

**`src/aletheore/scanner/graph.py:2330-2335`** (Python) and **`:2453-2458`**
(JS/TS), vs. Go (`:2358-2363`), Rust, Java, Ruby, PHP, C/C++, C# - all of
which explicitly skip an edge where `target == rel_path`. **Severity: low.**

Confirmed by reading each language branch in the main loop: seven of the
nine explicitly guard against a file importing itself
(`if target == rel_path: continue`); Python's and JS/TS's resolution loops
have no equivalent check. This matters because `dead_code.py:172` uses
`if not module.get("imported_by", [])` as its unreachable-file test - a
Python or JS/TS file that happens to statically self-import (an absolute
`import pkg.mod` from inside `pkg/mod.py` itself, or a JS file
`require`-ing its own path) gets a non-empty `imported_by` purely from
itself, and silently escapes dead-code detection even if genuinely unused
by everything else - the exact false-negative class the other seven
languages' filter exists to prevent. A narrow-trigger case (self-import is
uncommon in idiomatic code), but a genuine cross-language inconsistency
against a convention already applied seven other times in the same
function.

**Fix:** add the same `if target == rel_path: continue` (or equivalent) to
the Python and JS/TS resolution loops.
