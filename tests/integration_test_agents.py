"""
Integration Test: Sequential 2-turn validation ("hi" and "當前專案路徑") across all CLI agents:
- Pi Agent (pi_rpc)
- OpenCode (acp)
- OMP (acp)
- Claude Code (claude_stream)

Includes full end-to-end payload verification right before sending to Telegram (TG Pre-Send Message Payload)!
"""

import asyncio
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agents_on_hand.drivers import (
    DriverEvent,
    PiRPCDriver,
    ACPDriver,
    ClaudeStreamDriver,
)
from agents_on_hand.stream_handler import UnifiedStreamer

WORKING_DIR = Path("/Users/carlos/pywork/Agents-On-Hand")
TIMEOUT = 60.0  # Max seconds to wait for each turn's response


class MockTelegramBot:
    """Mock Telegram Bot to intercept and record messages prepared right before sending."""

    def __init__(self):
        self.sent_messages = []
        self.next_msg_id = 1000

    async def send_chat_action(self, chat_id, action):
        pass

    async def send_message(self, chat_id, text, parse_mode=None, reply_markup=None):
        self.next_msg_id += 1
        record = {"action": "send", "text": text, "msg_id": self.next_msg_id, "parse_mode": parse_mode}
        self.sent_messages.append(record)
        msg = type("Message", (), {"message_id": self.next_msg_id})()
        return msg

    async def edit_message_text(self, chat_id, message_id, text, parse_mode=None):
        record = {"action": "edit", "text": text, "msg_id": message_id, "parse_mode": parse_mode}
        self.sent_messages.append(record)
        return message_id


class MockAgentSession:
    """Bridge driver event listeners with UnifiedStreamer."""

    def __init__(self, driver):
        self.driver = driver

    def register_listener(self, listener):
        self.driver.register_listener(listener)

    def unregister_listener(self, listener):
        self.driver.unregister_listener(listener)

    async def respond_permission(self, request_id, approved: bool):
        """Proxy permission response to underlying driver."""
        if hasattr(self.driver, "respond_permission"):
            await self.driver.respond_permission(request_id, approved)


async def test_agent(name: str, driver):
    print(f"\n{'='*60}")
    print(f"🧪 Sequential Integration Test: {name}")
    print(f"{'='*60}")

    text_chunks = []
    thought_chunks = []
    tool_requests = []
    exit_received = asyncio.Event()
    first_token_time = None

    def on_event(event: DriverEvent):
        nonlocal first_token_time
        if event.event_type in (DriverEvent.TEXT_DELTA, DriverEvent.THOUGHT_DELTA) and event.content:
            if first_token_time is None:
                first_token_time = asyncio.get_event_loop().time()

        if event.event_type == DriverEvent.TEXT_DELTA and event.content:
            text_chunks.append(event.content)
            sys.stdout.write(event.content)
            sys.stdout.flush()

        elif event.event_type == DriverEvent.THOUGHT_DELTA and event.content:
            thought_chunks.append(event.content)

        elif event.event_type == DriverEvent.TOOL_REQUEST:
            print(f"\n  🛡️ TOOL REQUEST: {event.tool_name} | args={event.tool_args}")
            tool_requests.append(event.tool_name)
            # Auto-approve tool requests so agents don't hang waiting for permission
            print(f"  ✅ AUTO-APPROVING tool request (req_id={event.request_id})")
            asyncio.create_task(driver.respond_permission(event.request_id, approved=True))

        elif event.event_type == DriverEvent.EXIT:
            print(f"\n  🔴 EXIT event received")
            exit_received.set()

    # Setup Mock Telegram Bot and UnifiedStreamer
    mock_bot = MockTelegramBot()
    mock_session = MockAgentSession(driver)
    streamer = UnifiedStreamer(bot=mock_bot, chat_id=99999, session=mock_session, edit_interval=0.1)

    # Start driver
    print(f"  → Starting driver...")
    success = await driver.start()
    if not success:
        print(f"  ❌ Driver failed to start!")
        return False

    print(f"  ✅ Driver started successfully")
    driver.register_listener(on_event)
    streamer.start()

    # ----------------------------------------------------
    # TURN 1: Prompt "hi"
    # ----------------------------------------------------
    prompt1 = "hi"
    print(f"  → Turn 1: Sending prompt: '{prompt1}'")
    streamer.notify_user_input()
    first_token_time = None
    start_time1 = asyncio.get_event_loop().time()
    driver.send_prompt(prompt1)

    deadline1 = asyncio.get_event_loop().time() + TIMEOUT
    while asyncio.get_event_loop().time() < deadline1:
        await asyncio.sleep(0.2)
        full_text = "".join(text_chunks)
        if full_text.strip() and exit_received.is_set():
            break
        if full_text.strip():
            snapshot = full_text
            await asyncio.sleep(3.0)
            if "".join(text_chunks) == snapshot:
                break

    # Flush streamer to trigger final edit/send to Mock Telegram Bot
    async with streamer._lock:
        await streamer._flush_edit_locked()

    turn1_text = "".join(text_chunks)
    turn1_thought = "".join(thought_chunks)
    ttft1 = (first_token_time - start_time1) if first_token_time is not None else None

    # Get exact message payload ready for Telegram
    tg_payload1 = mock_bot.sent_messages[-1]["text"] if mock_bot.sent_messages else streamer._render_content(turn1_text, turn1_thought)

    print(f"\n\n  {'─'*50}")
    print(f"  📊 TURN 1 RESULT SUMMARY for {name}:")
    print(f"  Text response: {len(turn1_text)} chars")
    if ttft1 is not None:
        print(f"  ⚡ Turn 1 TTFT: {ttft1:.2f}s")
        if ttft1 > 10.0:
            print(f"  ⚠️  HIGH TTFT WARNING: Agent took {ttft1:.1f}s for first token!")
    if turn1_thought:
        print(f"  Thought: {len(turn1_thought)} chars")

    print(f"  📱 TG Pre-Send Payload (Turn 1):\n    >>> {tg_payload1.strip()[:120]}...")

    if not turn1_text.strip() or not tg_payload1.strip():
        print(f"  ❌ FAIL — Turn 1 received no text response or TG payload was empty!")
        streamer.stop()
        driver.stop()
        return False

    if exit_received.is_set():
        print(f"  ❌ FAIL — EXIT received after Turn 1; session died prematurely!")
        streamer.stop()
        driver.stop()
        return False

    # ----------------------------------------------------
    # TURN 2: Prompt "當前專案路徑"
    # ----------------------------------------------------
    prompt2 = "當前專案路徑"
    print(f"\n  → Turn 2: Sending prompt: '{prompt2}' (Project Path Check)...")
    text_chunks.clear()
    thought_chunks.clear()
    streamer.notify_user_input()
    first_token_time = None
    start_time2 = asyncio.get_event_loop().time()
    driver.send_prompt(prompt2)

    deadline2 = asyncio.get_event_loop().time() + TIMEOUT
    while asyncio.get_event_loop().time() < deadline2:
        await asyncio.sleep(0.2)
        full_text2 = "".join(text_chunks)
        if full_text2.strip() and exit_received.is_set():
            break
        if full_text2.strip():
            snapshot2 = full_text2
            await asyncio.sleep(3.0)
            if "".join(text_chunks) == snapshot2:
                break

    # Flush streamer to trigger final edit/send to Mock Telegram Bot
    async with streamer._lock:
        await streamer._flush_edit_locked()

    turn2_text = "".join(text_chunks)
    turn2_thought = "".join(thought_chunks)
    ttft2 = (first_token_time - start_time2) if first_token_time is not None else None

    # Get exact message payload ready for Telegram
    tg_payload2 = mock_bot.sent_messages[-1]["text"] if mock_bot.sent_messages else streamer._render_content(turn2_text, turn2_thought)

    print(f"\n\n  {'─'*50}")
    print(f"  📊 TURN 2 RESULT SUMMARY for {name}:")
    print(f"  Text response: {len(turn2_text)} chars")
    if ttft2 is not None:
        print(f"  ⚡ Turn 2 TTFT: {ttft2:.2f}s")
        if ttft2 > 10.0:
            print(f"  ⚠️  HIGH TTFT WARNING: Turn 2 took {ttft2:.1f}s for first token!")

    print(f"  📱 TG Pre-Send Payload (Turn 2):\n    >>> {tg_payload2.strip()[:150]}...")

    if not turn2_text.strip() or not tg_payload2.strip():
        print(f"  ❌ FAIL — Turn 2 received no response or TG payload was empty!")
        streamer.stop()
        driver.stop()
        return False

    # Strict Validation: Verify TG Pre-Send payload contains working directory keyword
    expected_kw = "agents-on-hand"
    if expected_kw in tg_payload2.lower():
        print(f"  ✅ PASS — TG Pre-Send Payload & Project Path Validation OK (Found '{expected_kw}')!")
        result = True
    else:
        print(f"  ❌ FAIL — TG Pre-Send Payload Validation Failed! Payload does not contain '{expected_kw}'")
        print(f"     Actual TG Payload: '{tg_payload2.strip()}'")
        result = False

    streamer.stop()
    driver.stop()
    return result


async def main():
    print("\n🚀 AOH Multi-Protocol Sequential Integration Test")
    print("   Testing 4 Agents (Pi Agent, OpenCode, OMP, Claude Code) sequentially for 2-turn dialogue")
    print("   Validating Telegram Pre-Send Message Payload & Strict Project Path Matching\n")

    agent_configs = [
        ("Pi Agent (pi --mode rpc)", lambda: PiRPCDriver("pi", WORKING_DIR)),
        ("OpenCode (opencode acp)", lambda: ACPDriver("opencode acp", WORKING_DIR)),
        ("OMP (omp acp)", lambda: ACPDriver("omp acp", WORKING_DIR)),
        ("Claude Code (claude stream-json)", lambda: ClaudeStreamDriver("claude", WORKING_DIR)),
    ]

    result_map = {}

    for name, driver_factory in agent_configs:
        driver = driver_factory()
        try:
            passed = await test_agent(name, driver)
            result_map[name] = passed
        except Exception as e:
            print(f"  ❌ {name}: Exception — {e}")
            result_map[name] = False

        # Brief cooldown between agents
        await asyncio.sleep(2.0)

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
        print("🎉 ALL 4 AGENT INTEGRATION TESTS (INCLUDING TG PAYLOAD VERIFICATION) PASSED!")
    else:
        print("⚠️  SOME AGENT INTEGRATION TESTS FAILED")
    print(f"{'='*60}\n")

    return 0 if all_pass else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
