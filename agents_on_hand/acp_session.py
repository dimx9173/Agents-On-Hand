import asyncio
import logging
import time
from collections.abc import Callable
from pathlib import Path

from .acp_client import ACPClient
from .config import SESSION_LOG_DIR

logger = logging.getLogger(__name__)


def extract_acp_text_delta(params: dict) -> str:
    """Safely extract string text from any ACP notification params."""
    if not isinstance(params, dict):
        return ""

    raw = params.get("content") or params.get("delta")
    if not raw and "update" in params:
        up = params["update"]
        if isinstance(up, dict):
            raw = up.get("content") or up.get("delta") or up.get("text")

    if isinstance(raw, dict):
        if "text" in raw and isinstance(raw["text"], str):
            return raw["text"]
        elif "delta" in raw and isinstance(raw["delta"], str):
            return raw["delta"]
        return ""
    elif isinstance(raw, str):
        return raw
    elif raw is not None:
        return str(raw)

    return ""


class ACPSession:

    """
    Session wrapper for ACP (Agent Client Protocol) Agents.
    Presents the same unified interface as CLISession to session_manager and bot.py.
    """

    def __init__(
        self,
        session_id: str,
        user_id: int,
        agent_key: str,
        agent_name: str,
        command: str,
        working_dir: Path,
        on_exit_callback: Callable[["ACPSession"], None] | None = None,
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
        self.is_acp: bool = True

        self.client: ACPClient | None = None
        self._listeners: list[Callable[[dict], None]] = []
        self._permission_listeners: list[Callable[[int, dict], None]] = []
        self._on_exit_callback = on_exit_callback
        self.recent_output: str = ""

    async def start(self):
        """Start the ACP subprocess and perform handshake."""
        self.working_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Starting ACPSession {self.session_id} ({self.agent_name}): command='{self.command}'")

        self.client = ACPClient(self.command, str(self.working_dir))
        self.client.register_listener(self._on_update)
        self.client.register_permission_listener(self._on_permission_request)

        try:
            await self.client.start()
            self.is_running = True
            asyncio.create_task(self._monitor_exit())
        except Exception as e:
            logger.error(f"Failed to start ACPSession {self.session_id}: {e}")
            self.is_running = False
            if self._on_exit_callback:
                try:
                    self._on_exit_callback(self)
                except Exception as ex:
                    logger.error(f"Error in ACPSession exit callback: {ex}")


    async def _monitor_exit(self):
        """Monitor client read loop and trigger exit callback when ACP process terminates."""
        if self.client and self.client._read_task:
            try:
                await self.client._read_task
            except Exception:
                pass
        self.is_running = False
        logger.info(f"ACPSession process exited: {self.session_id}")
        if self._on_exit_callback:
            try:
                self._on_exit_callback(self)
            except Exception as e:
                logger.error(f"Error in ACPSession exit callback: {e}")


    def register_listener(self, callback: Callable[[dict], None]):
        """Register listener for ACP updates."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def register_permission_listener(self, callback: Callable[[int, dict], None]):
        """Register listener for ACP tool permission requests."""
        if callback not in self._permission_listeners:
            self._permission_listeners.append(callback)

    def unregister_listener(self, callback: Callable):
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _on_update(self, params: dict):
        """Handle incoming update params from ACP Client."""
        text_delta = extract_acp_text_delta(params)
        if text_delta:
            self.recent_output += text_delta
            with open(self.log_file_path, "a", encoding="utf-8") as f:
                f.write(text_delta)

        for listener in self._listeners:
            try:
                listener(params)
            except Exception as e:
                logger.error(f"Error in ACPSession listener: {e}", exc_info=True)


    def _on_permission_request(self, req_id: int, params: dict):
        """Forward permission request to registered listeners."""
        for callback in self._permission_listeners:
            try:
                callback(req_id, params)
            except Exception as e:
                logger.error(f"Error in ACPSession permission listener: {e}")

    def send_input(self, text: str):
        """Send prompt to ACP agent via background task."""
        if self.client and self.client.is_running:
            logger.info(f"Sending prompt to ACPSession {self.session_id}: '{text}'")
            asyncio.create_task(self.client.prompt(text))

    def send_control_char(self, char: str):
        """Send cancel signal if ESC or Ctrl+C passed."""
        if self.client and self.client.is_running:
            logger.info(f"Sending cancel to ACPSession {self.session_id}")
            asyncio.create_task(self.client.cancel())

    async def respond_permission(self, req_id: int, approved: bool):
        """Respond to permission request."""
        if self.client:
            await self.client.respond_to_permission(req_id, approved)

    def get_last_n_lines(self, n: int = 100) -> str:
        """Read last N lines from session log file."""
        if not self.log_file_path.exists():
            return "(No log content yet)"
        try:
            with open(self.log_file_path, encoding="utf-8", errors="replace") as f:
                lines = f.readlines()
                return "".join(lines[-n:])
        except Exception as e:
            return f"Error reading log: {e}"

    def kill(self):
        """Terminate the ACP session."""
        self.is_running = False
        if self.client:
            self.client.stop()
        if self._on_exit_callback:
            try:
                self._on_exit_callback(self)
            except Exception:
                pass
