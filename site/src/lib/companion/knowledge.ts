// Server-side allowlist. Wording comes from the same data rendered by public pages.
import { flow, languageSupport, mcpHead, mcpSetup, quickstart } from "@/data/developers";
import { hallbook } from "@/data/hallbook";
import { hallbookCopy } from "@/data/hallbook-copy";
import { plans, credit } from "@/data/pricing";
import legal from "@/data/legal.json";

export type Fact = { id: string; title: string; text: string; href: string; command?: string };
export function plain(html: string) {
  const entities: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', apos: "'", nbsp: " ", ndash: "–", mdash: "—", lsquo: "‘", rsquo: "’", ldquo: "“", rdquo: "”", hellip: "…" };
  return html.replace(/<[^>]+>/g, " ").replace(/&(#x[0-9a-f]+|#[0-9]+|[a-z]+);/gi, (original, entity: string) => {
    if (!entity.startsWith("#")) return Object.hasOwn(entities, entity.toLowerCase()) ? entities[entity.toLowerCase()] : original;
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
  ...quickstart.commands.map((item, index) => ({ id: `quickstart-${index}`, title: item.title, text: `${item.title}: ${item.body}`, command: item.code, href: "/developers#quickstart" })),
  { id: "mcp", title: mcpHead.title, text: mcpHead.intro, href: "/developers#mcp" },
  ...plans.map((plan) => ({ id: `price-${plan.id}`, title: `${plan.name} pricing`, text: `${plan.name}: ${plan.price} ${plan.priceNote}. ${plan.desc}`, href: "/pricing" })),
  { id: "credits", title: credit.title, text: `${credit.intro} ${credit.note}`, href: "/pricing#credit" },
  ...paragraphs(legal.privacy.html, "The free CLI").map((text, index) => ({ id: `privacy-cli-${index}`, title: "CLI privacy and network activity", text, href: "/privacy" })),
  ...paragraphs(legal.security.html, "Your source code").map((text, index) => ({ id: `security-code-${index}`, title: "Hosted source code handling", text, href: "/security" })),
  ...paragraphs(legal.security.html, "Where we're honest about gaps").map((text, index) => ({ id: `security-gaps-${index}`, title: "Certification limits", text, href: "/security" })),
  ...paragraphs(legal.security.html, "GitHub App permissions").map((text, index) => ({ id: `perms-${index}`, title: "GitHub App permissions", text, href: "/security" })),
  ...paragraphs(legal.security.html, "Reporting a vulnerability").map((text, index) => ({ id: `vuln-${index}`, title: "Reporting a vulnerability", text, href: "/security" })),
  ...paragraphs(legal.refund.html, "How refunds work").map((text, index) => ({ id: `refund-${index}`, title: "Refunds", text, href: "/refund" })),
  ...paragraphs(legal.refund.html, "Cancelling").map((text, index) => ({ id: `cancel-${index}`, title: "Cancelling a subscription", text, href: "/refund" })),
  ...paragraphs(legal.privacy.html, "Contact").map((text, index) => ({ id: `contact-${index}`, title: "Contacting support", text, href: "/privacy" })),
  { id: "mcp-setup", title: mcpSetup.title, text: mcpSetup.body, command: mcpSetup.code, href: "/developers#mcp" },
  { id: "languages", title: languageSupport.title, text: languageSupport.body, href: "/developers#quickstart" },
  ...paragraphs(legal.terms.html, "What Aletheore is").map((text, index) => ({ id: `terms-what-${index}`, title: "What Aletheore is, and its license", text, href: "/terms" })),
  { id: "hallbook-what", title: "Hallbook", text: hallbookCopy.summary, href: "/hallbook" },
  { id: "hallbook-setup", title: "Hallbook needs no setup per agent", text: hallbookCopy.noSetup, href: "/hallbook#no-setup" },
  ...(hallbook.stats.total > 0 && hallbook.stats.shelves.length > 0 ? [{ id: "hallbook-catalog", title: "Hallbook catalogue", text: `${hallbook.stats.total.toLocaleString("en-US")} skills across ${hallbook.stats.shelves.length} shelves: ${hallbook.stats.shelves.map((shelf) => `${shelf.name} (${shelf.count})`).join(", ")}.`, href: "/hallbook#shelves" }] : []),
  { id: "hallbook-custom", title: "Your own Hallbook skills", text: hallbookCopy.customBefore + hallbookCopy.customMarked, command: hallbookCopy.addCommand, href: "/hallbook#custom" },
  ...hallbookCopy.vettingChecks.map(([title, text], index) => ({ id: `hallbook-vetting-${index}`, title: `How Hallbook skills are vetted: ${title}`, text, href: "/hallbook#vetting" })),
  { id: "hallbook-results", title: hallbookCopy.benchmark.question, text: `${hallbookCopy.benchmark.question} ${hallbookCopy.benchmark.body} ${hallbookCopy.benchmark.marked}`, href: "/hallbook#receipts" },
  { id: "hallbook-claim", title: "What Hallbook claims", text: hallbookCopy.access.before + hallbookCopy.access.marked + hallbookCopy.access.after, href: "/hallbook#receipts" },
  // Theo's own description and data handling, so questions about the assistant are answered from the policy, not from the CLI's.
  ...paragraphs(legal.privacy.html, "Theo, the website assistant").map((text, index) => ({ id: `theo-${index}`, title: "Theo, the website assistant", text, href: "/privacy" })),
];
export const CORPUS_VERSION = "public-site-v2";
const STOP_WORDS = new Set("a an the is are i we you it to of for in on and or my me can do does how what about with please aletheore".split(" "));
// Words people use that the documentation phrases differently. Each maps to words that really appear in the target facts.
const SYNONYMS: Record<string, string[]> = {
  cost: ["pricing"], costs: ["pricing"], price: ["pricing"], prices: ["pricing"], pay: ["pricing"], paid: ["pricing"], paying: ["pricing"], much: ["pricing"],
  fee: ["pricing"], fees: ["pricing"], charge: ["pricing"], charges: ["pricing"], plan: ["pricing"], plans: ["pricing"], tier: ["pricing"], tiers: ["pricing"],
  subscription: ["pricing"], subscribe: ["pricing"], billing: ["pricing"], cheap: ["pricing"], expensive: ["pricing"], upgrade: ["pricing"],
  money: ["refund"], reimburse: ["refund"], chargeback: ["refund"], returns: ["refund"],
  unsubscribe: ["cancel"], terminate: ["cancel"], canceling: ["cancel"], cancelling: ["cancel"],
  contact: ["support"], email: ["support"], reach: ["support"], human: ["support", "theo"], person: ["support"],
  bug: ["vulnerability", "report"], bugs: ["vulnerability", "report"], exploit: ["vulnerability", "report"], hack: ["vulnerability", "report"], hacked: ["vulnerability", "report"],
  disclose: ["vulnerability", "report"], disclosure: ["vulnerability", "report"], cve: ["vulnerability", "report"], vulnerabilities: ["vulnerability", "report"],
  access: ["permissions"], scopes: ["permissions"], scope: ["permissions"], allowed: ["permissions"],
  claude: ["mcp", "agent"], cursor: ["mcp", "agent"], codex: ["mcp", "agent"], copilot: ["mcp", "agent"], windsurf: ["mcp", "agent"], gemini: ["mcp", "agent"],
  ide: ["mcp", "agent"], editor: ["mcp", "agent"], agents: ["mcp", "agent"],
  setup: ["install"], installation: ["install"], installing: ["install"], download: ["install"], started: ["install"],
  who: ["theo"], name: ["theo"], bot: ["theo"], robot: ["theo"], chatbot: ["theo"], yourself: ["theo"], hi: ["theo"], hello: ["theo"], hey: ["theo"],
  ip: ["theo"], address: ["theo"], track: ["theo"], tracking: ["theo"], cookies: ["theo"],
  license: ["license", "licence"], licence: ["license", "licence"], licensing: ["license", "licence"], licensed: ["license", "licence"],
  agpl: ["license", "affero"], gpl: ["license", "affero"], copyleft: ["license", "affero"], opensource: ["open", "source", "license"],
  proprietary: ["commercial", "license"], closed: ["commercial", "license"], commercially: ["commercial", "license"],
  language: ["language"], languages: ["language"], lang: ["language"], supported: ["support"],
};
const ADDRESSES_ASSISTANT = new Set(["you", "your", "yourself", "theo"]);
const wordsOf = (value: string) => value.toLowerCase().match(/[a-z0-9]+/g) ?? [];
const stem = (word: string) => (word.length > 4 && word.endsWith("s") && !word.endsWith("ss") ? word.slice(0, -1) : word);
const factWords = (fact: Fact) => new Set(wordsOf(`${fact.title} ${fact.text} ${fact.command ?? ""}`).map(stem));
const titleWords = (fact: Fact) => new Set(wordsOf(fact.title).map(stem));
const VOCABULARY = new Set(FACTS.flatMap((fact) => [...factWords(fact)]));
// A word found in few passages says more than one found in most of them ("install" versus "cli"), so rare words weigh more.
const DOCUMENT_FREQUENCY = new Map<string, number>();
for (const fact of FACTS) for (const word of factWords(fact)) DOCUMENT_FREQUENCY.set(word, (DOCUMENT_FREQUENCY.get(word) ?? 0) + 1);
const rarity = (word: string) => Math.log(1 + FACTS.length / (DOCUMENT_FREQUENCY.get(word) ?? 1));
// Within one or two edits: catches typos like "instal" or "aletheor" without a search index.
function editDistance(a: string, b: string): number {
  const row = Array.from({ length: b.length + 1 }, (_, j) => j);
  for (let i = 1; i <= a.length; i++) {
    let diagonal = row[0];
    row[0] = i;
    for (let j = 1; j <= b.length; j++) {
      const above = row[j];
      row[j] = Math.min(row[j] + 1, row[j - 1] + 1, diagonal + (a[i - 1] === b[j - 1] ? 0 : 1));
      diagonal = above;
    }
  }
  return row[b.length];
}
function nearVocabulary(word: string): string | undefined {
  if (word.length < 5) return undefined;
  const allowed = word.length >= 8 ? 2 : 1;
  let best: string | undefined;
  let bestDistance = allowed + 1;
  for (const candidate of VOCABULARY) {
    if (Math.abs(candidate.length - word.length) > allowed) continue;
    const distance = editDistance(word, candidate);
    if (distance < bestDistance) { best = candidate; bestDistance = distance; }
  }
  return best;
}
// A near-miss of the product name ("aletheor", "alethore") is corrected before the model sees the question: models classify
// a misspelled brand inconsistently, and only words within two edits of the name are touched.
export function normalizeProductName(question: string): string {
  return question.replace(/[A-Za-z]{7,10}/g, (word) => (word.toLowerCase() !== "aletheore" && editDistance(word.toLowerCase(), "aletheore") <= 2 ? "Aletheore" : word));
}
// The few chat shorthands that are unambiguous in this context. Whole words only, so nothing inside another word changes.
// "dos", "hw" and "ur" are left alone on purpose: DOS, hardware and Ur are real words in this domain.
const SHORTHAND: Record<string, string> = { wat: "what", wht: "what", pls: "please", plz: "please", abt: "about" };
export function normalizeQuestion(question: string): string {
  return normalizeProductName(// Not when part of a filename or path (".wat" is a WebAssembly text file).
    question.replace(/(?<![\w./-])[A-Za-z]{2,3}(?![\w./-])/g, (word) => SHORTHAND[word.toLowerCase()] ?? word));
}
export function retrieve(question: string, history: readonly string[] = []): Fact[] {
  const raw = wordsOf(question);
  const mentionsProduct = /aletheore/i.test(question) || raw.some((word) => word.length >= 7 && editDistance(word, "aletheore") <= 2);
  const assistantAddressed = raw.some((word) => ADDRESSES_ASSISTANT.has(word));
  const exact = new Set<string>();
  const fuzzy = new Set<string>();
  for (const word of raw) {
    if (word.length < 2 || STOP_WORDS.has(word)) continue;
    const base = stem(word);
    exact.add(base);
    for (const extra of SYNONYMS[word] ?? []) exact.add(stem(extra));
    if (!VOCABULARY.has(base) && !(SYNONYMS[word] ?? []).length) { const near = nearVocabulary(base); if (near) fuzzy.add(near); }
  }
  const previous = new Set(history.slice(-2).flatMap((value) => wordsOf(value).filter((word) => word.length > 1 && !STOP_WORDS.has(word)).map(stem)));
  if (!exact.size && !fuzzy.size && mentionsProduct) return FACTS.filter((fact) => fact.id.startsWith("flow-"));
  const ranked = FACTS.map((fact) => {
    const body = factWords(fact);
    const title = titleWords(fact);
    let score = 0;
    for (const word of exact) score += rarity(word) * ((body.has(word) ? 3 : 0) + (title.has(word) ? 2 : 0));
    for (const word of fuzzy) score += rarity(word) * (body.has(word) ? 2 : 0);
    for (const word of previous) score += rarity(word) * (body.has(word) ? 1 : 0);
    if (assistantAddressed && fact.id.startsWith("theo-")) score += 6;
    return { fact, score };
  }).sort((a, b) => b.score - a.score);
  const selected = ranked.filter((item) => item.score > 0).slice(0, 10).map((item) => item.fact);
  // Welcome questions still need the documented product overview.
  const overview = FACTS.find((fact) => fact.id === "flow-scan");
  if (overview && !selected.some((fact) => fact.id === overview.id)) selected.unshift(overview);
  return selected.slice(0, 11);
}
