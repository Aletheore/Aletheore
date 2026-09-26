import type { Metadata } from "next";

export const siteMetadata: Metadata = {
  metadataBase: new URL("https://www.aletheore.com"),
  title: { default: "Aletheore | Evidence-Grounded Code Audits & GitHub Automation", template: "%s | Aletheore" },
  description:
    "Evidence-grounded GitHub audits: secrets, vulnerabilities, dead code, live endpoint monitoring, AIRview architecture maps, AI-generated Docs, and automated PR reviews.",
  icons: { icon: "/logo-mark.png" },
  openGraph: { type: "website", siteName: "Aletheore", images: ["/logo-mark.png"] },
  twitter: { card: "summary", images: ["/logo-mark.png"] },
};
