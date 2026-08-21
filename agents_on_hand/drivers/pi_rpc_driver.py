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

    # extension_ui_request methods that actually await a user response.
    # Others (setStatus, setWidget, ...) are fire-and-forget UI updates.
    INTERACTIVE_UI_METHODS = frozenset({"confirm", "select", "input", "editor"})

    def __init__(self, command: str, working_dir: Path):
        # Ensure command includes --mode rpc
        cmd = command if "--mode" in command else f"{command} --mode rpc"
        super().__init__(cmd, working_dir)
        self.process: Optional[asyncio.subprocess.Process] = None
        self._read_task: Optional[asyncio.Task] = None
        self._stderr_task: Optional[asyncio.Task] = None

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
            self._stderr_task = asyncio.create_task(self._drain_stderr())
            logger.info(f"PiRPCDriver started successfully with command='{self.command}'")
            return True
        except Exception as e:
            logger.error(f"PiRPCDriver failed to start: {e}")
            self.is_running = False
            return False

    async def _drain_stderr(self):
        """Drain stderr so the pipe buffer can never fill and block the process."""
        while self.is_running and self.process and self.process.stderr:
            line = await self.process.stderr.readline()
            if not line:
                break
            logger.debug(f"pi stderr: {line.decode('utf-8', errors='replace').rstrip()}")

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
        # EXIT only on real stdout EOF / process termination, with the real exit code
        exit_code = self.process.returncode if self.process else None
        if exit_code is None:
            exit_code = 1
        self.emit_event(DriverEvent(DriverEvent.EXIT, exit_code=exit_code))

    def _handle_json_msg(self, data: dict):
        """Parse incoming JSON events from Pi RPC.

        Pi Agent --mode rpc format:
        - {"type": "message_update", "assistantMessageEvent": {"type": "text_delta", "delta": "..."}}
        - {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "..."}}
        - {"type": "extension_ui_request", "id": ..., "method": ..., "statusText": ...}
        - {"type": "turn_end", ...}
        """
        msg_type = data.get("type", "")

        if msg_type == "message_update":
            # Unwrap assistantMessageEvent
            evt = data.get("assistantMessageEvent", {})
            if not isinstance(evt, dict):
                return
            evt_type = evt.get("type", "")
            delta = evt.get("delta", "")

            if evt_type == "text_delta" and delta:
                self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=str(delta)))
            elif evt_type in ("thinking_delta", "thinking") and delta:
                self.emit_event(DriverEvent(DriverEvent.THOUGHT_DELTA, content=str(delta)))

        elif msg_type == "extension_ui_request":
            method = data.get("method", "")
            if method not in self.INTERACTIVE_UI_METHODS:
                # Fire-and-forget UI updates (e.g. setStatus "MCP: 3 servers enabled")
                # must NOT become tool approval dialogs.
                return
            req_id = data.get("id", "")
            status_text = data.get("statusText", "")
            self.emit_event(
                DriverEvent(
                    DriverEvent.TOOL_REQUEST,
                    request_id=req_id,
                    tool_name=method or "Tool Approval",
                    tool_args={"info": status_text},
                )
            )

        elif msg_type in ("turn_end", "agent_end", "agent_settled"):
            self.emit_event(DriverEvent(DriverEvent.TURN_END))



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
