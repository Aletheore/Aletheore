import tables from "@/data/benchmarks-tables.json";

export type Table = { columns: string[]; rows: string[][]; sources: string[] };
export const T = tables as unknown as Record<keyof typeof tables, Table>;

/** Rows as records keyed by column index, the shape DataTable renders. */
export function asRecords(t: Table): { columns: { key: string; label: string; align?: "right" }[]; rows: Record<string, string>[] } {
  return {
    // Numeric columns line up on the right; text columns stay left.
    columns: t.columns.map((label, i) => ({
      key: `c${i}`,
      label,
      align: i > 0 && t.rows.every((r) => /^[-+~$]?\d/.test(r[i])) ? ("right" as const) : undefined,
    })),
    rows: t.rows.map((r) => Object.fromEntries(r.map((v, i) => [`c${i}`, v]))),
  };
}

export const num = (s: string): number => Number.parseFloat(s.replace(/[^\d.]/g, ""));

export const head = {
  eyebrow: "Benchmarked, not just claimed",
  title: "We publish the rows we lose, too.",
  intro:
    "A separate, public repository measures Aletheore head-to-head against RepoWise, Graphify and the real PR reviewers: locating code, generated docs, PR review quality, and what a bare LLM gets wrong that a deterministic scanner gets right. Every retrieval number recomputes from raw results with no API key and no network; every PR-review number comes from real API runs with the actual production model.",
  reproduce: "git clone https://github.com/Aletheore/aletheore-benchmarks\ncd aletheore-benchmarks\npython scripts/score_fallback_judge.py",
  repoHref: "https://github.com/Aletheore/aletheore-benchmarks",
};

export const toc = [
  { id: "pr-review-head-to-head", label: "PR review", fresh: false },
  { id: "swe-prbench", label: "SWE-PRBench", fresh: true },
  { id: "compact-context", label: "Prompt size", fresh: false },
  { id: "locating-code", label: "Locating code", fresh: false },
  { id: "graphify", label: "Graphify", fresh: true },
  { id: "comprehension", label: "Comprehension", fresh: true },
  { id: "deterministic-vs-llm", label: "vs a bare LLM", fresh: true },
  { id: "scanner-accuracy", label: "Scanner accuracy", fresh: true },
  { id: "hosted-embeddings", label: "Embeddings", fresh: false },
  { id: "toon", label: "TOON", fresh: false },
  { id: "where-we-lose", label: "Where we lose", fresh: false },
] as const;

export const prReview = {
  eyebrow: "PR review, 13 real pull requests, one judge for every tool",
  title: "In the same range as the leading reviewers, after closing a precision gap of our own.",
  intro:
    "44 human-identified bugs across 13 real pull requests from sentry, grafana, cal.com and keycloak. Every tool's findings, ours and theirs, go through the same blinded judge (gpt-6-luna, told nothing about which tool wrote what), which scores both recall (how many of the 44 bugs a finding actually catches) and precision (how many findings are accurate, specific claims about the diff). Brackets are 95% intervals from resampling the 13 pull requests.",
  recallNote: "95% intervals from resampling the 13 pull requests are wide (about plus or minus 14 to 18 points): read this as \"in the same range,\" not a ranked leaderboard. No difference between Aletheore and Copilot or GitLab here is statistically significant.",
  precisionNote: "Precision rates land in the same range across most tools; the raw count of real, accurate issues surfaced does not. Qodo's near-100% precision comes from making far fewer findings in the first place (24 total, all accurate), a real trade-off, not a flaw either way.",
  gap: {
    title: "The gap we found and closed",
    body: "Before the fix, of Aletheore's roughly 96 findings per run, about 13 were confirmed false positives and about 14 more were ones the judge could not confirm; precision counts both against us, which is why it was 71.5%. Most were claims about one file that another file in the same pull request contradicts (\"no migration is included\", \"this field is never registered\"), because each per-file review call saw only its own patch. Showing every call the rest of the pull request raised Flash precision from 71.5% to 92.6% (a 20.4-point gain, 95% interval +7.2 to +31.3) with recall unchanged, and AIR from 79.7% to 93.3%. On Flash, confirmed false positives fell from 13.0 to 2.0 per run.",
  },
  pairwise: {
    title: "Head to head, with intervals",
    rows: [
      ["Flash minus GitLab Duo", "+3.3 pts [-12.9, +18.1]", "-2.9 pts [-13.2, +9.6]"],
      ["Flash minus Copilot", "-5.3 pts [-26.5, +14.3]", "+2.5 pts [-8.5, +12.0]"],
      ["Flash minus Qodo", "+30.5 pts [+22.2, +37.5]", "-7.7 pts [-13.9, -3.5]"],
      ["AIR (with verification) minus Flash", "-2.1 pts [-9.6, +6.6]", "+0.9 pts [-4.8, +7.1]"],
    ],
    note: "Only the Qodo row excludes zero on both measures: a real recall lead and a real precision deficit. Every other difference is inside the noise. The verification pass on AIR added little once shared context was on, at roughly five times the generation cost, and the product no longer runs it.",
  },
  read: [
    { title: "How to read this", body: "Against GitLab Duo and Copilot, Aletheore is in the same range on both measures, not ahead. Copilot's recall is numerically higher, and no difference between Aletheore and either of them is statistically significant. Against Qodo it is a trade-off: recall is about 30 points higher, but Qodo made far fewer findings and every one was accurate." },
    { title: "Bugs caught, and where our real lead is", body: "On the 44 golden bugs the tools are level: Aletheore catches 23.5 per run, GitLab Duo 22.0, Copilot 26.0 (the highest) and Qodo 10.0. Where the gap is large is the volume of accurate findings: about 87 per run against 60, 39 and 24. Those extra findings are accurate, specific claims about the diff, but most are not among the 44 golden bugs (many are test-coverage, maintainability and edge-case observations), so read it as breadth of accurate feedback, not as more bugs caught." },
    { title: "A regression we caught in ourselves", body: "A benchmark once found that a newer context block (sibling files in the same directory) cost 15 to 25 points of recall and doubled false positives in a controlled test. Production stopped feeding it into live reviews the same night. That history is published as Experiment 6." },
  ],
  notShown: "Tools not shown. CodeRabbit's terms bar publishing benchmark results about it without consent, Cursor's Bugbot terms allow it only alongside everything needed to replicate the test, and we removed Greptile's results because its terms restrict sharing the service's results with third parties without written authorization. We would add any of them if they agree.",
  fullHref: "https://github.com/Aletheore/aletheore-benchmarks/blob/master/pr_review/README.md#experiment-8-symmetric-llm-judged-comparison-on-13-real-prs-2026-09-24",
};

export const swe = {
  eyebrow: "An external benchmark we did not build",
  title: "On SWE-PRBench's published leaderboard, at or above the frontier-model cluster.",
  intro:
    "SWE-PRBench is an independent, published benchmark: 350 real merged pull requests with human-annotated ground truth, and a judge validated at kappa 0.75 against human agreement. We ran Aletheore's real review path on the published 100-task diff-only split with the paper's own unmodified harness, so the score sits directly beside its leaderboard.",
  links: [
    { label: "Dataset", href: "https://huggingface.co/datasets/foundry-ai/swe-prbench" },
    { label: "Harness", href: "https://github.com/FoundryHQ-AI/swe-prbench" },
    { label: "Paper (arXiv 2603.26130)", href: "https://arxiv.org/abs/2603.26130" },
  ],
  read: [
    "Bare-mode Aletheore (one call per diff) scored 0.149 to 0.169 depending only on which API route the judge used, statistically tied with Claude Sonnet 4.6, Claude Haiku 4.5, DeepSeek V3 and Mistral Large 3.",
    "Turning on per-file completeness, the production paid-tier setting, moved the score to 0.174: recall from 16.8% to 22.3% for about two points of precision. A repeat judge pass on the identical output scored 0.170, a 0.4-point gap.",
    "The model doing the work is GLM-5.3-Flash. Generation for all 100 tasks cost $0.055 in bare mode and $0.162 with per-file completeness (more calls). The other costs in the table are what the same 100 tasks would cost on those models at the bare-mode token volume.",
  ],
  caveats: [
    "The 0.174 sits about two points above the cluster, while our own regeneration-to-regeneration spread on this set was roughly 0.015 to 0.017 (standard deviation, measured in bare mode with a proxy judge). Read it as at or above the cluster, not as a ranking.",
    "Absolute scores are low for every model here (recall 22.3% and precision 20.5% for ours): SWE-PRBench is a hard benchmark.",
    "This is not the full hosted product. There were no file contents or symbol context for these unscanned external repos, and the second-model verification pass was off.",
  ],
  href: "https://github.com/Aletheore/aletheore-benchmarks/tree/master/swe_prbench",
};

export const compact = {
  eyebrow: "PR review, shipped, not hypothetical",
  title: "Dropping the raw file dump didn't cost review quality.",
  intro:
    "Flash Review can build its prompt with the full raw content of every changed file, or with Aletheore's own evidence alone (blast radius, referenced symbols, no file dump). Four experiments across three models asked whether that trade-off actually costs anything. The one that decided it: gpt-5.6-luna, the real production model, generating reviews under both arms; deepseek-v4-flash independently verifying every finding against the diff, three full repeats of a 50-case mixed-language corpus.",
  runsNote:
    "Read honestly, not cherry-picked: run 3's apparent 100% is not evidence that full context caught up. A transient network failure happened to strip out exactly the harder cases that produced its rejects and uncertains in the other two runs. Evidence-only never underperformed full context in any run.",
  sizeNote:
    "Read the last row honestly rather than expecting a 6 to 10 times price drop to match the token drop: on this model, completion tokens run 11k to 13k almost regardless of which arm generated the prompt, so the real dollar savings land at roughly a quarter, not a multiple. What the input-side reduction buys is headroom against a tight tokens-per-minute quota, such as a free-tier provider's 6,000 TPM.",
  result: "Result: evidence-only shipped as Flash Review's production default, not an experiment behind a flag. Total cost for all three validation runs on the real production model: $0.9229.",
};

export const locating = {
  eyebrow: "Locating code, same questions, same ground truth",
  title: "Every shared corpus, both systems, best RepoWise mode.",
  intro:
    "RepoWise searches its own generated wiki, so each corpus had one built for it first (init --coverage 1.0, deepseek-v4-flash, 1,341 pages, $1.85 total). The table shows RepoWise's best of semantic or full-text search per corpus, so it isn't beating a weaker mode by default.",
  stats: [
    { value: "5-0-2", label: "wins, losses, ties on top-1", sub: "all 7 shared corpora" },
    { value: "$0.00", label: "to build a searchable index", sub: "against RepoWise's $1.85" },
    { value: "93.3%", label: "cross-language top-5 accuracy", sub: "up from 60.0% after a language pre-filter fix" },
  ],
  after:
    "Top-1: 5 wins, 0 losses, 2 ties. Flask (measured separately, not one of the seven shared corpora): Aletheore 68.8% / 93.8% / 100% (top-1/3/5) against RepoWise semantic 28.1% / 56.2% / 56.2%. Where we lose: jekyll top-5, 46.7% against their 66.7%.",
  vocab:
    "Both systems were also given vocabulary-phrased questions on the same wikis. RepoWise gains from this too, because its wiki pages name the symbols, and on jekyll it overtakes us outright: 80.0% to 66.7%. Across those ten cells we lead in seven, tie in two and lose one. RepoWise's --mode symbol returned 0.0% on every corpus and is excluded as a misapplied mode, not counted as a loss.",
  latency: "Search latency, measured in-process for both tools: Aletheore 40.5 ms mean against RepoWise's 52.5 ms, reversed from an earlier 125 ms against 68 ms figure (see METHODOLOGY.md).",
  matrixNote:
    "Aletheore 0.8.11 installed from PyPI, each corpus re-scanned and re-indexed from scratch with local nomic-embed-text (768-dim) embeddings, no API key. Two phrasing regimes are published per corpus: general deliberately avoids the project's own vocabulary, vocabulary uses it. Real users ask somewhere between the two. Every weak corpus moves 20 to 47 points on phrasing alone, larger than any ranking change in this programme.",
};

export const graphify = {
  eyebrow: "Code intelligence against Graphify",
  title: "Ties or leads Graphify on all 15 questions, in fewer tokens.",
  intro:
    "Graphify is a tree-sitter code-knowledge-graph tool with its own benchmark on frappe/erpnext (about 1M lines). Rather than cite either tool's published numbers, we ran both ourselves under one shared agent loop and one anonymized judge, on the same pinned ERPNext commit: 15 independently authored questions, each run three times (baseline grep/read/list, plus Aletheore, plus Graphify), scored by a judge never told which tool produced which answer.",
  read: [
    "Aletheore ties or leads Graphify on every question once a ground-truth error found in pre-publication review was corrected.",
    "Excluding the one question both tools timed out on, Aletheore answers for 36% fewer tokens than Graphify (9,803 against 15,296 mean).",
    "The honest caveat: only 2 of 15 questions actually discriminate between conditions, so this is a real result on this question set, not a claim that generalizes past 15 questions on one corpus. Total real cost of the whole comparison: $0.19.",
  ],
  href: "https://github.com/Aletheore/aletheore-benchmarks/tree/master/graphify_comparison",
};

export const comprehension = {
  eyebrow: "Explaining code, \"how does X work?\"",
  title: "Roughly at parity with RepoWise, leaning ahead.",
  intro:
    "A blind LLM judge scores answers 0 to 3, each question graded twice with the two systems' positions swapped, an equal 12,000-character context budget, tool names scrubbed and three repeats per question. Re-measured across five languages on current code, full 12-question architecture set per corpus.",
  note:
    "We lead on average, not decisively. Most individual per-corpus gaps sit inside that corpus's own measured judge-repeat spread. The aggregate preference count across all 360 judged pairs is more telling: Aletheore 198 (55.0%), RepoWise 144 (40.0%), tie 18 (5.0%). This reverses an earlier loss (2.13 against 2.35) measured on different code with a different method.",
  href: "https://github.com/Aletheore/aletheore-benchmarks/blob/master/AIRVIEW_GAP.md",
};

export const detVsLlm = {
  eyebrow: "Deterministic analysis against a bare LLM",
  title: "Handed the same data, a bare LLM still cannot reproduce it.",
  intro:
    "For the parts of Aletheore that are not an LLM call at all (hotspots, ownership, dead code, computed from real git history and a real import graph), can a bare LLM reproduce the answer if it is simply handed the same data? On the same Flask corpus, given the exact git log slice and import statements the scanner consumes:",
  href: "https://github.com/Aletheore/aletheore-benchmarks/blob/master/DETERMINISTIC_VS_LLM.md",
};

export const scanners = {
  eyebrow: "Deterministic scanner accuracy",
  title: "No LLM, no judge: exact matches against labeled ground truth.",
  intro:
    "Aletheore's fully deterministic scanners (secrets, vulnerabilities, dead code, licenses, database schema) and structural code search had no systematic accuracy measurement before this pass. Each ran the real production function against real, large open-source repos, not mocks. The synthetic pilot gives exact-match ground truth; the real-repo runs give a false-positive signal a pilot cannot, and they found real defects that were fixed.",
  cards: [
    {
      name: "Secrets and vulnerabilities",
      pilot: "Secrets 6/6 recall, 0/5 false positives. Vulnerabilities 10/10 recall, 0/7 false positives, all 10 manifest ecosystems covered.",
      real: "21,430 files across 20 real repos; 96 secrets and 231 vulnerability findings manually reviewed. 6 real product gaps found and fixed, all shipped with regression tests. Against RepoWise on secrets, same 20 repos: RepoWise misses 5 of 6 real credential formats, and every one of its 375 real-repo findings was noise, mostly Django's own test-suite fake passwords.",
    },
    {
      name: "Dead code",
      pilot: "10/10 recall, 0/7 false positives.",
      real: "Flask reported all six of its real runtime dependencies as unused: a severe bug (the unused-dependency check read a graph field that structurally excludes external packages), found and fixed.",
    },
    {
      name: "AST-pattern search",
      pilot: "All 12 existing unit tests pass.",
      real: "A reproducible tree-sitter segfault on large repos, on Python 3.12 as well as 3.14 (not 3.14-only as first documented). Fixed with batched subprocess isolation, verified 20/20 clean on both versions.",
    },
    {
      name: "License detection",
      pilot: "46 pre-existing unit tests, all passing.",
      real: "3 real gaps found and fixed: BSD license bodies contain no \"BSD\" keyword at all, LICENSE.rst was not a recognized filename, and Maven license lookups never followed parent POM references.",
    },
    {
      name: "SQL schema extraction",
      pilot: "Zero crashes across 5,378 real migration files in 9 real repos, raw SQL and ORM-native (Django, Rails, Alembic) conventions.",
      real: "8 real gaps found and fixed over two rounds; one alone, a Django FK field subclass Sentry uses 249 times, took Sentry's extracted relations from 8 to 235. RepoWise extracted zero foreign keys or indexes across 622 files.",
    },
  ],
  href: "https://github.com/Aletheore/aletheore-benchmarks#deterministic-scanner-accuracy",
};

export const embeddings = {
  eyebrow: "Hosted embeddings, jina against local nomic",
  title: "Full 23-row comparison.",
  intro:
    "This one does not carry the same \"no API key, no network\" guarantee as the rest of the page: it measures Aletheore's hosted embedding endpoint (jina-embeddings-v2-base-code, requires aletheore login and a paid plan) against the same 13 corpora, 23 corpus and regime pairs and questions as the retrieval matrix, with only the embedder changed from local nomic-embed-text to hosted jina (both 768-dim).",
  summary:
    "Mean: top-1 +5.0 points, MRR +0.049. 20 of 23 rows flat or better; 3 worse, all in Slim's vocabulary regime or zod. Thrift's cross-language row is the largest single gain (+20.0 points), the same regime the language pre-filter fix targeted.",
  update:
    "Update, 2026-08-20: a later, independent hosted-jina measurement against the live service found a much steeper zod gap than the -6.7 points above (general top-1 20.0% to 0.0%, vocabulary 60.0% to 6.7%). Two decoy files that jina ranks above the true answer explain it: a 37-line smoke-test file that imports every zod build variant, and the roughly 30 locale files under packages/zod/src/.",
};

export const toon = {
  eyebrow: "Same evidence, fewer tokens",
  title: "TOON against raw JSON, measured on this exact repo's own self-scan.",
  stats: [
    { value: "1,174,274", label: "air.json tokens" },
    { value: "619,575", label: "air.toon tokens" },
  ],
  headline: "47.2%",
  headlineLabel: "fewer tokens, same evidence",
  disk: "53.8% smaller on disk",
  note: "Every coding agent that reads this file pays less to do it. Measured with the o200k_base tokenizer on a fresh self-scan of this repository (2026-09-25 snapshot; 5,112,929 against 2,362,099 bytes). The absolute counts change as the repo grows.",
};

export const lose = {
  eyebrow: "Where we lose",
  title: "Stated here rather than in a footnote.",
  items: [
    "Five of eight corpora score below 35% top-1 under vocabulary-avoiding phrasing, though most of that gap is our question authoring, not the product. Every one recovers 20 to 47 points when asked in the project's own terms.",
    "AIRview writes a page for only 21 of 100 changed files on Flask's last 30 commits; the rest are served by a deterministic fallback, not the generated wiki this project is named for.",
    "\"How does X work?\" comprehension is close, not a clean win: 2.00 against 1.77 averaged across five languages, but most per-corpus gaps sit inside the judge's own noise.",
    "On SWE-PRBench our absolute recall (22.3%) and precision (20.5%) are low, as they are for every model; the score is above the published cluster by a margin close to our own run-to-run variance.",
    "The Graphify comparison rests on 15 questions on one corpus, of which only 2 discriminate between conditions.",
    "Our local setup was slower than Graphify's: scanning and indexing ERPNext took about 23 minutes against their about 1 minute. We found the real bottleneck (dead-code detection, 77% of scan time, not parsing) and fixed it in aletheore 0.9.5: 53.23 s against the old 236.02 s, verified against the installed release. Indexing (about 19 minutes, a separate I/O-bound step) is now the honest remaining gap.",
    "The PR-review comparison rests on 13 pull requests, so its intervals are wide and small differences between tools are noise. Our fix for cross-file false positives was developed while looking at these same pull requests and has not been checked on unseen ones. \"Precision\" counts any accurate, specific claim about the diff, so it measures accuracy of claims, not how important they are. The benchmark runs the diff-only generation path; the hosted product adds more context and checks. The other tools' rows are single runs from before 2026-09-23.",
  ],
};

export const closing = {
  title: "Every number above is in a public repo, with the raw results.",
  body: "418 questions across 12 corpora, a 50-case PR-review corpus, the SWE-PRBench run, the Graphify comparison and the scanner-accuracy suites: all reproducible, all checked against real Aletheore output, not a mockup.",
};
