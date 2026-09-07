import agents_on_hand.stream_handler as sh


def pytest_runtest_setup(item):
    sh._chat_next_slot.clear()
    sh._chat_flood_until.clear()
    sh._CHAT_MIN_GAP = 0.0


def pytest_runtest_teardown(item):
    sh._CHAT_MIN_GAP = 1.2

