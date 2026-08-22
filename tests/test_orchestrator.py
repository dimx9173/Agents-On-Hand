import unittest
from pathlib import Path

from agents_on_hand.ansi_cleaner import (
    clean_cli_output,
    strip_ansi_codes,
)
from agents_on_hand.config import is_path_allowed


class TestAnsiCleaner(unittest.TestCase):
    def test_strip_ansi_codes(self):
        raw = "\x1b[31mHello\x1b[0m World!"
        self.assertEqual(strip_ansi_codes(raw), "Hello World!")

    def test_carriage_return_handling(self):
        raw = "Loading 10%\rLoading 50%\rLoading 100%"
        self.assertEqual(strip_ansi_codes(raw), "Loading 100%")

    def test_clean_cli_output_tui_filtering(self):
        raw_tui = (
            "╭─── omp v17.2.12 ──────────────────╮\n"
            "│ Welcome back!                     │\n"
            "│   Hello, how can I help you?      │\n"
            "╰───────────────────────────────────╯"
        )
        cleaned = clean_cli_output(raw_tui)
        self.assertEqual(cleaned, "Hello, how can I help you?")

    def test_format_hermes_style(self):
        raw = "Executing tool: read auth.py\nHere is the auth file explanation."
        from agents_on_hand.ansi_cleaner import format_hermes_style
        formatted = format_hermes_style(raw)
        self.assertIn("🛠️ *Tool*: `read auth.py`", formatted)
        self.assertIn("Here is the auth file explanation.", formatted)




class TestConfigPermissions(unittest.TestCase):
    def test_is_path_allowed(self):
        current_dir = Path.cwd()
        self.assertTrue(is_path_allowed(current_dir))


if __name__ == "__main__":
    unittest.main()
