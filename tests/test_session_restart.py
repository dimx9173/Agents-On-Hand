"""Regression tests for offline-session resume (sess:restart).

Bugs locked in: the 🔄 button emitted `sess:restart:{id}` with no handler
branch (taps did nothing), and the fire-and-forget probe left the UI stuck on
"正在重連" with no success/failure feedback.
"""

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents_on_hand.session_manager import AgentSession, SessionManager


def _offline_session(tmp_path: Path) -> AgentSession:
    with patch("agents_on_hand.session_manager.SessionTraceLogger"):
        s = AgentSession(
            session_id="sess_r1",
            user_id=1,
            agent_key="prime",
            agent_name="Prime Agent",
            command="prime-agent --mode acp",
            working_dir=tmp_path,
            acp_session_id="acp-ctx-1",
        )
    s.is_running = False
    return s


def _text_of(call) -> str:
    return call.args[0] if call.args else call.kwargs.get("text", "")


def test_registry_is_acp_only_plus_bash_with_load_session_flags():
    from agents_on_hand.config import AVAILABLE_CLI_AGENTS

    assert {"kimi", "gemini", "qwen", "hermes", "omp", "opencode", "openclaw", "prime"} <= set(
        AVAILABLE_CLI_AGENTS
    )
    assert not {"claude", "codex", "pi"} & set(AVAILABLE_CLI_AGENTS)
    for key, info in AVAILABLE_CLI_AGENTS.items():
        assert info["use_acp"] or key == "bash", f"non-ACP agent {key} must not enter registry"
        assert isinstance(info["load_session"], bool), key


def test_kimi_resume_skipped_when_agent_lacks_load_session():
    tmp = Path("/tmp")
    with patch("agents_on_hand.session_manager.SessionTraceLogger"):
        prime = AgentSession("sess_p", 1, "prime", "Prime Agent", "prime-agent --mode acp", tmp)
        kim = AgentSession("sess_k", 1, "kimi", "Kimi Code", "kimi acp", tmp)
    for s in (prime, kim):
        s.resume_acp_session_id = "ctx-1"
        s.acp_session_id = "ctx-1"

    seen = {}

    class SpyACP:
        pid = 1

        def __init__(self, cmd, wd, resume_session_id=None):
            seen["resume"] = resume_session_id

        def set_trace(self, t):
            pass

        def register_listener(self, cb):
            pass

        async def start(self):
            return True

    import agents_on_hand.session_manager as sm

    with patch.dict(sm.DRIVER_MAP, {"acp": SpyACP, "pty": MagicMock()}):
        asyncio.run(kim.start(["acp"]))
        assert seen["resume"] == "ctx-1", "kimi (load_session=True) must attempt session/load"
        seen.clear()
        asyncio.run(prime.start(["acp"]))
        assert seen["resume"] is None, "prime (load_session=False) must start fresh"


@pytest.mark.asyncio
async def test_restart_session_reuses_id_and_returns_probe_task():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with patch("agents_on_hand.session_manager.SessionTraceLogger"):
            mgr = SessionManager(store_path=tmp / "state.json")
        sess = _offline_session(tmp)
        mgr.sessions[sess.session_id] = sess
        with (
            patch("agents_on_hand.config.is_path_allowed", return_value=True),
            patch.object(AgentSession, "start", new=AsyncMock(return_value=True)) as start_mock,
        ):
            s, reason, probe = mgr.restart_session(sess.session_id)
            assert s is sess
            assert reason == "ok"
            assert s is not None and s.resume_acp_session_id == "acp-ctx-1"
            assert probe is not None
            assert await probe is True
            start_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_restart_session_guards_running_starting_and_sandbox():
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        with patch("agents_on_hand.session_manager.SessionTraceLogger"):
            mgr = SessionManager(store_path=tmp / "state.json")
        sess = _offline_session(tmp)
        mgr.sessions[sess.session_id] = sess

        s, reason, probe = mgr.restart_session("sess_missing")
        assert (s, reason, probe) == (None, "not_found", None)

        sess.is_running = True
        s, reason, probe = mgr.restart_session(sess.session_id)
        assert (s, reason, probe) == (None, "already_running", None)
        sess.is_running = False

        sess.is_starting = True
        s, reason, probe = mgr.restart_session(sess.session_id)
        assert (s, reason, probe) == (None, "already_running", None)
        sess.is_starting = False

        with patch("agents_on_hand.config.is_path_allowed", return_value=False):
            s, reason, probe = mgr.restart_session(sess.session_id)
        assert (s, reason, probe) == (None, "sandbox", None)


def _restart_query():
    q = MagicMock()
    q.answer = AsyncMock()
    q.data = "sess:restart:sess_r1"
    q.from_user = MagicMock(id=1)
    q.message = MagicMock()
    q.message.chat_id = 77
    q.message.message_id = 5
    q.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = q
    return q, update


async def _dispatch(update, ctx):
    from agents_on_hand.ui.session_menu import session_action_callback_handler

    await session_action_callback_handler(update, ctx)


@pytest.mark.asyncio
async def test_restart_handler_reports_success_after_probe():
    q, update = _restart_query()
    sess = MagicMock()
    sess.agent_name = "Prime Agent"
    sess.working_dir = Path("/tmp/x")
    sess.active_driver_name = "acp"
    probe = asyncio.Future()
    probe.set_result(True)
    ctx = MagicMock()
    ctx.bot.edit_message_text = AsyncMock()
    with (
        patch("agents_on_hand.ui.session_menu.session_manager") as sm,
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch("agents_on_hand.ui.session_menu.create_streamer_for_session") as mk,
    ):
        sm.restart_session.return_value = (sess, "ok", probe)
        mk.return_value = MagicMock()
        await _dispatch(update, ctx)
        await asyncio.sleep(0.05)
    ack_texts = [_text_of(c) for c in q.edit_message_text.call_args_list]
    assert any("正在 Resume" in t for t in ack_texts)
    final = ctx.bot.edit_message_text.call_args.kwargs["text"]
    assert "✅" in final and "Resumed" in final


@pytest.mark.asyncio
async def test_restart_handler_reports_failure_after_probe():
    q, update = _restart_query()
    sess = MagicMock()
    sess.agent_name = "Prime Agent"
    sess.working_dir = Path("/tmp/x")
    sess.active_driver_name = "none"
    probe = asyncio.Future()
    probe.set_result(False)
    ctx = MagicMock()
    ctx.bot.edit_message_text = AsyncMock()
    with (
        patch("agents_on_hand.ui.session_menu.session_manager") as sm,
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
        patch("agents_on_hand.ui.session_menu.create_streamer_for_session") as mk,
    ):
        sm.restart_session.return_value = (sess, "ok", probe)
        mk.return_value = MagicMock()
        await _dispatch(update, ctx)
        await asyncio.sleep(0.05)
    final = ctx.bot.edit_message_text.call_args.kwargs["text"]
    assert "❌" in final and "失敗" in final


@pytest.mark.asyncio
async def test_restart_handler_already_running_answers_only():
    q, update = _restart_query()
    ctx = MagicMock()
    ctx.bot.edit_message_text = AsyncMock()
    with (
        patch("agents_on_hand.ui.session_menu.session_manager") as sm,
        patch("agents_on_hand.security.is_user_allowed", return_value=True),
    ):
        sm.restart_session.return_value = (None, "already_running", None)
        await _dispatch(update, ctx)
    assert q.answer.await_count >= 1
    final_call = q.answer.call_args_list[-1]
    assert final_call.kwargs.get("show_alert") is True
    ctx.bot.edit_message_text.assert_not_called()
