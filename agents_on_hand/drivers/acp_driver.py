"""
ACP (Agent Client Protocol) Driver for OMP and OpenCode.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any, Optional
from .base_driver import BaseDriver, DriverEvent
from ..acp_client import ACPClient

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


class ACPDriver(BaseDriver):
    """Protocol Driver implementing standard ACP (JSON-RPC 2.0 stdio)."""

    def __init__(self, command: str, working_dir: Path):
        super().__init__(command, working_dir)
        self.client: Optional[ACPClient] = None

    async def start(self) -> bool:
        """Start the ACP subprocess and perform initialize + session/new handshake."""
        try:
            self.client = ACPClient(self.command, str(self.working_dir))
            self.client.register_listener(self._on_acp_update)
            self.client.register_permission_listener(self._on_acp_permission_request)

            await self.client.start()
            self.is_running = True
            asyncio.create_task(self._monitor_exit())
            return True
        except Exception as e:
            logger.warning(f"ACPDriver probing failed for command '{self.command}': {e}")
            self.is_running = False
            if self.client:
                self.client.stop()
            return False


    async def _monitor_exit(self):
        """Monitor client read loop and emit EXIT event when ACP process terminates."""
        if self.client and self.client._read_task:
            try:
                await self.client._read_task
            except Exception:
                pass
        self.is_running = False
        self.emit_event(DriverEvent(DriverEvent.EXIT, exit_code=0))

    def _on_acp_update(self, params: dict):
        """Process incoming ACP update notification and emit normalized DriverEvents."""
        text_delta = extract_acp_text_delta(params)
        if text_delta:
            # Check if this update represents a thought vs output text
            update_kind = ""
            if isinstance(params, dict) and "update" in params:
                up = params["update"]
                if isinstance(up, dict):
                    update_kind = up.get("sessionUpdate", "")

            if "thought" in update_kind:
                self.emit_event(DriverEvent(DriverEvent.THOUGHT_DELTA, content=text_delta))
            else:
                self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=text_delta))

    def _on_acp_permission_request(self, req_id: Any, params: dict):
        """Process ACP tool permission request."""
        tool_name = params.get("name", "Tool Execution") or params.get("title", "Tool")
        tool_args = params.get("args", {}) or params.get("description", {})
        self.emit_event(
            DriverEvent(
                DriverEvent.TOOL_REQUEST,
                request_id=req_id,
                tool_name=tool_name,
                tool_args=tool_args,
            )
        )

    def send_prompt(self, text: str):
        """Send prompt to ACP Agent."""
        if self.client and self.is_running:
            asyncio.create_task(self.client.prompt(text))

    def send_control_char(self, char: str):
        """Control chars are handled via prompt or SIGINT for ACP."""
        if self.client and self.client.process:
            try:
                if char in ("\x03", "\x1b"):
                    self.client.process.terminate()
            except Exception:
                pass

    async def respond_permission(self, request_id: Any, approved: bool):
        """Respond to permission request."""
        if self.client:
            await self.client.respond_to_permission(request_id, approved)

    def stop(self):
        """Stop the ACP client."""
        self.is_running = False
        if self.client:
            self.client.stop()
