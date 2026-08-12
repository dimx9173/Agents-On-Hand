import unittest
from unittest.mock import MagicMock
from agents_on_hand.session_manager import SessionManager, CLISession
from agents_on_hand.acp_session import ACPSession


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


if __name__ == "__main__":
    unittest.main()
