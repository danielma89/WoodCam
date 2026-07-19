"""Compactação compatível dos percursos persistidos no FCStd."""

from __future__ import annotations

import base64
import json
import zlib


def encode_moves(moves):
    raw = json.dumps(moves, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.b64encode(zlib.compress(raw, 6)).decode("ascii")


def decode_moves(encoded="", legacy_json=""):
    if encoded:
        raw = zlib.decompress(base64.b64decode(str(encoded))).decode("utf-8")
        return json.loads(raw)
    if legacy_json:
        return json.loads(str(legacy_json))
    return []


__all__ = ["decode_moves", "encode_moves"]
