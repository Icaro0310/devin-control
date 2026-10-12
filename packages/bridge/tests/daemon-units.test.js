// daemon-units.test.js — structural checks for adapters/daemon artifacts.
// The units are config, not code: the tests assert the contract the
// scheduler depends on (right ExecStart, right watch glob, right task
// name in every install path) instead of executing the CLI.
import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const DAEMON = path.join(
  path.dirname(fileURLToPath(import.meta.url)), "..", "adapters", "daemon");

const read = (f) => fs.readFileSync(path.join(DAEMON, f), "utf8");

describe("devin-bridge-intake systemd units", () => {
  it("service runs the real CLI, oneshot", () => {
    const s = read("devin-bridge-intake.service");
    assert.match(s, /\[Service\][\s\S]*Type=oneshot/);
    assert.match(s, /ExecStart=\/usr\/bin\/env devin-bridge intake/);
    // sessions map inside the state dir (hardening whitelist needs it)
    assert.match(s, /--sessions-file %h\/\.local\/state\/devin-bridge/);
  });

  it("service survives fresh profiles — auto-creates state/config roots", () => {
    const s = read("devin-bridge-intake.service");
    assert.match(s, /StateDirectory=devin-bridge/);
    assert.match(s, /ConfigurationDirectory=Devin devin/);
    // remaining Devin roots are optional ("-" prefix) — a missing dir
    // must not fail mount-namespace setup
    for (const m of s.matchAll(/ReadWritePaths=(.*)/g)) {
      for (const p of m[1].trim().split(/\s+/)) {
        assert.ok(p.startsWith("-"), `non-optional ReadWritePaths: ${p}`);
      }
    }
  });

  it("path unit watches the *.json glob, not raw emptiness", () => {
    const p = read("devin-bridge-intake.path");
    assert.match(p, /PathExistsGlob=%h\/\.local\/state\/devin-bridge\/mailbox\/inbox\/\*\.json/);
    assert.doesNotMatch(p, /^\s*DirectoryNotEmpty=/m);
    assert.match(p, /Unit=devin-bridge-intake\.service/);
  });

  it("timer re-arms after each run", () => {
    const t = read("devin-bridge-intake.timer");
    assert.match(t, /OnUnitInactiveSec=/);
    assert.match(t, /WantedBy=timers\.target/);
  });
});

describe("devin-bridge-intake task.xml + ps1", () => {
  it("task name is consistent across xml and both scripts", () => {
    const xml = read("devin-bridge-intake.task.xml");
    assert.match(xml, /<URI>\\devin-bridge-intake<\/URI>/);
    for (const s of ["install-task.ps1", "remove-task.ps1"]) {
      assert.match(read(s), /TaskName = 'devin-bridge-intake'/);
    }
    assert.match(read("install-task.ps1"), /devin-bridge-intake\.task\.xml/);
  });

  it("task action calls the real CLI", () => {
    const xml = read("devin-bridge-intake.task.xml");
    assert.match(xml, /<Arguments>\/c devin-bridge intake<\/Arguments>/);
    assert.match(xml, /<LogonTrigger>/);
  });
});
