import type { Metadata } from "next";

export const siteMetadata: Metadata = {
  metadataBase: new URL("https://www.aletheore.com"),
  title: { default: "Aletheore | Evidence-Grounded Code Audits & GitHub Automation", template: "%s | Aletheore" },
  description:
    "Evidence-grounded GitHub audits: secrets, vulnerabilities, dead code, live endpoint monitoring, AIRview architecture maps, AI-generated Docs, and automated PR reviews.",
  // Google wants a square favicon in a multiple of 48 px, crawlable, ideally also at /favicon.ico.
  icons: {
    icon: [
      { url: "/favicon.ico", sizes: "48x48" },
      { url: "/favicon-48.png", sizes: "48x48", type: "image/png" },
      { url: "/icon-192.png", sizes: "192x192", type: "image/png" },
    ],
    apple: "/apple-touch-icon.png",
  },
  openGraph: { type: "website", siteName: "Aletheore", images: ["/logo-mark.png"] },
  twitter: { card: "summary", images: ["/logo-mark.png"] },
};
