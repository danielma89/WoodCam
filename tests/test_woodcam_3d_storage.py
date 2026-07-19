import json
import unittest

from woodcam_3d.storage import decode_moves, encode_moves


class ToolpathStorageTests(unittest.TestCase):
    def test_moves_round_trip_and_compress_repetitive_toolpath(self):
        moves = [
            {
                "type": "cut",
                "x": index / 10.0,
                "y": 12.5,
                "z": -2.0,
                "feed": 1200.0,
            }
            for index in range(5000)
        ]
        encoded = encode_moves(moves)
        legacy = json.dumps(moves, ensure_ascii=False, separators=(",", ":"))
        self.assertEqual(decode_moves(encoded), moves)
        self.assertLess(len(encoded), len(legacy) * 0.35)

    def test_legacy_moves_remain_readable(self):
        moves = [{"type": "rapid", "x": 1.0, "y": 2.0, "z": 3.0}]
        self.assertEqual(decode_moves("", json.dumps(moves)), moves)


if __name__ == "__main__":
    unittest.main()
