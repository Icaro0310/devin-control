#!/usr/bin/env node
/**
 * devin-bridge — policy-gated bridge that drives `devin acp` on Windows or Linux.
 *
 * Commands:
 *   new <repo-dir>                 create an isolated session (updates .sessions.json)
 *   resume <repo-dir> [--resume id]  reconnect to a mapped/explicit session
 *   prompt <repo-dir> <text>|--file f  send a prompt, stream the reply
 *   sessions [--json]              list repo→sessionId mappings
 *   policy --show|--init|--check <cap> <target>  inspect the permission policy
 *
 * Global options:
 *   --sessions-file <f>   mapping file (default ./.sessions.json)
 *   --policy <f>          policy file (default ./policy.json if present,
 *                         else the fail-closed built-in: everything asks)
 *   --bin <path>          agent executable (default: BRIDGE_AGENT_BIN →
 *                           BRIDGE_AGENT strategy → devin autodetect)
 *   --timeout-ms <n>      per-call timeout (default 300000; prompt waits up to 45min)
 *   --yes                 auto-approve policy "ask" prompts (headless runs)
 *   --label origin:purpose  tag sessions the bridge creates (default
 *                           "bridge:unlabeled"; sent as session/new _meta
 *                           and recorded in the labels sidecar)
 *   --state-dir <d>       bridge state dir (default: DEVIN_BRIDGE_STATE_DIR
 *                         or platform state dir; holds session-labels.json)
 *   --help
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { DevinAcp } from "../src/acp-client.js";
import { Policy, examplePolicyDoc, presetPolicyDoc } from "../src/policy.js";
import { SessionMap, ensureSession, runTask } from "../src/dispatch.js";
import {
  LABELS_FILENAME, LabelStore, bridgeStateDir, parseLabel,
} from "../src/labels.js";
import {
  mailboxDir, checkPermissions, parseTask, listInbox, moveTo,
} from "../src/mailbox.js";

const CAPS = ["terminal", "fsRead", "fsWrite", "network"];

function parseArgs(argv) {
  const opts = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    // End-of-options marker: everything after "--" is positional, so
    // targets like "--version" reach commands literally (policy --check).
    if (a === "--") { opts._.push(...argv.slice(i + 1)); break; }
    if (!a.startsWith("--")) { opts._.push(a); continue; }
    const [k, inline] = a.slice(2).split("=", 2);
    if (inline !== undefined) { opts[k] = inline; continue; }
    const next = argv[i + 1];
    if (next !== undefined && !next.startsWith("--")) { opts[k] = next; i++; }
    else opts[k] = true;
  }
  return opts;
}

function usage(exitCode = 0) {
  console.log(`devin-bridge <command> [args] [options]

Commands:
  new <repo-dir>                    create a session for the repo (records mapping)
  resume <repo-dir> [--resume <id>] reconnect to a stored/explicit session
  prompt <repo-dir> <text>          send a prompt (or --file <f>), stream the reply
  sessions [--json]                 list repo -> session mappings
  policy --show                     print the effective policy
  policy --init [--force] [--preset ask|read-only|full]
                                    write ./policy.json (preset or documented defaults)
  policy --check <cap> <target>     decision for terminal|fsRead|fsWrite|network
  probe [--no-session]              ACP compatibility probe: handshake, auth,
                                    methods; --no-session skips session/new
  intake [--dry-run] [--repo <dir>] process mailbox tasks: JSON files in
                                    <state-dir>/mailbox/inbox/ (untrusted
                                    input — policy still gates every step)

Options: --sessions-file f --policy f --preset ask|read-only|full
         --bin path --timeout-ms n --yes --help
         --label origin:purpose (default bridge:unlabeled) --state-dir d

Agent env: BRIDGE_AGENT_BIN path — explicit executable (any ACP CLI)
         BRIDGE_AGENT name — strategy from the registry (default devin)
         BRIDGE_AGENT_ARGS '["sub","--flags"]' — argv override (JSON array)
         BRIDGE_CREDENTIALS_PATH file — credentials.toml override

Presets: read-only denies terminal/fsWrite/network (observe-only intake);
full allows everything — HIGH RISK, trusted scratch environments only.`);
  process.exit(exitCode);
}

function loadPolicy(opts) {
  const file = opts.policy || (fs.existsSync("policy.json") ? "policy.json" : null);
  if (file) {
    console.error(`[policy] ${path.resolve(file)}`);
    return Policy.load(file, { root: process.cwd() });
  }
  if (opts.preset) {
    if (opts.preset === "full") {
      console.error("[policy] WARNING: preset 'full' allows everything — trusted environments only");
    }
    console.error(`[policy] preset ${opts.preset}`);
    return new Policy(presetPolicyDoc(opts.preset), { root: process.cwd() });
  }
  console.error("[policy] built-in default (everything requires asking)");
  return Policy.default();
}

/** Interactive askHandler: explicit y/yes approves, anything else denies. */
function interactiveAsker() {
  const rl = readline.createInterface({ input: process.stdin, output: process.stderr });
  process.on("exit", () => rl.close());
  return (req) => new Promise((resolve) => {
    const desc = req.command ?? req.path ?? req.reason ?? req.capability;
    rl.question(`[policy:ask] agent wants ${req.capability} ${desc} — allow? [y/N] `, (ans) => {
      resolve(/^y(es)?$/i.test(ans.trim()) ? "allow" : "deny");
    });
  });
}

function makeClient(opts, { forPrompt = false, policy = null } = {}) {
  const askHandler = opts.yes
    ? async () => "allow"
    : (process.stdin.isTTY && process.stderr.isTTY ? interactiveAsker() : null);
  const onNotification = forPrompt
    ? (msg) => {
        const upd = msg.params?.update;
        if (msg.method === "session/update" && upd?.sessionUpdate === "agent_message_chunk"
            && upd.content?.type === "text") {
          process.stderr.write(upd.content.text); // live stream; stdout stays clean
        }
      }
    : null;
  const acp = new DevinAcp({
    bin: opts.bin || undefined,
    cwd: process.cwd(),
    policy: policy || loadPolicy(opts),
    askHandler,
    timeoutMs: opts["timeout-ms"] ? Number(opts["timeout-ms"]) : undefined,
    onNotification,
  });
  acp.start();
  return acp;
}

/** Labels sidecar in the bridge state dir (--state-dir > env > platform). */
function loadLabelStore(opts) {
  const dir = opts["state-dir"]
    ? path.resolve(opts["state-dir"])
    : bridgeStateDir();
  return LabelStore.load(path.join(dir, LABELS_FILENAME));
}

const CMDS = {
  async new(opts) {
    const repoDir = opts._[0];
    if (!repoDir) usage(1);
    const abs = path.resolve(repoDir);
    if (!fs.existsSync(abs)) throw new Error(`repo dir does not exist: ${abs}`);
    const label = parseLabel(opts.label);
    const store = loadLabelStore(opts);
    const acp = makeClient(opts);
    try {
      await acp.init();
      await acp.newSession(abs, { label });
      const file = opts["sessions-file"] || ".sessions.json";
      const map = SessionMap.load(file);
      map.set(path.basename(abs), { sessionId: acp.sessionId, cwd: abs });
      map.save();
      store.record(acp.sessionId, { ...label, cwd: abs });
      store.save();
      if (opts.model) await acp.setModel(opts.model);
      console.log(JSON.stringify({
        repo: path.basename(abs), sessionId: acp.sessionId,
        label: label.label,
        models: acp.listModels(), model: acp.currentModel(),
      }, null, 2));
    } finally { acp.stop(); }
  },

  async resume(opts) {
    const repoDir = opts._[0];
    if (!repoDir) usage(1);
    const abs = path.resolve(repoDir);
    if (!fs.existsSync(abs)) throw new Error(`repo dir does not exist: ${abs}`);
    const acp = makeClient(opts);
    try {
      await acp.init();
      const file = opts["sessions-file"] || ".sessions.json";
      const map = SessionMap.load(file);
      const store = loadLabelStore(opts);
      const out = await ensureSession(acp, abs, {
        map, resumeId: opts.resume,
        label: parseLabel(opts.label), labelStore: store,
      });
      map.save();
      store.save();
      console.log(JSON.stringify({
        repo: path.basename(abs), ...out, model: acp.currentModel(),
      }, null, 2));
    } finally { acp.stop(); }
  },

  async prompt(opts) {
    const repoDir = opts._[0];
    if (!repoDir) usage(1);
    const abs = path.resolve(repoDir);
    const promptText = opts.file
      ? fs.readFileSync(opts.file, "utf8")
      : opts._.slice(1).join(" ");
    const acp = makeClient(opts, { forPrompt: true });
    try {
      await acp.init();
      const res = await runTask(acp, abs, {
        promptText,
        sessionsFile: opts["sessions-file"] || ".sessions.json",
        resumeId: opts.resume,
        timeoutMs: opts["timeout-ms"] ? Number(opts["timeout-ms"]) : 45 * 60 * 1000,
        label: parseLabel(opts.label),
        labelStore: loadLabelStore(opts),
      });
      process.stderr.write("\n");
      console.log("===== RESULT =====");
      console.log(JSON.stringify({
        repo: res.repo, sessionId: res.sessionId, resumed: res.resumed,
        model: res.modelLabel, stopReason: res.stopReason,
        cost: res.cost, timedOut: res.timedOut,
      }, null, 2));
      console.log("===== TEXT =====");
      console.log(res.text);
    } finally { acp.stop(); }
  },

  sessions(opts) {
    const file = opts["sessions-file"] || ".sessions.json";
    const map = SessionMap.load(file);
    const store = loadLabelStore(opts);
    const withLabels = map.list().map((e) => ({
      ...e, label: store.get(e.sessionId)?.label || null,
    }));
    if (opts.json) return console.log(JSON.stringify(withLabels, null, 2));
    if (!withLabels.length) return console.log(`(no sessions in ${path.resolve(file)})`);
    for (const e of withLabels) {
      console.log(`${e.repo}\t${e.sessionId}\t${e.updatedAt}\t${e.cwd}\t${e.label || "-"}`);
    }
  },

  /** BR-1: ACP compatibility probe (foundation for drift checks). */
  async probe(opts) {
    const acp = makeClient(opts);
    const report = { bin: acp.bin || null, checks: [] };
    const mark = (name, ok, detail) =>
      report.checks.push({ check: name, ok, ...(detail ? { detail } : {}) });
    try {
      const t0 = Date.now();
      await acp.init();
      mark("initialize+authenticate", true, `${Date.now() - t0}ms`);
      if (!opts["no-session"]) {
        // probe session in a temp dir, labelled so janitor can reap it
        const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "devin-bridge-probe-"));
        try {
          const res = await acp.newSession(tmp, {
            label: { origin: "bridge", purpose: "probe", label: "bridge:probe" },
          });
          mark("session/new", true);
          report.sessionId = res.sessionId;
          report.models = acp.listModels();
          report.configOptions = acp.configOptions;
          loadLabelStore(opts).record(res.sessionId, {
            label: "bridge:probe", origin: "bridge", purpose: "probe", cwd: tmp,
          }).save();
        } catch (e) {
          mark("session/new", false, e.message);
        }
      }
    } catch (e) {
      mark("initialize+authenticate", false, e.message);
    } finally { acp.stop(); }
    report.ok = report.checks.every((c) => c.ok);
    console.log(JSON.stringify(report, null, 2));
    if (!report.ok) process.exitCode = 1;
  },

  /** BR-3: file-based mailbox intake — no listener, untrusted input. */
  async intake(opts) {
    const stateDir = opts["state-dir"]
      ? path.resolve(opts["state-dir"]) : bridgeStateDir();
    const root = mailboxDir(stateDir);
    checkPermissions(path.join(root, "inbox"));
    // --dry-run skips the policy load: dry-run never builds an ACP
    // client, so the policy is unused on this path and a missing/invalid
    // --policy must not block task validation. On a real run the policy
    // still loads here, before any task leaves inbox/, so a config error
    // aborts early and the next scheduled run retries the same files
    // once it is fixed.
    const policy = opts["dry-run"] ? null : loadPolicy(opts);
    const files = listInbox(root);
    const store = loadLabelStore(opts);
    const results = [];
    for (const file of files) {
      const name = path.basename(file);
      const parsed = parseTask(file);
      if (!parsed.ok) {
        const dst = moveTo(root, file, "failed");
        fs.writeFileSync(`${dst}.err`, parsed.error + "\n");
        results.push({ file: name, status: "rejected", error: parsed.error });
        continue;
      }
      const t = parsed.task;
      const repoDir = t.repo || (opts.repo ? path.resolve(opts.repo) : process.cwd());
      if (opts["dry-run"]) {
        results.push({ file: name, status: "dry-run", repo: repoDir,
          promptPreview: t.prompt.slice(0, 80) });
        continue;
      }
      const work = moveTo(root, file, "processing");
      let acp;
      try {
        acp = makeClient(opts, { forPrompt: true, policy });
        await acp.init();
        const res = await runTask(acp, repoDir, {
          promptText: t.prompt,
          sessionsFile: opts["sessions-file"] || ".sessions.json",
          timeoutMs: opts["timeout-ms"] ? Number(opts["timeout-ms"]) : 45 * 60 * 1000,
          label: { origin: "mailbox", purpose: name.replace(/\.json$/, ""),
            label: `mailbox:${name.replace(/\.json$/, "")}` },
          labelStore: store,
        });
        if (t.model) await acp.setModel(t.model);
        store.save();
        const dst = moveTo(root, work, "done");
        fs.writeFileSync(`${dst}.result.json`, JSON.stringify({
          sessionId: res.sessionId, stopReason: res.stopReason,
          cost: res.cost, timedOut: res.timedOut, text: res.text,
        }, null, 2) + "\n");
        results.push({ file: name, status: "done", sessionId: res.sessionId,
          stopReason: res.stopReason });
      } catch (e) {
        const dst = moveTo(root, work, "failed");
        fs.writeFileSync(`${dst}.err`, String(e.message) + "\n");
        results.push({ file: name, status: "failed", error: e.message });
      } finally { acp?.stop(); }
    }
    console.log(JSON.stringify({ mailbox: root, results }, null, 2));
  },

  policy(opts) {
    const file = opts.policy || "policy.json";
    if (opts.init !== undefined) {
      if (fs.existsSync(file) && !opts.force) {
        throw new Error(`${file} already exists (use --force to overwrite)`);
      }
      const doc = opts.preset ? presetPolicyDoc(opts.preset) : examplePolicyDoc();
      fs.writeFileSync(file, JSON.stringify(doc, null, 2) + "\n");
      return console.log(`wrote ${path.resolve(file)}${opts.preset ? ` (preset: ${opts.preset})` : ""}`);
    }
    if (opts.check !== undefined) {
      const [cap, target] = [opts.check === true ? opts._[0] : opts.check,
        opts.check === true ? opts._[1] : opts._[0]];
      const p = loadPolicy(opts);
      const fn = { terminal: "checkTerminal", fsRead: "checkFsRead",
        fsWrite: "checkFsWrite", network: "checkNetwork" }[cap];
      if (!fn || target === undefined) {
        throw new Error(`usage: policy --check <${CAPS.join("|")}> <target>`);
      }
      return console.log(JSON.stringify({ capability: cap, target, decision: p[fn](target) }));
    }
    // default: --show
    const p = loadPolicy(opts);
    console.log(JSON.stringify(p.toJSON(), null, 2));
  },
};

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  const cmd = opts._.shift();
  if (opts.help || !cmd) usage(cmd ? 0 : 0);
  const fn = CMDS[cmd];
  if (!fn) {
    console.error(`unknown command: ${cmd}`);
    usage(1);
  }
  await fn(opts);
}

main().catch((e) => {
  // Never include credential material in error output.
  console.error(`FATAL: ${e.message}`);
  process.exit(1);
});
