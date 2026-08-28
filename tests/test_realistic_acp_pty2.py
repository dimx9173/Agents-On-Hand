"""ACP/PTY realistic — towards 75%."""
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

def test_acp_client_listeners():
    from agents_on_hand.acp_client import ACPClient
    c = ACPClient("echo", "/tmp")
    cb = MagicMock()
    c.register_listener(cb)
    c.register_listener(cb)
    assert len(c._listeners) == 1
    cb2 = MagicMock()
    c.register_permission_listener(cb2)
    assert len(c._permission_listeners) == 1
    # no start, just check state
    assert c.is_running is False
    assert c._request_id == 0

def test_acp_client_has_start():
    from agents_on_hand.acp_client import ACPClient
    c = ACPClient("echo hello", "/tmp")
    assert hasattr(c, "start")
    assert hasattr(c, "register_listener")

def test_pty_driver_basic(tmp_path):
    from agents_on_hand.drivers.pty_driver import PTYDriver
    d = PTYDriver("bash", tmp_path)
    assert d.command == "bash"
    cb = MagicMock()
    d.register_listener(cb)
    assert cb in d._listeners
    d.unregister_listener(cb)
    assert cb not in d._listeners

def test_logging_setup_levels(tmp_path, monkeypatch):
    from agents_on_hand.logging_setup import setup_logging
    # should not crash with different levels
    monkeypatch.setenv("SESSION_LOG_DIR", str(tmp_path))
    for lvl in ["DEBUG", "INFO", "WARNING"]:
        setup_logging(level=lvl)
    # check sanitize
    from agents_on_hand.logging_setup import _sanitize
    assert isinstance(_sanitize("test 123"), str)

def test_ansi_cleaner_comprehensive():
    from agents_on_hand.ansi_cleaner import strip_ansi_codes, format_telegram_code_block, clean_cli_output
    assert strip_ansi_codes("\x1b[31mred\x1b[0m") == "red"
    assert strip_ansi_codes("") == ""
    assert "world" in clean_cli_output("hello\nworld")
    block = format_telegram_code_block("a"*5000, max_chars=100)
    assert "```" in block
