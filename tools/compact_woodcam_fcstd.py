"""Create a smaller copy of an FCStd with legacy oversized WoodCAM paths.

Run with ``freecadcmd tools/compact_woodcam_fcstd.py SOURCE.FCStd COPY.FCStd``.
The source is only opened for reading. The copy keeps the same objects and
movements; only the encoding of long repeated move metadata changes.
"""

from __future__ import annotations

from pathlib import Path
import sys

import FreeCAD

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from woodcam_3d.storage import decode_moves, repack_legacy_moves
from woodcam_editor.adapters.freecad_store import protect_woodcam_document_view


def main(source_path, copy_path):
    source = Path(source_path).resolve(strict=True)
    target = Path(copy_path).resolve()
    if source == target or target.exists():
        raise ValueError("Escolha um arquivo de saída novo, diferente do FCStd original.")
    if target.suffix.lower() != ".fcstd":
        raise ValueError("A cópia deve ter extensão .FCStd.")
    source_stat = source.stat()
    document = FreeCAD.openDocument(str(source))
    if document is None:
        raise RuntimeError("Não foi possível abrir o FCStd original.")
    changed = []
    try:
        protect_woodcam_document_view(document)
        for operation in document.Objects:
            properties = set(getattr(operation, "PropertiesList", ()) or ())
            if "MovesCompressedBase64" not in properties:
                continue
            encoded = str(operation.MovesCompressedBase64 or "")
            if len(encoded) < 8_000_000 or encoded.startswith("WC2:"):
                continue
            print(
                "OTIMIZANDO", operation.Name, "CARACTERES", len(encoded),
                "MOVIMENTOS", int(getattr(operation, "MoveCount", 0) or 0),
                flush=True,
            )
            compact, count = repack_legacy_moves(
                encoded,
                progress=lambda amount: print("PROGRESSO", amount, flush=True),
            )
            if count != int(operation.MoveCount):
                raise ValueError(
                    f"{operation.Name}: número de movimentos mudou "
                    f"({count} versus {operation.MoveCount})."
                )
            if len(compact) >= len(encoded):
                raise ValueError(f"{operation.Name}: a codificação não reduziu o percurso.")
            operation.MovesCompressedBase64 = compact
            changed.append((operation.Name, len(encoded), len(compact), count))
            del encoded, compact
        if not changed:
            raise ValueError("O FCStd não contém percursos legados grandes a otimizar.")
        if (source.stat().st_size, source.stat().st_mtime_ns) != (
            source_stat.st_size, source_stat.st_mtime_ns
        ):
            raise RuntimeError("O FCStd original mudou durante a leitura; tente novamente.")
        document.saveAs(str(target))
    finally:
        FreeCAD.closeDocument(document.Name)

    reopened = FreeCAD.openDocument(str(target))
    try:
        for name, before, after, count in changed:
            operation = reopened.getObject(name)
            if operation is None or len(str(operation.MovesCompressedBase64)) != after:
                raise RuntimeError(f"Cópia inválida: percurso {name} não foi preservado.")
            if len(decode_moves(operation.MovesCompressedBase64)) != count:
                raise RuntimeError(f"Cópia inválida: movimentos de {name} não conferem.")
            print("VERIFICADO", name, before, "->", after, count, flush=True)
    finally:
        FreeCAD.closeDocument(reopened.Name)
    print("COPIA_OTIMIZADA", str(target), target.stat().st_size, flush=True)


if len(sys.argv) < 4:
    raise SystemExit("Uso: freecadcmd tools/compact_woodcam_fcstd.py SOURCE.FCStd COPY.FCStd")
main(sys.argv[2], sys.argv[3])
