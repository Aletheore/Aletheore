import type { Metadata, Viewport } from "next";
import { Geist, IBM_Plex_Mono } from "next/font/google";
import { WebsiteCompanion } from "@/components/site/website-companion";
import { Footer } from "@/components/site/footer";
import { Header } from "@/components/site/header";
import "./globals.css";
import { companionEnabled } from "@/lib/companion/enabled";
import { siteMetadata } from "@/lib/site-metadata";

const plexMono = IBM_Plex_Mono({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--font-plex-mono",
  display: "swap",
});
const geist = Geist({ subsets: ["latin"], variable: "--font-geist", display: "swap" });

export const metadata: Metadata = siteMetadata;

export const viewport: Viewport = { width: "device-width", initialScale: 1, themeColor: "#F7F5F2", colorScheme: "light" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" data-scroll-behavior="smooth" className={`${plexMono.variable} ${geist.variable}`}>
      <body suppressHydrationWarning>
        <Header />
        <main>{children}</main>
        <Footer />
        {/* Chat is off unless COMPANION_LIVE_ENABLED=1 and a provider key are set (lib/companion/enabled.ts). The
            flag is read at build time here, so changing it needs a redeploy, like every Vercel env change. */}
        <WebsiteCompanion live={companionEnabled()} />
      </body>
    </html>
  );
}
