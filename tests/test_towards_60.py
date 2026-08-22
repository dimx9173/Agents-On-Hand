import pathlib
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

@pytest.mark.asyncio
async def test_session_manager_custom_command(tmp_path):
    from agents_on_hand.session_manager import SessionManager
    mgr = SessionManager(store_path=tmp_path / "state.json")
    sess = mgr.create_session(user_id=5, agent_key="custom_xyz", working_dir=tmp_path, custom_command="echo hello")
    assert sess.command == "echo hello"
    assert sess.agent_name.startswith("Custom")
    assert mgr.set_active_session(5, sess.session_id) is True
    assert mgr.set_active_session(5, "nonexistent") is False
    assert mgr.get_session("nonexistent") is None
    assert mgr.get_active_session(999) is None
    assert mgr.list_user_sessions(999) == []
    assert mgr.kill_session("nonexistent") is False
    assert mgr.get_session(sess.session_id) is not None
    # prune with no offline
    sess.is_running = True
    assert mgr.prune_offline_sessions(5) == 0
    sess.is_running = False
    assert mgr.prune_offline_sessions(5) == 1

def test_session_manager_handle_exit(tmp_path):
    from agents_on_hand.session_manager import SessionManager, AgentSession
    from agents_on_hand.drivers.base_driver import DriverEvent
    mgr = SessionManager(store_path=tmp_path / "state.json")
    cb_calls = []
    mgr.register_on_finished_callback(lambda s: cb_calls.append(s.session_id))
    s = AgentSession(session_id="sess_cb", user_id=1, agent_key="bash", agent_name="Bash", command="bash", working_dir=tmp_path, on_exit_callback=mgr._handle_session_exit)
    # simulate exit event
    s._on_driver_event(DriverEvent(event_type=DriverEvent.EXIT, content="exit"))
    assert s.is_running is False

@pytest.mark.asyncio
async def test_stream_handler_render_and_split():
    from agents_on_hand.stream_handler import split_text_into_chunks, UnifiedStreamer
    from agents_on_hand.drivers.base_driver import DriverEvent
    assert split_text_into_chunks("a"*100, max_chars=10) == ["a"*10]*10
    sess = MagicMock()
    sess.session_id = "sess_test"
    sess.register_listener = MagicMock()
    sess.unregister_listener = MagicMock()
    bot = MagicMock()
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=123))
    bot.edit_message_text = AsyncMock()
    bot.send_chat_action = AsyncMock()
    streamer = UnifiedStreamer(bot=bot, chat_id=1, session=sess, edit_interval=0.01)
    streamer.start()
    assert sess.register_listener.called
    # text delta
    evt = DriverEvent(event_type=DriverEvent.TEXT_DELTA, content="hello")
    streamer._on_driver_event(evt)
    assert "hello" in streamer.current_text
    # thought delta
    evt2 = DriverEvent(event_type=DriverEvent.THOUGHT_DELTA, content="think")
    streamer._on_driver_event(evt2)
    assert "think" in streamer.current_thought
    # tool result
    evt3 = DriverEvent(event_type=DriverEvent.TOOL_RESULT, content="output", tool_name="bash")
    streamer._on_driver_event(evt3)
    assert "Tool" in streamer.current_text or "bash" in streamer.current_text
    streamer.stop()
    assert sess.unregister_listener.called

def test_ansi_cleaner_edge():
    from agents_on_hand.ansi_cleaner import strip_ansi_codes, clean_cli_output
    assert strip_ansi_codes("") == ""
    assert strip_ansi_codes("no ansi") == "no ansi"
    assert "hello" in clean_cli_output("\x1b[31mhello\x1b[0m world")
