import contextlib
import io
import sys
import types
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class CLITests(unittest.TestCase):
    def test_detects_background_service_commands(self) -> None:
        self.assertTrue(main.looks_like_background_service_command("npm run dev"))
        self.assertTrue(main.looks_like_background_service_command("python app.py"))
        self.assertTrue(main.looks_like_background_service_command("uvicorn main:app --reload"))
        self.assertFalse(main.looks_like_background_service_command("python -m pytest"))

    def test_execute_command_uses_background_execution_by_default(self) -> None:
        captured: dict[str, object] = {}

        def fake_start_background_process(command, cwd, timeout_seconds=None, output_log_path=None):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["timeout_seconds"] = timeout_seconds
            captured["output_log_path"] = output_log_path
            return {
                "status": "ok",
                "content": "started",
                "pid": 1234,
                "cwd": cwd,
                "log_path": output_log_path,
            }

        original = main.start_background_process
        main.start_background_process = fake_start_background_process
        try:
            result = main.execute_tool_call(types.SimpleNamespace(function=types.SimpleNamespace(
                name="execute_command",
                arguments={"command": "python -m pytest", "cwd": "."},
            )))
        finally:
            main.start_background_process = original

        self.assertEqual(result["status"], "ok")
        self.assertEqual(captured["command"], "python -m pytest")
        self.assertEqual(captured["cwd"], str(Path.cwd()))
        self.assertIsNotNone(captured["output_log_path"])
        self.assertEqual(result["log_path"], captured["output_log_path"])

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
