// Server-side allowlist. Wording comes from the same data rendered by public pages.
import { flow, quickstart, mcpHead } from "@/data/developers";
import { plans, credit } from "@/data/pricing";
import legal from "@/data/legal.json";

export type Fact = { id: string; title: string; text: string; href: string; command?: string };
export function plain(html: string) {
  const entities: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", ndash: "–", mdash: "—", lsquo: "‘", rsquo: "’", ldquo: "“", rdquo: "”", hellip: "…" };
  return html.replace(/<[^>]+>/g, " ").replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z]+);/gi, (original, entity: string) => {
    if (!entity.startsWith("#")) return entities[entity] ?? original;
    const code = entity[1].toLowerCase() === "x" ? parseInt(entity.slice(2), 16) : Number(entity.slice(1));
    return code > 0 && code <= 0x10ffff && !(code >= 0xd800 && code <= 0xdfff) ? String.fromCodePoint(code) : "�";
  }).replace(/\s+/g, " ").trim();
}
function paragraphs(html: string, section: string): string[] {
  const body = [...html.matchAll(/<h2>(.*?)<\/h2>([\s\S]*?)(?=<h2>|$)/g)].find((m) => plain(m[1]) === section)?.[2];
  if (!body) throw new Error(`Missing companion source section: ${section}`);
  return [...body.matchAll(/<p>([\s\S]*?)<\/p>/g)].map((m) => plain(m[1]));
}
export const FACTS: readonly Fact[] = [
  ...flow.map((item) => ({ id: `flow-${item.id}`, title: item.title, text: item.body, href: "/developers" })),
  ...quickstart.commands.map((item, index) => ({ id: `quickstart-${index}`, title: item.title, text: item.body, command: item.code, href: "/developers#quickstart" })),
  { id: "mcp", title: mcpHead.title, text: mcpHead.intro, href: "/developers#mcp" },
  ...plans.map((plan) => ({ id: `price-${plan.id}`, title: `${plan.name} pricing`, text: `${plan.name}: ${plan.price} ${plan.priceNote}. ${plan.desc}`, href: "/pricing" })),
  { id: "credits", title: credit.title, text: `${credit.intro} ${credit.note}`, href: "/pricing#credit" },
  ...paragraphs(legal.privacy.html, "The free CLI").map((text, index) => ({ id: `privacy-cli-${index}`, title: "CLI privacy and network activity", text, href: "/privacy" })),
  ...paragraphs(legal.security.html, "Your source code").map((text, index) => ({ id: `security-code-${index}`, title: "Hosted source code handling", text, href: "/security" })),
  ...paragraphs(legal.security.html, "Where we're honest about gaps").map((text, index) => ({ id: `security-gaps-${index}`, title: "Certification limits", text, href: "/security" })),
];
export const CORPUS_VERSION = "public-site-v1";
const STOP_WORDS = new Set("a an the is are i we you it to of for in on and or my me can do does how what about with please aletheore".split(" "));
export function retrieve(question: string, history: readonly string[] = []): Fact[] {
  const tokens = (value: string) => [...new Set(value.toLowerCase().match(/[a-z0-9]+/g) ?? [])].filter((word) => word.length > 1 && !STOP_WORDS.has(word));
  const current = tokens(question);
  const previous = tokens(history.slice(-2).join(" "));
  if (!current.length && /aletheore/i.test(question)) return FACTS.filter((fact) => fact.id.startsWith("flow-"));
  const ranked = FACTS.map((fact) => {
    const body = new Set(tokens(`${fact.title} ${fact.text} ${fact.command ?? ""}`));
    return { fact, score: current.reduce((n, word) => n + (body.has(word) ? 3 : 0), 0) + previous.reduce((n, word) => n + (body.has(word) ? 1 : 0), 0) };
  }).sort((a, b) => b.score - a.score);
  const selected = ranked.filter((item) => item.score > 0).slice(0, 8).map((item) => item.fact);
  // Welcome questions still need the documented product overview.
  const overview = FACTS.find((fact) => fact.id === "flow-scan");
  if (overview && !selected.some((fact) => fact.id === overview.id)) selected.unshift(overview);
  return selected.slice(0, 9);
}
