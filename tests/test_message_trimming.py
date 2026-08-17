import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from copilot.agent import trim_messages_window


class TrimMessagesWindowTests(unittest.TestCase):
    def test_returns_all_messages_when_within_limit(self) -> None:
        messages = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "yo"}]
        self.assertEqual(trim_messages_window(messages, 5), messages)

    def test_drops_leading_orphaned_tool_messages(self) -> None:
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "user", "content": "next"},
        ]
        window = trim_messages_window(messages, 2)
        self.assertEqual(window, [{"role": "user", "content": "next"}])

    def test_drops_assistant_with_tool_calls_when_results_trimmed(self) -> None:
        messages = [
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c2"}]},
            {"role": "user", "content": "next"},
        ]
        # window size 2 -> naive slice starts at the second assistant call
        window = trim_messages_window(messages, 2)
        self.assertEqual(window, [{"role": "user", "content": "next"}])

    def test_keeps_complete_tool_group(self) -> None:
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]},
            {"role": "tool", "tool_call_id": "c1", "content": "r1"},
            {"role": "assistant", "content": "done"},
        ]
        window = trim_messages_window(messages, 3)
        self.assertEqual(window[0], {"role": "assistant", "content": "", "tool_calls": [{"id": "c1"}]})
        self.assertEqual(window[1]["role"], "tool")


if __name__ == "__main__":
    unittest.main()
