"""Failed-turn boundary notices follow the active display language (fork overlay)."""

import pytest

import hermes_cli.config as hermes_config
from agent import i18n
from agent.turn_failure_copy import (
    FAILED_TURN_DISPLAY_KIND,
    FAILED_TURN_NOTICE,
    PARTIAL_FAILED_TURN_NOTICE,
    failed_turn_notice,
    localized_failed_turn_notice,
    untyped_failed_turn_display_kind,
)

ZH_FULL = "这条请求没有处理。如果还需要，请再发一次。"
ZH_PARTIAL = "这一轮没有完成，部分操作可能已经执行，重发前先确认结果。"
TOOL_TURN = [{"role": "user", "content": "hi"}, {"role": "tool", "content": "ok"}]


@pytest.fixture
def language(monkeypatch):
    def _set(value):
        if value is None:
            monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
        else:
            monkeypatch.setenv("HERMES_LANGUAGE", value)
        monkeypatch.setattr(hermes_config, "load_config_readonly", lambda: {})
        i18n.reset_language_cache()

    yield _set
    i18n.reset_language_cache()


def test_english_is_the_default(language):
    language(None)
    assert failed_turn_notice([]) == FAILED_TURN_NOTICE
    assert failed_turn_notice(TOOL_TURN) == PARTIAL_FAILED_TURN_NOTICE


@pytest.mark.parametrize("value", ["zh", "zh-CN", "chinese"])
def test_chinese_language_gets_chinese_copy(language, value):
    language(value)
    assert failed_turn_notice([]) == ZH_FULL
    assert failed_turn_notice(TOOL_TURN) == ZH_PARTIAL
    assert localized_failed_turn_notice(partial=True) == ZH_PARTIAL


def test_display_language_from_config_is_used(monkeypatch):
    monkeypatch.delenv("HERMES_LANGUAGE", raising=False)
    monkeypatch.setattr(hermes_config, "load_config_readonly", lambda: {"display": {"language": "zh"}})
    i18n.reset_language_cache()
    try:
        assert localized_failed_turn_notice() == ZH_FULL
    finally:
        i18n.reset_language_cache()


def test_untranslated_language_falls_back_to_english(language):
    language("fr")
    assert localized_failed_turn_notice() == FAILED_TURN_NOTICE
    assert localized_failed_turn_notice(partial=True) == PARTIAL_FAILED_TURN_NOTICE


def test_explicit_lang_overrides_active_language(language):
    language("zh")
    assert localized_failed_turn_notice(lang="en") == FAILED_TURN_NOTICE
    assert failed_turn_notice([], lang="en") == FAILED_TURN_NOTICE


@pytest.mark.parametrize("text", [FAILED_TURN_NOTICE, PARTIAL_FAILED_TURN_NOTICE, ZH_FULL, ZH_PARTIAL])
def test_untyped_rows_in_any_language_read_back_as_boundary(text):
    assert untyped_failed_turn_display_kind("assistant", text) == FAILED_TURN_DISPLAY_KIND
    assert untyped_failed_turn_display_kind("assistant", f"引用：{text}") is None
    assert untyped_failed_turn_display_kind("user", text) is None
