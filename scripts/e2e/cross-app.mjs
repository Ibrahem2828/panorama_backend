// Cross-application end-to-end run: student web app <-> backend <-> staff dashboard.
// Needs the local stack (see ops/e2e/README.md): backend :8000, web :3000, dashboard :3100, seeded with scripts/e2e/seed.py.
//   node ops/e2e/cross-app.mjs
import assert from "node:assert/strict";

const WEB = process.env.E2E_WEB_URL ?? "http://127.0.0.1:3000";
const DASH = process.env.E2E_DASHBOARD_URL ?? "http://127.0.0.1:3100";
const PASSWORD = process.env.E2E_PASSWORD ?? "E2eStrongPass#2026";
const API = "/api/backend/api/v1";

class Client {
  constructor(base, name) { this.base = base; this.name = name; this.jar = new Map(); }
  absorb(response) {
    for (const raw of response.headers.getSetCookie?.() ?? []) {
      const [pair, ...attrs] = raw.split(";");
      const [n, ...v] = pair.split("=");
      const value = v.join("=");
      if (attrs.some((a) => /max-age=0/i.test(a)) || value === "") this.jar.delete(n.trim());
      else this.jar.set(n.trim(), value);
    }
  }
  async call(path, { method = "GET", body, form, headers = {} } = {}) {
    const h = { Origin: new URL(this.base).origin, ...headers };
    if (this.jar.size) h.Cookie = [...this.jar].map(([n, v]) => `${n}=${v}`).join("; ");
    if (method !== "GET" && this.jar.has("panorama_csrf")) h["x-panorama-csrf"] = this.jar.get("panorama_csrf");
    let payload;
    if (form) payload = form;
    else if (body !== undefined) { h["Content-Type"] = "application/json"; payload = JSON.stringify(body); }
    const response = await fetch(this.base + path, { method, headers: h, body: payload, redirect: "manual" });
    this.absorb(response);
    const text = await response.text();
    let json = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    return { status: response.status, json, text, data: json?.data, rows: json?.data?.results ?? json?.data ?? [] };
  }
  async login(identifier, password = PASSWORD) {
    this.jar.clear();
    return this.call("/api/auth/login", { method: "POST", body: { identifier, password } });
  }
}

let failures = 0;
const results = [];
async function step(name, fn) {
  try { await fn(); results.push(1); console.log(`  ok  ${name}`); }
  catch (error) { failures += 1; results.push(0); console.error(`FAIL  ${name}\n      ${String(error.message).slice(0, 500)}`); }
}
const ok = (r, expected = [200, 201]) => assert.ok([].concat(expected).includes(r.status), `HTTP ${r.status}: ${r.text.slice(0, 400)}`);
const key = () => `x-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
const stamp = Date.now();

function tinyPdf(pages = 1) {
  const NL = String.fromCharCode(10);
  const kids = Array.from({ length: pages }, (_, i) => `${3 + i} 0 R`).join(" ");
  const objs = ["<< /Type /Catalog /Pages 2 0 R >>", `<< /Type /Pages /Kids [${kids}] /Count ${pages} >>`];
  for (let i = 0; i < pages; i += 1) objs.push("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] >>");
  let out = `%PDF-1.4${NL}`;
  const offsets = [];
  objs.forEach((body, i) => { offsets.push(out.length); out += `${i + 1} 0 obj${NL}${body}${NL}endobj${NL}`; });
  const xref = out.length;
  out += `xref${NL}0 ${objs.length + 1}${NL}0000000000 65535 f ${NL}`;
  for (const o of offsets) out += `${String(o).padStart(10, "0")} 00000 n ${NL}`;
  out += `trailer${NL}<< /Size ${objs.length + 1} /Root 1 0 R >>${NL}startxref${NL}${xref}${NL}%%EOF${NL}`;
  return Buffer.from(out, "latin1");
}
// 1x1 PNG
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==", "base64");

const student = new Client(WEB, "student");
const printStaff = new Client(DASH, "print-staff");
const admin = new Client(DASH, "admin");
const itSupport = new Client(DASH, "it-support");
console.log(`Cross-app run: web=${WEB} dashboard=${DASH}`);

// ---------------------------------------------------------------- access boundaries
await step("staff sign in on the dashboard; students and normal users are refused", async () => {
  ok(await printStaff.login("print@e2e.test"));
  ok(await admin.login("admin@e2e.test"));
  ok(await itSupport.login("it@e2e.test"));
  const s = await new Client(DASH, "s").login("student@e2e.test");
  assert.ok([401, 403].includes(s.status), `student got a dashboard session: ${s.status} ${s.text.slice(0, 200)}`);
  const n = await new Client(DASH, "n").login("normal@e2e.test");
  assert.ok([401, 403].includes(n.status), `normal user got a dashboard session: ${n.status}`);
});

await step("staff are refused by the student web app", async () => {
  const r = await new Client(WEB, "staff-on-web").login("admin@e2e.test");
  assert.ok([401, 403].includes(r.status), `staff account opened a student session: ${r.status} ${r.text.slice(0, 200)}`);
});

await step("dashboard stats are scoped to the caller's capabilities", async () => {
  const forPrint = await printStaff.call(`${API}/dashboard/stats`);
  ok(forPrint);
  assert.ok("total_orders" in forPrint.data.printing, "print staff cannot see printing stats");
  assert.deepEqual(forPrint.data.users, {}, "print staff can see user stats");
  const forAdmin = await admin.call(`${API}/dashboard/stats`);
  ok(forAdmin);
  assert.ok("total" in forAdmin.data.users, "admin cannot see user stats");
});

// ---------------------------------------------------------------- printing: student -> staff -> student
let orderId;
await step("student places a print order in the web app", async () => {
  ok(await student.login("student@e2e.test"));
  const pickup = (await student.call(`${API}/printing/pickup-locations/`)).rows[0].id;
  const form = new FormData();
  form.append("items[0]uploaded_file", new Blob([tinyPdf(4)], { type: "application/pdf" }), "cross-app.pdf");
  for (const [k, v] of Object.entries({ copies: 2, color_mode: "color", paper_size: "A4", sides: "one_sided", binding: "none" })) form.append(`items[0]${k}`, String(v));
  form.append("pickup_location", String(pickup));
  form.append("user_notes", `cross-app ${stamp}`);
  const r = await student.call(`${API}/printing/orders/`, { method: "POST", headers: { "Idempotency-Key": key() }, form });
  ok(r);
  orderId = r.data.id;
  assert.equal(Number(r.data.total_price), 3200, `expected 4 pages x 2 copies x 400 colour A4 = 3200, got ${r.data.total_price}`);
});

await step("print staff sees the order on the dashboard with the same price", async () => {
  const list = await printStaff.call(`${API}/dashboard/printing/orders/`);
  ok(list);
  const found = list.rows.find((o) => o.id === orderId);
  assert.ok(found, "order is not visible to print staff");
  const detail = await printStaff.call(`${API}/dashboard/printing/orders/${orderId}/`);
  ok(detail);
  assert.equal(detail.data.status, "submitted");
});

await step("print staff moves the order through the status machine; an illegal jump is refused", async () => {
  const bad = await printStaff.call(`${API}/dashboard/printing/orders/${orderId}/status/`, { method: "PATCH", body: { status: "delivered" } });
  assert.ok([400, 409, 422].includes(bad.status), `submitted -> delivered was allowed: ${bad.status}`);
  for (const status of ["under_review", "accepted", "printing", "ready"]) {
    const r = await printStaff.call(`${API}/dashboard/printing/orders/${orderId}/status/`, { method: "PATCH", body: { status } });
    ok(r);
  }
});

await step("student sees the new status and receives a notification", async () => {
  const order = await student.call(`${API}/printing/orders/my/${orderId}/`);
  ok(order);
  assert.equal(order.data.status, "ready");
  const notifications = await student.call(`${API}/notifications/`);
  ok(notifications);
  assert.ok(notifications.rows.length >= 1, "no notification was created for the status change");
});

// ---------------------------------------------------------------- support: student <-> staff
let ticketId;
await step("student opens a support ticket; admin answers it from the dashboard; student sees the answer", async () => {
  const created = await student.call(`${API}/support/tickets/`, { method: "POST", headers: { "Idempotency-Key": key() }, body: { category: "technical", subject: `Cross-app ${stamp}`, message: "Please help." } });
  ok(created);
  ticketId = created.data.id;
  const queue = await admin.call(`${API}/dashboard/support/tickets/`);
  ok(queue);
  assert.ok(queue.rows.some((t) => t.id === ticketId), "ticket missing from the staff queue");
  ok(await admin.call(`${API}/dashboard/support/tickets/${ticketId}/messages/`, { method: "POST", body: { message: "We are on it." } }));
  const mine = await student.call(`${API}/support/tickets/${ticketId}/`);
  ok(mine);
  const texts = JSON.stringify(mine.data);
  assert.ok(texts.includes("We are on it."), "student cannot see the staff reply");
});

// ---------------------------------------------------------------- verification: student -> admin
const email = `flow+${stamp}@e2e.test`;
const phone = `+9639${String(stamp).slice(-8)}`;
const fresh = new Client(WEB, "fresh-student");
await step("a new student registers and verifies the e-mail code", async () => {
  const reg = await fresh.call(`${API}/auth/register/student/`, { method: "POST", body: { full_name: "Flow Student", email, phone_number: phone, password: PASSWORD, password_confirm: PASSWORD, student_number: "1234567", otp_channel: "email" } });
  ok(reg);
  ok(await fresh.call(`${API}/auth/otp/verify/`, { method: "POST", body: { identifier: email, channel: "email", purpose: "verify_email", code: reg.data.development_otp } }));
  ok(await fresh.login(email));
});

await step("the student completes the academic profile and submits the university card", async () => {
  const uni = (await fresh.call(`${API}/universities/`)).rows[0];
  const fac = (await fresh.call(`${API}/universities/${uni.id}/faculties/`)).rows[0];
  const maj = (await fresh.call(`${API}/faculties/${fac.id}/majors/`)).rows[0];
  const year = (await fresh.call(`${API}/academic-years/`)).rows[0];
  const sem = (await fresh.call(`${API}/semesters/`)).rows[0];
  const patch = await fresh.call(`${API}/students/me/profile/`, { method: "PATCH", body: { university: uni.id, faculty: fac.id, major: maj.id, academic_year: year.id, semester: sem.id } });
  ok(patch);
  const form = new FormData();
  form.append("card_image", new Blob([PNG], { type: "image/png" }), "card.png");
  for (const [k, v] of Object.entries({ university: uni.id, faculty: fac.id, major: maj.id, academic_year: year.id, semester: sem.id, student_number: "1234567" })) form.append(k, String(v));
  const submit = await fresh.call(`${API}/verification/submit/`, { method: "POST", headers: { "Idempotency-Key": key() }, form });
  assert.ok([200, 201, 400].includes(submit.status), submit.text.slice(0, 400));
  console.log(`      submit -> ${submit.status} ${submit.text.slice(0, 200)}`);
  ok(submit);
});

let verificationId;
await step("admin sees the pending verification and approves it", async () => {
  const list = await admin.call(`${API}/dashboard/verifications/`);
  ok(list);
  const pending = list.rows.find((v) => (v.user?.email ?? v.user_email ?? "").includes(String(stamp)) || v.student_number === "1234567");
  assert.ok(pending, `no pending verification for the new student (${list.rows.length} rows)`);
  verificationId = pending.id;
  ok(await admin.call(`${API}/dashboard/verifications/${verificationId}/approve/`, { method: "POST", headers: { "Idempotency-Key": key() }, body: {} }));
});

await step("the student is now approved and can use groups", async () => {
  const status = await fresh.call(`${API}/verification/me/`);
  ok(status);
  assert.equal(status.data.status ?? status.data.verification_status, "approved");
  ok(await fresh.call(`${API}/groups/my/`));
});

// ---------------------------------------------------------------- RBAC: the escalation fixes seen through the real UI stack
await step("an admin cannot escalate privileges or touch IT support; IT support can", async () => {
  const me = (await admin.call(`${API}/auth/me`)).data;
  const own = await admin.call(`${API}/dashboard/users/${me.id}/permission-overrides`, { method: "PUT", headers: { "Idempotency-Key": key() }, body: { permission_code: "system.manage", effect: "allow" } });
  assert.equal(own.status, 403, `admin self-escalation returned ${own.status}`);
  const users = await admin.call(`${API}/dashboard/users/`);
  ok(users);
  const it = users.rows.find((u) => u.role === "it_support");
  assert.ok(it, "no IT support account listed");
  const deactivate = await admin.call(`${API}/dashboard/users/${it.id}/deactivate`, { method: "POST", headers: { "Idempotency-Key": key() }, body: { reason: "should be refused" } });
  assert.equal(deactivate.status, 403, `admin deactivated IT support: ${deactivate.status}`);
  const ok2 = await itSupport.call(`${API}/dashboard/users/`);
  ok(ok2);
});

// ---------------------------------------------------------------- operations: maintenance mode
await step("maintenance mode blocks the student app (503), keeps login open, and can be lifted by staff", async () => {
  const created = await admin.call(`${API}/dashboard/maintenance-modes/`, { method: "POST", headers: { "Idempotency-Key": key() }, body: { enabled: true, message_en: "Back soon", message_ar: "نعود قريباً", retry_after_seconds: 30 } });
  ok(created);
  const id = created.data.id;
  try {
    await new Promise((resolve) => setTimeout(resolve, 100));
    const blocked = await student.call(`${API}/announcements`);
    assert.equal(blocked.status, 503, `maintenance did not block the student (got ${blocked.status}; the config cache may need up to 60s)`);
    // sign-in (and the /auth/me read that builds the session) must keep working so staff can lift maintenance
    ok(await new Client(WEB, "m").login("student@e2e.test"));
    ok(await new Client(DASH, "staff-in-maintenance").login("admin@e2e.test"));
  } finally {
    await admin.call(`${API}/dashboard/maintenance-modes/${id}`, { method: "PATCH", body: { enabled: false } });
  }
});

const failed = results.filter((r) => r === 0).length;
console.log(`\n${results.length - failed}/${results.length} steps passed`);
process.exit(failures ? 1 : 0);
