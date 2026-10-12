"use strict";

// Minimal vscode stub injected before requiring the extension — enough for
// the pure paths to run outside the editor.
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
    createStatusBarItem: () => ({ show() {}, hide() {} }),
    showInformationMessage: () => Promise.resolve(),
    showErrorMessage: () => Promise.resolve(),
    showQuickPick: () => Promise.resolve(undefined),
    showInputBox: () => Promise.resolve(undefined),
    showTextDocument: () => Promise.resolve({ edit: async () => true }),
  },
  workspace: {
    getConfiguration: () => ({ get: (k, d) => d }),
    workspaceFolders: [{ uri: { fsPath: "/repo" } }],
    openTextDocument: () => Promise.resolve({}),
  },
  commands: { registerCommand: () => ({ dispose() {} }) },
};

const Module = require("module");
const origRequire = Module.prototype.require;
Module.prototype.require = function (id) {
  if (id === "vscode") return vscodeStub;
  return origRequire.apply(this, arguments);
};

const { __internals__ } = require("../extension.js");
const test = require("node:test");
const assert = require("node:assert");

test("READONLY_ARGS is exactly the read-only CLI surface", () => {
  assert.deepStrictEqual(__internals__.READONLY_ARGS, {
    sessions: ["sessions", "--json"],
    policyShow: ["policy", "--show"],
    probe: ["probe", "--no-session"],
  });
});

test("probe args always include --no-session", () => {
  assert.ok(__internals__.READONLY_ARGS.probe.includes("--no-session"));
});

test("CAPS is the fixed capability enum", () => {
  assert.deepStrictEqual(__internals__.CAPS, ["terminal", "fsRead", "fsWrite", "network"]);
});

test("parseJson parses payloads and names the command on failure", () => {
  const parse = __internals__.parseJson;
  assert.deepStrictEqual(parse('[{"repo":"x"}]', "sessions"), [{ repo: "x" }]);
  assert.throws(() => parse("(no sessions)", "sessions"), /sessions/);
});
