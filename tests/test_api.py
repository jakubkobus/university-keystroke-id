"""API tests. Need the venv packages:  .venv/Scripts/python -m pytest tests/test_api.py"""
import os
import sqlite3
import tempfile
import uuid
from contextlib import closing

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from api.main import create_app  # noqa: E402
from data.schemas import PROMPTS  # noqa: E402

TEXT = PROMPTS["fixed_01"]
ADMIN = {"X-Admin-Key": "secret-admin"}


def events_for(text=TEXT, hold=90.0, gap=70.0):
    ev, t = [], 0.0
    for ch in text:
        ev.append({"key": ch, "type": "down", "time_ms": t})
        ev.append({"key": ch, "type": "up", "time_ms": t + hold})
        t += hold + gap
    return ev


def sample_body(session="S01", **over):
    body = {"session_id": session, "prompt_id": "fixed_01", "entered_text": TEXT,
            "valid": True, "events": events_for(), "client_id": str(uuid.uuid4())}
    body.update(over)
    return body


@pytest.fixture()
def env():
    with tempfile.TemporaryDirectory() as tmp:
        db_path = os.path.join(tmp, "t.db")
        client = TestClient(create_app(db_path=db_path, admin_key="secret-admin",
                                       frontend_dir=os.path.join(tmp, "none"), max_sessions=3))
        yield client, db_path


def make_user(client, uid="U01"):
    r = client.post("/api/admin/users", json={"user_id": uid}, headers=ADMIN)
    assert r.status_code == 201, r.text
    return {"X-Invite-Code": r.json()["invite_code"]}


def ready_user(client, uid="U01"):
    """User with consent and one session S01."""
    h = make_user(client, uid)
    assert client.post("/api/consent", headers=h).status_code == 200
    assert client.post("/api/sessions", json={}, headers=h).json() == {"session_id": "S01"}
    return h


# ---------------------------------------------------------------- basics / auth

def test_health_and_prompts(env):
    c, _ = env
    assert c.get("/api/health").json() == {"status": "ok"}
    assert c.get("/api/prompts").json()["prompts"]["fixed_01"] == TEXT


def test_participant_endpoints_require_invite_code(env):
    c, _ = env
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/me", headers={"X-Invite-Code": "nope"}).status_code == 401
    assert c.post("/api/samples", json=sample_body()).status_code == 401


def test_admin_requires_key_and_can_be_disabled(env):
    c, _ = env
    assert c.get("/api/admin/stats").status_code == 403
    assert c.get("/api/admin/stats", headers={"X-Admin-Key": "wrong"}).status_code == 403
    with tempfile.TemporaryDirectory() as tmp:
        off = TestClient(create_app(db_path=os.path.join(tmp, "x.db"), admin_key="",
                                    frontend_dir=os.path.join(tmp, "none")))
        assert off.get("/api/admin/stats", headers=ADMIN).status_code == 403


def test_invite_code_is_stored_hashed(env):
    c, db_path = env
    code = make_user(c)["X-Invite-Code"]
    with closing(sqlite3.connect(db_path)) as conn:
        stored = [r[0] for r in conn.execute("SELECT code_hash FROM invites")]
    assert stored and code not in stored[0]


def test_duplicate_user_conflict(env):
    c, _ = env
    make_user(c)
    assert c.post("/api/admin/users", json={"user_id": "U01"}, headers=ADMIN).status_code == 409


def test_bad_user_id_rejected(env):
    c, _ = env
    r = c.post("/api/admin/users", json={"user_id": "bad id!"}, headers=ADMIN)
    assert r.status_code == 422


# ---------------------------------------------------------------- consent / sessions

def test_consent_required_before_sessions_and_samples(env):
    c, _ = env
    h = make_user(c)
    assert c.get("/api/me", headers=h).json()["consent"] is False
    assert c.post("/api/sessions", json={}, headers=h).status_code == 403
    assert c.post("/api/samples", json=sample_body(), headers=h).status_code == 403
    assert c.post("/api/consent", headers=h).status_code == 200
    assert c.get("/api/me", headers=h).json()["consent"] is True


def test_session_numbering_and_limit(env):
    c, _ = env
    h = ready_user(c)                       # S01
    assert c.post("/api/sessions", json={}, headers=h).json()["session_id"] == "S02"
    assert c.post("/api/sessions", json={}, headers=h).json()["session_id"] == "S03"
    assert c.post("/api/sessions", json={}, headers=h).status_code == 429


def test_unknown_prompt_for_session(env):
    c, _ = env
    h = make_user(c)
    c.post("/api/consent", headers=h)
    assert c.post("/api/sessions", json={"prompt_id": "nope"}, headers=h).status_code == 422


# ---------------------------------------------------------------- samples

def test_save_sample_and_progress(env):
    c, _ = env
    h = ready_user(c)
    r = c.post("/api/samples", json=sample_body(), headers=h)
    assert r.status_code == 201
    assert r.json()["duplicate"] is False
    prog = c.get("/api/me", headers=h).json()["sessions"]
    assert prog == [{"session_id": "S01", "prompt_id": "fixed_01", "valid": 1, "invalid": 0}]


def test_retry_with_same_client_id_is_idempotent(env):
    c, _ = env
    h = ready_user(c)
    body = sample_body()
    first = c.post("/api/samples", json=body, headers=h)
    again = c.post("/api/samples", json=body, headers=h)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json() == {"sample_id": first.json()["sample_id"], "valid": None,
                            "duplicate": True}
    assert c.get("/api/me", headers=h).json()["sessions"][0]["valid"] == 1


def test_invalid_attempt_is_stored_but_counted_separately(env):
    c, _ = env
    h = ready_user(c)
    bad = sample_body(valid=False, entered_text="biometria202", events=events_for("biometria202"))
    assert c.post("/api/samples", json=bad, headers=h).status_code == 201
    assert c.get("/api/me", headers=h).json()["sessions"][0]["invalid"] == 1


def test_wrong_text_marked_valid_is_rejected(env):
    c, _ = env
    h = ready_user(c)
    r = c.post("/api/samples", json=sample_body(entered_text="biometria2025"), headers=h)
    assert r.status_code == 422


def test_unknown_session_label(env):
    c, _ = env
    h = ready_user(c)
    assert c.post("/api/samples", json=sample_body("S09"), headers=h).status_code == 404


def test_cannot_write_into_another_users_session(env):
    c, _ = env
    h1 = ready_user(c, "U01")
    h2 = ready_user(c, "U02")
    c.post("/api/samples", json=sample_body(), headers=h1)
    # U02 posts to ITS OWN S01; the user id comes from the code, so U01's data is untouched
    c.post("/api/samples", json=sample_body(), headers=h2)
    s1 = c.get("/api/me", headers=h1).json()["sessions"][0]["valid"]
    s2 = c.get("/api/me", headers=h2).json()["sessions"][0]["valid"]
    assert (s1, s2) == (1, 1)


@pytest.mark.parametrize("mutate", [
    lambda b: b["events"][0].update(time_ms=-5),
    lambda b: b["events"][0].update(type="press"),
    lambda b: b["events"][0].update(time_ms=float("nan")),
    lambda b: b.update(extra_field=1),
    lambda b: b.update(events=b["events"] * 20),           # too many events
    lambda b: b.update(session_id="../etc"),
    lambda b: b["events"].__delitem__(-1),                 # missing 'up'
])
def test_malformed_samples_are_rejected(env, mutate):
    c, _ = env
    h = ready_user(c)
    body = sample_body()
    mutate(body)
    try:
        r = c.post("/api/samples", json=body, headers=h)
    except ValueError:        # the test client itself refuses to send NaN as JSON
        return
    assert r.status_code == 422, r.text
    assert c.get("/api/me", headers=h).json()["sessions"][0]["valid"] == 0


# ---------------------------------------------------------------- predict / admin data

def test_predict_not_implemented_yet(env):
    c, _ = env
    h = ready_user(c)
    r = c.post("/api/predict", json={"events": events_for()}, headers=h)
    assert r.status_code == 501


def test_admin_stats_and_csv_export(env):
    c, _ = env
    h = ready_user(c)
    for _ in range(3):
        c.post("/api/samples", json=sample_body(), headers=h)
    stats = c.get("/api/admin/stats", headers=ADMIN).json()
    assert stats["users"] == 1 and stats["consented"] == 1 and stats["valid_samples"] == 3
    assert stats["per_user"]["U01"]["S01"] == {"valid": 3, "invalid": 0}
    csv_text = c.get("/api/admin/export.csv", headers=ADMIN).text.splitlines()
    assert len(csv_text) == 4 and csv_text[0].startswith("user_id,session_number,H_1")


def test_backup_script(env, monkeypatch):
    c, db_path = env
    ready_user(c)
    monkeypatch.setenv("KEYSTROKEID_DB", db_path)
    from scripts import backup
    assert backup.main() == 0
    backups = os.listdir(os.path.join(os.path.dirname(db_path), "backups"))
    assert len(backups) == 1
    with closing(sqlite3.connect(os.path.join(os.path.dirname(db_path), "backups",
                                              backups[0]))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1
