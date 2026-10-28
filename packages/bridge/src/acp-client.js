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
 *   BRIDGE_AGENT            agent strategy name (default "devin";
 *                           see AGENT_STRATEGIES)
 *   BRIDGE_AGENT_BIN        explicit agent executable — the generic
 *                           escape hatch for any ACP CLI; checked
 *                           before the strategy's candidates
 *   BRIDGE_AGENT_ARGS       JSON array argv override (default: the
 *                           strategy's own argv, e.g. ["acp"])
 *   BRIDGE_AGENT_AUTH       authenticate methodId override — "none"
 *                           skips authenticate (default: the
 *                           strategy's, "devin-browser" for devin)
 *   BRIDGE_CREDENTIALS_PATH credentials.toml override (any strategy)
 *   BRIDGE_AGENT_TOKEN_KEY  credentials.toml key name holding the
 *                           session token (default: the selected
 *                           strategy's tokenKey — "windsurf_api_key"
 *                           for devin, the only registered strategy).
 *                           Lets a custom agent driven via
 *                           BRIDGE_AGENT_BIN read its own key name
 *                           without a registry entry; agents that need
 *                           no handshake can still use
 *                           BRIDGE_AGENT_AUTH=none.
 *   DEVIN_CLI_PATH          devin strategy: Devin CLI executable
 *                           (auto-detected from PATH)
 *   DEVIN_CREDENTIALS_PATH  legacy credentials override — kept for
 *                           back-compat; BRIDGE_CREDENTIALS_PATH wins
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

/** devin strategy: platform default for <data>/devin/credentials.toml. */
function devinCredentialsPath({ platform, env, home }) {
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

export function credentialsPath({
  platform = process.platform,
  env = process.env,
  home = os.homedir(),
} = {}) {
  // Generic override first; the Devin-specific env stays as a
  // back-compat fallback; otherwise the selected strategy decides.
  if (env.BRIDGE_CREDENTIALS_PATH) return path.resolve(env.BRIDGE_CREDENTIALS_PATH);
  if (env.DEVIN_CREDENTIALS_PATH) return path.resolve(env.DEVIN_CREDENTIALS_PATH);
  return agentStrategy({ env }).credentialsPath({ platform, env, home });
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
 * Agent strategies — everything the bridge needs to know about an
 * ACP-compatible CLI, in one named entry:
 *
 *   binCandidates({platform, env, home}) → candidate executables, in
 *     precedence order (each resolves through the safe-executable
 *     helper — see agentBin).
 *   credentialsPath({platform, env, home}) → default credentials.toml
 *     location for the strategy.
 *   agentArgs({env}) → argv that puts the binary in ACP mode.
 *   authMethod → ACP `authenticate` methodId sent during init.
 *   tokenKey → credentials.toml key holding the session token sent as
 *     `authenticate` `_meta.api_key`.
 *
 * BRIDGE_AGENT selects the entry (default "devin"). To support another
 * ACP CLI, add a strategy here — or skip the registry entirely and point
 * BRIDGE_AGENT_BIN/BRIDGE_AGENT_ARGS/BRIDGE_CREDENTIALS_PATH at it.
 */
export const AGENT_STRATEGIES = {
  devin: {
    binCandidates: devinBinCandidates,
    credentialsPath: devinCredentialsPath,
    agentArgs: () => ["acp"],
    authMethod: "devin-browser",
    tokenKey: "windsurf_api_key",
  },
};

/** Resolve the configured agent strategy; unknown names fail fast. */
export function agentStrategy({ env = process.env } = {}) {
  const name = env.BRIDGE_AGENT || "devin";
  const strategy = AGENT_STRATEGIES[name];
  if (!strategy) {
    throw new Error(
      `unknown BRIDGE_AGENT "${name}" — known strategies: ${Object.keys(AGENT_STRATEGIES).join(", ")}`,
    );
  }
  return { name, ...strategy };
}

/**
 * Candidate executables for the configured agent, in order:
 * BRIDGE_AGENT_BIN (explicit escape hatch) → the selected strategy's
 * candidates (BRIDGE_AGENT, default "devin").
 */
export function agentBinCandidates({
  platform = process.platform,
  env = process.env,
  home = os.homedir(),
} = {}) {
  const candidates = [];
  if (env.BRIDGE_AGENT_BIN) candidates.push(env.BRIDGE_AGENT_BIN);
  candidates.push(...agentStrategy({ env }).binCandidates({ platform, env, home }));
  return candidates;
}

/**
 * Autodetect an agent binary. Candidate precedence is
 * unchanged (env override → per-user install dir → PATH basename), but
 * each candidate resolves through the shared safe-executable helper: a
 * manual PATH walk that never touches cwd and always returns an absolute
 * spawn target {file, argv}. The workspace is untrusted input by
 * definition — autodetect (trusted:false) refuses anything inside
 * `opts.workspaceDir` (default: process.cwd()); an explicit --bin is a
 * user gesture and may point there on purpose. `opts.env.PATH` overrides
 * the walked PATH (a caller carrying its own environment gets its own
 * resolution). Returns the spec or null.
 */
function resolveBin(candidates, opts = {}) {
  const workspaceDir = "workspaceDir" in opts ? opts.workspaceDir : process.cwd();
  const trusted = opts.trusted === true;
  for (const candidate of candidates) {
    const spec = resolveSafeExecutable(candidate, {
      workspaceDir, trusted, npmPackage: null, env: opts.env,
    });
    if (spec) return spec;
  }
  return null;
}

/** Devin CLI candidates only (the `devin` strategy); prefer agentBin. */
export function devinBin(opts = {}) {
  return resolveBin(devinBinCandidates(opts), opts);
}

/** The configured agent: BRIDGE_AGENT_BIN → BRIDGE_AGENT strategy → devin. */
export function agentBin(opts = {}) {
  return resolveBin(agentBinCandidates(opts), opts);
}

/**
 * argv that puts the agent binary in ACP mode: BRIDGE_AGENT_ARGS (a JSON
 * array of strings) overrides; otherwise the selected strategy decides
 * (["acp"] for devin).
 */
export function agentArgs({ env = process.env } = {}) {
  const raw = env.BRIDGE_AGENT_ARGS;
  if (raw == null) return agentStrategy({ env }).agentArgs({ env });
  // A set-but-empty value used to read as "unset" and silently fell back
  // to the strategy argv — a config that wanted zero args got ["acp"]
  // instead. Empty/whitespace is now an explicit, actionable error;
  // '[]' is the only way to say zero args.
  if (!String(raw).trim()) {
    throw new Error(
      "BRIDGE_AGENT_ARGS is set but empty — unset it to use the "
      + 'strategy default (["acp"] for devin), or set \'[]\' for zero args',
    );
  }
  let parsed;
  try {
    parsed = JSON.parse(raw);
  } catch {
    parsed = null;
  }
  if (!Array.isArray(parsed) || parsed.some((a) => typeof a !== "string")) {
    throw new Error('BRIDGE_AGENT_ARGS must be a JSON array of strings (e.g. \'["acp"]\')');
  }
  return parsed;
}

/**
 * The `authenticate` methodId to send during init: BRIDGE_AGENT_AUTH
 * overrides (the literal value "none" disables authenticate entirely —
 * for agents that need no auth handshake); otherwise the selected
 * strategy decides ("devin-browser" for devin). Returns null to skip.
 */
export function agentAuthMethod({ env = process.env } = {}) {
  const raw = env.BRIDGE_AGENT_AUTH;
  if (raw === undefined || raw === "") {
    return agentStrategy({ env }).authMethod ?? null;
  }
  return /^none$/i.test(raw) ? null : raw;
}

/**
 * Read the session token from credentials.toml. The key name is
 * BRIDGE_AGENT_TOKEN_KEY when set, else the selected strategy's
 * `tokenKey` ("windsurf_api_key" for devin), else the literal
 * "windsurf_api_key" fallback. Without the env override, a custom agent
 * driven via BRIDGE_AGENT_BIN reads the devin strategy's key — so its
 * credentials.toml must contain `windsurf_api_key = "..."`, set the
 * override, skip auth with BRIDGE_AGENT_AUTH=none, or pass opts.token.
 */
export function readSessionToken() {
  const cred = credentialsPath();
  if (!fs.existsSync(cred)) {
    throw new Error(`credentials.toml not found at ${cred} — sign in to Devin Desktop first (or set BRIDGE_CREDENTIALS_PATH)`);
  }
  const key = process.env.BRIDGE_AGENT_TOKEN_KEY
    || agentStrategy().tokenKey || "windsurf_api_key";
  const m = new RegExp(`${key}\\s*=\\s*"([^"]+)"`).exec(fs.readFileSync(cred, "utf8"));
  if (!m) throw new Error(`${key} missing from credentials.toml`);
  return m[1];
}

/**
 * Minimal ACP client: newline-delimited JSON-RPC over stdio.
 * Usage: const acp = new DevinAcp(); acp.start(); await acp.init(); ... acp.stop();
 */
export class DevinAcp {
  /**
   * @param {object} opts
   * @param {string} opts.bin        executable (default: agentBin() — BRIDGE_AGENT_BIN,
   *                                 BRIDGE_AGENT strategy, else the devin autodetect)
   * @param {string[]} opts.args     argv (default: agentArgs() — BRIDGE_AGENT_ARGS,
   *                                 else the strategy's, ["acp"] for devin; tests
   *                                 pass a fixture script)
   * @param {string} opts.cwd        working dir → also the session cwd default
   * @param {Policy} opts.policy     permission policy (default: fail-closed)
   * @param {Function} opts.askHandler  async ({capability, reason, params}) => "allow"|"deny"
   *                                    invoked when policy returns "ask". No handler → deny.
   * @param {string} opts.token      session token override (default: credentials.toml)
   * @param {number} opts.timeoutMs  per-call timeout
   * @param {Function} opts.onNotification  (msg) => void, surfaces every agent notification
   */
  constructor({
    bin, args, cwd, policy, askHandler, token, timeoutMs, onNotification,
  } = {}) {
    this.cwd = cwd || process.cwd();
    const spec = bin
      ? resolveSafeExecutable(bin, { workspaceDir: this.cwd, trusted: true })
      : agentBin({ workspaceDir: this.cwd });
    if (!spec) {
      const strat = agentStrategy();
      throw new Error(
        `ACP agent executable not found (strategy "${strat.name}") — set BRIDGE_AGENT_BIN`
        + `${strat.name === "devin" ? " or DEVIN_CLI_PATH" : ""}, or pass opts.bin`,
      );
    }
    this.bin = spec.file;
    this.binArgv = spec.argv;
    this.args = args !== undefined ? args : agentArgs();
    this.policy = policy || Policy.default({ root: this.cwd });
    // The credentials file the bridge authenticates with is denied to
    // the agent no matter what the policy says — a custom
    // BRIDGE_CREDENTIALS_PATH can sit outside the built-in
    // credential-store basenames the policy already denies.
    this.policy.denyFsReadPath(credentialsPath());
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
      const err = new Error(`agent process exited (code ${code})`);
      for (const { reject } of this._pending.values()) reject(err);
      this._pending.clear();
    });
    // 'error' fires when the spawn itself fails (e.g. ENOENT on a bad
    // shebang, EACCES) — without a listener Node crashes the bridge with
    // "Unhandled 'error' event". Reject pending calls like 'exit' does;
    // 'exit' still fires afterwards and finds the map already cleared.
    this.child.on("error", (spawnErr) => {
      const err = new Error(`agent process failed to spawn: ${spawnErr.message}`);
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
    const res = await this.call("initialize", {
      protocolVersion: 1,
      clientCapabilities: { fs: { readTextFile: true, writeTextFile: true }, terminal: true },
    });
    const method = this._authMethodFor(res);
    if (method !== null) {
      const apiKey = this._token || readSessionToken();
      await this.call("authenticate", {
        methodId: method,
        _meta: { api_key: apiKey },
      });
    }
    return this;
  }

  /**
   * The auth method to send for an `initialize` response, or `null` to
   * skip `authenticate`. ACP negotiation: an agent that advertises
   * `authMethods` is only sent a method it supports — anything else is
   * skipped rather than an unsupported call. An agent that does not
   * advertise the field at all (the Devin CLI) gets the configured
   * method unconditionally; an empty advertised list means the agent
   * supports no auth methods.
   */
  _authMethodFor(initRes) {
    const method = agentAuthMethod();
    if (method === null) return null;
    const offered = initRes?.authMethods;
    if (offered == null) return method;
    return offered.some((m) => (m.id || m.methodId) === method)
      ? method
      : null;
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
