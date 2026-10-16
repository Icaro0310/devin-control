/**
 * policy.js — permission policy engine for devin-bridge.
 *
 * A `policy.json` document describes, per capability, which agent
 * operations are `allow`ed, `deny`ed or must be `ask`ed. Evaluation is
 * always fail-closed: anything not explicitly allowed falls back to the
 * capability default, and the shipped default is "ask" (never
 * auto-approve). Deny rules always win over allow rules.
 *
 * Document shape (version 1):
 * {
 *   "version": 1,
 *   "defaults": { "terminal": "ask", "fsRead": "ask", "fsWrite": "ask",
 *                 "network": "ask", "permission": "ask" },
 *   "terminal": { "allow": ["git status*"], "deny": ["rm -rf*"] },
 *   "fs": {
 *     "read":  { "allow": ["**"], "deny": ["**\/.env"] },
 *     "write": { "allow": ["src/**"], "deny": ["**\/.git/**"] }
 *   },
 *   "network": { "allow": ["api.github.com"], "deny": ["*.onion"] }
 * }
 *
 * Glob semantics:
 *   fs patterns      `*` = within a path segment, `**` = any depth
 *                    (`**\/` also matches zero directories), `?` = one char.
 *   terminal/host    `*` matches any run of characters (the command line
 *                    has no meaningful "segments"), `?` = one char.
 */

import fs from "node:fs";
import path from "node:path";

export const DECISIONS = Object.freeze(["allow", "deny", "ask"]);
const CAPABILITIES = ["terminal", "fsRead", "fsWrite", "network", "permission"];

/**
 * Credential-store basenames that are denied for fs reads no matter what
 * the user policy says. The bridge reads credentials.toml to authenticate;
 * agent sessions must never be handed those bytes back.
 */
export const BUILTIN_FS_READ_DENY = Object.freeze([
  "**/credentials.toml",
  "**/windsurf_api_key",
]);

/**
 * Convert a glob to a RegExp.
 * @param {string} pattern
 * @param {{command?: boolean, caseSensitive?: boolean}} opts
 *   command=true switches `*`/`**` to "match anything" mode (command lines
 *   and host globs); default is path mode where `*` cannot cross `/`.
 */
export function globToRegExp(pattern, { command = false, caseSensitive = true } = {}) {
  const pat = String(pattern).replace(/\\/g, "/");
  let re = "^";
  for (let i = 0; i < pat.length; i++) {
    const c = pat[i];
    if (c === "*") {
      if (command) {
        while (pat[i + 1] === "*") i++;
        re += ".*";
      } else if (pat[i + 1] === "*") {
        // [^/]* (not [^/]+) so "**/" also matches POSIX absolute paths
        // whose leading "/" is an empty first segment, e.g. /tmp/x.
        if (pat[i + 2] === "/") { re += "(?:[^/]*/)*"; i += 2; }
        else { re += ".*"; i += 1; }
      } else {
        re += "[^/]*";
      }
    } else if (c === "?") {
      re += command ? "." : "[^/]";
    } else if ("\\^$.|+()[]{}".includes(c)) {
      re += "\\" + c;
    } else {
      re += c;
    }
  }
  return new RegExp(re + "$", caseSensitive ? "" : "i");
}

/**
 * Normalise a filesystem path for matching: resolved against `root` when
 * relative, backslashes → "/", no trailing slash. Drive-letter and UNC
 * forms count as absolute on every platform.
 */
export function normalizeFsPath(p, root = process.cwd()) {
  const s = String(p);
  const abs = path.isAbsolute(s) || /^[A-Za-z]:[\\/]/.test(s)
    ? path.normalize(s)
    : path.resolve(root, s);
  return abs.replace(/\\/g, "/").replace(/\/+$/, "");
}

/** Candidate strings an fs path is tested against: the absolute form plus
 *  the root-relative form when the path lives under `root`. */
function fsCandidates(p, root) {
  const abs = normalizeFsPath(p, root);
  const rel = path.relative(root, abs.replace(/\//g, path.sep)).replace(/\\/g, "/");
  const out = [abs];
  if (rel && !rel.startsWith("..") && !path.isAbsolute(rel)) out.push(rel);
  return out;
}

function compileList(list, opts) {
  return (list || []).map((g) => globToRegExp(g, opts));
}

function validateLists(name, node) {
  if (node == null) return { allow: [], deny: [] };
  if (typeof node !== "object" || Array.isArray(node)) {
    throw new Error(`policy.${name} must be an object {allow, deny}`);
  }
  for (const key of ["allow", "deny"]) {
    const v = node[key];
    if (v != null && (!Array.isArray(v) || v.some((x) => typeof x !== "string"))) {
      throw new Error(`policy.${name}.${key} must be an array of glob strings`);
    }
  }
  return { allow: node.allow || [], deny: node.deny || [] };
}

export class Policy {
  /**
   * @param {object} doc  parsed policy.json content
   * @param {{root?: string, platform?: NodeJS.Platform}} opts
   *   root: base dir for resolving relative fs patterns/targets.
   *   platform: 'win32' makes fs matching case-insensitive.
   */
  constructor(doc = {}, { root = process.cwd(), platform = process.platform } = {}) {
    if (doc.version !== undefined && doc.version !== 1) {
      throw new Error(`unsupported policy version: ${doc.version} (expected 1)`);
    }
    this.root = path.resolve(root).replace(/\\/g, "/");
    const caseSensitive = platform !== "win32";
    this._fsCaseSensitive = caseSensitive;

    const defaults = { ...(doc.defaults || {}) };
    for (const cap of CAPABILITIES) {
      const v = defaults[cap] ?? "ask";
      if (!DECISIONS.includes(v)) {
        throw new Error(`policy.defaults.${cap} must be one of ${DECISIONS.join("|")} (got ${v})`);
      }
      defaults[cap] = v;
    }
    this.defaults = defaults;

    const terminal = validateLists("terminal", doc.terminal);
    const fsRead = validateLists("fs.read", doc.fs?.read);
    const fsWrite = validateLists("fs.write", doc.fs?.write);
    const network = validateLists("network", doc.network);

    this._rules = {
      terminal: {
        allow: compileList(terminal.allow, { command: true }),
        deny: compileList(terminal.deny, { command: true }),
      },
      fsRead: {
        allow: compileList(fsRead.allow, { caseSensitive }),
        deny: compileList(
          [...fsRead.deny, ...BUILTIN_FS_READ_DENY],
          { caseSensitive },
        ),
      },
      fsWrite: {
        allow: compileList(fsWrite.allow, { caseSensitive }),
        deny: compileList(fsWrite.deny, { caseSensitive }),
      },
      network: {
        allow: compileList(network.allow, { command: true, caseSensitive: false }),
        deny: compileList(network.deny, { command: true, caseSensitive: false }),
      },
    };
    this._doc = doc;
  }

  static load(file, opts) {
    let doc;
    try {
      doc = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch (e) {
      throw new Error(`cannot load policy ${file}: ${e.message}`);
    }
    return new Policy(doc, opts);
  }

  /** The shipped, fail-closed policy: everything requires asking. */
  static default(opts) {
    return new Policy({ version: 1 }, opts);
  }

  toJSON() {
    return {
      version: 1,
      defaults: { ...this.defaults },
      ...(this._doc.terminal ? { terminal: this._doc.terminal } : {}),
      ...(this._doc.fs ? { fs: this._doc.fs } : {}),
      ...(this._doc.network ? { network: this._doc.network } : {}),
    };
  }

  _decide(rules, values, fallback) {
    const list = Array.isArray(values) ? values : [values];
    for (const v of list) {
      if (rules.deny.some((re) => re.test(v))) return "deny";
    }
    for (const v of list) {
      if (rules.allow.some((re) => re.test(v))) return "allow";
    }
    return fallback;
  }

  /** `commandLine` = the full command string (command + args). */
  checkTerminal(commandLine) {
    const cmd = String(commandLine).replace(/\s+/g, " ").trim();
    return this._decide(this._rules.terminal, cmd, this.defaults.terminal);
  }

  checkFsRead(p) {
    return this._decide(this._rules.fsRead, fsCandidates(p, this.root), this.defaults.fsRead);
  }

  /**
   * Deny reads of one exact path no matter what the rules say — used for
   * the resolved credentials file, which a custom BRIDGE_CREDENTIALS_PATH
   * can place outside the built-in credential-store basenames.
   */
  denyFsReadPath(p) {
    const abs = normalizeFsPath(p, this.root);
    const re = new RegExp(
      `^${abs.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}$`,
      this._fsCaseSensitive ? "" : "i",
    );
    this._rules.fsRead.deny.push(re);
    return this;
  }

  checkFsWrite(p) {
    return this._decide(this._rules.fsWrite, fsCandidates(p, this.root), this.defaults.fsWrite);
  }

  checkNetwork(host) {
    return this._decide(this._rules.network, String(host).toLowerCase(), this.defaults.network);
  }

  /**
   * Classify an ACP `session/request_permission` params object and return
   * { decision, reason }. Executes→terminal rules, read/search→fsRead,
   * edit/write/delete/move→fsWrite, fetch→network; anything else falls
   * back to defaults.permission.
   */
  checkPermissionRequest(params = {}) {
    const tc = params.toolCall || {};
    const kind = String(tc.kind || "").toLowerCase();
    const firstLoc = tc.locations?.[0]?.path;

    if (kind === "execute") {
      const cmd = tc.rawInput?.command || tc.rawInput?.input || tc.title || "";
      const decision = this.checkTerminal(cmd);
      return { decision, reason: `terminal: ${cmd}` };
    }
    if (kind === "read" || kind === "search") {
      if (firstLoc) {
        const decision = this.checkFsRead(firstLoc);
        return { decision, reason: `fsRead: ${firstLoc}` };
      }
    }
    if (kind === "edit" || kind === "write" || kind === "delete" || kind === "move") {
      if (firstLoc) {
        const decision = this.checkFsWrite(firstLoc);
        return { decision, reason: `fsWrite: ${firstLoc}` };
      }
    }
    if (kind === "fetch") {
      const url = tc.rawInput?.url || tc.title || "";
      let host = "";
      try { host = new URL(url).hostname; } catch { /* not a URL */ }
      if (host) {
        const decision = this.checkNetwork(host);
        return { decision, reason: `network: ${host}` };
      }
    }
    return { decision: this.defaults.permission, reason: `kind: ${kind || "unknown"}` };
  }
}

/** Shipped fail-closed policy instance (root = cwd). */
export function defaultPolicy(opts) {
  return Policy.default(opts);
}

/**
 * Named presets (BR-2). `ask` is the shipped fail-closed default;
 * `read-only` is for intake/automation where the agent must observe but
 * never act; `full` exists for trusted scratch environments only — it is
 * labelled high-risk and prints a warning whenever it is selected.
 */
export const POLICY_PRESETS = Object.freeze(["ask", "read-only", "full"]);

export function presetPolicyDoc(name) {
  if (name === "ask") return { version: 1 };
  if (name === "read-only") {
    return {
      version: 1,
      defaults: {
        terminal: "deny",
        fsRead: "allow",
        fsWrite: "deny",
        network: "deny",
        permission: "deny",
      },
      fs: {
        read: {
          allow: ["**"],
          deny: ["**/.env", "**/.env.*", "**/credentials*",
                 "**/*.pem", "**/*.key", "**/id_rsa*"],
        },
      },
    };
  }
  if (name === "full") {
    return {
      version: 1,
      defaults: {
        terminal: "allow",
        fsRead: "allow",
        fsWrite: "allow",
        network: "allow",
        permission: "allow",
      },
    };
  }
  throw new Error(
    `unknown preset: ${name} (expected ${POLICY_PRESETS.join("|")})`
  );
}

/** The example policy shipped as policy.example.json. */
export function examplePolicyDoc() {
  return {
    version: 1,
    defaults: {
      terminal: "ask",
      fsRead: "ask",
      fsWrite: "ask",
      network: "ask",
      permission: "ask",
    },
    terminal: {
      allow: ["git status", "git diff*", "git log*", "npm test*", "node --test*"],
      deny: ["rm -rf*", "rmdir /s*", "del /f /s*", "format *", "curl*|*sh*", "wget*|*sh*"],
    },
    fs: {
      read: {
        allow: ["**"],
        deny: ["**/.env", "**/.env.*", "**/credentials*", "**/*.pem", "**/*.key", "**/id_rsa*"],
      },
      write: {
        allow: [],
        deny: ["**/.env", "**/.env.*", "**/credentials*", "**/.git/**"],
      },
    },
    network: {
      allow: [],
      deny: ["*.onion", "169.254.169.254", "localhost", "127.*"],
    },
  };
}
