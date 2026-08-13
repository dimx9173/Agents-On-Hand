"""
Unified Session Manager for Agents-On-Hand with Probing Chain Protocol Driver Architecture.
"""

import asyncio
import logging
import uuid
import time
from pathlib import Path
from typing import Dict, Optional, Callable, List, Any

from .config import SESSION_LOG_DIR, AVAILABLE_CLI_AGENTS
from .ansi_cleaner import strip_ansi_codes
from .logging_setup import SessionTraceLogger
from .drivers import (
    BaseDriver,
    DriverEvent,
    ACPDriver,
    PiRPCDriver,
    ClaudeStreamDriver,
    PTYDriver,
)

logger = logging.getLogger(__name__)


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
        on_exit_callback: Optional[Callable[["AgentSession"], None]] = None,
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
        self._on_exit_callback = on_exit_callback

        self.driver: Optional[BaseDriver] = None
        self.active_driver_name: str = "none"

        # Buffer for recent live streaming
        self.recent_output: str = ""

        # Structured per-session trace log
        self.trace = SessionTraceLogger(session_id)
        self.trace.event("SESSION_INIT", f"agent={agent_name} command={command} cwd={working_dir}")

        # Timing helpers
        self._response_start_time: Optional[float] = None
        self._first_token_time: Optional[float] = None
        self._response_chars: int = 0

        # One-shot background completion callback.
        # Set by bot when user switches away from this session while it is still
        # processing.  Fired once on the next EXIT event so the bot can send a
        # "background response complete" notification.
        self._bg_completion_callback: Optional[Callable[["AgentSession"], None]] = None

    @property
    def is_acp(self) -> bool:
        """Return True if using a structured protocol (ACP, Pi RPC, Claude Stream)."""
        return self.active_driver_name in ("acp", "pi_rpc", "claude_stream")

    async def start(self, preferred_drivers: List[str]) -> bool:
        """
        Execute Probing Chain to instantiate the highest priority working driver.
        Probing order: acp -> pi_rpc -> claude_stream -> pty (lowest fallback).
        """
        self.working_dir.mkdir(parents=True, exist_ok=True)
        logger.info(
            f"Starting session {self.session_id} ({self.agent_name}): command='{self.command}', probing drivers={preferred_drivers}"
        )

        driver_map = {
            "acp": ACPDriver,
            "pi_rpc": PiRPCDriver,
            "claude_stream": ClaudeStreamDriver,
            "pty": PTYDriver,
        }

        for driver_name in preferred_drivers:
            driver_cls = driver_map.get(driver_name)
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
            return True

        self.is_running = False
        return False

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

        elif event.event_type == DriverEvent.EXIT:
            # Record response completion timing before marking exit
            if self._response_start_time is not None:
                elapsed = time.monotonic() - self._response_start_time
                self.trace.agent_response_done(self.agent_name, self._response_chars, elapsed)
            self.trace.event("DRIVER_EXIT", f"driver={self.active_driver_name}")
            self.is_running = False

            # Fire one-shot background completion callback (set when user switched away)
            bg_cb = self._bg_completion_callback
            self._bg_completion_callback = None
            if bg_cb:
                try:
                    bg_cb(self)
                except Exception as e:
                    logger.error(f"Error in background completion callback: {e}")

            if self._on_exit_callback:
                try:
                    self._on_exit_callback(self)
                except Exception as e:
                    logger.error(f"Error in session exit callback: {e}")



    def send_input(self, text: str):
        """Send prompt to active driver."""
        if self.driver and self.is_running:
            self.trace.user_input(text)
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
        self, callback: Optional[Callable[["AgentSession"], None]]
    ) -> None:
        """Register a one-shot callback fired when this session completes in the background.

        Called by the bot when the user switches to another session while this
        session is still processing.  The callback is invoked exactly once on the
        next DRIVER_EXIT event, then cleared automatically.
        Pass None to cancel a previously registered callback.
        """
        self._bg_completion_callback = callback


    def register_listener(self, callback: Callable[[DriverEvent], None]):
        """Register event listener to active driver.

        Also bridges ACP permission_request events as TOOL_REQUEST DriverEvents
        so that UnifiedStreamer can render the Telegram Inline Keyboard buttons.
        """
        if not self.driver:
            return
        self.driver.register_listener(callback)
        # Bridge permission_request → TOOL_REQUEST so stream_handler receives it
        if hasattr(self.driver, "register_permission_listener"):
            def _perm_bridge(req_id: Any, params: dict):
                callback(
                    DriverEvent(
                        DriverEvent.TOOL_REQUEST,
                        request_id=req_id,
                        tool_name=params.get("name") or params.get("title") or "Tool Execution",
                        tool_args=params.get("args") or params.get("description") or {},
                    )
                )
            # Store bridge so we can remove it during unregister
            if not hasattr(self, "_perm_bridges"):
                self._perm_bridges: Dict[Any, Any] = {}
            self._perm_bridges[id(callback)] = _perm_bridge
            self.driver.register_permission_listener(_perm_bridge)

    def unregister_listener(self, callback: Callable[[DriverEvent], None]):
        """Unregister event listener from active driver."""
        if not self.driver:
            return
        self.driver.unregister_listener(callback)
        # Remove bridged permission listener if present
        bridges = getattr(self, "_perm_bridges", {})
        bridge = bridges.pop(id(callback), None)
        if bridge and hasattr(self.driver, "unregister_permission_listener"):
            self.driver.unregister_permission_listener(bridge)

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
            with open(self.log_file_path, "r", encoding="utf-8", errors="replace") as f:
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

    def __init__(self):
        self.sessions: Dict[str, AgentSession] = {}
        self.user_active_session: Dict[int, str] = {}
        self._on_session_finished_callbacks: List[Callable[[AgentSession], None]] = []

    def register_on_finished_callback(self, cb: Callable[[AgentSession], None]):
        self._on_session_finished_callbacks.append(cb)

    def _handle_session_exit(self, session: AgentSession):
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
        custom_command: Optional[str] = None,
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
        return session

    def get_session(self, session_id: str) -> Optional[AgentSession]:
        return self.sessions.get(session_id)

    def get_active_session(self, user_id: int) -> Optional[AgentSession]:
        active_id = self.user_active_session.get(user_id)
        if active_id and active_id in self.sessions:
            return self.sessions[active_id]
        return None

    def set_active_session(self, user_id: int, session_id: str) -> bool:
        if session_id in self.sessions:
            self.user_active_session[user_id] = session_id
            return True
        return False

    def list_user_sessions(self, user_id: int) -> List[AgentSession]:
        return [s for s in self.sessions.values() if s.user_id == user_id]

    def kill_session(self, session_id: str) -> bool:
        session = self.sessions.get(session_id)
        if session:
            session.stop()
            del self.sessions[session_id]
            for uid, active_sid in list(self.user_active_session.items()):
                if active_sid == session_id:
                    del self.user_active_session[uid]
            return True
        return False


session_manager = SessionManager()
