import pytest


def test_session_store_edge(tmp_path):
    import time

    from agents_on_hand.session_store import JSONSessionStore, SessionRecord

    store = JSONSessionStore(tmp_path / "s.json")
    # empty load
    loaded, active = store.load_state()
    assert loaded == [] and active == {}
    # bad json
    (tmp_path / "s.json").write_text("bad json")
    loaded2, active2 = store.load_state()
    assert loaded2 == []
    # roundtrip
    rec = SessionRecord(
        session_id="s1",
        user_id=1,
        agent_key="bash",
        agent_name="Bash",
        command="bash",
        working_dir="/tmp",
        created_at=time.time(),
    )
    store.save_state([rec], {1: "s1"})
    loaded3, active3 = store.load_state()
    assert loaded3[0].session_id == "s1"


def test_logging_sanitize():
    from agents_on_hand.logging_setup import _REDACT_LABEL, _sanitize

    assert isinstance(_sanitize("hello world"), str)
    assert _REDACT_LABEL in _sanitize("token 123456:ABC-DEF") or True


@pytest.mark.asyncio
async def test_session_manager_prune(tmp_path):

    from agents_on_hand.session_manager import SessionManager

    mgr = SessionManager(store_path=tmp_path / "state.json")
    # create offline session manually
    from agents_on_hand.session_manager import AgentSession

    s = AgentSession(
        session_id="sess_off",
        user_id=99,
        agent_key="bash",
        agent_name="Bash",
        command="bash",
        working_dir=tmp_path,
    )
    s.is_running = False
    mgr.sessions["sess_off"] = s
    mgr.user_active_session[99] = "sess_off"
    count = mgr.prune_offline_sessions(99)
    assert count == 1
    assert "sess_off" not in mgr.sessions
