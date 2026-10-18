import type { Metadata } from "next";
import { PageHead } from "@/components/site/page-head";
import { ChangelogList } from "@/components/site/changelog-list";

export const metadata: Metadata = {
  title: "Changelog",
  description:
    "A dated history of customer-facing changes to Aletheore, including every security issue found and fixed, with how long each one took to fix.",
  alternates: { canonical: "/changelog" },
};

export default function ChangelogPage() {
  return (
    <>
      <PageHead eyebrow="Dated, not curated" title="What changed, including what went wrong.">
        <p>
          Customer-facing changes only - not a raw commit log. A security issue is only listed here once the fix is
          already live, never while it is still open. The &ldquo;Security&rdquo; tab is the same list filtered to
          those entries - nothing here is written only for that tab and left out of the full list.
        </p>
      </PageHead>
      <section className="mx-auto max-w-[1080px] px-8 pb-24">
        <ChangelogList />
      </section>
    </>
  );
}
