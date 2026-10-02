"""Compactação compatível dos percursos persistidos no FCStd."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import zlib

try:
    import orjson as _fast_json
except ImportError:
    _fast_json = None


_SHARED_FIELDS = (
    "segment_ids", "cleared_link_segment_ids", "profile_id", "profile_loop_id"
)
_REF_KEY = "$woodcam_shared_ref"
_V2_PREFIX = "WC2:"
_LEGACY_STREAM_THRESHOLD = 16_000_000


def _large_shared_value(name, value):
    if name in {"segment_ids", "cleared_link_segment_ids"}:
        return isinstance(value, (tuple, list)) and len(value) >= 8
    return isinstance(value, str) and len(value) >= 128


class _SharedMoveValues:
    def __init__(self):
        self.values = []
        self._identity = {}
        self._fingerprints = {}
        self._scoped = {}

    def reference(self, name, value, scope=None):
        scoped_key = (name, scope) if scope is not None else None
        if scoped_key is not None:
            existing = self._scoped.get(scoped_key)
            if existing is not None and self.values[existing][1] == value:
                return existing
        identity = (name, id(value))
        existing = self._identity.get(identity)
        if existing is not None and self.values[existing][1] is value:
            if scoped_key is not None:
                self._scoped[scoped_key] = existing
            return existing
        serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.blake2b(serialized.encode("utf-8"), digest_size=16).digest()
        fingerprint = (name, digest)
        existing = self._fingerprints.get(fingerprint)
        if existing is not None and self.values[existing][1] == value:
            self._identity[identity] = existing
            if scoped_key is not None:
                self._scoped[scoped_key] = existing
            return existing
        index = len(self.values)
        self.values.append((name, value))
        self._identity[identity] = index
        self._fingerprints[fingerprint] = index
        if scoped_key is not None:
            self._scoped[scoped_key] = index
        return index


def _compact_move(move, shared):
    compact = dict(move)
    for name in _SHARED_FIELDS:
        value = compact.get(name)
        if _large_shared_value(name, value):
            compact[name] = {
                _REF_KEY: shared.reference(
                    name, value,
                    scope=move.get("cut_operation_id")
                    if name != "cleared_link_segment_ids" else None,
                )
            }
    return compact


def _json_loads(raw):
    return _fast_json.loads(raw) if _fast_json is not None else json.loads(raw)


def iter_legacy_moves(encoded):
    """Read old flat move arrays without inflating the whole path in memory."""
    inflater = zlib.decompressobj()
    pending = b""
    opened = False
    boundary = b'},{"type":'
    chunk_size = 4 * 262144

    def raw_chunks():
        for index in range(0, len(encoded), chunk_size):
            yield inflater.decompress(
                base64.b64decode(encoded[index:index + chunk_size])
            )
        yield inflater.flush()

    for raw in raw_chunks():
        pending += raw
        if not opened:
            stripped = pending.lstrip()
            if not stripped:
                continue
            if stripped[:1] != b"[":
                raise ValueError("Percurso legado não começa com uma lista.")
            pending = stripped[1:]
            opened = True
        while True:
            split_at = pending.find(boundary)
            if split_at < 0:
                break
            yield _json_loads(pending[:split_at + 1])
            pending = pending[split_at + 2:]
        if len(pending) > 50_000_000:
            raise ValueError("Movimento legado excede o limite de leitura segura.")
    tail = pending.strip()
    if not opened or not tail.endswith(b"]") or not inflater.eof:
        raise ValueError("Percurso legado incompleto.")
    last = tail[:-1].strip()
    if last:
        yield _json_loads(last)


def repack_legacy_moves(encoded, progress=None):
    """Preserve old movements while deduplicating long repeated metadata."""
    shared = _SharedMoveValues()
    compressor = zlib.compressobj(6)
    output = io.BytesIO()
    output.write(compressor.compress(b'{"woodcam_moves_format":2,"moves":['))
    count = 0
    for move in iter_legacy_moves(encoded):
        if count:
            output.write(compressor.compress(b","))
        compact = _compact_move(move, shared)
        serialized = json.dumps(
            compact, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        output.write(compressor.compress(serialized))
        count += 1
        if progress is not None and count % 5000 == 0:
            progress(count)
    shared_json = json.dumps(
        shared.values, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    output.write(compressor.compress(b'],"shared":' + shared_json + b"}"))
    output.write(compressor.flush())
    return _V2_PREFIX + base64.b64encode(output.getvalue()).decode("ascii"), count


def encode_moves(moves):
    shared = _SharedMoveValues()
    compact = [_compact_move(move, shared) for move in moves]
    payload = (
        {"woodcam_moves_format": 2, "moves": compact, "shared": shared.values}
        if shared.values else compact
    )
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    encoded = base64.b64encode(zlib.compress(raw, 6)).decode("ascii")
    return _V2_PREFIX + encoded if shared.values else encoded


def decode_moves(encoded="", legacy_json=""):
    if encoded:
        is_v2 = str(encoded).startswith(_V2_PREFIX)
        if not is_v2 and len(encoded) >= _LEGACY_STREAM_THRESHOLD:
            # Older files can repeat a whole trail's metadata on every move.
            # Parsing one move at a time avoids a multi-gigabyte raw JSON copy.
            shared = _SharedMoveValues()
            moves = []
            for move in iter_legacy_moves(str(encoded)):
                for name in _SHARED_FIELDS:
                    value = move.get(name)
                    if _large_shared_value(name, value):
                        index = shared.reference(
                            name, value,
                            scope=move.get("cut_operation_id")
                            if name != "cleared_link_segment_ids" else None,
                        )
                        move[name] = shared.values[index][1]
                moves.append(move)
            return moves
        raw = zlib.decompress(
            base64.b64decode(str(encoded)[len(_V2_PREFIX):] if is_v2 else str(encoded))
        ).decode("utf-8")
        payload = json.loads(raw)
        if isinstance(payload, list):
            return payload
        if not isinstance(payload, dict) or payload.get("woodcam_moves_format") != 2:
            raise ValueError("Formato de percurso WoodCAM desconhecido.")
        shared = payload["shared"]
        moves = payload["moves"]
        for move in moves:
            for name in _SHARED_FIELDS:
                reference = move.get(name)
                if isinstance(reference, dict) and set(reference) == {_REF_KEY}:
                    index = int(reference[_REF_KEY])
                    stored_name, value = shared[index]
                    if stored_name != name:
                        raise ValueError("Referência de percurso WoodCAM inválida.")
                    move[name] = value
        return moves
    if legacy_json:
        return json.loads(str(legacy_json))
    return []


__all__ = ["decode_moves", "encode_moves"]
