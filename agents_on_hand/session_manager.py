"""
Unified Session Manager for Agents-On-Hand with Probing Chain Protocol Driver Architecture.
"""

import asyncio
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

            for driver_name in preferred_drivers:
                driver_cls = DRIVER_MAP.get(driver_name)
                if not driver_cls:
                    continue

                logger.info(f"Probing driver '{driver_name}' for session {self.session_id}...")
                candidate_driver = driver_cls(self.command, self.working_dir)
                candidate_driver.register_listener(self._on_driver_event)

                success = await candidate_driver.start()
                if success:
                    self.driver = candidate_driver
                    self.active_driver_name = driver_name
                    self.is_running = True
                    for cb in list(self._listeners):
                        self._attach_listener_to_driver(cb)
                    logger.info(
                        f"Session {self.session_id} successfully bound to Driver '{driver_name}'"
                    )
                    return True

                logger.warning(
                    f"Driver '{driver_name}' probing failed for session {self.session_id}. Trying next driver..."
                )

            # Final fallback to PTY Driver
            logger.warning(f"All probing drivers failed for session {self.session_id}. Falling back to PTY...")
            pty = PTYDriver(self.command, self.working_dir)
            pty.register_listener(self._on_driver_event)
            if await pty.start():
                self.driver = pty
                self.active_driver_name = "pty"
                self.is_running = True
                for cb in list(self._listeners):
                    self._attach_listener_to_driver(cb)
                return True

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
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(event.content)

        elif event.event_type == DriverEvent.THOUGHT_DELTA and event.content:
            self.trace.event("THOUGHT_DELTA", f"{len(event.content)} chars")

        elif event.event_type == DriverEvent.TOOL_REQUEST:
            self.trace.tool_request(event.request_id, getattr(event, "tool_name", "unknown"))

        elif event.event_type in (DriverEvent.TURN_END, DriverEvent.EXIT):
            # Record response completion timing
            if self._response_start_time is not None:
                elapsed = time.monotonic() - self._response_start_time
                self.trace.agent_response_done(self.agent_name, self._response_chars, elapsed)
            self.trace.event("TURN_END", f"driver={self.active_driver_name} type={event.event_type}")

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



    def send_input(self, text: str):
        """Send prompt to active driver."""
        if self.driver and self.is_running:
            self.last_user_prompt = text
            self.trace.user_input(text)
            # Record user prompt in log file for complete conversation history
            try:
                with open(self.log_file_path, "a", encoding="utf-8") as f:
                    f.write(f"\n👤 User: {text}\n\n")
            except Exception as e:
                logger.error(f"Error writing user prompt to log: {e}")

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

    def stop(self):
        """Stop active session and driver."""
        self.is_running = False
        if self.driver:
            self.driver.stop()
        # Flush and close structured trace log
        try:
            self.trace.close()
        except Exception:
            pass

    def get_last_n_lines(self, n: int = 100) -> str:
        """Read last N lines from log file."""
        if not self.log_file_path.exists():
            return self.recent_output or "(No logs recorded yet)"

        try:
            with open(self.log_file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                last_lines = "".join(lines[-n:])
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
        return SessionRecord(session_id=s.session_id, user_id=s.user_id, agent_key=s.agent_key, agent_name=s.agent_name, command=s.command, working_dir=str(s.working_dir), created_at=s.created_at)

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
                s = AgentSession(session_id=r.session_id, user_id=r.user_id, agent_key=r.agent_key, agent_name=r.agent_name, command=r.command, working_dir=Path(r.working_dir), on_exit_callback=self._handle_session_exit)
                s.is_running = False
                s.created_at = r.created_at
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

        preferred_drivers = ["acp", "pty"]
        if agent_key in AVAILABLE_CLI_AGENTS:
            agent_info = AVAILABLE_CLI_AGENTS[agent_key]
            agent_name = agent_info["name"]
            command = agent_info["command"]
            preferred_drivers = agent_info.get("drivers", ["acp", "pty"])
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
            session.stop()
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
            sid for sid, s in self.sessions.items()
            if s.user_id == user_id and not s.is_running
        ]
        count = 0
        for sid in offline_ids:
            s = self.sessions.pop(sid, None)
            if s:
                s.stop()
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
                    session.stop()
                    count += 1
                except Exception as e:
                    logger.error(f"Error stopping session {session.session_id} during shutdown: {e}")
        if count > 0:
            logger.info(f"Shutdown cleaned up {count} active agent sessions.")
        return count


session_manager = SessionManager()
import atexit
atexit.register(session_manager.shutdown_all_sessions)
