import type { Metadata } from "next";
import { LegalPage } from "@/components/site/legal-page";
import legal from "@/data/legal.json";

export const metadata: Metadata = {
  title: "Refund Policy",
  description: "How refunds work for Aletheore purchases, which are handled by Paddle as merchant of record.",
  alternates: { canonical: "/refund" },
};

export default function Page() {
  const p = legal.refund;
  return <LegalPage title={p.title} updated={p.updated} html={p.html} />;
}
