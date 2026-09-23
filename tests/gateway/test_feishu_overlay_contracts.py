"""Owner Feishu overlay contracts that contradict upstream defaults.

Upgrade gate (rebase, cherry-pick, or adapter rewrite port):
    scripts/run_tests.sh tests/gateway/test_feishu_overlay_contracts.py -q --tb=short

Missing file or any fail = overlay not done. Do not report 齐.
Whole ``test_feishu.py`` green is not enough (v0.21.1 quiet port, 2026-09-08).
Covers: @_all is not a mention; group auto_thread; DM flatten; quote body ignores
root_id; in-topic follow-up only for our session + original sender; CardKit
send path (v0.21.2/v0.21.3 dropped ``_send_stream_card`` while config stayed on);
plain send must call a real ``_send_post_or_text`` (v0.21.4 overlay called it
without defining it, so the hook's plain relay failed and it DMed);
one CardKit card across tool boundaries (v0.21.4 kept the adapter methods but
dropped the consumer hook, so each tool round opened a new card);
yaml ``extra.stream_card`` must still enable cards after upgrade (env bridge +
truthy parse + CardKit ``<at id>`` rewrite so true-@ does not fall back to post);
a CardKit outage still delivers the answer head (a dropped preview must not
report success); ``om_`` thread target replies in-thread; ``hermes feishu send`` (plugin CLI,
core ``hermes send`` stays upstream) is metadata ``plain`` on the adapter and a
``card:`` id is a failure; confirm-relay ``<at user_id>`` becomes a native
post at element so the user is pinged (hook relay topics, 2026-09-18).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace
from unittest.mock import patch

from tests.gateway.feishu_helpers import make_sender


def _admits_group(adapter, message, sender_id, chat_id=""):
    sender = make_sender(open_id=sender_id.open_id, user_id=sender_id.user_id)
    if not hasattr(message, "chat_type"):
        message.chat_type = "group"
    if chat_id:
        message.chat_id = chat_id
    return adapter._admit(sender, message) is None


def _overlay_adapter():
    from gateway.config import PlatformConfig
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter(PlatformConfig())
    adapter._bot_open_id = "ou_bot"
    adapter._bot_user_id = ""
    adapter._bot_name = "Test Bot"
    return adapter


def _all_mention():
    return SimpleNamespace(
        key="@_all",
        id=SimpleNamespace(open_id="", user_id=""),
        name="所有人",
    )


def _bot_mention():
    return SimpleNamespace(
        key="@_user_1",
        id=SimpleNamespace(open_id="ou_bot", user_id=""),
        name="Test Bot",
    )


def _text_message(*, text: str, mentions):
    return SimpleNamespace(
        content=json.dumps({"text": text}),
        message_type="text",
        mentions=mentions,
    )


def _thread_message(*, message_id, thread_id=None, root_id=None, parent_id=None, upper_message_id=None):
    return SimpleNamespace(
        message_id=message_id,
        thread_id=thread_id,
        root_id=root_id,
        parent_id=parent_id,
        upper_message_id=upper_message_id,
    )


def test_pure_at_all_in_text_is_not_self_mention():
    """SDK often omits @_all from mentions[]; the text literal must not admit."""
    message = _text_message(text="@_all attention", mentions=[])
    assert _overlay_adapter()._mentions_self(message) is False


def test_pure_at_all_mention_object_is_not_self_mention():
    message = _text_message(text="@_all attention", mentions=[_all_mention()])
    assert _overlay_adapter()._mentions_self(message) is False


def test_at_all_with_bot_identity_is_not_self_mention():
    mention = _all_mention()
    mention.id.open_id = "ou_bot"
    mention.name = "Test Bot"
    assert _overlay_adapter()._message_mentions_bot([mention]) is False


def test_at_all_plus_real_bot_mention_is_self_mention():
    message = _text_message(
        text="@_all @_user_1 attention",
        mentions=[_all_mention(), _bot_mention()],
    )
    assert _overlay_adapter()._mentions_self(message) is True


@patch.dict(os.environ, {"FEISHU_GROUP_POLICY": "allowlist", "FEISHU_ALLOWED_USERS": "ou_allowed"}, clear=True)
def test_at_all_still_requires_policy_gate():
    """Allowlist still applies when the bot is actually @mentioned."""
    adapter = _overlay_adapter()
    message = _text_message(
        text="@_all @_user_1 attention",
        mentions=[_all_mention(), _bot_mention()],
    )
    blocked = SimpleNamespace(open_id="ou_blocked", user_id=None)
    allowed = SimpleNamespace(open_id="ou_allowed", user_id=None)
    assert _admits_group(adapter, message, blocked, "") is False
    assert _admits_group(adapter, message, allowed, "") is True


@patch.dict(os.environ, {"FEISHU_GROUP_POLICY": "allowlist", "FEISHU_ALLOWED_USERS": "ou_allowed"}, clear=True)
def test_pure_at_all_does_not_bypass_require_mention():
    """Allowlisted sender + @_all only must still be rejected."""
    adapter = _overlay_adapter()
    message = _text_message(text="@_all attention", mentions=[_all_mention()])
    allowed = SimpleNamespace(open_id="ou_allowed", user_id=None)
    assert _admits_group(adapter, message, allowed, "") is False


def test_group_first_message_synthesizes_thread_from_message_id():
    """Group @ with no native topic must reply_in_thread under the trigger om_."""
    adapter = _overlay_adapter()
    message = _thread_message(message_id="om_trigger")
    assert adapter._resolve_inbound_thread_id(message, chat_type="group", message_id="om_trigger") == "om_trigger"


def test_group_prefers_root_id_over_thread_id():
    """Follow-up omt_ must not split the om_ auto-thread session."""
    adapter = _overlay_adapter()
    message = _thread_message(message_id="om_follow", thread_id="omt_later", root_id="om_trigger")
    assert adapter._resolve_inbound_thread_id(
        message, chat_type="group", message_id="om_follow",
    ) == "om_trigger"


def test_group_keeps_native_thread_when_no_root_id():
    adapter = _overlay_adapter()
    message = _thread_message(message_id="om_follow", thread_id="omt_native")
    assert adapter._resolve_inbound_thread_id(
        message, chat_type="group", message_id="om_follow",
    ) == "omt_native"


def test_auto_thread_off_does_not_synthesize():
    adapter = _overlay_adapter()
    adapter._auto_thread_enabled = False
    message = _thread_message(message_id="om_trigger")
    assert adapter._resolve_inbound_thread_id(message, chat_type="group", message_id="om_trigger") is None


def test_dm_quote_never_carries_thread_id():
    """p2p flatten: quoted cards / omt_ must stay on the main DM session."""
    adapter = _overlay_adapter()
    message = _thread_message(message_id="om_dm", thread_id="omt_quote", root_id="om_card")
    assert adapter._resolve_inbound_thread_id(message, chat_type="p2p", message_id="om_dm") is None


def test_reply_to_ignores_root_id():
    """Quote body comes from parent/upper only; root_id is the topic root, not the quoted card."""
    adapter = _overlay_adapter()
    message = _thread_message(
        message_id="om_follow",
        root_id="om_root",
        parent_id="om_parent",
        upper_message_id="om_upper",
    )
    assert adapter._resolve_inbound_reply_to(message) == "om_parent"
    message_root_only = _thread_message(message_id="om_follow", root_id="om_root")
    assert adapter._resolve_inbound_reply_to(message_root_only) is None


def _followup_adapter(*, owner="ou_owner", thread="om_ours"):
    adapter = _overlay_adapter()
    adapter._require_mention = True
    adapter._group_policy = "open"
    adapter._default_group_policy = "open"
    adapter._thread_followup_without_mention = True
    adapter._session_store = SimpleNamespace(
        lookup_by_session_key=lambda key: (
            SimpleNamespace(origin=SimpleNamespace(user_id=owner, user_id_alt=None))
            if thread in key
            else None
        ),
    )
    return adapter


def _topic_message(*, thread_id="om_ours", root_id="om_ours"):
    message = _text_message(text="continue without at", mentions=[])
    message.chat_type = "group"
    message.chat_id = "oc_group"
    message.thread_id = thread_id
    message.root_id = root_id
    return message


def test_our_topic_followup_without_mention_is_admitted():
    adapter = _followup_adapter()
    owner = SimpleNamespace(open_id="ou_owner", user_id=None)
    assert _admits_group(adapter, _topic_message(), owner, "oc_group") is True


def test_stranger_in_our_topic_without_mention_is_rejected():
    adapter = _followup_adapter()
    stranger = SimpleNamespace(open_id="ou_stranger", user_id=None)
    assert _admits_group(adapter, _topic_message(), stranger, "oc_group") is False


def test_someone_elses_topic_without_mention_is_rejected():
    adapter = _followup_adapter()
    owner = SimpleNamespace(open_id="ou_owner", user_id=None)
    message = _topic_message(thread_id="omt_joe", root_id="om_joe")
    assert _admits_group(adapter, message, owner, "oc_group") is False


def test_main_timeline_without_mention_is_rejected():
    adapter = _followup_adapter()
    owner = SimpleNamespace(open_id="ou_owner", user_id=None)
    assert _admits_group(adapter, _text_message(text="hello", mentions=[]), owner, "oc_group") is False


def test_stream_card_send_path_survives_upgrade():
    """A normal send must enter CardKit. A leftover method name is not enough.

    v0.21.2/v0.21.3 kept ``extra.stream_card`` and the callable names while
    the send body was gone. The upgrade gate stays green on that tree.
    """
    import asyncio

    from gateway.platforms.base import SendResult
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = object.__new__(FeishuAdapter)
    adapter._client = SimpleNamespace(cardkit=object())
    adapter._stream_card_enabled = True
    adapter._stream_card_in_thread = True
    adapter._group_rules = {}
    seen = {}

    async def _card(**kwargs):
        seen.update(kwargs)
        return SendResult(success=True, message_id="card:kept")

    with patch.object(adapter, "_send_stream_card", _card):
        result = asyncio.run(adapter.send("oc_group", "hello"))
    assert result.success is True
    assert result.message_id == "card:kept"
    assert seen.get("chat_id") == "oc_group"
    assert seen.get("finalize") is True


def test_stream_card_settings_round_trip():
    from plugins.platforms.feishu.adapter import FeishuAdapter

    settings = FeishuAdapter._load_settings({
        "stream_card": True,
        "stream_card_in_thread": True,
        "group_rules": {"oc_off": {"policy": "open", "stream_card": False}},
    })
    assert settings.stream_card_enabled is True
    assert settings.stream_card_in_thread is True
    assert settings.group_rules["oc_off"].stream_card is False


def test_stream_is_message_follows_plain_and_thread():
    """The public one-card answer is the stream-card answer, not the chat switch."""
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = object.__new__(FeishuAdapter)
    adapter._client = SimpleNamespace(cardkit=object())
    adapter._stream_card_enabled = True
    adapter._stream_card_in_thread = False
    adapter._group_rules = {}
    assert adapter.stream_is_message_for_chat("oc_group") is True
    assert adapter.stream_is_message_for_chat("oc_group", {"plain": True}) is False
    assert adapter.stream_is_message_for_chat("oc_group", {"thread_id": "om_root"}) is False


def test_stream_card_in_thread_opt_in_uses_card():
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = object.__new__(FeishuAdapter)
    adapter._stream_card_enabled = True
    adapter._stream_card_in_thread = True
    adapter._group_rules = {}
    adapter._client = SimpleNamespace(cardkit=object())
    assert adapter._uses_stream_card("oc_group", {"thread_id": "om_root"}) is True


def test_to_boolean_accepts_yaml_and_env_truthy():
    """yaml_env_setter writes str(True)=='True'; extra loss must not disable cards."""
    from plugins.platforms.feishu.adapter import _to_boolean

    for value in (True, 1, "true", "True", "TRUE", "1", "yes"):
        assert _to_boolean(value) is True, value
    for value in (False, 0, "false", "False", "", None, "no"):
        assert _to_boolean(value) is False, value


@patch.dict(os.environ, {}, clear=False)
def test_apply_yaml_config_seeds_nested_extra_stream_card():
    """Upgrade must keep extra.stream_card even when the key lives under extra:."""
    from plugins.platforms.feishu.adapter import FeishuAdapter, _apply_yaml_config

    os.environ.pop("FEISHU_STREAM_CARD", None)
    os.environ.pop("FEISHU_STREAM_CARD_IN_THREAD", None)
    seeded = _apply_yaml_config({}, {
        "allow_bots": "all",
        "extra": {"stream_card": True, "stream_card_in_thread": True},
    })
    assert seeded is not None
    assert seeded.get("stream_card") is True
    assert seeded.get("stream_card_in_thread") is True
    assert os.environ.get("FEISHU_STREAM_CARD", "").lower() == "true"
    assert os.environ.get("FEISHU_STREAM_CARD_IN_THREAD", "").lower() == "true"


@patch.dict(os.environ, {"FEISHU_STREAM_CARD": "True", "FEISHU_STREAM_CARD_IN_THREAD": "True"}, clear=False)
def test_missing_extra_stream_card_still_honors_env_bridge():
    """If extra is dropped on upgrade, FEISHU_STREAM_CARD from yaml bridge still enables cards."""
    from plugins.platforms.feishu.adapter import FeishuAdapter

    settings = FeishuAdapter._load_settings({})
    assert settings.stream_card_enabled is True
    assert settings.stream_card_in_thread is True


def test_yaml_platform_config_enables_topic_stream_card():
    """Gateway PlatformConfig extra must turn on in-topic CardKit, not just parse settings."""
    from gateway.config import PlatformConfig
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = FeishuAdapter(PlatformConfig.from_dict({
        "enabled": True,
        "extra": {"stream_card": True, "stream_card_in_thread": True},
    }))
    adapter._client = SimpleNamespace(cardkit=object())
    assert adapter._uses_stream_card("oc_group", {"thread_id": "om_root", "reply_in_thread": True}) is True


def test_stream_card_rewrites_user_id_at_tags_for_cardkit():
    """True-@ in a stream card must use CardKit ``<at id>``, not post ``user_id``."""
    from plugins.platforms.feishu.adapter import FeishuAdapter

    out = FeishuAdapter._prepare_stream_card_content(
        '<at user_id="ou_test_owner">Owner</at> 发测试',
    )
    assert '<at id="ou_test_owner"></at>' in out
    assert "user_id=" not in out
    assert "发测试" in out


def test_plain_post_promotes_at_user_id_to_native_element():
    """Confirm-relay ``<at user_id>`` must become a native post at tag so Feishu pings."""
    from plugins.platforms.feishu.adapter import _build_markdown_post_rows

    rows = _build_markdown_post_rows(
        '<at user_id="ou_test_approver">Owner</at>\n**🟠 需要你确认**',
    )
    ats = [el for row in rows for el in row if el.get("tag") == "at"]
    assert ats and ats[0].get("user_id") == "ou_test_approver"
    assert any(
        el.get("tag") == "md" and "需要你确认" in el.get("text", "")
        for row in rows for el in row
    )


def test_om_thread_target_replies_in_thread():
    """``hermes send --to feishu:<chat>:<om_root>`` must reply under om_root with reply_in_thread.

    Upstream sends thread targets with message.create(receive_id_type=thread_id), which Feishu
    rejects for ``om_`` ids (99992402: only ``omt_`` topic ids). The attention-notify hook keeps
    one topic per CLI session by replying to the root message id, so this path is an owner contract.
    """
    import asyncio

    from gateway.config import PlatformConfig
    from plugins.platforms.feishu.adapter import FeishuAdapter

    replies, creates = [], []

    async def _reply(request):
        replies.append(request)
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_reply"))

    async def _create(request):
        creates.append(request)
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_new"))

    adapter = FeishuAdapter(PlatformConfig())
    adapter._client = SimpleNamespace(
        im=SimpleNamespace(v1=SimpleNamespace(message=SimpleNamespace(reply=_reply, create=_create)))
    )

    async def _run_blocking(fn, *args, **kwargs):
        return await fn(*args, **kwargs)

    with patch.object(adapter, "_run_blocking", _run_blocking), patch.object(
        FeishuAdapter, "_build_reply_message_body",
        staticmethod(lambda **kwargs: SimpleNamespace(**kwargs)),
    ), patch.object(
        FeishuAdapter, "_build_reply_message_request",
        staticmethod(lambda message_id, body: SimpleNamespace(message_id=message_id, request_body=body)),
    ), patch.object(
        FeishuAdapter, "_build_create_message_body",
        staticmethod(lambda **kwargs: SimpleNamespace(**kwargs)),
    ), patch.object(
        FeishuAdapter, "_build_create_message_request",
        staticmethod(lambda receive_id_type, request_body: SimpleNamespace(
            receive_id_type=receive_id_type, request_body=request_body)),
    ):
        asyncio.run(adapter._send_raw_message(
            chat_id="oc_group", msg_type="text", payload='{"text":"hi"}',
            reply_to=None, metadata={"thread_id": "om_root"},
        ))
        # An omt_ topic id keeps upstream's create(receive_id_type=thread_id) path.
        asyncio.run(adapter._send_raw_message(
            chat_id="oc_group", msg_type="text", payload='{"text":"hi"}',
            reply_to=None, metadata={"thread_id": "omt_topic"},
        ))

    assert len(replies) == 1 and replies[0].message_id == "om_root"
    assert replies[0].request_body.reply_in_thread is True
    assert len(creates) == 1 and creates[0].receive_id_type == "thread_id"
    assert creates[0].request_body.receive_id == "omt_topic"


def test_plain_metadata_skips_stream_card():
    """``plain`` is the generic flag; Feishu maps it to classic (no CardKit)."""
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = object.__new__(FeishuAdapter)
    adapter._stream_card_enabled = True
    adapter._stream_card_in_thread = True
    adapter._group_rules = {}
    adapter._client = SimpleNamespace(cardkit=object())
    assert adapter._uses_stream_card("oc_group", {"plain": True}) is False
    assert adapter._uses_stream_card("oc_group", {"plain": True, "thread_id": "om_root"}) is False
    assert adapter._uses_stream_card("oc_group", {"force_classic_message": True}) is False


def test_standalone_send_plain_forces_classic_message():
    """``hermes feishu send`` reaches the adapter as metadata ``plain`` via ``_standalone_send``.

    Owner contract: the attention-notify hook relays through ``hermes feishu send`` so the group
    keeps CardKit for the bot's own replies while hook posts stay classic and return ``om_`` ids.
    Core ``hermes send`` / ``send_message`` stay upstream; the plugin owns ``plain``.
    """
    import asyncio

    from plugins.platforms.feishu import adapter as feishu_adapter

    seen = {}

    class _Transient:
        def __init__(self, pconfig):
            pass

        def _build_lark_client(self, domain):
            return object()

        async def send(self, chat_id, message, metadata=None):
            seen["metadata"] = metadata
            return SimpleNamespace(success=True, message_id="om_plain_standalone")

    with patch.object(feishu_adapter, "FeishuAdapter", _Transient), \
            patch.object(feishu_adapter, "_load_lark_oapi", lambda: True), \
            patch.object(feishu_adapter, "_sdk_domain", lambda name: "feishu"):
        out = asyncio.run(feishu_adapter._standalone_send(SimpleNamespace(), "oc_group", "hi",
                                                          thread_id="om_root", plain=True))
    assert out["message_id"] == "om_plain_standalone"
    assert seen["metadata"] == {"thread_id": "om_root", "plain": True}


def test_plain_send_rejects_stream_card_id():
    """A classic/plain send that still returns ``card:`` is a failure, not a topic root."""
    import asyncio

    from gateway.platforms.base import SendResult
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = object.__new__(FeishuAdapter)
    adapter._client = object()

    async def _card(*_a, **_k):
        return SendResult(success=True, message_id="card:abc")

    with patch.object(adapter, "format_message", lambda c: c), \
            patch.object(adapter, "_uses_stream_card", lambda *a, **k: True), \
            patch.object(adapter, "_send_stream_card", _card):
        out = asyncio.run(adapter.send("oc_group", "hi", metadata={"plain": True}))
    assert out.success is False
    assert out.message_id != "card:abc"


def test_feishu_send_cli_posts_classic_threadable():
    """``hermes feishu send --to feishu:<chat>:<om_root> --json BODY`` (the hook's exact call).

    Registered by the plugin, always ``plain``, thread id parsed from the target, JSON out.
    """
    import argparse
    import asyncio
    import contextlib
    import io

    from gateway.config import Platform
    from plugins.platforms.feishu import adapter as feishu_adapter
    from plugins.platforms.feishu import cli as feishu_cli

    registered = {}
    ctx = SimpleNamespace(register_platform=lambda **kw: None,
                          register_cli_command=lambda **kw: registered.update(kw))
    feishu_adapter.register(ctx)
    assert registered["name"] == "feishu" and registered["handler_fn"] is feishu_cli.dispatch

    parser = argparse.ArgumentParser()
    registered["setup_fn"](parser)
    calls = []

    async def _standalone(pconfig, chat_id, message, *, thread_id=None, plain=False, **_kw):
        calls.append((chat_id, message, thread_id, plain))
        return {"success": True, "platform": "feishu", "chat_id": chat_id, "message_id": "om_reply"}

    pconfig = SimpleNamespace(enabled=True)
    config = SimpleNamespace(platforms={Platform("feishu"): pconfig}, get_home_channel=lambda p: None)
    mirrors = []
    out = io.StringIO()
    with patch("hermes_cli.send_cmd._load_hermes_env", lambda: None), \
            patch("gateway.config.load_gateway_config", lambda: config), \
            patch.object(feishu_adapter, "_standalone_send", _standalone), \
            patch("gateway.mirror.mirror_to_session",
                  lambda *a, **kw: mirrors.append((a, kw)) or True), \
            contextlib.redirect_stdout(out):
        args = parser.parse_args(["send", "--to", "feishu:oc_group:om_root", "--json", "hello"])
        rc = args.func(args)
    assert rc == 0
    assert calls == [("oc_group", "hello", "om_root", True)]
    payload = json.loads(out.getvalue())
    assert payload["message_id"] == "om_reply" and payload["mirrored"] is True
    # mirrored into the topic session so the bot sees the relayed text when the user answers there
    assert mirrors == [(("feishu", "oc_group", "hello"), {"thread_id": "om_root"})]

    with patch("hermes_cli.send_cmd._load_hermes_env", lambda: None), \
            contextlib.redirect_stdout(io.StringIO()) as bad:
        args = parser.parse_args(["send", "--to", "telegram:1", "--json", "hello"])
        assert args.func(args) == 1
    assert "feishu" in json.loads(bad.getvalue())["error"]


def test_plain_send_reaches_classic_post():
    """``--plain`` must send a classic message and return ``om_``, not raise.

    The v0.21.4 overlay's ``send()`` called ``_send_post_or_text`` without
    defining it. Attention relay then failed closed and posted a private card.
    """
    import asyncio

    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = object.__new__(FeishuAdapter)
    adapter._client = object()
    adapter._stream_card_enabled = True
    adapter._stream_card_in_thread = True
    adapter._group_rules = {}
    seen = {}

    async def _retry(*, chat_id, msg_type, payload, reply_to, metadata):
        seen["chat_id"] = chat_id
        seen["msg_type"] = msg_type
        seen["reply_to"] = reply_to
        seen["metadata"] = metadata
        return SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_plain_ok"))

    with patch.object(adapter, "_feishu_send_with_retry", _retry):
        result = asyncio.run(adapter.send(
            "oc_group",
            "hello",
            reply_to="om_root",
            metadata={"plain": True, "thread_id": "om_root"},
        ))
    assert result.success is True
    assert result.message_id == "om_plain_ok"
    assert seen["chat_id"] == "oc_group"
    assert seen["reply_to"] == "om_root"
    assert seen["metadata"]["thread_id"] == "om_root"
    assert seen["metadata"]["plain"] is True


def test_tool_boundary_keeps_one_stream_card():
    """A tool round must edit the open CardKit card, not send another one.

    v0.21.4 kept ``_send_stream_card`` and ``stream_is_message_for_chat`` but
    dropped the consumer hook. Each tool boundary then opened a new card.
    """
    import asyncio

    from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig

    sends = []
    edits = []

    async def _send(**kwargs):
        sends.append(kwargs)
        return SimpleNamespace(success=True, message_id="card:1")

    async def _edit(**kwargs):
        edits.append(kwargs)
        return SimpleNamespace(success=True, message_id=kwargs.get("message_id"))

    class _CardAdapter:
        MAX_MESSAGE_LENGTH = 4096
        REQUIRES_EDIT_FINALIZE = True

        def __init__(self):
            self.send = _send
            self.edit_message = _edit

        def stream_is_message_for_chat(self, chat_id, metadata=None):
            return not bool((metadata or {}).get("plain"))

    adapter = _CardAdapter()
    consumer = GatewayStreamConsumer(
        adapter,
        "oc_group",
        StreamConsumerConfig(edit_interval=0.01, buffer_threshold=5, cursor=""),
    )

    async def _run():
        consumer.on_delta("先核对 /new 能不能定制。")
        consumer.on_delta(None)
        consumer.on_delta("结论：可以看本地 tip。")
        consumer.finish()
        await consumer.run()

    asyncio.run(_run())

    assert len(sends) == 1, sends
    assert (sends[0].get("metadata") or {}).get("expect_edits") is True
    assert edits, "the next round must edit the open card"
    assert all(call.get("message_id") == "card:1" for call in edits)
    bodies = sends[0].get("content", "") + "".join(call.get("content", "") for call in edits)
    assert "先核对" in bodies
    assert "结论" in bodies
    assert any(call.get("finalize") is True for call in edits)


def test_card_outage_still_delivers_the_answer_head():
    """CardKit create failing must not cost the answer its head.

    The overlay used to drop the ``expect_edits`` preview as success/None. The
    consumer then counted that text as shown and sent only the tail.
    """
    import asyncio

    from gateway.platforms.base import SendResult
    from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig
    from plugins.platforms.feishu.adapter import FeishuAdapter

    adapter = object.__new__(FeishuAdapter)
    adapter._client = SimpleNamespace(cardkit=object())
    adapter._stream_card_enabled = True
    adapter._stream_card_in_thread = True
    adapter._group_rules = {}
    visible = {}

    async def _no_card(**_k):
        return None

    async def _post(*, chat_id, content, reply_to, metadata):
        mid = f"om_{len(visible) + 1}"
        visible[mid] = content
        return SendResult(success=True, message_id=mid)

    async def _edit(chat_id, message_id, content, *, finalize=False):
        visible[message_id] = content
        return SendResult(success=True, message_id=message_id)

    consumer = GatewayStreamConsumer(
        adapter, "oc_group", StreamConsumerConfig(edit_interval=0.01, buffer_threshold=5, cursor=""))

    async def _run():
        task = asyncio.create_task(consumer.run())
        consumer.on_delta("答案开头：先说结论。")
        await asyncio.sleep(0.1)  # let the preview tick fire before the rest arrives
        consumer.on_delta("后半段：细节在这里。")
        consumer.finish()
        await task

    with patch.object(adapter, "format_message", lambda c: c), \
            patch.object(adapter, "_send_stream_card", _no_card), \
            patch.object(adapter, "_send_post_or_text", _post), \
            patch.object(adapter, "edit_message", _edit):
        asyncio.run(_run())

    shown = "".join(visible.values())
    assert "答案开头" in shown, visible
    assert "后半段" in shown, visible


def test_plain_metadata_opens_a_second_message_at_tool_boundary():
    """``plain`` is not one card. The consumer must ask the public method."""
    import asyncio

    from gateway.stream_consumer import GatewayStreamConsumer, StreamConsumerConfig

    sends = []

    async def _send(**kwargs):
        sends.append(kwargs)
        return SimpleNamespace(success=True, message_id=f"om_{len(sends)}")

    async def _edit(**kwargs):
        return SimpleNamespace(success=True, message_id=kwargs.get("message_id"))

    class _PlainAdapter:
        MAX_MESSAGE_LENGTH = 4096
        REQUIRES_EDIT_FINALIZE = False

        def __init__(self):
            self.send = _send
            self.edit_message = _edit

        def stream_is_message_for_chat(self, chat_id, metadata=None):
            return not bool((metadata or {}).get("plain"))

    consumer = GatewayStreamConsumer(
        _PlainAdapter(),
        "oc_group",
        StreamConsumerConfig(edit_interval=0.01, buffer_threshold=5, cursor=""),
        metadata={"plain": True},
    )

    async def _run():
        consumer.on_delta("先核对这一版。")
        consumer.on_delta(None)
        consumer.on_delta("结论在这里。")
        consumer.finish()
        await consumer.run()

    asyncio.run(_run())
    assert len(sends) == 2, sends
