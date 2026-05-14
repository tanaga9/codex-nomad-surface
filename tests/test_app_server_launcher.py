import os
import subprocess
import unittest
from unittest.mock import patch

from codex_nomad_surface.app import (
    app_server_bin,
    app_server_launch_command,
    app_server_launch_environment,
    format_app_server_launch_command,
    start_local_app_server,
)
from codex_nomad_surface.settings import AppSettings


class AppServerLauncherTests(unittest.TestCase):
    def test_app_server_bin_defaults_to_codex(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(app_server_bin(), "codex")

    def test_app_server_bin_uses_configured_value(self) -> None:
        with patch.dict(
            os.environ,
            {"CODEX_APP_SERVER_BIN": "  custom-codex  "},
            clear=True,
        ):
            self.assertEqual(app_server_bin(), "custom-codex")

    def test_blank_app_server_bin_defaults_to_codex(self) -> None:
        with patch.dict(
            os.environ,
            {"CODEX_APP_SERVER_BIN": "   "},
            clear=True,
        ):
            self.assertEqual(app_server_bin(), "codex")

    def test_launch_command_uses_configured_binary_and_listen_url(self) -> None:
        settings = AppSettings(app_server_url="ws://127.0.0.1:9999")
        with patch.dict(
            os.environ,
            {"CODEX_APP_SERVER_BIN": "/path/to/codex"},
            clear=True,
        ):
            self.assertEqual(
                app_server_launch_command(settings),
                ["/path/to/codex", "app-server", "--listen", "ws://127.0.0.1:9999"],
            )

    def test_launch_command_display_matches_launch_command(self) -> None:
        settings = AppSettings(app_server_url="ws://127.0.0.1:9999")
        with patch.dict(
            os.environ,
            {"CODEX_APP_SERVER_BIN": "/path with spaces/codex"},
            clear=True,
        ):
            self.assertEqual(
                format_app_server_launch_command(settings),
                "'/path with spaces/codex' app-server --listen ws://127.0.0.1:9999",
            )

    def test_start_local_app_server_uses_launch_command_directly(self) -> None:
        settings = AppSettings(app_server_url="ws://127.0.0.1:9999")
        process = object()
        with (
            patch("codex_nomad_surface.app.subprocess.Popen", return_value=process) as popen,
            patch("codex_nomad_surface.app.terminate_process_at_exit") as terminate,
        ):
            ok, message, started = start_local_app_server(settings)

        self.assertTrue(ok)
        self.assertEqual(message, "Starting Codex App Server...")
        self.assertIs(started, process)
        popen.assert_called_once_with(
            ["codex", "app-server", "--listen", "ws://127.0.0.1:9999"],
            stdin=subprocess.DEVNULL,
            env=None,
        )
        terminate.assert_called_once_with(process)

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
