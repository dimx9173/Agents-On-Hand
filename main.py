"""Entry point for Agents-On-Hand Telegram Bot."""
import os
import sys

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

# Initialise logging FIRST (before any other AOH imports)
from agents_on_hand.logging_setup import setup_logging  # noqa: E402

setup_logging()

from agents_on_hand.bot import main  # noqa: E402

if __name__ == "__main__":
    main()
