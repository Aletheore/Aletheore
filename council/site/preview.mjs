// Local preview: serves this folder and answers /api/slots with the same handler Vercel will run.
// Usage: node preview.mjs [port] [--lan]   (default 4180), then open http://127.0.0.1:4180/
// --lan also listens on your local network so a phone on the same Wi-Fi can open it (this is a preview of a page that is not public: stop it when done).
import { createServer } from "node:http";
import { readFile } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { networkInterfaces } from "node:os";
import { fileURLToPath } from "node:url";
import slots from "./api/slots.js";

const root = fileURLToPath(new URL(".", import.meta.url));
const lan = process.argv.includes("--lan");
const port = Number(process.argv.slice(2).find((a) => /^\d+$/.test(a)) || 4180);
const types = { ".html": "text/html; charset=utf-8", ".txt": "text/plain; charset=utf-8", ".json": "application/json", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml", ".png": "image/png" };

createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  if (url.pathname === "/api/slots") {
    res.status = (code) => { res.statusCode = code; return res; };
    res.json = (body) => { res.setHeader("Content-Type", "application/json"); res.end(JSON.stringify(body)); };
    return slots(req, res);
  }
  let name = url.pathname === "/" ? "index.html" : normalize(url.pathname).replace(/^(\.\.[/\\])+/, "").replace(/^[/\\]+/, "");
  if (!extname(name)) name += ".html"; // clean URLs, as vercel.json asks for: /privacy serves privacy.html
  if (name.startsWith("api") || name.includes("node_modules") || name.startsWith("design") || ["preview.mjs", "check.mjs", "README.md"].includes(name)) { res.statusCode = 404; return res.end("not found"); }
  try {
    const body = await readFile(join(root, name));
    res.setHeader("Content-Type", types[extname(name)] || "application/octet-stream");
    res.end(body);
  } catch {
    res.statusCode = 404;
    res.end("not found");
  }
}).listen(port, lan ? "0.0.0.0" : "127.0.0.1", () => {
  console.log(`http://127.0.0.1:${port}/`);
  if (lan) for (const list of Object.values(networkInterfaces())) for (const i of list || []) if (i.family === "IPv4" && !i.internal) console.log(`on your network: http://${i.address}:${port}/`);
});
