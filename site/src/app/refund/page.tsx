import type { Metadata } from "next";
import { LegalPage } from "@/components/site/legal-page";
import legal from "@/data/legal.json";

export const metadata: Metadata = {
  title: "Refund Policy",
  description: "Aletheore's 14-day refund window and how to request one.",
  alternates: { canonical: "/refund" },
};

export default function Page() {
  const p = legal.refund;
  return <LegalPage title={p.title} updated={p.updated} html={p.html} />;
}
