import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot.session import SessionStore


class SessionStoreTests(unittest.TestCase):
    def test_load_ignores_malformed_entries_and_caps_history(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text(
                json.dumps(
                    [
                        {"role": "user", "content": "one"},
                        {"invalid": True},
                        {"role": "assistant", "content": "two"},
                        "not a message",
                        {"role": "user", "content": "three"},
                    ]
                ),
                encoding="utf-8",
            )
            store = SessionStore(path, max_messages=2)
            self.assertEqual(
                store.load(),
                [
                    {"role": "assistant", "content": "two"},
                    {"role": "user", "content": "three"},
                ],
            )

    def test_load_recovers_from_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            path.write_text("{broken", encoding="utf-8")
            store = SessionStore(path, max_messages=4)
            self.assertEqual(store.load(), [])

    def test_save_is_bounded_and_does_not_mutate_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.json"
            store = SessionStore(path, max_messages=2)
            history = [
                {"role": "user", "content": "one"},
                {"role": "assistant", "content": "two"},
                {"role": "user", "content": "three"},
            ]
            store.save(history)
            self.assertEqual(len(history), 3)
            self.assertEqual(store.load(), history[-2:])


if __name__ == "__main__":
    unittest.main()
