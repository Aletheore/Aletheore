import type { Metadata } from "next";
import { LegalPage } from "@/components/site/legal-page";
import legal from "@/data/legal.json";

export const metadata: Metadata = {
  title: "Terms of Service",
  description: "Terms of service for the Aletheore CLI and the paid GitHub App plans, Flash and AIR.",
  alternates: { canonical: "/terms" },
};

export default function Page() {
  const p = legal.terms;
  return <LegalPage title={p.title} updated={p.updated} html={p.html} />;
}
