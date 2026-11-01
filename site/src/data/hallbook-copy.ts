// Hallbook wording shared by the /hallbook page and the website assistant, so the two cannot drift apart.
import { receipts as r } from "./hallbook";

const [deltaLo, deltaHi] = r.agentBenchmark.passRateDeltaPoints;
const [, tokHi] = r.agentBenchmark.tokenMultiple;

export const hallbookCopy = {
  summary:
    "A library of curated agent skills bundled with Aletheore's MCP server. Your agent asks once and gets the right skill, or an honest answer that nothing fits. Searching the catalogue needs no download, no sign-in and no network call.",
  noSetup:
    "Skills usually travel as folders: find one, copy it into each agent's skills directory, restart the agent, and do it again for the next skill, the next agent and the next machine. Hallbook replaces all of that with one connection.",
  customBefore:
    "Write a skill once, or ask your agent to save what you just worked out, and it sits next to the catalogue. Every agent connected to Aletheore finds it the same way, so ",
  customMarked: "there are no folders to keep in step between tools.",
  addCommand: "aletheore hallbook add my-skill ./my-skill.md",
  vettingIntro:
    "A skill is instructions your agent will follow, so a bad one is a real risk. This is a real skill file from the catalogue. The list is what our checks cover, and where they stop.",
  vettingChecks: [
    ["Pinned source", "The exact repository and commit it came from, and the author is credited. Nothing is fetched from the network at read time."],
    ["Its own licence", "Each skill carries its licence. Only permissive ones (MIT, Apache-2.0, BSD, ISC and similar) are accepted."],
    ["Instruction pattern lint", "Instruction text is matched against two known-bad shapes: a download piped straight into a shell (curl or wget into sh, bash and similar), and phrasing that asks to send a secret or credential to a URL. It is a lint, not a general prompt-injection detector, and a clean result is not a security review."],
    ["Curation record and bundled scripts", "Shelf, quality score and the vetting result sit next to the skill. A bundled scripts/ folder is run through our secrets scanner, and only that: the pattern lint above reads the instruction text, not the scripts. A skill that fails any check is held back, and a person reviews each finding before it is cleared."],
  ] as const,
  benchmark: {
    question: "Do skills make an agent score higher?",
    body: `We tested five ways of delivering skills on ${r.agentBenchmark.tasks} engineering tasks with hidden tests. Pass rates moved between minus ${Math.abs(deltaLo)} and plus ${deltaHi} points, all within noise, and skills used up to ${tokHi} times the tokens.`,
    marked: "We do not claim a score gain on tasks a strong model already knows.",
  },
  access: {
    before: "What we do claim is access: ",
    marked: "curated skills, offline, one call, and an honest answer when nothing fits.",
    after: " Published research finds the big gains come from skills that carry knowledge a model lacks, such as a specific product or procedure. That is what we test next.",
  },
};
