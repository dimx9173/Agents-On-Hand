"""
Unit tests for Multi-Protocol Driver Architecture and Probing Chain.
"""

import unittest
import asyncio
from pathlib import Path
from agents_on_hand.drivers import (
    DriverEvent,
    ACPDriver,
    PiRPCDriver,
    ClaudeStreamDriver,
    PTYDriver,
)
from agents_on_hand.session_manager import AgentSession, session_manager


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


if __name__ == "__main__":
    unittest.main()
