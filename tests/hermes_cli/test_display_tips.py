"""display.tips: gateway /new draws the user's list; the built-in corpus stays the fallback."""

import pytest

from hermes_cli.tips import (
    TIPS,
    choose_nonrepeating_tip,
    clear_gateway_reset_tip_memory,
    draw_gateway_reset_tip,
    gateway_reset_tip,
    gateway_reset_tip_slot,
    get_random_tip,
    normalize_display_tips,
)
from hermes_constants import get_hermes_home, hermes_home_key


@pytest.fixture(autouse=True)
def _fresh_tip_memory():
    clear_gateway_reset_tip_memory()
    yield
    clear_gateway_reset_tip_memory()


def _write_tips(raw_yaml: str) -> None:
    (get_hermes_home() / "config.yaml").write_text(raw_yaml, encoding="utf-8")


class TestNormalizeDisplayTips:
    def test_bare_string_is_one_tip(self):
        assert normalize_display_tips("  等你拍板。我先不动。  ") == ["等你拍板。我先不动。"]

    def test_list_drops_blanks_and_non_strings(self):
        assert normalize_display_tips([" 甲 ", "", "  ", 3, None, "乙"]) == ["甲", "乙"]

    def test_wrong_type_is_empty(self):
        assert normalize_display_tips({"mode": "replace"}) == []
        assert normalize_display_tips(0) == []
        assert normalize_display_tips(None) == []


class TestChooseNonrepeatingTip:
    def test_skips_the_last_draw_when_another_line_exists(self):
        picked = choose_nonrepeating_tip(["甲", "乙"], "甲", chooser=lambda pool: pool[0])
        assert picked == "乙"

    def test_repeats_when_it_is_the_only_line(self):
        assert choose_nonrepeating_tip(["甲"], "甲", chooser=lambda pool: pool[0]) == "甲"


class TestGatewayResetTip:
    def test_draws_configured_lines_not_the_builtin_corpus(self, monkeypatch):
        _write_tips("display:\n  tips:\n    - 只此一句\n")
        monkeypatch.setattr("hermes_cli.tips.random.choice", lambda pool: pool[0])
        body, own_words = draw_gateway_reset_tip(gateway_reset_tip_slot("feishu", "chat-1"))
        assert body == "只此一句"
        assert own_words is True

    def test_builtin_draw_is_not_marked_as_the_users_line(self):
        _write_tips("display:\n  tips: []\n")
        body, own_words = draw_gateway_reset_tip("feishu:chat-1:")
        assert own_words is False
        assert body in TIPS

    def test_empty_list_uses_the_builtin_corpus(self, monkeypatch):
        _write_tips("display:\n  tips: []\n")
        seen = gateway_reset_tip("feishu:chat-1:")
        assert seen in TIPS

    def test_bad_shape_uses_the_builtin_corpus(self):
        _write_tips("display:\n  tips:\n    mode: replace\n")
        assert gateway_reset_tip("feishu:chat-1:") in TIPS

    def test_bare_string_in_config_is_the_only_draw(self):
        _write_tips("display:\n  tips: 只此一句\n")
        assert gateway_reset_tip("feishu:chat-1:") == "只此一句"

    def test_same_chat_does_not_repeat_the_previous_line(self, monkeypatch):
        _write_tips("display:\n  tips:\n    - 甲\n    - 乙\n")
        monkeypatch.setattr("hermes_cli.tips.random.choice", lambda pool: pool[0])
        slot = gateway_reset_tip_slot("feishu", "chat-1", "topic-9")
        assert gateway_reset_tip(slot) == "甲"
        assert gateway_reset_tip(slot) == "乙"

    def test_another_chat_keeps_its_own_last_line(self, monkeypatch):
        _write_tips("display:\n  tips:\n    - 甲\n    - 乙\n")
        monkeypatch.setattr("hermes_cli.tips.random.choice", lambda pool: pool[0])
        first = gateway_reset_tip_slot("feishu", "chat-1")
        second = gateway_reset_tip_slot("feishu", "chat-2")
        assert gateway_reset_tip(first) == "甲"
        assert gateway_reset_tip(second) == "甲"

    def test_another_profile_keeps_its_own_last_line(self, monkeypatch, tmp_path):
        _write_tips("display:\n  tips:\n    - 甲\n    - 乙\n")
        other = tmp_path / "other-profile"
        other.mkdir()
        (other / "config.yaml").write_text(
            "display:\n  tips:\n    - 甲\n    - 乙\n", encoding="utf-8")
        monkeypatch.setattr("hermes_cli.tips.random.choice", lambda pool: pool[0])
        slot = gateway_reset_tip_slot("feishu", "chat-1")
        assert gateway_reset_tip(slot) == "甲"
        assert hermes_home_key() != hermes_home_key(other)
        monkeypatch.setenv("HERMES_HOME", str(other))
        assert gateway_reset_tip(slot) == "甲"

    def test_cli_draw_ignores_display_tips(self):
        _write_tips("display:\n  tips:\n    - 只此一句\n")
        assert get_random_tip() in TIPS
        assert get_random_tip() != "只此一句"
