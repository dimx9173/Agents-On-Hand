import unittest
import asyncio
from agents_on_hand.acp_client import ACPClient
from agents_on_hand.acp_session import ACPSession


class TestACPEngine(unittest.TestCase):
    def test_json_rpc_handler(self):
        client = ACPClient(command="echo", working_dir=".")
        updates = []
        client.register_listener(lambda p: updates.append(p))

        # Simulate incoming agent/update notification
        client._handle_json_msg({
            "jsonrpc": "2.0",
            "method": "agent/update",
            "params": {"content": "Hello from ACP"}
        })

        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["content"], "Hello from ACP")

    def test_permission_request_handler(self):
        client = ACPClient(command="echo", working_dir=".")
        perms = []
        client.register_permission_listener(lambda req_id, params: perms.append((req_id, params)))

        # Simulate incoming permission request from agent
        client._handle_json_msg({
            "jsonrpc": "2.0",
            "id": 101,
            "method": "agent/request_permission",
            "params": {"name": "bash", "args": "rm -rf /tmp/test"}
        })

        self.assertEqual(len(perms), 1)
        self.assertEqual(perms[0][0], 101)
        self.assertEqual(perms[0][1]["name"], "bash")


if __name__ == "__main__":
    unittest.main()
