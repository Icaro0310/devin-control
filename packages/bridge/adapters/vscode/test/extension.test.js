"use strict";

// Minimal vscode + child_process stubs injected before requiring the
// extension — every runBridge call is captured so tests can assert what
// actually reaches the spawned process.
const statusBarStub = {
  text: "", tooltip: "", backgroundColor: undefined, command: "",
  show() {}, hide() {}, dispose() {},
};
const calls = []; // { exe, args, opts }
let settings = {};
let nextResult = { code: 0, stdout: "[]", stderr: "" };
let quickPickValue;
let inputBoxValue;

const vscodeStub = {
  ThemeColor: class {
    constructor(id) {
      this.id = id;
    }
  },
  StatusBarAlignment: { Left: 1, Right: 2 },
  Position: class {
    constructor(line, ch) {
      this.line = line;
      this.character = ch;
    }
  },
  window: {
    createStatusBarItem: () => statusBarStub,
    showInformationMessage: () => Promise.resolve(),
    showErrorMessage: () => Promise.resolve(),
    showQuickPick: () => Promise.resolve(quickPickValue),
    showInputBox: () => Promise.resolve(inputBoxValue),
    showTextDocument: () => Promise.resolve({ edit: async () => true }),
  },
  workspace: {
    isTrusted: true,
    getConfiguration: () => ({ get: (k, d) => (k in settings ? settings[k] : d) }),
    workspaceFolders: [{ uri: { fsPath: "/repo" } }],
    openTextDocument: () => Promise.resolve({}),
  },
  commands: { registerCommand: () => ({ dispose() {} }) },
};

const cpStub = {
  execFile: (exe, args, opts, cb) => {
    calls.push({ exe, args, opts });
    const err = nextResult.code
      ? Object.assign(new Error(`exit ${nextResult.code}`), { code: nextResult.code })
      : null;
    cb(err, nextResult.stdout, nextResult.stderr);
  },
};

const Module = require("module");
const origRequire = Module.prototype.require;
Module.prototype.require = function (id) {
  if (id === "vscode") return vscodeStub;
  if (id === "child_process") return cpStub;
  return origRequire.apply(this, arguments);
};

const ext = require("../extension.js");
const { __internals__ } = ext;
const test = require("node:test");
const assert = require("node:assert");
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");

// PATHEXT on Windows never matches a bare extensionless file — fixtures
// carry the real executable suffix (".EXE", matching PATHEXT's case so
// resolved paths string-compare equal).
const EXE = process.platform === "win32" ? ".EXE" : "";

// spawnSpec resolves for real now — plant a fake devin-bridge on PATH so
// the spawned calls below have a target to resolve to.
const pathBin = fs.mkdtempSync(path.join(os.tmpdir(), "bridge-path-"));
fs.writeFileSync(path.join(pathBin, "devin-bridge" + EXE), "#!/bin/sh\n", { mode: 0o755 });
process.env.PATH = `${pathBin}${path.delimiter}${process.env.PATH}`;

ext.activate({ subscriptions: [] });

const FORBIDDEN = new Set([
  "new", "resume", "prompt", "intake",
  "--init", "--apply", "--force", "--yes", "--dry-run", "--file", "--model",
]);
const assertCallsReadOnly = () => {
  for (const c of calls) {
    for (const token of c.args) {
      assert.ok(!FORBIDDEN.has(token), `forbidden arg reached the CLI: ${token}`);
    }
    assert.ok(!("shell" in c.opts), "spawn must never use a shell");
  }
};

test("READONLY_ARGS is exactly the read-only CLI surface", () => {
  assert.deepStrictEqual(__internals__.READONLY_ARGS, {
    sessions: ["sessions", "--json"],
    policyShow: ["policy", "--show"],
    probe: ["probe", "--no-session"],
  });
});

test("CAPS is the fixed capability enum", () => {
  assert.deepStrictEqual(__internals__.CAPS, ["terminal", "fsRead", "fsWrite", "network"]);
});

test("parseJson parses payloads and names the command on failure", () => {
  const parse = __internals__.parseJson;
  assert.deepStrictEqual(parse('[{"repo":"x"}]', "sessions"), [{ repo: "x" }]);
  assert.throws(() => parse("(no sessions)", "sessions"), /sessions/);
});

test("spawnSpec resolves an absolute target — never a bare name or shell", () => {
  settings = {};
  const spec = __internals__.spawnSpec(["sessions", "--json"]);
  assert.ok(spec, "fixture devin-bridge on PATH must resolve");
  if (process.platform === "win32") {
    // the file is either the resolved binary or node on a resolved .js
    // entry — never cmd.exe.
    assert.notStrictEqual(spec.file, "cmd.exe");
  } else {
    assert.ok(path.isAbsolute(spec.file), "spawn target must be absolute");
    assert.deepStrictEqual(spec.argv, ["sessions", "--json"]);
  }
});

test("a .js executable spawns through the editor's own node", () => {
  settings = { executable: "/opt/bridge/bin/devin-bridge.js" };
  const spec = __internals__.spawnSpec(["sessions", "--json"]);
  assert.strictEqual(spec.file, process.execPath);
  assert.deepStrictEqual(spec.argv, ["/opt/bridge/bin/devin-bridge.js", "sessions", "--json"]);
  settings = {};
});

// Windows cwd-search fix: an executable planted inside an UNTRUSTED
// workspace must never be the spawn target; the trusted case keeps
// working for repos that deliberately carry their own tools.
const withWorkspace = (ws, trusted, fn) => {
  const oldFolders = vscodeStub.workspace.workspaceFolders;
  const oldTrusted = vscodeStub.workspace.isTrusted;
  const oldPath = process.env.PATH;
  vscodeStub.workspace.workspaceFolders = [{ uri: { fsPath: ws } }];
  vscodeStub.workspace.isTrusted = trusted;
  process.env.PATH = `${ws}${path.delimiter}${oldPath}`; // ws first: planted wins PATH order
  try { return fn(); } finally {
    vscodeStub.workspace.workspaceFolders = oldFolders;
    vscodeStub.workspace.isTrusted = oldTrusted;
    process.env.PATH = oldPath;
  }
};

test("untrusted workspace skips a repo-planted executable", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "bridge-ws-"));
  const planted = path.join(ws, "devin-bridge" + EXE);
  fs.writeFileSync(planted, "#!/bin/sh\n", { mode: 0o755 });
  try {
    const spec = withWorkspace(ws, false,
      () => __internals__.spawnSpec(["probe", "--no-session"]));
    assert.ok(spec, "the PATH fixture outside the workspace still resolves");
    assert.ok(!spec.file.startsWith(ws + path.sep),
      `spawn target must not be the planted binary: ${spec.file}`);
  } finally { fs.rmSync(ws, { recursive: true, force: true }); }
});

test("trusted workspace allows a repo-local executable", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "bridge-ws-"));
  const planted = path.join(ws, "devin-bridge" + EXE);
  fs.writeFileSync(planted, "#!/bin/sh\n", { mode: 0o755 });
  try {
    const spec = withWorkspace(ws, true,
      () => __internals__.spawnSpec(["probe", "--no-session"]));
    assert.ok(spec);
    assert.strictEqual(spec.file, planted);
  } finally { fs.rmSync(ws, { recursive: true, force: true }); }
});

test("sessions spawns the whitelisted args in the workspace dir", async () => {
  calls.length = 0;
  nextResult = { code: 0, stdout: '[{"repo":"a","sessionId":"s1"},{"repo":"b","sessionId":"s2"}]', stderr: "" };
  const list = await __internals__.sessions();
  assert.strictEqual(list.length, 2);
  assert.strictEqual(calls.length, 1);
  assert.deepStrictEqual(calls[0].args, ["sessions", "--json"]);
  assert.strictEqual(calls[0].opts.cwd, "/repo");
  assert.match(statusBarStub.text, /2 sessions/);
  assertCallsReadOnly();
});

test("policy --show renders the effective policy", async () => {
  calls.length = 0;
  nextResult = { code: 0, stdout: '{"version":1,"rules":[]}', stderr: "" };
  await __internals__.policyShow();
  assert.deepStrictEqual(calls[0].args, ["policy", "--show"]);
  assertCallsReadOnly();
});

test("policyCheck passes cap and a -- end-of-options marker before the target", async () => {
  calls.length = 0;
  quickPickValue = "terminal";
  inputBoxValue = "--version";
  nextResult = { code: 0, stdout: '{"capability":"terminal","target":"--version","decision":"ask"}', stderr: "" };
  const decision = await __internals__.policyCheck();
  assert.strictEqual(decision.target, "--version");
  assert.deepStrictEqual(calls[0].args, ["policy", "--check", "terminal", "--", "--version"]);
  assertCallsReadOnly();
});

test("a metachar target reaches the CLI as ONE literal arg (no injection)", async () => {
  calls.length = 0;
  quickPickValue = "terminal";
  inputBoxValue = "x & calc";
  nextResult = { code: 0, stdout: '{"capability":"terminal","target":"x & calc","decision":"ask"}', stderr: "" };
  await __internals__.policyCheck();
  assert.deepStrictEqual(calls[0].args, ["policy", "--check", "terminal", "--", "x & calc"]);
  assert.ok(!("shell" in calls[0].opts), "no shell anywhere near a metachar target");
});

test("policyCheck aborts without spawning when input is cancelled", async () => {
  calls.length = 0;
  quickPickValue = undefined;
  inputBoxValue = "x";
  await __internals__.policyCheck();
  quickPickValue = "fsRead";
  inputBoxValue = undefined;
  await __internals__.policyCheck();
  assert.strictEqual(calls.length, 0);
});

test("probe always runs --no-session and renders the verdict", async () => {
  calls.length = 0;
  nextResult = { code: 0, stdout: '{"ok":true,"checks":[{"check":"init","ok":true}]}', stderr: "" };
  const okReport = await __internals__.probe();
  assert.strictEqual(okReport.ok, true);
  assert.deepStrictEqual(calls[0].args, ["probe", "--no-session"]);
  assert.match(statusBarStub.text, /connected/);

  nextResult = { code: 1, stdout: '{"ok":false,"checks":[{"check":"init","ok":false}]}', stderr: "" };
  const failReport = await __internals__.probe();
  assert.strictEqual(failReport.ok, false);
  assert.match(statusBarStub.text, /probe failed/);
  assert.strictEqual(statusBarStub.backgroundColor.id, "statusBarItem.errorBackground");
  assertCallsReadOnly();
});

test("a failed call renders unavailable without leaving a stale color", async () => {
  statusBarStub.backgroundColor = { id: "statusBarItem.warningBackground" };
  calls.length = 0;
  nextResult = { code: 2, stdout: "", stderr: "boom" };
  const out = await __internals__.sessions();
  assert.strictEqual(out, null);
  assert.match(statusBarStub.text, /unavailable/);
  assert.strictEqual(statusBarStub.backgroundColor, undefined);
  assertCallsReadOnly();
});

test("manifest declares limited untrusted-workspace support", () => {
  const manifest = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "package.json"), "utf8"),
  );
  const uw = manifest.capabilities.untrustedWorkspaces;
  assert.strictEqual(uw.supported, "limited");
  assert.deepStrictEqual(uw.restrictedConfigurations.sort(), [
    "devinBridge.checkOnStartup",
    "devinBridge.executable",
  ]);
});

test("checkOnStartup never spawns in an untrusted workspace", () => {
  const context = { subscriptions: [] };
  settings = { checkOnStartup: true };
  vscodeStub.workspace.isTrusted = false;
  calls.length = 0;
  ext.activate(context);
  assert.strictEqual(calls.length, 0);
  vscodeStub.workspace.isTrusted = true;
  ext.activate(context);
  assert.strictEqual(calls.length, 1);
  assert.deepStrictEqual(calls[0].args, ["sessions", "--json"]);
  settings = {};
  assertCallsReadOnly();
});
