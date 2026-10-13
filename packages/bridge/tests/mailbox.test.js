import { describe, it, beforeEach, afterEach } from "node:test";
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";
import {
  mailboxDir, checkPermissions, parseTask, listInbox, moveTo,
  MAX_TASK_BYTES,
} from "../src/mailbox.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const BRIDGE = path.join(HERE, "..", "bin", "devin-bridge.js");

let dir;
beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), "mailbox-test-"));
});
afterEach(() => fs.rmSync(dir, { recursive: true, force: true }));

describe("mailboxDir", () => {
  it("creates the four stages", () => {
    const root = mailboxDir(dir);
    for (const s of ["inbox", "processing", "done", "failed"]) {
      assert.ok(fs.statSync(path.join(root, s)).isDirectory());
    }
  });
  it("is idempotent", () => {
    mailboxDir(dir);
    mailboxDir(dir); // no throw
  });
});

describe("checkPermissions", () => {
  it("accepts owner-only dirs", () => {
    const d = path.join(dir, "ok");
    fs.mkdirSync(d, { mode: 0o700 });
    checkPermissions(d); // no throw
  });
  it("rejects group/other-accessible dirs (POSIX)", {
    skip: process.platform === "win32",
  }, () => {
    const d = path.join(dir, "open");
    fs.mkdirSync(d, { mode: 0o755 });
    assert.throws(() => checkPermissions(d), /accessible by group\/other/);
  });
});

describe("parseTask", () => {
  const write = (name, content) => {
    const f = path.join(dir, name);
    fs.writeFileSync(f, content);
    return f;
  };

  it("parses a minimal task", () => {
    const r = parseTask(write("a.json", JSON.stringify({ task: "do it" })));
    assert.equal(r.ok, true);
    assert.equal(r.task.prompt, "do it");
    assert.equal(r.task.repo, null);
  });

  it("resolves repo to an absolute path", () => {
    const r = parseTask(write("b.json",
      JSON.stringify({ task: "x", repo: dir })));
    assert.equal(r.ok, true);
    assert.equal(r.task.repo, path.resolve(dir));
  });

  it("rejects invalid JSON", () => {
    assert.equal(parseTask(write("c.json", "{nope")).ok, false);
  });
  it("rejects non-object", () => {
    assert.equal(parseTask(write("d.json", "[1]")).ok, false);
    assert.equal(parseTask(write("e.json", "42")).ok, false);
  });
  it("rejects missing/empty task", () => {
    assert.equal(parseTask(write("f.json", "{}")).ok, false);
    assert.equal(parseTask(write("g.json", '{"task":"  "}')) .ok, false);
  });
  it("rejects oversized files", () => {
    const f = write("big.json",
      JSON.stringify({ task: "x".repeat(MAX_TASK_BYTES) }));
    const r = parseTask(f);
    assert.equal(r.ok, false);
    assert.match(r.error, /exceeds/);
  });
  it("rejects a repo that does not exist", () => {
    const r = parseTask(write("h.json",
      JSON.stringify({ task: "x", repo: "/no/such/dir-zzz" })));
    assert.equal(r.ok, false);
    assert.match(r.error, /does not exist/);
  });
});

describe("listInbox / moveTo", () => {
  it("lists .json files sorted by name", () => {
    const root = mailboxDir(dir);
    fs.writeFileSync(path.join(root, "inbox", "b.json"), "{}");
    fs.writeFileSync(path.join(root, "inbox", "a.json"), "{}");
    fs.writeFileSync(path.join(root, "inbox", "note.txt"), "x");
    const files = listInbox(root).map((f) => path.basename(f));
    assert.deepEqual(files, ["a.json", "b.json"]);
  });

  it("moves a file to another stage with a timestamp prefix", () => {
    const root = mailboxDir(dir);
    const f = path.join(root, "inbox", "a.json");
    fs.writeFileSync(f, "{}");
    const dst = moveTo(root, f, "done");
    assert.ok(!fs.existsSync(f));
    assert.ok(fs.existsSync(dst));
    assert.match(path.basename(dst), /-a\.json$/);
  });
});

describe("intake CLI (subprocess)", () => {
  // Pins the dry-run/real-run split in bin/devin-bridge.js: dry-run never
  // builds an ACP client so the policy load is skipped there; a real run
  // still loads it before any task leaves inbox/.
  const stageFiles = (root, stage) =>
    fs.readdirSync(path.join(root, stage)).filter((f) => f.endsWith(".json"));
  const intake = (args) => spawnSync(
    process.execPath, [BRIDGE, "intake", "--state-dir", dir, ...args],
    { cwd: dir, encoding: "utf8" });

  it("--dry-run validates tasks even with a missing --policy", () => {
    const root = mailboxDir(dir);
    fs.writeFileSync(path.join(root, "inbox", "t1.json"),
      JSON.stringify({ task: "summarise the diff" }));
    const res = intake(["--dry-run", "--policy", path.join(dir, "gone.json")]);
    assert.equal(res.status, 0, res.stderr);
    const out = JSON.parse(res.stdout);
    assert.deepEqual(out.results.map((r) => [r.file, r.status]),
      [["t1.json", "dry-run"]]);
    assert.deepEqual(stageFiles(root, "inbox"), ["t1.json"]);
  });

  it("a real run aborts on a bad --policy before any file leaves inbox/", () => {
    const root = mailboxDir(dir);
    fs.writeFileSync(path.join(root, "inbox", "t1.json"),
      JSON.stringify({ task: "summarise the diff" }));
    const res = intake(["--policy", path.join(dir, "gone.json")]);
    assert.equal(res.status, 1);
    assert.match(res.stderr, /cannot load policy/);
    assert.deepEqual(stageFiles(root, "inbox"), ["t1.json"]);
    for (const stage of ["processing", "done", "failed"]) {
      assert.deepEqual(stageFiles(root, stage), []);
    }
  });
});
