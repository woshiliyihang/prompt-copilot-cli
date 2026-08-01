import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import main


class MCPTransportTests(unittest.TestCase):
    def test_normalize_sse_server_config(self) -> None:
        config = main.normalize_mcp_server_config(
            {
                "name": "remote",
                "transport": "sse",
                "url": "http://localhost:8000/sse",
                "headers": {"Authorization": "Bearer token"},
            }
        )

        self.assertEqual(config["transport"], "sse")
        self.assertEqual(config["url"], "http://localhost:8000/sse")
        self.assertEqual(config["headers"]["Authorization"], "Bearer token")


if __name__ == "__main__":
    unittest.main()
