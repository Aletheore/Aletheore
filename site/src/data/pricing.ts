import type { TierKey } from "@/lib/pricing";

export type FeatureText = string | { parts: (string | { href: string; label: string })[]; mark?: "*" | "**" };

export type Plan = {
  id: "community" | TierKey;
  name: string;
  price: string;
  priceNote: string;
  desc: string;
  features: FeatureText[];
  flagship?: boolean;
  cta: { label: string; href?: string; subscribe?: TierKey };
};

const INSTALL = "https://github.com/apps/aletheore/installations/new";

export const pricingHead = {
  eyebrow: "Pricing",
  title: "Free where it can be. Paid where AI actually runs.",
  intro: "The deterministic scanner is free forever. You only pay once AI enters the picture: reviews, audits, docs.",
};

export const plans: Plan[] = [
  {
    id: "community",
    name: "Community",
    price: "$0",
    priceNote: "forever",
    desc: "Local-first. CLI, free GitHub Action, free GitHub App usage. Fully self-service.",
    features: [
      "Deterministic scan from the CLI.",
      "Targeted query and diff commands from existing evidence.",
      "Local dashboard, history, and MCP tools.",
      "GitHub Action PR comments for new secrets, vulnerabilities, and layer violations.",
      { parts: ["Free AI-powered PR reviews, no expiration planned."], mark: "*" },
    ],
    cta: { label: "Install free", href: INSTALL },
  },
  {
    id: "flash",
    name: "Flash",
    price: "$8",
    priceNote: "/month",
    desc: "Automatic PR reviews on every push. $5 of AI credit a month, about 400 reviews.",
    flagship: true,
    features: [
      "Everything in Community.",
      {
        parts: [
          "Automatic Flash reviews on every push to an open pull request, powered by $5 of AI credit each month. One model, evidence-grounded. On 13 real pull requests Flash caught about 53% of human-identified bugs with about 93% of its findings judged accurate, in the same range as other leading AI reviewers (small sample, wide intervals; method and limits on the ",
          { href: "/benchmarks#pr-review-head-to-head", label: "Benchmarks page" },
          ").",
        ],
        mark: "**",
      },
      "Low-credit email alert and a per-PR review history.",
      "Monthly billing only.",
    ],
    cta: { label: "Subscribe", subscribe: "flash" },
  },
  {
    id: "air",
    name: "AIR",
    price: "$29.99",
    priceNote: "/month",
    desc: "Up to 3 team seats, $18 of AI credit a month. Everything in Flash plus the managed dashboard.",
    features: [
      { parts: ["Everything in Flash, with $18 of AI credit each month shared across PR reviews, AIRview, Docs and managed audits."], mark: "**" },
      "Managed audits and AIRview, written by DeepSeek.",
      "AI-generated Docs for every public symbol: grounded in source, kept current automatically, nobody has to write one. Exportable as a single Markdown file.",
      "Slack / Teams alerts on new findings.",
      "Branch-protection Check Runs for new secrets.",
      "Endpoint health monitoring across multiple targets, with a public status API.",
      "+$6.99/mo per additional team member.",
    ],
    cta: { label: "Subscribe", subscribe: "air" },
  },
];

export const installNote = "Install it on your GitHub account or organization, then hit Subscribe above to activate Flash or AIR on that installation.";

export const credit = {
  eyebrow: "What the credit actually buys",
  title: "Same $1 = 1 credit. AIR just starts with more.",
  intro: "Credit is shared across everything AI touches: reviews, audits, Docs generation. It never expires once purchased; the monthly allotment resets each cycle.",
  meters: [
    { label: "Flash: $5/mo included", value: 5, display: "$5", note: "about 400 PR reviews" },
    { label: "AIR: $18/mo included", value: 18, display: "$18", note: "shared across reviews, AIRview, Docs and audits" },
  ],
  note: "Bars show dollars of monthly credit. Both plans can buy more credit any time; it never expires, and reviews pause (you get an email first) rather than overcharge you.",
};

export const footnotes = [
  {
    mark: "*",
    title: "Free AI Reviews:",
    body: "AI-powered PR reviews are free, built on the same evidence-only (compact) prompt as AIR. See the Benchmarks page for the methodology. Routed across multiple providers' free tiers so it isn't tied to any single company's quota; usage is globally rate-limited and subject to availability, and access may be adjusted if that changes.",
  },
  {
    mark: "**",
    title: "AI credit:",
    body: "Flash includes $5 and AIR includes $18 of AI credit each month, spent as reviews and builds run, with no fixed review limit. As a guide, $5 covers about 400 PR reviews on Flash. On AIR the $18 is shared across PR reviews, Docs, managed audits and AIRview: as a guide it covers about 400 PR reviews alongside typical use of the others, and more reviews if you use less of them, while heavy use of any one leaves less for the rest. Larger pull requests and repositories use more credit, smaller ones less. Figures are estimates from our measured average cost per review, not a guarantee.",
  },
];

export const proofChips = ["Source-grounded PR reviews", "Endpoint monitoring with file and line", "MCP tools for coding agents"];

export const enterprise = {
  eyebrow: "Enterprise",
  title: "Need something custom?",
  body: "A larger team, a custom seat or repo allotment, a procurement or security questionnaire, or billing terms the self-serve plans don't cover. Talk to us directly and we'll work out something that fits.",
  cta: { label: "Contact us", href: "mailto:support@aletheore.com?subject=Enterprise%20inquiry" },
};

export const installHref = INSTALL;
