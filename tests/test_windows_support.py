import os
import sys
import unittest
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.app import channel_cli, cli_room, config
from backend.app.main import PROJECT_ROOT, replika_sender_command
import configured_names  # noqa: F401  (display names an installation would configure)


class ConfigFileTests(unittest.TestCase):
    def test_parse_handles_comments_quotes_and_export(self):
        values = config.parse_config(
            "# comment\n\nA=1\nexport B = two\nC='[\"x\"]'\nD=\"quoted value\"\nnot a pair\nE=a=b\n"
        )
        self.assertEqual(values, {"A": "1", "B": "two", "C": '["x"]', "D": "quoted value", "E": "a=b"})

    def test_file_values_never_override_process_environment(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.env"
            path.write_text("TT_TEST_EXISTING=file\nTT_TEST_NEW=file\n", encoding="utf-8")
            with patch.dict(os.environ, {"TT_TEST_EXISTING": "process"}):
                applied = config.load_config_file(path)
                self.assertEqual(os.environ["TT_TEST_EXISTING"], "process")
                self.assertEqual(os.environ["TT_TEST_NEW"], "file")
            self.assertEqual(applied, {"TT_TEST_NEW": "file"})
        os.environ.pop("TT_TEST_NEW", None)

    def test_missing_file_is_a_no_op(self):
        self.assertEqual(config.load_config_file(Path("/nonexistent/tinytable/config.env")), {})

    def test_windows_locations_use_per_user_profile(self):
        env = {"APPDATA": r"C:\Users\e\AppData\Roaming", "LOCALAPPDATA": r"C:\Users\e\AppData\Local"}
        # PureWindowsPath stands in for WindowsPath, which cannot be built on POSIX.
        with patch.object(config.os, "name", "nt"), patch.object(config, "Path", PureWindowsPath), patch.dict(os.environ, env):
            self.assertEqual(config.config_dir(), PureWindowsPath(r"C:\Users\e\AppData\Roaming\TinyTable"))
            self.assertEqual(config.default_db_path(), PureWindowsPath(r"C:\Users\e\AppData\Local\TinyTable\data\tinytable.db"))

    def test_posix_db_default_is_unchanged(self):
        with patch.object(config.os, "name", "posix"):
            self.assertEqual(config.default_db_path(), Path("data/tinytable.db"))


class ReplikaSenderCommandTests(unittest.TestCase):
    def test_source_checkout_runs_provider_with_current_python(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("TINYTABLE_PYTHON", None)
            os.environ.pop("TINYTABLE_API_URL", None)
            command = replika_sender_command("hi Juniper", Path("boot.md"))
        self.assertEqual(command[:2], [sys.executable, str(PROJECT_ROOT / "replika_provider.py")])
        self.assertEqual(
            command[2:],
            ["--bootstrap-file", "boot.md", "--send-text", "hi Juniper", "--enable-outbound", "--tinytable-api", "http://127.0.0.1:8000/api"],
        )

    def test_frozen_build_uses_bundled_sender_or_reports_missing(self):
        with TemporaryDirectory() as tmp, patch("backend.app.main.frozen", return_value=True):
            exe = Path(tmp) / "tinytable-server"
            with patch.object(sys, "executable", str(exe)):
                self.assertIsNone(replika_sender_command("hi", Path("boot.md")))
                sender = exe.with_name("tinytable-replika.exe" if os.name == "nt" else "tinytable-replika")
                sender.write_text("")
                self.assertEqual(replika_sender_command("hi", Path("boot.md"))[0], str(sender))


class WindowsCliTests(unittest.TestCase):
    def test_operator_token_mode_check_skipped_on_windows(self):
        with TemporaryDirectory() as tmp:
            token = Path(tmp) / "operator-token"
            token.write_text("secret\n")
            token.chmod(0o644)
            with patch.object(channel_cli, "TOKEN_FILE", token), patch.dict(os.environ, {"TINYTABLE_OPERATOR_TOKEN": ""}):
                with self.assertRaises(channel_cli.ChannelError):
                    channel_cli._token()
                with patch.object(channel_cli.os, "name", "nt"):
                    self.assertEqual(channel_cli._token(), "secret")

    def test_hermes_batch_shim_is_refused_on_windows(self):
        agent = {"transport": "hermes_peer", "target": "peer-host", "config": {}}
        with patch.object(cli_room.os, "name", "nt"), patch.object(cli_room.shutil, "which", return_value=r"C:\bin\hermes.CMD"):
            with self.assertRaises(ValueError):
                cli_room._command(agent, "hello & del *")
        with patch.object(cli_room.os, "name", "nt"), patch.object(cli_room.shutil, "which", return_value=r"C:\bin\hermes.exe"):
            self.assertEqual(cli_room._command(agent, "hi"), [r"C:\bin\hermes.exe", "peer", "dm", "peer-host", "hi"])


if __name__ == "__main__":
    unittest.main()
