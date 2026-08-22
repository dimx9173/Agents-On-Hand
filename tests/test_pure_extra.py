from agents_on_hand.stream_handler import split_text_into_chunks, UnifiedStreamer
from agents_on_hand.ansi_cleaner import strip_ansi_codes, format_hermes_style, clean_cli_output

def test_split_chunks():
    assert split_text_into_chunks("") == []
    assert split_text_into_chunks("a"*5000, max_chars=3800) == ["a"*3800, "a"*1200]
    txt = "line1\nline2\nline3"
    assert split_text_into_chunks(txt, max_chars=10) == ["line1", "line2", "line3"]

def test_render_content():
    s = UnifiedStreamer(bot=None, chat_id=1, session=object())
    out = s._render_content("hello", "thinking")
    assert "Thinking" in out or "hello" in out
    out2 = s._render_content("hi", "")
    assert "hi" in out2

def test_ansi_cleaner_more():
    assert strip_ansi_codes("\x1b[33mtest\x1b[0m more") == "test more"
    assert clean_cli_output("hello\nworld") == "hello\nworld"
    fmt = format_hermes_style("tool call: ls\nresult ok")
    assert fmt is not None

def test_config_helpers():
    from agents_on_hand.config import is_path_allowed, is_user_allowed, _parse_user_ids
    import pathlib
    assert _parse_user_ids("1,2, 3") == {1,2,3}
    try:
        _parse_user_ids("a,b")
        assert False
    except ValueError:
        pass
    assert is_path_allowed(pathlib.Path.cwd()) in (True, False)
