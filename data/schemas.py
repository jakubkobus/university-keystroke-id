"""Sample contract (GUI -> DATA) and its validation.

A sample is a dict:
    {
      'user_id': 'U03', 'session_id': 'S01', 'prompt_id': 'fixed_01',
      'entered_text': 'biometria2026', 'valid': True,
      'events': [{'key': 'b', 'type': 'down', 'time_ms': 0.0}, ...]
    }
Time is in ms relative to the start of the attempt (monotonic clock on the GUI side).
"""
from __future__ import annotations

SCHEMA_VERSION = 1
EVENT_TYPES = ("down", "up")
REQUIRED_FIELDS = ("user_id", "session_id", "prompt_id", "entered_text", "valid", "events")
MAX_EVENTS = 200          # hard cap, far above 2 * len(prompt); protects the database
MAX_CLIENT_ID_LEN = 64

# Plain-text test prompt (NOT a password). To be agreed with the team.
PROMPTS = {"fixed_01": "biometria2026"}


class SampleError(ValueError):
    """The sample violates the data contract."""


def validate_sample(sample: dict, prompts: dict | None = None) -> None:
    """Raise SampleError on the first contract violation.

    Samples with valid=False must still have a correct STRUCTURE, but they do not
    need complete down/up pairs or the right text (that is why they were rejected).
    """
    prompts = PROMPTS if prompts is None else prompts
    for f in REQUIRED_FIELDS:
        if f not in sample:
            raise SampleError(f"missing field '{f}'")
    if not isinstance(sample["valid"], bool):
        raise SampleError("'valid' must be a bool")
    events = sample["events"]
    if not isinstance(events, list):
        raise SampleError("'events' must be a list")
    if len(events) > MAX_EVENTS:
        raise SampleError(f"too many events ({len(events)} > {MAX_EVENTS})")
    cid = sample.get("client_id")
    if cid is not None and (not isinstance(cid, str) or not cid
                            or len(cid) > MAX_CLIENT_ID_LEN):
        raise SampleError("'client_id' must be a non-empty string of at most "
                          f"{MAX_CLIENT_ID_LEN} characters")

    prev_t = None
    for i, ev in enumerate(events):
        if not isinstance(ev, dict) or set(ev) != {"key", "type", "time_ms"}:
            raise SampleError(f"event {i}: required fields are key/type/time_ms")
        if ev["type"] not in EVENT_TYPES:
            raise SampleError(f"event {i}: type '{ev['type']}' (allowed: down/up)")
        if not isinstance(ev["key"], str) or not ev["key"]:
            raise SampleError(f"event {i}: empty key")
        t = ev["time_ms"]
        if isinstance(t, bool) or not isinstance(t, (int, float)) or t < 0:
            raise SampleError(f"event {i}: time_ms must be a number >= 0")
        if prev_t is not None and t < prev_t:
            raise SampleError(f"event {i}: time is decreasing ({t} < {prev_t})")
        prev_t = t

    if not sample["valid"]:
        return

    expected = prompts.get(sample["prompt_id"])
    if expected is None:
        raise SampleError(f"unknown prompt_id '{sample['prompt_id']}'")
    if sample["entered_text"] != expected:
        raise SampleError("valid=True, but the entered text differs from the reference text")
    pair_events(events, expected)


def pair_events(events: list[dict], text: str) -> list[tuple[float, float]]:
    """Pair down/up events and return [(press_ms, release_ms)] in PRESS order.

    The press order must match the characters of `text`. Overlapping keys
    (next key down before the previous key up) are allowed.
    Raises SampleError on a missing pair or a wrong character.
    """
    pending: dict[str, list[int]] = {}   # key -> queue of indices into `pairs`
    pairs: list[list[float | None]] = []
    order: list[str] = []
    for ev in events:
        k = ev["key"]
        if ev["type"] == "down":
            pairs.append([ev["time_ms"], None])
            order.append(k)
            pending.setdefault(k, []).append(len(pairs) - 1)
        else:
            queue = pending.get(k)
            if not queue:
                raise SampleError(f"'up' without 'down' for key '{k}'")
            pairs[queue.pop(0)][1] = ev["time_ms"]
    if any(p[1] is None for p in pairs):
        raise SampleError("missing 'up' for at least one 'down'")
    if "".join(order) != text:
        raise SampleError("key press order does not match the reference text")
    return [(p[0], p[1]) for p in pairs]
