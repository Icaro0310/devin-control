import { describe, it, after } from "node:test";
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { DevinAcp, AcpError, maxCostField } from "../src/acp-client.js";
import { Policy } from "../src/policy.js";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const FIXTURE = path.join(HERE, "fixtures", "fake-acp.mjs");
const FIXTURES = path.join(HERE, "fixtures");
const BIN = path.join(HERE, "..", "bin", "devin-bridge.js");

const clients = [];
async function makeClient({
  policy, askHandler, timeoutMs = 15000, terminalTimeoutMs, killGraceMs,
} = {}) {
  const acp = new DevinAcp({
    bin: process.execPath,
    args: [FIXTURE],
    cwd: FIXTURES,
    timeoutMs,
    terminalTimeoutMs,
    killGraceMs,
    token: "test-token",
    policy: policy || Policy.default({ root: FIXTURES }),
    askHandler,
  });
  acp.start();
  await acp.init();
  clients.push(acp);
  return acp;
}
after(() => { for (const c of clients.splice(0)) c.stop(); });

describe("DevinAcp — handshake and session lifecycle", () => {
  it("init() completes initialize + authenticate over NDJSON", async () => {
    const acp = await makeClient();
    assert.ok(acp);
  });

  it("newSession returns id + config options; models are listable/settable", async () => {
    const acp = await makeClient();
    await acp.newSession();
    assert.equal(acp.sessionId, "fx-new-1");
    assert.deepEqual(acp.listModels().map((m) => m.value), ["swe-2", "free-1"]);
    assert.equal(acp.currentModel(), "swe-2");
    await acp.setModel("free-1");
    assert.equal(acp.currentModel(), "free-1");
  });

  it("loadSession resumes; AcpError exposes kind for session_locked", async () => {
    const acp = await makeClient();
    await acp.loadSession("fx-old");
    assert.equal(acp.sessionId, "fx-old");
    await assert.rejects(
      acp.loadSession("fx-locked"),
      (e) => e instanceof AcpError && e.kind === "session_locked",
    );
  });

  it("unknown methods reject with an AcpError", async () => {
    const acp = await makeClient();
    await assert.rejects(acp.call("bogus/method"), AcpError);
  });
});

describe("DevinAcp — prompt", () => {
  it("aggregates streamed text and surfaces model label, usage, cost", async () => {
    const acp = await makeClient();
    await acp.newSession();
    const res = await acp.prompt("hello there");
    assert.equal(res.text, "echo: hello there");
    assert.equal(res.modelLabel, "fake-model-1");
    assert.equal(res.stopReason, "end_turn");
    assert.deepEqual(res.usage, { tokens: 42 });
    assert.equal(res.cost, 0);
    assert.equal(res.timedOut, false);
  });

  it("cost reflects creditCost notifications", async () => {
    const acp = await makeClient();
    await acp.newSession();
    const res = await acp.prompt("COST 7");
    assert.equal(res.cost, 7);
  });

  it("client-side timeout cancels and returns partial state instead of throwing", async () => {
    const acp = await makeClient();
    await acp.newSession();
    const res = await acp.prompt("HANG", { timeoutMs: 250 });
    assert.equal(res.timedOut, true);
    assert.equal(res.text, "");
  });
});

describe("DevinAcp — subprocess lifecycle failures are controlled", () => {
  it("an agent that exits mid-call rejects the pending call", async () => {
    const acp = new DevinAcp({
      bin: process.execPath,
      args: ["-e", "setTimeout(() => process.exit(3), 50)"],
      cwd: FIXTURES,
      timeoutMs: 5000,
      token: "t",
    });
    acp.start();
    clients.push(acp);
    await assert.rejects(acp.call("initialize", {}), /exited \(code 3\)/);
  });

  it("a spawn-level error rejects pending calls instead of crashing the host", async (t) => {
    if (process.platform === "win32") {
      return t.skip("bad-shebang spawn failure is POSIX-specific");
    }
    // exec fails at spawn (ENOENT on the interpreter): 'error' fires, not
    // just 'exit'. Unhandled 'error' on a ChildProcess crashes the process.
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "acp-bad-spawn-"));
    const bad = path.join(dir, "bad-agent.sh");
    fs.writeFileSync(bad, "#!/nonexistent-interp-xyz\nexit 0\n", { mode: 0o755 });
    const acp = new DevinAcp({
      bin: bad, args: [], cwd: dir, timeoutMs: 5000, token: "t",
    });
    acp.start();
    clients.push(acp);
    await assert.rejects(
      acp.call("initialize", {}),
      /failed to spawn|ENOENT|exited/,
    );
  });

  it("an agent that never answers initialize rejects on startup timeout", async () => {
    const acp = new DevinAcp({
      bin: process.execPath,
      args: ["-e", "setTimeout(() => {}, 60000)"],
      cwd: FIXTURES,
      timeoutMs: 150,
      token: "t",
    });
    acp.start();
    clients.push(acp);
    await assert.rejects(acp.init(), /initialize: timeout 150ms/);
  });
});

describe("DevinAcp — policy-gated agent requests", () => {
  it("terminal allowed by policy runs and returns output", async () => {
    const acp = await makeClient({
      policy: new Policy({ version: 1, terminal: { allow: ["echo*"] } }, { root: FIXTURES }),
    });
    await acp.newSession();
    const res = await acp.prompt("RUN_TERM echo hello-term");
    assert.match(res.text, /TERM_OUT:hello-term/);
  });

  it("terminal denied by policy never spawns", async () => {
    const acp = await makeClient({
      policy: new Policy({ version: 1, terminal: { deny: ["echo*"] } }, { root: FIXTURES }),
    });
    await acp.newSession();
    const res = await acp.prompt("RUN_TERM echo nope");
    assert.match(res.text, /TERM_ERR:/);
  });

  it("terminal ask with no askHandler fails closed", async () => {
    const acp = await makeClient(); // defaultPolicy: ask
    await acp.newSession();
    const res = await acp.prompt("RUN_TERM echo nope");
    assert.match(res.text, /TERM_ERR:/);
  });

  it("terminal ask with an approving askHandler proceeds", async () => {
    const acp = await makeClient({ askHandler: async () => "allow" });
    await acp.newSession();
    const res = await acp.prompt("RUN_TERM echo via-ask");
    assert.match(res.text, /TERM_OUT:via-ask/);
  });

  it("fs read allowed path returns content; denied path errors", async () => {
    const acp = await makeClient({
      policy: new Policy(
        { version: 1, fs: { read: { allow: ["**"], deny: ["**/.env"] } } },
        { root: FIXTURES },
      ),
    });
    await acp.newSession();
    const ok = await acp.prompt(`READ_FILE ${path.join(FIXTURES, "sample.txt")}`);
    assert.match(ok.text, /FILE_CONTENT:sample-fixture-content/);
    const denied = await acp.prompt(`READ_FILE ${path.join(FIXTURES, ".env")}`);
    assert.match(denied.text, /FILE_ERR:/);
  });

  it("fs write honours policy and lands on disk", async () => {
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "db-w-"));
    const target = path.join(tmp, "out.txt");
    const acp = await makeClient({
      policy: new Policy({ version: 1, fs: { write: { allow: ["**"] } } }, { root: tmp }),
    });
    await acp.newSession();
    const res = await acp.prompt(`WRITE_FILE ${target}`);
    assert.match(res.text, /WRITE_OK/);
    assert.equal(fs.readFileSync(target, "utf8"), "fixture-write");
  });

  it("request_permission picks allow option when policy allows", async () => {
    const acp = await makeClient({
      policy: new Policy({ version: 1, terminal: { allow: ["deploy*"] } }, { root: FIXTURES }),
    });
    await acp.newSession();
    const res = await acp.prompt("NEED_PERM deploy prod");
    assert.match(res.text, /OUTCOME:o_allow_always/);
  });

  it("request_permission picks reject/cancel when policy denies", async () => {
    const acp = await makeClient({
      policy: new Policy({ version: 1, terminal: { deny: ["deploy*"] } }, { root: FIXTURES }),
    });
    await acp.newSession();
    const res = await acp.prompt("NEED_PERM deploy prod");
    assert.match(res.text, /OUTCOME:o_reject/);
  });

  it("request_permission ask → askHandler decides; no handler cancels", async () => {
    const acp = await makeClient({ askHandler: async () => "allow" });
    await acp.newSession();
    const res = await acp.prompt("NEED_PERM anything");
    assert.match(res.text, /OUTCOME:o_allow_always/);

    const strict = await makeClient(); // ask, no handler
    await strict.newSession();
    const res2 = await strict.prompt("NEED_PERM anything");
    assert.match(res2.text, /OUTCOME:(o_reject|cancelled)/);
  });
});

describe("DevinAcp — agent auth (BRIDGE_AGENT_AUTH)", () => {
  function withEnv(vars, fn) {
    const saved = {};
    for (const k of Object.keys(vars)) saved[k] = process.env[k];
    for (const [k, v] of Object.entries(vars)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
    return Promise.resolve().then(fn).finally(() => {
      for (const [k, v] of Object.entries(saved)) {
        if (v === undefined) delete process.env[k]; else process.env[k] = v;
      }
    });
  }

  it("devin default still sends devin-browser (agent advertises nothing)", async () => {
    const acp = await makeClient();
    await acp.newSession();
    const res = await acp.prompt("AUTH_STATE");
    assert.equal(res.text, "AUTH:devin-browser");
  });

  it("BRIDGE_AGENT_AUTH=none skips authenticate entirely", () =>
    withEnv({ BRIDGE_AGENT_AUTH: "none" }, async () => {
      const acp = await makeClient();
      await acp.newSession();
      const res = await acp.prompt("AUTH_STATE");
      assert.equal(res.text, "AUTH:none");
    }));

  it("an empty advertised authMethods list sends no authenticate", () =>
    withEnv({ FAKE_ACP_AUTH_METHODS: "[]" }, async () => {
      const acp = await makeClient();
      await acp.newSession();
      const res = await acp.prompt("AUTH_STATE");
      assert.equal(res.text, "AUTH:none"); // agent supports no auth methods
    }));

  it("a method the agent does not advertise is never sent", () =>
    withEnv({ FAKE_ACP_AUTH_METHODS: '[{"id":"acme-auth"}]' }, async () => {
      const acp = await makeClient();
      await acp.newSession();
      const res = await acp.prompt("AUTH_STATE");
      assert.equal(res.text, "AUTH:none"); // devin-browser not advertised → skipped
    }));

  it("BRIDGE_AGENT_AUTH picks a method the agent advertises", () =>
    withEnv({
      FAKE_ACP_AUTH_METHODS: '[{"id":"acme-auth"}]',
      BRIDGE_AGENT_AUTH: "acme-auth",
    }, async () => {
      const acp = await makeClient();
      await acp.newSession();
      const res = await acp.prompt("AUTH_STATE");
      assert.equal(res.text, "AUTH:acme-auth");
    }));
});

describe("DevinAcp — terminal lifetime and cleanup", () => {
  const allowAll = () => new Policy(
    { version: 1, terminal: { allow: ["*"] } }, { root: FIXTURES });
  const sleepCmd = () => (process.platform === "win32"
    ? "ping -n 30 127.0.0.1" : "sleep 30");
  const tick = (ms = 50) => new Promise((r) => setTimeout(r, ms));
  const pidAlive = (pid) => {
    try { process.kill(pid, 0); return true; }
    catch (e) { return e.code === "EPERM"; }
  };

  it("kills a terminal that outlives terminalTimeoutMs", async () => {
    const acp = await makeClient({
      policy: allowAll(), terminalTimeoutMs: 300, killGraceMs: 200,
    });
    await acp.newSession();
    const t0 = Date.now();
    const res = await acp.prompt(`RUN_TERM ${sleepCmd()}`);
    const elapsed = Date.now() - t0;
    assert.equal(res.timedOut, false);
    assert.ok(elapsed < 15000, `terminal was not bounded (${elapsed}ms)`);
    assert.match(res.text, /TERM_OUT:/);
    assert.match(res.text, /EXIT:\{/); // wait_for_exit returned a real status
    if (process.platform !== "win32") {
      assert.match(res.text, /"signal":"SIG(TERM|KILL)"/);
    }
  });

  it("policy terminal.timeoutMs bounds the terminal when no opt is given", async () => {
    const acp = await makeClient({
      policy: new Policy(
        { version: 1, terminal: { allow: ["*"], timeoutMs: 300 } },
        { root: FIXTURES },
      ),
      killGraceMs: 200,
    });
    await acp.newSession();
    const res = await acp.prompt(`RUN_TERM ${sleepCmd()}`);
    assert.equal(res.timedOut, false);
    assert.match(res.text, /EXIT:\{/);
  });

  it("escalates to SIGKILL when the terminal ignores SIGTERM", async (t) => {
    if (process.platform === "win32") {
      return t.skip("POSIX signal semantics");
    }
    const acp = await makeClient({
      policy: allowAll(), terminalTimeoutMs: 300, killGraceMs: 200,
    });
    const fixture = path.join(FIXTURES, "term-ignore.mjs");
    const id = await acp._newTerminal({ command: process.execPath, args: [fixture] });
    const rec = acp._terminals.get(id);
    // term-ignore.mjs survives SIGTERM by design — the process group can
    // only empty out via the KILL escalation.
    const groupAlive = () => {
      try { process.kill(-rec.child.pid, 0); return true; }
      catch (e) { return e.code === "EPERM"; }
    };
    const deadline = Date.now() + 8000;
    while (groupAlive() && Date.now() < deadline) await tick();
    assert.equal(groupAlive(), false, "SIGTERM-ignoring terminal was not KILLed");
  });

  it("stop() reaps live terminals and their whole process tree", async () => {
    const acp = await makeClient({
      policy: allowAll(), terminalTimeoutMs: 60000, killGraceMs: 200,
    });
    const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "acp-gc-"));
    const pidFile = path.join(tmp, "gpid.txt");
    const fixture = path.join(FIXTURES, "term-grandchild.mjs");
    const id = await acp._newTerminal({
      command: process.execPath, args: [fixture, pidFile],
    });
    const fileDeadline = Date.now() + 8000;
    while (!fs.existsSync(pidFile) && Date.now() < fileDeadline) await tick();
    assert.ok(fs.existsSync(pidFile), "grandchild pid file never appeared");
    const gpid = Number(fs.readFileSync(pidFile, "utf8"));
    assert.ok(pidAlive(gpid));
    const rec = acp._terminals.get(id);
    acp.stop();
    const deadline = Date.now() + 8000;
    while (acp.hasLiveChildren() && Date.now() < deadline) await tick();
    assert.equal(acp.hasLiveChildren(), false);
    assert.ok(rec.exitCode !== null || rec.signal !== null);
    // the grandchild must be dead too — this is the orphan regression
    const gdeadline = Date.now() + 5000;
    while (pidAlive(gpid) && Date.now() < gdeadline) await tick();
    assert.equal(pidAlive(gpid), false, `grandchild ${gpid} survived stop()`);
  });
});

describe("devin-bridge CLI — signal handling", () => {
  it("SIGTERM reaps the agent and exits 143 instead of orphaning it", async (t) => {
    if (process.platform === "win32") {
      return t.skip("POSIX signals only — Windows kills via TerminateProcess");
    }
    const cwd = fs.mkdtempSync(path.join(os.tmpdir(), "db-sig-"));
    const env = {
      ...process.env,
      BRIDGE_AGENT_BIN: process.execPath,
      BRIDGE_AGENT_ARGS: JSON.stringify([FIXTURE]),
      BRIDGE_AGENT_AUTH: "none",
    };
    const child = spawn(process.execPath, [
      BIN, "prompt", cwd, "HANG", "--timeout-ms", "120000",
    ], { cwd, env, stdio: ["ignore", "pipe", "pipe"] });
    child.stderr.resume(); // drain the live stream
    child.stdout.resume();
    // give init + session/new + session/prompt time to get in-flight
    await new Promise((r) => setTimeout(r, 2500));
    const t0 = Date.now();
    child.kill("SIGTERM");
    const { code } = await new Promise((resolve) => {
      child.on("exit", (c) => resolve({ code: c }));
    });
    const elapsed = Date.now() - t0;
    assert.equal(code, 143); // handler ran — default SIGTERM death has code null
    assert.ok(elapsed < 10000, `shutdown took ${elapsed}ms`);
  });
});

describe("maxCostField", () => {
  it("finds the largest numeric cost field anywhere in notifications", () => {
    const notifs = [
      { params: { stats: { creditCost: 3 } } },
      { params: { deep: { acuUsed: 12.5, other: "x" } } },
      { params: { committed_acu_cost: 9 } },
    ];
    assert.equal(maxCostField(notifs), 12.5);
    assert.equal(maxCostField([]), 0);
  });
});
