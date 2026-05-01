import tempfile
import unittest
from pathlib import Path

from codex_nomad_surface import settings
from codex_nomad_surface.settings import AppSettings, load_settings, save_settings


class SettingsTests(unittest.TestCase):
    def test_new_chat_runtime_defaults_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            original_app_dir = settings.APP_DIR
            original_settings_path = settings.SETTINGS_PATH
            try:
                settings.APP_DIR = Path(temp_dir)
                settings.SETTINGS_PATH = settings.APP_DIR / "settings.json"
                save_settings(
                    AppSettings(
                        app_server_url="ws://127.0.0.1:9999",
                        new_chat_model_provider="openai",
                        new_chat_model="gpt-test",
                        new_chat_reasoning_effort="high",
                    )
                )

                loaded = load_settings()

                self.assertEqual(loaded.app_server_url, "ws://127.0.0.1:9999")
                self.assertEqual(loaded.new_chat_model_provider, "openai")
                self.assertEqual(loaded.new_chat_model, "gpt-test")
                self.assertEqual(loaded.new_chat_reasoning_effort, "high")
            finally:
                settings.APP_DIR = original_app_dir
                settings.SETTINGS_PATH = original_settings_path


if __name__ == "__main__":
    unittest.main()
