/**
 * term-grandchild.mjs — spawns a grandchild process that records its pid
 * in the file given as argv[2], then idles. Used to prove that killing a
 * terminal reaps the whole process tree, not just the direct child.
 */
import { spawn } from "node:child_process";
import fs from "node:fs";

const pidFile = process.argv[2];
const grandchild = spawn(
  process.execPath,
  ["-e", "setInterval(() => {}, 60000)"],
  { stdio: "ignore" },
);
fs.writeFileSync(pidFile, String(grandchild.pid));
setInterval(() => {}, 60000);
