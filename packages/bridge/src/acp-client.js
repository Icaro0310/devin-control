/**
 * acp-client.js — cross-platform ACP client for the Devin CLI embedded in
 * Devin Desktop. Implements newline-delimited JSON-RPC over stdio.
 *
 * The CLI's `acp` mode authenticates via `authenticate` with
 * `_meta.api_key` = `windsurf_api_key` from the platform credentials.toml
 * (the session token the IDE keeps fresh) — it does NOT use `devin auth login` state.
 * That enables real Devin sessions without the interactive PKCE flow.
 *
 * SECURITY: the token is read for the handshake and is never logged,
 * stored in .sessions.json or echoed into errors.
 *
 * Unlike the original, every agent→client capability request
 * (terminal/*, fs/*, session/request_permission) is gated through a
 * Policy. With no policy file the client is fail-closed (everything
 * requires asking; headless asks deny). See src/policy.js.
 *
 * Env:
 *   DEVIN_CLI_PATH          Devin CLI executable (auto-detected from PATH)
 *   DEVIN_CREDENTIALS_PATH  credentials.toml override
 *   ACP_TIMEOUT_MS          per-call timeout (default 300000)
 */

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import crypto from "node:crypto";
import { spawn } from "node:child_process";
import { createRequire } from "node:module";
import { Policy } from "./policy.js";

// Shared safe-executable resolver — byte-identical copy of devin-powerups'
// adapters/shared/safe-executable.cjs (lockstep enforced by
// config-restricted-check). On Windows, CreateProcess searches cwd before
// PATH: the agent binary resolves to an absolute spawn target or not at
// all, and autodetect never accepts a workspace-resident candidate.
const require = createRequire(import.meta.url);
const { resolveSafeExecutable } = require("./safe-executable.cjs");

export function credentialsPath({
  platform = process.platform,
  env = process.env,
  home = os.homedir(),
} = {}) {
  if (env.DEVIN_CREDENTIALS_PATH) return path.resolve(env.DEVIN_CREDENTIALS_PATH);
  if (platform === "win32") {
    const appdata = env.APPDATA || path.join(home, "AppData", "Roaming");
    return path.join(appdata, "devin", "credentials.toml");
  }
  if (platform === "darwin") {
    return path.join(home, "Library", "Application Support", "devin", "credentials.toml");
  }
  const dataHome = env.XDG_DATA_HOME || path.join(home, ".local", "share");
  return path.join(dataHome, "devin", "credentials.toml");
}

export function devinBinCandidates({
  platform = process.platform,
  env = process.env,
  home = os.homedir(),
} = {}) {
  const candidates = [];
  if (env.DEVIN_CLI_PATH) candidates.push(env.DEVIN_CLI_PATH);
  if (platform === "win32") {
    const local = env.LOCALAPPDATA || path.join(home, "AppData", "Local");
    candidates.push(path.join(
      local,
      "Programs", "Devin", "resources", "app",
      "extensions", "windsurf", "devin", "bin", "devin.exe",
    ));
  }
  candidates.push(platform === "win32" ? "devin.exe" : "devin");
  return candidates;
}

/**
 * Autodetect the Devin CLI agent binary. Candidate precedence is
 * unchanged (DEVIN_CLI_PATH → per-user install dir → PATH basename), but
 * each candidate resolves through the shared safe-executable helper: a
 * manual PATH walk that never touches cwd and always returns an absolute
 * spawn target {file, argv}. The workspace is untrusted input by
 * definition — autodetect (trusted:false) refuses anything inside
 * `opts.workspaceDir` (default: process.cwd()); an explicit --bin is a
 * user gesture and may point there on purpose. Returns the spec or null.
 */
export function devinBin(opts = {}) {
  const workspaceDir = "workspaceDir" in opts ? opts.workspaceDir : process.cwd();
  const trusted = opts.trusted === true;
  for (const candidate of devinBinCandidates(opts)) {
    const spec = resolveSafeExecutable(candidate, {
      workspaceDir, trusted, npmPackage: null,
    });
    if (spec) return spec;
  }
  return null;
}

/** Read windsurf_api_key from %APPDATA%\devin\credentials.toml (IDE session token). */
export function readSessionToken() {
  const cred = credentialsPath();
  if (!fs.existsSync(cred)) {
    throw new Error(`credentials.toml not found at ${cred} — sign in to Devin Desktop first`);
  }
  const m = /windsurf_api_key\s*=\s*"([^"]+)"/.exec(fs.readFileSync(cred, "utf8"));
  if (!m) throw new Error("windsurf_api_key missing from credentials.toml");
  return m[1];
}

/**
 * Minimal ACP client: newline-delimited JSON-RPC over stdio.
 * Usage: const acp = new DevinAcp(); acp.start(); await acp.init(); ... acp.stop();
 */
export class DevinAcp {
  /**
   * @param {object} opts
   * @param {string} opts.bin        executable (default: auto-detected devin.exe)
   * @param {string[]} opts.args     argv (default ["acp"]; tests pass a fixture script)
   * @param {string} opts.cwd        working dir → also the session cwd default
   * @param {Policy} opts.policy     permission policy (default: fail-closed)
   * @param {Function} opts.askHandler  async ({capability, reason, params}) => "allow"|"deny"
   *                                    invoked when policy returns "ask". No handler → deny.
   * @param {string} opts.token      session token override (default: credentials.toml)
   * @param {number} opts.timeoutMs  per-call timeout
   * @param {Function} opts.onNotification  (msg) => void, surfaces every agent notification
   */
  constructor({
    bin, args = ["acp"], cwd, policy, askHandler, token, timeoutMs, onNotification,
  } = {}) {
    this.cwd = cwd || process.cwd();
    const spec = bin
      ? resolveSafeExecutable(bin, { workspaceDir: this.cwd, trusted: true })
      : devinBin({ workspaceDir: this.cwd });
    if (!spec) throw new Error("Devin CLI not found (set DEVIN_CLI_PATH)");
    this.bin = spec.file;
    this.binArgv = spec.argv;
    this.args = args;
    this.policy = policy || Policy.default({ root: this.cwd });
    this.askHandler = askHandler || null;
    this._token = token || null;
    this.timeoutMs = Number(timeoutMs || process.env.ACP_TIMEOUT_MS || 300000);
    this.onNotification = onNotification || null;
    this._id = 0;
    this._pending = new Map();
    this._buf = "";
    this.notifications = [];
    // agent-created terminals: id -> {child, chunks, truncated, exitCode, signal, waiters}
    this._terminals = new Map();
  }

  start() {
    this.child = spawn(this.bin, this.binArgv.concat(this.args), {
      cwd: this.cwd,
      stdio: ["pipe", "pipe", "ignore"],
    });
    this.child.stdout.setEncoding("utf8");
    this.child.stdout.on("data", (chunk) => this._onData(chunk));
    this.child.on("exit", (code) => {
      const err = new Error(`devin acp exited (code ${code})`);
      for (const { reject } of this._pending.values()) reject(err);
      this._pending.clear();
    });
    return this;
  }

  _onData(chunk) {
    this._buf += chunk;
    let idx;
    while ((idx = this._buf.indexOf("\n")) >= 0) {
      const line = this._buf.slice(0, idx).trim();
      this._buf = this._buf.slice(idx + 1);
      if (!line.startsWith("{")) continue;
      let msg;
      try { msg = JSON.parse(line); } catch { continue; }
      if (msg.id !== undefined && this._pending.has(msg.id)) {
        const { resolve, reject } = this._pending.get(msg.id);
        this._pending.delete(msg.id);
        msg.error ? reject(new AcpError(msg.error)) : resolve(msg.result);
      } else if (msg.method && msg.id !== undefined) {
        // agent→client request (terminal/*, fs/*, request_permission):
        // must answer or the agent blocks forever.
        this._handleAgentRequest(msg);
      } else {
        this.notifications.push(msg);
        this.onNotification?.(msg);
      }
    }
  }

  _respond(id, result, error) {
    const out = { jsonrpc: "2.0", id };
    if (error) out.error = { code: -32603, message: String(error) };
    else out.result = result ?? null;
    try { this.child.stdin.write(JSON.stringify(out) + "\n"); } catch { /* dead */ }
  }

  /**
   * Gate a capability through the policy. Returns true to proceed, false
   * to refuse. "ask" defers to askHandler; no handler → deny (fail closed).
   */
  async _gate(decision, req) {
    if (decision === "allow") return true;
    if (decision === "deny") return false;
    if (!this.askHandler) return false;
    try {
      return (await this.askHandler(req)) === "allow";
    } catch {
      return false;
    }
  }

  async _newTerminal(params) {
    const commandLine = [params.command, ...(params.args || [])].join(" ");
    const decision = this.policy.checkTerminal(commandLine);
    const ok = await this._gate(decision, {
      capability: "terminal", command: commandLine, params,
    });
    if (!ok) throw new Error(`policy denied terminal command (${decision})`);
    const id = crypto.randomUUID();
    const rec = {
      chunks: [], truncated: false, exitCode: null, signal: null,
      waiters: [], byteLimit: params.outputByteLimit || 1024 * 1024,
    };
    const child = spawn(params.command, params.args || [], {
      cwd: params.cwd || this.cwd,
      env: params.env ? { ...process.env, ...Object.fromEntries(
        (params.env || []).map((e) => [e.name, e.value])) } : process.env,
      shell: true, windowsHide: true,
    });
    rec.child = child;
    let size = 0;
    const collect = (d) => {
      const s = d.toString();
      size += s.length;
      if (size <= rec.byteLimit) rec.chunks.push(s);
      else rec.truncated = true;
    };
    child.stdout?.on("data", collect);
    child.stderr?.on("data", collect);
    child.on("exit", (code, signal) => {
      rec.exitCode = code; rec.signal = signal;
      for (const w of rec.waiters.splice(0)) w();
    });
    this._terminals.set(id, rec);
    return id;
  }

  async _fsRead(p) {
    const decision = this.policy.checkFsRead(p.path);
    const ok = await this._gate(decision, { capability: "fsRead", path: p.path, params: p });
    if (!ok) throw new Error(`policy denied fs read (${decision})`);
    let content = fs.readFileSync(p.path, "utf8");
    if (p.line || p.limit) {
      const lines = content.split("\n");
      content = lines.slice((p.line || 1) - 1,
        p.limit ? (p.line || 1) - 1 + p.limit : undefined).join("\n");
    }
    return { content };
  }

  async _fsWrite(p) {
    const decision = this.policy.checkFsWrite(p.path);
    const ok = await this._gate(decision, { capability: "fsWrite", path: p.path, params: p });
    if (!ok) throw new Error(`policy denied fs write (${decision})`);
    fs.mkdirSync(path.dirname(p.path), { recursive: true });
    fs.writeFileSync(p.path, p.content ?? "", "utf8");
    return {};
  }

  static _pick(options, prefix) {
    const opts = options || [];
    return opts.find((o) => o.kind === `${prefix}_always`)
      || opts.find((o) => String(o.kind || "").startsWith(prefix))
      || null;
  }

  async _handleAgentRequest(msg) {
    const p = msg.params || {};
    try {
      switch (msg.method) {
        case "terminal/create":
          return this._respond(msg.id, { terminalId: await this._newTerminal(p) });
        case "terminal/output": {
          const rec = this._terminals.get(p.terminalId);
          if (!rec) return this._respond(msg.id, null, "unknown terminal");
          return this._respond(msg.id, {
            output: rec.chunks.join(""),
            truncated: rec.truncated,
            exitStatus: rec.exitCode === null && rec.signal === null
              ? null : { exitCode: rec.exitCode, signal: rec.signal },
          });
        }
        case "terminal/wait_for_exit": {
          const rec = this._terminals.get(p.terminalId);
          if (!rec) return this._respond(msg.id, null, "unknown terminal");
          if (rec.exitCode === null && rec.signal === null) {
            await new Promise((r) => rec.waiters.push(r));
          }
          return this._respond(msg.id, { exitCode: rec.exitCode, signal: rec.signal });
        }
        case "terminal/kill": {
          this._terminals.get(p.terminalId)?.child?.kill();
          return this._respond(msg.id, {});
        }
        case "terminal/release": {
          const rec = this._terminals.get(p.terminalId);
          if (rec && rec.exitCode === null) rec.child?.kill();
          this._terminals.delete(p.terminalId);
          return this._respond(msg.id, {});
        }
        case "fs/read_text_file":
          return this._respond(msg.id, await this._fsRead(p));
        case "fs/write_text_file":
          return this._respond(msg.id, await this._fsWrite(p));
        case "session/request_permission": {
          const { decision, reason } = this.policy.checkPermissionRequest(p);
          const allow = await this._gate(decision, {
            capability: "permission", reason, params: p,
          });
          const pick = allow
            ? DevinAcp._pick(p.options, "allow")
            : DevinAcp._pick(p.options, "reject");
          return this._respond(msg.id, pick
            ? { outcome: { outcome: "selected", optionId: pick.optionId } }
            : { outcome: { outcome: "cancelled" } });
        }
        default:
          return this._respond(msg.id, null, `method not implemented: ${msg.method}`);
      }
    } catch (e) {
      return this._respond(msg.id, null, e.message);
    }
  }

  call(method, params = {}, timeoutMs = this.timeoutMs) {
    const id = ++this._id;
    this.child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n");
    return new Promise((resolve, reject) => {
      const t = setTimeout(() => {
        this._pending.delete(id);
        reject(new Error(`ACP ${method}: timeout ${timeoutMs}ms`));
      }, timeoutMs);
      this._pending.set(id, {
        resolve: (v) => { clearTimeout(t); resolve(v); },
        reject: (e) => { clearTimeout(t); reject(e); },
      });
    });
  }

  async init() {
    await this.call("initialize", {
      protocolVersion: 1,
      clientCapabilities: { fs: { readTextFile: true, writeTextFile: true }, terminal: true },
    });
    const apiKey = this._token || readSessionToken();
    await this.call("authenticate", {
      methodId: "devin-browser",
      _meta: { api_key: apiKey },
    });
    return this;
  }

  /**
   * Create a session. `label` is an optional {label, origin, purpose}
   * object (see src/labels.js) sent to the agent under `_meta` — the
   * spec-sanctioned ACP extension point — so downstream tooling can tag
   * bridge-created sessions. Whether the agent persists `_meta` is
   * undocumented; the deterministic record is the local sidecar
   * (LabelStore) written by the dispatcher.
   */
  async newSession(cwd = this.cwd, { label } = {}) {
    const params = { cwd, mcpServers: [] };
    if (label && (label.origin || label.purpose)) {
      params._meta = {
        "devin-bridge": {
          origin: label.origin,
          purpose: label.purpose,
          label: label.label || `${label.origin}:${label.purpose}`,
        },
      };
    }
    const res = await this.call("session/new", params);
    this.sessionId = res.sessionId;
    this.configOptions = res.configOptions || [];
    return res;
  }

  /**
   * Resume an existing session (ACP session/load). Throws AcpError with
   * kind "session_locked" when open in another process.
   */
  async loadSession(sessionId, cwd = this.cwd) {
    const res = await this.call("session/load", { sessionId, cwd, mcpServers: [] });
    this.sessionId = res.sessionId || sessionId;
    this.configOptions = res.configOptions || this.configOptions;
    return res;
  }

  modelOption() {
    return this.configOptions?.find((o) => o.id === "model");
  }

  /** Available models: [{value, name, description, meta}] */
  listModels() {
    return (this.modelOption()?.options || []).map((o) => ({
      value: o.value,
      name: o.name,
      description: o.description || "",
      meta: o._meta || {},
    }));
  }

  currentModel() {
    return this.modelOption()?.currentValue || null;
  }

  async setModel(value) {
    const res = await this.call("session/set_config_option", {
      sessionId: this.sessionId, configId: "model", value,
    });
    this.configOptions = res.configOptions || this.configOptions;
    return this.currentModel();
  }

  /**
   * Cancel the active turn (ACP notification — no reply). Best-effort:
   * used after a client-side timeout so the agent stops burning quota
   * server-side with nobody listening.
   */
  cancel() {
    if (!this.sessionId || !this.child) return;
    try {
      this.child.stdin.write(JSON.stringify({
        jsonrpc: "2.0",
        method: "session/cancel",
        params: { sessionId: this.sessionId },
      }) + "\n");
    } catch { /* dead process */ }
  }

  /**
   * Run a prompt and return { text, modelLabel, usage, stopReason, cost, timedOut }.
   * modelLabel comes from the _cognition.ai/agent_stopped notification —
   * real proof of the model that answered. cost is the largest numeric
   * value in cost-shaped notification fields (creditCost, acuCost,
   * committed_*_cost, acuUsed…); 0 = no quota consumed.
   * On client-side timeout it does NOT throw: sends session/cancel and
   * returns timedOut:true with whatever partial text streamed in — the
   * caller decides whether to continue, keep the partial or fail.
   */
  async prompt(text, { timeoutMs } = {}) {
    const base = this.notifications.length;
    let res = null;
    let timedOut = false;
    try {
      res = await this.call(
        "session/prompt",
        { sessionId: this.sessionId, prompt: [{ type: "text", text }] },
        timeoutMs ?? this.timeoutMs,
      );
    } catch (e) {
      if (!/timeout/i.test(e.message)) throw e;
      timedOut = true;
      this.cancel();
    }
    const notifs = this.notifications.slice(base);
    let reply = "";
    let modelLabel = null;
    for (const n of notifs) {
      const upd = n.params?.update;
      if (n.method === "session/update" && upd?.sessionUpdate === "agent_message_chunk") {
        if (upd.content?.type === "text") reply += upd.content.text;
      }
      if (n.method === "_cognition.ai/agent_stopped") {
        modelLabel = n.params?.stats?.modelLabel || modelLabel;
      }
    }
    return {
      text: reply.trim(), modelLabel, usage: res?.usage, stopReason: res?.stopReason,
      cost: maxCostField(notifs), timedOut,
    };
  }

  stop() {
    for (const rec of this._terminals.values()) {
      if (rec.exitCode === null && rec.signal === null) {
        try { rec.child?.kill(); } catch { /* already dead */ }
      }
    }
    this._terminals.clear();
    try { this.child?.kill(); } catch { /* already dead */ }
  }
}

export class AcpError extends Error {
  constructor(error) {
    super(error.message || "ACP error");
    this.code = error.code;
    this.kind = error.data?.["cognition.ai/errorKind"];
    this.quotaExhausted = this.kind === "resource_exhausted" || /quota/i.test(error.message || "");
  }
}

const COST_KEY = /(credit_?cost|acu_?cost|acuUsed|quota_cost|overage_cost|committed_(credit|acu|quota|overage)_cost)/i;

/** Recursively scan notifications for cost fields > 0. */
export function maxCostField(notifs) {
  let max = 0;
  const walk = (v) => {
    if (v && typeof v === "object") {
      for (const [k, val] of Object.entries(v)) {
        if (COST_KEY.test(k) && typeof val === "number" && val > max) max = val;
        else if (val && typeof val === "object") walk(val);
      }
    }
  };
  for (const n of notifs) walk(n);
  return max;
}
