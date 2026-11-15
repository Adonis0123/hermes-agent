"""A reply target that is not a Feishu message id never reaches ``message.reply``.

A card button click is routed as a synthetic event whose ``message_id`` is the callback
token (``c-…``, kept for dedup), and the gateway hands that id back as ``reply_to``.
Feishu's reply API rejects it (99992354 "not a valid {open_message_id}"), so the
response to every click was lost, on the post path and on the plain-text fallback.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

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


def test_card_click_response_is_posted_to_the_chat(feishu):
    adapter, replies, creates = feishu

    result = asyncio.run(adapter.send("oc_group", "#25 marked done.", reply_to=_CARD_TOKEN))

    assert result.success and result.message_id == "om_new"
    assert replies == []
    assert [(c.receive_id_type, c.request_body.receive_id) for c in creates] == [("chat_id", "oc_group")]


def test_card_click_in_a_topic_still_replies_to_the_topic_message(feishu):
    adapter, replies, creates = feishu

    # A real message id in the metadata is still the anchor: the click token only drops itself.
    result = asyncio.run(adapter.send(
        "oc_group", "#26 marked done.", reply_to=_CARD_TOKEN,
        metadata={"thread_id": "omt_topic", "reply_to_message_id": "om_root"},
    ))

    assert result.success and result.message_id == "om_reply"
    assert [r.message_id for r in replies] == ["om_root"]
    assert creates == []
