"""Entry point for Agents-On-Hand Telegram Bot."""
import sys
import os

# Ensure project root is on the path
sys.path.insert(0, os.path.dirname(__file__))

from agents_on_hand.bot import main

if __name__ == "__main__":
    main()
