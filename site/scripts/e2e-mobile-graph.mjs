import puppeteer from "puppeteer-core";
import { existsSync } from "node:fs";

// Set CHROME to a Chrome/Chromium executable when it is not installed at a standard path.

const base = process.env.BASE ?? "http://localhost:3100";
const candidates = process.platform === "darwin"
  ? ["/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"]
  : process.platform === "win32"
    ? [`${process.env.PROGRAMFILES ?? "C:/Program Files"}/Google/Chrome/Application/chrome.exe`]
    : ["/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser"];
const chrome = process.env.CHROME ?? candidates.find(existsSync);
if (!chrome || !existsSync(chrome)) throw new Error("Set CHROME to an installed Chrome or Chromium executable.");
const browser = await puppeteer.launch({ executablePath: chrome, headless: "new", args: ["--no-sandbox"] });
try {
  for (const reduced of [false, true]) {
    const page = await browser.newPage();
    try {
      await page.setViewport({ width: 375, height: 812, isMobile: true, hasTouch: true });
      if (reduced) await page.emulateMediaFeatures([{ name: "prefers-reduced-motion", value: "reduce" }]);
      await page.goto(base, { waitUntil: "networkidle0" });
      const graph = await page.$('canvas[aria-label^="Dependency graph of the Aletheore repository"]');
      if (!graph) throw new Error("Live graph not found");
      await graph.scrollIntoView();
      const box = await graph.boundingBox();
      if (!box) throw new Error("Live graph is not visible");
      let selected = false;
      for (let y = 12; y < box.height - 12 && !selected; y += 18) {
        for (let x = 12; x < box.width - 12 && !selected; x += 18) {
          await page.touchscreen.tap(box.x + x, box.y + y);
          selected = await page.evaluate(() => !![...document.querySelectorAll("button")].find((el) => el.textContent === "Clear selection"));
        }
      }
      if (!selected) throw new Error(`No graph node could be selected (reduced motion: ${reduced})`);
      const clear = await page.$("button::-p-text(Clear selection)");
      if (!clear) throw new Error("Clear selection control disappeared before it could be used");
      await clear.scrollIntoView();
      const clearBox = await clear.boundingBox();
      if (!clearBox) throw new Error("Clear selection control is not visible");
      await page.touchscreen.tap(clearBox.x + clearBox.width / 2, clearBox.y + clearBox.height / 2);
      if (await page.evaluate(() => !![...document.querySelectorAll("button")].find((el) => el.textContent === "Clear selection"))) {
        throw new Error(`Graph selection was not cleared (reduced motion: ${reduced})`);
      }
    } finally {
      await page.close();
    }
  }
  console.log("Mobile graph selection and clear passed with normal and reduced motion.");
} finally {
  await browser.close();
}
