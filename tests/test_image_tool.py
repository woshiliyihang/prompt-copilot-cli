import sys
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


class ImageToolTests(unittest.TestCase):
    def test_read_image_as_base64_tool(self) -> None:
        image_path = Path("tests") / "fixtures" / "sample.png"
        result = main.execute_tool_call(DummyToolCall("read_image_as_base64", {"path": str(image_path)}))

        self.assertEqual(result["status"], "ok")
        self.assertIn("loaded into context", result["content"].lower())
        self.assertNotIn("base64", result["content"].lower())


if __name__ == "__main__":
    unittest.main()
