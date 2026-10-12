"use strict";

// Thin adapter: the extension shells out to `devin-bridge` and renders the
// result — no ACP, session or policy logic is reimplemented here.
//
// READ-ONLY BY CONSTRUCTION. Every CLI call goes through runBridge and the
// only arg arrays it can ever receive are the whitelisted literals in
// READONLY_ARGS below: sessions --json, policy --show, policy --check and
// probe --no-session. Session control (the new/resume/prompt subcommands,
// mailbox intake, policy --init, or a probe that creates a session) is
// unreachable — orchestration mutation stays on the real CLI.
// test/cli-contract.test.js asserts the whitelist is the entire surface.

const vscode = require("vscode");
const cp = require("child_process");

let statusBar;
// Monotonic counter — a slow call finishing late must not overwrite a
// newer verdict already rendered on the status bar.
let seq = 0;

// Contract with the CLI JSON payloads. Keep in sync with the
// "devin-bridge-cli" range in package.json — test/cli-contract.test.js
// asserts it.
const COMPATIBLE_CLI = { min: [0, 1], maxExclusive: [0, 2] };

// The entire CLI surface this adapter can reach. --no-session is not
// optional on probe: without it the probe creates a session.
const READONLY_ARGS = {
  sessions: ["sessions", "--json"],
  policyShow: ["policy", "--show"],
  probe: ["probe", "--no-session"],
};

// Valid capabilities for `policy --check` — the only command whose args
// include user input (the target). The capability is a fixed enum.
const CAPS = ["terminal", "fsRead", "fsWrite", "network"];

function config() {
  const c = vscode.workspace.getConfiguration("devinBridge");
  return { executable: c.get("executable", "devin-bridge") };
}

// .sessions.json and policy.json resolve relative to the repo — run in the
// workspace folder so the view matches `devin-bridge` on the command line.
function workspaceDir() {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders || !folders.length) {
    throw new Error("devin-bridge adapter needs an open workspace folder");
  }
  return folders[0].uri.fsPath;
}

// Runs the CLI once and resolves {code, stdout, stderr}. Never throws on a
// non-zero exit — probe exits 1 when a check fails, which is a renderable
// result, not an error.
function runBridge(args) {
  const { executable } = config();
  return new Promise((resolve, reject) => {
    cp.execFile(
      executable,
      args,
      { cwd: workspaceDir(), timeout: 120000, maxBuffer: 8 * 1024 * 1024 },
      (err, stdout, stderr) => {
        if (err && err.killed) {
          reject(new Error("devin-bridge timed out after 120s"));
          return;
        }
        if (err && err.code === "ENOENT") {
          reject(
            new Error(
              "devin-bridge not found on PATH. Install it " +
              "(npm i -g @icaro0310/devin-bridge) or set devinBridge.executable.",
            ),
          );
          return;
        }
        resolve({ code: err ? err.code : 0, stdout, stderr });
      },
    );
  });
}

function parseJson(stdout, what) {
  try {
    return JSON.parse(stdout);
  } catch {
    throw new Error(`devin-bridge did not return JSON for ${what} — got text output`);
  }
}

async function showJson(payload) {
  const doc = await vscode.workspace.openTextDocument({ language: "json" });
  const editor = await vscode.window.showTextDocument(doc, { preview: true });
  await editor.edit((b) =>
    b.insert(new vscode.Position(0, 0), JSON.stringify(payload, null, 2)));
}

function render(text, tooltip, bg) {
  statusBar.text = text;
  statusBar.tooltip = tooltip;
  statusBar.backgroundColor = bg;
  statusBar.show();
}

async function sessions() {
  const mySeq = ++seq;
  render("$(sync~spin) bridge: sessions", "Listing devin-bridge sessions", undefined);
  try {
    const { stdout } = await runBridge(READONLY_ARGS.sessions);
    const list = parseJson(stdout, "sessions");
    if (mySeq === seq) {
      render(
        `$(server) bridge: ${list.length} session${list.length === 1 ? "" : "s"}`,
        "devin-bridge sessions in this workspace",
        undefined,
      );
    }
    await showJson(list);
    return list;
  } catch (e) {
    if (mySeq === seq) render("$(error) bridge: unavailable", e.message, undefined);
    vscode.window.showErrorMessage(e.message);
    return null;
  }
}

async function policyShow() {
  const { stdout } = await runBridge(READONLY_ARGS.policyShow);
  await showJson(parseJson(stdout, "policy"));
}

async function policyCheck() {
  const cap = await vscode.window.showQuickPick(CAPS, { placeHolder: "Capability to check" });
  if (!cap) return;
  const target = await vscode.window.showInputBox({
    prompt: `Target to test against the ${cap} rule (command, path or host)`,
  });
  if (target === undefined) return;
  const { stdout, stderr } = await runBridge(["policy", "--check", cap, target]);
  const decision = parseJson(stdout, "policy check");
  vscode.window.showInformationMessage(
    `devin-bridge policy: ${decision.capability} ${JSON.stringify(decision.target)} → ${decision.decision}`,
  );
  return decision;
}

async function probe() {
  const mySeq = ++seq;
  render("$(sync~spin) bridge: probing", "devin-bridge probe --no-session", undefined);
  try {
    // Exit code 1 means a check failed — still a renderable report.
    const { stdout } = await runBridge(READONLY_ARGS.probe);
    const report = parseJson(stdout, "probe");
    if (mySeq === seq) {
      const warn = report.ok ? undefined : new vscode.ThemeColor("statusBarItem.errorBackground");
      render(
        report.ok ? "$(pass) bridge: connected" : "$(error) bridge: probe failed",
        `ACP handshake ${report.ok ? "ok" : "failed"} (${(report.checks || []).length} checks)`,
        warn,
      );
    }
    await showJson(report);
    return report;
  } catch (e) {
    if (mySeq === seq) render("$(error) bridge: unavailable", e.message, undefined);
    vscode.window.showErrorMessage(e.message);
    return null;
  }
}

function activate(context) {
  statusBar = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Left, 50);
  statusBar.command = "devin-bridge.sessions";
  statusBar.text = "$(plug) bridge";
  statusBar.tooltip = "devin-bridge — click to list sessions (read-only)";
  statusBar.show();

  context.subscriptions.push(
    statusBar,
    vscode.commands.registerCommand("devin-bridge.sessions", sessions),
    vscode.commands.registerCommand("devin-bridge.policy", policyShow),
    vscode.commands.registerCommand("devin-bridge.policyCheck", policyCheck),
    vscode.commands.registerCommand("devin-bridge.probe", probe),
  );

  if (vscode.workspace.getConfiguration("devinBridge").get("checkOnStartup", false)) {
    sessions();
  }
}

function deactivate() {}

module.exports = { activate, deactivate };
// Test-only surface — pure internals reachable without the editor.
module.exports.__internals__ = { COMPATIBLE_CLI, READONLY_ARGS, CAPS, parseJson };
