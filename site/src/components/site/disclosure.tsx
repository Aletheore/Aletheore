export function Disclosure({ summary, children }: { summary: string; children: React.ReactNode }) {
  return (
    <details className="group border border-line bg-raised">
      <summary className="cursor-pointer list-none px-4 py-3 font-display text-[12.5px] font-semibold marker:hidden focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-stamp [&::-webkit-details-marker]:hidden">
        <span className="mr-2 inline-block text-stamp transition-transform group-open:rotate-90" aria-hidden="true">&#9656;</span>
        {summary}
      </summary>
      <div className="border-t border-dashed border-line-strong p-4">{children}</div>
    </details>
  );
}
