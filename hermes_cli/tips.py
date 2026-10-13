"""Random tips shown at CLI session start to help users discover features.

The tip text lives in the i18n catalog (``locales/<lang>.yaml`` under ``tips.tNNN`` and
``tips.placeholder.pNN``), so language packs translate it like every other user-facing string.
The English catalog is the source of truth for HOW MANY tips exist (key parity is enforced by
tests/agent/test_i18n.py); the pickers resolve each key through ``t()`` for the active language.
"""

from __future__ import annotations

import logging
import random
import threading
from typing import Any, Callable, Optional

from agent.i18n import DEFAULT_LANGUAGE, t

logger = logging.getLogger(__name__)

# Last custom tip per (profile home, chat slot). Process memory only.
_RESET_TIP_LAST: dict[tuple[str, str], str] = {}

TIP_KEY_PREFIX = "tips.t"
PLACEHOLDER_KEY_PREFIX = "tips.placeholder.p"
_TIP_KEY_WIDTH = 3
_PLACEHOLDER_KEY_WIDTH = 2
# Hard stop for the contiguous-key probe so a pathological catalog can never spin.
_MAX_KEYS = 10_000

_keys_lock = threading.Lock()
_keys_cache: dict[str, tuple[str, ...]] = {}


def _catalog_keys(prefix: str, width: int) -> tuple[str, ...]:
    """Contiguous ``<prefix><NN…>`` keys present in the English catalog, cached per prefix.

    Counted against English (the parity baseline) so a partial translation never shrinks the
    pool: a missing translated key falls back to English inside ``t()``. The count only changes
    when the bundled catalog changes, i.e. on a code change, so a process-lifetime cache is safe.
    """
    with _keys_lock:
        cached = _keys_cache.get(prefix)
    if cached is not None:
        return cached
    keys: list[str] = []
    for index in range(1, _MAX_KEYS):
        key = f"{prefix}{index:0{width}d}"
        if t(key, lang=DEFAULT_LANGUAGE) == key:  # bare-key echo == not in the catalog
            break
        keys.append(key)
    found = tuple(keys)
    with _keys_lock:
        _keys_cache[prefix] = found
    return found


def reset_tips_cache() -> None:
    """Forget the probed key lists (tests that swap the locales dir call this)."""
    with _keys_lock:
        _keys_cache.clear()


def tip_keys() -> tuple[str, ...]:
    """Catalog keys of every startup tip, in catalog order."""
    return _catalog_keys(TIP_KEY_PREFIX, _TIP_KEY_WIDTH)


def composer_placeholder_keys() -> tuple[str, ...]:
    """Catalog keys of every empty-composer example prompt, in catalog order."""
    return _catalog_keys(PLACEHOLDER_KEY_PREFIX, _PLACEHOLDER_KEY_WIDTH)


def get_random_tip(exclude_recent: int = 0) -> str:
    """Return a random tip string in the active language ("" if the catalog has none)."""
    keys = tip_keys()
    return t(random.choice(keys)) if keys else ""


def get_random_composer_placeholder() -> str:
    """Return a rotating task-oriented placeholder for the empty composer, in the active language.

    Kept generic — Hermes is not a coding-only agent, so the prompts must fit any project or none.
    """
    keys = composer_placeholder_keys()
    return t(random.choice(keys)) if keys else ""


def normalize_display_tips(raw: Any) -> list[str]:
    """Coerce ``display.tips`` into stripped strings. A bare string is one tip."""
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    tips: list[str] = []
    for item in raw:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text:
            tips.append(text)
    return tips


def choose_nonrepeating_tip(
    tips: list[str],
    last: Optional[str],
    chooser: Optional[Callable[[list[str]], str]] = None,
) -> str:
    """Pick one tip, skipping ``last`` while another line is available."""
    if not tips:
        raise ValueError("tips must be non-empty")
    pool = [tip for tip in tips if tip != last] or tips
    return (chooser or random.choice)(pool)


def gateway_reset_tip_slot(platform: str, chat_id: str, thread_id: Optional[str] = None) -> str:
    """Chat identity for tip memory. A thread is its own chat."""
    return "\n".join((platform or "", str(chat_id or ""), str(thread_id or "")))


def clear_gateway_reset_tip_memory() -> None:
    """Drop remembered draws. Tests use this; a process restart does the same."""
    _RESET_TIP_LAST.clear()


def _display_tips_raw() -> Any:
    from hermes_cli.config import load_config_readonly
    display = load_config_readonly().get("display") or {}
    if not isinstance(display, dict):
        return None
    return display.get("tips")


def draw_gateway_reset_tip(chat_slot: str) -> tuple[str, bool]:
    """Return ``(body, from_user_list)`` for gateway ``/new`` and ``/reset``.

    A non-empty ``display.tips`` list is the whole pool. Those lines are the user's
    own words, so the caller prints them without the product-tip label.
    """
    try:
        tips = normalize_display_tips(_display_tips_raw())
    except Exception:
        logger.debug("Could not read display.tips", exc_info=True)
        tips = []
    if not tips:
        return get_random_tip(), False
    from hermes_constants import hermes_home_key
    slot = (hermes_home_key(), chat_slot)
    chosen = choose_nonrepeating_tip(tips, _RESET_TIP_LAST.get(slot))
    _RESET_TIP_LAST[slot] = chosen
    return chosen, True
