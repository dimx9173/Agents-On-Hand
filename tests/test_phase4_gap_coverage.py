"""Phase-4 gap coverage: pty_driver branches, process_utils edges, logging_setup trace API.

Targets the three lowest-coverage modules from the optimization baseline:
pty_driver (~66%), process_utils (~69%), logging_setup (~72%).
"""

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pexpect

# ── PTYDriver ──────────────────────────────────────────────────────────


def _make_driver(tmp_path: Path):
    from agents_on_hand.drivers.pty_driver import PTYDriver

    return PTYDriver("bash", tmp_path)


def test_pty_start_failure_returns_false(tmp_path):
    """pexpect.spawn raising -> start() returns False, is_running False."""
    d = _make_driver(tmp_path)
    with patch("agents_on_hand.drivers.pty_driver.pexpect.spawn", side_effect=OSError("no pty")):
        assert asyncio.run(d.start()) is False
        assert d.is_running is False


def test_pty_start_success_spawns_read_task(tmp_path):
    """Successful spawn sets is_running and creates a read task (cancelled on stop)."""
    d = _make_driver(tmp_path)
    fake_proc = MagicMock()
    fake_proc.isalive.return_value = False  # read loop exits immediately
    with patch("agents_on_hand.drivers.pty_driver.pexpect.spawn", return_value=fake_proc):
        assert asyncio.run(d.start()) is True
        assert d.process is fake_proc
        d.stop()
        assert d.is_running is False


def test_pty_read_nonblocking_no_process(tmp_path):
    d = _make_driver(tmp_path)
    assert d._read_nonblocking() == ""


def test_pty_read_nonblocking_timeout_and_eof(tmp_path):
    d = _make_driver(tmp_path)
    d.process = MagicMock()
    d.process.read_nonblocking.side_effect = pexpect.TIMEOUT("timed out")
    assert d._read_nonblocking() == ""
    d.process.read_nonblocking.side_effect = pexpect.EOF("eof")
    assert d._read_nonblocking() == ""
    d.process.read_nonblocking.side_effect = None
    d.process.read_nonblocking.return_value = "hello"
    assert d._read_nonblocking() == "hello"


def test_pty_send_guards_no_process(tmp_path):
    """send_* with no process / not running must be no-ops, never raise."""
    d = _make_driver(tmp_path)
    d.send_prompt("hi")
    d.send_control_char("\x03")
    asyncio.run(d.respond_permission("req-1", True))
    d.process = MagicMock()
    d.is_running = False
    d.send_prompt("hi")
    d.send_control_char("\x03")
    asyncio.run(d.respond_permission("req-1", False))
    d.process.sendline.assert_not_called()
    d.process.send.assert_not_called()


def test_pty_send_prompt_and_control(tmp_path):
    d = _make_driver(tmp_path)
    d.process = MagicMock()
    d.is_running = True
    d.send_prompt("echo hi")
    d.process.sendline.assert_called_once_with("echo hi")
    d.send_control_char("\x03")
    d.process.send.assert_called_once_with("\x03")


def test_pty_respond_permission_y_n(tmp_path):
    d = _make_driver(tmp_path)
    d.process = MagicMock()
    d.is_running = True
    asyncio.run(d.respond_permission("r1", True))
    d.process.send.assert_called_with("y\r\n")
    asyncio.run(d.respond_permission("r2", False))
    d.process.send.assert_called_with("n\r\n")


def test_pty_read_loop_emits_delta_and_exit(tmp_path):
    """Read loop emits TEXT_DELTA for cleaned output then EXIT on EOF."""

    async def _run():
        d = _make_driver(tmp_path)
        events = []
        d.register_listener(events.append)
        d.process = MagicMock()
        d.process.isalive.side_effect = [True, False]
        d.is_running = True
        with patch.object(d, "_read_nonblocking", return_value="hello world"):
            await d._read_loop()
        kinds = [e.event_type for e in events]
        assert "exit" in kinds
        assert d.is_running is False
        return events

    events = asyncio.run(_run())
    assert events, "expected at least the EXIT event"


def test_pty_read_loop_handles_pexpect_eof(tmp_path):
    async def _run():
        d = _make_driver(tmp_path)
        events = []
        d.register_listener(events.append)
        d.process = MagicMock()
        d.process.isalive.return_value = True
        d.is_running = True
        with patch.object(d, "_read_nonblocking", side_effect=pexpect.EOF("done")):
            await d._read_loop()
        return events

    events = asyncio.run(_run())
    assert any(e.event_type == "exit" for e in events)


def test_pty_read_loop_handles_generic_error_then_eof(tmp_path):
    """Generic exception in loop sleeps and continues; EOF thereafter exits."""

    async def _run():
        d = _make_driver(tmp_path)
        d.process = MagicMock()
        d.process.isalive.return_value = True
        d.is_running = True
        calls = {"n": 0}

        def _flaky():
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("transient read error")
            raise pexpect.EOF("done")

        with (
            patch.object(d, "_read_nonblocking", side_effect=_flaky),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            await d._read_loop()

    asyncio.run(_run())


def test_pty_stop_terminates_tree_and_closes(tmp_path):
    d = _make_driver(tmp_path)
    d.process = MagicMock()
    d.is_running = True
    with patch("agents_on_hand.drivers.pty_driver.kill_process_tree") as mock_kill:
        d.stop()
        mock_kill.assert_called_once_with(d.process)
    d.process.terminate.assert_called_once()
    d.process.close.assert_called_once()


def test_pty_stop_tolerates_terminate_close_errors(tmp_path):
    d = _make_driver(tmp_path)
    d.process = MagicMock()
    d.process.terminate.side_effect = OSError("gone")
    d.process.close.side_effect = OSError("gone")
    with patch("agents_on_hand.drivers.pty_driver.kill_process_tree"):
        d.stop()  # must not raise
    assert d.is_running is False


# ── process_utils edges ────────────────────────────────────────────────


def test_is_process_alive_falsy_and_exited():
    from agents_on_hand.process_utils import is_process_alive

    assert is_process_alive(None) is False
    assert is_process_alive(False) is False
    proc = MagicMock()
    proc.returncode = 1
    assert is_process_alive(proc) is False


def test_is_process_alive_poll_and_isalive():
    from agents_on_hand.process_utils import is_process_alive

    proc = MagicMock()
    proc.returncode = None
    proc.poll.return_value = 0
    proc.isalive.side_effect = AssertionError("should not reach isalive")
    assert is_process_alive(proc) is False

    proc2 = MagicMock()
    proc2.returncode = None
    proc2.poll.return_value = None
    proc2.isalive.return_value = False
    assert is_process_alive(proc2) is False

    proc3 = MagicMock()
    proc3.returncode = None
    proc3.poll.return_value = None
    proc3.isalive.return_value = True
    assert is_process_alive(proc3) is True


def test_is_process_alive_poll_raises_tolerated():
    from agents_on_hand.process_utils import is_process_alive

    proc = MagicMock()
    proc.returncode = None
    proc.poll.side_effect = OSError("nope")
    proc.isalive.return_value = True
    assert is_process_alive(proc) is True

    proc2 = MagicMock()
    proc2.returncode = None
    proc2.poll.return_value = None
    proc2.isalive.side_effect = OSError("nope")
    # isalive errors are swallowed -> proc assumed alive (fail-open for liveness)
    assert is_process_alive(proc2) is True


def test_kill_process_tree_falsy_and_dead_proc():
    from agents_on_hand.process_utils import kill_process_tree

    kill_process_tree(None)  # must not raise
    dead = MagicMock()
    dead.returncode = 0
    with patch("os.kill") as mock_kill:
        kill_process_tree(dead)
        mock_kill.assert_not_called()


def test_kill_process_tree_small_pid_uses_object_fallback():
    from agents_on_hand.process_utils import kill_process_tree

    proc = MagicMock()
    proc.pid = 1
    proc.returncode = None
    proc.poll.return_value = None
    with patch("os.kill") as mock_kill:
        kill_process_tree(proc)
        mock_kill.assert_not_called()
    proc.terminate.assert_called_once()
    proc.kill.assert_called_once()


def test_kill_process_tree_self_pid_refused():
    from agents_on_hand.process_utils import kill_process_tree

    proc = MagicMock()
    proc.pid = 999999
    proc.returncode = None
    proc.poll.return_value = None
    with (
        patch("os.getpid", return_value=999999),
        patch("os.killpg") as mock_killpg,
        patch("os.kill") as mock_kill,
    ):
        kill_process_tree(proc)
        mock_killpg.assert_not_called()
        mock_kill.assert_not_called()


def test_kill_process_tree_pgid_lookup_errors_use_pid_fallback():
    from agents_on_hand.process_utils import kill_process_tree

    proc = MagicMock()
    proc.pid = 42424
    proc.returncode = None
    proc.poll.return_value = None
    with (
        patch("os.getpid", return_value=1),
        patch("os.getpgrp", return_value=5),
        patch("os.getpgid", side_effect=ProcessLookupError("gone")),
        patch("os.kill") as mock_kill,
        patch("os.killpg") as mock_killpg,
    ):
        kill_process_tree(proc)
        mock_killpg.assert_not_called()
        assert mock_kill.call_count == 2


def test_kill_process_tree_killpg_errors_swallowed():
    """killpg raising generic errors must not propagate."""
    from agents_on_hand.process_utils import kill_process_tree

    proc = MagicMock()
    proc.pid = 43434
    proc.returncode = None
    proc.poll.return_value = None
    with (
        patch("os.getpid", return_value=1),
        patch("os.getpgrp", return_value=5),
        patch("os.getpgid", return_value=43434),
        patch("os.killpg", side_effect=RuntimeError("weird")),
    ):
        kill_process_tree(proc)  # must not raise


# ── logging_setup trace API ────────────────────────────────────────────


def test_sanitizing_filter_redacts_and_never_crashes():
    from agents_on_hand.logging_setup import _SanitizingFilter

    f = _SanitizingFilter()
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "token=%s", ("abc",), None)
    assert f.filter(record) is True
    record2 = logging.LogRecord("t", logging.INFO, __file__, 1, "hello %(k)s", None, None)
    record2.args = {"k": "v"}
    assert f.filter(record2) is True
    # un-stringifiable msg must never crash the filter
    bad = logging.LogRecord("t", logging.INFO, __file__, 1, "msg", None, None)
    with patch("agents_on_hand.logging_setup._sanitize", side_effect=RuntimeError("boom")):
        assert f.filter(bad) is True


def test_session_trace_logger_full_api(tmp_path):
    from agents_on_hand.logging_setup import SessionTraceLogger

    SessionTraceLogger._TRACE_LOG_DIR = tmp_path
    trace = SessionTraceLogger(session_id="sess_gap")
    trace.event("CUSTOM", "detail")
    trace.session_init("bash", "bash", "/tmp", ["pty"])
    trace.driver_probe("pty", 1, 2)
    trace.driver_bound("pty", True)
    trace.user_input("hello", turn_id="t1")
    trace.streamer_start()
    trace.streamer_stop()
    trace.streamer_switch("sess_a", "sess_b", reason="test")
    trace.agent_first_token("bash", 0.123, turn_id="t1")
    trace.agent_response_done("bash", 42, 1.5, turn_id="t1")
    trace.thought_delta(10, turn_id="t1")
    trace.tool_request("req1", "bash", "ls", turn_id="t1")
    trace.tool_result("req1", "bash", "ok", turn_id="t1")
    trace.perm_request("req1", "bash", turn_id="t1")
    trace.permission_response("req1", True, turn_id="t1")
    trace.acp_call("session/new", 0.05, True, turn_id="t1")
    trace.acp_session_id("acp-sess-1")
    trace.tg_deliver(None, 5, False, True, turn_id="t1")
    trace.bg_completion(turn_id="t1", notification_sent=True)
    trace.turn_end("t1", driver="pty", reason="normal")
    trace.error("boom")
    trace.close()
    content = (tmp_path / "sess_gap.trace.log").read_text(encoding="utf-8")
    for marker in (
        "CUSTOM",
        "SESSION_INIT",
        "DRIVER_PROBE",
        "USER_INPUT",
        "TURN_START",
        "STREAMER_START",
        "AGENT_TTFT",
        "AGENT_DONE",
        "THOUGHT_DELTA",
        "TOOL_REQUEST",
        "TOOL_RESULT",
        "PERM_REQUEST",
        "PERM_RESPONSE",
        "ACP_CALL",
        "ACP_SESSION_ID",
        "TG_DELIVER",
        "BG_COMPLETION",
        "TURN_END",
        "ERROR",
        "SESSION_END",
    ):
        assert marker in content, marker


def test_session_trace_logger_turn_id_auto_generated(tmp_path):
    from agents_on_hand.logging_setup import SessionTraceLogger

    SessionTraceLogger._TRACE_LOG_DIR = tmp_path
    trace = SessionTraceLogger(session_id="sess_auto")
    trace.user_input("hi without turn")
    assert trace._current_turn_id, "turn id should be auto-generated"
    trace.agent_first_token("bash", 0.5)  # falls back to current turn
    trace.close()


def test_setup_logging_level_resolution(monkeypatch, tmp_path):
    import agents_on_hand.logging_setup as ls

    monkeypatch.setattr(ls, "_SETUP_DONE", False)
    monkeypatch.setattr(ls, "APP_LOG_FILE", str(tmp_path / "aoh.log"))
    monkeypatch.setenv("AOH_DEBUG", "1")
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    ls.setup_logging()
    assert ls._SETUP_DONE is True
    ls._SETUP_DONE = False  # reset for other tests
    root = logging.getLogger()
    for h in root.handlers[:]:
        try:
            h.close()
        except Exception:
            pass
        root.removeHandler(h)
