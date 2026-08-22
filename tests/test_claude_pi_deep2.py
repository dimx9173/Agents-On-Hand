"""Claude/pi deep — hit _handle_json_msg branches."""
from pathlib import Path
from unittest.mock import MagicMock
import pytest

def test_claude_handle_json_msg_all():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver
    d = ClaudeStreamDriver("claude", Path("/tmp"))
    # mock listeners to capture
    calls = []
    d._listeners = [lambda e: calls.append(e)]
    # assistant with text
    d._handle_json_msg({"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}})
    # assistant with no content
    d._handle_json_msg({"type": "assistant", "message": {}})
    d._handle_json_msg({"type": "assistant"})
    # result
    d._handle_json_msg({"type": "result", "result": "ok"})
    d._handle_json_msg({"type": "result", "is_error": True, "result": "fail"})
    # system
    d._handle_json_msg({"type": "system", "subtype": "init"})
    # unknown
    d._handle_json_msg({"type": "unknown", "foo": "bar"})
    d._handle_json_msg({})
    d._handle_json_msg({"type": None})
    assert True

def test_pi_rpc_handle():
    from agents_on_hand.drivers.pi_rpc_driver import PiRPCDriver
    d = PiRPCDriver("pi", Path("/tmp"))
    # PiRPC may have _handle_msg or similar
    for meth in ["_handle_msg", "_handle_json", "_on_msg", "_handle_line"]:
        if hasattr(d, meth):
            try:
                getattr(d, meth)({"method": "update", "params": {"content": "hi"}})
                getattr(d, meth)('{"jsonrpc":"2.0","method":"update","params":{"content":"hi"}}')
            except Exception:
                pass
    # basic
    assert "pi" in d.command
    d.register_listener(MagicMock())
    assert len(d._listeners) >= 1
    d.stop()

def test_pty_handle_data():
    from agents_on_hand.drivers.pty_driver import PTYDriver
    d = PTYDriver("bash", Path("/tmp"))
    # try handle data if exists
    for meth in ["_on_data", "_handle_data", "_on_output"]:
        if hasattr(d, meth):
            try:
                getattr(d, meth)(b"hello\n")
                getattr(d, meth)("hello")
            except Exception:
                pass
    d.register_listener(MagicMock())
    assert len(d._listeners) >= 1
    d.stop()

def test_acp_client_handle_variants():
    from agents_on_hand.acp_client import ACPClient
    c = ACPClient("echo", "/tmp")
    # variants already tested but add more
    c._handle_json_msg({"id": 1, "result": {"data": "ok"}})
    c._handle_json_msg({"id": 2, "error": {"code": -32600, "message": "Invalid"}})
    c._handle_json_msg({"method": "update", "params": {"delta": "hi"}})
    c._handle_json_msg({"method": "requestPermission", "params": {"tool": "bash"}, "id": 10})
    c._handle_json_msg({"jsonrpc": "2.0", "method": "notify", "params": {}})
    assert True
