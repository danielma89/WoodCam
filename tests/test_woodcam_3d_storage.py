import base64
import json
import unittest
from unittest.mock import patch
import zlib

from woodcam_3d.storage import (
    decode_moves, encode_moves, iter_legacy_moves, repack_legacy_moves,
)


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

    def test_large_repeated_cut_metadata_is_stored_once(self):
        segment_ids = tuple(f"external-{index:05d}" for index in range(800))
        profile_id = "physical-profile:" + ",".join(segment_ids)
        moves = [
            {
                "type": "feed_cut", "x": float(index), "y": 0.0, "z": -3.0,
                "cut_operation_id": "cut-00001", "segment_ids": segment_ids,
                "profile_id": profile_id,
                "profile_loop_id": profile_id + ":pass-0001",
            }
            for index in range(200)
        ]
        encoded = encode_moves(moves)
        baseline = base64.b64encode(
            zlib.compress(json.dumps(moves, separators=(",", ":")).encode(), 6)
        )
        self.assertLess(len(encoded), len(baseline) * 0.1)
        self.assertEqual(decode_moves(encoded), json.loads(json.dumps(moves)))
        legacy = baseline.decode("ascii")
        self.assertEqual(list(iter_legacy_moves(legacy)), json.loads(json.dumps(moves)))
        repacked, count = repack_legacy_moves(legacy)
        self.assertEqual(count, len(moves))
        self.assertLess(len(repacked), len(legacy) * 0.1)
        self.assertEqual(decode_moves(repacked), json.loads(json.dumps(moves)))
        with patch("woodcam_3d.storage._LEGACY_STREAM_THRESHOLD", 1):
            self.assertEqual(decode_moves(legacy), json.loads(json.dumps(moves)))


if __name__ == "__main__":
    unittest.main()
