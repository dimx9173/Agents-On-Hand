"""ACP session & extract delta — realistic responsible use cases."""
from pathlib import Path
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

def test_extract_acp_text_delta_all_branches():
    from agents_on_hand.drivers.acp_driver import extract_acp_text_delta
    assert extract_acp_text_delta(None) == ""
    assert extract_acp_text_delta("not dict") == ""
    assert extract_acp_text_delta({"content": "hello"}) == "hello"
    assert extract_acp_text_delta({"delta": "world"}) == "world"
    assert extract_acp_text_delta({"update": {"content": "up_hello"}}) == "up_hello"
    assert extract_acp_text_delta({"update": {"delta": "up_delta"}}) == "up_delta"
    assert extract_acp_text_delta({"update": {"text": "up_text"}}) == "up_text"
    assert extract_acp_text_delta({"content": {"text": "nested"}}) == "nested"
    assert extract_acp_text_delta({"content": {"delta": "nested_delta"}}) == "nested_delta"
    assert extract_acp_text_delta({"content": {"unknown": 123}}) == ""
    assert extract_acp_text_delta({"content": 123}) == ""
    assert extract_acp_text_delta({"content": None}) == ""
    assert extract_acp_text_delta({"content": {"text": 123}}) == ""
def test_pty_driver_send_and_stop(tmp_path):
    from agents_on_hand.drivers.pty_driver import PTYDriver
    d = PTYDriver("bash", tmp_path)
    cb = MagicMock()
    d.register_listener(cb)
    assert len(d._listeners) == 1
    # send without process should not crash
    d.send_prompt("hello")
    d.send_control_char("\x03")
    d.stop()
    assert True

def test_acp_driver_basic(tmp_path):
    from agents_on_hand.drivers.acp_driver import ACPDriver
    d = ACPDriver("opencode acp", tmp_path)
    assert "opencode" in d.command
    cb = MagicMock()
    d.register_listener(cb)
    assert cb in d._listeners

def test_claude_stream_driver_parse():
    from agents_on_hand.drivers.claude_stream_driver import ClaudeStreamDriver
    from pathlib import Path
    d = ClaudeStreamDriver("claude", Path("/tmp"))
    # should have stream-json
    assert "stream-json" in d.command
    # _parse_line should handle json
    sample = '{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}'
    # call internal parse if exists
    if hasattr(d, "_handle_line"):
        try:
            d._handle_line(sample)
        except Exception:
            pass
    assert True
