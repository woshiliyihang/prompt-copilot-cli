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

    def test_plan_prompt_includes_command_classification_guidance(self) -> None:
        prompt = main.plan_user_request.__code__.co_consts
        joined = " ".join(str(item) for item in prompt if isinstance(item, str))
        self.assertIn("短时命令", joined)
        self.assertIn("持久命令", joined)

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
