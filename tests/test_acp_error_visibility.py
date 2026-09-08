"""Tests: ACP prompt errors must surface to Telegram (never silently swallowed)."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from agents_on_hand.drivers.acp_driver import ACPDriver, format_prompt_error
from agents_on_hand.drivers.base_driver import DriverEvent
from agents_on_hand.stream_handler import UnifiedStreamer

# ---------- format_prompt_error classification ----------


def test_format_prompt_error_timeout():
    msg = format_prompt_error(asyncio.TimeoutError())
    assert "逾時" in msg
    assert "/aoh_esc" in msg


def test_format_prompt_error_busy_internal():
    # prime-agent: -32603 "A prompt turn is already running for this ACP session"
    exc = RuntimeError(
        {
            "code": -32603,
            "message": "Internal error",
            "data": {"details": "A prompt turn is already running for this ACP session"},
        }
    )
    msg = format_prompt_error(exc)
    assert "忙碌" in msg
    assert "already running" in msg


def test_format_prompt_error_busy_agent_busy_code():
    # newer prime-agent: -32600 turn.agent_busy
    exc = RuntimeError(
        {
            "code": -32600,
            "message": "Invalid request: another turn is already in progress",
            "data": {"code": "turn.agent_busy"},
        }
    )
    msg = format_prompt_error(exc)
    assert "忙碌" in msg


def test_format_prompt_error_generic():
    msg = format_prompt_error(RuntimeError("boom"))
    assert "ACP prompt 失敗" in msg
    assert "boom" in msg


def test_format_prompt_error_empty_exception_shows_type():
    msg = format_prompt_error(ValueError())
    assert "ValueError" in msg


# ---------- ACPDriver.send_prompt emits ERROR events ----------


def _make_driver() -> ACPDriver:
    return ACPDriver(command="fake-agent --mode acp", working_dir=Path("/tmp"))


@pytest.mark.asyncio
async def test_send_prompt_emits_error_on_busy():
    driver = _make_driver()
    exc = RuntimeError(
        {
            "code": -32603,
            "message": "Internal error",
            "data": {"details": "A prompt turn is already running for this ACP session"},
        }
    )
    client = MagicMock()
    client.prompt = AsyncMock(side_effect=exc)
    driver.client = client
    driver.is_running = True

    events: list[DriverEvent] = []
    driver.register_listener(events.append)

    driver.send_prompt("進度？")
    await asyncio.sleep(0.05)

    types = [e.event_type for e in events]
    assert types == [DriverEvent.ERROR, DriverEvent.TURN_END]
    assert "忙碌" in events[0].content


@pytest.mark.asyncio
async def test_send_prompt_no_error_event_on_success():
    driver = _make_driver()
    client = MagicMock()
    client.prompt = AsyncMock(return_value={})
    driver.client = client
    driver.is_running = True

    events: list[DriverEvent] = []
    driver.register_listener(events.append)

    driver.send_prompt("hello")
    await asyncio.sleep(0.05)

    assert [e.event_type for e in events] == [DriverEvent.TURN_END]


@pytest.mark.asyncio
async def test_send_prompt_not_running_emits_error():
    driver = _make_driver()
    driver.client = None
    driver.is_running = False

    events: list[DriverEvent] = []
    driver.register_listener(events.append)

    driver.send_prompt("進度？")
    await asyncio.sleep(0.01)

    types = [e.event_type for e in events]
    assert types == [DriverEvent.ERROR, DriverEvent.TURN_END]
    assert "未在運行" in events[0].content


# ---------- UnifiedStreamer delivers ERROR to Telegram ----------


def _make_streamer() -> tuple[UnifiedStreamer, MagicMock]:
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
    bot.edit_message_text = AsyncMock()
    bot.send_chat_action = AsyncMock()
    sess = MagicMock()
    sess.session_id = "test_err_sess"
    sess.agent_name = "Prime Agent"
    sess.register_listener = MagicMock()
    sess.unregister_listener = MagicMock()
    streamer = UnifiedStreamer(bot=bot, chat_id=123, session=sess, edit_interval=0.01)
    return streamer, bot


@pytest.mark.asyncio
async def test_streamer_delivers_error_message():
    streamer, bot = _make_streamer()
    streamer.start()
    try:
        streamer._on_driver_event(DriverEvent(DriverEvent.ERROR, content="⚠️ Agent 忙碌中：測試"))
        await asyncio.sleep(0.05)
        err_call = next(
            c for c in bot.send_message.call_args_list if "忙碌" in c.kwargs.get("text", "")
        )
        # plain text: agent-provided details must never break Telegram formatting
        assert err_call.kwargs.get("parse_mode") is None
    finally:
        streamer.stop()


@pytest.mark.asyncio
async def test_streamer_ignores_empty_error_content():
    streamer, bot = _make_streamer()
    streamer.start()
    try:
        streamer._on_driver_event(DriverEvent(DriverEvent.ERROR, content="   "))
        await asyncio.sleep(0.05)
        assert not bot.send_message.called
    finally:
        streamer.stop()
