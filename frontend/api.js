// Thin API client + an outbox that retries uploads. Timing is measured in the browser;
// nothing here is time-critical (samples are sent only AFTER an attempt is finished).

const OUTBOX_KEY = "keystrokeid.outbox";

export class ApiError extends Error {
  constructor(status, detail) {
    super(typeof detail === "string" ? detail : `HTTP ${status}`);
    this.status = status;
    this.detail = detail;
  }
}

export class Api {
  constructor(code, fetchImpl = (...a) => fetch(...a)) {
    this.code = code;
    this.fetch = fetchImpl;
  }

  async request(method, path, body) {
    const headers = { "X-Invite-Code": this.code };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    let res;
    try {
      res = await this.fetch(path, {
        method, headers, body: body === undefined ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new ApiError(0, "network");          // offline / DNS / TLS / server down
    }
    let data = null;
    try { data = await res.json(); } catch { /* empty or non-JSON body */ }
    if (!res.ok) throw new ApiError(res.status, data && data.detail);
    return { status: res.status, data };
  }

  me() { return this.request("GET", "/api/me").then((r) => r.data); }
  prompts() { return this.request("GET", "/api/prompts").then((r) => r.data.prompts); }
  consent() { return this.request("POST", "/api/consent").then((r) => r.data); }
  createSession(deviceNote) {
    return this.request("POST", "/api/sessions", { prompt_id: "fixed_01", device_note: deviceNote })
      .then((r) => r.data);
  }
  sendSample(sample) { return this.request("POST", "/api/samples", sample); }
}

// ------------------------------------------------------------------ outbox

function readOutbox() {
  try { return JSON.parse(localStorage.getItem(OUTBOX_KEY) || "[]"); } catch { return []; }
}
function writeOutbox(list) {
  try { localStorage.setItem(OUTBOX_KEY, JSON.stringify(list)); } catch { /* storage unavailable */ }
}

// Errors that will never succeed on retry: drop the item instead of blocking the queue.
const PERMANENT = (s) => s >= 400 && s < 500 && s !== 408 && s !== 429;

export class Outbox {
  constructor(api, userId) {
    this.api = api;
    this.userId = userId;               // outbox items are per participant on a shared computer
    this.memory = readOutbox();         // also keeps items if localStorage is unavailable
    this.flushing = null;
  }

  get pending() { return this.memory.filter((i) => i.user === this.userId).length; }

  add(sample) {
    this.memory.push({ user: this.userId, sample });
    writeOutbox(this.memory);
  }

  // Sends queued samples in order. Stops at the first transient failure (network/5xx/429).
  // Resolves to {sent, dropped, pending}. Safe to call repeatedly; calls are serialised.
  flush() {
    if (this.flushing) return this.flushing;
    this.flushing = (async () => {
      let sent = 0, dropped = 0;
      while (true) {
        const idx = this.memory.findIndex((i) => i.user === this.userId);
        if (idx < 0) break;
        try {
          await this.api.sendSample(this.memory[idx].sample);
          sent++;
        } catch (e) {
          if (e instanceof ApiError && PERMANENT(e.status)) {
            console.warn("dropping sample the server rejected:", e.status, e.detail);
            dropped++;
          } else {
            break;                        // transient: keep it and retry later
          }
        }
        this.memory.splice(idx, 1);
        writeOutbox(this.memory);
      }
      return { sent, dropped, pending: this.pending };
    })().finally(() => { this.flushing = null; });
    return this.flushing;
  }
}
