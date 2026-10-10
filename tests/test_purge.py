"""Tests cho purge_conversations.py. Chạy trên DB tạm, không chạm DB thật.

    $env:PYTHONPATH = "D:\\rag_chatbot"
    .\\venv\\Scripts\\python.exe -m pytest tests\\test_purge.py -v
"""
from __future__ import annotations

import importlib
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_file = tmp_path / "chat_history.db"
    monkeypatch.setenv("CONVERSATION_DATABASE_URL", f"sqlite:///{db_file.as_posix()}")

    for name in list(sys.modules):
        if name.startswith("services.conversation_service") or name.startswith("scripts.purge"):
            del sys.modules[name]

    database = importlib.import_module("services.conversation_service.database")
    models = importlib.import_module("services.conversation_service.models")
    database.Base.metadata.create_all(database.engine)
    purge = importlib.import_module("scripts.purge_conversations")
    return database, models, purge


def _make_conv(session, models, *, deleted_days_ago=None, generating=False):
    now = datetime.now(timezone.utc)
    conv = models.Conversation(
        id=str(uuid4()),
        owner_id="tester",
        title="t",
        deleted_at=(now - timedelta(days=deleted_days_ago)) if deleted_days_ago is not None else None,
    )
    turn = models.ChatTurn(
        id=str(uuid4()),
        conversation_id=conv.id,
        request_id=str(uuid4()),
        input_hash="x",
        status="generating" if generating else "completed",
    )
    msg = models.Message(
        id=str(uuid4()),
        conversation_id=conv.id,
        turn_id=turn.id,
        sequence=1,
        role="user",
        content="hi",
        status="completed",
    )
    session.add_all([conv, turn, msg])
    session.commit()
    return conv.id


def _run(purge, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["purge", *argv])
    return purge.main()


def test_only_eligible_are_purged(env, monkeypatch):
    database, models, purge = env
    with database.SessionLocal() as s:
        active = _make_conv(s, models)
        recent = _make_conv(s, models, deleted_days_ago=1)
        old = _make_conv(s, models, deleted_days_ago=60)

    assert _run(purge, monkeypatch, "--retention-days", "30", "--execute", "--yes") == 0

    with database.SessionLocal() as s:
        assert s.get(models.Conversation, active) is not None
        assert s.get(models.Conversation, recent) is not None
        assert s.get(models.Conversation, old) is None


def test_cascade_removes_messages(env, monkeypatch):
    database, models, purge = env
    with database.SessionLocal() as s:
        old = _make_conv(s, models, deleted_days_ago=60)

    _run(purge, monkeypatch, "--retention-days", "30", "--execute", "--yes")

    with database.SessionLocal() as s:
        assert s.query(models.Message).filter_by(conversation_id=old).count() == 0
        assert s.query(models.ChatTurn).filter_by(conversation_id=old).count() == 0


def test_skips_generating_turn(env, monkeypatch):
    database, models, purge = env
    with database.SessionLocal() as s:
        busy = _make_conv(s, models, deleted_days_ago=60, generating=True)

    _run(purge, monkeypatch, "--retention-days", "30", "--execute", "--yes")

    with database.SessionLocal() as s:
        assert s.get(models.Conversation, busy) is not None


def test_dry_run_deletes_nothing(env, monkeypatch):
    database, models, purge = env
    with database.SessionLocal() as s:
        old = _make_conv(s, models, deleted_days_ago=60)

    assert _run(purge, monkeypatch, "--retention-days", "30") == 0

    with database.SessionLocal() as s:
        assert s.get(models.Conversation, old) is not None


def test_abort_when_exceeds_max_delete(env, monkeypatch):
    database, models, purge = env
    with database.SessionLocal() as s:
        ids = [_make_conv(s, models, deleted_days_ago=60) for _ in range(3)]

    code = _run(purge, monkeypatch, "--retention-days", "30", "--max-delete", "2", "--execute", "--yes")
    assert code == 2

    with database.SessionLocal() as s:
        assert all(s.get(models.Conversation, i) is not None for i in ids)


def test_backup_is_created(env, monkeypatch, tmp_path):
    database, models, purge = env
    with database.SessionLocal() as s:
        _make_conv(s, models, deleted_days_ago=60)

    _run(purge, monkeypatch, "--retention-days", "30", "--execute", "--yes")
    assert list(tmp_path.glob("chat_history_backup_*.db"))
