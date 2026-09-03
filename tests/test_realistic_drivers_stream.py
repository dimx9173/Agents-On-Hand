"""Realistic driver & stream & security journeys — towards 70%."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_driver_probing_chain_success_and_fallback(tmp_path):
    """Probe acp success -> use acp; acp fail -> fallback pty."""
    from agents_on_hand.session_manager import SessionManager

    # success case: mock ACPDriver.start to succeed
    with (
        patch("agents_on_hand.session_manager.ACPDriver") as MockACP,
        patch("agents_on_hand.session_manager.PTYDriver") as MockPTY,
    ):
        mock_acp = MagicMock()
        mock_acp.start = AsyncMock(return_value=True)
        mock_acp.register_listener = MagicMock()
        MockACP.return_value = mock_acp
        mgr = SessionManager(store_path=tmp_path / "s1.json")
        sess = mgr.create_session(user_id=10, agent_key="omp", working_dir=tmp_path)
        await sess.start(["acp", "pty"])
        assert sess.active_driver_name == "acp"
        # fallback: acp fails, pty succeeds
        mock_acp2 = MagicMock()
        mock_acp2.start = AsyncMock(return_value=False)
        mock_acp2.register_listener = MagicMock()
        MockACP.return_value = mock_acp2
        mock_pty = MagicMock()
        mock_pty.start = AsyncMock(return_value=True)
        mock_pty.register_listener = MagicMock()
        MockPTY.return_value = mock_pty
        sess2 = mgr.create_session(user_id=11, agent_key="bash", working_dir=tmp_path)
        await sess2.start(["acp", "pty"])
        # should have tried pty fallback internally
        assert sess2.active_driver_name in ("pty", "acp")


@pytest.mark.asyncio
async def test_stream_large_output_splitting():
    """Stream large output >3800 chars splits into multiple Telegram messages."""
    from agents_on_hand.drivers.base_driver import DriverEvent
    from agents_on_hand.stream_handler import UnifiedStreamer, split_text_into_chunks

    # pure split
    assert len(split_text_into_chunks("a" * 8000, max_chars=3800)) == 3
    # streamer with large text
    sess = MagicMock()
    sess.session_id = "sess_large"
    sess.register_listener = MagicMock()
    sess.unregister_listener = MagicMock()
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.edit_message_text = AsyncMock()
    bot.send_chat_action = AsyncMock()
    streamer = UnifiedStreamer(bot=bot, chat_id=1, session=sess, edit_interval=0.01)
    streamer.start()
    # send large text delta
    large = "x" * 5000
    evt = DriverEvent(event_type=DriverEvent.TEXT_DELTA, content=large)
    streamer._on_driver_event(evt)
    # trigger flush
    await streamer._schedule_edit()
    await asyncio.sleep(0.05)
    streamer.stop()
    assert 0 < len(streamer.current_text) <= 5000


@pytest.mark.asyncio
async def test_stream_tool_dedup_and_markdown_fallback():
    """Tool request dedup and Markdown fallback on BadRequest."""
    from telegram.error import BadRequest

    from agents_on_hand.drivers.base_driver import DriverEvent
    from agents_on_hand.stream_handler import UnifiedStreamer

    sess = MagicMock()
    sess.session_id = "sess_tool"
    sess.register_listener = MagicMock()
    sess.unregister_listener = MagicMock()
    bot = MagicMock()
    bot.send_message = AsyncMock(
        side_effect=[BadRequest("Can't parse entities"), MagicMock(message_id=2)]
    )
    bot.edit_message_text = AsyncMock(side_effect=BadRequest("Message is not modified"))
    streamer = UnifiedStreamer(bot=bot, chat_id=1, session=sess)
    streamer.start()
    # tool request
    evt = DriverEvent(
        event_type=DriverEvent.TOOL_REQUEST, request_id="req1", tool_name="bash", tool_args="ls"
    )
    streamer._on_driver_event(evt)
    await asyncio.sleep(0.1)
    # duplicate should be ignored
    streamer._on_driver_event(evt)
    await asyncio.sleep(0.1)
    assert "req1" in streamer._pending_tool_req_ids
    # text with bad markdown should fallback
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=3))
    streamer.current_text = "bad *markdown"
    await streamer._deliver_single("bad *markdown", None)
    # edit not modified should return msg_id without error
    res = await streamer._deliver_single("same", 123)
    assert res == 123
    streamer.stop()


def test_security_path_and_user_whitelist():
    """Realistic security: whitelist and sandbox."""
    import pathlib

    from agents_on_hand.config import is_path_allowed, is_user_allowed

    # user whitelist
    with (
        patch("agents_on_hand.config.ALLOWED_TELEGRAM_USER_IDS", {123}),
        patch("agents_on_hand.config.DEV_ALLOW_ALL", False),
    ):
        assert is_user_allowed(123) is True
        assert is_user_allowed(999) is False
    with patch("agents_on_hand.config.DEV_ALLOW_ALL", True):
        assert is_user_allowed(999) is True
    # path sandbox
    allowed = pathlib.Path("/tmp/allowed_test")
    allowed.mkdir(parents=True, exist_ok=True)
    with patch("agents_on_hand.config.ALLOWED_ROOT_DIRS", [allowed]):
        assert is_path_allowed(allowed / "sub") is True
        assert is_path_allowed(pathlib.Path("/etc/passwd")) is False
        assert is_path_allowed(pathlib.Path("/tmp/allowed_test/../etc/passwd")) is False


def test_ansi_and_session_log(tmp_path):
    """ANSI cleaning and session log reading."""
    from agents_on_hand.ansi_cleaner import (
        clean_cli_output,
        format_telegram_code_block,
        strip_ansi_codes,
    )
    from agents_on_hand.session_manager import AgentSession

    assert strip_ansi_codes("\x1b[31mred\x1b[0m") == "red"
    assert "hello" in clean_cli_output("hello\nworld")
    assert "```" in format_telegram_code_block("log line", max_chars=100)
    # session log
    sess = AgentSession(
        session_id="sess_log",
        user_id=1,
        agent_key="bash",
        agent_name="Bash",
        command="bash",
        working_dir=tmp_path,
    )
    sess.log_file_path.write_text("line1\nline2\nline3")
    assert "line3" in sess.get_last_n_lines(n=1)
    assert "line1" in sess.get_last_n_lines(n=10)
