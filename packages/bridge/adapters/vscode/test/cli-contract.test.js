"use strict";

// Contract guard: the CLI-version pin in package.json, the COMPATIBLE_CLI
// bounds in extension.js, and the real @icaro0310/devin-bridge version must
// agree — and the adapter must stay read-only: no string literal that would
// reach a mutating devin-bridge subcommand may exist in extension.js.

const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");

const ADAPTER = path.join(__dirname, "..");
const pkg = JSON.parse(fs.readFileSync(path.join(ADAPTER, "package.json"), "utf8"));
const src = fs.readFileSync(path.join(ADAPTER, "extension.js"), "utf8");

// packages/bridge/package.json is the source of truth for the CLI version.
const cliPkg = JSON.parse(
  fs.readFileSync(path.join(ADAPTER, "..", "..", "package.json"), "utf8"),
);
const realVersion = cliPkg.version;

// Whole-token match: any of these appearing as a quoted literal in
// extension.js could reach a mutating code path (session create/drive,
// mailbox processing, policy write, or a probe that spawns a session).
const FORBIDDEN_TOKENS = new Set([
  "new", "resume", "prompt", "intake",
  "--init", "--apply", "--force", "--yes", "--dry-run", "--file", "--model",
]);

function parseRange(range) {
  const m = /^>=(\d+)\.(\d+)\.(\d+),<(\d+)\.(\d+)\.(\d+)$/.exec(range);
  assert.ok(m, `pin "${range}" must be a ">=x.y.z,<x.y.z" range`);
  return {
    min: [Number(m[1]), Number(m[2])],
    maxExclusive: [Number(m[4]), Number(m[5])],
  };
}

test("package.json pins the devin-bridge CLI range", () => {
  assert.ok(pkg["devin-bridge-cli"], "missing devin-bridge-cli field");
  parseRange(pkg["devin-bridge-cli"]);
});

test("COMPATIBLE_CLI matches the package.json pin", () => {
  const { min, maxExclusive } = parseRange(pkg["devin-bridge-cli"]);
  const m = /COMPATIBLE_CLI\s*=\s*\{\s*min:\s*\[(\d+),\s*(\d+)\],\s*maxExclusive:\s*\[(\d+),\s*(\d+)\]/.exec(src);
  assert.ok(m, "COMPATIBLE_CLI bounds not found in extension.js");
  assert.deepStrictEqual([Number(m[1]), Number(m[2])], min);
  assert.deepStrictEqual([Number(m[3]), Number(m[4])], maxExclusive);
});

test("the real devin-bridge version falls inside the pinned range", () => {
  const { min, maxExclusive } = parseRange(pkg["devin-bridge-cli"]);
  const [maj, minr] = realVersion.split(".").map(Number);
  const geMin = maj > min[0] || (maj === min[0] && minr >= min[1]);
  const ltMax = maj < maxExclusive[0] || (maj === maxExclusive[0] && minr < maxExclusive[1]);
  assert.ok(geMin && ltMax, `devin-bridge ${realVersion} outside ${pkg["devin-bridge-cli"]}`);
});

test("contributed commands are exactly the read-only four", () => {
  const ids = pkg.contributes.commands.map((c) => c.command).sort();
  assert.deepStrictEqual(ids, [
    "devin-bridge.policy",
    "devin-bridge.policyCheck",
    "devin-bridge.probe",
    "devin-bridge.sessions",
  ]);
});

test("no mutating subcommand or flag literal exists in extension.js", () => {
  for (const m of src.matchAll(/"([^"\n]+)"|'([^'\n]+)'/g)) {
    const token = m[1] ?? m[2];
    assert.ok(
      !FORBIDDEN_TOKENS.has(token),
      `forbidden literal "${token}" found in extension.js`,
    );
  }
});
