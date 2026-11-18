"""Local SQLite database: users, sessions, samples, events.

All functions take an optional `db_path` (default keystrokeid.db, ignored by Git).
No HTTP/REST: the GUI and ML modules call these functions directly.
"""
from __future__ import annotations

import csv
import hashlib
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from .features import extract_features, feature_names
from .schemas import PROMPTS, SCHEMA_VERSION, SampleError, validate_sample

DEFAULT_DB_PATH = "keystrokeid.db"

_DDL = """
CREATE TABLE IF NOT EXISTS users (
    user_id    TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    consent_at TEXT
);
CREATE TABLE IF NOT EXISTS invites (
    code_hash  TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL UNIQUE REFERENCES users(user_id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        TEXT NOT NULL REFERENCES users(user_id),
    session_number INTEGER NOT NULL,
    prompt_id      TEXT NOT NULL,
    started_at     TEXT NOT NULL,
    device_note    TEXT,
    UNIQUE (user_id, session_number)
);
CREATE TABLE IF NOT EXISTS samples (
    sample_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     INTEGER NOT NULL REFERENCES sessions(id),
    prompt_id      TEXT NOT NULL,
    entered_text   TEXT NOT NULL,
    valid          INTEGER NOT NULL CHECK (valid IN (0, 1)),
    created_at     TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    client_id      TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    sample_id   INTEGER NOT NULL REFERENCES samples(sample_id) ON DELETE CASCADE,
    event_index INTEGER NOT NULL,
    key_name    TEXT NOT NULL,
    event_type  TEXT NOT NULL CHECK (event_type IN ('down', 'up')),
    time_ms     REAL NOT NULL,
    UNIQUE (sample_id, event_index)
);
CREATE INDEX IF NOT EXISTS idx_sessions_user  ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_samples_session ON samples(session_id);
CREATE INDEX IF NOT EXISTS idx_events_sample   ON events(sample_id);
"""


def _now() -> str:
    # Metadata only (created_at). H/DD/UD timings come from the GUI measurement.
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH):
    """Connection with foreign_keys=ON; commit on success, rollback on error."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(_DDL)
    # WAL lets readers and the single writer work concurrently (persistent setting).
    with connect(db_path) as conn:
        conn.execute("PRAGMA journal_mode = WAL")


def add_user(pseudonym: str, db_path: str = DEFAULT_DB_PATH) -> str:
    """Add a user (e.g. 'U03'). Returns user_id. Raises ValueError if it already exists."""
    if not pseudonym or not pseudonym.strip():
        raise ValueError("empty pseudonym")
    with connect(db_path) as conn:
        try:
            conn.execute("INSERT INTO users(user_id, created_at) VALUES (?, ?)",
                         (pseudonym, _now()))
        except sqlite3.IntegrityError:
            raise ValueError(f"user '{pseudonym}' already exists") from None
    return pseudonym


def list_users(db_path: str = DEFAULT_DB_PATH) -> list[str]:
    with connect(db_path) as conn:
        return [r["user_id"] for r in
                conn.execute("SELECT user_id FROM users ORDER BY user_id")]


def create_session(user_id: str, prompt_id: str, device_note: str | None = None,
                   db_path: str = DEFAULT_DB_PATH) -> int:
    """Create the user's next session (S01, S02, ...). Returns the internal session id."""
    if prompt_id not in PROMPTS:
        raise ValueError(f"unknown prompt_id '{prompt_id}'")
    with connect(db_path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
            raise ValueError(f"unknown user '{user_id}'")
        n = conn.execute("SELECT COALESCE(MAX(session_number), 0) + 1 FROM sessions "
                         "WHERE user_id=?", (user_id,)).fetchone()[0]
        cur = conn.execute(
            "INSERT INTO sessions(user_id, session_number, prompt_id, started_at, device_note) "
            "VALUES (?, ?, ?, ?, ?)", (user_id, n, prompt_id, _now(), device_note))
        return cur.lastrowid


def save_sample(session_id: int, sample: dict, db_path: str = DEFAULT_DB_PATH) -> int:
    """Validate and save a sample + its events atomically (all or nothing).

    `session_id` is the id returned by create_session(). The user_id/session_id
    fields of the sample dict must match the session stored in the database.
    If the sample carries a `client_id` that was already stored, nothing is written
    and the existing sample_id is returned (safe retry after a network error).
    """
    validate_sample(sample)
    with connect(db_path) as conn:
        row = conn.execute("SELECT user_id, session_number, prompt_id FROM sessions WHERE id=?",
                           (session_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown session {session_id}")
        label = f"S{row['session_number']:02d}"
        if sample["user_id"] != row["user_id"] or sample["session_id"] != label:
            raise SampleError(
                f"sample ({sample['user_id']}/{sample['session_id']}) does not match "
                f"the session in the database ({row['user_id']}/{label})")
        if sample["prompt_id"] != row["prompt_id"]:
            raise SampleError("sample prompt_id differs from the session prompt_id")
        client_id = sample.get("client_id")
        if client_id is not None:
            dup = conn.execute("SELECT sample_id FROM samples WHERE client_id=?",
                               (client_id,)).fetchone()
            if dup is not None:      # retried upload: idempotent, return the existing id
                return dup["sample_id"]
        cur = conn.execute(
            "INSERT INTO samples(session_id, prompt_id, entered_text, valid, created_at, "
            "schema_version, client_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (session_id, sample["prompt_id"], sample["entered_text"],
             int(sample["valid"]), _now(), SCHEMA_VERSION, client_id))
        sample_id = cur.lastrowid
        conn.executemany(
            "INSERT INTO events(sample_id, event_index, key_name, event_type, time_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            [(sample_id, i, e["key"], e["type"], float(e["time_ms"]))
             for i, e in enumerate(sample["events"])])
        return sample_id


def load_sample(sample_id: int, db_path: str = DEFAULT_DB_PATH) -> dict:
    """Return the sample in contract format (+ 'sample_id'), events in original order."""
    with connect(db_path) as conn:
        s = conn.execute(
            "SELECT sa.*, se.user_id, se.session_number FROM samples sa "
            "JOIN sessions se ON se.id = sa.session_id WHERE sa.sample_id=?",
            (sample_id,)).fetchone()
        if s is None:
            raise KeyError(f"no such sample {sample_id}")
        evs = conn.execute(
            "SELECT key_name, event_type, time_ms FROM events WHERE sample_id=? "
            "ORDER BY event_index", (sample_id,)).fetchall()
    return {
        "sample_id": s["sample_id"],
        "user_id": s["user_id"],
        "session_id": f"S{s['session_number']:02d}",
        "prompt_id": s["prompt_id"],
        "entered_text": s["entered_text"],
        "valid": bool(s["valid"]),
        "events": [{"key": e["key_name"], "type": e["event_type"], "time_ms": e["time_ms"]}
                   for e in evs],
    }


def get_user_samples(user_id: str, only_valid: bool = True,
                     db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    with connect(db_path) as conn:
        q = ("SELECT sa.sample_id FROM samples sa JOIN sessions se ON se.id = sa.session_id "
             "WHERE se.user_id=?" + (" AND sa.valid=1" if only_valid else "") +
             " ORDER BY sa.sample_id")
        ids = [r[0] for r in conn.execute(q, (user_id,))]
    return [load_sample(i, db_path) for i in ids]


def get_samples_for_training(prompt_id: str = "fixed_01", session_numbers: list[int] | None = None,
                             db_path: str = DEFAULT_DB_PATH):
    """Return (X, y, groups) from valid=1 samples only.

    X - list of feature vectors [H.., DD.., UD..] (see features.py),
    y - user_id labels, groups - session numbers (for session-based splits).
    `session_numbers` filters sessions, e.g. [1] = train, [2] = validation, [3] = test.
    """
    text = PROMPTS[prompt_id]
    sql = ("SELECT sa.sample_id, se.user_id, se.session_number FROM samples sa "
           "JOIN sessions se ON se.id = sa.session_id WHERE sa.valid=1 AND sa.prompt_id=?")
    params: list = [prompt_id]
    if session_numbers:
        sql += f" AND se.session_number IN ({','.join('?' * len(session_numbers))})"
        params += list(session_numbers)
    sql += " ORDER BY se.user_id, se.session_number, sa.sample_id"
    with connect(db_path) as conn:
        rows = conn.execute(sql, params).fetchall()
    X, y, groups = [], [], []
    for r in rows:
        smp = load_sample(r["sample_id"], db_path)
        X.append(extract_features(smp["events"], text))
        y.append(r["user_id"])
        groups.append(r["session_number"])
    return X, y, groups


def export_csv(path: str, prompt_id: str = "fixed_01", db_path: str = DEFAULT_DB_PATH) -> int:
    """Write a CSV: user_id, session_number, H_*, DD_*, UD_*. Returns the row count.

    Note: the file contains biometric data - do not push it to a public repo.
    """
    X, y, groups = get_samples_for_training(prompt_id, db_path=db_path)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "session_number"] + feature_names(PROMPTS[prompt_id]))
        for xi, yi, gi in zip(X, y, groups):
            w.writerow([yi, gi] + [f"{v:.3f}" for v in xi])
    return len(X)


# ---------------------------------------------------------------- invites / consent

def _hash_code(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def create_invite(user_id: str, db_path: str = DEFAULT_DB_PATH) -> str:
    """Create a one-per-user invite code. Returns the plain code - shown ONCE, only
    its hash is stored. Raises ValueError for an unknown user or an existing invite."""
    code = secrets.token_urlsafe(9)
    with connect(db_path) as conn:
        if conn.execute("SELECT 1 FROM users WHERE user_id=?", (user_id,)).fetchone() is None:
            raise ValueError(f"unknown user '{user_id}'")
        try:
            conn.execute("INSERT INTO invites(code_hash, user_id, created_at) VALUES (?, ?, ?)",
                         (_hash_code(code), user_id, _now()))
        except sqlite3.IntegrityError:
            raise ValueError(f"user '{user_id}' already has an invite") from None
    return code


def user_for_code(code: str, db_path: str = DEFAULT_DB_PATH) -> str | None:
    """Return the user_id owning this invite code, or None."""
    if not code or len(code) > 128:
        return None
    with connect(db_path) as conn:
        row = conn.execute("SELECT user_id FROM invites WHERE code_hash=?",
                           (_hash_code(code),)).fetchone()
    return row["user_id"] if row else None


def record_consent(user_id: str, db_path: str = DEFAULT_DB_PATH) -> str:
    """Store the consent timestamp (first consent wins). Returns the timestamp."""
    with connect(db_path) as conn:
        row = conn.execute("SELECT consent_at FROM users WHERE user_id=?",
                           (user_id,)).fetchone()
        if row is None:
            raise ValueError(f"unknown user '{user_id}'")
        if row["consent_at"]:
            return row["consent_at"]
        ts = _now()
        conn.execute("UPDATE users SET consent_at=? WHERE user_id=?", (ts, user_id))
        return ts


def has_consent(user_id: str, db_path: str = DEFAULT_DB_PATH) -> bool:
    with connect(db_path) as conn:
        row = conn.execute("SELECT consent_at FROM users WHERE user_id=?",
                           (user_id,)).fetchone()
    return bool(row and row["consent_at"])


# ---------------------------------------------------------------- lookups / stats

def find_session(user_id: str, session_label: str, db_path: str = DEFAULT_DB_PATH) -> int | None:
    """Map a label like 'S02' to the internal session id of this user, or None."""
    if not (len(session_label) >= 2 and session_label[0] == "S" and session_label[1:].isdigit()):
        return None
    with connect(db_path) as conn:
        row = conn.execute("SELECT id FROM sessions WHERE user_id=? AND session_number=?",
                           (user_id, int(session_label[1:]))).fetchone()
    return row["id"] if row else None


def find_sample_by_client_id(client_id: str, db_path: str = DEFAULT_DB_PATH) -> int | None:
    with connect(db_path) as conn:
        row = conn.execute("SELECT sample_id FROM samples WHERE client_id=?",
                           (client_id,)).fetchone()
    return row["sample_id"] if row else None


def user_progress(user_id: str, db_path: str = DEFAULT_DB_PATH) -> list[dict]:
    """Per-session counts of valid / invalid samples for one user."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT se.session_number AS n, se.prompt_id AS prompt_id, "
            "COALESCE(SUM(sa.valid), 0) AS valid_count, "
            "COALESCE(SUM(1 - sa.valid), 0) AS invalid_count "
            "FROM sessions se LEFT JOIN samples sa ON sa.session_id = se.id "
            "WHERE se.user_id=? GROUP BY se.id ORDER BY se.session_number",
            (user_id,)).fetchall()
    return [{"session_id": f"S{r['n']:02d}", "prompt_id": r["prompt_id"],
             "valid": r["valid_count"], "invalid": r["invalid_count"]} for r in rows]


def collection_stats(db_path: str = DEFAULT_DB_PATH) -> dict:
    """Totals and a per-user/per-session matrix to monitor data collection."""
    with connect(db_path) as conn:
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        consented = conn.execute(
            "SELECT COUNT(*) FROM users WHERE consent_at IS NOT NULL").fetchone()[0]
        rows = conn.execute(
            "SELECT se.user_id AS u, se.session_number AS n, "
            "COALESCE(SUM(sa.valid), 0) AS v, COALESCE(SUM(1 - sa.valid), 0) AS i "
            "FROM sessions se LEFT JOIN samples sa ON sa.session_id = se.id "
            "GROUP BY se.id ORDER BY se.user_id, se.session_number").fetchall()
    per_user: dict[str, dict] = {}
    for r in rows:
        per_user.setdefault(r["u"], {})[f"S{r['n']:02d}"] = {"valid": r["v"], "invalid": r["i"]}
    return {"users": users, "consented": consented,
            "valid_samples": sum(s["valid"] for u in per_user.values() for s in u.values()),
            "invalid_samples": sum(s["invalid"] for u in per_user.values() for s in u.values()),
            "per_user": per_user}
