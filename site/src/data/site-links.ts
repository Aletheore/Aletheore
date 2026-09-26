export type SiteLink = { label: string; href: string; external?: boolean };

export const internalPaths = [
  "/", "/pricing", "/developers", "/benchmarks", "/dogfooding", "/status", "/privacy", "/terms", "/refund", "/security",
] as const;

/** Pages that exist in this app. Links to the rest are not prefetched (they would 404) until they ship. */
export const builtPaths: ReadonlySet<string> = new Set(["/", "/pricing", "/developers", "/benchmarks"]);
export const prefetchFor = (href: string): false | undefined => (builtPaths.has(href.split("#")[0]) ? undefined : false);

export const getStartedHref = "https://app.aletheore.com/";

export const navLinks: SiteLink[] = [
  { label: "Pricing", href: "/pricing" },
  { label: "Developers", href: "/developers" },
  { label: "Benchmarks", href: "/benchmarks" },
  { label: "Proof", href: "/dogfooding" },
  { label: "Status", href: "/status" },
  { label: "GitHub", href: "https://github.com/Aletheore/Aletheore", external: true },
];

export const followOptions = [
  { id: "linkedin", name: "LinkedIn", note: "Product updates and launch notes", href: "https://www.linkedin.com/company/aletheore" },
  { id: "instagram", name: "Instagram", note: "@aletheore, short demo videos", href: "https://www.instagram.com/aletheore/" },
] as const;

export const footerNote = "Evidence-grounded repository audits. Free for individuals, local-first.";

export const footerColumns: { title: string; links: SiteLink[] }[] = [
  {
    title: "Product",
    links: [
      { label: "Pricing", href: "/pricing" },
      { label: "Developers", href: "/developers" },
      { label: "Benchmarks", href: "/benchmarks" },
      { label: "GitHub", href: "https://github.com/Aletheore/Aletheore", external: true },
    ],
  },
  {
    title: "Community",
    links: [
      { label: "LinkedIn", href: "https://www.linkedin.com/company/aletheore", external: true },
      { label: "Sponsor", href: "https://github.com/sponsors/ArihantK15", external: true },
      { label: "Issues", href: "https://github.com/Aletheore/Aletheore/issues", external: true },
    ],
  },
  {
    title: "Legal",
    links: [
      { label: "Terms", href: "/terms" },
      { label: "Privacy", href: "/privacy" },
      { label: "Refund Policy", href: "/refund" },
      { label: "Security", href: "/security" },
    ],
  },
  { title: "Contact", links: [{ label: "support@aletheore.com", href: "mailto:support@aletheore.com", external: true }] },
];
