"""
Unified Session Manager for Agents-On-Hand with Probing Chain Protocol Driver Architecture.
"""

import asyncio
import atexit
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .ansi_cleaner import strip_ansi_codes
from .config import AVAILABLE_CLI_AGENTS, SESSION_LOG_DIR, SESSION_STATE_FILE
from .drivers import (
    ACPDriver,
    BaseDriver,
    ClaudeStreamDriver,
    DriverEvent,
    PiRPCDriver,
    PTYDriver,
)
from .logging_setup import SessionTraceLogger
from .process_utils import is_pid_alive, kill_pid_safely
from .session_store import JSONSessionStore, SessionRecord

logger = logging.getLogger(__name__)

DRIVER_MAP: dict[str, type[BaseDriver]] = {
    "acp": ACPDriver,
    "pi_rpc": PiRPCDriver,
    "claude_stream": ClaudeStreamDriver,
    "pty": PTYDriver,
}


class AgentSession:
    """
    Unified Session wrapper for all Agents.
    Uses Probing Chain to select the highest-priority working driver (ACP, Pi RPC, Claude Stream, PTY).
    """

    def __init__(
        self,
        session_id: str,
        user_id: int,
        agent_key: str,
        agent_name: str,
        command: str,
        working_dir: Path,
        on_exit_callback: Callable[["AgentSession"], None] | None = None,
    ):
        self.session_id: str = session_id
        self.user_id: int = user_id
        self.agent_key: str = agent_key
        self.agent_name: str = agent_name
        self.command: str = command
        self.working_dir: Path = working_dir
        self.log_file_path: Path = SESSION_LOG_DIR / f"{session_id}.log"
        self.created_at: float = time.time()
        self.is_running: bool = False
        self.is_starting: bool = False
        self._on_exit_callback = on_exit_callback

        self.driver: BaseDriver | None = None
        self.active_driver_name: str = "none"

        self._listeners: list[Callable[[DriverEvent], None]] = []
        self._perm_bridges: dict[Any, Any] = {}

        # Buffer for recent live streaming
        self.recent_output: str = ""

        # P3 batched log writer: TEXT_DELTA events arrive at high frequency
        # (one open/write/close per delta). Buffer and flush on size (64KB),
        # age (2s), or turn end — cuts file syscalls ~100x under burst load.
        # NOTE: log_file_path honours a custom SESSION_LOG_DIR; the parent is
        # created lazily on first flush so import/construct stays side-effect free.
        self._log_buffer: list[str] = []
        self._log_buffer_chars: int = 0
        self._log_last_flush: float = 0.0

        # Structured per-session trace log
        self.trace = SessionTraceLogger(session_id)
        self.trace.event("SESSION_INIT", f"agent={agent_name} command={command} cwd={working_dir}")

        # Timing helpers
        self._response_start_time: float | None = None
        self._first_token_time: float | None = None
        self._response_chars: int = 0
        self.last_user_prompt: str = ""

        # One-shot background completion callback.
        # Set by bot when user switches away from this session while it is still
        # processing.  Fired once on the next EXIT event so the bot can send a
        # "background response complete" notification.
        self._bg_completion_callback: Callable[[AgentSession], None] | None = None

        # OS PID of the spawned agent process (recorded on driver bind).
        # Persisted so a crash/restart can reap a stale/orphaned process
        # left behind by a previous bot lifecycle.
        self.agent_pid: int | None = None

    @property
    def is_acp(self) -> bool:
        """Return True if using a structured protocol (ACP, Pi RPC, Claude Stream)."""
        return self.active_driver_name in ("acp", "pi_rpc", "claude_stream")

    async def start(self, preferred_drivers: list[str]) -> bool:
        """
        Execute Probing Chain to instantiate the highest priority working driver.
        Probing order: acp -> pi_rpc -> claude_stream -> pty (lowest fallback).
        """
        self.is_starting = True
        try:
            self.working_dir.mkdir(parents=True, exist_ok=True)
            logger.info(
                f"Starting session {self.session_id} ({self.agent_name}): command='{self.command}', probing drivers={preferred_drivers}"
            )

            total = len(preferred_drivers) + 1  # +1 for PTY fallback
            for idx, driver_name in enumerate(preferred_drivers, 1):
                driver_cls = DRIVER_MAP.get(driver_name)
                if not driver_cls:
                    continue

                logger.info(f"Probing driver '{driver_name}' for session {self.session_id}...")
                self.trace.driver_probe(driver_name, idx, total)
                candidate_driver = driver_cls(self.command, self.working_dir)
                # Wire trace for ACP observability
                if hasattr(candidate_driver, "set_trace"):
                    try:
                        candidate_driver.set_trace(self.trace)
                    except Exception:
                        pass
                candidate_driver.register_listener(self._on_driver_event)

                success = await candidate_driver.start()
                if success:
                    self.driver = candidate_driver
                    self.active_driver_name = driver_name
                    self.is_running = True
                    # Record OS PID for orphan-reaping on session delete.
                    self.agent_pid = candidate_driver.pid
                    for cb in list(self._listeners):
                        self._attach_listener_to_driver(cb)
                    logger.info(
                        f"Session {self.session_id} successfully bound to Driver '{driver_name}'"
                    )
                    self.trace.driver_bound(driver_name, True)
                    self.trace.event(
                        "SESSION_INIT",
                        f"agent={self.agent_name} command={self.command} cwd={self.working_dir} driver={driver_name}",
                    )
                    return True

                logger.warning(
                    f"Driver '{driver_name}' probing failed for session {self.session_id}. Trying next driver..."
                )
                self.trace.driver_bound(driver_name, False)

            # Final fallback to PTY Driver
            logger.warning(
                f"All probing drivers failed for session {self.session_id}. Falling back to PTY..."
            )
            self.trace.driver_probe("pty", total, total)
            pty = PTYDriver(self.command, self.working_dir)
            if hasattr(pty, "set_trace"):
                try:
                    pty.set_trace(self.trace)
                except Exception:
                    pass
            pty.register_listener(self._on_driver_event)
            if await pty.start():
                self.driver = pty
                self.active_driver_name = "pty"
                self.is_running = True
                self.agent_pid = pty.pid
                for cb in list(self._listeners):
                    self._attach_listener_to_driver(cb)
                self.trace.driver_bound("pty", True)
                return True
            self.trace.driver_bound("pty", False)

            self.is_running = False
            return False
        finally:
            self.is_starting = False

    def _on_driver_event(self, event: DriverEvent):
        """Handle events from driver, append to log file, and write trace entries."""
        if event.event_type == DriverEvent.TEXT_DELTA and event.content:
            # Track first-token timing
            if self._first_token_time is None and self._response_start_time is not None:
                self._first_token_time = time.monotonic()
                ttft = self._first_token_time - self._response_start_time
                self.trace.agent_first_token(self.agent_name, ttft)

            self._response_chars += len(event.content)
            self.recent_output += event.content
            if len(self.recent_output) > 10000:
                self.recent_output = self.recent_output[-8000:]
            self._buffer_log(event.content)

        elif event.event_type == DriverEvent.THOUGHT_DELTA and event.content:
            self.trace.thought_delta(len(event.content))

        elif event.event_type == DriverEvent.TOOL_REQUEST:
            self.trace.tool_request(
                event.request_id,
                getattr(event, "tool_name", "unknown"),
                str(getattr(event, "tool_args", "")),
            )
            self.trace.perm_request(event.request_id, getattr(event, "tool_name", "unknown"))

        elif event.event_type == DriverEvent.TOOL_RESULT:
            self.trace.tool_result(
                event.request_id, getattr(event, "tool_name", "unknown"), str(event.content)[:200]
            )

        elif event.event_type in (DriverEvent.TURN_END, DriverEvent.EXIT):
            self.flush_log_buffer()
            # Record response completion timing
            if self._response_start_time is not None:
                elapsed = time.monotonic() - self._response_start_time
                self.trace.agent_response_done(self.agent_name, self._response_chars, elapsed)
            self.trace.turn_end(driver=self.active_driver_name, reason=event.event_type)

            if event.event_type == DriverEvent.EXIT:
                self.is_running = False

            # Fire one-shot background completion callback (set when user switched away)
            bg_cb = self._bg_completion_callback
            self._bg_completion_callback = None
            if bg_cb:
                try:
                    bg_cb(self)
                except Exception as e:
                    logger.error(f"Error in background completion callback: {e}")

            if event.event_type == DriverEvent.EXIT and self._on_exit_callback:
                try:
                    self._on_exit_callback(self)
                except Exception as e:
                    logger.error(f"Error in session exit callback: {e}")

    def _buffer_log(self, content: str) -> None:
        """Append to the batched log buffer, flushing on size or age."""
        self._log_buffer.append(content)
        self._log_buffer_chars += len(content)
        now = time.monotonic()
        if self._log_buffer_chars >= 65536 or (now - self._log_last_flush) >= 2.0:
            self.flush_log_buffer()

    def flush_log_buffer(self) -> None:
        """Write buffered log content to disk in a single append (fsync-free)."""
        if not self._log_buffer:
            return
        data = "".join(self._log_buffer)
        self._log_buffer = []
        self._log_buffer_chars = 0
        self._log_last_flush = time.monotonic()
        try:
            self.log_file_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(data)
        except Exception as e:
            logger.error(f"Error writing session log: {e}")

    def send_input(self, text: str, turn_id: str | None = None):
        """Send prompt to active driver."""
        if self.driver and self.is_running:
            self.last_user_prompt = text
            self.trace.user_input(text, turn_id=turn_id)
            # Record user prompt in log file for complete conversation history
            self._buffer_log(f"\n👤 User: {text}\n\n")

            # Reset per-response timing counters
            self._response_start_time = time.monotonic()
            self._first_token_time = None
            self._response_chars = 0
            self.driver.send_prompt(text)

    def send_control_char(self, char: str):
        """Send control character (ESC/Ctrl+C) to active driver."""
        if self.driver and self.is_running:
            self.driver.send_control_char(char)

    async def respond_permission(self, request_id: Any, approved: bool):
        """Respond to permission request."""
        self.trace.permission_response(request_id, approved)
        if self.driver:
            await self.driver.respond_permission(request_id, approved)

    def set_background_completion_callback(
        self, callback: Callable[["AgentSession"], None] | None
    ) -> None:
        """Register a one-shot callback fired when this session completes in the background.

        Called by the bot when the user switches to another session while this
        session is still processing.  The callback is invoked exactly once on the
        next DRIVER_EXIT event, then cleared automatically.
        Pass None to cancel a previously registered callback.
        """
        self._bg_completion_callback = callback

    def _attach_listener_to_driver(self, callback: Callable[[DriverEvent], None]):
        """Attach a registered listener to the current active driver."""
        if not self.driver:
            return
        self.driver.register_listener(callback)

    def register_listener(self, callback: Callable[[DriverEvent], None]):
        """Register event listener to session / active driver."""
        if callback not in self._listeners:
            self._listeners.append(callback)
        if self.driver:
            self._attach_listener_to_driver(callback)

    def unregister_listener(self, callback: Callable[[DriverEvent], None]):
        """Unregister event listener from session / active driver."""
        if callback in self._listeners:
            self._listeners.remove(callback)
        if self.driver:
            self.driver.unregister_listener(callback)

    def stop(self) -> int | None:
        """Stop active session and driver.

        Returns the agent PID *before* clearing it, so callers
        (kill_session / prune / shutdown) can reap a process that
        outlived driver.stop() — without this the orphan-kill below
        would always see None (dead code).
        """
        self.is_running = False
        self.flush_log_buffer()
        if self.driver:
            self.driver.stop()
        # Flush and close structured trace log
        try:
            self.trace.close()
        except Exception:
            pass
        stale_pid = self.agent_pid
        self.agent_pid = None
        return stale_pid

    def get_last_n_lines(self, n: int = 100) -> str:
        """Read last N lines from log file (tail-seek, O(window) not O(file))."""
        self.flush_log_buffer()
        if not self.log_file_path.exists():
            return self.recent_output or "(No logs recorded yet)"

        try:
            # Seek-based tail: read at most the last 64KB instead of the
            # whole file. 100 lines of agent output virtually always fit;
            # fall back to a full read only if fewer than n lines found.
            with open(self.log_file_path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 65536))
                tail_text = f.read().decode("utf-8", errors="replace")
            tail_lines = tail_text.splitlines(keepends=True)
            # A 64KB window holds far more than n=100 lines in practice; only
            # fall back to a full read when the window didn't reach the file
            # start AND looks truncated (fewer newline-terminated lines than n).
            newline_count = tail_text.count("\n")
            if size > 65536 and newline_count < n:
                with open(self.log_file_path, encoding="utf-8", errors="replace") as f:
                    tail_lines = f.readlines()
            last_lines = "".join(tail_lines[-n:])
            return strip_ansi_codes(last_lines)
        except Exception as e:
            logger.error(f"Error reading log file: {e}")
            return f"Error reading log file: {e}"


# Aliases for backwards compatibility with tests and handlers
CLISession = AgentSession
ACPSession = AgentSession


class SessionManager:
    """Manager for multi-session CLI/ACP Agents."""

    def __init__(self, store_path: Path | None = None):
        self.sessions: dict[str, AgentSession] = {}
        self.user_active_session: dict[int, str] = {}
        self._on_session_finished_callbacks: list[Callable[[AgentSession], None]] = []
        self._store = JSONSessionStore(store_path or SESSION_STATE_FILE)
        self._load_from_store()

    def register_on_finished_callback(self, cb: Callable[[AgentSession], None]):
        self._on_session_finished_callbacks.append(cb)

    def _to_record(self, s: AgentSession) -> SessionRecord:
        return SessionRecord(
            session_id=s.session_id,
            user_id=s.user_id,
            agent_key=s.agent_key,
            agent_name=s.agent_name,
            command=s.command,
            working_dir=str(s.working_dir),
            created_at=s.created_at,
            pid=s.agent_pid,
        )

    def _save_to_store(self) -> None:
        try:
            records = [self._to_record(s) for s in self.sessions.values()]
            self._store.save_state(records, self.user_active_session)
        except Exception as e:
            logger.warning(f"Failed to persist sessions: {e}")

    def _load_from_store(self) -> None:
        try:
            records, active = self._store.load_state()
            for r in records:
                s = AgentSession(
                    session_id=r.session_id,
                    user_id=r.user_id,
                    agent_key=r.agent_key,
                    agent_name=r.agent_name,
                    command=r.command,
                    working_dir=Path(r.working_dir),
                    on_exit_callback=self._handle_session_exit,
                )
                s.is_running = False
                s.created_at = r.created_at
                stale_pid: int | None = getattr(r, "pid", None)
                # A stale PID from a previous bot lifecycle may already be dead
                # (or worse, recycled). Only keep it if the process still exists;
                # kill_session/prune reap it via kill_pid_safely (pgid-guarded).
                s.agent_pid = stale_pid if is_pid_alive(stale_pid) else None
                self.sessions[r.session_id] = s
            for uid, sid in active.items():
                if sid in self.sessions:
                    self.user_active_session[uid] = sid
        except Exception as e:
            logger.warning(f"Failed to load sessions: {e}")

    def _handle_session_exit(self, session: AgentSession):
        try:
            self._save_to_store()
        except Exception:
            pass
        for cb in self._on_session_finished_callbacks:
            try:
                cb(session)
            except Exception as e:
                logger.error(f"Error in session finished callback: {e}")

    def create_session(
        self,
        user_id: int,
        agent_key: str,
        working_dir: Path,
        custom_command: str | None = None,
    ) -> AgentSession:
        session_id = f"sess_{uuid.uuid4().hex[:8]}"

        preferred_drivers: list[str] = ["acp", "pty"]
        agent_name: str
        command: str
        if agent_key in AVAILABLE_CLI_AGENTS:
            agent_info = AVAILABLE_CLI_AGENTS[agent_key]
            agent_name = str(agent_info["name"])
            command = str(agent_info["command"])
            preferred_drivers = list(agent_info.get("drivers", ["acp", "pty"]))
        else:
            agent_name = f"Custom ({agent_key})"
            command = custom_command or agent_key

        session = AgentSession(
            session_id=session_id,
            user_id=user_id,
            agent_key=agent_key,
            agent_name=agent_name,
            command=command,
            working_dir=working_dir,
            on_exit_callback=self._handle_session_exit,
        )

        asyncio.create_task(session.start(preferred_drivers))

        self.sessions[session_id] = session
        self.user_active_session[user_id] = session_id
        self._save_to_store()
        return session

    def get_session(self, session_id: str) -> AgentSession | None:
        return self.sessions.get(session_id)

    def find_running_session(
        self, user_id: int, agent_key: str, working_dir: Path
    ) -> AgentSession | None:
        """Find the user's running session for the same agent + directory.

        PRP reuse: starting an agent in a directory that already has a live
        session would strand the old process (orphan) and split conversation
        context. Callers check this *before* create_session and offer to
        attach instead. Returns the most recently created match, else None.
        """
        try:
            target = working_dir.expanduser().resolve()
        except Exception:
            return None
        best: AgentSession | None = None
        for s in self.sessions.values():
            if s.user_id != user_id or s.agent_key != agent_key or not s.is_running:
                continue
            try:
                if s.working_dir.expanduser().resolve() != target:
                    continue
            except Exception:
                continue
            if best is None or s.created_at > best.created_at:
                best = s
        return best

    def get_active_session(self, user_id: int) -> AgentSession | None:
        active_id = self.user_active_session.get(user_id)
        if active_id and active_id in self.sessions:
            return self.sessions[active_id]
        return None

    def set_active_session(self, user_id: int, session_id: str) -> bool:
        if session_id in self.sessions:
            self.user_active_session[user_id] = session_id
            self._save_to_store()
            return True
        return False

    def list_user_sessions(self, user_id: int) -> list[AgentSession]:
        return [s for s in self.sessions.values() if s.user_id == user_id]

    def kill_session(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session:
            stale_pid = session.stop()
            if stale_pid is not None:
                kill_pid_safely(stale_pid)
            del self.sessions[session_id]
            for uid, active_sid in list(self.user_active_session.items()):
                if active_sid == session_id:
                    del self.user_active_session[uid]
            self._save_to_store()
            return True
        return False

    def prune_offline_sessions(self, user_id: int) -> int:
        """Remove all non-running sessions for a given user from memory and store.

        Returns the number of pruned sessions.
        """
        offline_ids = [
            sid for sid, s in self.sessions.items() if s.user_id == user_id and not s.is_running
        ]
        count = 0
        for sid in offline_ids:
            s = self.sessions.pop(sid, None)
            if s:
                stale_pid = s.stop()
                if stale_pid is not None:
                    kill_pid_safely(stale_pid)
                count += 1
            if self.user_active_session.get(user_id) == sid:
                del self.user_active_session[user_id]
        if count > 0:
            self._save_to_store()
        return count

    def shutdown_all_sessions(self) -> int:
        """Cleanly terminate all running sessions and their process trees on shutdown/exit."""
        count = 0
        for session in list(self.sessions.values()):
            if session.is_running:
                try:
                    stale_pid = session.stop()
                    if stale_pid is not None:
                        kill_pid_safely(stale_pid)
                    count += 1
                except Exception as e:
                    logger.error(
                        f"Error stopping session {session.session_id} during shutdown: {e}"
                    )
        if count > 0:
            logger.info(f"Shutdown cleaned up {count} active agent sessions.")
        return count


session_manager = SessionManager()
atexit.register(session_manager.shutdown_all_sessions)
