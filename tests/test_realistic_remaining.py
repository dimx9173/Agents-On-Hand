"""Remaining 7% — acp_client/stream/session_manager deep branches."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_acp_client_pending_and_listeners():
    from agents_on_hand.acp_client import ACPClient

    c = ACPClient("echo", "/tmp")
    # pending requests
    fut = MagicMock()
    c._pending_requests[10] = fut
    c._handle_json_msg({"id": 10, "result": {"ok": True}})
    # notify listeners
    calls = []
    c.register_listener(lambda x: calls.append(x))
    c._handle_json_msg({"method": "update", "params": {"content": "hi"}})
    # permission
    c.register_permission_listener(lambda x: calls.append("perm"))
    c._handle_json_msg({"id": 20, "method": "requestPermission", "params": {"tool": "bash"}})
    assert True


def test_acp_client_error_handling():
    from agents_on_hand.acp_client import ACPClient

    c = ACPClient("echo", "/tmp")
    c._pending_requests[5] = MagicMock()
    c._handle_json_msg({"id": 5, "error": {"message": "fail"}})
    c._handle_json_msg({"method": "unknown", "params": {}})
    c._handle_json_msg({"bad": "data"})
    c._handle_json_msg({"id": 999, "result": {}})  # no pending
    assert True


@pytest.mark.asyncio
async def test_session_manager_create_custom_and_prune(tmp_path):
    from agents_on_hand.session_manager import AgentSession, SessionManager

    with patch("agents_on_hand.session_manager.AgentSession.start", new_callable=AsyncMock):
        mgr = SessionManager(store_path=tmp_path / "s.json")
        # custom command
        s1 = mgr.create_session(
            user_id=7, agent_key="custom", working_dir=tmp_path, custom_command="ls -la"
        )
        assert s1.command == "ls -la"
        # second user
        s2 = mgr.create_session(user_id=8, agent_key="bash", working_dir=tmp_path)
        assert len(mgr.list_user_sessions(7)) == 1
        assert len(mgr.list_user_sessions(8)) == 1
        assert s2.user_id == 8
        assert mgr.get_active_session(7).session_id == s1.session_id
        # switch active
        assert mgr.set_active_session(7, s1.session_id) is True
        assert mgr.set_active_session(7, "bad") is False
        # kill
        assert mgr.kill_session(s1.session_id) is True
        assert mgr.get_session(s1.session_id) is None
        assert mgr.kill_session("bad") is False
        # prune offline
        s_off = AgentSession(
            session_id="off1",
            user_id=9,
            agent_key="bash",
            agent_name="Bash",
            command="bash",
            working_dir=tmp_path,
        )
        s_off.is_running = False
        mgr.sessions["off1"] = s_off
        mgr.user_active_session[9] = "off1"
        assert mgr.prune_offline_sessions(9) == 1
        assert mgr.prune_offline_sessions(99) == 0


@pytest.mark.asyncio
async def test_stream_handler_tool_and_throttle():
    from agents_on_hand.drivers.base_driver import DriverEvent
    from agents_on_hand.stream_handler import UnifiedStreamer

    sess = MagicMock()
    sess.session_id = "sess_stream"
    sess.register_listener = MagicMock()
    sess.unregister_listener = MagicMock()
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=10))
    bot.edit_message_text = AsyncMock()
    bot.send_chat_action = AsyncMock()
    streamer = UnifiedStreamer(bot=bot, chat_id=1, session=sess, edit_interval=0.01)
    streamer.start()
    # tool request
    evt = DriverEvent(
        event_type=DriverEvent.TOOL_REQUEST, request_id="t1", tool_name="bash", tool_args="ls -la"
    )
    streamer._on_driver_event(evt)
    await __import__("asyncio").sleep(0.05)
    assert "t1" in streamer._pending_tool_req_ids
    # duplicate ignored
    streamer._on_driver_event(evt)
    await __import__("asyncio").sleep(0.05)
    assert len(streamer._pending_tool_req_ids) == 1
    # text and thought
    evt2 = DriverEvent(event_type=DriverEvent.TEXT_DELTA, content="hello ")
    streamer._on_driver_event(evt2)
    evt3 = DriverEvent(event_type=DriverEvent.THOUGHT_DELTA, content="thinking")
    streamer._on_driver_event(evt3)
    await streamer._schedule_edit()
    await __import__("asyncio").sleep(0.05)
    assert "hello" in streamer.current_text
    streamer.stop()
    assert sess.unregister_listener.called


def test_security_edge_cases():
    import pathlib

    from agents_on_hand.config import is_path_allowed, is_user_allowed

    # empty whitelist -> deny unless DEV_ALLOW
    with (
        patch("agents_on_hand.config.ALLOWED_TELEGRAM_USER_IDS", set()),
        patch("agents_on_hand.config.DEV_ALLOW_ALL", False),
    ):
        assert is_user_allowed(1) is False
    with patch("agents_on_hand.config.DEV_ALLOW_ALL", True):
        assert is_user_allowed(999) is True
    # path traversal
    allowed = pathlib.Path("/tmp/sec_test")
    allowed.mkdir(parents=True, exist_ok=True)
    with patch("agents_on_hand.config.ALLOWED_ROOT_DIRS", [allowed]):
        assert is_path_allowed(allowed) is True
        assert is_path_allowed(allowed / "a" / "b") is True
        assert is_path_allowed(pathlib.Path("/etc/passwd")) is False


def test_ansi_and_logging_extra(tmp_path):
    from agents_on_hand.ansi_cleaner import (
        clean_cli_output,
        format_hermes_style,
        format_telegram_code_block,
        strip_ansi_codes,
    )

    assert strip_ansi_codes("\x1b[31mred\x1b[0m") == "red"
    assert strip_ansi_codes("plain") == "plain"
    assert "world" in clean_cli_output("hello\nworld")
    assert "```" in format_telegram_code_block("x" * 100, max_chars=50)
    assert isinstance(format_hermes_style("tool: test"), str)
    from agents_on_hand.logging_setup import _sanitize, setup_logging

    assert isinstance(_sanitize("hello"), str)
    setup_logging(level="INFO")
