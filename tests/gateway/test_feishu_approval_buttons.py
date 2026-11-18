"""Tests for Feishu interactive card approval buttons."""

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.i18n import t

# ---------------------------------------------------------------------------
# Ensure the repo root is importable
# ---------------------------------------------------------------------------
_repo = str(Path(__file__).resolve().parents[2])
if _repo not in sys.path:
    sys.path.insert(0, _repo)


# ---------------------------------------------------------------------------
# Minimal Feishu mock so FeishuAdapter can be imported without lark-oapi
# ---------------------------------------------------------------------------
def _ensure_feishu_mocks():
    """Provide stubs for lark-oapi / aiohttp.web so the import succeeds."""
    if importlib.util.find_spec("lark_oapi") is None and "lark_oapi" not in sys.modules:
        mod = MagicMock()
        for name in (
            "lark_oapi", "lark_oapi.api.im.v1",
            "lark_oapi.event", "lark_oapi.event.callback_type",
        ):
            sys.modules.setdefault(name, mod)
    if importlib.util.find_spec("aiohttp") is None and "aiohttp" not in sys.modules:
        aio = MagicMock()
        sys.modules.setdefault("aiohttp", aio)
        sys.modules.setdefault("aiohttp.web", aio.web)


_ensure_feishu_mocks()

from gateway.config import PlatformConfig
import plugins.platforms.feishu.adapter as feishu_module
from plugins.platforms.feishu.adapter import FeishuAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_adapter() -> FeishuAdapter:
    """Create a FeishuAdapter with mocked internals."""
    config = PlatformConfig(enabled=True)
    adapter = FeishuAdapter(config)
    adapter._client = MagicMock()
    return adapter


def _make_card_action_data(
    action_value: dict,
    chat_id: str = "oc_12345",
    open_id: str = "ou_user1",
    token: str = "tok_abc",
) -> SimpleNamespace:
    """Create a mock Feishu card action callback data object."""
    return SimpleNamespace(
        event=SimpleNamespace(
            token=token,
            context=SimpleNamespace(open_chat_id=chat_id),
            operator=SimpleNamespace(open_id=open_id),
            action=SimpleNamespace(
                tag="button",
                value=action_value,
            ),
        ),
    )


def _close_submitted_coro(coro, _loop):
    """Close scheduled coroutines in sync-handler tests to avoid unawaited warnings."""
    coro.close()
    return SimpleNamespace(add_done_callback=lambda *_args, **_kwargs: None)


# ===========================================================================
# send_exec_approval — interactive card with buttons
# ===========================================================================

class TestFeishuExecApproval:
    """Test send_exec_approval sends an interactive card."""

    @pytest.mark.asyncio
    async def test_sends_interactive_card(self):
        adapter = _make_adapter()

        mock_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(message_id="msg_001"),
        )
        with patch.object(
            adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_send:
            result = await adapter.send_exec_approval(
                chat_id="oc_12345",
                command="rm -rf /important",
                session_key="agent:main:feishu:group:oc_12345",
                description="dangerous deletion",
            )

        assert result.success is True
        assert result.message_id == "msg_001"

        mock_send.assert_called_once()
        kwargs = mock_send.call_args[1]
        assert kwargs["chat_id"] == "oc_12345"
        assert kwargs["msg_type"] == "interactive"

        # Verify card payload contains the command and buttons
        card = json.loads(kwargs["payload"])
        assert "rm -rf /important" in card["elements"][0]["content"]
        assert "dangerous deletion" in card["elements"][0]["content"]

        # Check buttons
        actions = card["elements"][1]["actions"]
        assert len(actions) == 4
        action_names = [a["value"]["hermes_action"] for a in actions]
        assert action_names == [
            "approve_once", "approve_session", "approve_always", "deny"
        ]

    @pytest.mark.asyncio
    async def test_stores_approval_state(self):
        adapter = _make_adapter()

        mock_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(message_id="msg_002"),
        )
        with patch.object(
            adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
            return_value=mock_response,
        ):
            await adapter.send_exec_approval(
                chat_id="oc_12345",
                command="echo test",
                session_key="my-session-key",
            )

        assert len(adapter._approval_state) == 1
        approval_id = next(iter(adapter._approval_state.keys()))
        state = adapter._approval_state[approval_id]
        assert state["session_key"] == "my-session-key"
        assert state["message_id"] == "msg_002"
        assert state["chat_id"] == "oc_12345"


# ===========================================================================
# send_update_prompt — interactive card with buttons
# ===========================================================================

class TestFeishuUpdatePrompt:
    """Test send_update_prompt sends an interactive card."""

    @pytest.mark.asyncio
    async def test_sends_interactive_card(self):
        adapter = _make_adapter()

        mock_response = SimpleNamespace(
            success=lambda: True,
            data=SimpleNamespace(message_id="msg_up_001"),
        )
        with patch.object(
            adapter, "_feishu_send_with_retry", new_callable=AsyncMock,
            return_value=mock_response,
        ) as mock_send:
            result = await adapter.send_update_prompt(
                chat_id="oc_12345",
                prompt="Restore stashed changes after update?",
                default="y",
                session_key="agent:main:feishu:group:oc_12345",
                metadata={"thread_id": "th_1"},
            )

        assert result.success is True
        assert result.message_id == "msg_up_001"

        kwargs = mock_send.call_args[1]
        assert kwargs["chat_id"] == "oc_12345"
        assert kwargs["msg_type"] == "interactive"
        assert kwargs["metadata"] == {"thread_id": "th_1"}

        card = json.loads(kwargs["payload"])
        assert "Restore stashed changes after update?" in card["elements"][0]["content"]
        actions = card["elements"][1]["actions"]
        assert [a["value"]["hermes_update_prompt_action"] for a in actions] == ["y", "n"]


# ===========================================================================
# _resolve_approval — approval state pop + gateway resolution
# ===========================================================================

class TestResolveApproval:
    """Test _resolve_approval pops state and calls resolve_gateway_approval."""

    @pytest.mark.asyncio
    async def test_resolves_once(self):
        adapter = _make_adapter()
        # No Feishu allowlist: the gateway authorization decides (here: this user is allowed).
        adapter.set_authorization_check(lambda *_a, **_kw: True)
        adapter._approval_state[1] = {
            "session_key": "agent:main:feishu:group:oc_12345",
            "message_id": "msg_001",
            "chat_id": "oc_12345",
            "request_id": "req-1",
        }

        with patch("tools.approval.resolve_gateway_approval", return_value=1) as mock_resolve:
            await adapter._resolve_approval(1, "once", "Norbert", open_id="ou_user1", chat_id="oc_12345")

        mock_resolve.assert_called_once_with("agent:main:feishu:group:oc_12345", "once", request_id="req-1")
        assert 1 not in adapter._approval_state


    @pytest.mark.asyncio
    async def test_unauthorized_click_does_not_resolve(self):
        adapter = _make_adapter()
        adapter._admins = {"ou_admin"}
        adapter._approval_state[5] = {
            "session_key": "sess-5",
            "message_id": "msg_005",
            "chat_id": "oc_12345",
        }

        with patch("tools.approval.resolve_gateway_approval") as mock_resolve:
            await adapter._resolve_approval(5, "once", "Mallory", open_id="ou_intruder", chat_id="oc_12345")

        mock_resolve.assert_not_called()
        assert 5 in adapter._approval_state


# ===========================================================================
# _handle_card_action_event — non-approval card actions
# ===========================================================================

class TestNonApprovalCardAction:
    """Non-approval card actions should still route as synthetic commands."""

    @pytest.mark.asyncio
    async def test_routes_as_synthetic_command(self):
        adapter = _make_adapter()
        adapter._allowed_group_users = frozenset({"ou_user1"})  # clicker passes the group allowlist

        data = _make_card_action_data(
            action_value={"custom_action": "something_else"},
            token="tok_normal",
        )

        with (
            patch.object(
                adapter, "_resolve_sender_profile", new_callable=AsyncMock,
                return_value={"user_id": "ou_u", "user_name": "Dave", "user_id_alt": None},
            ),
            patch.object(adapter, "get_chat_info", new_callable=AsyncMock, return_value={"name": "Test Chat"}),
            patch.object(adapter, "_handle_message_with_guards", new_callable=AsyncMock) as mock_handle,
        ):
            await adapter._handle_card_action_event(data)

        mock_handle.assert_called_once()
        event = mock_handle.call_args[0][0]
        assert "/card button" in event.text

    def test_click_is_acknowledged_with_a_toast_and_still_forwarded(self, _patch_callback_card_types, monkeypatch):
        """A plugin takes seconds to settle its card: the clicker is told right away that the click
        landed, unless it could not be queued."""
        monkeypatch.setattr(feishu_module, "CallBackToast", SimpleNamespace, raising=False)
        adapter = _make_adapter()
        adapter._allowed_group_users = frozenset({"ou_user1"})  # clicker passes the group allowlist
        adapter._loop = MagicMock(is_closed=MagicMock(return_value=False))
        data = _make_card_action_data({"custom_action": "something_else"}, token="tok_normal")

        def _run_submitted(coro, _loop):
            asyncio.run(coro)
            return SimpleNamespace(add_done_callback=lambda *_a, **_k: None)

        with (
            patch.object(
                adapter, "_resolve_sender_profile", new_callable=AsyncMock,
                return_value={"user_id": "ou_u", "user_name": "Dave", "user_id_alt": None},
            ),
            patch.object(adapter, "get_chat_info", new_callable=AsyncMock, return_value={"name": "Test Chat"}),
            patch.object(adapter, "_handle_message_with_guards", new_callable=AsyncMock) as mock_handle,
        ):
            with patch("asyncio.run_coroutine_threadsafe", side_effect=_run_submitted):
                response = adapter._on_card_action_trigger(data)
            with patch("asyncio.run_coroutine_threadsafe", side_effect=RuntimeError("loop closed")):
                not_queued = adapter._on_card_action_trigger(data)

        assert "/card button" in mock_handle.call_args[0][0].text
        assert response.card is None and response.toast.type == "info"
        assert response.toast.content == t("platform.feishu.card_action.received") != "platform.feishu.card_action.received"
        assert not_queued.card is None and not hasattr(not_queued, "toast")
        mock_handle.assert_called_once()


# ===========================================================================
# _on_card_action_trigger — inline card response for approval actions
# ===========================================================================

class _FakeCallBackCard:
    def __init__(self):
        self.type = None
        self.data = None


class _FakeP2Response:
    def __init__(self):
        self.card = None


@pytest.fixture(autouse=False)
def _patch_callback_card_types(monkeypatch):
    """Provide real-ish P2CardActionTriggerResponse / CallBackCard for tests."""
    monkeypatch.setattr(feishu_module, "P2CardActionTriggerResponse", _FakeP2Response)
    monkeypatch.setattr(feishu_module, "CallBackCard", _FakeCallBackCard)


class TestCardActionCallbackResponse:
    """Test that _on_card_action_trigger returns updated card inline."""

    def test_drops_action_when_loop_not_ready(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = None
        data = _make_card_action_data({"hermes_action": "approve_once", "approval_id": 1})

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()

    def test_returns_card_for_approve_action(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_bob"}
        adapter._approval_state[1] = {
            "session_key": "sess-1",
            "message_id": "msg-1",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": 1},
            open_id="ou_bob",
        )
        adapter._sender_name_cache["ou_bob"] = ("Bob", 9999999999)

        with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_submitted_coro):
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is not None
        assert response.card.type == "raw"
        card = response.card.data
        assert t("platform.feishu.approval.resolved_once") in card["header"]["title"]["content"]
        assert "Bob" in card["elements"][0]["content"]


    def test_ignores_expired_cached_name(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_expired"}
        adapter._approval_state[4] = {
            "session_key": "sess-4",
            "message_id": "msg-4",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": 4},
            open_id="ou_expired",
        )
        adapter._sender_name_cache["ou_expired"] = ("Old Name", 1)

        with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_submitted_coro):
            response = adapter._on_card_action_trigger(data)

        card = response.card.data
        assert "Old Name" not in card["elements"][0]["content"]
        assert "ou_expired" in card["elements"][0]["content"]

    def test_rejects_approval_click_from_unauthorized_user(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_allowed"}
        adapter._approval_state[5] = {
            "session_key": "sess-5",
            "message_id": "msg-5",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": 5},
            open_id="ou_attacker",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()

    def test_rejects_approval_click_when_group_policy_open(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_allowed"}
        adapter._group_policy = "open"
        adapter._default_group_policy = "open"
        adapter._approval_state[6] = {
            "session_key": "sess-6",
            "message_id": "msg-6",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": 6},
            open_id="ou_attacker",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()


    def test_update_prompt_unauthorized_operator_returns_no_card(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._update_prompt_state[1] = {
            "session_key": "sess-up-1",
            "message_id": "msg_up_006",
            "chat_id": "oc_12345",
        }
        adapter._allowed_group_users = {"ou_allowed"}
        data = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": 1},
            open_id="ou_intruder",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()

    def test_update_prompt_unauthorized_click_rejected_when_group_policy_open(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_allowed"}
        adapter._group_policy = "open"
        adapter._default_group_policy = "open"
        adapter._update_prompt_state[7] = {
            "session_key": "sess-up-7",
            "message_id": "msg_up_007",
            "chat_id": "oc_12345",
        }
        data = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": 7},
            open_id="ou_intruder",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()


    def test_update_prompt_chat_mismatch_returns_no_card(self, _patch_callback_card_types):
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._allowed_group_users = {"ou_bob"}
        adapter._update_prompt_state[8] = {
            "session_key": "sess-up-8",
            "message_id": "msg_up_008",
            "chat_id": "oc_expected",
        }
        data = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": 8},
            chat_id="oc_mismatch",
            open_id="ou_bob",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        assert 8 in adapter._update_prompt_state
        mock_submit.assert_not_called()

    # Scenarios below are adapted from @liuliu0223's regression suite in
    # #99021: DM paired-mode (empty allowlist) positive paths, fail-closed
    # rejection of missing operator identity, and forwarded-card rejection.

    def test_paired_mode_participant_can_approve(self, _patch_callback_card_types):
        """Empty allowlist (DM paired mode): the card recipient can still approve."""
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._admins = set()
        adapter._allowed_group_users = set()
        # The paired DM user passes the gateway authorization (pairing store).
        adapter.set_authorization_check(lambda *_a, **_kw: True)
        adapter._approval_state[15] = {
            "session_key": "sess-15",
            "message_id": "msg-15",
            "chat_id": "oc_dm_chat",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": 15},
            chat_id="oc_dm_chat",
            open_id="ou_dm_user",
        )
        adapter._sender_name_cache["ou_dm_user"] = ("DM User", 9999999999)

        with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_submitted_coro):
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is not None
        assert t("platform.feishu.approval.resolved_once") in response.card.data["header"]["title"]["content"]

    def test_paired_mode_participant_can_confirm_update_prompt(self, _patch_callback_card_types):
        """Empty allowlist (DM paired mode): the prompt recipient can still confirm."""
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._admins = set()
        adapter._allowed_group_users = set()
        # The paired DM user passes the gateway authorization (pairing store).
        adapter.set_authorization_check(lambda *_a, **_kw: True)
        adapter._update_prompt_state[23] = {
            "session_key": "sess-up-23",
            "message_id": "msg_up_023",
            "chat_id": "oc_dm_chat",
        }
        data = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": 23},
            chat_id="oc_dm_chat",
            open_id="ou_dm_user",
        )
        adapter._sender_name_cache["ou_dm_user"] = ("DM User", 9999999999)

        with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_submitted_coro):
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is not None

    def test_empty_open_id_rejected_on_approval(self, _patch_callback_card_types):
        """Approval click without an operator identity is rejected (fail-closed)."""
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._admins = set()
        adapter._allowed_group_users = set()
        adapter._approval_state[24] = {
            "session_key": "sess-24",
            "message_id": "msg-24",
            "chat_id": "oc_dm_chat",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": 24},
            chat_id="oc_dm_chat",
            open_id="",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()
        assert 24 in adapter._approval_state

    def test_empty_open_id_rejected_on_update_prompt(self, _patch_callback_card_types):
        """Update prompt click without an operator identity is rejected (fail-closed)."""
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._admins = set()
        adapter._allowed_group_users = set()
        adapter._update_prompt_state[25] = {
            "session_key": "sess-up-25",
            "message_id": "msg_up_025",
            "chat_id": "oc_dm_chat",
        }
        data = _make_card_action_data(
            {"hermes_update_prompt_action": "y", "update_prompt_id": 25},
            chat_id="oc_dm_chat",
            open_id="",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()
        assert 25 in adapter._update_prompt_state

    def test_approval_card_forwarded_to_different_chat_rejected(self, _patch_callback_card_types):
        """Approval card forwarded out of its DM: chat mismatch rejects the click."""
        adapter = _make_adapter()
        adapter._loop = MagicMock()
        adapter._loop.is_closed = MagicMock(return_value=False)
        adapter._admins = set()
        adapter._allowed_group_users = set()
        adapter._approval_state[26] = {
            "session_key": "sess-26",
            "message_id": "msg-26",
            "chat_id": "oc_dm_chat",
        }
        data = _make_card_action_data(
            {"hermes_action": "approve_once", "approval_id": 26},
            chat_id="oc_forwarded_group",
            open_id="ou_dm_user",
        )

        with patch("asyncio.run_coroutine_threadsafe") as mock_submit:
            response = adapter._on_card_action_trigger(data)

        assert response is not None
        assert response.card is None
        mock_submit.assert_not_called()
        assert 26 in adapter._approval_state


class TestResolveUpdatePrompt:
    """Test update prompt resolution persists the response file."""

    @pytest.mark.asyncio
    async def test_writes_response_file(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        (tmp_path / ".hermes").mkdir()
        adapter.set_authorization_check(lambda *_a, **_kw: True)
        adapter._update_prompt_state[1] = {
            "session_key": "sess-up-1",
            "message_id": "msg_up_003",
            "chat_id": "oc_12345",
        }

        await adapter._resolve_update_prompt(1, "y", "Alice", open_id="ou_user1", chat_id="oc_12345")

        assert (tmp_path / ".hermes" / ".update_response").read_text() == "y"
        assert 1 not in adapter._update_prompt_state

    @pytest.mark.asyncio
    async def test_unauthorized_operator_does_not_write_response(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        (tmp_path / ".hermes").mkdir()
        adapter._allowed_group_users = {"ou_allowed"}
        adapter._group_policy = "open"
        adapter._default_group_policy = "open"
        adapter._update_prompt_state[2] = {
            "session_key": "sess-up-2",
            "message_id": "msg_up_004",
            "chat_id": "oc_12345",
        }

        await adapter._resolve_update_prompt(2, "y", "Mallory", open_id="ou_intruder", chat_id="oc_12345")

        assert not (tmp_path / ".hermes" / ".update_response").exists()
        assert 2 in adapter._update_prompt_state

    @pytest.mark.asyncio
    async def test_missing_operator_identity_does_not_write_response(self, tmp_path, monkeypatch):
        adapter = _make_adapter()
        monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
        (tmp_path / ".hermes").mkdir()
        adapter._allowed_group_users = {"ou_allowed"}
        adapter._update_prompt_state[3] = {
            "session_key": "sess-up-3",
            "message_id": "msg_up_005",
            "chat_id": "oc_12345",
        }

        await adapter._resolve_update_prompt(3, "y", "Anonymous", open_id="", chat_id="oc_12345")

        assert not (tmp_path / ".hermes" / ".update_response").exists()
        assert 3 in adapter._update_prompt_state




def test_resolved_card_keeps_the_request(_patch_callback_card_types):
    """Clicking a button replaces the card; the replacement still shows the command and reason."""
    import asyncio
    adapter = _make_adapter()
    response = SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="msg_9"))
    with patch.object(adapter, "_feishu_send_with_retry", new_callable=AsyncMock, return_value=response):
        asyncio.run(adapter.send_exec_approval(chat_id="oc_12345", command="rm -rf ./build",
                                               session_key="sess-9", description="dangerous deletion"))
    approval_id = next(iter(adapter._approval_state))
    adapter._loop = MagicMock()
    adapter._loop.is_closed = MagicMock(return_value=False)
    adapter._allowed_group_users = {"ou_bob"}
    adapter._sender_name_cache["ou_bob"] = ("Bob", 9999999999)
    data = _make_card_action_data({"hermes_action": "approve_once", "approval_id": approval_id}, open_id="ou_bob")
    with patch("asyncio.run_coroutine_threadsafe", side_effect=_close_submitted_coro):
        card = adapter._on_card_action_trigger(data).card.data
    content = card["elements"][0]["content"]
    assert "rm -rf ./build" in content and "dangerous deletion" in content and "Bob" in content


# ===========================================================================
# Plugin card clicks run inline: never queued behind, interrupting or starting a turn
# ===========================================================================

# Imported at collection: importing gateway.run inside a test trips the real-home I/O guard
# (hermes_bootstrap probes the checkout's parent for a payload manifest).
from gateway.run import GatewayRunner


def _fast_lane_runner(*, authorized: bool = True):
    """A bare GatewayRunner whose chat has an agent turn running; the running-turn path, the
    pending queue and a new agent turn are recorders the tests assert stay untouched."""
    from datetime import datetime

    from gateway.config import GatewayConfig, Platform
    from gateway.session import SessionEntry

    runner = object.__new__(GatewayRunner)
    runner.config = GatewayConfig(platforms={Platform.FEISHU: PlatformConfig(enabled=True)})
    runner.adapters = {}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), emit_collect=AsyncMock(return_value=[]), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key="k", session_id="sess-1", created_at=datetime.now(), updated_at=datetime.now(),
        platform=Platform.FEISHU, chat_type="group")
    runner._running_agents = {}
    runner._pending_messages = {}
    runner._session_db = None
    runner._is_user_authorized = lambda _source: authorized
    runner._set_session_env = lambda _context: None
    runner._is_session_running = lambda _key: True  # the chat's agent turn is running
    runner._hm_handle_running_session_message = AsyncMock(return_value=None)
    runner._handle_message_with_agent = AsyncMock(return_value=None)
    runner._queue_or_replace_pending_event = MagicMock()
    return runner


def _assert_no_turn_touched(runner):
    runner._hm_handle_running_session_message.assert_not_called()  # not queued, steered or interrupting
    runner._queue_or_replace_pending_event.assert_not_called()
    runner._handle_message_with_agent.assert_not_called()  # no agent turn started


class TestPluginCardClickFastLane:
    """A click whose /card command a plugin registered runs now, even while the chat is busy."""

    @staticmethod
    def _busy_adapter(runner):
        adapter = _make_adapter()
        adapter._allowed_group_users = frozenset({"ou_user1"})
        adapter.set_message_handler(runner._handle_message)
        adapter.set_busy_session_handler(AsyncMock(return_value=True))
        adapter._handle_message_while_active = AsyncMock()
        adapter._send_with_retry = AsyncMock(return_value=SimpleNamespace(success=True, message_id="om_reply"))
        # Every session of this adapter reads as busy (a turn or an earlier click holds the guard).
        busy_guard = asyncio.Event()
        busy_guard.set()
        adapter._active_sessions = MagicMock(__contains__=lambda _self, _key: True,
                                             __getitem__=lambda _self, _key: busy_guard)
        return adapter

    @staticmethod
    def _patched(adapter):
        return (
            patch.object(adapter, "_resolve_sender_profile", new_callable=AsyncMock,
                         return_value={"user_id": "ou_user1", "user_name": "Dave", "user_id_alt": None}),
            patch.object(adapter, "get_chat_info", new_callable=AsyncMock, return_value={"name": "Test Chat"}),
        )

    @staticmethod
    def _register(monkeypatch, handler):
        from hermes_cli import plugins as plugins_mod
        monkeypatch.setattr(plugins_mod, "get_plugin_command_handler",
                            lambda name: handler if name == "card" else None)

    @pytest.mark.asyncio
    async def test_busy_chat_click_runs_the_plugin_now_and_replies(self, monkeypatch):
        seen = []

        def handler(raw_args, *, source, session_id, session_key, platform):
            seen.append((raw_args, source.chat_id, session_key, platform))
            return "已改成「已解决」"

        self._register(monkeypatch, handler)
        runner = _fast_lane_runner()
        adapter = self._busy_adapter(runner)
        data = _make_card_action_data({"wa": "set_status", "id": 7}, token="c-tok1")
        profile, chat = self._patched(adapter)
        with profile, chat, patch.object(adapter, "_handle_message_with_guards",
                                         new_callable=AsyncMock) as guarded:
            await adapter._handle_card_action_event(data)

        assert len(seen) == 1
        raw_args, chat_id, session_key, platform = seen[0]
        assert (raw_args, chat_id, platform) == ('button {"wa": "set_status", "id": 7}', "oc_12345", "feishu")
        assert session_key.endswith("oc_12345")  # plugin session context names this chat's session
        guarded.assert_not_called()  # skipped the per-chat lock and the session guard
        adapter._handle_message_while_active.assert_not_called()  # no busy ack, no pending slot
        adapter._busy_session_handler.assert_not_called()
        adapter._send_with_retry.assert_awaited_once()  # the reply only: no "queued" ack
        sent = adapter._send_with_retry.call_args.kwargs
        assert sent["chat_id"] == "oc_12345" and sent["content"] == "已改成「已解决」"
        assert adapter._pending_messages == {} and runner._pending_messages == {}
        _assert_no_turn_touched(runner)

    @pytest.mark.asyncio
    async def test_unauthorized_clicker_does_not_run_the_plugin(self, monkeypatch):
        handler = MagicMock(return_value="ran")
        self._register(monkeypatch, handler)
        runner = _fast_lane_runner(authorized=False)
        adapter = self._busy_adapter(runner)
        profile, chat = self._patched(adapter)
        with profile, chat:
            await adapter._handle_card_action_event(_make_card_action_data({"wa": "x"}, token="c-tok2"))
        handler.assert_not_called()
        adapter._send_with_retry.assert_not_called()
        _assert_no_turn_touched(runner)

    @pytest.mark.asyncio
    async def test_duplicate_token_runs_once(self, monkeypatch):
        handler = MagicMock(return_value=None)
        self._register(monkeypatch, handler)
        adapter = self._busy_adapter(_fast_lane_runner())
        data = _make_card_action_data({"wa": "x"}, token="c-dup")
        profile, chat = self._patched(adapter)
        with profile, chat:
            await adapter._handle_card_action_event(data)
            await adapter._handle_card_action_event(data)
        handler.assert_called_once()
        adapter._send_with_retry.assert_not_called()  # an empty reply sends nothing

    @pytest.mark.asyncio
    async def test_concurrent_clicks_each_run_without_waiting(self, monkeypatch):
        """A second click lands while the first is still in its handler: both run, neither queues."""
        release = asyncio.Event()
        started = []

        async def handler(raw_args, *, source, session_id, session_key, platform):
            started.append(raw_args)
            if len(started) == 1:
                await release.wait()

        self._register(monkeypatch, handler)
        adapter = self._busy_adapter(_fast_lane_runner())
        profile, chat = self._patched(adapter)
        with profile, chat:
            first = asyncio.create_task(adapter._handle_card_action_event(
                _make_card_action_data({"n": 1}, token="c-a")))
            await asyncio.sleep(0.05)
            await asyncio.wait_for(adapter._handle_card_action_event(
                _make_card_action_data({"n": 2}, token="c-b")), timeout=2)
            release.set()
            await first
        assert len(started) == 2

    @pytest.mark.asyncio
    async def test_raising_handler_starts_no_turn(self, monkeypatch):
        def handler(*_a, **_k):
            raise RuntimeError("boom")

        self._register(monkeypatch, handler)
        runner = _fast_lane_runner()
        adapter = self._busy_adapter(runner)
        profile, chat = self._patched(adapter)
        with profile, chat:
            await adapter._handle_card_action_event(_make_card_action_data({"wa": "x"}, token="c-err"))
        _assert_no_turn_touched(runner)
        adapter._send_with_retry.assert_not_called()

    @pytest.mark.asyncio
    async def test_without_a_plugin_command_the_click_keeps_the_guarded_path(self, monkeypatch):
        self._register(monkeypatch, None)
        adapter = self._busy_adapter(_fast_lane_runner())
        adapter._dispatch_inline_reply = AsyncMock(side_effect=AssertionError("dispatched inline"))
        profile, chat = self._patched(adapter)
        with profile, chat, patch.object(adapter, "_handle_message_with_guards",
                                         new_callable=AsyncMock) as guarded:
            await adapter._handle_card_action_event(_make_card_action_data({"wa": "x"}, token="c-old"))
        guarded.assert_called_once()
        assert "/card button" in guarded.call_args[0][0].text

    @pytest.mark.asyncio
    async def test_fast_lane_runs_only_after_admission(self, monkeypatch):
        """A clicker the chat's admission gate rejects never reaches the plugin, busy chat or not."""
        handler = MagicMock(return_value="ran")
        self._register(monkeypatch, handler)
        runner = _fast_lane_runner()
        adapter = self._busy_adapter(runner)
        adapter._allowed_group_users = frozenset({"ou_owner"})  # ou_user1 is not on the allowlist
        adapter._message_handler = AsyncMock(wraps=adapter._message_handler)
        profile, chat = self._patched(adapter)
        with profile, chat:
            await adapter._handle_card_action_event(_make_card_action_data({"wa": "x"}, token="c-adm"))
        adapter._message_handler.assert_not_called()
        handler.assert_not_called()
