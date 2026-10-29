// No model calls: the browser's API requests are intercepted with public fixtures.
import assert from "node:assert/strict";
import { existsSync } from "node:fs";
import { join } from "node:path";
import { spawn, execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import puppeteer from "puppeteer-core";

const candidates = [process.env.CHROME_BIN, process.env.CHROME_PATH,
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
  process.env.PROGRAMFILES && join(process.env.PROGRAMFILES, "Google/Chrome/Application/chrome.exe"),
  process.env["PROGRAMFILES(X86)"] && join(process.env["PROGRAMFILES(X86)"], "Google/Chrome/Application/chrome.exe"),
  process.env.LOCALAPPDATA && join(process.env.LOCALAPPDATA, "Google/Chrome/Application/chrome.exe")];
const executablePath = candidates.find((path) => path && existsSync(path));
if (!executablePath) throw new Error("Chrome not found; set CHROME_BIN to its executable path.");
const base = process.env.COMPANION_TEST_BASE ?? "http://127.0.0.1:3102";
if (!["127.0.0.1", "localhost"].includes(new URL(base).hostname)) throw new Error("Browser checks require a local test server.");
let server;
let browser;
let release;
try {
  if (!process.env.COMPANION_TEST_BASE) {
    server = spawn(process.execPath, [fileURLToPath(new URL("../node_modules/next/dist/bin/next", import.meta.url)), "dev", "--hostname", "127.0.0.1", "--port", "3102"], {
      cwd: fileURLToPath(new URL("..", import.meta.url)), stdio: "ignore",
      env: { ...process.env, COMPANION_LIVE_ENABLED: "1", INDIEROUTER_API_KEY: "test" },
    });
    let ready = false;
    for (let attempt = 0; attempt < 120; attempt++) {
      if (server.exitCode !== null) throw new Error("Local test server exited before startup.");
      try { if ((await fetch(base, { signal: AbortSignal.timeout(1000) })).ok) { ready = true; break; } } catch { /* bounded startup polling */ }
      await new Promise((resolve) => setTimeout(resolve, 500));
    }
    assert(ready, "Local test server did not become ready.");
  }
  browser = await puppeteer.launch({ executablePath, headless: true, args: process.env.CI ? ["--no-sandbox"] : [] });
  const page = await browser.newPage();
  await page.setViewport({ width: 1440, height: 900 });
  await page.setRequestInterception(true);
  let held = false;
  let apiCalls = 0;
  page.on("request", async (request) => {
    if (new URL(request.url()).pathname !== "/api/companion") { await request.continue(); return; }
    apiCalls++;
    if (held) await new Promise((resolve) => { release = resolve; });
    try { await request.respond({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "answer", text: "Install Aletheore using the documented CLI quickstart.", command: "pip install aletheore", sources: [{ label: "Developer guide", href: "/developers" }] }) }); } catch { /* Stop aborts the intercepted request. */ }
  });
  const dialogOpen = () => page.waitForFunction(() => document.querySelector("dialog.companion-chat")?.open === true);
  const dialogClosed = () => page.waitForFunction(() => document.querySelector("dialog.companion-chat")?.open === false);
  await page.goto(base, { waitUntil: "networkidle0" });
  await page.click(".companion-trigger"); await dialogOpen();
  assert(await page.$eval(".companion-chat", (el) => !el.matches(":modal")), "Desktop dialog must be nonmodal.");
  await page.type(".companion-chat textarea", "Install Aletheore");
  await page.click('.companion-chat button[aria-label="Send question"]');
  await page.waitForSelector(".companion-chat__answer");
  assert.equal(apiCalls, 1);
  await page.click(".companion-chat__answer nav a"); await dialogClosed();
  await page.waitForFunction(() => location.pathname === "/developers");
  await page.click(".companion-trigger"); await dialogOpen();
  assert.match(await page.$eval(".companion-chat__log", (el) => el.textContent), /Install Aletheore/);
  await page.keyboard.press("Escape"); await dialogClosed();
  assert(await page.$eval(".companion-trigger", (el) => el === document.activeElement), "Escape restores focus.");
  await page.setViewport({ width: 390, height: 844 });
  await page.click(".companion-trigger"); await dialogOpen();
  assert(await page.$eval(".companion-chat", (el) => el.matches(":modal")), "Mobile dialog must trap focus through showModal.");
  await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
  held = true;
  await page.type(".companion-chat textarea", "Explain MCP");
  await page.click('.companion-chat button[aria-label="Send question"]');
  await page.waitForSelector(".companion-chat__loading");
  assert.equal(await page.$eval(".companion-trigger .aletheore-mascot__art", (el) => getComputedStyle(el).animationName), "none");
  await page.click(".companion-chat__loading button");
  await page.waitForSelector(".companion-chat__error");
  assert.match(await page.$eval(".companion-chat__error", (el) => el.textContent), /Stopped/);
  release?.(); release = undefined;
  await page.click(".companion-chat__tools button");
  await page.click(".companion-chat__clear button");
  await page.waitForFunction(() => !document.querySelector(".companion-chat__turn"));
  await page.click(".companion-close"); await dialogClosed();
  console.log("Companion browser checks passed: desktop/mobile dialogs, navigation persistence, Escape/focus, reduced motion, Stop and Clear. No provider requests.");
} finally {
  release?.();
  await browser?.close();
  if (server && server.exitCode === null) {
    if (process.platform === "win32") execFileSync("taskkill", ["/pid", String(server.pid), "/t", "/f"]);
    else server.kill("SIGTERM");
  }
}
