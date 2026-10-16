/**
 * fake-acp.mjs — scripted ACP agent over newline-delimited JSON-RPC stdio.
 * Spawned by tests as `node fake-acp.mjs` (DevinAcp bin=process.execPath).
 *
 * Prompt text triggers:
 *   "HANG"                 never answers session/prompt (tests client timeout)
 *   "NEED_PERM <cmd>"      issues session/request_permission (kind=execute,
 *                          rawInput.command=<cmd>), echoes OUTCOME:<optionId|cancelled>
 *   "RUN_TERM <cmd...>"    terminal/create + terminal/output + wait_for_exit,
 *                          echoes TERM_OUT:<text> or TERM_ERR:<message>
 *   "READ_FILE <path>"     fs/read_text_file, echoes FILE_CONTENT:<text> or FILE_ERR:<msg>
 *   "WRITE_FILE <path>"    fs/write_text_file (content "fixture-write"),
 *                          echoes WRITE_OK or WRITE_ERR:<msg>
 *   "COST <n>"             emits a notification containing creditCost <n>
 *   "DUMP_SESSION_NEW"     replies NEW_PARAMS:<json of last session/new params>
 *   "AUTH_STATE"           replies AUTH:<methodId|none> — the methodId the
 *                          last authenticate used, "none" if never called
 *
 * Env: FAKE_ACP_AUTH_METHODS — JSON array of {id} auth methods advertised
 * in the initialize result (tests ACP auth negotiation).
 */

import readline from "node:readline";

const pending = new Map();
let nextId = 1000;
let model = "swe-2";
let lastSessionNewParams = null;
let lastAuthMethod = null;

let advertisedAuth = null;
try {
  advertisedAuth = process.env.FAKE_ACP_AUTH_METHODS
    ? JSON.parse(process.env.FAKE_ACP_AUTH_METHODS)
    : null;
} catch {
  advertisedAuth = null;
}

const MODEL_OPT = () => ({
  id: "model",
  currentValue: model,
  options: [
    { value: "swe-2", name: "SWE-2", description: "fixture strong" },
    { value: "free-1", name: "Free", description: "fixture free" },
  ],
});

function send(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}
function respond(id, result, error) {
  const out = { jsonrpc: "2.0", id };
  if (error) out.error = error;
  else out.result = result ?? null;
  send(out);
}
function request(method, params) {
  const id = ++nextId;
  send({ jsonrpc: "2.0", id, method, params });
  return new Promise((resolve, reject) => pending.set(id, { resolve, reject }));
}
function notify(method, params) {
  send({ jsonrpc: "2.0", method, params });
}
function chunk(text) {
  notify("session/update", {
    sessionId: "fx",
    update: { sessionUpdate: "agent_message_chunk", content: { type: "text", text } },
  });
}
async function finish(id, text, extra = {}) {
  if (extra.cost != null) {
    notify("session/update", {
      sessionId: "fx",
      update: { sessionUpdate: "usage", stats: { creditCost: extra.cost } },
    });
  }
  if (text) chunk(text);
  notify("_cognition.ai/agent_stopped", {
    sessionId: "fx",
    stats: { modelLabel: "fake-model-1" },
  });
  respond(id, { stopReason: "end_turn", usage: { tokens: 42 } });
}

async function runPrompt(id, params) {
  const text = (params.prompt || []).map((b) => b.text || "").join("");
  try {
    if (text === "HANG") return; // never respond
    if (text.startsWith("NEED_PERM ")) {
      const cmd = text.slice(10);
      const res = await request("session/request_permission", {
        sessionId: params.sessionId,
        toolCall: {
          toolCallId: "tc-1",
          kind: "execute",
          title: cmd,
          rawInput: { command: cmd },
        },
        options: [
          { optionId: "o_allow", kind: "allow_once", name: "Allow once" },
          { optionId: "o_allow_always", kind: "allow_always", name: "Always" },
          { optionId: "o_reject", kind: "reject_once", name: "Reject" },
        ],
      });
      const outcome = res?.outcome?.outcome === "selected"
        ? `OUTCOME:${res.outcome.optionId}`
        : "OUTCOME:cancelled";
      return finish(id, outcome);
    }
    if (text.startsWith("RUN_TERM ")) {
      const cmd = text.slice(9);
      try {
        const { terminalId } = await request("terminal/create", {
          sessionId: params.sessionId,
          command: cmd.split(" ")[0],
          args: cmd.split(" ").slice(1),
          outputByteLimit: 65536,
        });
        await request("terminal/wait_for_exit", { sessionId: params.sessionId, terminalId });
        const out = await request("terminal/output", { sessionId: params.sessionId, terminalId });
        return finish(id, `TERM_OUT:${out.output.trim()}`);
      } catch (e) {
        return finish(id, `TERM_ERR:${e.message}`);
      }
    }
    if (text.startsWith("READ_FILE ")) {
      const p = text.slice(10);
      try {
        const res = await request("fs/read_text_file", { sessionId: params.sessionId, path: p });
        return finish(id, `FILE_CONTENT:${res.content.trim()}`);
      } catch (e) {
        return finish(id, `FILE_ERR:${e.message}`);
      }
    }
    if (text.startsWith("WRITE_FILE ")) {
      const p = text.slice(11);
      try {
        await request("fs/write_text_file", {
          sessionId: params.sessionId,
          path: p,
          content: "fixture-write",
        });
        return finish(id, "WRITE_OK");
      } catch (e) {
        return finish(id, `WRITE_ERR:${e.message}`);
      }
    }
    if (text.startsWith("COST ")) {
      return finish(id, "cost noted", { cost: Number(text.slice(5)) || 0 });
    }
    if (text === "DUMP_SESSION_NEW") {
      return finish(id, `NEW_PARAMS:${JSON.stringify(lastSessionNewParams)}`);
    }
    if (text === "AUTH_STATE") {
      return finish(id, `AUTH:${lastAuthMethod ?? "none"}`);
    }
    return finish(id, `echo: ${text}`);
  } catch (e) {
    respond(id, null, { code: -32603, message: String(e.message || e) });
  }
}

process.stdout.write("fake-acp banner (not json, must be ignored by client)\n");

readline.createInterface({ input: process.stdin }).on("line", (line) => {
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  if (msg.id !== undefined && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
    return;
  }
  if (msg.id === undefined || !msg.method) return; // notification from client
  switch (msg.method) {
    case "initialize":
      return respond(msg.id, {
        protocolVersion: 1,
        agentCapabilities: { loadSession: true },
        ...(advertisedAuth ? { authMethods: advertisedAuth } : {}),
      });
    case "authenticate":
      lastAuthMethod = msg.params?.methodId || "?";
      return respond(msg.id, {});
    case "session/new":
      lastSessionNewParams = msg.params || {};
      return respond(msg.id, { sessionId: "fx-new-1", configOptions: [MODEL_OPT()] });
    case "session/load": {
      const sid = msg.params?.sessionId;
      if (sid === "fx-locked") {
        return respond(msg.id, null, {
          code: -32000,
          message: "session locked",
          data: { "cognition.ai/errorKind": "session_locked" },
        });
      }
      if (sid === "fx-missing") {
        return respond(msg.id, null, {
          code: -32000,
          message: "no such session",
          data: { "cognition.ai/errorKind": "not_found" },
        });
      }
      return respond(msg.id, { sessionId: sid, configOptions: [MODEL_OPT()] });
    }
    case "session/set_config_option":
      model = msg.params?.value || model;
      return respond(msg.id, { configOptions: [MODEL_OPT()] });
    case "session/prompt":
      return void runPrompt(msg.id, msg.params || {});
    default:
      return respond(msg.id, null, { code: -32601, message: `unknown method ${msg.method}` });
  }
});
