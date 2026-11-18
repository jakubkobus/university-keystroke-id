// Run:  node --test tests/js
import test from "node:test";
import assert from "node:assert/strict";

import { Recorder, MAX_EVENTS } from "../../frontend/recorder.js";
import { Api, ApiError, Outbox } from "../../frontend/api.js";

const TEXT = "biometria2026";

// Types `text` with fixed hold/gap; returns the recorder. `t` advances in ms.
function typeAll(rec, text = TEXT, { hold = 90, gap = 70, start = 1000 } = {}) {
  let t = start;
  for (const ch of text) {
    rec.keyDown(ch, t);
    rec.keyUp(ch, t + hold);
    t += hold + gap;
  }
  return rec;
}

test("clean attempt is valid, starts at 0 ms, has 26 ordered events", () => {
  const rec = typeAll(new Recorder(TEXT));
  assert.equal(rec.complete, true);
  assert.equal(rec.valid, true);
  assert.equal(rec.events.length, 26);
  assert.equal(rec.events[0].time_ms, 0);
  assert.equal(rec.events[1].time_ms, 90);
  assert.equal(rec.events[2].time_ms, 160);
  const s = rec.toSample("S01", "fixed_01", "id-1");
  assert.equal(s.valid, true);
  assert.equal(s.entered_text, TEXT);
  for (let i = 1; i < s.events.length; i++) assert.ok(s.events[i].time_ms >= s.events[i - 1].time_ms);
});

test("overlapping keys (next down before previous up) are allowed", () => {
  const rec = new Recorder("ab");
  rec.keyDown("a", 0); rec.keyDown("b", 60); rec.keyUp("a", 100); rec.keyUp("b", 150);
  assert.equal(rec.complete, true);
  assert.equal(rec.valid, true);
  assert.deepEqual(rec.events.map((e) => [e.key, e.type, e.time_ms]),
    [["a", "down", 0], ["b", "down", 60], ["a", "up", 100], ["b", "up", 150]]);
});

test("not complete while the last key is still held", () => {
  const rec = new Recorder("ab");
  rec.keyDown("a", 0); rec.keyUp("a", 50); rec.keyDown("b", 100);
  assert.equal(rec.complete, false);
  assert.equal(rec.finished, false);
  rec.keyUp("b", 150);
  assert.equal(rec.complete, true);
});

test("wrong key invalidates immediately", () => {
  const rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.keyUp("b", 50); rec.keyDown("x", 100);
  assert.equal(rec.invalidReason, "wrong_key");
  assert.equal(rec.finished, true);
  assert.equal(rec.toSample("S01", "fixed_01", "i").valid, false);
});

test("Backspace / Delete are 'correction', other special keys are 'unsupported_key'", () => {
  let rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.keyUp("b", 50); rec.keyDown("Backspace", 80);
  assert.equal(rec.invalidReason, "correction");
  rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.keyDown("Shift", 20);
  assert.equal(rec.invalidReason, "unsupported_key");
  rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.keyDown("Unidentified", 20);
  assert.equal(rec.invalidReason, "unsupported_key");
});

test("special keys BEFORE the first character do not start an attempt", () => {
  const rec = new Recorder(TEXT);
  rec.keyDown("Shift", 0); rec.keyDown("Tab", 5); rec.keyDown("Backspace", 9);
  assert.equal(rec.started, false);
  assert.equal(rec.valid, true);
  typeAll(rec, TEXT, { start: 500 });
  assert.equal(rec.valid, true);
  assert.equal(rec.events[0].time_ms, 0);
});

test("autorepeat and composition invalidate", () => {
  let rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.keyDown("b", 30, { repeat: true });
  assert.equal(rec.invalidReason, "autorepeat");
  rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.keyDown("i", 30, { composing: true });
  assert.equal(rec.invalidReason, "composition");
});

test("focus loss and paste invalidate only a running attempt", () => {
  let rec = new Recorder(TEXT);
  rec.focusLost(); rec.pasteOrDrop(); rec.invalidate("unsupported_key");
  assert.equal(rec.valid, true);                       // nothing started: no poisoning
  typeAll(rec);
  assert.equal(rec.valid, true);

  rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.focusLost();
  assert.equal(rec.invalidReason, "focus_lost");
  rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.pasteOrDrop();
  assert.equal(rec.invalidReason, "paste");
});

test("keyup without keydown: ignored before start, invalid after", () => {
  const rec = new Recorder(TEXT);
  rec.keyUp("Enter", 0);                               // e.g. Enter pressed on the previous screen
  assert.equal(rec.started, false);
  rec.keyDown("b", 10); rec.keyUp("z", 20);
  assert.equal(rec.invalidReason, "up_without_down");
});

test("too many characters", () => {
  const rec = typeAll(new Recorder("ab"), "ab");
  assert.equal(rec.complete, true);
  rec.keyDown("c", 999);                                // ignored: attempt already complete
  assert.equal(rec.events.length, 4);
});

test("reset gives a clean attempt", () => {
  const rec = new Recorder(TEXT);
  rec.keyDown("x", 0);
  assert.equal(rec.valid, false);
  rec.reset();
  typeAll(rec);
  assert.equal(rec.valid, true);
});

test("event count is capped and long key names are truncated to server limits", () => {
  const rec = new Recorder("a".repeat(500));
  for (let i = 0; i < 300; i++) rec.keyDown("a", i);
  assert.ok(rec.events.length <= MAX_EVENTS);
  assert.ok(rec.toSample("S01", "fixed_01", "i").events.length <= MAX_EVENTS);

  const r2 = new Recorder("ab");
  r2.keyDown("a", 0); r2.keyDown("LaunchApplication1", 5);
  assert.ok(r2.events.every((e) => e.key.length <= 16));
});

test("empty key name never reaches the server as ''", () => {
  const rec = new Recorder(TEXT);
  rec.keyDown("b", 0); rec.keyDown("", 5);
  assert.ok(rec.events.every((e) => e.key.length >= 1));
});

// ------------------------------------------------------------------ api / outbox

function fakeFetch(script) {
  const calls = [];
  const f = async (path, opts) => {
    calls.push({ path, opts });
    const r = script.shift();
    if (r === "network") throw new TypeError("failed to fetch");
    return { ok: r < 400, status: r, json: async () => (r < 400 ? { ok: true } : { detail: "x" }) };
  };
  f.calls = calls;
  return f;
}
const smp = (id) => ({ session_id: "S01", prompt_id: "fixed_01", entered_text: "", valid: false,
                       events: [], client_id: id });

test("outbox keeps samples on network/5xx failure and resends the SAME client_id", async () => {
  const f = fakeFetch(["network", 503, 201]);
  const box = new Outbox(new Api("code", f), "U01");
  box.add(smp("A"));
  assert.deepEqual(await box.flush(), { sent: 0, dropped: 0, pending: 1 });
  assert.deepEqual(await box.flush(), { sent: 0, dropped: 0, pending: 1 });
  assert.deepEqual(await box.flush(), { sent: 1, dropped: 0, pending: 0 });
  for (const c of f.calls) assert.equal(JSON.parse(c.opts.body).client_id, "A");
  assert.equal(f.calls[0].opts.headers["X-Invite-Code"], "code");
});

test("outbox drops permanently rejected samples and continues with the rest", async () => {
  const f = fakeFetch([422, 201, 200]);
  const box = new Outbox(new Api("c", f), "U01");
  box.add(smp("A")); box.add(smp("B")); box.add(smp("C"));
  const r = await box.flush();
  assert.deepEqual(r, { sent: 2, dropped: 1, pending: 0 });
});

test("outbox keeps order and stops at the first transient failure", async () => {
  const f = fakeFetch([201, "network"]);
  const box = new Outbox(new Api("c", f), "U01");
  box.add(smp("A")); box.add(smp("B")); box.add(smp("C"));
  assert.deepEqual(await box.flush(), { sent: 1, dropped: 0, pending: 2 });
  assert.equal(f.calls.length, 2);
});

test("concurrent flush calls are serialised (no double send)", async () => {
  const f = fakeFetch([201, 201]);
  const box = new Outbox(new Api("c", f), "U01");
  box.add(smp("A")); box.add(smp("B"));
  const [a, b] = await Promise.all([box.flush(), box.flush()]);
  assert.equal(f.calls.length, 2);
  assert.deepEqual(a, b);
});

test("outbox is per participant", async () => {
  const f = fakeFetch([201]);
  const shared = new Outbox(new Api("c", f), "U01");
  shared.add(smp("A"));
  shared.userId = "U02";
  assert.equal(shared.pending, 0);
  assert.deepEqual(await shared.flush(), { sent: 0, dropped: 0, pending: 0 });
});

test("ApiError carries status; 401 from the server is surfaced", async () => {
  const api = new Api("bad", fakeFetch([401]));
  await assert.rejects(() => api.me(), (e) => e instanceof ApiError && e.status === 401);
  const off = new Api("c", fakeFetch(["network"]));
  await assert.rejects(() => off.me(), (e) => e instanceof ApiError && e.status === 0);
});
