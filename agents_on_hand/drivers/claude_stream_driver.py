"""
Claude Code Stream-JSON Driver for claude -p --output-format=stream-json.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional
from .base_driver import BaseDriver, DriverEvent

logger = logging.getLogger(__name__)


class ClaudeStreamDriver(BaseDriver):
    """Protocol Driver for Claude Code running with --output-format=stream-json."""

    def __init__(self, command: str, working_dir: Path):
        cmd = command if "--output-format=stream-json" in command else f"{command} -p --output-format=stream-json"
        super().__init__(cmd, working_dir)
        self.process: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        """Start the claude process with stream-json format."""
        try:
            cmd_parts = self.command.split()
            self.process = await asyncio.create_subprocess_exec(
                *cmd_parts,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.working_dir),
            )
            self.is_running = True
            self._read_task = asyncio.create_task(self._read_loop())
            logger.info(f"ClaudeStreamDriver started with command='{self.command}'")
            return True
        except Exception as e:
            logger.error(f"ClaudeStreamDriver failed to start: {e}")
            self.is_running = False
            return False

    async def _read_loop(self):
        """Read stream-json output lines continuously from Claude Code."""
        while self.is_running and self.process and self.process.stdout:
            try:
                line = await self.process.stdout.readline()
                if not line:
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if not line_str:
                    continue

                try:
                    data = json.loads(line_str)
                    self._handle_json_msg(data)
                except json.JSONDecodeError:
                    self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=line_str + "\n"))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in ClaudeStreamDriver read loop: {e}")
                break

        self.is_running = False
        self.emit_event(DriverEvent(DriverEvent.EXIT, exit_code=0))

    def _handle_json_msg(self, data: dict):
        """Parse incoming stream-json events from Claude Code."""
        msg_type = data.get("type", "")

        if msg_type in ("text", "content_block_delta"):
            delta = data.get("delta", {})
            text = delta.get("text", "") if isinstance(delta, dict) else str(delta)
            if not text:
                text = data.get("text", "")
            if text:
                self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=str(text)))
        elif msg_type in ("thinking", "thought"):
            text = data.get("thinking", "") or data.get("text", "")
            if text:
                self.emit_event(DriverEvent(DriverEvent.THOUGHT_DELTA, content=str(text)))
        elif msg_type == "tool_use":
            tool_name = data.get("name", "Tool")
            tool_args = data.get("input", {})
            self.emit_event(
                DriverEvent(
                    DriverEvent.TOOL_REQUEST,
                    request_id=data.get("id", "req"),
                    tool_name=tool_name,
                    tool_args=tool_args,
                )
            )

    def send_prompt(self, text: str):
        """Send prompt to Claude Code stdio."""
        if self.process and self.process.stdin and self.is_running:
            msg_bytes = (text + "\n").encode("utf-8")
            self.process.stdin.write(msg_bytes)
            asyncio.create_task(self.process.stdin.drain())

    def send_control_char(self, char: str):
        """Send terminate signal for ESC/Ctrl+C."""
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass

    async def respond_permission(self, request_id: Any, approved: bool):
        """Respond to tool permission request."""
        if self.process and self.process.stdin and self.is_running:
            res = "y\n" if approved else "n\n"
            self.process.stdin.write(res.encode("utf-8"))
            await self.process.stdin.drain()

    def stop(self):
        """Stop Claude Code process."""
        self.is_running = False
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
