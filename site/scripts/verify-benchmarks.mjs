// Checks every number in src/data/benchmarks-tables.json against the committed READMEs of the benchmarks repo.
//   node scripts/verify-benchmarks.mjs ~/Documents/GitHub/aletheore-benchmarks
import { readFileSync } from "node:fs";
import { join } from "node:path";

const repo = process.argv[2] ?? "../../aletheore-benchmarks";
const tables = JSON.parse(readFileSync(new URL("../src/data/benchmarks-tables.json", import.meta.url), "utf8"));
const norm = (s) => s.toLowerCase().replace(/[*`\s]+/g, "");
const numbers = (s) => s.match(/\d[\d,.]*%?/g) ?? [];
// Tables whose source is prose or a differently shaped table: each number must appear somewhere in the sources.
const LOOSE = new Set(["compactRuns", "compactSize", "indexCost"]);
// Tables where one label spans several source tables (scores in one, costs in another): each number needs a row with the label.
const PER_NUMBER = new Set(["swePr"]);
// Figures that are derived from source numbers rather than quoted; they are recomputed here.
const DERIVED = [
  { id: "compactSize", label: "Avg cost per review, same run", note: "~24% cheaper", value: () => Math.round((1 - 0.0041 / 0.0054) * 100), expect: 24 },
];

const failures = [];
let checked = 0;
for (const [id, t] of Object.entries(tables)) {
  const text = t.sources.map((f) => readFileSync(join(repo, f), "utf8")).join("\n");
  const lines = text.split("\n").filter((l) => l.startsWith("|"));
  const flat = norm(text);
  for (const row of t.rows) {
    const label = row[0];
    const nums = row.slice(1).flatMap(numbers);
    checked++;
    if (LOOSE.has(id)) {
      const derived = DERIVED.find((d) => d.id === id && d.label === label);
      const missing = nums.filter((n) => !flat.includes(norm(n)) && !(derived && n.startsWith(String(derived.expect)) && derived.value() === derived.expect));
      if (missing.length) failures.push(`${id} / ${label}: not found anywhere: ${missing.join(", ")}`);
      continue;
    }
    const candidates = lines.filter((l) => norm(l).includes(norm(label)));
    if (PER_NUMBER.has(id)) {
      const lost = nums.filter((n) => !candidates.some((l) => norm(l).includes(norm(n))));
      if (lost.length) failures.push(`${id} / ${label}: no source row with the label carries ${lost.join(", ")}`);
      continue;
    }
    const ok = candidates.some((l) => nums.every((n) => norm(l).includes(norm(n))));
    if (!ok) failures.push(`${id} / ${label}: no source row carries ${nums.join(", ")} (${candidates.length} rows share the label)`);
  }
}

// Prose claims that appear in the page copy and must exist verbatim in the repo.
const claims = JSON.parse(readFileSync(new URL("../src/data/benchmarks-claims.json", import.meta.url), "utf8"));
for (const { file, text } of claims) {
  checked++;
  if (!norm(readFileSync(join(repo, file), "utf8")).includes(norm(text))) failures.push(`claim not found in ${file}: ${text}`);
}

console.log(`${checked} rows and claims checked against ${repo}`);
if (failures.length) {
  console.error(failures.join("\n"));
  process.exit(1);
}
console.log("all numbers match");
