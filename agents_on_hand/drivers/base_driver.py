"""
Base Driver Interface for Agents-On-Hand Protocol Drivers.
"""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Callable, Optional, Dict, Any, List
import logging

logger = logging.getLogger(__name__)


class DriverEvent:
    """Standardized event payload for all protocol drivers."""

    TEXT_DELTA = "text_delta"
    THOUGHT_DELTA = "thought_delta"
    TOOL_REQUEST = "tool_request"
    TOOL_RESULT = "tool_result"
    TURN_END = "turn_end"
    EXIT = "exit"

    def __init__(
        self,
        event_type: str,
        content: str = "",
        request_id: Optional[Any] = None,
        tool_name: str = "",
        tool_args: Optional[Dict[str, Any]] = None,
        exit_code: int = 0,
    ):
        self.event_type = event_type
        self.content = content
        self.request_id = request_id
        self.tool_name = tool_name
        self.tool_args = tool_args or {}
        self.exit_code = exit_code

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.event_type,
            "content": self.content,
            "request_id": self.request_id,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "exit_code": self.exit_code,
        }


class BaseDriver(ABC):
    """
    Abstract Protocol Driver Base Class.
    All protocol drivers (ACP, Pi RPC, Claude Stream, PTY) inherit from this class.
    """

    def __init__(self, command: str, working_dir: Path):
        self.command: str = command
        self.working_dir: Path = working_dir
        self.is_running: bool = False
        self._listeners: List[Callable[[DriverEvent], None]] = []

    def register_listener(self, callback: Callable[[DriverEvent], None]):
        """Register listener for standardized DriverEvents."""
        if callback not in self._listeners:
            self._listeners.append(callback)

    def unregister_listener(self, callback: Callable[[DriverEvent], None]):
        """Unregister listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def emit_event(self, event: DriverEvent):
        """Emit a DriverEvent to all registered listeners."""
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.error(f"Error in Driver listener: {e}", exc_info=True)

    @abstractmethod
    async def start(self) -> bool:
        """
        Start the agent process and perform initial handshake/probe.
        Returns True if successful, False if handshake/probe failed.
        """
        pass

    @abstractmethod
    def send_prompt(self, text: str):
        """Send a user prompt/input to the agent."""
        pass

    @abstractmethod
    def send_control_char(self, char: str):
        """Send a control character (e.g. ESC or Ctrl+C) to the agent."""
        pass

    @abstractmethod
    async def respond_permission(self, request_id: Any, approved: bool):
        """Respond to a tool execution permission request."""
        pass

    @abstractmethod
    def stop(self):
        """Stop the driver process gracefully."""
        pass
