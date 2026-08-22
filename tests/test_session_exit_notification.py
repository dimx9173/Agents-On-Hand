import unittest
from unittest.mock import MagicMock

from agents_on_hand.session_manager import SessionManager


class TestSessionExitNotification(unittest.TestCase):
    def test_session_exit_callback_trigger(self):
        sm = SessionManager()
        exited_sessions = []

        sm.register_on_finished_callback(lambda sess: exited_sessions.append(sess))

        # Create dummy session and simulate exit
        session = MagicMock()
        session.session_id = "sess_test_123"

        sm._handle_session_exit(session)
        self.assertEqual(len(exited_sessions), 1)
        self.assertEqual(exited_sessions[0].session_id, "sess_test_123")

    def test_background_turn_completion_callback_trigger(self):
        from pathlib import Path

        from agents_on_hand.drivers.base_driver import DriverEvent
        from agents_on_hand.session_manager import AgentSession

        session = AgentSession(
            session_id="sess_bg_test",
            user_id=123,
            agent_key="pi",
            agent_name="Pi Agent",
            command="pi",
            working_dir=Path("/tmp"),
        )

        completed_sessions = []
        session.set_background_completion_callback(lambda s: completed_sessions.append(s))

        # Simulate TURN_END driver event
        session._on_driver_event(DriverEvent(DriverEvent.TURN_END))

        self.assertEqual(len(completed_sessions), 1)
        self.assertEqual(completed_sessions[0].session_id, "sess_bg_test")
        # Ensure callback is one-shot and cleared after firing
        self.assertIsNone(session._bg_completion_callback)


if __name__ == "__main__":
    unittest.main()
