import raw from "./hallbook.json";

export type HbCard = { alternates?: string[]; id: string; title: string; description: string; category: string; shelf: string; license: string; quality?: number; source?: string };
export type HbFind = { confidence: "none" | "low" | "high"; note?: string; top?: HbCard; alternatives?: HbCard[]; candidates?: HbCard[]; chars?: number; preview?: string };
export type HbShelf = { key: string; name: string; count: number; top: string[] };

export const hallbook = raw as unknown as {
  stats: { total: number; categories: number; shelves: HbShelf[]; licenses: [string, number][] };
  skill: { id: string; head: string };
  examples: Record<string, HbFind>;
};

export const exampleQueries = Object.keys(hallbook.examples);
export const EXAMPLE_LABEL: Record<string, string> = {
  "plan a zero downtime database migration": "database migration",
  "create a stripe webhook handler": "stripe webhook",
  "write a good git commit message": "commit message",
  "design a REST API for invoices": "REST API",
  "fix the thing": "fix the thing",
};

/** Where a number on the page comes from. Paths are relative to the Aletheore repo (skill-hub branch). */
export type Sourced = { source: string; /** True when the number could not be traced to a file in the repo. */ unverified?: boolean };
export type Metric = Sourced & { value: number; display: string; before?: { value: number; display: string } };

const LAB = "search-lab/scratch/final_eval.log";
const CAL = "search-lab/data/confidence_calibration.json";

export const receipts = {
  heldoutQueries: { value: 994, source: "search-lab/data/queries_heldout.jsonl (994 lines)" } as Sourced & { value: number },
  findFirst: { value: 45, display: "45%", before: { value: 9, display: "9%" }, source: `${LAB}, HELD-OUT lab R@1 0.447 vs baseline 0.089` } as Metric,
  findTopFive: { value: 73, display: "73%", before: { value: 21, display: "21%" }, source: `${LAB}, HELD-OUT lab R@5 0.726 vs baseline 0.210` } as Metric,
  confidentPrecision: { value: 87, display: "85 to 89%", source: `${CAL}: precision of a confident answer, held-out 0.854, dev 0.877, test 0.887 (bar drawn at the 87 midpoint)` } as Metric,
  useSaid: { value: 98, display: "96 to 100%", source: `hallbook/search-lab branch, receipts/stage5.jsonl: asking for the Aletheore skills, 24 coding tasks per model, DeepSeek V4 Flash 24 of 24 and V4.1 Flash 23 of 24` } as Metric,
  useAgentsMd: { value: 17, display: "17%", source: `hallbook/search-lab branch, receipts/stage5.jsonl: one line in AGENTS.md, DeepSeek V4 Flash, 4 of 24 coding tasks (the two free models were 3 of 24 and 13 of 24)` } as Metric,
  useNothing: { value: 2, display: "0 to 4%", source: `hallbook/search-lab branch, receipts/stage5.jsonl: nothing said, DeepSeek V4 Flash 0 of 24 and V4.1 Flash 1 of 24` } as Metric,
  riskyHits: { value: 53, source: "search-lab/data/triage.md and triage.jsonl: 53 hits, 48 SAFE_DOC, 5 SAFE_BENIGN, 0 SUSPICIOUS (classified by a model, deepseek-v4-flash)" } as Sourced & { value: number },
  heldBack: { value: 6, source: "docs/superpowers/specs/2026-10-18-aletheore-skill-hub-design.md: six held back at export (3 instruction patterns, 1 curl | bash, 2 secrets scan). The 'dropped for phoning home' detail is not in that file." } as Sourced & { value: number },
  writesToAgentFolders: { value: 0, source: "src/aletheore/cli.py, the `aletheore hallbook` commands: list, get, search, categories, files, get-file only read; snippet and hook print text for the user to paste; add and remove touch only Aletheore's own custom-skills folder. Bundled skill scripts were checked for writes into agent folders; the one that did (skill-creator's run_eval.py) was adjusted to run in a throwaway project directory." } as Sourced & { value: number },
  agentUse: {
    source: "hallbook/search-lab branch, receipts/agents/CROSS_AGENT_RESULTS.md: six tasks per condition (twelve for Codex, run twice); Antigravity and Codex from logged tool calls, Claude from subagents reporting their own tool use",
    rows: [
      { agent: "Claude Code (subagents)", nothing: [0, 6], snippet: [5, 6], say: [5, 6] },
      { agent: "Antigravity CLI", nothing: [0, 6], snippet: [5, 6], say: [6, 6] },
      { agent: "Codex CLI", nothing: [0, 12], snippet: [3, 12], say: [2, 12] },
    ] as { agent: string; nothing: [number, number]; snippet: [number, number]; say: [number, number] }[],
  } as Sourced & { rows: { agent: string; nothing: [number, number]; snippet: [number, number]; say: [number, number] }[] },
  agentBenchmark: { tasks: 30, passRateDeltaPoints: [-4.5, 5] as [number, number], tokenMultiple: [1, 1.6] as [number, number], source: `hallbook/search-lab branch, receipts/quick_paired.py on stage6.jsonl: DeepSeek V4 Flash, fixtures f11 to f40, paired against no skills. Pass-rate change: hallbook +1.7, hint +5.0, inject +3.3, oracle +2.2, ask -4.5 points, every 95% interval includes zero. Tokens: 0.99 (inject) to 1.64 (ask) times` } as Sourced & { tasks: number; passRateDeltaPoints: [number, number]; tokenMultiple: [number, number] },
};
