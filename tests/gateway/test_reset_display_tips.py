"""Gateway /new and /reset append a line from display.tips when that list is set."""

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.event import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key
from hermes_constants import get_hermes_home


def _make_source(*, chat_id: str = "c1", thread_id: str | None = None) -> SessionSource:
    return SessionSource(
        platform=Platform.TELEGRAM,
        user_id="u1",
        chat_id=chat_id,
        user_name="tester",
        chat_type="dm",
        thread_id=thread_id,
    )


def _make_event(source: SessionSource) -> MessageEvent:
    return MessageEvent(text="/new", source=source, message_id="m1")


def _make_runner(source: SessionSource):
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.TELEGRAM: PlatformConfig(enabled=True, token="***")}
    )
    adapter = MagicMock()
    adapter.send = AsyncMock()
    runner.adapters = {Platform.TELEGRAM: adapter}
    runner._voice_mode = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._pending_model_notes = {}
    runner._background_tasks = set()

    session_key = build_session_key(source)
    session_entry = SessionEntry(
        session_key=session_key,
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.TELEGRAM,
        chat_type="dm",
    )
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = session_entry
    runner.session_store.reset_session.return_value = session_entry
    runner.session_store._entries = {session_key: session_entry}
    runner.session_store._generate_session_key.return_value = session_key
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._pending_approvals = {}
    runner._session_db = None
    runner._agent_cache_lock = None
    runner._is_user_authorized = lambda _source: True
    runner._reset_notice_session_info = lambda _source: ""
    return runner


def _write_tips() -> None:
    (get_hermes_home() / "config.yaml").write_text(
        "display:\n"
        "  language: zh\n"
        "  tips:\n"
        "    - 等你拍板。我先不动。\n"
        "    - 我这台机器上是好的。\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_new_reply_uses_configured_tip_and_keeps_the_localized_label():
    from hermes_cli.tips import clear_gateway_reset_tip_memory

    clear_gateway_reset_tip_memory()
    _write_tips()
    source = _make_source()
    runner = _make_runner(source)

    first = str(await runner._handle_reset_command(_make_event(source)))
    second = str(await runner._handle_reset_command(_make_event(source)))

    lines = {"等你拍板。我先不动。", "我这台机器上是好的。"}
    assert "提示" not in first
    assert "提示" not in second
    assert any(line in first for line in lines)
    assert any(line in second for line in lines)
    assert first != second
