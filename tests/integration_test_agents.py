"""
Integration Test: Send "hi" to Pi Agent (pi_rpc) and OpenCode (acp) via AOH drivers.
"""

import asyncio
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents_on_hand.drivers import DriverEvent, PiRPCDriver, ACPDriver
from pathlib import Path

WORKING_DIR = Path("/Users/carlos/pywork/Agents-On-Hand")
TIMEOUT = 60.0  # Max seconds to wait for response


async def test_agent(name: str, driver, prompt: str = "Hi"):
    print(f"\n{'='*60}")
    print(f"🧪 Integration Test: {name}")
    print(f"{'='*60}")

    text_chunks = []
    thought_chunks = []
    tool_requests = []
    exit_received = asyncio.Event()

    def on_event(event: DriverEvent):
        if event.event_type == DriverEvent.TEXT_DELTA and event.content:
            text_chunks.append(event.content)
            sys.stdout.write(event.content)
            sys.stdout.flush()

        elif event.event_type == DriverEvent.THOUGHT_DELTA and event.content:
            thought_chunks.append(event.content)

        elif event.event_type == DriverEvent.TOOL_REQUEST:
            print(f"\n  🛡️ TOOL REQUEST: {event.tool_name} | args={event.tool_args}")
            tool_requests.append(event.tool_name)

        elif event.event_type == DriverEvent.EXIT:
            print(f"\n  🔴 EXIT event received")
            exit_received.set()

    # Start driver
    print(f"  → Starting driver...")
    success = await driver.start()
    if not success:
        print(f"  ❌ Driver failed to start!")
        return False

    print(f"  ✅ Driver started successfully")
    driver.register_listener(on_event)

    # Send prompt
    print(f"  → Sending prompt: '{prompt}'")
    driver.send_prompt(prompt)

    # Wait for text response or timeout
    deadline = asyncio.get_event_loop().time() + TIMEOUT
    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(0.2)
        # Check if we have a full response (turn ended or enough text received)
        full_text = "".join(text_chunks)
        if full_text.strip() and exit_received.is_set():
            break
        # Or if we have text and haven't received any new chunks for 3s
        if full_text.strip():
            snapshot = full_text
            await asyncio.sleep(3.0)
            if "".join(text_chunks) == snapshot:
                break  # No new text for 3s, assume response complete

    # Summary
    full_text = "".join(text_chunks)
    full_thought = "".join(thought_chunks)

    print(f"\n\n  {'─'*50}")
    print(f"  📊 RESULT SUMMARY for {name}:")
    print(f"  Text response: {len(full_text)} chars")
    if full_thought:
        print(f"  Thought: {len(full_thought)} chars (💭 thinking detected)")
    if tool_requests:
        print(f"  Tool requests: {tool_requests}")

    if full_text.strip():
        print(f"  ✅ PASS — Agent responded successfully!")
        result = True
    else:
        print(f"  ❌ FAIL — No text response received!")
        result = False

    driver.stop()
    return result


async def main():
    print("\n🚀 AOH Multi-Protocol Integration Test")
    print("   Sending 'Hi' to Pi Agent (PiRPC) and OpenCode (ACP) in parallel\n")

    pi_driver = PiRPCDriver("pi", WORKING_DIR)
    acp_driver = ACPDriver("opencode acp", WORKING_DIR)

    results = await asyncio.gather(
        test_agent("Pi Agent (pi --mode rpc)", pi_driver, "Hi"),
        test_agent("OpenCode (opencode acp)", acp_driver, "Hi"),
        return_exceptions=True,
    )

    agent_names = ["Pi Agent (pi --mode rpc)", "OpenCode (opencode acp)"]
    result_map = {}
    for name, result in zip(agent_names, results):
        if isinstance(result, Exception):
            print(f"  ❌ {name}: Exception — {result}")
            result_map[name] = False
        else:
            result_map[name] = result

    print(f"\n{'='*60}")
    print("📋 INTEGRATION TEST FINAL REPORT")
    print(f"{'='*60}")
    all_pass = True
    for name, passed in result_map.items():
        status = "✅ PASS" if passed else "❌ FAIL"
        print(f"  {status}  {name}")
        if not passed:
            all_pass = False

    print(f"\n{'='*60}")
    if all_pass:
        print("🎉 ALL INTEGRATION TESTS PASSED!")
    else:
        print("⚠️  SOME INTEGRATION TESTS FAILED")
    print(f"{'='*60}\n")

    return 0 if all_pass else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
