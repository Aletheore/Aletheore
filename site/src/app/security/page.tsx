import type { Metadata } from "next";
import { LegalPage } from "@/components/site/legal-page";
import legal from "@/data/legal.json";

export const metadata: Metadata = {
  title: "Security",
  description: "How Aletheore handles your source code, what data it stores, and how to report a security issue.",
  alternates: { canonical: "/security" },
};

export default function Page() {
  const p = legal.security;
  return <LegalPage title={p.title} updated={p.updated} html={p.html} />;
}
