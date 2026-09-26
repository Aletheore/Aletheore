import Link from "next/link";
import { footerColumns, footerNote } from "@/data/site-links";
import { Logo } from "./logo";

export function Footer() {
  return (
    <footer className="mt-5 border-t border-line pb-10 pt-12">
      <div className="mx-auto grid max-w-[1080px] gap-8 px-8 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr_1fr]">
        <div>
          <Logo />
          <p className="mt-3 max-w-[30ch] text-[13px] leading-[1.55] text-faint">{footerNote}</p>
        </div>
        {footerColumns.map((col) => (
          <div key={col.title}>
            <h4 className="mb-3.5 text-xs uppercase tracking-[0.04em] text-faint">{col.title}</h4>
            {col.links.map((l) =>
              l.external ? (
                <a key={l.href} href={l.href} rel="noopener" className="mb-2 block text-[13.5px] text-muted hover:text-ink">{l.label}</a>
              ) : (
                <Link key={l.href} href={l.href} prefetch={false} className="mb-2 block text-[13.5px] text-muted hover:text-ink">{l.label}</Link>
              ),
            )}
          </div>
        ))}
      </div>
      <svg aria-hidden="true" viewBox="0 0 1080 200" className="mx-auto mt-12 block w-full max-w-[1080px] px-8 text-ink/80">
        <text
          x="0" y="170" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeDasharray="0.1 6"
          style={{ fontFamily: "var(--font-plex-mono), monospace", fontWeight: 700, fontSize: 196 }}
        >
          Aletheore
        </text>
      </svg>
    </footer>
  );
}
