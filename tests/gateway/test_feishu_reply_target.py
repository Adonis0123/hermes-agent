"""A reply target that is not a Feishu message id never reaches ``message.reply``.

A card button click is routed as a synthetic event whose ``message_id`` is the callback
token (``c-…``, kept for dedup), and the gateway hands that id back as ``reply_to``.
Feishu's reply API rejects it (99992354 "not a valid {open_message_id}"), so the
confirmation of every click was lost on both the stream-card and the post/text path.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

_CARD_TOKEN = "c-1bd2cb2f0a7e4c55"


@pytest.fixture
def feishu():
    """A real adapter over a fake Feishu that, like the API, only replies to ``om_`` ids."""
    from gateway.config import PlatformConfig
    from plugins.platforms.feishu.adapter import FeishuAdapter

    replies, creates = [], []

    async def _reply(request):
        replies.append(request)
        if not str(request.message_id).startswith("om_"):
            return SimpleNamespace(success=lambda: False, code=99992354, msg="not a valid {open_message_id}")
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_reply"))

    async def _create(request):
        creates.append(request)
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_new"))

    async def _run_blocking(fn, *args, **kwargs):
        return await fn(*args, **kwargs)

    adapter = FeishuAdapter(PlatformConfig())
    adapter._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(reply=_reply, create=_create))),
    )
    with patch.object(adapter, "_run_blocking", _run_blocking), patch.object(
        FeishuAdapter, "_build_reply_message_body", staticmethod(lambda **kwargs: SimpleNamespace(**kwargs)),
    ), patch.object(
        FeishuAdapter, "_build_reply_message_request",
        staticmethod(lambda message_id, body: SimpleNamespace(message_id=message_id, request_body=body)),
    ), patch.object(
        FeishuAdapter, "_build_create_message_body", staticmethod(lambda **kwargs: SimpleNamespace(**kwargs)),
    ), patch.object(
        FeishuAdapter, "_build_create_message_request",
        staticmethod(lambda receive_id_type, request_body: SimpleNamespace(
            receive_id_type=receive_id_type, request_body=request_body)),
    ):
        yield adapter, replies, creates


def test_card_click_confirmation_is_posted_to_the_chat(feishu):
    adapter, replies, creates = feishu

    result = asyncio.run(adapter.send("oc_group", "#25 已标记完成。", reply_to=_CARD_TOKEN))

    assert result.success and result.message_id == "om_new"
    assert replies == []
    assert [(c.receive_id_type, c.request_body.receive_id) for c in creates] == [("chat_id", "oc_group")]

    # A real message id in the metadata is still the anchor: the click token only drops itself.
    result = asyncio.run(adapter.send(
        "oc_group", "#26 已标记完成。", reply_to=_CARD_TOKEN, metadata={"thread_id": "om_root"},
    ))

    assert result.success and result.message_id == "om_reply"
    assert [r.message_id for r in replies] == ["om_root"]
    assert len(creates) == 1


def test_card_click_confirmation_stream_card_is_posted_to_the_chat(feishu):
    adapter, replies, creates = feishu
    adapter._client.cardkit = object()
    adapter._stream_card_enabled = True
    adapter._create_stream_card_entity = AsyncMock(return_value="card_1")
    adapter._stream_card_content = AsyncMock(return_value=True)
    adapter._set_stream_card_mode = AsyncMock(return_value=True)
    adapter._apply_native_tables = AsyncMock()

    result = asyncio.run(adapter.send("oc_group", "#25 已标记完成。", reply_to=_CARD_TOKEN))

    assert result.success and result.message_id == "card:card_1"
    assert replies == []
    assert [(c.receive_id_type, c.request_body.receive_id, c.request_body.msg_type) for c in creates] == [
        ("chat_id", "oc_group", "interactive"),
    ]


def test_inline_plugin_card_click_reply_is_posted_to_the_chat(feishu, monkeypatch):
    """A plugin card click dispatched inline (busy chat or not) still delivers its reply, posted
    to the chat rather than replying to the callback token."""
    from hermes_cli import plugins as plugins_mod

    adapter, replies, creates = feishu
    monkeypatch.setattr(plugins_mod, "get_plugin_command_handler", lambda name: object() if name == "card" else None)
    adapter._allowed_group_users = frozenset({"ou_user1"})
    adapter.set_message_handler(AsyncMock(return_value="#25 已标记完成。"))
    data = SimpleNamespace(event=SimpleNamespace(
        token=_CARD_TOKEN, context=SimpleNamespace(open_chat_id="oc_group"),
        operator=SimpleNamespace(open_id="ou_user1"), action=SimpleNamespace(tag="button", value={"wa": "done"}),
    ))
    with patch.object(adapter, "_resolve_sender_profile", AsyncMock(
            return_value={"user_id": "ou_user1", "user_name": "Dave", "user_id_alt": None})), \
            patch.object(adapter, "get_chat_info", AsyncMock(return_value={"name": "Group"})):
        asyncio.run(adapter._handle_card_action_event(data))

    event = adapter._message_handler.await_args.args[0]
    assert event.message_id == _CARD_TOKEN and event._hermes_plugin_command_inline is True
    assert replies == []
    assert [(c.receive_id_type, c.request_body.receive_id) for c in creates] == [("chat_id", "oc_group")]
