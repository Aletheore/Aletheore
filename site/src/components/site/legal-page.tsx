import { PageHead } from "@/components/site/page-head";

/** Renders a policy page. The HTML is our own committed copy, not user input. */
export function LegalPage({ title, updated, html }: { title: string; updated: string; html: string }) {
  return (
    <>
      <PageHead eyebrow={updated} title={title} />
      <article className="legal mx-auto max-w-[1080px] px-8 pb-24" dangerouslySetInnerHTML={{ __html: html }} />
    </>
  );
}
