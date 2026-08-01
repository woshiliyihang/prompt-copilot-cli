import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class MultimodalMessageTests(unittest.TestCase):
    def test_build_multimodal_user_message_with_image(self) -> None:
        image_path = Path("tests") / "fixtures" / "sample.png"
        message = main.build_multimodal_user_message("describe this", image_path)

        self.assertEqual(message["role"], "user")
        self.assertEqual(message["content"][0]["type"], "text")
        self.assertEqual(message["content"][1]["type"], "image_url")
        self.assertIn("data:image/", message["content"][1]["image_url"]["url"])

    def test_build_multimodal_user_message_compresses_image(self) -> None:
        from PIL import Image

        with tempfile.TemporaryDirectory() as tmpdir:
            image_path = Path(tmpdir) / "large.png"
            large_image = Image.new("RGB", (4000, 4000), color=(255, 0, 0))
            large_image.save(image_path)
            original_bytes = image_path.read_bytes()
            message = main.build_multimodal_user_message("describe this", image_path, max_bytes=5000)

            payload = message["content"][1]["image_url"]["url"]
            encoded = payload.split(",", 1)[1]
            self.assertLess(len(encoded), 5000)
            self.assertLess(len(encoded), len(original_bytes) * 2)


if __name__ == "__main__":
    unittest.main()
