import type { Metadata } from "next";
import { BarList, type BarRow } from "@/components/site/bar-list";
import { DataTable } from "@/components/site/data-table";
import { Disclosure } from "@/components/site/disclosure";
import { PageHead } from "@/components/site/page-head";
import { Callout, FreshBadge, Prose } from "@/components/site/prose";
import { Section } from "@/components/site/section";
import { StatRow } from "@/components/site/stat-row";
import { Terminal } from "@/components/site/terminal";
import { Button } from "@/components/ui/button";
import * as B from "@/data/benchmarks";
import { asRecords, num, T } from "@/data/benchmarks";

export const metadata: Metadata = {
  title: "Benchmarks: measured in the open",
  description:
    "Aletheore against RepoWise, Graphify and the leading PR reviewers on locating code, PR review, comprehension and scanner accuracy. Every number is reproducible from the public aletheore-benchmarks repo, and we publish the rows we lose too.",
  alternates: { canonical: "/benchmarks" },
};

const pct = (s: string) => num(s.split(" ")[0]);
const ours = (label: string) => label.startsWith("Aletheore");
const short = (label: string) => label.replace(", shared context", "").replace(", no shared context (before)", ", before the fix");

function recallRows(): BarRow[] {
  return T.prRecall.rows.slice(0, 5).map((r) => ({ label: short(r[0]), value: pct(r[2]), display: r[2].split(" ")[0], ours: ours(r[0]) }));
}
function precisionRows(): BarRow[] {
  return [...T.prRecall.rows.slice(0, 5), T.prRecall.rows[5]]
    .map((r) => ({ label: short(r[0]), value: pct(r[3]), display: r[3].split(" ")[0], ours: ours(r[0]) }))
    .sort((a, b) => b.value - a.value);
}
function accurateRows(): BarRow[] {
  return T.prRecall.rows.slice(0, 5).map((r) => ({ label: short(r[0]), value: num(r[4]), display: r[4], ours: ours(r[0]) }));
}
function sweRows(): BarRow[] {
  return T.swePr.rows.map((r) => {
    const range = r[1].includes(" - ");
    const [lo, hi] = range ? r[1].split(" - ").map(Number) : [Number(r[1]), Number(r[1])];
    return { label: r[0].replace("Aletheore (GLM-5.3-Flash, per_file_completeness=True)", "Aletheore, per-file").replace("Aletheore (GLM-5.3-Flash, bare mode)", "Aletheore, bare mode"), value: (lo + hi) / 2, display: range ? `${lo} to ${hi}` : r[1], ours: ours(r[0]) };
  });
}
function locatingRows(): BarRow[] {
  return T.locating.rows.map((r) => ({
    label: `${r[0]} (${r[1]})`,
    value: pct(r[2]),
    display: r[2],
    ours: true,
    other: { label: "RepoWise, best mode", value: Math.max(pct(r[3]), pct(r[4])), display: `${Math.max(pct(r[3]), pct(r[4])).toFixed(1)}%` },
  }));
}
function comprehensionRows(): BarRow[] {
  return T.comprehension.rows.map((r) => ({
    label: r[1] ? `${r[0]} (${r[1]})` : "Average",
    value: num(r[2]),
    display: r[2],
    ours: true,
    other: { label: "RepoWise", value: num(r[3]), display: r[3] },
  }));
}

export default function BenchmarksPage() {
  return (
    <>
      <PageHead eyebrow={B.head.eyebrow} title={B.head.title}>
        <p>{B.head.intro}</p>
      </PageHead>

      <div className="mx-auto max-w-[1080px] px-8">
        <Terminal terms={[{ id: "repro", label: "Reproduce it yourself", copy: B.head.reproduce, lines: B.head.reproduce.split("\n").map((text) => ({ text, kind: "cmd" as const })) }]} />
      </div>

      <nav aria-label="Sections" className="sticky top-[76px] z-10 mt-10 border-y border-line bg-paper/90 backdrop-blur">
        <ul className="mx-auto flex max-w-[1080px] gap-1 overflow-x-auto px-8 py-2 font-display text-[12px]">
          {B.toc.map((t) => (
            <li key={t.id} className="flex-none">
              <a href={`#${t.id}`} className="block rounded-[3px] px-2.5 py-1.5 text-muted hover:bg-raised hover:text-ink focus-visible:outline-2 focus-visible:outline-stamp">
                {t.label}
                {t.fresh ? <span className="ml-1 text-stamp" aria-label="new">*</span> : null}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      <Section id="pr-review-head-to-head" eyebrow={B.prReview.eyebrow} title={B.prReview.title} intro={B.prReview.intro}>
        <div className="grid gap-10 md:grid-cols-2">
          <div>
            <BarList caption="Recall: bugs actually caught, of 44" rows={recallRows()} />
            <p className="mt-3 text-[12px] leading-[1.6] text-faint">{B.prReview.recallNote}</p>
          </div>
          <div>
            <BarList caption="Precision: confirmed-accurate findings as a share of everything flagged" rows={precisionRows()} />
            <p className="mt-3 text-[12px] leading-[1.6] text-faint">{B.prReview.precisionNote}</p>
          </div>
        </div>
        <div className="mt-10">
          <BarList caption="Accurate findings per run, in absolute terms, not a rate" rows={accurateRows()} max={100} />
        </div>
        <div className="mt-10"><DataTable caption="Full PR review comparison with intervals" {...asRecords(T.prRecall)} oursKey="Aletheore" /></div>
        <div className="mt-8 grid gap-6 lg:grid-cols-2">
          <Callout title={B.prReview.gap.title} tone="warn">{B.prReview.gap.body}</Callout>
          <div>
            <h3 className="mb-3 font-display text-[13.5px] font-bold">{B.prReview.pairwise.title}</h3>
            <DataTable
              caption="Pairwise differences with 95% intervals"
              columns={[{ key: "a", label: "Comparison" }, { key: "b", label: "Recall", align: "right" }, { key: "c", label: "Precision", align: "right" }]}
              rows={B.prReview.pairwise.rows.map(([a, b, c]) => ({ a, b, c }))}
            />
            <p className="mt-3 text-[12px] leading-[1.6] text-faint">{B.prReview.pairwise.note}</p>
          </div>
        </div>
        <div className="mt-8 grid gap-6 lg:grid-cols-3">
          {B.prReview.read.map((r) => (<Callout key={r.title} title={r.title}>{r.body}</Callout>))}
        </div>
        <Prose className="mt-8 text-[12.5px] text-faint">{B.prReview.notShown}</Prose>
        <p className="mt-3 text-[12.5px]"><a className="text-ink underline" href={B.prReview.fullHref} rel="noopener">Full method, limitations and the reproduce script, Experiment 8 &rarr;</a></p>
      </Section>

      <Section id="swe-prbench" eyebrow={B.swe.eyebrow} title={B.swe.title} intro={B.swe.intro}>
        <p className="mb-6 flex flex-wrap gap-x-5 gap-y-1 text-[12.5px]">
          {B.swe.links.map((l) => (<a key={l.href} className="text-ink underline" href={l.href} rel="noopener">{l.label}</a>))}
        </p>
        <div className="grid gap-10 lg:grid-cols-[1.1fr_0.9fr]">
          <BarList caption="Overall score on the published eval_100 split, diff-only (higher is better)" rows={sweRows()} max={0.2} />
          <ul className="flex flex-col gap-3 text-[13.5px] leading-[1.65] text-muted">
            {B.swe.read.map((r) => (<li key={r} className="flex gap-2.5"><span className="mt-[9px] size-1.5 flex-none bg-stamp" aria-hidden="true" /><span>{r}</span></li>))}
          </ul>
        </div>
        <div className="mt-8"><DataTable caption="SWE-PRBench scores and generation cost" {...asRecords(T.swePr)} oursKey="Aletheore" /></div>
        <p className="mt-2 text-[12px] text-faint">*Competitor costs are priced at Aletheore&apos;s bare-mode token volume for the same 100 tasks; per-file mode makes more calls, which is why our own figure is higher there.</p>
        <div className="mt-8 grid gap-6 lg:grid-cols-2">
          <div>
            <h3 className="mb-3 font-display text-[13.5px] font-bold">Bare mode against per-file completeness</h3>
            <DataTable caption="Bare mode against per-file completeness" {...asRecords(T.perFile)} />
          </div>
          <Callout title="Read it honestly" tone="warn">
            <ul className="flex flex-col gap-2">{B.swe.caveats.map((c) => (<li key={c}>{c}</li>))}</ul>
          </Callout>
        </div>
        <p className="mt-4 text-[12.5px]"><a className="text-ink underline" href={B.swe.href} rel="noopener">Method, every caveat and the per-task data &rarr;</a></p>
      </Section>

      <Section id="compact-context" eyebrow={B.compact.eyebrow} title={B.compact.title} intro={B.compact.intro}>
        <DataTable caption="Verified-accept rate by arm, three runs" {...asRecords(T.compactRuns)} highlightFirstCol />
        <Prose className="mt-3 text-[12.5px] text-faint">{B.compact.runsNote}</Prose>
        <h3 className="mb-3 mt-10 font-display text-[13.5px] font-bold">How much smaller is the prompt, actually?</h3>
        <DataTable caption="Prompt size and cost by arm" {...asRecords(T.compactSize)} />
        <Prose className="mt-3 text-[12.5px] text-faint">{B.compact.sizeNote}</Prose>
        <p className="mt-4 text-[13.5px] font-semibold">{B.compact.result}</p>
      </Section>

      <Section id="locating-code" eyebrow={B.locating.eyebrow} title={B.locating.title} intro={B.locating.intro}>
        <StatRow stats={B.locating.stats} />
        <div className="mt-10"><BarList caption="Top-1 accuracy: Aletheore against RepoWise's best mode, all 7 shared corpora" rows={locatingRows()} /></div>
        <Prose className="mt-4 text-[12.5px] text-faint">{B.locating.after}</Prose>
        <div className="mt-8"><DataTable caption="Locating code, top-1, by corpus" {...asRecords(T.locating)} /></div>
        <Prose className="mt-6">{B.locating.vocab}</Prose>
        <div className="mt-4"><DataTable caption="General against vocabulary phrasing" {...asRecords(T.vocabulary)} /></div>
        <Prose className="mt-4 text-[12.5px] text-faint">{B.locating.latency}</Prose>
        <div className="mt-8"><DataTable caption="Indexing cost" {...asRecords(T.indexCost)} /></div>
        <div className="mt-8">
          <Disclosure summary="Full retrieval matrix: top-1, top-3, top-5 and MRR, 12 corpora, 23 regime rows">
            <Prose className="mb-4 text-[12.5px]">{B.locating.matrixNote}</Prose>
            <DataTable caption="Full retrieval matrix" {...asRecords(T.matrix)} />
          </Disclosure>
        </div>
      </Section>

      <Section id="graphify" eyebrow={B.graphify.eyebrow} title={B.graphify.title} intro={<>{B.graphify.intro} <FreshBadge /></>}>
        <div className="grid gap-10 lg:grid-cols-2">
          <BarList
            caption="Key-fact coverage on 15 ERPNext questions (mean of 30 samples)"
            rows={T.graphify.rows.map((r) => ({ label: r[0].replace(" (grep + read + list only)", ""), value: pct(r[1]), display: r[1], ours: r[0] === "+ Aletheore" }))}
          />
          <BarList
            caption="Tokens per query (mean of 15 samples, lower is better)"
            max={20000}
            rows={T.graphify.rows.map((r) => ({ label: r[0].replace(" (grep + read + list only)", ""), value: num(r[2]), display: r[2], ours: r[0] === "+ Aletheore" }))}
          />
        </div>
        <ul className="mt-8 flex max-w-[76ch] flex-col gap-3 text-[13.5px] leading-[1.65] text-muted">
          {B.graphify.read.map((r) => (<li key={r} className="flex gap-2.5"><span className="mt-[9px] size-1.5 flex-none bg-stamp" aria-hidden="true" /><span>{r}</span></li>))}
        </ul>
        <p className="mt-4 text-[12.5px]"><a className="text-ink underline" href={B.graphify.href} rel="noopener">Method, both corrections made before publishing, and the setup-time comparison &rarr;</a></p>
      </Section>

      <Section id="comprehension" eyebrow={B.comprehension.eyebrow} title={B.comprehension.title} intro={<>{B.comprehension.intro} <FreshBadge /></>}>
        <BarList caption="Blind-judge score, 0 to 3, per corpus (higher is better)" max={3} rows={comprehensionRows()} />
        <Prose className="mt-6">{B.comprehension.note}</Prose>
        <p className="mt-3 text-[12.5px]"><a className="text-ink underline" href={B.comprehension.href} rel="noopener">The full history, including a fix that shipped and sat undetected for months &rarr;</a></p>
      </Section>

      <Section id="deterministic-vs-llm" eyebrow={B.detVsLlm.eyebrow} title={B.detVsLlm.title} intro={<>{B.detVsLlm.intro} <FreshBadge /></>}>
        <DataTable caption="Deterministic analysis against a bare LLM on the same Flask data" {...asRecords(T.detVsLlm)} highlightFirstCol />
        <p className="mt-4 text-[12.5px]"><a className="text-ink underline" href={B.detVsLlm.href} rel="noopener">Methodology and known limitations &rarr;</a></p>
      </Section>

      <Section id="scanner-accuracy" eyebrow={B.scanners.eyebrow} title={B.scanners.title} intro={<>{B.scanners.intro} <FreshBadge /></>}>
        <div className="grid gap-px border border-line bg-line md:grid-cols-2">
          {B.scanners.cards.map((c, i) => (
            <article key={c.name} className={`bg-raised p-6 ${i === 0 ? "md:col-span-2" : ""}`}>
              <h3 className="font-display text-[15px] font-bold">{c.name}</h3>
              <dl className="mt-3 grid gap-3 text-[13px] leading-[1.65] md:grid-cols-[7rem_1fr]">
                <dt className="font-display text-[11.5px] uppercase tracking-[0.05em] text-faint">Pilot</dt>
                <dd className="text-muted">{c.pilot}</dd>
                <dt className="font-display text-[11.5px] uppercase tracking-[0.05em] text-stamp">Real repos</dt>
                <dd className="text-muted">{c.real}</dd>
              </dl>
            </article>
          ))}
        </div>
        <p className="mt-4 text-[12.5px]"><a className="text-ink underline" href={B.scanners.href} rel="noopener">Each corpus&apos;s full case tables and root-cause write-ups &rarr;</a></p>
      </Section>

      <Section id="hosted-embeddings" eyebrow={B.embeddings.eyebrow} title={B.embeddings.title} intro={B.embeddings.intro}>
        <Prose>{B.embeddings.summary}</Prose>
        <div className="mt-6">
          <Disclosure summary="All 23 corpus and regime rows: nomic against jina">
            <DataTable caption="Local nomic against hosted jina embeddings" {...asRecords(T.jina)} />
          </Disclosure>
        </div>
        <Prose className="mt-4 text-[12.5px] text-faint">{B.embeddings.update}</Prose>
      </Section>

      <Section id="toon" eyebrow={B.toon.eyebrow} title={B.toon.title}>
        <div className="grid gap-px border border-line bg-line sm:grid-cols-3">
          {B.toon.stats.map((s) => (
            <div key={s.label} className="bg-raised p-5"><div className="font-display text-[28px] font-bold tabular-nums">{s.value}</div><div className="mt-1 text-[12.5px] text-muted">{s.label}</div></div>
          ))}
          <div className="bg-raised p-5"><div className="font-display text-[34px] font-bold text-stamp">{B.toon.headline}</div><div className="mt-1 text-[12.5px] text-muted">{B.toon.headlineLabel}, {B.toon.disk}</div></div>
        </div>
        <Prose className="mt-4 text-[12.5px] text-faint">{B.toon.note}</Prose>
      </Section>

      <Section id="where-we-lose" eyebrow={B.lose.eyebrow} title={B.lose.title}>
        <ul className="flex flex-col gap-3">
          {B.lose.items.map((t) => (
            <li key={t} className="flex gap-3 border border-line bg-raised p-4 text-[13.5px] leading-[1.65] text-muted">
              <span className="font-display text-stamp" aria-hidden="true">!</span>
              <span>{t}</span>
            </li>
          ))}
        </ul>
      </Section>

      <div className="mx-auto max-w-[1080px] px-8 pb-20 pt-6">
        <div className="crop-marks flex flex-col gap-6 border border-line bg-raised p-8 md:flex-row md:items-center md:justify-between">
          <div>
            <h2 className="max-w-[34ch] font-display text-[22px] font-bold leading-[1.15]">{B.closing.title}</h2>
            <p className="mt-3 max-w-[60ch] text-[14px] leading-[1.7] text-muted">{B.closing.body}</p>
          </div>
          <div className="flex flex-wrap gap-3">
            <Button asChild><a href={B.head.repoHref} rel="noopener">View aletheore-benchmarks on GitHub</a></Button>
            <Button asChild variant="secondary"><a href="/dogfooding">See a raw scan instead</a></Button>
          </div>
        </div>
      </div>
    </>
  );
}
