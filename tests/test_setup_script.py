import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "tinytable_setup.py"


class SetupScriptTests(unittest.TestCase):
    def run_setup(self, work: Path, *args: str) -> subprocess.CompletedProcess:
        env = {"TINYTABLE_SETUP_WORK": str(work), "TINYTABLE_SETUP_SECRETS": str(work / "secrets"), "PATH": "/usr/bin:/bin"}
        return subprocess.run([sys.executable, str(SCRIPT), *args], env=env, capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=60)

    def test_every_command_is_listed(self):
        result = self.run_setup(Path("."), "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        for command in ("init", "names", "matrix", "kindroid-har", "secrets", "check"):
            self.assertIn(command, result.stdout)

    def test_names_writes_seat_settings_without_a_terminal(self):
        with TemporaryDirectory() as directory:
            work = Path(directory)
            (work / "compose.yaml").write_text("")
            result = self.run_setup(work, "names", "--operator-name", "Sam", "--replika-name", "Juniper", "--kindroid-name", "Wren")
            self.assertEqual(result.returncode, 0, result.stderr)
            written = (work / "local" / "seats.env").read_text()
            self.assertIn("TINYTABLE_REPLIKA_COMPANION_NAME=Juniper", written)
            self.assertIn("TINYTABLE_KINDROID_COMPANION_NAME=Wren", written)
            self.assertIn("TINYTABLE_OPERATOR_PRONOUNS=they/them", written)


if __name__ == "__main__":
    unittest.main()
