import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot.memory import MemoryStore


class MemoryFtsTests(unittest.TestCase):
    def test_existing_rows_are_indexed_when_fts_is_created(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "memory.db"
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE memories (id INTEGER PRIMARY KEY AUTOINCREMENT, content TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL)"
            )
            conn.execute(
                "INSERT INTO memories(content, kind, created_at) VALUES (?, ?, ?)",
                ("项目使用 Python", "fact", "2026-01-01T00:00:00+00:00"),
            )
            conn.commit()
            conn.close()

            store = MemoryStore(db_path)
            results = store.search("Python", limit=3)
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0]["content"], "项目使用 Python")
            store.close()


if __name__ == "__main__":
    unittest.main()
