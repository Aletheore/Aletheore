export type ChangelogTag = "security" | "fix" | "feature" | "perf";

export type ChangelogEntry = {
  date: string; // ISO yyyy-mm-dd, the date it shipped to production
  title: string;
  tags: ChangelogTag[];
  body: string;
  /** For security entries: how long from us learning about it to a fix being live. */
  responseTime?: string;
  link?: { label: string; href: string };
};

/**
 * Customer-facing changes only - not an engineering commit log. Scope is
 * deliberately narrow: did this change what a customer's data, access, or
 * service experienced. Internal-only matters (refactors, flaky-test fixes,
 * internal audit tooling, dependency bumps with no visible effect) don't
 * belong here even if they happened on the same repo.
 *
 * A security entry is only added once the fix is live in production, never
 * while a vulnerability is still open - see /security for the policy this
 * follows.
 *
 * Curated milestones, not an exhaustive commit log: every entry traces to a
 * real, verified merged PR (read via git log/git show, not paraphrased from
 * memory), covering this project's full history from 2026-07-26 through
 * 2026-10-21. Entries a real customer wouldn't notice or care about (an
 * internal refactor, a flaky test fix, an internal audit tool) were left
 * out on purpose, even when the underlying commit was itself real.
 */
export const changelogEntries: ChangelogEntry[] = [
  {
    date: "2026-10-21",
    title:
      "Hardened member removal, the dashboard, account deletion, and refund handling after a security review",
    tags: ["security", "fix"],
    body: "An independent security review of our own code turned up several gaps, and the fixes for the ones in the hosted app are now live. Removing a member from an installation now also revokes the API tokens that member created; before, those tokens stayed usable until someone revoked them by hand. Finding line numbers in the dashboard are now escaped the same way file paths already were, closing a stored cross-site-scripting path. The account-deletion confirmation code is no longer written to the server logs if the email provider is ever missing in production; the request now fails with an error instead. And when a refund is raised but not yet approved, an affiliate's commission is no longer reversed early; it is reversed when the refund is approved.",
    link: { label: "Our security practices", href: "/security" },
  },
  {
    date: "2026-10-21",
    title: "Tightened request handling on public endpoints against abuse",
    tags: ["security", "fix"],
    body: "Three API endpoints (runtime events, embeddings, and managed audit) used to parse and validate a request body before checking who was calling; they now authenticate first, so an unauthenticated caller can no longer make the server do that work. The GitHub and Paddle webhook endpoints now reject oversized request bodies before reading them into memory. The comment command that triggers an audit from a pull request is now rate limited per commenter, so one account spamming it cannot use up the allowance of the maintainers who really use it. The server also now refuses to start with a session secret shorter than 32 characters.",
    link: { label: "Our security practices", href: "/security" },
  },
  {
    date: "2026-10-14",
    title: "Flash Review no longer runs on dependency-bot and draft pull requests",
    tags: ["feature"],
    body: "Flash Review is no longer queued for pull requests opened by Dependabot, Renovate, pre-commit-ci or Snyk, for draft pull requests, for titles containing [skip review], [no review] or [skip aletheore], or for the labels skip-review and no-review. Before this, every dependency version bump triggered a review charged to your credit. The deterministic scan still runs on all of them. Marking a draft ready for review queues one review that covers everything since the last reviewed commit, and the aletheore-review label forces a review of any pull request.",
    link: { label: "PR #1038 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/1038" },
  },
  {
    date: "2026-10-14",
    title: "aletheore index now tells you when it uploads source code",
    tags: ["fix"],
    body: "Saving an Aletheore token quietly switches aletheore index from the local embedder to our hosted embedding service, the one place the CLI sends source code anywhere. The CLI now says so: when you log in or enter a token, and once per run on stderr when an index build actually uploads code chunks, however few there are. Chunks are embedded and returned, not stored. Searching an index sends only your question and stays silent. Released in 0.9.27, which also makes the hosted service return 1024-dimension vectors, so run aletheore index once to rebuild an index you built with a token earlier.",
    link: { label: "PR #1024 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/1024" },
  },
  {
    date: "2026-10-13",
    title: "Hosted code embeddings now run on DeepInfra, and the privacy policy says so",
    tags: ["feature", "fix"],
    body: "The embeddings behind Flash Review's similar-diff cache, AIRview's caches and the hosted search index now come from DeepInfra (Qwen3-Embedding-8B, 1024 dimensions) instead of our own Jina service. Embedding use is metered against your credit. Because the vector size changed, the Flash and AIRview caches started cold and AIR users' local search indexes rebuild once. The privacy policy and developers page were corrected in the same change: they used to say the CLI never uploads source code, which is true of a scan but not of aletheore index when logged in, and they now name DeepInfra as the embedding provider.",
    link: { label: "What the privacy policy says today", href: "/privacy" },
  },
  {
    date: "2026-10-13",
    title: "Credit top-up minimum is now $6, and two Flash Review accuracy fixes",
    tags: ["fix"],
    body: "The dashboard top-up form still accepted $5 after the payment price's minimum was raised to $6, because the $0.15 service charge at that size no longer covers the payment processor's fee. The form, its hint text and its click-time check now all use $6. Separately, when Flash Review reuses a cached review for a similar diff, it now drops a finding whose quoted code has disappeared, which usually means the bug was fixed, instead of posting it again. The shared-state check also now recognizes work handed to threads through threading.Thread, run_in_executor and to_thread.",
    link: { label: "PR #1019 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/1019" },
  },
  {
    date: "2026-10-10",
    title:
      "Flash Review keeps more accurate findings, and the exception check stops flagging prose",
    tags: ["fix"],
    body: 'Flash Review used to drop a finding if the code it quoted was not within 8 lines of the line it cited. On 13 real pull requests that dropped 39 of 220 proposed findings, 32 of them accurate. A finding whose quote sits elsewhere in the file is now moved to the right line instead of dropped, and recall on that corpus rose from about 50% to 59%. The Python exception-mismatch check also no longer reads prose like "can raise an error" as an exception name, understands dotted and tuple handlers, and treats a bare Exception handler as covering everything: 4 of its 8 findings in that run were false positives, and none were afterwards.',
    link: { label: "PR #1011 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/1011" },
  },
  {
    date: "2026-10-10",
    title:
      "Fixed annual AIR subscribers seeing the wrong credit figure, and a pricing fallback that could overcharge",
    tags: ["fix"],
    body: "Annual AIR subscribers now see their annual credit figure on the dashboard and admin page instead of the monthly one. We also fixed how a model missing from our price table would have been billed: it fell back to the table's highest rate, about 200 times what a Flash Review actually costs, charged against your real credit balance. We found no case where this happened to a customer. It now falls back to the table's median and raises an error on our side so we notice and add the model.",
    link: { label: "PR #1015 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/1015" },
  },
  {
    date: "2026-10-10",
    title: "Hardened payment webhook handling against crashes and retried deliveries",
    tags: ["fix"],
    body: "A payment-provider webhook with an absurdly large timestamp header used to crash the handler before its signature was checked; it is now rejected cleanly. A genuine webhook could also be refused if its timestamp had formatting such as a leading zero, because we hashed a normalized copy instead of the exact header value that was signed. Our check of the payment provider's allowed IP list no longer treats an empty or badly stale list as valid, and handles IPv4-mapped addresses and trailing commas in forwarded headers. Separately, a retried delivery arriving within the provider's three-day retry window could re-run a one-time Live Wiki build and spend LLM credit twice; that duplicate is now prevented.",
    link: { label: "PR #1012 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/1012" },
  },
  {
    date: "2026-10-09",
    title: "Fixed a packaging bug that could silently skip some scanner findings",
    tags: ["fix"],
    body:
      "The Semgrep rules and the Joern query used by our static-analysis stage weren't being packaged correctly in a recent release, which meant some of their findings could be silently skipped rather than reported. Fixed so both are packaged and run as intended. Hosted Semgrep stays off by default until we've measured its real signal-to-noise on top of what we already run.",
  },
  {
    date: "2026-10-08",
    title: "Faster scans on large repositories, and three scanner bugs fixed",
    tags: ["perf", "fix"],
    body:
      "Scanning large repos is now meaningfully faster: on the Linux kernel (1.46M commits, ~95k files), a cold scan dropped from roughly 12+ minutes to about 5 minutes, and a warm re-scan to about 2.5 minutes, mainly from overlapping git/network/parsing work that used to run sequentially and caching more per-file state between scans. Three real bugs were found and fixed along the way: a quadratic slowdown in error handling on repos with many parse errors, a parser-cache collision that applied the wrong language grammar to .ts and .tsx files interchangeably once scanning ran in parallel (nondeterministic, wrong results on some files), and a history-depth setting that wasn't persisting correctly on warm scans.",
  },
  {
    date: "2026-10-03",
    title: "Fixed AIRview and Docs occasionally dropping entire outputs",
    tags: ["fix"],
    body:
      "A formatting quirk in the underlying model's output could cause AIRview and Docs to silently drop an entire batch of generated descriptions when one response came back malformed. The model is now asked to return valid JSON directly, significantly reducing these failures.",
    link: { label: "PR #907 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/907" },
  },
  {
    date: "2026-10-02",
    title: "Removing a repo from an installation now deletes its retained checkout, and the privacy policy was corrected to match",
    tags: ["security", "fix"],
    responseTime: "same day",
    body:
      "An internal audit found that deselecting a single repository from an installation (while keeping the rest) only hid it from the dashboard - the working copy of that repo's source code, kept on disk between scans for incremental re-scanning, was not being deleted. We fixed the deletion path the same day: removing a repo now purges its on-disk checkout immediately, not just on a full uninstall. The audit also found our privacy policy under-described one feature: Docs sync (AIRview Docs) sends the source of individual public functions and classes to write descriptions, not only \"derived evidence\" as a prior version of the policy implied. The policy text was corrected to state this plainly rather than changing what the feature does.",
    link: { label: "What the privacy policy says today", href: "/privacy" },
  },
  {
    date: "2026-10-02",
    title: "Fixed a billing bug where a refund could silently convert purchased credit into plan credit",
    tags: ["fix"],
    body:
      "If a reserved charge came out of purchased top-up credit (because the plan's included credit was already used up) and that charge was later refunded, the refund was going back into the plan's credit bucket instead of the top-up balance it actually came from - meaning credit a customer paid for could quietly turn into credit that resets at renewal instead of staying theirs. Fixed so a refund returns to the balance it was drawn from. A separate bug in the same pass: free-tier installs were being held to a per-repo review cap meant only for paid plans; that cap is now paid-only, as intended.",
  },
  {
    date: "2026-10-02",
    title: "Fixed credit top-ups and affiliate commissions priced in the wrong currency",
    tags: ["fix"],
    body:
      "Checkout can localize prices into a customer's own currency, which was throwing off how much credit a top-up granted and how affiliate commissions were calculated. Both are now priced correctly in USD regardless of checkout currency.",
    link: { label: "PR #893 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/893" },
  },
  {
    date: "2026-10-02",
    title: "Affiliate program is now AIR-only, with updated commission and annual pricing",
    tags: ["feature"],
    body:
      "Affiliate referrals and commissions now apply only to the AIR plan, and commission is calculated on actual earnings after fees rather than the tax-inclusive total. The annual AIR plan's included credit was also adjusted to reflect its two free months.",
    link: { label: "PR #897 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/897" },
  },
  {
    date: "2026-10-02",
    title: "Flash plan price updated; credit top-ups now carry a service charge",
    tags: ["feature"],
    body:
      "The Flash plan is now $10/month, and credit top-ups carry a service charge of $1.15 per $1.00 of credit purchased, plus tax. You still receive exactly the credit you pay for.",
    link: { label: "PR #898 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/898" },
  },
  {
    date: "2026-10-02",
    title: "Fixed delayed credit top-ups not being credited",
    tags: ["fix"],
    body:
      "A top-up delivered late by our payment provider, for example after a retry following downtime, could fail to credit your account at all. Late top-ups are now credited correctly within the provider's full retry window, and any top-up that still can't be matched now raises an alert instead of silently disappearing.",
    link: { label: "PR #902 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/902" },
  },
  {
    date: "2026-10-02",
    title: "Fixed credit balance and checkout confirmation not reflecting a purchase",
    tags: ["fix"],
    body:
      "The main credit balance used to show only your plan's included credit, so a top-up could look like it hadn't arrived. It now shows your full available credit, and checkout now confirms your payment was received and updates the balance in view.",
    link: { label: "PR #904 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/904" },
  },
  {
    date: "2026-10-02",
    title: "New Usage & credit page in the AIR dashboard",
    tags: ["feature"],
    body:
      "AIR customers now have a dedicated Usage & credit page in the dashboard showing balance, top-ups, and review history, using the same shared experience Flash customers already have.",
    link: { label: "PR #905 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/905" },
  },
  {
    date: "2026-10-01",
    title: "Hardened the deterministic scan pipeline against several gaps",
    tags: ["fix"],
    body:
      "Fixed cases where a scanner failing to run could look identical to a clean scan, where a plain file rename could be mislabeled as both a new and a resolved finding, and re-enabled a Java resource-leak check that had been too broadly silenced, so issues it would have caught are no longer missed.",
    link: { label: "PR #889 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/889" },
  },
  {
    date: "2026-09-30",
    title: "Pull-request comments now include a visual change diagram",
    tags: ["feature"],
    body:
      "Aletheore's evidence comment on a pull request now includes a diagram showing which changed files have dependents elsewhere in the repo, and which of those have new findings, instead of only a text list.",
    link: { label: "PR #881 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/881" },
  },
  {
    date: "2026-09-28",
    title: "Fixed a cross-account access issue in installation permission checks",
    tags: ["security", "fix"],
    responseTime: "about 1 day from report to fix merged, fix deployed the same day",
    body:
      "A user reported that signing in with their own, unrelated GitHub account could show another installation's private repository list and billing/checkout target. Root cause: GitHub's own installations API returns every installation where an account has read access to even one repository, including through a public repo with no invitation - and that broad set was being used directly as \"this account administers this installation\" in several places with no further per-account check. A full sweep of the affected code found six endpoints with the same gap, the most severe being one that could mint a usable API credential for someone else's installation. All six were fixed with a shared, correct permission check, each with its own regression test, and the fix was deployed to production the same day it was confirmed.",
    link: { label: "Our security practices", href: "/security" },
  },
  {
    date: "2026-09-24",
    title: "Flash customers can now buy more AI review credit directly",
    tags: ["feature"],
    body:
      "Flash plan customers who ran out of AI credit previously had no way to buy more without upgrading. A new standalone credit page lets Flash customers see their balance and purchase a top-up directly.",
    link: { label: "PR #800 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/800" },
  },
  {
    date: "2026-09-24",
    title: "Updated privacy policy to name all free-tier review providers",
    tags: ["fix"],
    body:
      "Our privacy policy didn't fully disclose which third-party providers process code for free-tier pull-request reviews. It now names every provider in the free-tier review path, matching what was already disclosed for paid plans.",
    link: { label: "PR #811 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/811" },
  },
  {
    date: "2026-09-23",
    title: "Fixed several scanners silently not running in production",
    tags: ["fix"],
    body:
      "A few of our automated scanners had never actually been installed in the production scanning environment, so some pull-request scans were missing their findings with no visible indication anything was wrong. All scanners are now verified to run correctly on every scan.",
    link: { label: "PR #781 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/781" },
  },
  {
    date: "2026-09-23",
    title: "Fixed a denial-of-service vulnerability in license detection",
    tags: ["security", "fix"],
    body:
      "A specially crafted project file could make a scan hang for several minutes, due to a vulnerable pattern in our license-detection code. This was reported to us responsibly through private disclosure and has been fixed; the same files now process in milliseconds.",
    link: { label: "PR #782 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/782" },
  },
  {
    date: "2026-09-22",
    title: "Added always-on secret and misconfiguration scanning",
    tags: ["feature", "security"],
    body:
      "Every scan and pull-request review now also runs a dedicated secret and misconfiguration scanner, catching things like exposed API keys and risky Dockerfile settings that earlier checks could miss. This runs automatically, with no setup required.",
    link: { label: "PR #763 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/763" },
  },
  {
    date: "2026-09-22",
    title: "New deterministic security check available on every plan",
    tags: ["feature"],
    body:
      "Pull requests now get a dedicated \"Aletheore Deterministic Scan\" check combining findings from several scanners into one check run. Unlike most other checks, it's available on every plan, including free.",
    link: { label: "PR #764 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/764" },
  },
  {
    date: "2026-09-22",
    title: "Added always-on Java code-quality scanning",
    tags: ["feature"],
    body:
      "Scans and pull-request reviews now also run a Java code-quality scanner, surfacing real bug-shaped issues in Java code automatically, with known-noisy rules filtered out so the signal stays useful.",
    link: { label: "PR #772 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/772" },
  },
  {
    date: "2026-09-21",
    title: "Fixed being billed after deleting your account",
    tags: ["fix"],
    body:
      "Deleting your Aletheore account removed your local data but could leave the underlying subscription active, so you kept being charged with no installation left to manage it from. Account deletion now also cancels the subscription itself.",
    link: { label: "PR #750 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/750" },
  },
  {
    date: "2026-09-21",
    title: "Flash Review now reviews each changed file more thoroughly",
    tags: ["feature"],
    body:
      "Flash Review used to cap how many issues it would call out across an entire pull request, so multi-file PRs with several real bugs often only got one or two surfaced. It now evaluates each changed file in its own pass, so more genuine issues get caught in PRs that touch several files.",
    link: { label: "PR #762 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/762" },
  },
  {
    date: "2026-09-18",
    title: "Fixed duplicate seat purchases from double-clicking",
    tags: ["fix"],
    body:
      "Clicking the buy-seat or remove-seat button twice in quick succession could trigger two separate billing requests, resulting in being charged for (or losing) two seats instead of one. These buttons, along with the API-token generation button, now disable themselves while a request is in progress.",
    link: { label: "PR #741 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/741" },
  },
  {
    date: "2026-09-18",
    title: "Fixed account deletion leaving some customer data behind",
    tags: ["fix", "security"],
    body:
      "Deleting an installation could, in some cases, leave a customer's email address behind in our records, or fail outright with an error instead of completing. Account deletion now fully removes this data every time.",
    link: { label: "PR #742 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/742" },
  },
  {
    date: "2026-09-13",
    title: "Fixed a denial-of-service risk in CODEOWNERS parsing",
    tags: ["security", "fix"],
    body:
      "A specially crafted CODEOWNERS file in a scanned repository could make ownership resolution hang for a long time on a single pattern. This has been fixed so ownership lookups stay fast regardless of how a repo's CODEOWNERS file is written.",
    link: { label: "PR #696 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/696" },
  },
  {
    date: "2026-09-13",
    title: "Smoother hovering and panning on large dependency graphs",
    tags: ["perf", "fix"],
    body:
      "Hovering over nodes or panning around the dashboard's dependency and cluster graphs used to feel laggy on big repositories. Both interactions are now much faster and stay responsive even on repos with tens of thousands of modules.",
    link: { label: "PR #699 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/699" },
  },
  {
    date: "2026-09-13",
    title: "Flash Review suggestions now show as one-click GitHub fixes",
    tags: ["feature"],
    body:
      "When Flash Review is confident in a fix, it now renders a real GitHub suggested-change block you can apply with one click, instead of just describing the fix in prose. A second model double-checks each suggestion for correctness before it's allowed to render as clickable, and anything less certain still falls back to a plain description.",
    link: { label: "PR #707 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/707" },
  },
  {
    date: "2026-09-11",
    title: "Fixed a billing bug that could reset credit to a stale amount",
    tags: ["fix"],
    body:
      "A delayed billing webhook arriving out of order could wipe a customer's current credit balance back to an old period's allotment. Billing updates now only apply when they're actually newer than what's on file.",
    link: { label: "PR #656 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/656" },
  },
  {
    date: "2026-09-11",
    title: "New dashboard cards for licenses, vulnerabilities, and API endpoints",
    tags: ["feature"],
    body:
      "The local dashboard now shows dedicated cards for dependency licenses, vulnerability findings, and mapped API endpoints, instead of just a bare vulnerability count. These cards also clearly note when a scan skipped one of these checks, rather than showing a misleading zero.",
    link: { label: "PR #661 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/661" },
  },
  {
    date: "2026-09-11",
    title: "Dependency graphs no longer freeze on large repositories",
    tags: ["perf"],
    body:
      "The dashboard's dependency and cluster graphs could hang the browser tab indefinitely on very large codebases. They now use a much faster layout algorithm, so even repos with tens of thousands of files render in under two minutes with the page staying responsive.",
    link: { label: "PR #662 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/662" },
  },
  {
    date: "2026-09-10",
    title: "Switched to transparent dollar-credit pricing",
    tags: ["feature"],
    body:
      "Instead of an estimated review-count limit, your plan now shows a real dollar credit balance that matches what's actually enforced. You can top up credit that never expires, and your balance, a buy-more option, and low-balance alerts are all visible from the dashboard.",
    link: { label: "PR #646 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/646" },
  },
  {
    date: "2026-09-10",
    title: "Fixed a stuck checkout status on the settings page",
    tags: ["fix"],
    body:
      "Buying credit could leave the checkout status stuck on \"Opening checkout...\" even after it had finished or failed. It now updates correctly on success, close, or error.",
    link: { label: "PR #651 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/651" },
  },
  {
    date: "2026-09-09",
    title: "Corrected stale pricing and an inaccurate model claim on our site",
    tags: ["fix"],
    body:
      "Our site listed an outdated Flash plan price and incorrectly claimed AIRview and managed audits were written by an OpenAI model. Both have been corrected.",
    link: { label: "PR #642 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/642" },
  },
  {
    date: "2026-09-08",
    title: "Choose which endpoints get monitored past the default cap",
    tags: ["feature"],
    body:
      "Endpoint health monitoring previously checked only the first 64 endpoints found in a repo with no indication anything was being skipped. The dashboard now tells you when that cap is hit, and you can choose which endpoints to monitor beyond it.",
    link: { label: "PR #590 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/590" },
  },
  {
    date: "2026-09-08",
    title: "Dead-code detection no longer flags real entry points in compiled languages",
    tags: ["fix"],
    body:
      "Go, Rust, Java, and C# programs could have their actual entry points incorrectly flagged as dead code. That's fixed.",
    link: { label: "PR #594 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/594" },
  },
  {
    date: "2026-09-08",
    title: "Secret scanner now catches SECRET_KEY and *_TOKEN assignments",
    tags: ["security", "fix"],
    body:
      "One of the most common ways credentials show up in code, direct SECRET_KEY and *_TOKEN variable assignments, was being missed by the secret scanner. It's now detected.",
    link: { label: "PR #595 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/595" },
  },
  {
    date: "2026-09-08",
    title: "Flash Review stopped flagging false positives on Python class headers",
    tags: ["fix"],
    body:
      "A scope check meant to catch real mismatches was instead firing a confusing, incorrect warning on nearly every Python class definition it reviewed. That's fixed.",
    link: { label: "PR #596 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/596" },
  },
  {
    date: "2026-09-08",
    title: "Maven dependency scanning now works without a declared namespace",
    tags: ["security", "fix"],
    body:
      "A pom.xml file that didn't declare its Maven XML namespace, a common and valid way to write one, was invisible to vulnerability scanning entirely. Those dependencies are now correctly scanned.",
    link: { label: "PR #599 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/599" },
  },
  {
    date: "2026-09-08",
    title: "Fixed a billing webhook crash on malformed seat data",
    tags: ["fix"],
    body:
      "A Paddle webhook with an unexpected seat-quantity value could crash permanently, meaning a plan or seat change wouldn't apply until we stepped in manually. It now handles malformed values safely.",
    link: { label: "PR #609 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/609" },
  },
  {
    date: "2026-09-08",
    title: "ChatOps commands no longer trigger on ordinary words",
    tags: ["fix"],
    body:
      "Typing a normal sentence starting with words like \"auditing\" or \"dismissed\" in a PR comment could accidentally trigger a real audit job or dismiss a finding. Commands now require an exact match.",
    link: { label: "PR #610 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/610" },
  },
  {
    date: "2026-09-08",
    title: "Fixed health checks hiding real outages across multiple targets",
    tags: ["fix"],
    body:
      "If you monitored the same endpoint across multiple targets, like staging and production, their results could get blended together, masking a real production outage behind a healthy staging result. Each target's results are now tracked separately.",
    link: { label: "PR #628 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/628" },
  },
  {
    date: "2026-09-08",
    title: "Fixed a crash in the dashboard's History tab",
    tags: ["fix"],
    body:
      "One older snapshot with missing data could crash the entire History tab for a session. A bad snapshot is now skipped instead of breaking the whole view.",
    link: { label: "PR #631 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/631" },
  },
  {
    date: "2026-09-07",
    title: "Local embeddings can now set themselves up automatically",
    tags: ["feature"],
    body:
      "If you choose local embeddings, Aletheore can now install and start Ollama for you, with your confirmation, instead of requiring you to do it by hand first.",
    link: { label: "PR #561 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/561" },
  },
  {
    date: "2026-09-07",
    title: "Flash Review now handles larger files",
    tags: ["feature"],
    body:
      "The per-file size limit for Flash Review context was raised from 80KB to 100KB, so fewer large files get skipped during a review. The monthly spend cap for the Flash tier was raised to $6 to match.",
    link: { label: "PR #563 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/563" },
  },
  {
    date: "2026-09-06",
    title: "Flash Review now fully respects your ignored_paths setting",
    tags: ["fix"],
    body:
      "Files listed under ignored_paths in .aletheore.json could still show up in some Flash Review checks. That setting is now honored everywhere it should be.",
    link: { label: "PR #554 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/554" },
  },
  {
    date: "2026-09-05",
    title: "Database schema mapping is now free for everyone",
    tags: ["feature"],
    body:
      "Schema mapping was previously limited to paid plans. It's now available on every plan, on the same opt-out basis as our other scanners.",
    link: { label: "PR #543 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/543" },
  },
  {
    date: "2026-09-04",
    title: "Unused-dependency detection was flagging everything as unused",
    tags: ["fix"],
    body:
      "A bug meant every real, in-use dependency in a scanned repo could be reported as unused. This is now fixed and verified against real projects.",
    link: { label: "PR #529 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/529" },
  },
  {
    date: "2026-09-04",
    title: "Schema mapping now understands Django, Rails, and Alembic migrations",
    tags: ["feature"],
    body:
      "Database schema mapping previously came back empty for repos using Django, Rails, or Alembic migrations instead of raw SQL files. It now reads those migration files directly and builds a real schema model from them.",
    link: { label: "PR #539 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/539" },
  },
  {
    date: "2026-09-03",
    title: "Watch mode was silently dropping security findings",
    tags: ["fix"],
    body:
      "Running the scanner in watch mode could silently lose vulnerability, license, and secret-history findings after the very first rebuild. Those findings are now correctly carried forward on every incremental update.",
    link: { label: "PR #522 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/522" },
  },
  {
    date: "2026-09-03",
    title: "Closed six real gaps in secret detection",
    tags: ["security", "fix"],
    body:
      "A new benchmark run against real open-source repositories uncovered several ways a committed secret could slip past detection, including test TLS keys, bare credential references, and vendored API examples. All six are now caught.",
    link: { label: "PR #527 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/527" },
  },
  {
    date: "2026-09-02",
    title: "Search code by shape, not just keywords",
    tags: ["feature"],
    body:
      "You can now search a codebase using structural AST patterns instead of plain text, across all supported languages. This finds matches a keyword or regex search would miss entirely.",
    link: { label: "PR #511 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/511" },
  },
  {
    date: "2026-09-01",
    title: "New tool: trace a change's full blast radius",
    tags: ["feature"],
    body:
      "Added a blast-radius tool for coding agents and the CLI that shows everything that depends on a file, both directly and transitively, and which of those dependents actually call a given function. It's a faster way to understand the real impact of a change before making it.",
    link: { label: "PR #509 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/509" },
  },
  {
    date: "2026-08-31",
    title: "Added Swift support to the code scanner",
    tags: ["feature"],
    body:
      "The scanner now understands Swift files, including symbol extraction, module-level import resolution, dependency vulnerability scanning, and license checks.",
    link: { label: "PR #480 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/480" },
  },
  {
    date: "2026-08-31",
    title: "Findings now point to the exact function, not just a line",
    tags: ["feature"],
    body:
      "Flash Review comments and CLI secrets findings now include the specific function or class where the issue was found, not just a file and line number. This makes it faster to see exactly what code a finding is talking about.",
    link: { label: "PR #485 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/485" },
  },
  {
    date: "2026-08-31",
    title: "Closed a security gap in webhook alert delivery",
    tags: ["security", "fix"],
    body:
      "We fixed a gap that could let a configured Slack or Teams alert webhook get redirected to an internal address after it had already passed validation. Alert destinations are now re-validated and pinned at the moment an alert is sent, closing that window completely.",
    link: { label: "PR #503 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/503" },
  },
  {
    date: "2026-08-30",
    title: "You can now dismiss a Flash Review finding by replying to it",
    tags: ["feature"],
    body:
      "Flash Review findings now post as individual inline PR comments, and replying with /dismiss tells Aletheore the finding isn't useful so it stops resurfacing on future reviews.",
    link: { label: "PR #467 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/467" },
  },
  {
    date: "2026-08-30",
    title: "Launched the Flash plan with proper billing enforcement",
    tags: ["feature"],
    body:
      "Our lower-cost Flash plan is now fully wired up with live billing: it gets its own review limits and spend cap, and AIR-only features are correctly restricted to the AIR plan instead of being available by accident.",
    link: { label: "PR #468 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/468" },
  },
  {
    date: "2026-08-30",
    title: "Fixed billing and branding issues for Flash plan customers",
    tags: ["fix"],
    body:
      "Flash-plan installations could trigger full AIR-only documentation builds that ate into their spend cap, and payment-failed, cancellation, and weekly digest emails incorrectly showed AIR branding and AIR-only features to Flash customers. Both are now fixed.",
    link: { label: "PR #470 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/470" },
  },
  {
    date: "2026-08-30",
    title: "Flash Review no longer misses large or complex file changes",
    tags: ["fix"],
    body:
      "GitHub omits the diff for some large or complex changed files, and Flash Review was silently skipping those files entirely. It now reconstructs the diff itself so those changes get reviewed too.",
    link: { label: "PR #471 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/471" },
  },
  {
    date: "2026-08-30",
    title: "Added Kotlin support to the code scanner",
    tags: ["feature"],
    body:
      "The scanner now understands Kotlin (.kt and .kts) files, including symbol extraction and import resolution, for use across dependency graphs, dead-code detection, and other scan features.",
    link: { label: "PR #479 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/479" },
  },
  {
    date: "2026-08-29",
    title: "Added two new free automatic checks to PR reviews",
    tags: ["feature"],
    body:
      "Flash Review now includes two additional checks that run at no extra cost: one flags a newly introduced exception handler that silently swallows errors, and the other adds a pass or fail check for known dependency vulnerabilities directly in your PR's checks tab.",
    link: { label: "PR #462 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/462" },
  },
  {
    date: "2026-08-28",
    title: "Removing a repo from the GitHub App now actually stops activity on it",
    tags: ["fix"],
    body:
      "Deselecting a repository in your GitHub App installation previously had no effect: it kept appearing on your dashboard and kept triggering scans and reviews. Removed repos are now hidden and stop being processed, and reselecting them brings them back.",
    link: { label: "PR #437 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/437" },
  },
  {
    date: "2026-08-28",
    title: "Faster, cheaper incremental documentation updates",
    tags: ["perf"],
    body:
      "AIRview's incremental wiki updates now only regenerate the files that actually changed in a push, instead of rewriting every file in an affected area. Unchanged files keep their existing content, so small changes cost much less to update.",
    link: { label: "PR #447 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/447" },
  },
  {
    date: "2026-08-28",
    title: "Made plan-upgrade billing writes atomic",
    tags: ["fix"],
    body:
      "A crash partway through processing a Paddle plan change could leave your installation on a new plan with the wrong seat count or missing billing IDs. Plan changes are now applied as a single all-or-nothing update.",
    link: { label: "PR #449 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/449" },
  },
  {
    date: "2026-08-28",
    title: "Fixed import-tracking bugs across several languages",
    tags: ["fix"],
    body:
      "We fixed a batch of import-resolution bugs affecting Rust re-exports and nested imports, PHP grouped and aliased imports, Java static wildcard imports, TypeScript import-equals syntax, and Django route registration, all of which could leave real dependencies or routes out of your scan results.",
    link: { label: "PR #453 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/453" },
  },
  {
    date: "2026-08-27",
    title: "Fixed unmerged pull requests polluting your repository's history and code graph",
    tags: ["fix"],
    body:
      "Scanning a pull request could accidentally merge that PR's commits and code changes into the data we keep for your default branch, even if the PR was later closed without merging. PR scans no longer affect your main branch's stored history or code graph.",
    link: { label: "PR #406 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/406" },
  },
  {
    date: "2026-08-27",
    title: "Faster scans through parallel file parsing",
    tags: ["perf"],
    body:
      "Scans now parse files across multiple processes instead of one at a time, speeding up the file-parsing stage of a scan on larger repositories.",
    link: { label: "PR #414 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/414" },
  },
  {
    date: "2026-08-27",
    title: "Redesigned the marketing site and dashboard",
    tags: ["feature"],
    body:
      "Both the public site and the hosted dashboard have a new look: a lighter, glass-style theme with translucent panels, replacing the previous dark theme.",
    link: { label: "PR #427 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/427" },
  },
  {
    date: "2026-08-27",
    title: "Updated AIR plan seats and pricing",
    tags: ["feature"],
    body:
      "The AIR plan now includes 3 seats instead of 5, and an additional seat is now $6.99 instead of $4.99. Pricing pages and in-app seat purchasing reflect the new price.",
    link: { label: "PR #435 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/435" },
  },
  {
    date: "2026-08-26",
    title: "Fixed Flash Review sometimes reporting a PR as clean when it never actually ran",
    tags: ["fix"],
    body:
      "If every available model failed partway through a free-tier review, Flash Review could still post 'No issues found' and mark the PR as reviewed, instead of reporting the failure. It now correctly reports that the review did not complete.",
    link: { label: "PR #405 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/405" },
  },
  {
    date: "2026-08-25",
    title: "Clearer path from the free CLI to the GitHub App",
    tags: ["feature"],
    body:
      "After a scan or audit, the CLI now points free users toward installing the GitHub App, which runs free evidence-grounded PR reviews automatically. We also removed outdated 'early access' language from the pricing page.",
    link: { label: "PR #385 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/385" },
  },
  {
    date: "2026-08-25",
    title: "Added email as an alert channel for endpoint monitoring",
    tags: ["feature"],
    body:
      "Endpoint monitoring alerts for downtime, recovery, latency, and shape changes could previously only be delivered to Slack or Teams. You can now also receive these alerts by email.",
    link: { label: "PR #389 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/389" },
  },
  {
    date: "2026-08-25",
    title: "Added Pushover as an alert channel for endpoint monitoring",
    tags: ["feature"],
    body:
      "You can now route endpoint-monitoring alerts to Pushover alongside Slack, Teams, and email. A downtime alert uses Pushover's emergency priority, so it keeps repeating until you acknowledge it.",
    link: { label: "PR #390 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/390" },
  },
  {
    date: "2026-08-25",
    title: "Closed an HTML injection risk in health-alert emails",
    tags: ["security", "fix"],
    body:
      "Health-alert emails built part of their content from information in your repository without escaping it first, which could let a crafted repository inject markup into the email. That content is now safely escaped before the email is sent.",
    link: { label: "PR #393 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/393" },
  },
  {
    date: "2026-08-23",
    title: "Fixed wiki and docs updates silently failing on large repositories",
    tags: ["fix"],
    body:
      "On larger repositories, AIRview's wiki and documentation updates could time out mid-run and disappear with no result and no notice. These updates now run as their own jobs with a longer timeout, so they complete instead of quietly dropping.",
    link: { label: "PR #364 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/364" },
  },
  {
    date: "2026-08-21",
    title: "Secret scanner now catches credentials assigned to object attributes",
    tags: ["fix", "security"],
    body:
      "Our secret scanner was missing hardcoded credentials written as an attribute assignment, such as self.PASSWORD or cfg.API_KEY, one of the most common ways credentials show up in object-oriented code. It now catches these too.",
    link: { label: "PR #334 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/334" },
  },
  {
    date: "2026-08-21",
    title: "Flash Review no longer keeps your PR diffs indefinitely",
    tags: ["security", "fix"],
    body:
      "The cache that helps Flash Review recognize similar diffs was storing your actual pull request code with no expiration. We added a 30-day retention window, and uninstalling the app still deletes everything immediately. Our privacy policy has been updated to reflect this.",
    link: { label: "PR #344 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/344" },
  },
  {
    date: "2026-08-20",
    title: "Closed a billing race that could let usage overshoot its cap",
    tags: ["fix", "security"],
    body:
      "Under concurrent load, two reviews on the same installation could both pass a spend-cap check before either recorded its cost, letting real usage exceed the intended monthly cap. Cap checks are now atomic, so this can no longer happen.",
    link: { label: "PR #304 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/304" },
  },
  {
    date: "2026-08-20",
    title: "Fixed a bug where buying extra seats could silently lose a purchase",
    tags: ["fix"],
    body:
      "Two near-simultaneous seat purchases on the same account, such as a double-click or two admins acting at once, could overwrite each other, so two successful purchases sometimes added only one seat. Seat purchases are now serialized so every successful purchase is actually applied, and the settings page got clearer visual grouping with direct links for setting up Slack/Teams webhooks.",
    link: { label: "PR #307 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/307" },
  },
  {
    date: "2026-08-20",
    title: "Independent second-model verification for Flash Review (AIR tier)",
    tags: ["feature"],
    body:
      "On the AIR tier, every Flash Review finding is now independently re-checked by a second model against the real diff before it's shown to you, and anything that doesn't hold up is dropped. This is designed to reduce false positives in PR review comments.",
    link: { label: "PR #310 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/310" },
  },
  {
    date: "2026-08-20",
    title: "Fixed a permissions gap that let non-admin org members manage billing",
    tags: ["security", "fix"],
    body:
      "We fixed a bug where a GitHub org member with only read access to a repository could be mistaken for an admin, letting them claim the first paid seat on an installation or reach the billing portal to view or change payment details. Admin access is now verified against your real per-repository GitHub permissions.",
    link: { label: "PR #315 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/315" },
  },
  {
    date: "2026-08-19",
    title: "Fixed reliability issues in free-tier Flash Review",
    tags: ["fix"],
    body:
      "Free-tier Flash Review had a usage-cap race condition and an incorrectly-scoped daily limit that could cause reviews to fail or be capped too early. Both are fixed, and per-feature spend is now tracked separately so your usage is easier to reason about.",
    link: { label: "PR #291 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/291" },
  },
  {
    date: "2026-08-18",
    title: "Scans got noticeably faster",
    tags: ["perf"],
    body:
      "The scanner was walking the entire repository tree multiple times per scan without skipping ignored directories, wasting most of that work. Fixing it made scans measurably faster, with no change in results.",
    link: { label: "PR #284 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/284" },
  },
  {
    date: "2026-08-16",
    title: "Hosted embeddings actually work now",
    tags: ["fix"],
    body:
      "Our hosted embedding service for paid-plan semantic search was silently failing on every request and falling back to a local model without telling you. Resource limits and batching were corrected so hosted embeddings now run as intended.",
    link: { label: "PR #259 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/259" },
  },
  {
    date: "2026-08-15",
    title: "Fixed dependency graphs for C# repositories",
    tags: ["fix"],
    body:
      "C# code rarely uses import statements the way other languages do, which meant Aletheore's dependency graph and generated documentation for C# repos were missing most real relationships between files. Dependencies are now also inferred from how types are actually used, substantially improving accuracy for C# codebases.",
    link: { label: "PR #250 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/250" },
  },
  {
    date: "2026-08-15",
    title: "Fixed Flash Review failures under concurrent PR activity",
    tags: ["fix"],
    body:
      "If an installation had multiple pull requests being reviewed at the same time, some Flash Reviews could fail outright and silently skip posting a comment. The underlying lock is now held only as long as it actually needs to be, so reviews queue and complete instead of failing.",
    link: { label: "PR #251 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/251" },
  },
  {
    date: "2026-08-14",
    title: "Deterministic database diagrams generated from your migrations",
    tags: ["feature"],
    body:
      "Aletheore can now read your Postgres migrations and generate an entity-relationship diagram of your database schema, with every table, column, and relationship tracing back to the exact migration file and line that created it.",
    link: { label: "PR #203 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/203" },
  },
  {
    date: "2026-08-14",
    title: "Fixed a deployment issue that crash-looped the app server in production",
    tags: ["fix"],
    body:
      "A packaging mistake left a required module out of the deployed app server image, causing it to crash repeatedly in production. The build has been fixed and verified.",
    link: { label: "PR #246 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/246" },
  },
  {
    date: "2026-08-12",
    title: "Fixed an affiliate payout bug that overcounted commissions",
    tags: ["fix"],
    body:
      "A reporting bug multiplied an affiliate's owed commissions by their number of referrals, in one case tripling what was actually owed. Affiliate payout totals are now calculated correctly.",
    link: { label: "PR #217 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/217" },
  },
  {
    date: "2026-08-12",
    title: "Fixed a bug that could expose a private repo's status publicly",
    tags: ["security", "fix"],
    body:
      "Opting one repo into the public status page was supposed to apply to just that repo, but it was actually a single account-wide switch, meaning your other repos, including private ones, could become publicly visible too. The opt-in is now correctly scoped per repo, and affected settings were restored to match real intent.",
    link: { label: "PR #219 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/219" },
  },
  {
    date: "2026-08-12",
    title: "AIRview wiki pages got noticeably more detailed",
    tags: ["feature"],
    body:
      "AIRview-generated documentation pages now go deeper on the files that matter most, with a dedicated overview, rationale, and gotchas for each, and can now reference related files outside the immediate module when explaining how something works.",
    link: { label: "PR #222 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/222" },
  },
  {
    date: "2026-08-11",
    title: "Fixed a bug that silently dropped affiliate referral credit",
    tags: ["fix"],
    body:
      "Checkouts completed with a valid affiliate discount code weren't being attributed to the referring affiliate, due to a mismatch in how we read our payment provider's data. Referral credit for discounted signups is now recorded correctly.",
    link: { label: "PR #194 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/194" },
  },
  {
    date: "2026-08-11",
    title: "Codebase search got dramatically more accurate",
    tags: ["feature", "perf"],
    body:
      "We measured our semantic code search and found it returned the right file in the top 5 results less than half the time. After a round of fixes, that's now effectively 100%, so AI-assisted answers about your codebase are grounded in the right code far more often.",
    link: { label: "PR #208 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/208" },
  },
  {
    date: "2026-08-11",
    title: "Hosted embeddings: semantic code search without running your own model",
    tags: ["feature"],
    body:
      "Paid plans can now use Aletheore's hosted embedding service for semantic code search instead of setting up and running a local model. Nothing from your code is retained by the service; only usage is metered against your plan.",
    link: { label: "PR #209 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/209" },
  },
  {
    date: "2026-08-10",
    title: "Self-serve data export and a safer account-deletion flow",
    tags: ["feature", "security"],
    body:
      "You can now download a JSON export of your connected repos, findings, members, and usage data from the admin panel. Deleting all account data now also requires a one-time email code, and admin actions like adding members or rotating tokens are now logged for your own audit trail.",
    link: { label: "PR #157 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/157" },
  },
  {
    date: "2026-08-10",
    title: "Deleted accounts are now fully cleaned up, and a login-hijack hole was closed",
    tags: ["security", "fix"],
    body:
      "Repo checkouts left on disk after a data deletion are now actually purged, closing a gap where deletion wasn't as immediate as promised. We also fixed a sign-in flow issue that could have let an attacker link a victim's browser session to the wrong GitHub account.",
    link: { label: "PR #160 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/160" },
  },
  {
    date: "2026-08-10",
    title: "Security audit: fixed credential leaks, missing integrity checks, and a webhook timing issue",
    tags: ["security", "fix"],
    body:
      "An audit found and fixed several real issues: installation tokens were leaking into error-alert emails and logs, a rate limit could be bypassed with a spoofed header, and third-party scripts loaded with no integrity checks. We also widened our billing webhook's timing tolerance so ordinary clock drift can no longer cause a successful payment to silently fail to upgrade an account, and cached a GitHub API call every dashboard page load depended on.",
    link: { label: "PR #167 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/167" },
  },
  {
    date: "2026-08-10",
    title: "Security audit: fixed an open redirect and a repeating server-error bug",
    tags: ["security", "fix"],
    body:
      "Follow-up fixes from the same audit: a sign-in redirect could be tricked into sending you to an external site, and a corrupted session token was causing repeated server errors instead of just signing you out. A missing-account edge case that defaulted to paid-plan access instead of free was also closed.",
    link: { label: "PR #168 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/168" },
  },
  {
    date: "2026-08-10",
    title: "Fixed a GitHub Marketplace billing bug that could lock out or under-charge purchases",
    tags: ["fix", "security"],
    body:
      "A bug in how we read GitHub Marketplace purchase events could have created a duplicate account record for a new customer, or granted full paid access to anyone whose plan name didn't exactly match what we expected. Both were fixed ahead of the Marketplace listing going live.",
    link: { label: "PR #169 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/169" },
  },
  {
    date: "2026-08-10",
    title: "Scanning no longer risks committing a map of your secrets",
    tags: ["security", "fix"],
    body:
      "Running a scan wrote a file listing every detected secret's location, with nothing stopping it from being committed to your repo. It's now automatically added to your .gitignore on your first scan.",
    link: { label: "PR #175 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/175" },
  },
  {
    date: "2026-08-10",
    title: "Secret previews are now hashed, not shown in the clear",
    tags: ["security", "fix"],
    body:
      "Previews of detected secrets shown in scan output and reports used to include real characters from the actual secret. They're now a one-way salted hash instead, so the real value never leaves your scan.",
    link: { label: "PR #176 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/176" },
  },
  {
    date: "2026-07-28",
    title: "Regression Fencing flags breaking changes to shared code",
    tags: ["feature"],
    body:
      "When a pull request changes a function's signature, Aletheore now checks whether the other files calling it were updated in the same PR. On paid plans, a dedicated check flags the gap before an unupdated caller ships as a regression.",
    link: { label: "PR #95 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/95" },
  },
  {
    date: "2026-07-26",
    title: "One simpler Pro plan",
    tags: ["feature"],
    body:
      "Aletheore now runs on a single Pro plan instead of separate Indie, Team, and Enterprise tiers. Pro includes improved model routing for one-time audits, with clear monthly caps on new repos audited and PR reviews so usage stays predictable.",
    link: { label: "PR #63 on GitHub", href: "https://github.com/Aletheore/Aletheore/pull/63" },
  },
];
