import type { NextConfig } from "next";

const PAGES = "pricing|developers|benchmarks|dogfooding|status|changelog|privacy|terms|refund|security";

const nextConfig: NextConfig = {
  reactStrictMode: true,
  poweredByHeader: false,
  // A stray package-lock.json in the home directory (outside this repo) was
  // making Turbopack infer the workspace root as the home directory itself,
  // which made it try to resolve/watch the whole home folder. Pinning the
  // root here is the fix Next's own Turbopack docs recommend for a
  // misdetected root. process.cwd() is the directory `next` is run from, so
  // this is only right when it is run from site/ (the Vercel project's root
  // directory, and `npm run dev` / `npm run build` inside site/); running it
  // from the repo root would pin the wrong directory.
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
  // Local review only: the Hallbook demo asks the real Python ranking (python3 docs/skill-hub-curation/site-local/server.py).
  // In production there is no such server, so the page falls back to its recorded examples.
  async rewrites() {
    const api = process.env.HALLBOOK_API ?? (process.env.NODE_ENV === "production" ? "" : "http://127.0.0.1:8780");
    return api ? [{ source: "/api/hallbook/:path*", destination: `${api}/api/:path*` }] : [];
  },
  async redirects() {
    return [
      { source: "/index.html", destination: "/", permanent: true },
      { source: `/:slug(${PAGES}).html`, destination: "/:slug", permanent: true },
    ];
  },
};

export default nextConfig;
