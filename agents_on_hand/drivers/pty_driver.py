"""
PTY Fallback Driver for Agents-On-Hand (Unix Pseudo-Terminal via pexpect).
"""

import asyncio
import logging
from pathlib import Path
from typing import Any

import pexpect

from ..ansi_cleaner import IncrementalAnsiCleaner, clean_cli_output
from ..process_utils import kill_process_tree, set_pdeathsig_and_pgrp
from .base_driver import BaseDriver, DriverEvent

logger = logging.getLogger(__name__)


class PTYDriver(BaseDriver):
    """Fallback Protocol Driver using Unix PTY (pexpect)."""

    def __init__(self, command: str, working_dir: Path):
        super().__init__(command, working_dir)
        self.process: pexpect.spawn | None = None
        self._read_task: asyncio.Task | None = None
        self._ansi_cleaner = IncrementalAnsiCleaner()

    async def start(self) -> bool:
        """Spawn the process in a pseudo-terminal."""
        try:
            self.working_dir.mkdir(parents=True, exist_ok=True)
            self.process = pexpect.spawn(
                self.command,
                cwd=str(self.working_dir),
                encoding="utf-8",
                echo=False,
                dimensions=(30, 120),
                preexec_fn=set_pdeathsig_and_pgrp,
            )
            self.is_running = True
            loop = asyncio.get_running_loop()
            self._read_task = loop.create_task(self._read_loop())
            logger.info(f"PTYDriver started successfully with command='{self.command}'")
            return True
        except Exception as e:
            logger.error(f"PTYDriver failed to start: {e}")
            self.is_running = False
            return False

    async def _read_loop(self):
        """Read output from PTY and emit TEXT_DELTA events."""
        loop = asyncio.get_running_loop()
        while self.is_running and self.process and self.process.isalive():
            try:
                chunk = await loop.run_in_executor(None, lambda: self._read_nonblocking())
                if chunk:
                    cleaned_chunk = self._ansi_cleaner.feed(chunk)
                    cleaned = clean_cli_output(cleaned_chunk)
                    if cleaned:
                        self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=cleaned))
                else:
                    await asyncio.sleep(0.1)
            except pexpect.EOF:
                break
            except Exception as e:
                logger.error(f"Error in PTYDriver read loop: {e}")
                await asyncio.sleep(0.1)

        remaining_buf = self._ansi_cleaner.flush()
        if remaining_buf:
            cleaned = clean_cli_output(remaining_buf)
            if cleaned:
                self.emit_event(DriverEvent(DriverEvent.TEXT_DELTA, content=cleaned))

        self.is_running = False
        self.emit_event(DriverEvent(DriverEvent.EXIT, exit_code=0))

    def _read_nonblocking(self) -> str:
        """Read non-blocking chunk from pexpect process."""
        if not self.process:
            return ""
        try:
            return self.process.read_nonblocking(size=4096, timeout=0.1)
        except (pexpect.TIMEOUT, pexpect.EOF):
            return ""

    def send_prompt(self, text: str):
        """Send text to PTY stdin."""
        if self.process and self.is_running:
            self.process.sendline(text)

    def send_control_char(self, char: str):
        """Send control character (e.g. ESC or Ctrl+C) to PTY."""
        if self.process and self.is_running:
            self.process.send(char)

    async def respond_permission(self, request_id: Any, approved: bool):
        """Respond to permission in PTY by sending y or n."""
        if self.process and self.is_running:
            res = "y\r\n" if approved else "n\r\n"
            self.process.send(res)

    def stop(self):
        """Terminate the PTY process and its process tree."""
        self.is_running = False
        if self._read_task and not self._read_task.done():
            self._read_task.cancel()
        if self.process:
            kill_process_tree(self.process)
            try:
                self.process.terminate(force=True)
            except Exception:
                pass
            try:
                self.process.close(force=True)
            except Exception:
                pass
