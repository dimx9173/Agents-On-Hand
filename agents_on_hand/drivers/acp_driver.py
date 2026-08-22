"""
ACP (Agent Client Protocol) Driver for OMP and OpenCode.
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

from ..acp_client import ACPClient
from .base_driver import BaseDriver, DriverEvent

logger = logging.getLogger(__name__)


def _extract_text_from_node(node: Any) -> str:
    """Recursively extract plain text from ACP content structures without stringifying raw containers."""
    if not node:
        return ""
    if isinstance(node, str):
        # Ignore raw internal JSON hook lines (e.g. OpenCode PostToolUse/transcript dumps)
        stripped = node.strip()
        if stripped.startswith('{"session_id":') and ("hook_event_name" in stripped or "transcript_path" in stripped):
            return ""
        return node
    elif isinstance(node, dict):
        if "text" in node and isinstance(node["text"], str):
            return node["text"]
        elif "delta" in node:
            return _extract_text_from_node(node["delta"])
        elif "content" in node:
            return _extract_text_from_node(node["content"])
        elif "output" in node and isinstance(node["output"], str):
            return node["output"]
        return ""
    elif isinstance(node, (list, tuple)):
        parts = [_extract_text_from_node(item) for item in node]
        return "".join(p for p in parts if p)
    return ""


def extract_acp_text_delta(params: dict) -> str:
    """Safely extract string text from any ACP notification params."""
    if not isinstance(params, dict):
        return ""

    raw = params.get("content") or params.get("delta")
    if not raw and "update" in params:
        up = params["update"]
        if isinstance(up, dict):
            raw = up.get("content") or up.get("delta") or up.get("text")
        else:
            raw = up

    return _extract_text_from_node(raw)


class ACPDriver(BaseDriver):
    """Protocol Driver implementing standard ACP (JSON-RPC 2.0 stdio)."""

    def __init__(self, command: str, working_dir: Path):
        super().__init__(command, working_dir)
        self.client: ACPClient | None = None

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
        if not isinstance(params, dict):
            return

        up = params.get("update") if isinstance(params.get("update"), dict) else params
        session_update = up.get("sessionUpdate", "") if isinstance(up, dict) else ""
        update_str = str(session_update).lower()

        # Informational toolCall progress updates should not trigger approval cards (real approvals arrive via _on_acp_permission_request)
        if any(kw in update_str for kw in ("toolcall", "tool_call", "tooluse", "tool_use")):
            return

        # Handle tool result / post tool use events
        if any(kw in update_str for kw in ("toolresult", "tool_result", "posttooluse", "tooloutput")):
            tool_name = up.get("tool_name") or up.get("name") or up.get("title") or "Tool"
            raw_content = up.get("content") or up.get("output") or up.get("result") or ""
            text = _extract_text_from_node(raw_content)
            if text:
                self.emit_event(DriverEvent(DriverEvent.TOOL_RESULT, tool_name=tool_name, content=text))
            return

        text_delta = extract_acp_text_delta(params)
        if text_delta:
            if "thought" in update_str or "thinking" in update_str:
                self.emit_event(DriverEvent(DriverEvent.THOUGHT_DELTA, content=text_delta))
            else:
                self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=text_delta))

    def _on_acp_permission_request(self, req_id: Any, params: dict):
        """Process real ACP tool permission request."""
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
            async def _do_prompt():
                try:
                    await self.client.prompt(text)
                except Exception as e:
                    logger.error(f"Error in ACP prompt: {e}")
                finally:
                    self.emit_event(DriverEvent(DriverEvent.TURN_END))

            asyncio.create_task(_do_prompt())

    def send_control_char(self, char: str):
        """Control chars are handled via session/cancel notification for ACP."""
        if self.client and self.is_running:
            if char in ("\x03", "\x1b"):
                asyncio.create_task(self.client.cancel())

    async def respond_permission(self, request_id: Any, approved: bool):
        """Respond to permission request."""
        if self.client:
            await self.client.respond_to_permission(request_id, approved)

    def stop(self):
        """Stop the ACP client."""
        self.is_running = False
        if self.client:
            self.client.stop()
