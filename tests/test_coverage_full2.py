import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest


def test_extract_acp_text_delta_variants():
    from agents_on_hand.drivers.acp_driver import extract_acp_text_delta, _extract_text_from_node
    assert extract_acp_text_delta({}) == ""
    assert extract_acp_text_delta(None) == ""
    assert extract_acp_text_delta({"content": "hello"}) == "hello"
    assert extract_acp_text_delta({"delta": "hi"}) == "hi"
    assert extract_acp_text_delta({"update": {"content": "from update"}}) == "from update"
    assert extract_acp_text_delta({"update": {"delta": "d"}}) == "d"
    assert extract_acp_text_delta({"update": {"text": "t"}}) == "t"
    assert extract_acp_text_delta({"update": "raw string"}) == "raw string"
    assert extract_acp_text_delta({"content": {"text": "nested"}}) == "nested"
    assert extract_acp_text_delta({"content": {"delta": "d2"}}) == "d2"
    assert extract_acp_text_delta({"content": {"delta": {"text": "deep"}}}) == "deep"
    assert extract_acp_text_delta({"content": {"output": "out"}}) == "out"
    assert extract_acp_text_delta({"content": ["a", "b"]}) == "ab"
    assert extract_acp_text_delta({"content": {"unknown": 1}}) == ""
    assert _extract_text_from_node("") == ""
    assert _extract_text_from_node(None) == ""
    assert _extract_text_from_node(123) == ""
    assert _extract_text_from_node('{"session_id": "x", "hook_event_name": "y", "transcript_path": "z"}') == ""
    assert _extract_text_from_node("plain") == "plain"
    assert _extract_text_from_node({"text": "t"}) == "t"
    assert _extract_text_from_node({"delta": "d"}) == "d"
    assert _extract_text_from_node({"content": "c"}) == "c"
    assert _extract_text_from_node(["a", "", "b"]) == "ab"
    assert _extract_text_from_node(("x", "y")) == "xy"


def test_acp_driver_on_update():
    from agents_on_hand.drivers.acp_driver import ACPDriver
    d = ACPDriver("omp", Path("/tmp"))
    events = []
    d._listeners = [lambda e: events.append(e)]
    d._on_acp_update(None)
    d._on_acp_update({})
    d._on_acp_update({"update": {"sessionUpdate": "tool_call_progress", "content": "ignore"}})
    assert len([e for e in events if e.event_type == "tool_result"]) == 0
    events.clear()
    d._on_acp_update({"update": "not dict"})
    events.clear()
    d._on_acp_update({"update": {"sessionUpdate": "tool_result", "content": "result text", "tool_name": "bash"}})
    assert any(e.event_type == "tool_result" for e in events)
    events.clear()
    d._on_acp_update({"update": {"sessionUpdate": "postToolUse", "output": "out"}})
    d._on_acp_update({"content": "hello"})
    assert any("hello" in e.content for e in events)
    events.clear()
    d._on_acp_update({"content": "thought content", "update": {"sessionUpdate": "thinking"}})
    assert any(e.event_type == "thought_delta" for e in events)
    events.clear()
    d._on_acp_update({"content": ""})
    d._handle_json_msg = d._on_acp_update
    d._on_acp_permission_request(1, {"name": "tool", "args": {"a": 1}})
    assert any(e.event_type == "tool_request" for e in events)
    events.clear()
    d._on_acp_permission_request(2, {"title": "Approve", "description": "desc"})
    d._on_acp_permission_request(3, {})


@pytest.mark.asyncio
async def test_acp_driver_start_and_monitor():
    from agents_on_hand.drivers.acp_driver import ACPDriver
    d = ACPDriver("omp", Path("/tmp"))
    mock_client = MagicMock()
    mock_client.start = AsyncMock()
    mock_client._read_task = asyncio.get_running_loop().create_future()
    mock_client._read_task.set_result(None)
    mock_client.stop = MagicMock()
    with patch("agents_on_hand.drivers.acp_driver.ACPClient", return_value=mock_client):
        with patch("asyncio.create_task") as mock_ct:
            mock_ct.return_value = MagicMock()
            result = await d.start()
            assert result is True
            assert d.is_running is True
    d2 = ACPDriver("omp", Path("/tmp"))
    with patch("agents_on_hand.drivers.acp_driver.ACPClient", side_effect=Exception("fail")):
        result = await d2.start()
        assert result is False
    d.is_running = True
    d.client = mock_client
    d._listeners = [lambda e: None]
    await d._monitor_exit()
    assert d.is_running is False
    d.client._read_task = None
    await d._monitor_exit()
    d.client = None
    await d._monitor_exit()


def test_acp_driver_send():
    from agents_on_hand.drivers.acp_driver import ACPDriver
    d = ACPDriver("omp", Path("/tmp"))
    d.send_prompt("hi")
    d.send_control_char("\x03")
    mock_client = MagicMock()
    mock_client.prompt = AsyncMock()
    mock_client.cancel = AsyncMock()
    d.client = mock_client
    d.is_running = True
    with patch("asyncio.create_task") as mock_ct:
        d.send_prompt("hello")
        mock_ct.assert_called()
        call_arg = mock_ct.call_args[0][0]
        asyncio.run(call_arg)
    with patch("asyncio.create_task") as mock_ct:
        d.send_control_char("\x03")
        mock_ct.assert_called()
        d.send_control_char("\x1b")
        d.send_control_char("x")
    assert mock_client.cancel.call_count >= 1
    mock_client.prompt = AsyncMock(side_effect=Exception("err"))
    d.client = mock_client
    with patch("asyncio.create_task") as mock_ct:
        d.send_prompt("hi")
        coro = mock_ct.call_args[0][0]
        asyncio.run(coro)


@pytest.mark.asyncio
async def test_acp_driver_respond():
    from agents_on_hand.drivers.acp_driver import ACPDriver
    d = ACPDriver("omp", Path("/tmp"))
    await d.respond_permission(1, True)
    mock_client = MagicMock()
    mock_client.respond_to_permission = AsyncMock()
    d.client = mock_client
    await d.respond_permission(1, True)
    mock_client.respond_to_permission.assert_called()


def test_acp_driver_stop():
    from agents_on_hand.drivers.acp_driver import ACPDriver
    d = ACPDriver("omp", Path("/tmp"))
    d.stop()
    mock_client = MagicMock()
    d.client = mock_client
    d.is_running = True
    d.stop()
    mock_client.stop.assert_called()
    assert d.is_running is False


def test_acp_session_extract():
    from agents_on_hand.drivers.acp_driver import extract_acp_text_delta
    assert extract_acp_text_delta({}) == ""
    assert extract_acp_text_delta(None) == ""
    assert extract_acp_text_delta({"content": "hi"}) == "hi"
    assert extract_acp_text_delta({"delta": "hi"}) == "hi"
    assert extract_acp_text_delta({"update": {"content": "u"}}) == "u"
    assert extract_acp_text_delta({"update": {"delta": "d"}}) == "d"
    assert extract_acp_text_delta({"update": {"text": "t"}}) == "t"
    assert extract_acp_text_delta({"content": {"text": "t"}}) == "t"
    assert extract_acp_text_delta({"content": {"delta": "d"}}) == "d"
    assert extract_acp_text_delta({"content": 123}) == ""
    assert extract_acp_text_delta({"delta": {"text": "deep"}}) == "deep"


@pytest.mark.asyncio
async def test_acp_client_handle():
    from agents_on_hand.acp_client import ACPClient
    c = ACPClient("echo", "/tmp")
    fut = asyncio.get_running_loop().create_future()
    c._pending_requests[1] = fut
    c._handle_json_msg({"id": 1, "result": {"ok": 1}})
    assert fut.result() == {"ok": 1}
    fut2 = asyncio.get_running_loop().create_future()
    c._pending_requests[2] = fut2
    c._handle_json_msg({"id": 2, "error": {"code": -1, "message": "err"}})
    assert fut2.exception() is not None
    c._handle_json_msg({"id": 99, "result": {}})
    events = []
    c._listeners = [lambda p: events.append(p)]
    c._handle_json_msg({"method": "agent/update", "params": {"content": "hi"}})
    assert len(events) == 1
    c._listeners = [lambda p: (_ for _ in ()).throw(Exception("err"))]
    c._handle_json_msg({"method": "session/update", "params": {}})
    perm_events = []
    c._permission_listeners = [lambda i,p: perm_events.append((i,p))]
    c._handle_json_msg({"method": "agent/request_permission", "id": 10, "params": {"name": "tool"}})
    assert len(perm_events) == 1
    c._permission_listeners = [lambda i,p: (_ for _ in ()).throw(Exception("err"))]
    c._handle_json_msg({"method": "permission/request", "id": 11, "params": {}})
    c._handle_json_msg({"method": "unknown", "params": {}})
    c._handle_json_msg({"jsonrpc": "2.0", "method": "notify", "params": {}})


@pytest.mark.asyncio
async def test_acp_client_call_and_send():
    from agents_on_hand.acp_client import ACPClient
    c = ACPClient("echo", "/tmp")
    with pytest.raises(RuntimeError):
        await c.call_method("test", {}, timeout=1)
    mock_proc = MagicMock()
    mock_stdin = MagicMock()
    mock_stdin.write = MagicMock()
    mock_stdin.drain = AsyncMock()
    mock_proc.stdin = mock_stdin
    c.process = mock_proc
    c.is_running = True
    async def fake_wait(fut, timeout):
        fut.set_result({"ok": 1})
        return await fut
    with patch("asyncio.wait_for", side_effect=fake_wait):
        result = await c.call_method("initialize", {}, timeout=1)
        assert result == {"ok": 1}
    with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError()):
        with pytest.raises(asyncio.TimeoutError):
            await c.call_method("test2", {}, timeout=0.1)
    await c.send_notification("session/cancel", {})
    c.process = None
    await c.send_notification("x", {})
    c.process = mock_proc
    await c.respond_to_permission(1, True)
    c.process = None
    await c.respond_to_permission(1, True)
    c.process = mock_proc
    await c.cancel()
    c.stop()
    assert c.is_running is False
    mock_task = MagicMock()
    mock_task.done.return_value = False
    mock_task.cancel = MagicMock()
    c._read_task = mock_task
    c.process = mock_proc
    c.stop()
    mock_task.done.return_value = True
    c._read_task = mock_task
    c.stop()


@pytest.mark.asyncio
async def test_acp_client_read_loop():
    from agents_on_hand.acp_client import ACPClient
    c = ACPClient("echo", "/tmp")
    c.is_running = True
    mock_stdout = AsyncMock()
    mock_stdout.readline = AsyncMock(side_effect=[
        b'{"id": 1, "result": {}}\n',
        b'not json\n',
        b'  \n',
        b'',
    ])
    mock_proc = MagicMock()
    mock_proc.stdout = mock_stdout
    c.process = mock_proc
    await c._read_loop()
    assert c.is_running is False
    c.is_running = True
    mock_stdout2 = AsyncMock()
    mock_stdout2.readline = AsyncMock(side_effect=asyncio.CancelledError())
    mock_proc2 = MagicMock()
    mock_proc2.stdout = mock_stdout2
    c.process = mock_proc2
    await c._read_loop()
    c.is_running = True
    mock_stdout3 = AsyncMock()
    mock_stdout3.readline = AsyncMock(side_effect=ValueError("limit"))
    mock_stdout3.readuntil = AsyncMock(side_effect=Exception("fail"))
    mock_proc3 = MagicMock()
    mock_proc3.stdout = mock_stdout3
    mock_proc3.stdout.readuntil = AsyncMock(side_effect=Exception("fail"))
    c.process = mock_proc3
    mock_stdout3.readline = AsyncMock(side_effect=[ValueError("limit"), b''])
    c.process.stdout = mock_stdout3
    await c._read_loop()


@pytest.mark.asyncio
async def test_session_menu():
    from agents_on_hand.ui.session_menu import sessions_command, prune_command, session_action_callback_handler
    mock_session = MagicMock()
    mock_session.session_id = "sid1"
    mock_session.agent_name = "claude"
    mock_session.working_dir = Path("/tmp")
    mock_session.working_dir = MagicMock()
    mock_session.working_dir.name = "tmp"
    mock_session.is_running = True
    mock_session.get_last_n_lines.return_value = "log"
    mock_session.log_file_path = MagicMock()
    mock_session.log_file_path.exists.return_value = False
    with patch("agents_on_hand.ui.session_menu.session_manager") as mock_sm:
        mock_sm.list_user_sessions.return_value = []
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await sessions_command(update, MagicMock())
        update.message.reply_text.assert_called()
        mock_sm.list_user_sessions.return_value = [mock_session]
        mock_sm.get_active_session.return_value = mock_session
        update2 = MagicMock()
        update2.effective_user = MagicMock(id=1)
        update2.message = MagicMock()
        update2.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await sessions_command(update2, MagicMock())
        mock_session.is_running = False
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await sessions_command(update2, MagicMock())
    with patch("agents_on_hand.ui.session_menu.session_manager") as mock_sm:
        mock_sm.prune_offline_sessions.return_value = 1
        update = MagicMock()
        update.effective_user = MagicMock(id=1)
        update.message = MagicMock()
        update.message.reply_text = AsyncMock()
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await prune_command(update, MagicMock())
        mock_sm.prune_offline_sessions.return_value = 0
        with patch("agents_on_hand.security.is_user_allowed", return_value=True):
            await prune_command(update, MagicMock())


@pytest.mark.asyncio
async def test_directory_browser():
    from agents_on_hand.ui.directory_browser import send_directory_browser, directory_callback_handler, show_agent_selector, agent_start_callback_handler
    tmp = Path("/tmp")
    update = MagicMock()
    update.effective_user = MagicMock(id=1)
    update.callback_query = None
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    with patch("agents_on_hand.security.is_user_allowed", return_value=True):
        with patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=False):
            await send_directory_browser(update, MagicMock(), tmp)
    update.message.reply_text.assert_called()
    query = MagicMock()
    query.data = "noop"
    query.answer = AsyncMock()
    upd = MagicMock()
    upd.effective_user = MagicMock(id=1)
    upd.callback_query = query
    upd.message = MagicMock()
    upd.message.reply_text = AsyncMock()
    with patch("agents_on_hand.security.is_user_allowed", return_value=True):
        await directory_callback_handler(upd, MagicMock())
    query.data = "dir:nav:token:0"
    with patch("agents_on_hand.security.is_user_allowed", return_value=True):
        with patch("agents_on_hand.ui.directory_browser.resolve_path_token", return_value=tmp), patch("agents_on_hand.ui.directory_browser.send_directory_browser", new_callable=AsyncMock) as mock_send:
            await directory_callback_handler(upd, MagicMock())
    query.data = "dir:select:token"
    with patch("agents_on_hand.security.is_user_allowed", return_value=True):
        with patch("agents_on_hand.ui.directory_browser.resolve_path_token", return_value=tmp), patch("agents_on_hand.ui.directory_browser.show_agent_selector", new_callable=AsyncMock) as mock_show:
            await directory_callback_handler(upd, MagicMock())
    q2 = MagicMock()
    q2.edit_message_text = AsyncMock()
    with patch("agents_on_hand.ui.directory_browser.get_installed_cli_agents", return_value={}), patch("agents_on_hand.ui.directory_browser.get_path_token", return_value="tok"):
        await show_agent_selector(q2, tmp)
    q2.edit_message_text.assert_called()
    with patch("agents_on_hand.ui.directory_browser.get_installed_cli_agents", return_value={"claude": {"name": "Claude", "use_acp": False}}), patch("agents_on_hand.ui.directory_browser.get_path_token", return_value="tok"):
        await show_agent_selector(q2, tmp)
    upd2 = MagicMock()
    upd2.callback_query = MagicMock()
    upd2.callback_query.data = "agent:start:tok:claude"
    upd2.callback_query.from_user = MagicMock(id=1)
    upd2.callback_query.message = MagicMock(chat_id=123)
    upd2.callback_query.answer = AsyncMock()
    upd2.callback_query.edit_message_text = AsyncMock()
    mock_context = MagicMock()
    mock_context.bot.send_message = AsyncMock()
    with patch("agents_on_hand.security.is_user_allowed", return_value=True), patch("agents_on_hand.ui.directory_browser.resolve_path_token", return_value=tmp), patch("agents_on_hand.ui.directory_browser.is_path_allowed", return_value=True), patch("agents_on_hand.ui.directory_browser.session_manager") as mock_sm, patch("agents_on_hand.ui.directory_browser.create_streamer_for_session") as mock_create:
        mock_create.return_value = MagicMock(start=MagicMock())
        mock_session = MagicMock(session_id="sid", agent_name="claude", working_dir=tmp)
        mock_sm.create_session.return_value = mock_session
        with patch("agents_on_hand.ui.directory_browser.active_streamers", {}):
            await agent_start_callback_handler(upd2, mock_context)
    # fail-closed: unknown token resolves to None -> no session spawned
    upd3 = MagicMock()
    upd3.callback_query = MagicMock()
    upd3.callback_query.data = "agent:start:expired:claude"
    upd3.callback_query.from_user = MagicMock(id=1)
    upd3.callback_query.answer = AsyncMock()
    upd3.callback_query.message = MagicMock(chat_id=123)
    upd3.callback_query.edit_message_text = AsyncMock()
    with patch("agents_on_hand.security.is_user_allowed", return_value=True), patch("agents_on_hand.ui.directory_browser.resolve_path_token", return_value=None), patch("agents_on_hand.ui.directory_browser.session_manager") as mock_sm2:
        await agent_start_callback_handler(upd3, mock_context)
    mock_sm2.create_session.assert_not_called()
    upd3.callback_query.edit_message_text.assert_called()
    # fail-closed: dir:select with expired token -> alert, no agent selector
    query.data = "dir:select:expired"
    with patch("agents_on_hand.security.is_user_allowed", return_value=True), patch("agents_on_hand.ui.directory_browser.resolve_path_token", return_value=None) as mock_resolve_expired, patch("agents_on_hand.ui.directory_browser.show_agent_selector", new_callable=AsyncMock) as mock_show2:
        await directory_callback_handler(upd, MagicMock())
    mock_show2.assert_not_called()
