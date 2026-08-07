import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class DummyFunction:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = arguments


class DummyToolCall:
    def __init__(self, name: str, arguments: dict) -> None:
        self.function = DummyFunction(name, arguments)


class FileToolTests(unittest.TestCase):
    def test_delete_file_tool_removes_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "sample.txt"
            file_path.write_text("hello", encoding="utf-8")

            result = main.execute_tool_call(DummyToolCall("delete_file", {"path": str(file_path)}))

            self.assertEqual(result["status"], "ok")
            self.assertFalse(file_path.exists())

    def test_list_dir_can_walk_subdirectories_recursively(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            nested = root / "src" / "pkg"
            nested.mkdir(parents=True)
            (nested / "module.py").write_text("print('hi')", encoding="utf-8")

            result = main.execute_tool_call(DummyToolCall("list_dir", {"path": str(root), "recursive": True}))

            self.assertEqual(result["status"], "ok")
            entries = json.loads(result["content"])
            self.assertIn(str(nested / "module.py"), entries)

    def test_create_directory_tool_creates_nested_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "a" / "b" / "c"
            result = main.execute_tool_call(DummyToolCall("create_directory", {"path": str(target)}))

            self.assertEqual(result["status"], "ok")
            self.assertTrue(target.exists())

    def test_delete_directory_tool_removes_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "to-remove"
            (target / "nested").mkdir(parents=True)
            (target / "nested" / "file.txt").write_text("x", encoding="utf-8")

            result = main.execute_tool_call(DummyToolCall("delete_directory", {"path": str(target)}))

            self.assertEqual(result["status"], "ok")
            self.assertFalse(target.exists())

    def test_rename_path_tool_moves_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            old_path = Path(tmpdir) / "old.txt"
            new_path = Path(tmpdir) / "new.txt"
            old_path.write_text("hello", encoding="utf-8")

            result = main.execute_tool_call(DummyToolCall("rename_path", {"old_path": str(old_path), "new_path": str(new_path)}))

            self.assertEqual(result["status"], "ok")
            self.assertTrue(new_path.exists())
            self.assertFalse(old_path.exists())

    def test_copy_file_tool_copies_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            src = Path(tmpdir) / "src.txt"
            dest = Path(tmpdir) / "dest.txt"
            src.write_text("hello", encoding="utf-8")

            result = main.execute_tool_call(DummyToolCall("copy_file", {"source_path": str(src), "destination_path": str(dest)}))

            self.assertEqual(result["status"], "ok")
            self.assertTrue(dest.exists())
            self.assertEqual(dest.read_text(encoding="utf-8"), "hello")

    def test_search_code_tool_finds_pattern_in_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            target = root / "demo.py"
            target.write_text("def greet():\n    return 'hello'\n", encoding="utf-8")

            result = main.execute_tool_call(DummyToolCall("search_code", {"path": str(root), "pattern": "return 'hello'"}))

            self.assertEqual(result["status"], "ok")
            matches = json.loads(result["content"])
            self.assertTrue(matches)
            self.assertEqual(matches[0]["path"], str(target))

    def test_edit_file_tool_replaces_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            target = Path(tmpdir) / "demo.py"
            target.write_text("print('before')\n", encoding="utf-8")

            result = main.execute_tool_call(
                DummyToolCall(
                    "edit_file",
                    {"path": str(target), "old_string": "before", "new_string": "after"},
                )
            )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(target.read_text(encoding="utf-8"), "print('after')\n")


if __name__ == "__main__":
    unittest.main()
