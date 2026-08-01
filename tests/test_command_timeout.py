import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class CommandTimeoutTests(unittest.TestCase):
    def test_run_subprocess_command_times_out(self) -> None:
        returncode, stdout, stderr = main.run_subprocess_command(
            [sys.executable, "-c", "import time; time.sleep(2)"],
            cwd=".",
            timeout=1,
        )

        self.assertEqual(returncode, -1)
        self.assertEqual(stdout, "")
        self.assertIn("timed out", stderr.lower())


if __name__ == "__main__":
    unittest.main()
