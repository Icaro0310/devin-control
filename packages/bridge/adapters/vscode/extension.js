"use strict";

// Thin adapter: the extension shells out to `devin-bridge` and renders the
// result — no ACP, session or policy logic is reimplemented here.
//
// READ-ONLY BY CONSTRUCTION. Every CLI call goes through runBridge fed by
// whitelisted literal arg arrays: sessions --json, policy --show,
// policy --check -- <target>, probe --no-session. Session control
// (new/resume/prompt subcommands, mailbox intake, policy --init, a probe
// that creates a session) is unreachable — orchestration mutation stays on
// the real CLI. test/cli-contract.test.js asserts the whole surface.

const vscode = require("vscode");
const cp = require("child_process");
// Shared Restricted-Mode-safe executable resolver — byte-identical copy of
// devin-powerups' adapters/shared/safe-executable.cjs (lockstep enforced by
// config-restricted-check). On Windows, CreateProcess searches cwd before
// PATH: spawn only the absolute path this returns.
const { resolveSafeExecutable } = require("./safe-executable.cjs");

let statusBar;
let seq = 0; // monotonic — a slow call must not overwrite a newer verdict

// CLI JSON payload contract — sync with "devin-bridge-cli" in package.json.
const COMPATIBLE_CLI = { min: [0, 1], maxExclusive: [0, 2] };

// The entire CLI surface this adapter can reach — --no-session is not
// optional on probe (without it the probe creates a session).
const READONLY_ARGS = {
  sessions: ["sessions", "--json"],
  policyShow: ["policy", "--show"],
  probe: ["probe", "--no-session"],
};

// The only command taking user input: capability is a fixed enum; the
// target goes after a "--" end-of-options marker so "--version"-shaped
// targets reach the check literally.
const CAPS = ["terminal", "fsRead", "fsWrite", "network"];

function config() {
  const c = vscode.workspace.getConfiguration("devinBridge");
  return { executable: c.get("executable", "devin-bridge") };
}

// .sessions.json and policy.json resolve relative to the repo — run in the
// workspace folder so the view matches the CLI on the command line. All
// open folders feed the resolver's inside-workspace boundary (multi-root:
// a binary in ANY folder is workspace code); the first is the spawn cwd.
function workspaceDirs() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || !folders.length) {
    throw new Error("devin-bridge adapter needs an open workspace folder");
  }
  return folders.map((f) => f.uri.fsPath);
}

// What to spawn for a call. A .js executable always runs through the
// editor's own Node. Everything else resolves through the shared helper:
// it walks PATH itself (where.exe searches cwd first — the very vector
// this closes), unwraps npm .cmd/.bat shims to their .js entry, and in an
// untrusted workspace refuses any candidate inside the repo. Null = not
// found; runBridge surfaces the install hint. Never shell:true: argv
// would pass through cmd's string interpretation.
function spawnSpec(args) {
  const { executable } = config();
  if (/\.js$/i.test(executable)) {
    return { file: process.execPath, argv: [executable, ...args] };
  }
  const spec = resolveSafeExecutable(executable, {
    workspaceDirs: workspaceDirs(),
    trusted: vscode.workspace.isTrusted,
    npmPackage: "@icaro0310/devin-bridge",
  });
  return spec ? { file: spec.file, argv: spec.argv.concat(args) } : null;
}

// Resolves {code, stdout, stderr}; non-zero exit is a renderable result
// (probe exits 1 on a failed check), not an error.
function runBridge(args) {
  return new Promise((resolve, reject) => {
    const spec = spawnSpec(args);
    if (!spec) return reject(new Error(
      "devin-bridge not found on PATH. Install it " +
      "(npm i -g @icaro0310/devin-bridge) or set devinBridge.executable."));
    cp.execFile(spec.file, spec.argv,
      { cwd: workspaceDirs()[0], timeout: 120000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err && err.killed) return reject(new Error("devin-bridge timed out after 120s"));
        if (err && err.code === "ENOENT") return reject(new Error(
          "devin-bridge not found on PATH. Install it " +
          "(npm i -g @icaro0310/devin-bridge) or set devinBridge.executable."));
        resolve({ code: err ? err.code : 0, stdout, stderr });
      });
  });
}

function parseJson(stdout, what) {
  try { return JSON.parse(stdout); } catch {
    throw new Error(`devin-bridge did not return JSON for ${what} — got text output`);
  }
}

async function showJson(payload) {
  const doc = await vscode.workspace.openTextDocument({ language: "json" });
  const editor = await vscode.window.showTextDocument(doc, { preview: true });
  const text = JSON.stringify(payload, null, 2);
  await editor.edit((b) => b.insert(new vscode.Position(0, 0), text));
}

const render = (text, tooltip, bg) => {
  statusBar.text = text; statusBar.tooltip = tooltip;
  statusBar.backgroundColor = bg; statusBar.show();
};

async function sessions() {
  const mySeq = ++seq;
  render("$(sync~spin) bridge: sessions", "Listing devin-bridge sessions");
  try {
    const { stdout } = await runBridge(READONLY_ARGS.sessions);
    const list = parseJson(stdout, "sessions");
    if (mySeq === seq) {
      render(`$(server) bridge: ${list.length} session${list.length === 1 ? "" : "s"}`,
        "devin-bridge sessions in this workspace");
    }
    await showJson(list);
    return list;
  } catch (e) {
    if (mySeq !== seq) return null;
    render("$(error) bridge: unavailable", e.message);
    vscode.window.showErrorMessage(e.message);
    return null;
  }
}

const policyShow = async () =>
  showJson(parseJson((await runBridge(READONLY_ARGS.policyShow)).stdout, "policy"));

async function policyCheck() {
  const cap = await vscode.window.showQuickPick(CAPS, { placeHolder: "Capability to check" });
  if (!cap) return;
  const target = await vscode.window.showInputBox(
    { prompt: `Target to test against the ${cap} rule (command, path or host)` });
  if (target === undefined) return;
  const { stdout } = await runBridge(["policy", "--check", cap, "--", target]);
  const decision = parseJson(stdout, "policy check");
  vscode.window.showInformationMessage(
    `devin-bridge policy: ${decision.capability} ${JSON.stringify(decision.target)} → ${decision.decision}`);
  return decision;
}

async function probe() {
  const mySeq = ++seq;
  render("$(sync~spin) bridge: probing", "devin-bridge probe --no-session");
  try {
    // Exit code 1 means a check failed — still a renderable report.
    const { stdout } = await runBridge(READONLY_ARGS.probe);
    const report = parseJson(stdout, "probe");
    if (mySeq === seq) {
      const warn = report.ok ? undefined : new vscode.ThemeColor("statusBarItem.errorBackground");
      render(report.ok ? "$(pass) bridge: connected" : "$(error) bridge: probe failed",
        `ACP handshake ${report.ok ? "ok" : "failed"} (${(report.checks || []).length} checks)`, warn);
    }
    await showJson(report);
    return report;
  } catch (e) {
    if (mySeq !== seq) return null;
    render("$(error) bridge: unavailable", e.message);
    vscode.window.showErrorMessage(e.message);
    return null;
  }
}

function activate(context) {
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusBar.command = "devin-bridge.sessions";
  render("$(plug) bridge", "devin-bridge — click to list sessions (read-only)");
  const cmds = {
    "devin-bridge.sessions": sessions, "devin-bridge.policy": policyShow,
    "devin-bridge.policyCheck": policyCheck, "devin-bridge.probe": probe,
  };
  context.subscriptions.push(statusBar,
    ...Object.entries(cmds).map(([id, fn]) => vscode.commands.registerCommand(id, fn)));
  if (vscode.workspace.isTrusted &&
      vscode.workspace.getConfiguration("devinBridge").get("checkOnStartup", false)) sessions();
}

function deactivate() {}

module.exports = { activate, deactivate };
// Test-only surface — pure internals reachable without the editor.
module.exports.__internals__ = {
  COMPATIBLE_CLI, READONLY_ARGS, CAPS, parseJson, spawnSpec,
  sessions, policyShow, policyCheck, probe,
};
