"""
Pi Agent RPC Driver for pi --mode rpc.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Optional
from .base_driver import BaseDriver, DriverEvent

logger = logging.getLogger(__name__)


class PiRPCDriver(BaseDriver):
    """Protocol Driver for Pi Agent running in --mode rpc JSON event mode."""

    def __init__(self, command: str, working_dir: Path):
        # Ensure command includes --mode rpc
        cmd = command if "--mode" in command else f"{command} --mode rpc"
        super().__init__(cmd, working_dir)
        self.process: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None

    async def start(self) -> bool:
        """Start the pi --mode rpc process and monitor stdout JSON events."""
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
            logger.info(f"PiRPCDriver started successfully with command='{self.command}'")
            return True
        except Exception as e:
            logger.error(f"PiRPCDriver failed to start: {e}")
            self.is_running = False
            return False

    async def _read_loop(self):
        """Read stdout JSON lines continuously from pi --mode rpc."""
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
                    # Non-JSON fallback output
                    self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=line_str + "\n"))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in PiRPCDriver read loop: {e}")
                break

        self.is_running = False
        self.emit_event(DriverEvent(DriverEvent.EXIT, exit_code=0))

    def _handle_json_msg(self, data: dict):
        """Parse incoming JSON events from Pi RPC."""
        msg_type = data.get("type", "")
        
        if msg_type in ("text_delta", "content_block_delta", "message_delta"):
            text = data.get("text", "") or data.get("delta", "") or data.get("content", "")
            if text:
                self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=str(text)))
        elif msg_type in ("thought_delta", "thinking"):
            text = data.get("text", "") or data.get("thought", "")
            if text:
                self.emit_event(DriverEvent(DriverEvent.THOUGHT_DELTA, content=str(text)))
        elif msg_type == "extension_ui_request":
            req_id = data.get("id", "")
            tool_name = data.get("method", "Tool Approval")
            status_text = data.get("statusText", "")
            self.emit_event(
                DriverEvent(
                    DriverEvent.TOOL_REQUEST,
                    request_id=req_id,
                    tool_name=tool_name,
                    tool_args={"info": status_text},
                )
            )
        elif "text" in data:
            self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=str(data["text"])))

    def send_prompt(self, text: str):
        """Send prompt JSON message to pi stdio stdin."""
        if self.process and self.process.stdin and self.is_running:
            payload = {"type": "prompt", "message": text}
            msg_bytes = (json.dumps(payload) + "\n").encode("utf-8")
            self.process.stdin.write(msg_bytes)
            asyncio.create_task(self.process.stdin.drain())

    def send_control_char(self, char: str):
        """Send SIGINT to process for Ctrl+C or ESC."""
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass

    async def respond_permission(self, request_id: Any, approved: bool):
        """Respond to extension UI permission request."""
        if self.process and self.process.stdin and self.is_running:
            payload = {
                "id": request_id,
                "type": "extension_ui_response",
                "approved": approved,
            }
            msg_bytes = (json.dumps(payload) + "\n").encode("utf-8")
            self.process.stdin.write(msg_bytes)
            await self.process.stdin.drain()

    def stop(self):
        """Stop the Pi RPC process."""
        self.is_running = False
        if self.process:
            try:
                self.process.terminate()
            except Exception:
                pass
