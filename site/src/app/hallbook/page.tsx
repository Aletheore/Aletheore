import type { Metadata } from "next";
import { AnnotatedSkill } from "@/components/hallbook/annotated-skill";
import { Command } from "@/components/hallbook/command";
import { ConnectDiagram } from "@/components/hallbook/connect-diagram";
import { Mark } from "@/components/hallbook/mark";
import { Hero } from "@/components/hallbook/hero";
import { Transcript } from "@/components/hallbook/transcript";
import { BarList, type BarRow } from "@/components/site/bar-list";
import { Section } from "@/components/site/section";
import { hallbook, receipts as r } from "@/data/hallbook";

export const metadata: Metadata = {
  title: "Hallbook: curated agent skills, read locally",
  description: "A library of curated agent skills bundled with Aletheore's MCP server. Your agent asks once and gets the right skill, or an honest answer that nothing fits. No download, no sign-in, no network call.",
  alternates: { canonical: "/hallbook" },
  // Not indexed until Hallbook ships: remove this line (and add the page to public/sitemap.xml and llms.txt) at launch.
  robots: { index: false, follow: false },
};

const maxShelf = Math.max(...hallbook.stats.shelves.map((s) => s.count));
const shelfRows: BarRow[] = hallbook.stats.shelves.map((s) => ({ label: s.name, value: s.count, display: String(s.count), note: s.top.join(" · ") }));
const withBefore = (label: string, m: typeof r.findFirst): BarRow => ({ label, value: m.value, display: m.display, ours: true, other: m.before ? { label: "before", ...m.before } : undefined });
const findRows: BarRow[] = [
  withBefore("First result is the right skill", r.findFirst),
  withBefore("Right skill in the top five", r.findTopFive),
];
const preciseRows: BarRow[] = [{ label: "Confident answer is right", value: r.confidentPrecision.value, display: r.confidentPrecision.display, ours: true }];
const useRows: BarRow[] = [
  { label: "You say it in the prompt", value: r.useSaid.value, display: r.useSaid.display, ours: true },
  { label: "One line in AGENTS.md", value: r.useAgentsMd.value, display: r.useAgentsMd.display },
  { label: "Nothing said", value: r.useNothing.value, display: r.useNothing.display },
];
const [deltaLo, deltaHi] = r.agentBenchmark.passRateDeltaPoints;
const [tokLo, tokHi] = r.agentBenchmark.tokenMultiple;

export default function HallbookPage() {
  return (
    <>
      <Hero />

      <Section id="no-setup" eyebrow="1" title="Nothing to install per skill, per agent"
        intro="Skills usually travel as folders: find one, copy it into each agent's skills directory, restart the agent, and do it again for the next skill, the next agent and the next machine. Hallbook replaces all of that with one connection.">
        <ConnectDiagram />
        <p className="mt-6 max-w-[70ch] text-[15px] leading-[1.7]">
          Connect Aletheore once, as you would any MCP server, then ask your agent to use the skills. <Mark>Nothing to copy, no plugin to install, no restart, no setup per agent.</Mark>
        </p>
      </Section>

      <Section id="protocol" eyebrow="2" title="One call, then your agent moves on"
        intro="Agents lose time searching for the right skill, then searching again. Hallbook answers in a single call, and when it is not sure it says so, so the agent carries on without a wrong guide in its head. This is a recorded call against the real catalogue.">
        <Transcript />
        <p className="mt-5 flex flex-wrap items-center gap-2 font-display text-[12px] text-faint">
          {["~/.claude/skills", "~/.codex/skills", "~/.agents/skills"].map((f) => <span key={f} className="border border-dashed border-line-strong px-2.5 py-1 line-through decoration-stamp">{f}</span>)}
          <span className="ml-1">folders we never write to</span>
        </p>
      </Section>

      <Section id="custom" eyebrow="3" title="Your own skills live here too"
        intro={<>Write a skill once, or ask your agent to save what you just worked out, and it sits next to the catalogue. Every agent connected to Aletheore finds it the same way, so <Mark>there are no folders to keep in step between tools.</Mark></>}>
        <ol className="relative grid gap-10 md:grid-cols-3 md:gap-8">
          <span aria-hidden="true" className="absolute left-0 right-0 top-[22px] hidden border-t border-dashed border-line-strong md:block" />
          {[
            ["Add one from a file", "aletheore hallbook add my-skill ./my-skill.md", "Name it plainly. Custom skills are found by name, and the original is kept as a .bak if you add it again."],
            ["Let your agent save it", "Save what we just figured out as a skill called deploy-checklist.", "The agent calls aletheore_add_custom_skill. Next session, any agent can find it."],
            ["Take it with you", "aletheore login", "Sign in once and your custom skills follow your account to your other machines."],
          ].map(([t, cmd, note], i) => (
            <li key={t} className="hb-step relative bg-paper">
              <span className="hb-step-n" aria-hidden="true">{i + 1}</span>
              <h3 className="font-display text-[16px] font-bold leading-[1.25]">{t}</h3>
              <pre className="mt-3 overflow-x-auto whitespace-pre-wrap rounded-[3px] bg-ink px-3 py-2.5 font-display text-[12px] leading-[1.6] text-[#e8e2d4]">{cmd}</pre>
              <p className="mt-3 text-[13px] leading-[1.6] text-muted">{note}</p>
            </li>
          ))}
        </ol>
        <p className="mt-5 max-w-[68ch] text-[13px] leading-[1.7] text-faint">Custom skills show up in search and in the one-call lookup next to the {hallbook.stats.total.toLocaleString("en-US")} catalogue ones, with ids like custom/deploy-checklist. They are stored on your machine, and are only sent to your account if you sign in.</p>
      </Section>

      <Section id="shelves" eyebrow="4" title="Six shelves" intro="Chosen for work that agents actually do. Counts are read from the catalogue.">
        <BarList rows={shelfRows} max={maxShelf} caption="Skills per shelf" />
      </Section>

      <Section id="vetting" eyebrow="5" title="What we check before a skill ships"
        intro="A skill is instructions your agent will follow, so a bad one is a real risk. This is a real skill file from the catalogue. The list is what our checks cover, and where they stop.">
        <div className="grid gap-9 lg:grid-cols-[1.25fr_1fr]">
          <AnnotatedSkill />
          <ol className="grid content-start gap-5 text-[14px] leading-[1.6] text-muted">
            {[
              ["Pinned source", "The exact repository and commit it came from, and the author is credited. Nothing is fetched from the network at read time."],
              ["Its own licence", "Each skill carries its licence. Only permissive ones (MIT, Apache-2.0, BSD, ISC and similar) are accepted."],
              ["Instruction pattern lint", "Instruction text is matched against two known-bad shapes: a download piped straight into a shell (curl or wget into sh, bash and similar), and phrasing that asks to send a secret or credential to a URL. It is a lint, not a general prompt-injection detector, and a clean result is not a security review."],
              ["Curation record and bundled scripts", "Shelf, quality score and the vetting result sit next to the skill. A bundled scripts/ folder is run through our secrets scanner, and only that: the pattern lint above reads the instruction text, not the scripts. A skill that fails any check is held back, and a person reviews each finding before it is cleared."],
            ].map(([t, b], i) => (
              <li key={t} className="relative pl-10">
                <span className="absolute left-0 top-0 grid size-[26px] place-items-center rounded-full bg-stamp font-display text-[12px] font-bold text-paper">{i + 1}</span>
                <b className="font-display text-[15px] text-ink">{t}</b><p className="mt-0.5">{b}</p>
              </li>
            ))}
          </ol>
        </div>
        <dl className="mt-10 grid gap-8 border-t border-dashed border-line-strong pt-7 sm:grid-cols-3">
          {[[String(r.riskyHits.value), "hits from a broader curation-time scan for override phrases, piped shell commands and hidden characters. A model classified all of them as documentation or benign, and none as suspicious."],
            [String(r.heldBack.value), "candidates held back by the scanner, then reviewed one by one. Most were false alarms, one was dropped for phoning home."],
            [String(r.writesToAgentFolders.value), "skills that write into your agent's own folders."]].map(([n, t]) => (
            <div key={n}><dt className="font-display text-[44px] font-bold leading-none text-stamp">{n}</dt><dd className="mt-2 max-w-[34ch] text-[13px] leading-[1.6] text-muted">{t}</dd></div>
          ))}
        </dl>
      </Section>

      <Section id="receipts" eyebrow="6" title="Receipts" intro="We measured it, including the parts that did not go our way. These are our own tests on this catalogue, October 2026.">
        <div className="grid gap-12">
          <div><h3 className="mb-1 font-display text-[16px] font-bold">Finding the right skill from a vague request</h3>
            <p className="mb-4 text-[13px] text-muted">{r.heldoutQueries.value} requests written by a different model, in the voice of someone who does not know the skill names.</p>
            <BarList rows={findRows} caption="Finding the right skill from a vague request" /></div>
          <div><h3 className="mb-1 font-display text-[16px] font-bold">When it says &ldquo;high&rdquo;, it is right</h3>
            <p className="mb-4 text-[13px] text-muted">The lookup only hands over a skill when confident. Otherwise it gives short candidates, or says nothing fits.</p>
            <BarList rows={preciseRows} caption="Precision of a confident answer" /></div>
          <div><h3 className="mb-1 font-display text-[16px] font-bold">Getting your agent to use it</h3>
            <p className="mb-4 text-[13px] text-muted">Share of runs that called Hallbook. &ldquo;Say it&rdquo; means one line in your prompt: <b className="text-ink">Use the Aletheore skills available to you.</b></p>
            <BarList rows={useRows} caption="Share of runs that used Hallbook" /></div>
          <aside className="relative border border-ink bg-raised p-6">
            <span className="absolute -top-2.5 left-5 bg-paper px-2 font-display text-[11px] font-bold tracking-[0.16em] text-stamp">READ THIS</span>
            <p className="max-w-[72ch] text-[14.5px] leading-[1.7]"><b>Do skills make an agent score higher?</b> We tested five ways of delivering skills on {r.agentBenchmark.tasks} engineering tasks with hidden tests. Pass rates moved between minus {Math.abs(deltaLo)} and plus {deltaHi} points, all within noise, and skills used {tokLo} to {tokHi} times the tokens. <Mark>We do not claim a score gain on tasks a strong model already knows.</Mark></p>
            <p className="mt-3 max-w-[72ch] text-[14.5px] leading-[1.7] text-muted">What we do claim is access: <Mark>curated skills, offline, one call, and an honest answer when nothing fits.</Mark> Published research finds the big gains come from skills that carry knowledge a model lacks, such as a specific product or procedure. That is what we test next.</p>
          </aside>
        </div>
      </Section>

      <section id="install" className="mt-10 bg-ink py-16 text-[#e8e2d4]">
        <div className="mx-auto max-w-[1080px] px-8">
          <p className="font-display text-[12.5px] text-[#e8e2d499]">Use it</p>
          <h2 className="mt-3 max-w-[24ch] text-balance font-display text-[clamp(26px,3.6vw,40px)] font-bold leading-[1.1] tracking-[-0.02em]">Install it. Then just ask your agent to use the skills.</h2>
          <p className="mb-1 mt-8 font-display text-[11.5px] tracking-[0.08em] text-[#e8e2d490]">1 · INSTALL AND START THE SERVER</p>
          <Command text="pip install aletheore" /><Command text="aletheore mcp ." />
          <p className="mb-1 mt-6 font-display text-[11.5px] tracking-[0.08em] text-[#e8e2d490]">2 · SAY IT</p>
          <Command text="Use the Aletheore skills available to you for this task." />
          <p className="mb-1 mt-6 font-display text-[11.5px] tracking-[0.08em] text-[#e8e2d490]">3 · OR MAKE IT AUTOMATIC (CLAUDE CODE PROMPT HOOK)</p>
          <Command text="aletheore hallbook hook" />
          <p className="mt-6 max-w-[64ch] text-[13px] leading-[1.7] text-[#e8e2d4a0]">Built on MCP, so any MCP agent can call it. Tested with OpenCode so far. Your own skills can be added once and used everywhere. Every skill credits its author and links to its source.</p>
        </div>
      </section>
    </>
  );
}
