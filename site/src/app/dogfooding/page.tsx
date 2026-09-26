import type { Metadata } from "next";
import { PageHead } from "@/components/site/page-head";
import { Callout } from "@/components/site/prose";
import { Section } from "@/components/site/section";
import { StatRow } from "@/components/site/stat-row";
import { Terminal } from "@/components/site/terminal";
import { Button } from "@/components/ui/button";

export const metadata: Metadata = {
  title: "What Aletheore finds in real code",
  description:
    "We ran aletheore scan against Flask and Express with no cherry-picking. Real CVEs, real dead code, real hotspots, real endpoint maps, with every number independently verifiable.",
  alternates: { canonical: "/dogfooding" },
};

const code = "font-display text-[0.92em] text-ink";
const Code = ({ children }: { children: React.ReactNode }) => <code className={code}>{children}</code>;
const A = ({ href, children }: { href: string; children: React.ReactNode }) => (
  <a className="text-ink underline underline-offset-2" href={href} rel="noopener">{children}</a>
);
const H3 = ({ children }: { children: React.ReactNode }) => <h3 className="mt-10 font-display text-[16px] font-bold leading-snug">{children}</h3>;
const P = ({ children, className = "" }: { children: React.ReactNode; className?: string }) => (
  <p className={`mt-3 max-w-[72ch] text-[14px] leading-[1.75] text-muted ${className}`}>{children}</p>
);
const Pre = ({ children }: { children: string }) => (
  <pre className="mt-4 max-w-[80ch] overflow-x-auto rounded-[4px] border border-line bg-raised p-4 font-display text-[12px] leading-[1.7]"><code>{children}</code></pre>
);

export default function DogfoodingPage() {
  return (
    <>
      <PageHead eyebrow="Dogfooding, not marketing copy" title="We pointed Aletheore at real code. Here's exactly what came back.">
        <p>
          No cherry-picking, no staged examples: the full output of one command, run against two well-known open source projects at the commits
          listed at the bottom. Every number below is independently verifiable: clone the same repo at the same commit, run the same command,
          get the same answer.
        </p>
      </PageHead>

      <div className="mx-auto max-w-[1080px] px-8">
        <div className="max-w-[34rem]">
          <Terminal
            terms={[{
              id: "ran",
              label: "What we ran",
              copy: "pipx install aletheore\ngit clone https://github.com/pallets/flask.git\naletheore scan flask",
              lines: [
                { text: "pipx install aletheore", kind: "cmd" },
                { text: "git clone https://github.com/pallets/flask.git", kind: "cmd" },
                { text: "aletheore scan flask", kind: "cmd" },
              ],
            }]}
          />
        </div>
      </div>

      <Section id="flask" eyebrow="Flask · Python" title="5,545 commits. 83 files. 18.3k lines.">
        <StatRow
          stats={[
            { value: "5,545", label: "commits scanned" },
            { value: "83", label: "files" },
            { value: "18.3k", label: "lines" },
          ]}
        />
        <P className="mt-6">
          Scan finished in about 11 seconds, most of that being 6 real network round-trips (one per pinned dependency) to OSV.dev and the PyPI
          license registry, not local parsing. Skip both with <Code>--no-check-vulnerabilities --no-check-licenses</Code> and the same scan drops
          under 4 seconds.
        </P>

        <H3>17 advisories, on 3 dependencies, with exact installed versions</H3>
        <P>From a single OSV.dev lookup per pinned package, no LLM guessing.</P>
        <div className="mt-4 max-w-[820px] overflow-x-auto border border-line bg-raised" role="region" aria-label="Flask advisories by dependency" tabIndex={0}>
          <table className="w-full border-collapse text-left text-[13px]">
            <thead>
              <tr className="border-b border-line bg-paper font-display text-[11.5px] text-muted">
                <th scope="col" className="px-4 py-2.5 font-semibold">Package</th>
                <th scope="col" className="px-4 py-2.5 font-semibold">Installed</th>
                <th scope="col" className="px-4 py-2.5 font-semibold">Advisories</th>
              </tr>
            </thead>
            <tbody className="align-top">
              <tr className="border-b border-dashed border-line">
                <td className="px-4 py-3"><Code>jinja2</Code></td>
                <td className="px-4 py-3"><Code>3.1.2</Code></td>
                <td className="px-4 py-3 text-muted">10: sandbox breakout via <Code>attr</Code>/format method, HTML attribute injection via <Code>xmlattr</Code></td>
              </tr>
              <tr className="border-b border-dashed border-line">
                <td className="px-4 py-3"><Code>werkzeug</Code></td>
                <td className="px-4 py-3"><Code>3.1.0</Code></td>
                <td className="px-4 py-3 text-muted">6: <Code>safe_join()</Code> Windows device-name handling</td>
              </tr>
              <tr>
                <td className="px-4 py-3"><Code>click</Code></td>
                <td className="px-4 py-3"><Code>8.1.3</Code></td>
                <td className="px-4 py-3 text-muted">1: command injection in <Code>click.edit()</Code></td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="mt-6 max-w-[820px]">
          <Callout title="Worth being precise here rather than impressive">
            <p>
              OSV.dev cross-lists the same underlying vulnerability under multiple advisory identifiers (a GHSA and a PYSEC ID, sometimes more),
              so those 17 raw entries are really <strong className="text-ink">8 distinct issues</strong> when grouped by identical advisory text.
              Compare <Code>GHSA-cpwx-vrp4-4pq7</Code> and <Code>PYSEC-2026-1471</Code>: same summary, same bug, two IDs. Aletheore reports OSV&apos;s
              raw advisory list rather than silently deduping it, so that nuance is visible in the JSON rather than hidden. Which is also why we are
              flagging it here instead of just quoting &quot;17&quot; and moving on.
            </p>
          </Callout>
        </div>
        <P>
          These are pinned dev/test dependency versions in this checkout, not a claim about what real Flask deployments run. But they are exactly
          what a <Code>pip install -r requirements-dev.txt</Code> would actually pull down, and every one of them is independently verifiable on{" "}
          <A href="https://osv.dev">osv.dev</A>.
        </P>

        <H3>Correct dead-code detection, with a stated reason for each</H3>
        <Pre>{`{
  "unreachable_modules": [
    { "path": "docs/conf.py", "reason": "no other module imports this file" },
    { "path": "examples/celery/make_celery.py", "reason": "no other module imports this file" }
  ]
}`}</Pre>
        <P>
          Both are real, legitimate &quot;dead code&quot; by the only definition a deterministic scanner can honestly claim: nothing in the repo imports
          them. Sphinx invokes <Code>docs/conf.py</Code> by convention, never by import. That is exactly the kind of thing a naive dead-code check
          gets wrong and Aletheore gets right, because it is checking imports, not guessing intent.
        </P>

        <H3>A real hotspot, with real co-change data</H3>
        <P>
          <Code>flask/app.py</Code>: 354 commits, most frequently changed alongside <Code>CHANGES</Code> (87×), <Code>flask/helpers.py</Code> (58×),
          and its own test file (41×). That is not a lint rule; it is git history, correctly attributed.
        </P>
        <P>
          A correctly detected repo license (<Code>BSD-3-Clause</Code>, read from <Code>pyproject.toml</Code>) and zero false-positive secret findings
          across the full working tree and git history.
        </P>
      </Section>

      <Section id="express" eyebrow="Express · JavaScript" title="6,158 commits. 141 files. 21.5k lines.">
        <StatRow
          stats={[
            { value: "6,158", label: "commits scanned" },
            { value: "141", label: "files" },
            { value: "21.5k", label: "lines" },
          ]}
        />
        <P className="mt-6">Scan finished in about six seconds, including a full git-history secrets sweep.</P>

        <H3>1,284 API endpoints mapped, statically, from source</H3>
        <P>No server started, no LLM call.</P>
        <Pre>{`{
  "method": "GET",
  "path": "/restricted",
  "framework": "express",
  "file": "examples/auth/index.js",
  "line": 88,
  "handler": "restrict"
}`}</Pre>
        <P>
          Every one of those 1,284 entries carries a real file and line number, extracted from Express&apos;s own route-registration calls across its
          examples and test suite.
        </P>

        <H3>One real, current CVE</H3>
        <P>
          <Code>body-parser@2.2.1</Code>:{" "}
          <A href="https://github.com/advisories/GHSA-v422-hmwv-36x6"><Code>GHSA-v422-hmwv-36x6</Code></A>, a denial-of-service when an invalid{" "}
          <Code>limit</Code> value silently disables size enforcement.
        </P>
        <P>
          Zero secret findings, zero layer-convention violations. Express does not declare a layered architecture convention, so Aletheore correctly
          reports <Code>convention_detected: false</Code> instead of inventing one.
        </P>
      </Section>

      <Section id="the-point" eyebrow="The point" title="None of this required an API key, an account, or an LLM call." className="pb-24">
        <div className="crop-marks border border-line bg-raised p-8">
          <P className="mt-0">
            The only network traffic was the vulnerability and license registry lookups above, and both are one flag away from off. It is the same
            evidence a <Code>pipx install aletheore &amp;&amp; aletheore scan .</Code> gets you on your own repo, right now, for free.
          </P>
          <P className="text-[12.5px] text-faint">
            Commits scanned: Flask{" "}
            <A href="https://github.com/pallets/flask/commit/6a2f545bfd8ed31e19066a299296917e034aca58"><Code>6a2f545</Code></A> (2026-07-30), Express{" "}
            <A href="https://github.com/expressjs/express/commit/a3714473feb3d2908add734d340e7755fd85e0a3"><Code>a371447</Code></A> (2026-07-27).
            Aletheore v0.7.0.
          </P>
          <div className="mt-6 flex flex-wrap gap-3">
            <Button asChild><a href="https://pypi.org/project/aletheore/" rel="noopener">pipx install aletheore</a></Button>
            <Button asChild variant="secondary"><a href="https://github.com/Aletheore/Aletheore" rel="noopener">Read the source</a></Button>
          </div>
        </div>
      </Section>
    </>
  );
}
