"""
Comprehensive Safety and Anti-Recycling Tests for process_utils.py.
Verifies that kill_process_tree NEVER terminates unrelated system processes,
never targets recycled PIDs of dead processes, and never kills the parent group.
"""

import os
import signal
import pytest
from unittest.mock import MagicMock, patch

from agents_on_hand.process_utils import is_process_alive, kill_process_tree, set_pdeathsig_and_pgrp


def test_is_process_alive_variants():
    """Verify is_process_alive handles all subprocess styles safely."""
    assert is_process_alive(None) is False

    # Standard / Asyncio subprocess: returncode is None when running, int when dead
    running_proc = MagicMock()
    running_proc.returncode = None
    running_proc.poll.return_value = None
    assert is_process_alive(running_proc) is True

    dead_proc_0 = MagicMock()
    dead_proc_0.returncode = 0
    assert is_process_alive(dead_proc_0) is False

    dead_proc_neg = MagicMock()
    dead_proc_neg.returncode = -15
    assert is_process_alive(dead_proc_neg) is False

    # Process with poll() returning exit status
    polled_dead = MagicMock()
    polled_dead.returncode = None
    polled_dead.poll.return_value = 1
    assert is_process_alive(polled_dead) is False

    # pexpect.spawn: isalive() method
    pty_proc = MagicMock(spec=["isalive"])
    pty_proc.isalive.return_value = True
    assert is_process_alive(pty_proc) is True

    pty_dead = MagicMock(spec=["isalive"])
    pty_dead.isalive.return_value = False
    assert is_process_alive(pty_dead) is False


def test_kill_process_tree_ignores_dead_processes_to_prevent_pid_recycling_attack():
    """Dead processes MUST be ignored so their recycled PIDs are never sent signals."""
    dead_proc = MagicMock()
    dead_proc.pid = 12345
    dead_proc.returncode = 0

    with patch("os.killpg") as mock_killpg, patch("os.kill") as mock_kill:
        kill_process_tree(dead_proc)
        mock_killpg.assert_not_called()
        mock_kill.assert_not_called()
        dead_proc.terminate.assert_not_called()
        dead_proc.kill.assert_not_called()


def test_kill_process_tree_self_and_parent_group_protection():
    """Never kill our own PID or our own process group."""
    current_pid = os.getpid()
    current_pgid = os.getpgrp()

    # Self PID
    self_proc = MagicMock()
    self_proc.pid = current_pid
    self_proc.returncode = None
    self_proc.poll.return_value = None

    with patch("os.killpg") as mock_killpg, patch("os.kill") as mock_kill:
        kill_process_tree(self_proc)
        mock_killpg.assert_not_called()
        mock_kill.assert_not_called()

    # Process sharing our process group (e.g. spawned without separate setpgrp)
    sibling_proc = MagicMock()
    sibling_proc.pid = 99999
    sibling_proc.returncode = None
    sibling_proc.poll.return_value = None

    with patch("os.getpgid", return_value=current_pgid), \
         patch("os.killpg") as mock_killpg, \
         patch("os.kill") as mock_kill:
        kill_process_tree(sibling_proc)
        # Should NEVER killpg our own group!
        mock_killpg.assert_not_called()
        # Should ONLY send kill to the specific child PID
        mock_kill.assert_any_call(99999, signal.SIGTERM)
        mock_kill.assert_any_call(99999, signal.SIGKILL)


def test_kill_process_tree_system_pgid_protection():
    """Never kill system or root process groups (<= 1)."""
    sys_proc = MagicMock()
    sys_proc.pid = 88888
    sys_proc.returncode = None
    sys_proc.poll.return_value = None

    for bad_pgid in (0, 1, -1):
        with patch("os.getpgid", return_value=bad_pgid), \
             patch("os.killpg") as mock_killpg, \
             patch("os.kill") as mock_kill:
            kill_process_tree(sys_proc)
            mock_killpg.assert_not_called()


def test_kill_process_tree_dedicated_child_group_escalation():
    """Dedicated child process group is cleanly escalated from SIGTERM to SIGKILL."""
    child_proc = MagicMock()
    child_proc.pid = 77777
    child_proc.returncode = None
    child_proc.poll.return_value = None

    dedicated_child_pgid = 77777  # Different from current_pgid

    with patch("os.getpgid", return_value=dedicated_child_pgid), \
         patch("os.getpgrp", return_value=1234), \
         patch("os.killpg") as mock_killpg:
        kill_process_tree(child_proc)
        mock_killpg.assert_any_call(dedicated_child_pgid, signal.SIGTERM)
        mock_killpg.assert_any_call(dedicated_child_pgid, signal.SIGKILL)
        assert child_proc.terminate.called
        assert child_proc.kill.called


def test_kill_process_tree_handles_errors_gracefully():
    """ProcessLookupError and PermissionError must never crash."""
    proc = MagicMock()
    proc.pid = 55555
    proc.returncode = None
    proc.poll.return_value = None

    with patch("os.getpgid", side_effect=ProcessLookupError):
        kill_process_tree(proc)  # Should not raise

    with patch("os.getpgid", side_effect=PermissionError):
        kill_process_tree(proc)  # Should not raise


def test_set_pdeathsig_and_pgrp_error_tolerance():
    """Verify set_pdeathsig_and_pgrp handles platform/permission errors silently."""
    with patch("os.setpgrp", side_effect=OSError):
        with patch("ctypes.CDLL", side_effect=Exception):
            set_pdeathsig_and_pgrp()  # Should not raise
