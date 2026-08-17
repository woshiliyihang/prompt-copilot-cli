import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main
from copilot import memory as memory_module


class DummyFunction:
    def __init__(self, name: str, arguments: dict) -> None:
        self.name = name
        self.arguments = arguments


class DummyToolCall:
    def __init__(self, name: str, arguments: dict) -> None:
        self.function = DummyFunction(name, arguments)


class MemoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = memory_module.MemoryStore(Path(self._tmpdir.name) / "memory.db")

    def tearDown(self) -> None:
        self.store.close()
        self._tmpdir.cleanup()

    def test_add_and_retrieve_memory(self) -> None:
        result = self.store.add("用户偏好使用 pytest 运行测试", kind="preference")

        self.assertEqual(result["status"], "ok")
        rows = self.store.list_recent(limit=5)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["content"], "用户偏好使用 pytest 运行测试")
        self.assertEqual(rows[0]["kind"], "preference")

    def test_add_rejects_duplicate_content(self) -> None:
        first = self.store.add("always run pytest before committing")
        second = self.store.add("always run pytest before committing")

        self.assertFalse(first.get("duplicate"))
        self.assertTrue(second.get("duplicate"))
        self.assertEqual(self.store.count(), 1)

    def test_add_rejects_empty_and_oversized_content(self) -> None:
        self.assertEqual(self.store.add("   ")["status"], "error")
        too_long = self.store.add("x" * (memory_module.MAX_MEMORY_CHARS + 1))
        self.assertEqual(too_long["status"], "error")
        self.assertEqual(too_long["code"], "too_long")

    def test_add_rejects_secret_like_content(self) -> None:
        outcome = self.store.add("api_key=sk-abcdefghij1234567890abcdef")

        self.assertEqual(outcome["status"], "error")
        self.assertEqual(outcome["code"], "secret")

    def test_delete_removes_memory(self) -> None:
        added = self.store.add("temporary memory")

        self.assertTrue(self.store.delete(added["id"]))
        self.assertFalse(self.store.delete(added["id"]))
        self.assertEqual(self.store.count(), 0)

    def test_search_matches_chinese_and_english(self) -> None:
        self.store.add("部署环境在阿里云 ECS，区域 cn-hangzhou")
        self.store.add("the build pipeline uses GitHub Actions")

        zh_hits = self.store.search("阿里云", limit=3)
        self.assertEqual(len(zh_hits), 1)
        self.assertIn("阿里云", zh_hits[0]["content"])

        en_hits = self.store.search("pipeline", limit=3)
        self.assertEqual(len(en_hits), 1)
        self.assertIn("GitHub Actions", en_hits[0]["content"])

    def test_search_returns_empty_for_unknown_query(self) -> None:
        self.store.add("known memory entry")
        self.assertEqual(self.store.search("zzz-does-not-exist", limit=3), [])

    def test_search_respects_limit(self) -> None:
        for index in range(5):
            self.store.add(f"shared keyword entry {index}")

        self.assertEqual(len(self.store.search("shared", limit=2)), 2)

    def test_store_persists_across_connections(self) -> None:
        added = self.store.add("durable memory entry")
        self.store.close()

        reopened = memory_module.MemoryStore(Path(self._tmpdir.name) / "memory.db")
        try:
            self.assertEqual(reopened.count(), 1)
            hits = reopened.search("durable", limit=1)
            self.assertEqual(hits[0]["id"], added["id"])
        finally:
            reopened.close()

        # Recreate the handle so tearDown can close it safely.
        self.store = memory_module.MemoryStore(Path(self._tmpdir.name) / "memory.db")


class MemoryToolTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self.store = memory_module.MemoryStore(Path(self._tmpdir.name) / "memory.db")
        self._original_store = memory_module.default_store
        memory_module.default_store = lambda: self.store

    def tearDown(self) -> None:
        memory_module.default_store = self._original_store
        self.store.close()
        self._tmpdir.cleanup()

    def test_memory_add_tool_writes_entry(self) -> None:
        result = main.execute_tool_call(
            DummyToolCall("memory_add", {"content": "记住：测试用 pytest", "kind": "preference"})
        )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.store.count(), 1)

    def test_memory_search_tool_returns_matches(self) -> None:
        self.store.add("项目使用 SQLite 存储长期记忆")

        result = main.execute_tool_call(DummyToolCall("memory_search", {"query": "SQLite"}))

        self.assertEqual(result["status"], "ok")
        matches = json.loads(result["content"])
        self.assertEqual(len(matches), 1)
        self.assertIn("SQLite", matches[0]["content"])

    def test_memory_delete_tool_removes_entry(self) -> None:
        added = self.store.add("to be removed")

        result = main.execute_tool_call(DummyToolCall("memory_delete", {"id": added["id"]}))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(self.store.count(), 0)

    def test_memory_add_tool_rejects_secrets(self) -> None:
        result = main.execute_tool_call(
            DummyToolCall("memory_add", {"content": "password: hunter2secret"})
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.store.count(), 0)


class MemoryParsingTests(unittest.TestCase):
    def test_parse_extracted_memories_from_json_array(self) -> None:
        parsed = memory_module.parse_extracted_memories('["记住用 pytest", "不要提交 .env"]')
        self.assertEqual(parsed, ["记住用 pytest", "不要提交 .env"])

    def test_parse_extracted_memories_strips_code_fence(self) -> None:
        parsed = memory_module.parse_extracted_memories('```json\n["entry one"]\n```')
        self.assertEqual(parsed, ["entry one"])

    def test_parse_extracted_memories_returns_empty_for_garbage(self) -> None:
        self.assertEqual(memory_module.parse_extracted_memories("no json here"), [])
        self.assertEqual(memory_module.parse_extracted_memories(""), [])


if __name__ == "__main__":
    unittest.main()
