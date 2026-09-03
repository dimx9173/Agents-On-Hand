"""
Unit tests for Multi-Protocol Driver Architecture and Probing Chain.
"""

import asyncio
import unittest
from pathlib import Path

from agents_on_hand.drivers import (
    DriverEvent,
    PiRPCDriver,
)
from agents_on_hand.session_manager import AgentSession


class TestDrivers(unittest.TestCase):
    def test_driver_event_serialization(self):
        evt = DriverEvent(
            event_type=DriverEvent.TOOL_REQUEST,
            content="",
            request_id=42,
            tool_name="read_file",
            tool_args={"path": "auth.py"},
        )
        d = evt.to_dict()
        self.assertEqual(d["type"], "tool_request")
        self.assertEqual(d["request_id"], 42)
        self.assertEqual(d["tool_name"], "read_file")
        self.assertEqual(d["tool_args"]["path"], "auth.py")

    def test_agent_session_probing_chain(self):
        async def run_test():
            session = AgentSession(
                session_id="sess_test123",
                user_id=123,
                agent_key="bash",
                agent_name="Bash Shell",
                command="bash",
                working_dir=Path.cwd(),
            )
            # Probing chain with PTY fallback
            success = await session.start(["acp", "pty"])
            self.assertTrue(success)
            self.assertEqual(session.active_driver_name, "pty")
            self.assertFalse(session.is_acp)
            session.stop()

        asyncio.run(run_test())


class TestPiRPCDriverProtocol(unittest.TestCase):
    """Protocol-level tests for PiRPCDriver._handle_json_msg against real pi RPC events."""

    def _make_driver(self):
        events = []
        driver = PiRPCDriver("pi", Path.cwd())
        driver.register_listener(events.append)
        return driver, events

    def test_agent_end_does_not_exit_session(self):
        """agent_end is a per-turn signal; the process stays alive for the next prompt."""
        driver, events = self._make_driver()
        driver.is_running = True
        driver._handle_json_msg({"type": "agent_end", "messages": [], "willRetry": False})
        driver._handle_json_msg({"type": "agent_settled"})
        self.assertTrue(driver.is_running)
        self.assertFalse(any(e.event_type == DriverEvent.EXIT for e in events))

    def test_setstatus_is_not_a_tool_request(self):
        """setStatus is a fire-and-forget UI update, not an approval dialog."""
        driver, events = self._make_driver()
        driver._handle_json_msg(
            {
                "type": "extension_ui_request",
                "id": "some-uuid",
                "method": "setStatus",
                "statusText": "🔌 MCP: 3 servers enabled",
            }
        )
        self.assertFalse(any(e.event_type == DriverEvent.TOOL_REQUEST for e in events))

    def test_interactive_ui_request_becomes_tool_request(self):
        """Interactive methods (confirm/select/input/editor) still raise approvals."""
        driver, events = self._make_driver()
        driver._handle_json_msg(
            {
                "type": "extension_ui_request",
                "id": "req-1",
                "method": "confirm",
                "statusText": "Allow bash execution?",
            }
        )
        reqs = [e for e in events if e.event_type == DriverEvent.TOOL_REQUEST]
        self.assertEqual(len(reqs), 1)
        self.assertEqual(reqs[0].request_id, "req-1")
        self.assertEqual(reqs[0].tool_name, "confirm")

    def test_text_and_thinking_deltas_emitted(self):
        driver, events = self._make_driver()
        driver._handle_json_msg(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "text_delta", "delta": "Hello"},
            }
        )
        driver._handle_json_msg(
            {
                "type": "message_update",
                "assistantMessageEvent": {"type": "thinking_delta", "delta": "hmm"},
            }
        )
        types = [e.event_type for e in events]
        self.assertIn(DriverEvent.TEXT_DELTA, types)
        self.assertIn(DriverEvent.THOUGHT_DELTA, types)


if __name__ == "__main__":
    unittest.main()
