import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

class TestStreamCardNativeTables(unittest.IsolatedAsyncioTestCase):
    """Card markdown tables split columns evenly, so a closed card swaps in content-sized native tables."""

    REPLY = "改完了。\n\n| # | 现在 |\n|---|:---:|\n| 1 | 解释失败卡时，要带上同早没跑成的任务名 |\n\n收尾。"

    def test_index_column_is_narrow_and_text_kept_around(self):
        from plugins.platforms.feishu.adapter import _markdown_card_elements

        elements = _markdown_card_elements(self.REPLY)
        self.assertEqual([e["tag"] for e in elements], ["markdown", "table", "markdown"])
        table = elements[1]
        widths = [int(c["width"].rstrip("%")) for c in table["columns"]]
        self.assertEqual(sum(widths), 100)
        self.assertLess(widths[0], 15)
        self.assertEqual(table["columns"][1]["horizontal_align"], "center")
        self.assertEqual(table["rows"], [{"c0": "1", "c1": "解释失败卡时，要带上同早没跑成的任务名"}])

    def test_fenced_and_excess_tables_stay_markdown(self):
        from plugins.platforms.feishu.adapter import _markdown_card_elements

        fenced = "```\n| a | b |\n|---|---|\n| 1 | 2 |\n```"
        self.assertEqual([e["tag"] for e in _markdown_card_elements(fenced)], ["markdown"])
        many = "\n\n".join("| a | b |\n|---|---|\n| 1 | 2 |" for _ in range(6))
        tags = [e["tag"] for e in _markdown_card_elements(many)]
        self.assertEqual(tags.count("table"), 5)
        self.assertEqual(tags[-1], "markdown")

    def _adapter(self):
        from plugins.platforms.feishu.adapter import FeishuAdapter

        adapter = object.__new__(FeishuAdapter)
        adapter._stream_card_title_for = lambda chat_id: "小 ad"
        adapter._set_stream_card_mode = AsyncMock(return_value=True)
        adapter._update_stream_card = AsyncMock(return_value=True)
        adapter._stream_content_error_code = None
        adapter._stream_content_error_msg = None
        return adapter

    async def test_finalize_swaps_tables_then_next_write_restores_stream_body(self):
        import json
        from unittest.mock import MagicMock, patch

        from plugins.platforms.feishu import adapter as feishu_adapter

        adapter = self._adapter()
        adapter._stream_card_tables = {}
        adapter._client = MagicMock()
        adapter._run_blocking = AsyncMock(return_value=SimpleNamespace(code=0))
        adapter._response_succeeded = lambda response: True
        with patch.object(feishu_adapter, "ContentCardElementRequest", MagicMock()), \
                patch.object(feishu_adapter, "ContentCardElementRequestBody", MagicMock()):
            result = await adapter._edit_stream_card(
                card_id="c1", content=self.REPLY, finalize=True, message_id="card:c1", chat_id="oc",
            )
        adapter._run_blocking.assert_awaited_once()
        self.assertTrue(result.success)
        card = json.loads(adapter._update_stream_card.await_args.args[1])
        self.assertFalse(card["config"]["streaming_mode"])
        self.assertIn("table", [e["tag"] for e in card["body"]["elements"]])
        self.assertEqual(adapter._stream_card_tables, {"c1": "oc"})

        self.assertTrue(await adapter._stream_card_content("c1", "新内容"))
        card = json.loads(adapter._update_stream_card.await_args.args[1])
        self.assertEqual(card["body"]["elements"][0]["element_id"], "md_body")
        self.assertEqual(adapter._stream_card_tables, {})

    async def test_finalize_without_table_keeps_markdown_card(self):
        adapter = self._adapter()
        adapter._stream_card_content = AsyncMock(return_value=True)
        await adapter._edit_stream_card(
            card_id="c1", content="**结论**", finalize=True, message_id="card:c1", chat_id="oc",
        )
        adapter._update_stream_card.assert_not_called()
