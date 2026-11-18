"""KeystrokeID HTTP API (FastAPI) - a thin layer over the data/ module.

Run locally:   uvicorn api.main:create_app --factory --reload
Docs (Swagger): http://localhost:8000/docs

Configuration (environment variables):
    KEYSTROKEID_DB            SQLite file path             (default storage/keystrokeid.db)
    KEYSTROKEID_ADMIN_KEY     enables /api/admin/* (sent as the X-Admin-Key header);
                              when unset, admin endpoints are disabled
    KEYSTROKEID_MAX_SESSIONS  max sessions per participant (default 10)
    KEYSTROKEID_FRONTEND_DIR  static frontend to serve at '/' (default ./frontend)

Participants authenticate with a personal invite code in the X-Invite-Code header.
Timing values are measured in the browser; the server never measures time itself.
"""
from __future__ import annotations

import importlib
import os
import secrets
import tempfile
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from data import database as db
from data.features import extract_features
from data.schemas import MAX_CLIENT_ID_LEN, MAX_EVENTS, PROMPTS, SampleError

USER_ID_PATTERN = r"^[A-Za-z0-9_-]{1,32}$"


# ------------------------------------------------------------------ request models

class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str = Field(min_length=1, max_length=16)
    type: Literal["down", "up"]
    time_ms: float = Field(ge=0, le=600_000, allow_inf_nan=False)


class SampleIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(pattern=r"^S\d{1,4}$", description="session label, e.g. 'S01'")
    prompt_id: str = Field(max_length=32)
    entered_text: str = Field(max_length=128)
    valid: bool
    events: list[EventIn] = Field(max_length=MAX_EVENTS)
    client_id: str | None = Field(
        default=None, min_length=1, max_length=MAX_CLIENT_ID_LEN,
        description="client-generated UUID; makes retried uploads idempotent")


class SessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_id: str = Field(default="fixed_01", max_length=32)
    device_note: str | None = Field(default=None, max_length=200)


class PredictIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt_id: str = Field(default="fixed_01", max_length=32)
    events: list[EventIn] = Field(max_length=MAX_EVENTS)


class NewUserIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user_id: str = Field(pattern=USER_ID_PATTERN)


# ------------------------------------------------------------------ app factory

def create_app(db_path: str | None = None, admin_key: str | None = None,
               frontend_dir: str | None = None, max_sessions: int | None = None) -> FastAPI:
    db_path = db_path or os.environ.get("KEYSTROKEID_DB", "storage/keystrokeid.db")
    admin_key = admin_key if admin_key is not None else os.environ.get("KEYSTROKEID_ADMIN_KEY")
    max_sessions = max_sessions or int(os.environ.get("KEYSTROKEID_MAX_SESSIONS", "10"))
    frontend_dir = frontend_dir or os.environ.get("KEYSTROKEID_FRONTEND_DIR", "frontend")

    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    db.init_db(db_path)

    app = FastAPI(title="KeystrokeID API", version="1.0")

    # -------------------------------------------------------------- dependencies

    def current_user(x_invite_code: str | None = Header(default=None)) -> str:
        user = db.user_for_code(x_invite_code or "", db_path)
        if user is None:
            raise HTTPException(401, "missing or invalid invite code")
        return user

    def require_admin(x_admin_key: str | None = Header(default=None)) -> None:
        if not admin_key:
            raise HTTPException(403, "admin endpoints are disabled")
        if not x_admin_key or not secrets.compare_digest(x_admin_key, admin_key):
            raise HTTPException(403, "invalid admin key")

    def require_consent(user: str = Depends(current_user)) -> str:
        if not db.has_consent(user, db_path):
            raise HTTPException(403, "consent has not been given")
        return user

    # -------------------------------------------------------------- public

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    @app.get("/api/prompts")
    def prompts():
        return {"prompts": PROMPTS}

    # -------------------------------------------------------------- participant

    @app.get("/api/me")
    def me(user: str = Depends(current_user)):
        return {"user_id": user, "consent": db.has_consent(user, db_path),
                "sessions": db.user_progress(user, db_path)}

    @app.post("/api/consent")
    def consent(user: str = Depends(current_user)):
        return {"user_id": user, "consent_at": db.record_consent(user, db_path)}

    @app.post("/api/sessions", status_code=201)
    def new_session(body: SessionIn, user: str = Depends(require_consent)):
        if len(db.user_progress(user, db_path)) >= max_sessions:
            raise HTTPException(429, f"session limit reached ({max_sessions})")
        try:
            db.create_session(user, body.prompt_id, body.device_note, db_path)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        return {"session_id": db.user_progress(user, db_path)[-1]["session_id"]}

    @app.post("/api/samples", status_code=201)
    def add_sample(body: SampleIn, response: Response, user: str = Depends(require_consent)):
        session_pk = db.find_session(user, body.session_id, db_path)
        if session_pk is None:
            raise HTTPException(404, f"unknown session '{body.session_id}'")
        if body.client_id:
            existing = db.find_sample_by_client_id(body.client_id, db_path)
            if existing is not None:
                response.status_code = 200
                return {"sample_id": existing, "valid": None, "duplicate": True}
        sample = body.model_dump()
        sample["user_id"] = user          # identity comes from the invite code, never the body
        try:
            sample_id = db.save_sample(session_pk, sample, db_path)
        except SampleError as e:
            raise HTTPException(422, str(e)) from None
        return {"sample_id": sample_id, "valid": body.valid, "duplicate": False}

    @app.post("/api/predict")
    def predict(body: PredictIn, user: str = Depends(require_consent)):
        try:
            ml_predict = importlib.import_module("ml.predict")
        except ImportError:
            raise HTTPException(501, "models are not available yet") from None
        text = PROMPTS.get(body.prompt_id)
        if text is None:
            raise HTTPException(422, f"unknown prompt_id '{body.prompt_id}'")
        try:
            features = extract_features([e.model_dump() for e in body.events], text)
        except SampleError as e:
            raise HTTPException(422, str(e)) from None
        return {"predictions": ml_predict.predict_all(features)}

    # -------------------------------------------------------------- admin

    @app.post("/api/admin/users", status_code=201, dependencies=[Depends(require_admin)])
    def admin_create_user(body: NewUserIn):
        try:
            db.add_user(body.user_id, db_path)
            code = db.create_invite(body.user_id, db_path)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None
        return {"user_id": body.user_id, "invite_code": code}

    @app.get("/api/admin/stats", dependencies=[Depends(require_admin)])
    def admin_stats():
        return db.collection_stats(db_path)

    @app.get("/api/admin/export.csv", dependencies=[Depends(require_admin)])
    def admin_export(prompt_id: str = "fixed_01"):
        if prompt_id not in PROMPTS:
            raise HTTPException(422, f"unknown prompt_id '{prompt_id}'")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "export.csv"
            db.export_csv(str(out), prompt_id, db_path)
            content = out.read_text(encoding="utf-8")
        return Response(content, media_type="text/csv",
                        headers={"Content-Disposition": "attachment; filename=samples.csv"})

    # -------------------------------------------------------------- static frontend
    # Mounted last so that /api/* routes always win.
    if Path(frontend_dir).is_dir():
        app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")

    return app

