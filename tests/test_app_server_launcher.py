import os
import unittest
from unittest.mock import patch

from codex_nomad_surface.app import app_server_launch_environment


class AppServerLauncherTests(unittest.TestCase):
    def test_openai_api_key_is_optional_for_launch_environment(self) -> None:
        self.assertIsNone(app_server_launch_environment(""))
        self.assertIsNone(app_server_launch_environment("   "))

    def test_openai_api_key_is_added_to_launch_environment(self) -> None:
        with patch.dict(os.environ, {"EXISTING": "value"}, clear=True):
            env = app_server_launch_environment("  sk-test  ")

        self.assertIsNotNone(env)
        assert env is not None
        self.assertEqual(env["OPENAI_API_KEY"], "sk-test")
        self.assertEqual(env["EXISTING"], "value")


if __name__ == "__main__":
    unittest.main()
