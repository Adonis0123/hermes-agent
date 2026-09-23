"""CardKit stream-card contracts for the Feishu overlay.

Upgrade gate companion: tests/gateway/test_feishu_overlay_contracts.py
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

class TestStreamCardPerGroup(unittest.TestCase):
    """CardKit streaming is on or off per chat; a missing group key inherits the global switch."""

    def _adapter(self, *, enabled: bool, rules=None):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = object.__new__(FeishuAdapter)
        adapter._stream_card_enabled = enabled
        adapter._stream_card_in_thread = False
        adapter._group_rules = rules or {}
        adapter._client = SimpleNamespace(cardkit=object())
        return adapter

    def test_load_settings_parses_per_chat_stream_card(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        settings = FeishuAdapter._load_settings(
            {
                "stream_card": True,
                "stream_card_in_thread": False,
                "group_rules": {
                    "oc_on": {"policy": "open", "stream_card": True},
                    "oc_off": {"policy": "open", "stream_card": False},
                    "oc_inherit": {"policy": "open"},
                    "oc_thread": {
                        "policy": "open",
                        "stream_card": True,
                        "stream_card_in_thread": True,
                    },
                },
            }
        )
        self.assertTrue(settings.stream_card_enabled)
        self.assertFalse(settings.stream_card_in_thread)
        self.assertTrue(settings.group_rules["oc_on"].stream_card)
        self.assertFalse(settings.group_rules["oc_off"].stream_card)
        self.assertIsNone(settings.group_rules["oc_inherit"].stream_card)
        self.assertTrue(settings.group_rules["oc_thread"].stream_card_in_thread)

    def test_missing_group_key_inherits_global(self):
        from plugins.platforms.feishu.adapter import FeishuGroupRule

        adapter = self._adapter(
            enabled=True,
            rules={"oc_off": FeishuGroupRule(policy="open", stream_card=False)},
        )
        self.assertTrue(adapter._uses_stream_card("oc_other"))
        self.assertFalse(adapter._uses_stream_card("oc_off"))

    def test_per_chat_true_overrides_global_false(self):
        from plugins.platforms.feishu.adapter import FeishuGroupRule

        adapter = self._adapter(
            enabled=False,
            rules={"oc_on": FeishuGroupRule(policy="open", stream_card=True)},
        )
        self.assertTrue(adapter._uses_stream_card("oc_on"))
        self.assertFalse(adapter._uses_stream_card("oc_other"))

    def test_topic_still_bypasses_even_when_chat_enables_card(self):
        from plugins.platforms.feishu.adapter import FeishuGroupRule

        adapter = self._adapter(
            enabled=True,
            rules={"oc_on": FeishuGroupRule(policy="open", stream_card=True)},
        )
        self.assertFalse(adapter._uses_stream_card("oc_on", {"thread_id": "om_root"}))
        self.assertFalse(adapter._uses_stream_card("oc_on", {"reply_in_thread": True}))

    def test_topic_uses_card_when_in_thread_opt_in(self):
        from plugins.platforms.feishu.adapter import FeishuGroupRule

        adapter = self._adapter(
            enabled=True,
            rules={
                "oc_on": FeishuGroupRule(
                    policy="open", stream_card=True, stream_card_in_thread=True,
                )
            },
        )
        self.assertTrue(adapter._uses_stream_card("oc_on", {"thread_id": "om_root"}))

    def test_stream_is_message_matches_send_decision(self):
        """The consumer keeps one card open across tools exactly when send makes one."""
        adapter = self._adapter(enabled=True)
        for meta in (None, {"thread_id": "om_root"}, {"plain": True}):
            self.assertEqual(
                adapter.stream_is_message_for_chat("oc_on", meta),
                adapter._uses_stream_card("oc_on", meta),
            )
        self.assertTrue(adapter.stream_is_message_for_chat("oc_on"))
        self.assertFalse(adapter.stream_is_message_for_chat("oc_on", {"thread_id": "om_root"}))


class TestStreamCardThreadSend(unittest.TestCase):
    """CardKit interactive cannot use receive_id_type=thread_id (Feishu 99992402)."""

    def test_card_reply_anchor_uses_om_thread_root_when_reply_to_missing(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        anchor = FeishuAdapter._card_reply_anchor(
            None, {"thread_id": "om_root_msg", "reply_in_thread": True},
        )
        self.assertEqual(anchor, "om_root_msg")

    def test_card_reply_anchor_prefers_explicit_reply_to(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        anchor = FeishuAdapter._card_reply_anchor("om_user_msg", {"thread_id": "om_root_msg"})
        self.assertEqual(anchor, "om_user_msg")

    def test_card_reply_anchor_skips_omt_topic_ids(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        self.assertIsNone(FeishuAdapter._card_reply_anchor(None, {"thread_id": "omt_topic"}))

    def test_stream_card_id_from_message_id_works_as_instance_call(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = object.__new__(FeishuAdapter)
        self.assertEqual(adapter._stream_card_id_from_message_id("card:abc123"), "abc123")
        self.assertIsNone(adapter._stream_card_id_from_message_id("om_not_a_card"))


class TestStreamCardSendDispatch(unittest.IsolatedAsyncioTestCase):
    """Final send takes CardKit when enabled; a preview never reports success without an id."""

    def _adapter(self, *, enabled: bool):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = object.__new__(FeishuAdapter)
        adapter._client = SimpleNamespace(cardkit=object())
        adapter._stream_card_enabled = enabled
        adapter._stream_card_in_thread = True
        adapter._group_rules = {}
        adapter.format_message = lambda content: content
        adapter.truncate_message = lambda content, limit: [content]
        adapter._send_stream_card = AsyncMock(
            return_value=SimpleNamespace(success=True, message_id="card:abc"),
        )
        adapter._send_post_or_text = AsyncMock(
            return_value=SimpleNamespace(success=True, message_id="om_post"),
        )
        adapter.MAX_MESSAGE_LENGTH = 8000
        return adapter

    async def test_enabled_final_send_uses_stream_card(self):
        adapter = self._adapter(enabled=True)
        result = await adapter.send("oc_chat", "**结论**")
        self.assertEqual(result.message_id, "card:abc")
        adapter._send_stream_card.assert_awaited()
        adapter._send_post_or_text.assert_not_called()

    async def test_enabled_expect_edits_still_creates_card(self):
        adapter = self._adapter(enabled=True)
        result = await adapter.send(
            "oc_chat", "预览", metadata={"expect_edits": True, "thread_id": "om_root"},
        )
        self.assertEqual(result.message_id, "card:abc")
        kwargs = adapter._send_stream_card.await_args.kwargs
        self.assertFalse(kwargs["finalize"])

    async def test_interim_send_still_dropped_when_cards_on(self):
        adapter = self._adapter(enabled=True)
        result = await adapter.send(
            "oc_chat", "先读", metadata={"_interim_send": True},
        )
        self.assertTrue(result.success)
        self.assertIsNone(result.message_id)
        adapter._send_stream_card.assert_not_called()

    async def test_card_failure_posts_preview_for_real(self):
        """Card create failing must not drop the preview as success/None: the consumer would
        count its text as shown and the final would lose its head."""
        adapter = self._adapter(enabled=True)
        adapter._send_stream_card = AsyncMock(return_value=None)
        result = await adapter.send("oc_chat", "答案开头", metadata={"expect_edits": True})
        self.assertEqual(result.message_id, "om_post")
        adapter._send_post_or_text.assert_awaited()

    async def test_classic_preview_is_posted_not_dropped(self):
        adapter = self._adapter(enabled=False)
        result = await adapter.send("oc_chat", "预览", metadata={"expect_edits": True})
        self.assertEqual(result.message_id, "om_post")

    async def test_disabled_falls_back_to_post(self):
        adapter = self._adapter(enabled=False)
        result = await adapter.send("oc_chat", "**结论**")
        self.assertEqual(result.message_id, "om_post")
        adapter._send_stream_card.assert_not_called()
        adapter._send_post_or_text.assert_awaited()


class TestStreamCardSdkLoad(unittest.TestCase):
    """Stream cards need CardKit request types bound at SDK load, not later."""

    def test_load_lark_oapi_binds_cardkit_request_types(self):
        from plugins.platforms.feishu import adapter as feishu_adapter

        if not feishu_adapter.feishu_deps_present():
            self.skipTest("lark_oapi is required")
        self.assertTrue(feishu_adapter._load_lark_oapi())
        for name in (
            "CreateCardRequest",
            "CreateCardRequestBody",
            "ContentCardElementRequest",
            "ContentCardElementRequestBody",
            "SettingsCardRequest",
            "SettingsCardRequestBody",
        ):
            self.assertIsNotNone(
                getattr(feishu_adapter, name, None),
                f"{name} must be bound so stream cards do not silently fall back",
            )


class TestClosedStreamRewrite(unittest.IsolatedAsyncioTestCase):
    async def test_finalize_edit_reopens_closed_stream(self):
        """300309 means the creating send already closed the card. The finalize edit still writes."""
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = object.__new__(FeishuAdapter)
        adapter._stream_card_message_ids = {}
        adapter._stream_content_error_code = None
        adapter._stream_content_error_msg = None
        content_calls = {"n": 0}
        modes = []
        long = "全文" * 200

        async def content(card_id, text):
            content_calls["n"] += 1
            if content_calls["n"] == 1:
                adapter._stream_content_error_code = 300309
                adapter._stream_content_error_msg = "ErrMsg: streaming mode is closed"
                return False
            return True

        async def mode(card_id, *, streaming, summary=""):
            modes.append((streaming, summary))
            return True

        adapter._stream_card_content = content
        adapter._set_stream_card_mode = mode
        result = await adapter._edit_stream_card(
            card_id="c1", content=long, finalize=True, message_id="card:c1", chat_id="oc",
        )
        self.assertTrue(result.success)
        self.assertEqual(modes[0], (True, ""))
        self.assertNotIn(long, modes[0][1])
        self.assertEqual(modes[1][0], False)
        self.assertEqual(content_calls["n"], 2)


class TestStreamCardTitle(unittest.TestCase):
    """The card header comes from stream_card_title (config > env > "Hermes"), per chat when set."""

    def _load(self, extra, env=None):
        import os
        from unittest.mock import patch

        from plugins.platforms.feishu.adapter import FeishuAdapter

        clean = {k: v for k, v in os.environ.items() if k != "FEISHU_STREAM_CARD_TITLE"}
        clean.update(env or {})
        with patch.dict(os.environ, clean, clear=True):
            return FeishuAdapter._load_settings(extra)

    def _header_title(self, adapter, chat_id=None):
        import json

        card = json.loads(adapter._build_stream_card_json("body", streaming=True, chat_id=chat_id))
        return card["header"]["title"]["content"]

    def _adapter(self, settings):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = object.__new__(FeishuAdapter)
        adapter._stream_card_title = settings.stream_card_title
        adapter._group_rules = settings.group_rules
        return adapter

    def test_default_title_is_hermes(self):
        settings = self._load({})
        self.assertEqual(settings.stream_card_title, "Hermes")
        self.assertEqual(self._header_title(self._adapter(settings)), "Hermes")

    def test_bare_adapter_falls_back_to_default(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        self.assertEqual(self._header_title(object.__new__(FeishuAdapter)), "Hermes")

    def test_config_value_sets_title(self):
        settings = self._load({"stream_card_title": "  Team Bot  "})
        self.assertEqual(settings.stream_card_title, "Team Bot")
        self.assertEqual(self._header_title(self._adapter(settings), "oc_any"), "Team Bot")

    def test_env_value_sets_title(self):
        settings = self._load({}, env={"FEISHU_STREAM_CARD_TITLE": "Env Bot"})
        self.assertEqual(settings.stream_card_title, "Env Bot")

    def test_config_wins_over_env_and_blank_falls_back(self):
        self.assertEqual(
            self._load({"stream_card_title": "Yaml Bot"}, env={"FEISHU_STREAM_CARD_TITLE": "Env Bot"}).stream_card_title,
            "Yaml Bot",
        )
        self.assertEqual(self._load({"stream_card_title": "  "}).stream_card_title, "Hermes")

    def test_group_rule_overrides_title(self):
        settings = self._load(
            {
                "stream_card_title": "Global Bot",
                "group_rules": {
                    "oc_named": {"policy": "open", "stream_card_title": "Group Bot"},
                    "oc_inherit": {"policy": "open"},
                },
            }
        )
        self.assertEqual(settings.group_rules["oc_named"].stream_card_title, "Group Bot")
        self.assertIsNone(settings.group_rules["oc_inherit"].stream_card_title)
        adapter = self._adapter(settings)
        self.assertEqual(self._header_title(adapter, "oc_named"), "Group Bot")
        self.assertEqual(self._header_title(adapter, "oc_inherit"), "Global Bot")
        self.assertEqual(self._header_title(adapter, "oc_other"), "Global Bot")

    def test_yaml_bridge_seeds_title_from_extra(self):
        import os
        from unittest.mock import patch

        from plugins.platforms.feishu.adapter import _apply_yaml_config

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FEISHU_STREAM_CARD_TITLE", None)
            seeded = _apply_yaml_config({}, {"extra": {"stream_card_title": "Yaml Bot"}})
            self.assertEqual(seeded, {"stream_card_title": "Yaml Bot"})
            self.assertEqual(os.environ.get("FEISHU_STREAM_CARD_TITLE"), "Yaml Bot")


class TestStreamCardTitleOnCreate(unittest.IsolatedAsyncioTestCase):
    async def test_send_stream_card_passes_chat_to_card_title(self):
        """The creating send builds the card for its own chat, so a group title applies."""
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = object.__new__(FeishuAdapter)
        adapter._create_stream_card_entity = AsyncMock(return_value=None)
        await adapter._send_stream_card(
            chat_id="oc_named", content="hi", reply_to=None, metadata=None, finalize=True,
        )
        adapter._create_stream_card_entity.assert_awaited_once_with("hi", chat_id="oc_named")


class TestOverlaySettingsReadProfileScope(unittest.TestCase):
    """Overlay env reads go through the profile secret scope, like upstream settings."""

    def test_scoped_values_win_over_launch_environ(self):
        import os
        from unittest.mock import patch

        from agent import secret_scope as ss
        from plugins.platforms.feishu.adapter import FeishuAdapter

        launch_env = {
            "FEISHU_STREAM_CARD_TITLE": "Default Profile Bot",
            "FEISHU_STREAM_CARD": "false",
            "FEISHU_AUTO_THREAD": "true",
            "FEISHU_ROUTE_INBOUND_REACTIONS": "false",
        }
        scoped = {
            "FEISHU_STREAM_CARD_TITLE": "Secondary Bot",
            "FEISHU_STREAM_CARD": "true",
            "FEISHU_STREAM_CARD_IN_THREAD": "true",
            "FEISHU_AUTO_THREAD": "false",
            "FEISHU_ROUTE_INBOUND_REACTIONS": "true",
            "FEISHU_THREAD_FOLLOWUP_WITHOUT_MENTION": "false",
        }
        with patch.dict(os.environ, launch_env):
            tok = ss.set_secret_scope(scoped)
            try:
                settings = FeishuAdapter._load_settings({})
            finally:
                ss.reset_secret_scope(tok)
        self.assertEqual(settings.stream_card_title, "Secondary Bot")
        self.assertTrue(settings.stream_card_enabled)
        self.assertTrue(settings.stream_card_in_thread)
        self.assertFalse(settings.auto_thread_enabled)
        self.assertTrue(settings.route_inbound_reactions)
        self.assertFalse(settings.thread_followup_without_mention)

    def test_scoped_miss_uses_default_not_launch_environ(self):
        """Under multiplexing a secondary profile never borrows the launch process's value."""
        import os
        from unittest.mock import patch

        from agent import secret_scope as ss
        from plugins.platforms.feishu.adapter import FeishuAdapter

        was_multiplex = ss._MULTIPLEX_ACTIVE
        with patch.dict(os.environ, {"FEISHU_STREAM_CARD_TITLE": "Default Profile Bot"}):
            ss.set_multiplex_active(True)
            tok = ss.set_secret_scope({"SOME_OTHER_KEY": "x"})
            try:
                settings = FeishuAdapter._load_settings({"stream_card_title": ""})
            finally:
                ss.reset_secret_scope(tok)
                ss.set_multiplex_active(was_multiplex)
        self.assertEqual(settings.stream_card_title, "Hermes")

    def test_extra_still_wins_over_scoped_env(self):
        from agent import secret_scope as ss
        from plugins.platforms.feishu.adapter import FeishuAdapter

        tok = ss.set_secret_scope({"FEISHU_STREAM_CARD_TITLE": "Env Bot", "FEISHU_STREAM_CARD": "false"})
        try:
            settings = FeishuAdapter._load_settings({"stream_card_title": "Yaml Bot", "stream_card": True})
        finally:
            ss.reset_secret_scope(tok)
        self.assertEqual(settings.stream_card_title, "Yaml Bot")
        self.assertTrue(settings.stream_card_enabled)
