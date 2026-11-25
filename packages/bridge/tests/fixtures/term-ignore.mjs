/**
 * term-ignore.mjs — ignores SIGTERM and idles forever; only SIGKILL (or
 * exit) stops it. Used to prove the TERM→KILL escalation.
 */
process.on("SIGTERM", () => {});
setInterval(() => {}, 60000);
