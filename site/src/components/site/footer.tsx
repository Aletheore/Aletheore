import Link from "next/link";
import { footerColumns, footerNote, prefetchFor } from "@/data/site-links";
import { Logo } from "./logo";

export function Footer() {
  return (
    <footer className="mt-5 border-t border-line pb-10 pt-12">
      <div className="mx-auto grid max-w-[1080px] gap-8 px-8 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1fr_1fr_1fr]">
        <div>
          <Logo />
          <p className="mt-3 max-w-[30ch] text-[13px] leading-[1.55] text-faint">{footerNote}</p>
          <a
            href="https://mcpvault.io/servers/aletheore/health?utm_source=external_badge&utm_medium=referral&utm_campaign=mcp_health_report"
            rel="noopener"
            className="mt-4 inline-block"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src="https://mcpvault.io/badge/aletheore.svg?theme=dark" alt="MCPVault: claimed" height={32} width={197} className="h-8 w-auto" />
          </a>
        </div>
        {footerColumns.map((col) => (
          <div key={col.title}>
            <h3 className="mb-3.5 text-xs uppercase tracking-[0.04em] text-faint">{col.title}</h3>
            {col.links.map((l) =>
              l.external ? (
                <a key={l.href} href={l.href} rel="noopener" className="mb-2 block text-[13.5px] text-muted hover:text-ink">{l.label}</a>
              ) : (
                <Link key={l.href} href={l.href} prefetch={prefetchFor(l.href)} className="mb-2 block text-[13.5px] text-muted hover:text-ink">{l.label}</Link>
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
