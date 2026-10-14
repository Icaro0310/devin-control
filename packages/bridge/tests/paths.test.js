import assert from "node:assert/strict";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { credentialsPath, devinBin, devinBinCandidates } from "../src/acp-client.js";


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

test("Linux CLI is resolved from PATH", () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), "devin-bin-"));
  const executable = path.join(directory, "devin");
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
    fs.writeFileSync(path.join(dir, "devin"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  }
  const oldPath = process.env.PATH;
  process.env.PATH = `${ws}${path.delimiter}${realDir}`; // ws wins PATH order
  try {
    const spec = devinBin({ platform: "linux", env: {}, workspaceDir: ws });
    assert.equal(spec && spec.file, path.join(realDir, "devin"));
  } finally { process.env.PATH = oldPath; }
});

test("a DEVIN_CLI_PATH inside the workspace is refused", () => {
  const ws = fs.mkdtempSync(path.join(os.tmpdir(), "devin-ws-"));
  const planted = path.join(ws, "devin");
  const realDir = fs.mkdtempSync(path.join(os.tmpdir(), "devin-real-"));
  fs.writeFileSync(planted, "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  fs.writeFileSync(path.join(realDir, "devin"), "#!/bin/sh\nexit 0\n", { mode: 0o755 });
  const oldPath = process.env.PATH;
  process.env.PATH = realDir;
  try {
    const spec = devinBin({
      platform: "linux",
      env: { DEVIN_CLI_PATH: planted },
      workspaceDir: ws,
    });
    assert.equal(spec && spec.file, path.join(realDir, "devin"));
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
