import type { NextConfig } from "next";

const PAGES = "pricing|developers|benchmarks|dogfooding|status|changelog|privacy|terms|refund|security";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // A stray package-lock.json in the home directory (outside this repo) was
  // making Turbopack infer the workspace root as the home directory itself,
  // which made it try to resolve/watch the whole home folder. Pinning the
  // root here to this package's own directory is the fix Next's own
  // Turbopack docs recommend for a misdetected root.
  turbopack: { root: process.cwd() },
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
