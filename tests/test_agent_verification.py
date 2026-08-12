import unittest
from agents_on_hand.config import get_installed_cli_agents, AVAILABLE_CLI_AGENTS


class TestAgentVerification(unittest.TestCase):
    def test_get_installed_cli_agents(self):
        installed = get_installed_cli_agents()
        self.assertIsInstance(installed, dict)
        # bash is standard on unix systems and should be detected
        self.assertIn("bash", installed)
        self.assertEqual(installed["bash"]["command"], "bash")

        # omp is installed in user environment
        if "omp" in installed:
            self.assertEqual(installed["omp"]["use_acp"], True)


if __name__ == "__main__":
    unittest.main()
