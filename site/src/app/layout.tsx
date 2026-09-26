import type { Metadata, Viewport } from "next";
import { Geist, IBM_Plex_Mono } from "next/font/google";
import { Footer } from "@/components/site/footer";
import { Header } from "@/components/site/header";
import "./globals.css";

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-plex-mono",
  display: "swap",
});
const geist = Geist({ subsets: ["latin"], variable: "--font-geist", display: "swap" });

export const metadata: Metadata = {
  metadataBase: new URL("https://www.aletheore.com"),
  title: { default: "Aletheore | Evidence-Grounded Code Audits & GitHub Automation", template: "%s | Aletheore" },
  description:
    "Evidence-grounded GitHub audits: secrets, vulnerabilities, dead code, live endpoint monitoring, AIRview architecture maps, AI-generated Docs, and automated PR reviews.",
  icons: { icon: "/logo-mark.png" },
  openGraph: { type: "website", siteName: "Aletheore", images: ["/logo-mark.png"] },
  twitter: { card: "summary", images: ["/logo-mark.png"] },
};

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#F7F5F2" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${plexMono.variable} ${geist.variable}`}>
      <body>
        <Header />
        <main>{children}</main>
        <Footer />
      </body>
    </html>
  );
}
