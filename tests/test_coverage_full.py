import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


def test_claude_init_variants():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver
    d1 = ClaudeStreamDriver("claude", Path("/tmp"))
    assert "--output-format=stream-json" in d1.command
    assert "--verbose" in d1.command
    d2 = ClaudeStreamDriver("claude -p --output-format=stream-json", Path("/tmp"))
    assert "--verbose" in d2.command
    d3 = ClaudeStreamDriver("claude -p --verbose --output-format=stream-json", Path("/tmp"))
    assert d3.command == "claude -p --verbose --output-format=stream-json"
    d3.stop()


def test_claude_handle_json_msg_all_branches():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver, DriverEvent
    d = ClaudeStreamDriver("claude", Path("/tmp"))
    events = []
    d._listeners = [lambda e: events.append(e)]
    d._handle_json_msg({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}})
    assert any(e.content == "hello" for e in events)
    events.clear()
    d._handle_json_msg({"type": "assistant", "message": {"content": [{"type": "thinking", "thinking": "think"}]}})
    assert any(e.event_type == DriverEvent.THOUGHT_DELTA for e in events)
    events.clear()
    d._handle_json_msg({"type": "assistant", "message": {"content": [{"type": "thought", "thinking": "t2"}]}})
    events.clear()
    d._handle_json_msg({"type": "assistant", "message": {}})
    d._handle_json_msg({"type": "assistant"})
    d._handle_json_msg({"type": "assistant", "message": {"content": "not a list"}})
    d._handle_json_msg({"type": "assistant", "message": {"content": [{"type": "text", "text": ""}]}})
    d._handle_json_msg({"type": "assistant", "message": {"content": [{"type": "tool", "text": "x"}]}})
    d._handle_json_msg({"type": "assistant", "message": {"content": [{"text": "no type"}]}})
    d._handle_json_msg({"type": "assistant", "message": {"content": ["not dict"]}})
    d._handle_json_msg({"type": "result", "result": "ok result"})
    assert any("ok result" in e.content for e in events)
    events.clear()
    d._handle_json_msg({"type": "result", "result": ""})
    d._handle_json_msg({"type": "result"})
    d._handle_json_msg({"type": "result", "result": 123})
    d._handle_json_msg({"type": "text", "delta": {"text": "delta text"}})
    d._handle_json_msg({"type": "content_block_delta", "delta": {"text": "cb"}})
    d._handle_json_msg({"type": "text", "delta": "string delta"})
    d._handle_json_msg({"type": "text", "delta": {"text": ""}, "text": "fallback"})
    d._handle_json_msg({"type": "text", "delta": {}, "text": ""})
    d._handle_json_msg({"type": "text", "text": "direct"})
    events.clear()
    d._handle_json_msg({"type": "thinking", "thinking": "think1"})
    d._handle_json_msg({"type": "thought", "text": "think2"})
    d._handle_json_msg({"type": "thinking", "thinking": ""})
    events.clear()
    d._handle_json_msg({"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}, "id": "req1"})
    assert any(e.event_type == DriverEvent.TOOL_REQUEST for e in events)
    events.clear()
    d._handle_json_msg({"type": "tool_use"})
    d._handle_json_msg({"type": "system", "subtype": "init"})
    d._handle_json_msg({"type": "unknown"})
    d._handle_json_msg({})
    d._handle_json_msg({"type": None})


@pytest.mark.asyncio
async def test_claude_start_success_and_fail():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver
    d = ClaudeStreamDriver("claude", Path("/tmp"))
    mock_process = MagicMock()
    mock_process.stdout = AsyncMock()
    mock_process.stderr = AsyncMock()
    mock_process.stdin = MagicMock()
    mock_process.stdin.drain = AsyncMock()
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process) as mock_exec:
        with patch("asyncio.create_task") as mock_task:
            mock_task.return_value = MagicMock()
            result = await d.start()
            assert result is True
            assert d.is_running is True
            mock_exec.assert_called_once()
    d.stop()
    d2 = ClaudeStreamDriver("claude", Path("/tmp"))
    with patch("asyncio.create_subprocess_exec", side_effect=Exception("fail")):
        result = await d2.start()
        assert result is False
        assert d2.is_running is False


@pytest.mark.asyncio
async def test_claude_read_loop():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver
    d = ClaudeStreamDriver("claude", Path("/tmp"))
    d.is_running = True
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=[
        b'{"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]}}\n',
        b'not json line\n',
        b'   \n',
        b'{"type": "result", "result": "done"}\n',
        b'',
    ])
    mock_process = MagicMock()
    mock_process.stdout = mock_stdout
    d.process = mock_process
    events = []
    d._listeners = [lambda e: events.append(e)]
    await d._read_loop()
    assert d.is_running is False
    assert any(e.event_type == "exit" for e in events)


@pytest.mark.asyncio
async def test_claude_read_loop_cancel():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver
    d = ClaudeStreamDriver("claude", Path("/tmp"))
    d.is_running = True
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=asyncio.CancelledError())
    mock_process = MagicMock()
    mock_process.stdout = mock_stdout
    d.process = mock_process
    await d._read_loop()
    assert d.is_running is False


@pytest.mark.asyncio
async def test_claude_read_loop_exception():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver
    d = ClaudeStreamDriver("claude", Path("/tmp"))
    d.is_running = True
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=Exception("boom"))
    mock_process = MagicMock()
    mock_process.stdout = mock_stdout
    d.process = mock_process
    await d._read_loop()
    assert d.is_running is False


def test_claude_send_and_stop():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver
    d = ClaudeStreamDriver("claude", Path("/tmp"))
    d.send_prompt("hello")
    d.send_control_char("\x03")
    mock_process = MagicMock()
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_process.stdin = mock_stdin
    d.process = mock_process
    d.is_running = True
    with patch("asyncio.create_task") as mock_ct:
        d.send_prompt("hi")
        mock_stdin.write.assert_called()
        mock_ct.assert_called()
    d.send_control_char("\x1b")
    mock_process.terminate.assert_called()
    d.process.stdin = mock_stdin
    d.is_running = True
    with patch.object(d.process.stdin, 'drain', new_callable=AsyncMock):
        asyncio.run(d.respond_permission("req1", True))
        asyncio.run(d.respond_permission("req1", False))
    d.process.stdin = None
    asyncio.run(d.respond_permission("req1", True))
    d.is_running = False
    asyncio.run(d.respond_permission("req1", True))
    mock_process.terminate.side_effect = Exception("err")
    d.process = mock_process
    d.stop()
    d.process = None
    d.stop()


def test_pi_init_and_handle():
    from agents_on_hand.drivers.pi_rpc_driver import PiRPCDriver
    d = PiRPCDriver("pi", Path("/tmp"))
    assert "--mode rpc" in d.command
    d2 = PiRPCDriver("pi --mode rpc", Path("/tmp"))
    assert d2.command == "pi --mode rpc"
    events = []
    d._listeners = [lambda e: events.append(e)]
    d._handle_json_msg({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}})
    assert any("hi" in e.content for e in events)
    events.clear()
    d._handle_json_msg({"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "think"}})
    d._handle_json_msg({"type": "message_update", "assistantMessageEvent": {"type": "thinking", "delta": "think2"}})
    events.clear()
    d._handle_json_msg({"type": "message_update", "assistantMessageEvent": "not dict"})
    d._handle_json_msg({"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": ""}})
    d._handle_json_msg({"type": "message_update", "assistantMessageEvent": {"type": "other", "delta": "x"}})
    d._handle_json_msg({"type": "message_update"})
    d._handle_json_msg({"type": "extension_ui_request", "method": "confirm", "id": "1", "statusText": "ok"})
    assert any(e.event_type == "tool_request" for e in events)
    events.clear()
    d._handle_json_msg({"type": "extension_ui_request", "method": "select", "id": "2"})
    d._handle_json_msg({"type": "extension_ui_request", "method": "input", "id": "3"})
    d._handle_json_msg({"type": "extension_ui_request", "method": "editor", "id": "4"})
    d._handle_json_msg({"type": "extension_ui_request", "method": "setStatus", "id": "5"})
    d._handle_json_msg({"type": "extension_ui_request", "method": "", "id": "6"})
    events.clear()
    d._handle_json_msg({"type": "extension_ui_request"})
    d._handle_json_msg({"type": "turn_end"})
    d._handle_json_msg({"type": "agent_end"})
    d._handle_json_msg({"type": "agent_settled"})
    assert any(e.event_type == "turn_end" for e in events)
    events.clear()
    d._handle_json_msg({"type": "unknown"})
    d._handle_json_msg({})
    d.stop()


@pytest.mark.asyncio
async def test_pi_start_and_loops():
    from agents_on_hand.drivers.pi_rpc_driver import PiRPCDriver
    d = PiRPCDriver("pi", Path("/tmp"))
    mock_process = MagicMock()
    mock_process.stdout = AsyncMock()
    mock_process.stderr = AsyncMock()
    mock_process.stdin = MagicMock()
    mock_process.stdin.drain = AsyncMock()
    mock_process.returncode = 0
    with patch("asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=mock_process):
        with patch("asyncio.create_task") as mock_task:
            mock_task.return_value = MagicMock()
            result = await d.start()
            assert result is True
    d.stop()
    d2 = PiRPCDriver("pi", Path("/tmp"))
    with patch("asyncio.create_subprocess_exec", side_effect=Exception("fail")):
        result = await d2.start()
        assert result is False


@pytest.mark.asyncio
async def test_pi_read_loop():
    from agents_on_hand.drivers.pi_rpc_driver import PiRPCDriver
    d = PiRPCDriver("pi", Path("/tmp"))
    d.is_running = True
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=[
        b'{"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "hi"}}\n',
        b'not json\n',
        b'  \n',
        b'{"type": "turn_end"}\n',
        b'',
    ])
    mock_process = MagicMock()
    mock_process.stdout = mock_stdout
    mock_process.returncode = 0
    d.process = mock_process
    events = []
    d._listeners = [lambda e: events.append(e)]
    await d._read_loop()
    assert d.is_running is False
    assert any(e.event_type == "exit" for e in events)


@pytest.mark.asyncio
async def test_pi_drain_stderr():
    from agents_on_hand.drivers.pi_rpc_driver import PiRPCDriver
    d = PiRPCDriver("pi", Path("/tmp"))
    d.is_running = True
    mock_stderr = AsyncMock()
    mock_stderr.readline = AsyncMock(side_effect=[b"err line\n", b""])
    mock_process = MagicMock()
    mock_process.stderr = mock_stderr
    d.process = mock_process
    await d._drain_stderr()


def test_pi_send_methods():
    from agents_on_hand.drivers.pi_rpc_driver import PiRPCDriver
    d = PiRPCDriver("pi", Path("/tmp"))
    d.send_prompt("hi")
    d.send_control_char("x")
    mock_process = MagicMock()
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_process.stdin = mock_stdin
    d.process = mock_process
    d.is_running = True
    with patch("asyncio.create_task"):
        d.send_prompt("hello")
        assert mock_stdin.write.called
    d.send_control_char("\x03")
    mock_process.terminate.assert_called()
    mock_process.terminate.side_effect = Exception("err")
    d.send_control_char("y")
    d.process.stdin = mock_stdin
    d.is_running = True
    asyncio.run(d.respond_permission("id1", True))
    asyncio.run(d.respond_permission("id1", False))
    d.process.stdin = None
    asyncio.run(d.respond_permission("id1", True))
    d.is_running = False
    asyncio.run(d.respond_permission("id1", True))
    mock_process.terminate = MagicMock(side_effect=Exception("err"))
    d.process = mock_process
    d.stop()
    d.process = None
    d.stop()


def test_bot_imports():
    import agents_on_hand.bot as bot
    assert hasattr(bot, "main")
    assert hasattr(bot, "restricted")
    assert "main" in bot.__all__
    for name in bot.__all__:
        assert hasattr(bot, name)


@pytest.mark.asyncio
async def test_help_command():
    from agents_on_hand.handlers.chat import help_command
    with patch("agents_on_hand.handlers.chat.get_installed_cli_agents", return_value={"claude": {"name": "Claude", "use_acp": False}, "omp": {"name": "OMP", "use_acp": True}}):
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.callback_query = None
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await help_command(update, MagicMock())
        update.message.reply_text.assert_called_once()
        assert "Claude" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_esc_ctrlc_stop_no_session():
    from agents_on_hand.handlers.chat import esc_command, ctrlc_command, stop_command
    for cmd in [esc_command, ctrlc_command, stop_command]:
        with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
            mock_sm.get_active_session.return_value = None
            update = MagicMock()
            update.effective_user = MagicMock(id=1)
            update.message = MagicMock()
            update.message.reply_text = AsyncMock()
            update.callback_query = None
            with patch("agents_on_hand.security.is_user_allowed", return_value=True):
                await cmd(update, MagicMock())
            update.message.reply_text.assert_called()


@pytest.mark.asyncio
async def test_esc_ctrlc_with_session():
    from agents_on_hand.handlers.chat import esc_command, ctrlc_command
    for cmd in [esc_command, ctrlc_command]:
        mock_session = MagicMock()
        mock_session.is_running = True
        mock_session.agent_name = "claude"
        mock_session.session_id = "sid123"
        with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
            mock_sm.get_active_session.return_value = mock_session
            update = MagicMock()
            update.effective_user = MagicMock(id=1)
            update.message = MagicMock()
            update.message.reply_text = AsyncMock()
            update.callback_query = None
            with patch("agents_on_hand.security.is_user_allowed", return_value=True):
                await cmd(update, MagicMock())
            mock_session.send_control_char.assert_called_once()
            update.message.reply_text.assert_called()


@pytest.mark.asyncio
async def test_stop_with_session():
    from agents_on_hand.handlers.chat import stop_command
    mock_session = MagicMock()
    mock_session.session_id = "sid1"
    mock_session.agent_name = "claude"
    with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
        mock_sm.get_active_session.return_value = mock_session
        mock_sm.kill_session.return_value = True
        with patch("agents_on_hand.handlers.chat.active_streamers", {}) as mock_streamers:
            update = MagicMock()
            update.effective_user = MagicMock(id=1)
            update.message = MagicMock()
            update.message.reply_text = AsyncMock()
            update.callback_query = None
            with patch("agents_on_hand.security.is_user_allowed", return_value=True):
                await stop_command(update, MagicMock())
            mock_sm.kill_session.assert_called_with("sid1")
    with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
        mock_sm.get_active_session.return_value = mock_session
        mock_sm.kill_session.return_value = False
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        update.callback_query = None
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await stop_command(update, MagicMock())
        update.message.reply_text.assert_called()


@pytest.mark.asyncio
async def test_text_router_no_session():
    from agents_on_hand.handlers.chat import text_message_router
    with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
        mock_sm.get_active_session.return_value = None
        mock_sm.user_active_session = {}
        mock_sm.sessions = {}
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.text = "hello"
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()
        update.callback_query = None
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await text_message_router(update, MagicMock())
        update.message.reply_text.assert_called()
        assert "離線" in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_text_router_esc_shortcuts():
    from agents_on_hand.handlers.chat import text_message_router
    for txt in ["esc", "ESC", "!esc", "cancel", "!cancel"]:
        mock_session = MagicMock()
        mock_session.is_running = True
        mock_session.session_id = "sid"
        mock_session.agent_name = "claude"
        with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
            mock_sm.get_active_session.return_value = mock_session
            update = MagicMock()
            update.effective_user = MagicMock(id=1)
            update.message = MagicMock()
            update.message.text = txt
            update.message.chat_id = 123
            update.message.reply_text = AsyncMock()
            update.callback_query = None
            with patch("agents_on_hand.security.is_user_allowed", return_value=True):
                with patch("agents_on_hand.handlers.chat.active_streamers", {}):
                    await text_message_router(update, MagicMock())
            mock_session.send_control_char.assert_called_with("\x1b")
    for txt in ["ctrlc", "ctrl+c", "!ctrlc", "!stop"]:
        mock_session = MagicMock()
        mock_session.is_running = True
        mock_session.session_id = "sid"
        with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
            mock_sm.get_active_session.return_value = mock_session
            update = MagicMock()
            update.effective_user = MagicMock(id=1)
            update.message = MagicMock()
            update.message.text = txt
            update.message.chat_id = 123
            update.message.reply_text = AsyncMock()
            update.callback_query = None
            with patch("agents_on_hand.security.is_user_allowed", return_value=True):
                with patch("agents_on_hand.handlers.chat.active_streamers", {}):
                    await text_message_router(update, MagicMock())
            mock_session.send_control_char.assert_called_with("\x03")


@pytest.mark.asyncio
async def test_text_router_normal_flow():
    from agents_on_hand.handlers.chat import text_message_router
    mock_session = MagicMock()
    mock_session.is_running = True
    mock_session.session_id = "sid"
    mock_session.agent_name = "claude"
    mock_session.send_input = MagicMock()
    with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
        mock_sm.get_active_session.return_value = mock_session
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.text = "hello world"
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()
        update.callback_query = None
        context = MagicMock()
        context.bot = MagicMock()
        mock_streamer = MagicMock()
        mock_streamer.session.session_id = "sid"
        mock_streamer._is_active = True
        mock_streamer.notify_user_input = MagicMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            with patch("agents_on_hand.handlers.chat.active_streamers", {1: mock_streamer}):
                with patch("agents_on_hand.handlers.chat.create_streamer_for_session") as mock_create:
                    await text_message_router(update, context)
                    # send_input now includes turn_id for trace correlation
                    assert mock_session.send_input.called
                    assert mock_session.send_input.call_args[0][0] == "hello world"
                    assert "turn_id" in mock_session.send_input.call_args[1]


@pytest.mark.asyncio
async def test_text_router_is_starting():
    from agents_on_hand.handlers.chat import text_message_router
    mock_session = MagicMock()
    mock_session.is_running = False
    mock_session.is_starting = True
    mock_session.session_id = "sid"
    with patch("agents_on_hand.handlers.chat.session_manager") as mock_sm:
        mock_sm.get_active_session.return_value = mock_session
        mock_sm.user_active_session = {1: "sid"}
        mock_sm.sessions = {"sid": mock_session}
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.text = "hi"
        update.message.chat_id = 123
        update.message.reply_text = AsyncMock()
        update.callback_query = None
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            with patch("asyncio.sleep", new_callable=AsyncMock):
                await text_message_router(update, MagicMock())
        update.message.reply_text.assert_called()


@pytest.mark.asyncio
async def test_global_error_handler_variants():
    from telegram import Update as TgUpdate
    from agents_on_hand.app import global_error_handler
    mock_cq_msg = MagicMock()
    mock_cq_msg.reply_text = AsyncMock()
    mock_cq = MagicMock()
    mock_cq.message = mock_cq_msg
    update = object.__new__(TgUpdate)
    object.__setattr__(update, "callback_query", mock_cq)
    object.__setattr__(update, "message", None)
    object.__setattr__(update, "_frozen", False)
    ctx = MagicMock()
    ctx.error = ValueError("boom")
    await global_error_handler(update, ctx)
    mock_cq_msg.reply_text.assert_called_once()
    mock_msg = MagicMock()
    mock_msg.reply_text = AsyncMock()
    update2 = object.__new__(TgUpdate)
    object.__setattr__(update2, "_frozen", False)
    object.__setattr__(update2, "callback_query", None)
    object.__setattr__(update2, "message", mock_msg)
    ctx2 = MagicMock()
    ctx2.error = RuntimeError("oops")
    await global_error_handler(update2, ctx2)
    mock_msg.reply_text.assert_called_once()
    await global_error_handler("not an update", ctx)
    mock_cq_msg2 = MagicMock()
    mock_cq_msg2.reply_text = AsyncMock(side_effect=Exception("fail"))
    mock_cq2 = MagicMock()
    mock_cq2.message = mock_cq_msg2
    update3 = object.__new__(TgUpdate)
    object.__setattr__(update3, "callback_query", mock_cq2)
    object.__setattr__(update3, "message", None)
    object.__setattr__(update3, "_frozen", False)
    await global_error_handler(update3, ctx)


@pytest.mark.asyncio
async def test_post_init():
    from agents_on_hand.app import post_init
    from agents_on_hand import config as cfg
    mock_bot = MagicMock()
    mock_bot.set_my_commands = AsyncMock()
    mock_bot.send_message = AsyncMock()
    app = MagicMock()
    app.bot = mock_bot
    with patch.object(cfg, "ALLOWED_TELEGRAM_USER_IDS", {123}):
        with patch("agents_on_hand.app.ALLOWED_TELEGRAM_USER_IDS", {123}):
            await post_init(app)
    mock_bot.set_my_commands.assert_called_once()
    mock_bot2 = MagicMock()
    mock_bot2.set_my_commands = AsyncMock(side_effect=Exception("fail"))
    mock_bot2.send_message = AsyncMock()
    app2 = MagicMock()
    app2.bot = mock_bot2
    with patch.object(cfg, "ALLOWED_TELEGRAM_USER_IDS", {123}):
        with patch("agents_on_hand.app.ALLOWED_TELEGRAM_USER_IDS", {123}):
            await post_init(app2)
    mock_bot3 = MagicMock()
    mock_bot3.set_my_commands = AsyncMock()
    mock_bot3.send_message = AsyncMock()
    app3 = MagicMock()
    app3.bot = mock_bot3
    with patch.object(cfg, "ALLOWED_TELEGRAM_USER_IDS", set()):
        with patch("agents_on_hand.app.ALLOWED_TELEGRAM_USER_IDS", set()):
            await post_init(app3)
    mock_bot3.send_message.assert_not_called()


def test_main_no_token():
    import agents_on_hand.app as app_mod
    from agents_on_hand import config as cfg
    with patch.object(app_mod, "TELEGRAM_BOT_TOKEN", ""), patch.object(cfg, "TELEGRAM_BOT_TOKEN", ""):
        with patch("builtins.print") as mock_print:
            app_mod.main()
            mock_print.assert_called()


def test_main_no_users(capsys):
    import agents_on_hand.app as app_mod
    from agents_on_hand import config as cfg
    with patch.object(app_mod, "TELEGRAM_BOT_TOKEN", "123:abc"), patch.object(cfg, "TELEGRAM_BOT_TOKEN", "123:abc"), patch.object(cfg, "ALLOWED_TELEGRAM_USER_IDS", set()), patch.object(cfg, "DEV_ALLOW_ALL", False), patch.object(app_mod, "ALLOWED_TELEGRAM_USER_IDS", set()):
        app_mod.main()
    out = capsys.readouterr().out
    assert "ALLOWED_TELEGRAM_USER_IDS" in out


def test_main_success():
    import agents_on_hand.app as app_mod
    mock_app = MagicMock()
    mock_builder = MagicMock()
    mock_builder.token.return_value = mock_builder
    mock_builder.post_init.return_value = mock_builder
    mock_builder.build.return_value = mock_app
    mock_app.add_error_handler = MagicMock()
    mock_app.add_handler = MagicMock()
    mock_app.run_polling = MagicMock()
    with patch.object(app_mod, "TELEGRAM_BOT_TOKEN", "fake-token"):
        with patch.object(app_mod, "ALLOWED_TELEGRAM_USER_IDS", {123}):
            with patch("telegram.ext.Application.builder", return_value=mock_builder):
                with patch("agents_on_hand.session_manager.session_manager.register_on_finished_callback"):
                    app_mod.main()
                    mock_app.run_polling.assert_called_once()
