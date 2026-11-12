// Checks the site against the handoff. Run: node check.mjs   (exits 1 if anything fails)
import { existsSync, readFileSync } from "node:fs";
import { parseStatus } from "./api/slots.js";

const html = readFileSync(new URL("./index.html", import.meta.url), "utf8");
const failures = [];
const check = (name, ok, detail = "") => { console.log(`${ok ? "PASS" : "FAIL"}  ${name}${ok || !detail ? "" : `   [${detail}]`}`); if (!ok) failures.push(name); };

// no mockup markers or dead links
check("no mockup wording", !/mock-?up/i.test(html));
check("no placeholder wording", !/placeholder/i.test(html));
check("no TODO or lorem", !/\bTODO\b|\bFIXME\b|lorem ipsum/i.test(html));
check("no dead links (href=\"#\")", !/href="#"/.test(html));
check("every in-page link points at something that exists", [...html.matchAll(/href="#([^"]+)"/g)].every(([, id]) => html.includes(`id="${id}"`)), [...html.matchAll(/href="#([^"]+)"/g)].map((m) => m[1]).filter((id) => !html.includes(`id="${id}"`)).join(","));
check("no en or em dashes in the copy", !/[–—]/.test(html));
check("does not name the unpublished package", !/aletheore-council\b(?!-site)/.test(html.replace(/council-control/g, "")));
check("shows no install command that cannot be run", !/pip install|pipx install/.test(html));
check("open to search engines: no noindex tag, robots.txt allows crawling and names the sitemap, no X-Robots-Tag header",
  !/noindex/i.test(html) && /Allow: \//.test(readFileSync(new URL("./robots.txt", import.meta.url), "utf8")) && !/Disallow: \//.test(readFileSync(new URL("./robots.txt", import.meta.url), "utf8"))
  && /Sitemap: https:\/\/aletheorecouncil\.com\/sitemap\.xml/.test(readFileSync(new URL("./robots.txt", import.meta.url), "utf8"))
  && !/X-Robots-Tag/i.test(readFileSync(new URL("./vercel.json", import.meta.url), "utf8")));
check("the sitemap lists the home page and both legal pages", ["https://aletheorecouncil.com/</loc>", "https://aletheorecouncil.com/privacy</loc>", "https://aletheorecouncil.com/terms</loc>"]
  .every((u) => readFileSync(new URL("./sitemap.xml", import.meta.url), "utf8").includes(u)));
check("links back to aletheore.com with a plain followed link", /<a [^>]*href="https:\/\/aletheore\.com"[^>]*>/.test(html) && !/nofollow/i.test(html));

// copy that must stay true (security model, section 2: the server sees who posted)
check("does not claim the server cannot see who wrote what", !/who wrote what/i.test(html));
check("says the server can see who posted", /who posted each one/.test(html));

// the capacity statement the handoff requires (section 1b)
check("states the pool (150 live rooms)", /150 live rooms at a time/.test(html));
check("states the 5 GB a day public cap", /5 GB of files a day/.test(html));
check("says local rooms are always available with no limit", /always available, with no registry and no limit/.test(html));

// the counter is live, never a made-up number
check("the counter is read from /api/slots", /fetch\("\/api\/slots"/.test(html));
check("the counter starts empty, not at a made-up value", /id="cnt">…</.test(html));
check("a failed read says so", /could not be read just now/.test(html));

// scene 08 names only agents with recorded runs, and only the six real tools, and no link
const scene = html.slice(html.indexOf('id="L-agent"'), html.indexOf('id="tagline"'));
check("scene 08 names Claude Code and Codex (recorded runs), not OpenCode", /Claude Code/.test(scene) && /Codex/.test(scene) && !/OpenCode/.test(scene));
const six = new Set(["list_rooms", "room_status", "start_room", "make_invite", "list_join_requests", "define_area"]);
check("scene 08 uses only the six real tools", [...scene.matchAll(/council_control_([a-z_]+)/g)].every(([, t]) => six.has(t)));
check("scene 08 shows no invitation link", !/\/j\/inv_/.test(scene));
check("the demo window says it is an illustration, except the recorded runs", /council \u00b7 illustration, except the recorded agent runs/.test(html));

// the six tools and what has no tool
for (const t of six) check(`lists the tool ${t}`, scene.includes(t));

// icons and the social image: real files of the right size, linked from the page
const png = (name) => { const b = readFileSync(new URL(`./${name}`, import.meta.url)); return b.subarray(0, 8).toString("hex") === "89504e470d0a1a0a" ? { w: b.readUInt32BE(16), h: b.readUInt32BE(20) } : null; };
for (const [name, w, h] of [["og.png", 1200, 630], ["apple-touch-icon.png", 180, 180], ["favicon-32.png", 32, 32]]) {
  const d = existsSync(new URL(`./${name}`, import.meta.url)) ? png(name) : null;
  check(`${name} is a real ${w}x${h} PNG`, !!d && d.w === w && d.h === h, d ? `${d.w}x${d.h}` : "missing");
}
check("favicon.svg exists", existsSync(new URL("./favicon.svg", import.meta.url)));
for (const href of ["/favicon.svg", "/favicon-32.png", "/apple-touch-icon.png", "https://aletheorecouncil.com/og.png"]) check(`the page links ${href}`, html.includes(`"${href}"`));
for (const prop of ["og:title", "og:description", "og:image", "og:url", "twitter:card"]) check(`has ${prop}`, new RegExp(`(property|name)="${prop}"`).test(html));
check("the social title and description match the page's own", html.split('og:title" content="')[1]?.startsWith(html.match(/<title>(.*?)<\/title>/)[1]) && html.includes('name="description" content="Council is a shared room'));

// screens too short for the pinned walkthrough get a note, not a broken demo
check("a screen too short for the walkthrough (under 620 px, below an iPhone 15 in Safari at about 659) shows a note instead", /class="rotate"/.test(html) && html.includes("@media (max-width:900px) and (max-height:619px), (max-height:480px){#stage{display:none}"));
check("there is no camera zoom on small screens", /const narrow=\(\)=>matchMedia\("\(max-width:900px\)"\)\.matches/.test(html) && /function aim\(el,scale\)\{if\(narrow\(\)\)return/.test(html) && /scale:narrow\(\)\?1:1\.18/.test(html));
check("the stage uses the small viewport height (Safari's toolbar)", /#stage\{height:100svh\}/.test(html));

// the legal pages: real, linked, honest
for (const name of ["privacy", "terms"]) {
  const page = existsSync(new URL(`./${name}.html`, import.meta.url)) ? readFileSync(new URL(`./${name}.html`, import.meta.url), "utf8") : "";
  check(`${name}.html exists and is open to search engines`, page.length > 2000 && !/noindex/i.test(page));
  check(`${name}.html names the contact`, page.includes("support@aletheore.com"));
  check(`${name}.html has no mockup or placeholder wording, dashes or TODOs`, !/mock-?up|placeholder|lorem|\bTODO\b|[\u2013\u2014]/i.test(page));
  check(`the page links /${name}`, html.includes(`href="/${name}"`));
}
const privacyPage = readFileSync(new URL("./privacy.html", import.meta.url), "utf8");
check("privacy says what the registry stores and that it keeps no access log", /device&#x27;s id|device's id/.test(privacyPage) && /does not keep an access log/.test(privacyPage));
check("privacy says there is no automatic deletion of device ids yet", /no automatic deletion yet/.test(privacyPage));
check("privacy discloses the third party fonts and animation library", /Google Fonts/.test(privacyPage) && /cdnjs/.test(privacyPage));
check("privacy points at the Aletheore policy for the package's crash reporting", /aletheore\.com\/privacy/.test(privacyPage));
check("privacy says how to delete data and that backups may keep it", /Deleting your data/.test(privacyPage) && /Backups of the registry may keep/.test(privacyPage));
check("privacy and terms point at the Aletheore policies and security page", /aletheore\.com\/terms/.test(readFileSync(new URL("./terms.html", import.meta.url), "utf8")) && /aletheore\.com\/security/.test(readFileSync(new URL("./terms.html", import.meta.url), "utf8")));
check("terms say Council has not had an independent security review", /not had an independent security review/.test(readFileSync(new URL("./terms.html", import.meta.url), "utf8")));
check("the home page says it has not had an independent security review", /not had an independent security review/.test(html));

// the API route
check("the route accepts the registry's real shape", JSON.stringify(parseStatus({ in_use: 1, total: 150, queued: 0 })) === '{"in_use":1,"total":150}');
check("the route refuses nonsense", [null, "x", {}, { in_use: -1, total: 150 }, { in_use: 151, total: 150 }, { in_use: 1.5, total: 150 }, { in_use: 1, total: 0 }, { in_use: "1", total: 150 }].every((v) => parseStatus(v) === null));

console.log(failures.length ? `\n${failures.length} check(s) failed` : "\nall checks passed");
process.exit(failures.length ? 1 : 0);
