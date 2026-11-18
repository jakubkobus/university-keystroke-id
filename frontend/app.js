// Screens and flow: login -> consent -> home (sessions) -> typing task.
// Rendering uses textContent only (no innerHTML), so nothing from the server can inject markup.

import { Api, ApiError, Outbox } from "./api.js";
import { Recorder } from "./recorder.js";
import { T } from "./strings.js";

const TARGET_PER_SESSION = 20;
const PROMPT_ID = "fixed_01";
const CODE_KEY = "keystrokeid.code";

const root = document.getElementById("app");
const state = { api: null, outbox: null, me: null, promptText: null };

// ------------------------------------------------------------------ helpers

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(props)) {
    if (k === "class") node.className = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else if (v === true) node.setAttribute(k, "");
    else if (v !== false && v != null) node.setAttribute(k, v);
  }
  for (const c of children.flat()) node.append(c);
  return node;
}

function show(...nodes) {
  root.replaceChildren(...nodes);
  const h1 = root.querySelector("h1");
  if (h1) { h1.setAttribute("tabindex", "-1"); }
}

function store(key, value) {
  try {
    if (value === null) sessionStorage.removeItem(key); else sessionStorage.setItem(key, value);
  } catch { /* storage unavailable */ }
}
function load(key) { try { return sessionStorage.getItem(key); } catch { return null; } }

function uuid() {
  if (crypto.randomUUID) return crypto.randomUUID();
  const b = crypto.getRandomValues(new Uint8Array(16));
  return Array.from(b, (x) => x.toString(16).padStart(2, "0")).join("");
}

function errorText(e) {
  if (e instanceof ApiError && e.status === 0) return T.networkError;
  return T.errorGeneric;
}

// ------------------------------------------------------------------ login

function showLogin(message) {
  const input = el("input", {
    type: "password", id: "code", name: "code", autocomplete: "off", spellcheck: "false",
    autocapitalize: "off", "aria-describedby": "login-msg", required: true,
  });
  const msg = el("p", { id: "login-msg", class: message ? "msg error" : "msg info", role: "alert" },
    message || "");
  if (!message) msg.hidden = true;
  const form = el("form", {
    onsubmit: (ev) => { ev.preventDefault(); login(input.value.trim()); },
  },
    el("label", { for: "code" }, T.loginLabel), input, msg,
    el("div", { class: "actions" }, el("button", { class: "btn", type: "submit" }, T.loginButton)));
  show(el("h1", {}, T.loginTitle), el("p", { class: "muted" }, T.loginIntro),
       el("div", { class: "card" }, form));
  input.focus();
}

async function login(code) {
  if (!code) return showLogin(T.loginInvalid);
  const api = new Api(code);
  try {
    state.me = await api.me();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) { store(CODE_KEY, null); return showLogin(T.loginInvalid); }
    return showLogin(errorText(e));
  }
  store(CODE_KEY, code);
  state.api = api;
  state.outbox = new Outbox(api, state.me.user_id);
  state.outbox.flush();                          // deliver anything left from an earlier visit
  state.me.consent ? showHome() : showConsent();
}

// ------------------------------------------------------------------ consent

function showConsent() {
  const box = el("input", { type: "checkbox", id: "agree" });
  const btn = el("button", { class: "btn", type: "submit", disabled: true }, T.consentButton);
  const msg = el("p", { class: "msg error", role: "alert", hidden: true });
  box.addEventListener("change", () => { btn.disabled = !box.checked; });
  const form = el("form", {
    onsubmit: async (ev) => {
      ev.preventDefault();
      btn.disabled = true;
      try {
        await state.api.consent();
        state.me.consent = true;
        showHome();
      } catch (e) {
        msg.textContent = errorText(e); msg.hidden = false; btn.disabled = !box.checked;
      }
    },
  },
    el("label", { class: "check", for: "agree" }, box, el("span", {}, T.consentCheckbox)),
    msg, el("div", { class: "actions" }, btn));
  show(el("h1", {}, T.consentTitle),
       el("div", { class: "card" }, ...T.consentBody.map((t) => el("p", {}, t)), form));
}

// ------------------------------------------------------------------ home

async function showHome(message) {
  try {
    state.me = await state.api.me();
  } catch (e) {
    return show(el("p", { class: "msg error" }, errorText(e)),
                el("button", { class: "btn", onclick: () => showHome() }, T.retry));
  }
  const sessions = state.me.sessions;
  const last = sessions[sessions.length - 1];
  const canContinue = last && last.valid < TARGET_PER_SESSION;

  const list = sessions.length
    ? sessions.map((s) => el("div", { class: "session-row" },
        el("div", {}, T.sessionRow(s.session_id, s.valid, TARGET_PER_SESSION, s.invalid)),
        el("progress", { max: TARGET_PER_SESSION, value: Math.min(s.valid, TARGET_PER_SESSION),
                         "aria-label": s.session_id })))
    : [el("p", { class: "muted" }, T.noSessions)];

  const startBtn = el("button", { class: "btn", type: "button" },
    canContinue ? T.continueSession(last.session_id) : T.startSession);
  const msg = el("p", { class: "msg error", role: "alert", hidden: true });
  startBtn.addEventListener("click", async () => {
    startBtn.disabled = true;
    if (canContinue) return showTask(last.session_id, last.valid);
    try {
      const note = (navigator.userAgent || "").slice(0, 200);
      const s = await state.api.createSession(note);
      showTask(s.session_id, 0);
    } catch (e) {
      msg.textContent = e instanceof ApiError && e.status === 429 ? T.sessionLimit : errorText(e);
      msg.hidden = false; startBtn.disabled = false;
    }
  });

  show(el("h1", {}, T.homeTitle),
       el("p", { class: "muted" }, T.homeHello(state.me.user_id)),
       message ? el("p", { class: "msg ok" }, message) : "",
       el("div", { class: "card" }, ...list),
       el("p", { class: "muted" }, T.nextSessionHint),
       msg, el("div", { class: "actions" }, startBtn));
}

// ------------------------------------------------------------------ typing task

async function showTask(sessionLabel, alreadyValid) {
  if (!state.promptText) {
    try { state.promptText = (await state.api.prompts())[PROMPT_ID]; }
    catch (e) { return show(el("p", { class: "msg error" }, errorText(e)),
                            el("button", { class: "btn", onclick: () => showHome() }, T.retry)); }
  }
  const promptText = state.promptText;
  const rec = new Recorder(promptText);
  let validCount = alreadyValid;
  let closed = false;

  const field = el("input", {
    type: "text", id: "typing", class: "type-field", autocomplete: "off", autocorrect: "off",
    autocapitalize: "off", spellcheck: "false", "aria-label": promptText,
    placeholder: T.taskPlaceholder, enterkeyhint: "done",
  });
  const status = el("p", { class: "msg info", role: "status" }, T.taskInstruction);
  const counter = el("p", { class: "counter" });
  const pendingNote = el("p", { class: "muted", hidden: true });
  const exitBtn = el("button", { class: "btn secondary", type: "button" }, T.taskExit);

  const setStatus = (text, kind) => { status.className = `msg ${kind}`; status.textContent = text; };
  const renderCounter = () => { counter.textContent = T.taskCounter(validCount, TARGET_PER_SESSION); };
  const renderPending = () => {
    const n = state.outbox.pending;
    pendingNote.hidden = n === 0;
    if (n) pendingNote.textContent = T.taskPending(n);
  };

  async function leave(toHome, message) {
    closed = true;
    await state.outbox.flush();
    toHome ? showHome(message) : null;
  }

  async function finishAttempt() {
    const ok = rec.valid && rec.complete;
    const reason = rec.invalidReason;
    state.outbox.add(rec.toSample(sessionLabel, PROMPT_ID, uuid()));
    rec.reset();
    field.value = "";
    if (ok) validCount++;
    renderCounter();
    if (ok && validCount >= TARGET_PER_SESSION) {
      closed = true;
      field.disabled = true;
      setStatus(T.taskSending, "info");
      await state.outbox.flush();
      return showDone(sessionLabel);
    }
    setStatus(ok ? T.taskOk : (T.reasons[reason] || T.reasonDefault), ok ? "ok" : "error");
    field.focus();
    state.outbox.flush().then(renderPending);
    renderPending();
  }

  const guard = (fn) => (ev) => { if (!closed) fn(ev); };

  field.addEventListener("keydown", guard((e) => {
    if (e.ctrlKey || e.metaKey || e.altKey) { rec.invalidate("unsupported_key"); }
    rec.keyDown(e.key, e.timeStamp, { repeat: e.repeat, composing: e.isComposing });
    if (rec.finished) finishAttempt();
  }));
  field.addEventListener("keyup", guard((e) => {
    rec.keyUp(e.key, e.timeStamp);
    if (rec.finished) finishAttempt();
  }));
  field.addEventListener("blur", guard(() => {
    if (rec.started && !rec.complete) { rec.focusLost(); finishAttempt(); }
  }));
  // Text that appeared without matching key events (autofill, voice, on-screen keyboards,
  // programmatic input): the field no longer equals what the recorder saw typed.
  field.addEventListener("input", guard(() => {
    if (field.value === rec.enteredText) return;
    if (rec.started) { rec.invalidate("composition"); finishAttempt(); return; }
    rec.reset();
    field.value = "";
    setStatus(T.reasons.composition, "error");
  }));
  for (const type of ["paste", "drop", "cut"]) {
    field.addEventListener(type, guard((e) => { e.preventDefault(); rec.pasteOrDrop(); if (rec.finished) finishAttempt(); }));
  }
  field.addEventListener("contextmenu", (e) => e.preventDefault());
  exitBtn.addEventListener("click", () => leave(true));

  renderCounter();
  const touchOnly = window.matchMedia && matchMedia("(pointer: coarse)").matches
                    && !matchMedia("(pointer: fine)").matches;
  show(el("h1", {}, T.taskTitle(sessionLabel)),
       touchOnly ? el("p", { class: "msg error" }, T.taskKeyboard) : el("p", { class: "muted" }, T.taskKeyboard),
       el("div", { class: "card" },
         el("div", { class: "prompt", "aria-hidden": "true" }, promptText),
         field, status, counter, pendingNote),
       el("div", { class: "actions" }, exitBtn));
  renderPending();
  field.focus();
}

function showDone(sessionLabel) {
  show(el("h1", {}, T.taskDone),
       el("p", {}, T.nextSessionHint),
       el("div", { class: "actions" },
         el("button", { class: "btn", onclick: () => showHome() }, T.taskBack)));
}

// ------------------------------------------------------------------ boot

(function boot() {
  // A personal link may carry the code in the URL fragment (#code=...): fragments are never
  // sent to the server. Remove it from the address bar right away.
  let code = null;
  const m = /(?:^#|&)code=([^&]+)/.exec(location.hash);
  if (m) {
    try { code = decodeURIComponent(m[1]); } catch { code = null; }
    history.replaceState(null, "", location.pathname + location.search);
  }
  code = code || load(CODE_KEY);
  code ? login(code) : showLogin();
})();
