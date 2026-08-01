import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class CLITests(unittest.TestCase):
    def test_version_flag_prints_application_version_and_exits(self) -> None:
        parser = main.build_cli_parser()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exc:
                parser.parse_args(["--version"])

        self.assertEqual(exc.exception.code, 0)
        self.assertIn(main.APPLICATION_VERSION, output.getvalue())

    def test_help_flag_prints_usage_and_exits(self) -> None:
        parser = main.build_cli_parser()
        output = io.StringIO()

        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as exc:
                parser.parse_args(["--help"])

        self.assertEqual(exc.exception.code, 0)
        self.assertIn("usage:", output.getvalue().lower())


if __name__ == "__main__":
    unittest.main()
