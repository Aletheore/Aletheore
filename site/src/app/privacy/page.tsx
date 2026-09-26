import type { Metadata } from "next";
import { LegalPage } from "@/components/site/legal-page";
import legal from "@/data/legal.json";

export const metadata: Metadata = {
  title: "Privacy Policy",
  description: "How Aletheore handles your source code and derived evidence across the free CLI, the GitHub App and our LLM providers.",
  alternates: { canonical: "/privacy" },
};

export default function Page() {
  const p = legal.privacy;
  return <LegalPage title={p.title} updated={p.updated} html={p.html} />;
}
