import type { Metadata } from "next";
import { BarList } from "@/components/site/bar-list";
import { PageHead } from "@/components/site/page-head";
import { PlanCards } from "@/components/site/plan-cards";
import { Section } from "@/components/site/section";
import { Button } from "@/components/ui/button";
import ld from "@/data/jsonld.json";
import { credit, enterprise, footnotes, installHref, installNote, pricingHead, proofChips } from "@/data/pricing";

export const metadata: Metadata = {
  title: "Pricing",
  description:
    "Aletheore Community: free deterministic CLI scanning forever. Aletheore Flash ($8/mo): automatic PR reviews on $5 of AI credit a month (about 400 PRs). Aletheore AIR ($29.99/mo, $18 of AI credit a month): everything in Flash plus managed AI audits, AIRview, AI-generated Docs, live endpoint monitoring, and team seats.",
  alternates: { canonical: "/pricing" },
};

export default function PricingPage() {
  return (
    <>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: JSON.stringify(ld) }} />
      <PageHead eyebrow={pricingHead.eyebrow} title={pricingHead.title} centered>
        <p>{pricingHead.intro}</p>
      </PageHead>
      <div className="mx-auto max-w-[1080px] px-8">
        <PlanCards />
        <div className="mt-8 flex justify-center">
          <Button asChild variant="secondary"><a href={installHref} rel="noopener">Install the GitHub App</a></Button>
        </div>
        <p className="mx-auto mt-3 max-w-[60ch] text-center text-[12.5px] text-faint">{installNote}</p>
      </div>

      <Section id="credit" eyebrow={credit.eyebrow} title={credit.title} intro={credit.intro}>
        <BarList
          caption="Monthly AI credit included, in dollars"
          max={18}
          rows={credit.meters.map((m, i) => ({ label: m.label, value: m.value, display: m.display, note: m.note, ours: i === 1 }))}
        />
        <p className="mt-4 max-w-[68ch] text-[12.5px] leading-[1.6] text-faint">{credit.note}</p>
      </Section>

      <div className="mx-auto max-w-[1080px] px-8">
        <div className="rule-dashed flex flex-col gap-4 pt-8">
          {footnotes.map((f) => (
            <p key={f.mark} className="max-w-[86ch] text-[12.5px] leading-[1.65] text-muted">
              <sup className="mr-1 text-stamp">{f.mark}</sup>
              <strong className="text-ink">{f.title}</strong> {f.body}
            </p>
          ))}
          <p className="flex flex-wrap gap-x-6 gap-y-1 font-display text-[12px] text-faint">
            {proofChips.map((c) => <span key={c}>{c}</span>)}
          </p>
        </div>
      </div>

      <Section id="enterprise" eyebrow={enterprise.eyebrow} title={enterprise.title} className="pb-20">
        <div className="crop-marks flex flex-col gap-6 border border-line bg-raised p-8 md:flex-row md:items-center md:justify-between">
          <p className="max-w-[58ch] text-[14.5px] leading-[1.7] text-muted">{enterprise.body}</p>
          <Button asChild><a href={enterprise.cta.href}>{enterprise.cta.label}</a></Button>
        </div>
      </Section>
    </>
  );
}
