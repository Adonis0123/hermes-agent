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

    def test_paragraph_after_a_list_starts_a_new_markdown_element(self):
        """Feishu glues a paragraph that follows a list onto the last item, so the element ends there."""
        from plugins.platforms.feishu.adapter import _markdown_card_elements

        reply = "待办：\n- 请 Soap 在群里叫测试一起验\n- 合并 PR\n\n另有 10 条「未解决」\n\n**下一步**\n1. 发版"
        self.assertEqual(
            [e["content"] for e in _markdown_card_elements(reply)],
            ["待办：\n- 请 Soap 在群里叫测试一起验\n- 合并 PR", "另有 10 条「未解决」\n\n**下一步**\n1. 发版"],
        )

    def test_list_continuations_and_following_lists_stay_together(self):
        from plugins.platforms.feishu.adapter import _markdown_card_elements

        lists = "- a\n  续行\n\n  第二段\n\n* b\n\n1. c\n2. d\n\n+ e"
        self.assertEqual([e["content"] for e in _markdown_card_elements(lists)], [lists])
        # A heading or bold line after the list is a paragraph: split; trailing blanks split nothing.
        self.assertEqual(
            [e["content"] for e in _markdown_card_elements("- a\n\n# 标题\n- b\n\n\n")],
            ["- a", "# 标题\n- b"],
        )
        # "**bold**" and "---" are not list items, so nothing after them is cut.
        plain = "**粗体**\n\n正文\n\n---\n\n结尾"
        self.assertEqual([e["content"] for e in _markdown_card_elements(plain)], [plain])

    def test_fenced_code_is_never_split(self):
        from plugins.platforms.feishu.adapter import _markdown_card_elements

        fenced = "```\n- a\n\nnot a paragraph\n```"
        self.assertEqual([e["content"] for e in _markdown_card_elements(fenced)], [fenced])
        # A fence after a list starts its own element and keeps its blank lines.
        after_list = "- a\n\n```\nx\n\ny\n```\n\n尾"
        self.assertEqual(
            [e["content"] for e in _markdown_card_elements(after_list)],
            ["- a", "```\nx\n\ny\n```\n\n尾"],
        )

    def test_list_before_a_table_keeps_table_split(self):
        from plugins.platforms.feishu.adapter import _markdown_card_elements

        reply = "- a\n\n| x | y |\n|---|---|\n| 1 | 2 |\n\n- b\n\n收尾"
        self.assertEqual(
            [e["tag"] for e in _markdown_card_elements(reply)], ["markdown", "table", "markdown", "markdown"],
        )

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

    async def test_finalize_splits_a_list_glued_paragraph_without_a_table(self):
        import json

        adapter = self._adapter()
        adapter._stream_card_content = AsyncMock(return_value=True)
        await adapter._edit_stream_card(
            card_id="c1", content="- 一项\n\n另有 10 条", finalize=True, message_id="card:c1", chat_id="oc",
        )
        card = json.loads(adapter._update_stream_card.await_args.args[1])
        self.assertEqual([e["content"] for e in card["body"]["elements"]], ["- 一项", "另有 10 条"])
        self.assertEqual(adapter._stream_card_tables, {"c1": "oc"})  # next stream write restores md_body

    async def test_finalize_without_table_keeps_markdown_card(self):
        adapter = self._adapter()
        adapter._stream_card_content = AsyncMock(return_value=True)
        await adapter._edit_stream_card(
            card_id="c1", content="**结论**", finalize=True, message_id="card:c1", chat_id="oc",
        )
        adapter._update_stream_card.assert_not_called()
