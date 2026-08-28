"""ACP session final2 — remaining 7% deep."""
import pytest
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

def test_acp_session_extract_edge():
    from agents_on_hand.drivers.acp_driver import extract_acp_text_delta
    assert extract_acp_text_delta({"content": {"text": "t"}}) == "t"
    assert extract_acp_text_delta({"content": {"delta": "d"}}) == "d"
    assert extract_acp_text_delta({"update": {"text": "ut"}}) == "ut"
    assert extract_acp_text_delta({"content": 0}) in ("", "0")
    assert extract_acp_text_delta({"content": None}) == ""
    assert extract_acp_text_delta({}) == ""
    assert extract_acp_text_delta(None) == ""

@pytest.mark.asyncio
async def test_session_manager_full(tmp_path):
    from agents_on_hand.session_manager import SessionManager, AgentSession
    with patch("agents_on_hand.session_manager.AgentSession.start", new_callable=AsyncMock):
        mgr = SessionManager(store_path=tmp_path / "final.json")
        s1 = mgr.create_session(user_id=20, agent_key="bash", working_dir=tmp_path)
        s2 = mgr.create_session(user_id=20, agent_key="bash", working_dir=tmp_path)
        assert len(mgr.list_user_sessions(20)) == 2
        assert mgr.set_active_session(20, s1.session_id) is True
        assert mgr.get_active_session(20).session_id == s1.session_id
        assert mgr.kill_session(s1.session_id) is True
        assert mgr.get_session(s1.session_id) is None
        assert mgr.kill_session("bad") is False
        s_off = AgentSession(session_id="off_final", user_id=21, agent_key="bash", agent_name="Bash", command="bash", working_dir=tmp_path)
        s_off.is_running = False
        mgr.sessions["off_final"] = s_off
        mgr.user_active_session[21] = "off_final"
        assert mgr.prune_offline_sessions(21) == 1

def test_drivers_basic_all(tmp_path):
    from agents_on_hand.drivers.pty_driver import PTYDriver
    from agents_on_hand.drivers.acp_driver import ACPDriver
    for Cls, cmd in [(PTYDriver, "bash"), (ACPDriver, "opencode acp")]:
        d = Cls(cmd, tmp_path)
        cb = MagicMock()
        d.register_listener(cb)
        d.register_listener(cb)
        assert len(d._listeners) == 1
        d.send_prompt("hi")
        d.send_control_char("\x03")
        d.unregister_listener(cb)
        assert len(d._listeners) == 0
        d.stop()
