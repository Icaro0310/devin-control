import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import {
  credentialsPath, devinBin, devinBinCandidates,
  agentArgs, agentBin, agentBinCandidates, agentStrategy, DevinAcp,
} from "../src/acp-client.js";

// PATHEXT on Windows never matches a bare extensionless file — fixtures
// carry the real executable suffix (".EXE", matching PATHEXT's case so
// spec.file string-compares equal).
const EXE = process.platform === "win32" ? ".EXE" : "";


test("Linux credentials follow XDG_DATA_HOME", () => {
  assert.equal(
    credentialsPath({
      platform: "linux",
      env: { XDG_DATA_HOME: "/xdg/data" },
      home: "/home/user",
    }),
    path.join("/xdg/data", "devin", "credentials.toml"),
  );
});

test("Windows credentials follow APPDATA", () => {
  assert.equal(
    credentialsPath({
      platform: "win32",
      env: { APPDATA: "C:\\Users\\user\\AppData\\Roaming" },
      home: "C:\\Users\\user",
    }),
    path.join("C:\\Users\\user\\AppData\\Roaming", "devin", "credentials.toml"),
  );
});

test("explicit credentials path overrides platform defaults", () => {
  assert.equal(
    credentialsPath({
      platform: "linux",
      env: { DEVIN_CREDENTIALS_PATH: "/custom/credentials.toml" },
      home: "/home/user",
    }),
    path.resolve("/custom/credentials.toml"),
  );
});

test("CLI is resolved from PATH", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "devin-bin-"));
  const executable = path.join(directory, "devin" + EXE);
  fs.writeFileSync(executable, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  // Resolution goes through safe-executable, which walks the real
  // process PATH — inject it instead of a candidate-env override.
  const oldPath = process.env.PATH;
  process.env.PATH = directory;
  try {
    const spec = devinBin({ platform: "linux", env: {}, workspaceDir: null });
    assert.ok(spec);
    assert.equal(spec.file, executable);
    assert.deepEqual(spec.argv, []);
  } finally { process.env.PATH = oldPath; }
});

// Windows cwd-search fix: the agent binary must never come from the
// workspace being bridged — a repo-planted devin binary is skipped for
// the user's own install, and a DEVIN_CLI_PATH inside the workspace is
// refused outright.
test("autodetect skips a workspace-planted binary for the user's", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "devin-ws-"));
  const realDir = fs.mkdtempSync(path.join(os.tmpdir(), "devin-real-"));
  for (const dir of [ws, realDir]) {
    fs.writeFileSync(path.join(dir, "devin" + EXE), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  }
  const oldPath = process.env.PATH;
  process.env.PATH = `${ws}${path.delimiter}${realDir}`; // ws wins PATH order
  try {
    const spec = devinBin({ platform: "linux", env: {}, workspaceDir: ws });
    assert.equal(spec && spec.file, path.join(realDir, "devin" + EXE));
  } finally { process.env.PATH = oldPath; }
});

test("a DEVIN_CLI_PATH inside the workspace is refused", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "devin-ws-"));
  const planted = path.join(ws, "devin" + EXE);
  const realDir = fs.mkdtempSync(path.join(os.tmpdir(), "devin-real-"));
  fs.writeFileSync(planted, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  fs.writeFileSync(path.join(realDir, "devin" + EXE), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  const oldPath = process.env.PATH;
  process.env.PATH = realDir;
  try {
    const spec = devinBin({
      platform: "linux",
      env: { DEVIN_CLI_PATH: planted },
      workspaceDir: ws,
    });
    assert.equal(spec && spec.file, path.join(realDir, "devin" + EXE));
  } finally { process.env.PATH = oldPath; }
});

test("Windows bundle candidate follows LOCALAPPDATA", () => {
  const candidates = devinBinCandidates({
    platform: "win32",
    env: { LOCALAPPDATA: "C:\\Users\\user\\AppData\\Local" },
    home: "C:\\Users\\user",
  });
  assert.ok(candidates[0].endsWith("devin.exe"));
});

// ---- configurable agent (BRIDGE_AGENT*) --------------------------------

test("BRIDGE_CREDENTIALS_PATH wins over DEVIN_CREDENTIALS_PATH and defaults", () => {
  assert.equal(
    credentialsPath({
      platform: "linux",
      env: {
        BRIDGE_CREDENTIALS_PATH: "/generic/creds.toml",
        DEVIN_CREDENTIALS_PATH: "/devin/creds.toml",
      },
      home: "/home/user",
    }),
    path.resolve("/generic/creds.toml"),
  );
  // Legacy DEVIN_CREDENTIALS_PATH still applies when the generic is unset.
  assert.equal(
    credentialsPath({
      platform: "linux",
      env: { DEVIN_CREDENTIALS_PATH: "/devin/creds.toml" },
      home: "/home/user",
    }),
    path.resolve("/devin/creds.toml"),
  );
});

test("devin remains the default strategy (explicit or unset)", () => {
  for (const env of [{}, { BRIDGE_AGENT: "devin" }]) {
    assert.equal(agentStrategy({ env }).name, "devin");
    assert.equal(
      credentialsPath({
        platform: "linux",
        env: { ...env, XDG_DATA_HOME: "/xdg/data" },
        home: "/home/user",
      }),
      path.join("/xdg/data", "devin", "credentials.toml"),
    );
  }
});

test("unknown BRIDGE_AGENT fails fast", () => {
  assert.throws(
    () => agentStrategy({ env: { BRIDGE_AGENT: "nope" } }),
    /unknown BRIDGE_AGENT "nope".*devin/,
  );
  assert.throws(
    () => agentBinCandidates({ platform: "linux", env: { BRIDGE_AGENT: "nope" }, home: "/h" }),
    /unknown BRIDGE_AGENT/,
  );
  assert.throws(
    () => credentialsPath({ platform: "linux", env: { BRIDGE_AGENT: "nope" }, home: "/h" }),
    /unknown BRIDGE_AGENT/,
  );
});

test("BRIDGE_AGENT_BIN precedes the strategy candidates", () => {
  const candidates = agentBinCandidates({
    platform: "linux",
    env: { BRIDGE_AGENT_BIN: "/opt/acp/bin/agent" },
    home: "/home/user",
  });
  assert.equal(candidates[0], "/opt/acp/bin/agent");
  assert.ok(candidates.includes("devin"));
});

test("agentBin resolves an explicit BRIDGE_AGENT_BIN executable", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "agent-bin-"));
  const exe = path.join(dir, "my-agent" + EXE);
  fs.writeFileSync(exe, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  const spec = agentBin({
    platform: "linux",
    env: { BRIDGE_AGENT_BIN: exe },
    workspaceDir: null,
  });
  assert.ok(spec);
  assert.equal(spec.file, fs.realpathSync(exe));
});

test("a BRIDGE_AGENT_BIN inside the workspace is refused", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "agent-ws-"));
  const planted = path.join(ws, "agent" + EXE);
  const realDir = fs.mkdtempSync(path.join(os.tmpdir(), "devin-real-"));
  fs.writeFileSync(planted, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  fs.writeFileSync(path.join(realDir, "devin" + EXE), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  const oldPath = process.env.PATH;
  process.env.PATH = realDir;
  try {
    const spec = agentBin({
      platform: "linux",
      env: { BRIDGE_AGENT_BIN: planted },
      workspaceDir: ws,
    });
    // untrusted autodetect skips the planted binary, falls back to devin
    assert.equal(spec && spec.file, path.join(realDir, "devin" + EXE));
  } finally { process.env.PATH = oldPath; }
});

test("agentBin with no agent env keeps the devin autodetect", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "devin-bin-"));
  const exe = path.join(dir, "devin" + EXE);
  fs.writeFileSync(exe, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  const oldPath = process.env.PATH;
  process.env.PATH = dir;
  try {
    const spec = agentBin({ platform: "linux", env: {}, workspaceDir: null });
    assert.ok(spec);
    assert.equal(spec.file, exe);
  } finally { process.env.PATH = oldPath; }
});

test("agentArgs defaults to the devin acp subcommand", () => {
  assert.deepEqual(agentArgs({ env: {} }), ["acp"]);
  assert.deepEqual(agentArgs({ env: { BRIDGE_AGENT: "devin" } }), ["acp"]);
});

test("BRIDGE_AGENT_ARGS parses a JSON argv override", () => {
  assert.deepEqual(
    agentArgs({ env: { BRIDGE_AGENT_ARGS: '["serve","--acp"]' } }),
    ["serve", "--acp"],
  );
  assert.deepEqual(agentArgs({ env: { BRIDGE_AGENT_ARGS: "[]" } }), []);
});

test("BRIDGE_AGENT_ARGS rejects non-array JSON and invalid JSON", () => {
  for (const bad of ["not json", '"acp"', '{"a":1}', '["acp",2]']) {
    assert.throws(
      () => agentArgs({ env: { BRIDGE_AGENT_ARGS: bad } }),
      /BRIDGE_AGENT_ARGS must be a JSON array of strings/,
    );
  }
});

test("DevinAcp honors BRIDGE_AGENT_BIN + BRIDGE_AGENT_ARGS without opts", () => {
  const saved = {
    BRIDGE_AGENT_BIN: process.env.BRIDGE_AGENT_BIN,
    BRIDGE_AGENT_ARGS: process.env.BRIDGE_AGENT_ARGS,
  };
  process.env.BRIDGE_AGENT_BIN = process.execPath;
  process.env.BRIDGE_AGENT_ARGS = '["--acp-fake"]';
  try {
    const acp = new DevinAcp({ cwd: os.tmpdir() });
    assert.equal(acp.bin, fs.realpathSync(process.execPath));
    assert.deepEqual(acp.args, ["--acp-fake"]);
  } finally {
    for (const [k, v] of Object.entries(saved)) {
      if (v === undefined) delete process.env[k]; else process.env[k] = v;
    }
  }
});
