import type { NextConfig } from "next";

const PAGES = "pricing|developers|benchmarks|dogfooding|status|privacy|terms|refund|security";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
        ],
      },
    ];
  },
  async redirects() {
    return [
      { source: "/index.html", destination: "/", permanent: true },
      { source: `/:slug(${PAGES}).html`, destination: "/:slug", permanent: true },
    ];
  },
};

export default nextConfig;
