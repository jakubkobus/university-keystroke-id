// Keystroke recorder: pure logic, no DOM access, so it can be unit-tested in Node.
//
// Records keydown/keyup of ONE attempt at typing a fixed prompt. Times are ms since the
// first keydown (the first event is at 0.0). The attempt is marked invalid (never silently
// repaired) on: wrong key, autorepeat, Backspace/Delete/arrows/Shift/IME/unknown keys, an up
// without a down, too many keys, focus loss, paste/drop. Invalid attempts are still sent
// (valid=false) so the report can state how many entries were rejected.

export const MAX_KEY_NAME = 16;   // server limit for the 'key' field
export const MAX_EVENTS = 200;    // server limit for events per sample

export class Recorder {
  constructor(prompt) {
    this.prompt = prompt;
    this.reset();
  }

  reset() {
    this.events = [];          // [{key, type: 'down'|'up', time_ms}]
    this.typed = [];           // characters in press order
    this.down = new Map();     // key -> number of currently pressed (unreleased) presses
    this.t0 = null;
    this.invalidReason = null; // null while valid
    this.complete = false;
  }

  get valid() { return this.invalidReason === null; }
  get started() { return this.t0 !== null; }
  get enteredText() { return this.typed.join(""); }
  get pressedCount() { return this.typed.length; }

  _invalidate(reason) {
    if (this.invalidReason === null) this.invalidReason = reason;
  }

  _push(key, type, now) {
    if (this.events.length >= MAX_EVENTS) { this._invalidate("too_many_keys"); return; }
    this.events.push({
      key: key.slice(0, MAX_KEY_NAME),
      type,
      time_ms: Math.round((now - this.t0) * 1000) / 1000,
    });
  }

  // `now` is a DOMHighResTimeStamp (event.timeStamp, same clock as performance.now()).
  keyDown(key, now, { repeat = false, composing = false } = {}) {
    key = key || "Unidentified";
    if (this.complete) return;
    if (this.t0 === null) {
      // A non-printable key before the first character (Tab, Shift, ...) must not start the
      // attempt; Backspace on an empty field is harmless. Only a printable key starts it.
      if (key.length !== 1 || composing) return;
      this.t0 = now;
    }
    if (repeat) this._invalidate("autorepeat");
    if (composing) this._invalidate("composition");
    if (key.length !== 1) {
      this._invalidate(key === "Backspace" || key === "Delete" ? "correction" : "unsupported_key");
      this._push(key, "down", now);
      return;
    }
    if (this.typed.length >= this.prompt.length) this._invalidate("too_many_keys");
    else if (key !== this.prompt[this.typed.length]) this._invalidate("wrong_key");
    this.typed.push(key);
    this.down.set(key, (this.down.get(key) || 0) + 1);
    this._push(key, "down", now);
  }

  keyUp(key, now) {
    key = key || "Unidentified";
    if (this.t0 === null || this.complete) return;   // release of a key pressed before the attempt
    if (key.length !== 1) {
      this._push(key, "up", now);
      return;
    }
    const n = this.down.get(key) || 0;
    if (n === 0) { this._invalidate("up_without_down"); return; }
    this.down.set(key, n - 1);
    this._push(key, "up", now);
    this._checkComplete();
  }

  _checkComplete() {
    if (this.typed.length !== this.prompt.length) return;
    for (const n of this.down.values()) if (n > 0) return;   // a key is still held
    this.complete = true;
    if (this.enteredText !== this.prompt) this._invalidate("wrong_text");
  }

  // For things the DOM layer detects (Ctrl/Alt/Meta shortcuts). No-op before the attempt starts.
  invalidate(reason) { if (this.started && !this.complete) this._invalidate(reason); }

  focusLost() { if (this.started && !this.complete) this._invalidate("focus_lost"); }
  pasteOrDrop() { this.invalidate("paste"); }   // blocked by the DOM layer; no-op before the start

  // An attempt that has already failed can be finished early (we do not wait for the full text).
  get finished() { return this.complete || (this.started && !this.valid); }

  toSample(sessionId, promptId, clientId) {
    return {
      session_id: sessionId,
      prompt_id: promptId,
      entered_text: this.enteredText.slice(0, 128),
      valid: this.valid && this.complete,
      events: this.events.slice(0, MAX_EVENTS),
      client_id: clientId,
    };
  }
}
