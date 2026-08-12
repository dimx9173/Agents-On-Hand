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
        """Handle events from driver and append to log file."""
        if event.event_type == DriverEvent.TEXT_DELTA and event.content:
            self.recent_output += event.content
            if len(self.recent_output) > 10000:
                self.recent_output = self.recent_output[-8000:]
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(event.content)

        elif event.event_type == DriverEvent.EXIT:
            self.is_running = False
            if self._on_exit_callback:
                try:
                    self._on_exit_callback(self)
                except Exception as e:
                    logger.error(f"Error in session exit callback: {e}")

    def send_input(self, text: str):
        """Send prompt to active driver."""
        if self.driver and self.is_running:
            self.driver.send_prompt(text)

    def send_control_char(self, char: str):
        """Send control character (ESC/Ctrl+C) to active driver."""
        if self.driver and self.is_running:
            self.driver.send_control_char(char)

    async def respond_permission(self, request_id: Any, approved: bool):
        """Respond to permission request."""
        if self.driver:
            await self.driver.respond_permission(request_id, approved)

    def register_listener(self, callback: Callable[[DriverEvent], None]):
        """Register event listener to active driver."""
        if self.driver:
            self.driver.register_listener(callback)

    def unregister_listener(self, callback: Callable[[DriverEvent], None]):
        """Unregister event listener from active driver."""
        if self.driver:
            self.driver.unregister_listener(callback)

    def stop(self):
        """Stop active session and driver."""
        self.is_running = False
        if self.driver:
            self.driver.stop()

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
