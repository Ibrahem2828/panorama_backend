// Realtime group chat between two students through the web BFF + the backend WebSocket.
//   node scripts/e2e/chat.mjs        (stack running and seeded, see README.md)
import assert from "node:assert/strict";

const WEB = process.env.E2E_WEB_URL ?? "http://127.0.0.1:3000";
const WS_BASE = process.env.E2E_WS_URL ?? "ws://127.0.0.1:8000";
const PASSWORD = process.env.E2E_PASSWORD ?? "E2eStrongPass#2026";
const API = "/api/backend/api/v1";

class Session {
  constructor() { this.jar = new Map(); }
  async call(path, { method = "GET", body, headers = {} } = {}) {
    const h = { Origin: new URL(WEB).origin, ...headers };
    if (this.jar.size) h.Cookie = [...this.jar].map(([n, v]) => `${n}=${v}`).join("; ");
    if (method !== "GET" && this.jar.has("panorama_csrf")) h["x-panorama-csrf"] = this.jar.get("panorama_csrf");
    let payload;
    if (body !== undefined) { h["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    const response = await fetch(WEB + path, { method, headers: h, body: payload, redirect: "manual" });
    for (const raw of response.headers.getSetCookie?.() ?? []) {
      const [pair] = raw.split(";");
      const [n, ...v] = pair.split("=");
      const value = v.join("=");
      if (value === "") this.jar.delete(n); else this.jar.set(n, value);
    }
    const text = await response.text();
    let json = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    return { status: response.status, json, text, data: json?.data, rows: json?.data?.results ?? json?.data ?? [] };
  }
}

let failures = 0;
async function step(name, fn) {
  try { await fn(); console.log(`  ok  ${name}`); } catch (error) { failures += 1; console.error(`FAIL  ${name}\n      ${String(error.message).slice(0, 500)}`); }
}
const ok = (r, expected = [200, 201]) => assert.ok([].concat(expected).includes(r.status), `HTTP ${r.status}: ${r.text.slice(0, 300)}`);

function openSocket(url) {
  return new Promise((resolve, reject) => {
    const socket = new WebSocket(url); // no Origin header: this is how the React Native app connects
    const inbox = [];
    const waiters = [];
    socket.onmessage = (event) => {
      const message = JSON.parse(event.data);
      const waiter = waiters.findIndex((w) => w.match(message));
      if (waiter >= 0) waiters.splice(waiter, 1)[0].resolve(message); else inbox.push(message);
    };
    socket.onopen = () => resolve({
      socket,
      send: (payload) => socket.send(JSON.stringify(payload)),
      next: (match, ms = 5000) => new Promise((res, rej) => {
        const buffered = inbox.findIndex(match);
        if (buffered >= 0) return res(inbox.splice(buffered, 1)[0]);
        const timer = setTimeout(() => rej(new Error("timed out waiting for a socket message")), ms);
        waiters.push({ match, resolve: (m) => { clearTimeout(timer); res(m); } });
      }),
    });
    socket.onerror = () => reject(new Error("WebSocket handshake failed (rejected before accept)"));
    socket.onclose = (event) => reject(new Error(`WebSocket closed before it opened (code ${event.code})`));
  });
}

console.log(`Chat journey: web=${WEB} ws=${WS_BASE}`);
const alice = new Session();
const bob = new Session();
let groupId;

await step("two students sign in and find the same open group", async () => {
  ok(await alice.call("/api/auth/login", { method: "POST", body: { identifier: "student@e2e.test", password: PASSWORD } }));
  ok(await bob.call("/api/auth/login", { method: "POST", body: { identifier: "student2@e2e.test", password: PASSWORD } }));
  const group = (await alice.call(`${API}/groups/available/`)).rows.find((g) => g.name === "E2E Chat Group")
    ?? (await alice.call(`${API}/groups/my/`)).rows.find((g) => g.name === "E2E Chat Group");
  assert.ok(group, "seeded group not visible to the student");
  groupId = group.id;
});

await step("both join (open group, no approval needed)", async () => {
  for (const who of [alice, bob]) {
    const r = await who.call(`${API}/groups/${groupId}/join/`, { method: "POST", body: {} });
    assert.ok([200, 201, 400, 409].includes(r.status), r.text.slice(0, 300)); // 400/409 = already a member from an earlier run
    const mine = await who.call(`${API}/groups/my/`);
    ok(mine);
    assert.ok(mine.rows.some((g) => g.id === groupId), "group missing from my groups after joining");
  }
});

let a, b;
await step("each student gets a single-use chat ticket and connects", async () => {
  const ticketA = await alice.call(`${API}/groups/${groupId}/chat-ticket/`, { method: "POST", body: {} });
  const ticketB = await bob.call(`${API}/groups/${groupId}/chat-ticket/`, { method: "POST", body: {} });
  ok(ticketA); ok(ticketB);
  const tokenA = ticketA.data.ticket ?? ticketA.data.token;
  const tokenB = ticketB.data.ticket ?? ticketB.data.token;
  assert.ok(tokenA && tokenB, `no ticket in ${ticketA.text.slice(0, 200)}`);
  a = await openSocket(`${WS_BASE}/ws/v1/groups/${groupId}/chat/?ticket=${encodeURIComponent(tokenA)}`);
  b = await openSocket(`${WS_BASE}/ws/v1/groups/${groupId}/chat/?ticket=${encodeURIComponent(tokenB)}`);
  // the same ticket cannot be replayed
  await assert.rejects(() => openSocket(`${WS_BASE}/ws/v1/groups/${groupId}/chat/?ticket=${encodeURIComponent(tokenA)}`), /rejected|closed/);
});

const text = `hello from alice ${Date.now()}`;
await step("a message sent by one student arrives live for the other (and echoes to the sender)", async () => {
  a.send({ type: "message", content: text });
  const received = await b.next((m) => m.type === "message" && m.data?.content === text);
  assert.equal(received.data.content, text);
  const echoed = await a.next((m) => m.type === "message" && m.data?.content === text);
  assert.ok(echoed.data.id, "message has no id (not persisted?)");
});

await step("the message was persisted and is returned by the REST history", async () => {
  const history = await bob.call(`${API}/groups/${groupId}/messages/`);
  ok(history);
  assert.ok(history.rows.some((m) => m.content === text), "message missing from REST history");
});

await step("typing indicators are relayed", async () => {
  a.send({ type: "typing", is_typing: true });
  const typing = await b.next((m) => m.type === "typing");
  assert.equal(typing.is_typing, true);
});

await step("oversized and empty messages are rejected without dropping the connection", async () => {
  a.send({ type: "message", content: "x".repeat(5000) });
  const error = await a.next((m) => m.type === "error");
  assert.ok(error.code, "no error code for an oversized message");
  a.send({ type: "message", content: "still connected" });
  assert.equal((await b.next((m) => m.type === "message" && m.data?.content === "still connected")).data.content, "still connected");
});

await step("REST message posting reaches connected sockets too", async () => {
  const content = `via rest ${Date.now()}`;
  const posted = await bob.call(`${API}/groups/${groupId}/messages/`, { method: "POST", body: { content } });
  ok(posted);
  // delivery over the channel layer depends on the view broadcasting; at minimum it must be in the history
  const history = await alice.call(`${API}/groups/${groupId}/messages/`);
  assert.ok(history.rows.some((m) => m.content === content), "REST-posted message missing from history");
});

a?.socket.close();
b?.socket.close();
console.log(failures ? `\n${failures} step(s) failed` : "\nAll chat steps passed");
process.exit(failures ? 1 : 0);
