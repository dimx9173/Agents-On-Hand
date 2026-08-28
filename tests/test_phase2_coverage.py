"""Add coverage for callback_registry and session_store"""
import pathlib
import tempfile
import time

from agents_on_hand.callback_registry import (
    get_path_token,
    register_restart_info,
    resolve_path_token,
)
from agents_on_hand.session_store import JSONSessionStore, SessionRecord


def test_callback_registry_tokens():
    p1 = pathlib.Path("/tmp")
    t1 = get_path_token(p1)
    assert t1.startswith("p_")
    assert resolve_path_token(t1) == p1.resolve()
    t2 = get_path_token(p1)
    assert t1 == t2
    # fail-closed: unknown token -> None (no raw path fallback)
    assert resolve_path_token("nonexistent_token_xyz") is None

def test_restart_registry():
    tok = register_restart_info("bash", pathlib.Path("/tmp"))
    assert tok.startswith("r_")
    assert len(tok) == 10

def test_session_store_roundtrip():
    tmp = pathlib.Path(tempfile.mktemp(suffix=".json"))
    store = JSONSessionStore(tmp)
    rec = SessionRecord(session_id="sess_test", user_id=1, agent_key="bash", agent_name="Bash", command="bash", working_dir="/tmp", created_at=time.time())
    store.save_state([rec], {1: "sess_test"})
    loaded, active = store.load_state()
    assert loaded[0].session_id == "sess_test"
    assert active[1] == "sess_test"
    tmp.write_text("not json")
    loaded2, active2 = store.load_state()
    assert loaded2 == [] and active2 == {}
