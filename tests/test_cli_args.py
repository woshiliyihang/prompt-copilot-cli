import contextlib
import io
import sys
import tempfile
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

    def test_plan_prompt_includes_command_classification_guidance(self) -> None:
        prompt = main.plan_user_request.__code__.co_consts
        joined = " ".join(str(item) for item in prompt if isinstance(item, str))
        self.assertIn("短时命令", joined)
        self.assertIn("持久命令", joined)

    def test_special_commands_skip_planning(self) -> None:
        called = {"value": False}

        def fake_chat_once(client, model, messages, temperature, debug_enabled=False):
            called["value"] = True
            return types.SimpleNamespace(content="should not be used")

        original_chat_once = main.chat_once
        main.chat_once = fake_chat_once
        try:
            planned = main.plan_user_request(
                client=object(),
                model="gpt-4o-mini",
                history=[],
                user_text="/task-start",
                debug_enabled=False,
            )
        finally:
            main.chat_once = original_chat_once

        self.assertEqual(planned, "/task-start")
        self.assertFalse(called["value"])

    def test_plan_user_request_uses_generated_plan(self) -> None:
        captured: dict[str, object] = {}

        def fake_chat_once(client, model, messages, temperature, debug_enabled=False):
            captured["messages"] = messages
            return types.SimpleNamespace(content="用户原始指令：帮我修复这个 bug\n\n结合上下文得到用户的完整意图：修复当前仓库中的 bug。\n\n接下来按照这个步骤逐步执行完成任务：\n1、第一步：定位问题\n2、第二步：实现修复")

        original_chat_once = main.chat_once
        main.chat_once = fake_chat_once
        try:
            planned = main.plan_user_request(
                client=object(),
                model="gpt-4o-mini",
                history=[{"role": "assistant", "content": "上一条回复"}],
                user_text="帮我修复这个 bug",
                debug_enabled=False,
            )
        finally:
            main.chat_once = original_chat_once

        self.assertEqual(planned, "用户原始指令：帮我修复这个 bug\n\n结合上下文得到用户的完整意图：修复当前仓库中的 bug。\n\n接下来按照这个步骤逐步执行完成任务：\n1、第一步：定位问题\n2、第二步：实现修复")
        self.assertEqual(captured["messages"][0]["role"], "system")
        self.assertEqual(captured["messages"][-1]["content"], "帮我修复这个 bug")

    def test_task_end_command_uses_aligned_working_principles(self) -> None:
        captured: dict[str, object] = {}

        def fake_chat_once(client, model, messages, temperature, debug_enabled=False):
            captured["messages"] = messages
            return types.SimpleNamespace(content="final prompt")

        original_chat_once = main.chat_once
        original_lang = main.UI_SYSTEM_LANGUAGE
        main.UI_SYSTEM_LANGUAGE = "zh"
        main.chat_once = fake_chat_once
        try:
            with tempfile.TemporaryDirectory() as temp_dir:
                md_path = Path(temp_dir) / "recent_conversations.md"
                md_path.write_text(
                    "# Recent conversations\n\n## Round 1\n\n**User:** /task-start\n\nhello\n\n**Assistant:** hi",
                    encoding="utf-8",
                )
                main.handle_task_end_command(
                    md_path=md_path,
                    client=object(),
                    model="gpt-4o-mini",
                    system_prompt="ignored",
                    workdir=Path(temp_dir),
                    debug_enabled=False,
                )
        finally:
            main.UI_SYSTEM_LANGUAGE = original_lang
            main.chat_once = original_chat_once

        self.assertIn("messages", captured)
        system_prompt = captured["messages"][0]["content"]
        self.assertIn("先检查工作目录并理解需求", system_prompt)
        self.assertIn("生成最终提示词时，请严格遵循以下约束", system_prompt)
        self.assertIn("\n\n", system_prompt)

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
