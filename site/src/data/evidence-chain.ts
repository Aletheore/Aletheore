import raw from "./evidence-chain.json";

export type ChainStep = {
  id: "endpoint" | "handler" | "symbol" | "owner" | "touched" | "verdict";
  label: string;
  value: string;
  detail: string;
};
export type EvidenceChain = { source: string; commit: string; file: string; steps: ChainStep[] };

export const evidenceChain = raw as EvidenceChain;
