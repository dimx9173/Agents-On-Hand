import unittest
from pathlib import Path

from agents_on_hand.acp_client import ACPClient
from agents_on_hand.drivers.acp_driver import ACPDriver, extract_acp_text_delta
from agents_on_hand.drivers.base_driver import DriverEvent


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

    def test_extract_acp_text_delta_nested_and_hook_filter(self):
        # Nested list of content blocks
        params = {
            "update": {
                "sessionUpdate": "agentMessageChunk",
                "content": [
                    {"type": "content", "content": {"type": "text", "text": "File list: "}},
                    {"type": "content", "content": {"type": "text", "text": "main.py"}},
                ]
            }
        }
        self.assertEqual(extract_acp_text_delta(params), "File list: main.py")

        # Raw PostToolUse internal JSON hook line should be filtered out
        hook_json = '{"session_id":"ses_123","transcript_path":"/tmp/a.jsonl","hook_event_name":"PostToolUse"}'
        hook_params = {"update": {"sessionUpdate": "agentMessageChunk", "content": hook_json}}
        self.assertEqual(extract_acp_text_delta(hook_params), "")

    def test_acp_driver_event_routing(self):
        events = []
        driver = ACPDriver(command="echo", working_dir=Path("."))
        driver.register_listener(events.append)

        # Tool result event
        driver._on_acp_update({
            "update": {
                "sessionUpdate": "toolResult",
                "tool_name": "bash",
                "content": "output from bash command"
            }
        })
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].event_type, DriverEvent.TOOL_RESULT)
        self.assertEqual(events[0].tool_name, "bash")
        self.assertEqual(events[0].content, "output from bash command")

        # Text delta event
        driver._on_acp_update({
            "update": {
                "sessionUpdate": "agentMessageChunk",
                "content": "Here is the summary."
            }
        })
        self.assertEqual(len(events), 2)
        self.assertEqual(events[1].event_type, DriverEvent.TEXT_DELTA)
        self.assertEqual(events[1].content, "Here is the summary.")


if __name__ == "__main__":
    unittest.main()
