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

    def test_start_background_process_returns_running_state(self) -> None:
        result = main.start_background_process(
            [sys.executable, "-c", "import time; time.sleep(0.2)"],
            cwd=".",
            timeout_seconds=3,
            output_log_path=str(Path("logs") / "test_background.log"),
        )

        self.assertEqual(result.get("status"), "ok")
        self.assertEqual(result.get("state"), "running")
        self.assertIsNotNone(result.get("pid"))
        self.assertIn("log_path", result)


if __name__ == "__main__":
    unittest.main()
