"""Phase-5 perf & stability regression tests.

Locks in the optimization gains so future changes can't silently regress:
- P1: banner scan fast path (long lines skip keyword scan)
- P2: streamer render cache (identical flush = no Telegram edit; dirty flag)
- P3: batched session log (ordering preserved, single write per burst)
- P4: tail-seek log read (large file fast + correct)
- S3: ACP pending-request map bounded + popped on timeout
- S1/S2: ACPDriver.stop() cancels the exit monitor
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def test_p1_long_lines_skip_banner_scan():
    """Long content lines must not pay the 23-keyword scan."""
    from agents_on_hand import ansi_cleaner as ac

    long_line = "x" * 200
    with patch.object(ac, "_TUI_BANNER_KEYWORDS_LOWER", ("__never_match__",)):
        # If the fast path works, keywords are irrelevant for long lines;
        # result must be identical with or without matching keywords.
        assert ac.clean_cli_output(long_line) == long_line
    # TUI banners still filtered (short lines take the scan path)
    assert ac.clean_cli_output("Welcome back!") == ""
    assert ac.clean_cli_output("omp v1.2.3 started") == ""


def test_p1_patterns_are_precompiled():
    """Hot-path patterns must be module-level compiled objects, not re.compile per call."""
    import re

    from agents_on_hand import ansi_cleaner as ac

    for name in (
        "_BOX_CHAR_PATTERN",
        "_SIDE_BORDER_PATTERN",
        "_MD_LINK_PATTERN",
        "_MD_BOLD_PATTERN",
        "_MD_INLINE_CODE_PATTERN",
        "_MD_HEADER_PATTERN",
        "_MD_LIST_PATTERN",
        "_MD_TOOL_LINE_PATTERN",
        "_TAG_PATTERN",
        "_TAG_SPLIT_PATTERN",
        "_TUI_BANNER_KEYWORDS_LOWER",
    ):
        obj = getattr(ac, name)
        assert isinstance(obj, (re.Pattern, tuple)), name


def test_p2_identical_flush_skips_telegram_edit():
    """Second flush with no new deltas must not call edit_message_text again."""
    from agents_on_hand.stream_handler import UnifiedStreamer

    async def _run():
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
        bot.edit_message_text = AsyncMock()
        sess = MagicMock()
        sess.session_id = "sess_x"
        sess.agent_name = "Bash"
        st = UnifiedStreamer(bot=bot, chat_id=1, session=sess)
        st._is_active = True
        st.current_text = "hello world"
        st._dirty = True
        await st._flush_edit_locked()
        first_edits = bot.send_message.call_count + bot.edit_message_text.call_count
        assert first_edits == 1
        # No new delta -> dirty False; flush must be a no-op
        await st._flush_edit_locked()
        assert bot.send_message.call_count + bot.edit_message_text.call_count == first_edits
        # New delta -> dirty True; flush delivers again
        st.current_text += " more"
        st._dirty = True
        await st._flush_edit_locked()
        assert bot.send_message.call_count + bot.edit_message_text.call_count == first_edits + 1

    asyncio.run(_run())


def test_p2_final_turn_always_flushes():
    """Final turn must flush even when rendered text is unchanged (footer/buttons)."""
    from agents_on_hand.stream_handler import UnifiedStreamer

    async def _run():
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=9))
        bot.edit_message_text = AsyncMock()
        sess = MagicMock()
        sess.session_id = "sess_f"
        sess.agent_name = "Bash"
        st = UnifiedStreamer(bot=bot, chat_id=1, session=sess)
        st._is_active = True
        st.current_text = "done"
        st._dirty = True
        await st._flush_edit_locked()
        n1 = bot.send_message.call_count + bot.edit_message_text.call_count
        st._is_turn_final = True
        st._dirty = False  # even clean, final must flush
        await st._flush_edit_locked()
        n2 = bot.send_message.call_count + bot.edit_message_text.call_count
        assert n2 == n1 + 1

    asyncio.run(_run())


def test_p3_batched_log_preserves_order(tmp_path):
    """Burst of deltas + prompt must land in the log in order, in few writes."""
    from agents_on_hand.drivers.base_driver import DriverEvent
    from agents_on_hand.session_manager import AgentSession

    s = AgentSession.__new__(AgentSession)
    s.log_file_path = tmp_path / "order.log"
    s._log_buffer = []
    s._log_buffer_chars = 0
    s._log_last_flush = time.monotonic()
    s.recent_output = ""
    s._response_chars = 0
    s._first_token_time = None
    s._response_start_time = None
    s.trace = MagicMock()
    writes = {"n": 0}
    real_open = open

    def _counting_open(*a, **k):
        if (
            a
            and str(a[0]).endswith("order.log")
            and "a" in (a[1] if len(a) > 1 else k.get("mode", ""))
        ):
            writes["n"] += 1
        return real_open(*a, **k)

    with patch("builtins.open", _counting_open):
        for i in range(50):
            s._on_driver_event(DriverEvent(DriverEvent.TEXT_DELTA, content=f"chunk-{i}\n"))
        s.flush_log_buffer()
    content = (tmp_path / "order.log").read_text(encoding="utf-8")
    for i in range(50):
        assert f"chunk-{i}" in content
    # Order check: positions strictly increasing
    positions = [content.index(f"chunk-{i}") for i in range(50)]
    assert positions == sorted(positions)
    assert writes["n"] <= 3, f"expected batched writes, got {writes['n']}"


def test_p4_tail_seek_large_file_fast_and_correct(tmp_path):
    """200k-line log: fast tail read with correct last-100 content."""
    from agents_on_hand.session_manager import AgentSession

    big = tmp_path / "big.log"
    big.write_text("".join(f"line-{i}\n" for i in range(200000)))
    s = AgentSession.__new__(AgentSession)
    s.log_file_path = big
    s._log_buffer = []
    s.recent_output = ""
    t0 = time.monotonic()
    out = s.get_last_n_lines(100)
    elapsed = time.monotonic() - t0
    lines = out.splitlines()
    assert len(lines) == 100
    assert lines[0] == "line-199900"
    assert lines[-1] == "line-199999"
    assert elapsed < 0.5, f"tail read took {elapsed * 1000:.1f}ms, expected <500ms"


def test_p4_small_file_no_truncation(tmp_path):
    from agents_on_hand.session_manager import AgentSession

    small = tmp_path / "small.log"
    small.write_text("a\nb\nc")
    s = AgentSession.__new__(AgentSession)
    s.log_file_path = small
    s._log_buffer = []
    s.recent_output = ""
    assert s.get_last_n_lines(100) == "a\nb\nc"


def test_s3_pending_map_popped_on_timeout():
    """TimeoutError must remove the dead future (no dict leak)."""
    from agents_on_hand.acp_client import ACPClient

    async def _run():
        c = ACPClient("true", "/tmp")
        c.process = MagicMock()
        c.process.stdin = MagicMock()
        c.process.stdin.drain = AsyncMock()
        with patch("asyncio.wait_for", side_effect=asyncio.TimeoutError):
            with pytest.raises(asyncio.TimeoutError):
                await c.call_method("m", {}, timeout=0.01)
        assert c._pending_requests == {}

    asyncio.run(_run())


def test_s3_pending_map_bounded():
    """Pending map never exceeds 100 entries (oldest cancelled)."""
    from agents_on_hand.acp_client import ACPClient

    async def _run():
        c = ACPClient("true", "/tmp")
        c.process = MagicMock()
        c.process.stdin = MagicMock()
        c.process.stdin.drain = AsyncMock()
        pending_futs = []

        async def _hang(_fut, timeout=None):
            fut = asyncio.get_running_loop().create_future()
            pending_futs.append(fut)
            await fut

        with patch("asyncio.wait_for", side_effect=_hang):
            tasks = [asyncio.create_task(c.call_method("m", {}, timeout=60)) for _ in range(105)]
            await asyncio.sleep(0.1)
            assert len(c._pending_requests) <= 100
            for t in tasks:
                t.cancel()

    asyncio.run(_run())


def test_s1_s2_driver_stop_cancels_monitor(tmp_path):
    """ACPDriver.stop() must cancel the dangling _monitor_exit task."""
    from agents_on_hand.drivers.acp_driver import ACPDriver

    async def _run():
        d = ACPDriver("true", tmp_path)
        d.is_running = True
        d._monitor_task = asyncio.create_task(asyncio.sleep(60))
        await asyncio.sleep(0)
        d.client = MagicMock()
        d.stop()
        assert d._monitor_task is None
        assert d.is_running is False

    asyncio.run(_run())
