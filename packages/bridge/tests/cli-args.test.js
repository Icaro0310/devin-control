import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import path from "node:path";
import os from "node:os";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BIN = path.join(HERE, "..", "bin", "devin-bridge.js");

// Runs the real CLI in a scratch dir with no policy.json — policy --check
// resolves the built-in default policy, so no devin binary is needed.
function run(args) {
  const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "devin-bridge-cli-test-"));
  try {
    const stdout = execFileSync("node", [BIN, ...args], {
      cwd, encoding: "utf8", stdio: ["ignore", "pipe", "pipe"],
    });
    return { code: 0, stdout };
  } catch (e) {
    return { code: e.status ?? 1, stdout: e.stdout || "", stderr: e.stderr || "" };
  }
}

describe("cli arg parsing", () => {
  it("policy --check accepts an option-shaped target after --", () => {
    const r = run(["policy", "--check", "terminal", "--", "--version"]);
    assert.strictEqual(r.code, 0, r.stderr);
    const decision = JSON.parse(r.stdout.trim());
    assert.strictEqual(decision.capability, "terminal");
    assert.strictEqual(decision.target, "--version");
    assert.strictEqual(typeof decision.decision, "string");
  });

  it("option-shaped target without -- cannot reach the check (documents the boundary)", () => {
    const r = run(["policy", "--check", "terminal", "--version"]);
    assert.strictEqual(r.code, 1); // usage error — the target is swallowed as an option
  });

  it("-- passes several positional tokens through literally", () => {
    const r = run(["policy", "--check", "fsRead", "--", "--json", "--verbose"]);
    assert.strictEqual(r.code, 0, r.stderr);
    const decision = JSON.parse(r.stdout.trim());
    // first positional after the marker is the target
    assert.strictEqual(decision.target, "--json");
  });

  it("metachar targets reach the check as one literal arg", () => {
    const r = run(["policy", "--check", "terminal", "--", "x & calc"]);
    assert.strictEqual(r.code, 0, r.stderr);
    const decision = JSON.parse(r.stdout.trim());
    assert.strictEqual(decision.target, "x & calc");
  });
});
