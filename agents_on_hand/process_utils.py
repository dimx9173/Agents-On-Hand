"""
Process Utilities for robust subprocess lifecycle and anti-zombie process guarantees.
"""

import ctypes
import logging
import os
import signal
import sys
from typing import Any

logger = logging.getLogger(__name__)


def set_pdeathsig_and_pgrp():
    """
    Subprocess preexec_fn:
    1. Sets new process group (os.setpgrp) so the entire process tree can be killed with os.killpg.
    2. Sets Linux PR_SET_PDEATHSIG to SIGKILL.
       If the parent Python process dies for ANY reason (crash, SIGKILL, segfault),
       the Linux kernel immediately dispatches SIGKILL to the child process!
    """
    try:
        os.setpgrp()
    except Exception:
        pass

    if sys.platform.startswith("linux"):
        try:
            # PR_SET_PDEATHSIG is 1 in sys/prctl.h; signal.SIGKILL is 9
            libc = ctypes.CDLL("libc.so.6")
            libc.prctl(1, signal.SIGKILL)
        except Exception as e:
            logger.debug(f"Failed to set PR_SET_PDEATHSIG: {e}")


def is_process_alive(proc: Any) -> bool:
    """Check if proc object is still actively running to prevent PID reuse hazards."""
    if not proc:
        return False
    # Check asyncio subprocess / standard subprocess returncode
    if getattr(proc, "returncode", None) is not None:
        return False
    # Check poll()
    if callable(getattr(proc, "poll", None)):
        try:
            if proc.poll() is not None:
                return False
        except Exception:
            pass
    # Check pexpect isalive()
    if callable(getattr(proc, "isalive", None)):
        try:
            if not proc.isalive():
                return False
        except Exception:
            pass
    return True


def is_pid_alive(pid: int | None) -> bool:
    """Check whether a raw OS PID currently exists (kill-0 probe, no signal sent)."""
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, owned by another user
    except Exception:
        return False


def kill_pid_safely(pid: int | None, timeout: float = 0.5) -> bool:
    """SIGTERM-then-SIGKILL a raw PID with the same guards as kill_process_tree.

    Used to reap orphaned agent processes whose driver object is already gone
    (session deleted after a crash, stale state.json record). Returns True if
    a signal was delivered, False if there was nothing to kill.
    Guards: pid must be > 1, not ourselves, and its pgid must exist, be > 1,
    and differ from our own process group (PID-reuse protection).
    """
    if not isinstance(pid, int) or pid <= 1:
        return False
    if pid == os.getpid():
        return False
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False
    # System/root process groups must never be signalled (PID-reuse guard).
    if pgid <= 1:
        return False
    # NOTE: unlike kill_process_tree's killpg path, a raw PID kill only ever
    # signals the single pid — never the group — so sharing our pgid is fine
    # (normal case: agent child spawned without setpgrp). killpg is untouched.
    try:
        os.kill(pid, signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return False
    except Exception:
        return False
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if not is_pid_alive(pid):
            return True
        _time.sleep(0.05)
    try:
        os.kill(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    except Exception:
        pass
    return True


def kill_process_tree(proc: Any, timeout: float = 0.5) -> None:
    """
    Safely terminates a subprocess and its entire child process tree.
    Guarantees:
    - Never operates on already-exited processes to prevent Linux PID reuse hazards.
    - Never kills current process or current process group.
    - Never targets root or system pgids (<= 1).
    - Escalates safely from SIGTERM to SIGKILL only on valid dedicated child pgids.
    """
    if not proc:
        return

    # 1. Guard against dead/exited processes (prevents PID recycling attacks on system processes)
    if not is_process_alive(proc):
        return

    pid = getattr(proc, "pid", None)
    if not isinstance(pid, int) or pid <= 1:
        # Fallback to direct object termination if available
        if hasattr(proc, "terminate") and callable(proc.terminate):
            try:
                proc.terminate()
            except Exception:
                pass
        if hasattr(proc, "kill") and callable(proc.kill):
            try:
                proc.kill()
            except Exception:
                pass
        return

    current_pid = os.getpid()
    current_pgid = os.getpgrp()

    # Never kill ourselves
    if pid == current_pid:
        return

    # Check process group
    try:
        pgid = os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        pgid = None
    except Exception:
        pgid = None

    # System/root pgids (<= 1) must never be signalled — not even via the pid fallback.
    if pgid is not None and pgid <= 1:
        return
    # Only kill process group if pgid is valid, > 1, and NOT our own parent/current process group!
    if pgid is not None and pgid > 1 and pgid != current_pgid:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        except Exception:
            pass

        # Escalate to SIGKILL on child's dedicated process group
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        except Exception:
            pass
    else:
        # If child shares our process group (or pgid is invalid), kill ONLY this specific pid
        try:
            os.kill(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            pass
        except Exception:
            pass

        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        except Exception:
            pass

    # Direct process termination fallback on the object
    if hasattr(proc, "terminate") and callable(proc.terminate):
        try:
            proc.terminate()
        except Exception:
            pass
    if hasattr(proc, "kill") and callable(proc.kill):
        try:
            proc.kill()
        except Exception:
            pass
