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
