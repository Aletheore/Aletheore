// Regression check for two dev-mode issues: hydration warnings from browser extensions that edit <body> (Grammarly),
// and removeChild errors when counters or the pinned scene unmount during client-side navigation.
// Run against `npm run dev` (dev mode prints the warnings) with the server on :3100.
import puppeteer from "puppeteer-core";
const b = await puppeteer.launch({ executablePath: "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", headless: "new", args: ["--no-sandbox"] });
const BASE = process.env.BASE ?? "http://localhost:3100";
const p = await b.newPage(); await p.setViewport({ width: 1440, height: 900 });
const msgs = []; p.on("console", m => { if (["error", "warning"].includes(m.type())) msgs.push(m.type() + ": " + m.text().slice(0, 160)); }); p.on("pageerror", e => msgs.push("pageerror: " + String(e).slice(0, 160)));
// simulate Grammarly: extension attributes appear on <body> before React hydrates
await p.evaluateOnNewDocument(() => { const t = setInterval(() => { if (document.body) { document.body.setAttribute("data-gr-ext-installed", ""); document.body.setAttribute("data-new-gr-c-s-check-loaded", "9.99.0"); clearInterval(t); } }, 0); });
await p.goto(BASE + "/", { waitUntil: "networkidle0" });
await p.evaluate(() => [...document.querySelectorAll("dt")].find(e => e.textContent === "Files parsed").scrollIntoView({ block: "center" }));
await new Promise(r => setTimeout(r, 2500)); // counters have run
// client-side navigation away, which unmounts the counters
for (const href of ["/pricing", "/developers", "/benchmarks", "/"]) {
  await p.evaluate(h => document.querySelector(`header a[href="${h}"], footer a[href="${h}"]`)?.click(), href);
  await new Promise(r => setTimeout(r, 1200));
}
console.log("issues:", msgs.length ? "\n " + msgs.join("\n ") : "none");
await b.close();
process.exit(msgs.length ? 1 : 0);
