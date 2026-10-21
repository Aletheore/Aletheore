import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import test from "node:test";

const source = readFileSync(new URL("./render-and-composite.sh", import.meta.url), "utf8");
const gateStart = source.indexOf(
  'if [[ -f "$PROJECT/plan.json" && -d "$PROJECT/frames_fg" ]]; then',
);
const gateEnd = source.indexOf("\n# Custom mode", gateStart);
assert.ok(gateStart >= 0 && gateEnd > gateStart, "occlusion gate must be present");
const gate = source.slice(gateStart, gateEnd);

function runGate(t, { output = "", exitCode, skip = false }) {
  const root = mkdtempSync(join(tmpdir(), "hf-occlusion-gate-"));
  t.after(() => rmSync(root, { recursive: true, force: true }));
  const project = join(root, "project with spaces");
  mkdirSync(join(project, "frames_fg"), { recursive: true });
  writeFileSync(join(project, "plan.json"), "{}");
  writeFileSync(
    join(root, "check-occlusion.cjs"),
    `const fs = require("node:fs");
fs.writeSync(1, ${JSON.stringify(output)});
fs.writeFileSync(process.argv[2] + "/analyzer-ready", "");
${exitCode === undefined ? "setInterval(() => {}, 1000);" : `process.exit(${exitCode});`}
`,
  );
  // Run the production gate with a ready analyzer before advancing its Bash clock.
  // A silent hanging analyzer must abort, never leave a PASS in the gate ledger.
  const harness = join(root, "gate.sh");
  writeFileSync(
    harness,
    `#!/usr/bin/env bash
set -euo pipefail
PROJECT="$1"
GATES="$PROJECT/_gates.txt"; : > "$GATES"
OCC_PID=""
trap 'if [[ -n "$OCC_PID" ]]; then kill -9 "$OCC_PID" 2>/dev/null || true; wait "$OCC_PID" 2>/dev/null || true; fi' EXIT
sleep() {
  ${exitCode === undefined ? 'while [[ ! -f "$PROJECT/analyzer-ready" ]]; do command sleep 0.01; done\n  SECONDS=$((SECONDS + 10))' : "command sleep 0.01"}
}
${gate}
echo continued
`,
  );
  const result = spawnSync("bash", [harness, project], {
    encoding: "utf8",
    timeout: 10000,
    env: { ...process.env, OCCLUSION_SKIP: skip ? "1" : "0" },
  });
  assert.ifError(result.error);
  return { ...result, ledger: readFileSync(join(project, "_gates.txt"), "utf8") };
}

const verdict = "[v2] fixture  word-fail≥65%  cap-fail≥55%\n";
const failure = "[v2] 1 cap(s) FAIL: caption\n";

test("a timeout before any verdict aborts without recording PASS", (t) => {
  const result = runGate(t, {});
  assert.equal(result.status, 2, result.stderr);
  assert.equal(result.ledger, "");
  assert.doesNotMatch(result.stdout, /continued/);
  assert.match(result.stderr, /timed out before.*verdict/);
  assert.doesNotMatch(result.stderr, /hung after verdict/);
});

test("partial analysis output cannot make a timeout pass", (t) => {
  const result = runGate(t, { output: "[v2] measuring layout\n" });
  assert.equal(result.status, 2, result.stderr);
  assert.equal(result.ledger, "");
});

test("a pre-verdict timeout can only continue as an explicit override", (t) => {
  const result = runGate(t, { skip: true });
  assert.equal(result.status, 0, result.stderr);
  assert.match(result.ledger, /occlusion\+overflow OVERRIDDEN/);
  assert.doesNotMatch(result.ledger, /PASS/);
  assert.match(result.stdout, /continued/);
});

test("a passing verdict still tolerates a native teardown hang", (t) => {
  const result = runGate(t, { output: verdict });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.ledger, "occlusion+overflow PASS\n");
  assert.match(result.stdout, /continued/);
});

test("a failing verdict still aborts after a native teardown hang", (t) => {
  const result = runGate(t, { output: verdict + failure });
  assert.equal(result.status, 2, result.stderr);
  assert.equal(result.ledger, "");
  assert.doesNotMatch(result.stdout, /continued/);
});

test("a normally completed check records PASS", (t) => {
  const result = runGate(t, { output: verdict, exitCode: 0 });
  assert.equal(result.status, 0, result.stderr);
  assert.equal(result.ledger, "occlusion+overflow PASS\n");
  assert.doesNotMatch(result.stderr, /timed out|hung after verdict/);
});

test("a normally failed check aborts", (t) => {
  const result = runGate(t, { output: verdict + failure, exitCode: 2 });
  assert.equal(result.status, 2, result.stderr);
  assert.equal(result.ledger, "");
  assert.doesNotMatch(result.stderr, /timed out|hung after verdict/);
});

test("an analyzer error aborts without recording PASS", (t) => {
  const result = runGate(t, { output: "[v2] cannot measure layout\n", exitCode: 1 });
  assert.equal(result.status, 2, result.stderr);
  assert.equal(result.ledger, "");
  assert.doesNotMatch(result.stderr, /timed out|hung after verdict/);
});
