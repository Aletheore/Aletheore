import puppeteer from "puppeteer-core";
import { mkdirSync, writeFileSync } from "node:fs";
import { gzipSync } from "node:zlib";

const BASE = process.env.BASE ?? "http://localhost:3100";
const OUT = process.env.OUT ?? "e2e-out";
const CHROME = process.env.CHROME ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
mkdirSync(OUT, { recursive: true });

const passes = [
  { name: "desktop", viewport: { width: 1440, height: 900 } },
  { name: "mobile", viewport: { width: 375, height: 812, isMobile: true } },
  { name: "wide", viewport: { width: 2560, height: 1300 } },
  { name: "zoom200", viewport: { width: 720, height: 450 } },
  { name: "reduced-motion", viewport: { width: 1440, height: 900 }, reduced: true },
  { name: "no-webgl", viewport: { width: 1440, height: 900 }, noWebGL: true },
];

async function initialScriptGzipBytes() {
  const html = await (await fetch(BASE)).text();
  // Next ships a legacy polyfill bundle marked noModule; modern browsers never download it, so it is not counted.
  const srcs = [...new Set([...html.matchAll(/<script([^>]+)>/g)].filter((m) => !/noModule/i.test(m[1])).map((m) => /src="([^"]+)"/.exec(m[1])?.[1]).filter(Boolean))];
  let total = 0;
  for (const s of srcs) total += gzipSync(Buffer.from(await (await fetch(new URL(s, BASE))).arrayBuffer())).length;
  return { total, count: srcs.length };
}
const initial = await initialScriptGzipBytes();
if (initial.total > 200 * 1024) { console.error(`initial JS ${initial.total} bytes gzipped exceeds the 200 KB budget`); process.exitCode = 1; }

const browser = await puppeteer.launch({ executablePath: CHROME, headless: "new", args: ["--no-sandbox", "--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"] });
const results = [];
let failed = false;
const fail = (pass, msg) => { failed = true; results.push({ pass, fail: msg }); };

for (const p of passes) {
  const page = await browser.newPage();
  await page.setViewport({ deviceScaleFactor: 1, ...p.viewport });
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(String(e)));
  if (p.reduced) await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  if (p.noWebGL) {
    await page.evaluateOnNewDocument(() => {
      const orig = HTMLCanvasElement.prototype.getContext;
      HTMLCanvasElement.prototype.getContext = function (t, ...a) { return /webgl/i.test(t) ? null : orig.call(this, t, ...a); };
    });
  }
  await page.goto(BASE, { waitUntil: "networkidle0", timeout: 60000 });
  await new Promise((r) => setTimeout(r, 1200));

  const height = await page.evaluate(() => document.documentElement.scrollHeight);
  const seen = new Set();
  let i = 0;
  for (let y = 0; y < height; y += Math.round(p.viewport.height * 0.6), i++) {
    await page.evaluate((v) => window.scrollTo(0, v), y);
    await new Promise((r) => setTimeout(r, 350));
    const step = await page.evaluate(() => document.querySelector("[data-active-step]")?.getAttribute("data-active-step"));
    if (step !== null && step !== undefined) seen.add(step);
    if (i % 3 === 0) await page.screenshot({ path: `${OUT}/${p.name}-${String(i).padStart(2, "0")}.png` });
  }

  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  if (overflow > 0) fail(p.name, `horizontal overflow of ${overflow}px`);
  if (errors.length) fail(p.name, `console errors: ${errors.slice(0, 3).join(" | ")}`);
  const webgl = await page.evaluate(() => [...document.querySelectorAll("[data-webgl-ready]")].map((e) => e.getAttribute("data-webgl-ready")));
  if ((p.reduced || p.noWebGL) && webgl.includes("true")) fail(p.name, "WebGL started although it must be off in this pass");
  const pinned = p.viewport.width >= 768 && !p.reduced;
  if (pinned && p.name === "desktop" && seen.size < 6) fail(p.name, `pinned scene only reached steps ${[...seen].join(",")}`);
  results.push({ pass: p.name, height, overflow, steps: [...seen], webgl });
  await page.close();
}

await browser.close();
results.unshift({ initialJsGzipBytes: initial.total, scripts: initial.count, budget: 204800 });
writeFileSync(`${OUT}/summary.json`, JSON.stringify(results, null, 2));
console.log(JSON.stringify(results));
process.exit(failed || process.exitCode ? 1 : 0);
