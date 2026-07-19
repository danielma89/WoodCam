"""FreeCADCmd smoke for the optional bitmap-to-vector boundary."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main():
    try:
        import FreeCAD
        emit = lambda text: FreeCAD.Console.PrintMessage(str(text) + "\n")
    except Exception:
        emit = print
    if shutil.which("potrace") is None:
        emit("Bitmap trace smoke: SKIP (potrace indisponível)")
        return
    from PIL import Image, ImageDraw
    from woodcam_editor.importers.bitmap_trace import BitmapTraceOptions, trace_bitmap

    with tempfile.TemporaryDirectory(prefix="woodcam-bitmap-smoke-") as directory:
        source = os.path.join(directory, "source.png")
        image = Image.new("RGB", (160, 100), "white")
        draw = ImageDraw.Draw(image)
        draw.ellipse((20, 15, 90, 85), fill="black")
        draw.rectangle((105, 30, 150, 70), fill="black")
        image.save(source)
        result = trace_bitmap(
            source,
            layer_id="layer",
            options=BitmapTraceOptions(
                threshold=128,
                target_width_mm=160.0,
                noise_pixels=2,
            ),
        )
        assert len(result.entities) >= 2, len(result.entities)
        assert all(entity.closed for entity in result.entities)
        assert all(
            entity.metadata.get("source_format") == "bitmap_trace"
            for entity in result.entities
        )
    emit("WoodCAM bitmap trace smoke: OK")


# FreeCADCmd executes scripts under its own module name rather than
# ``__main__``; keep the smoke top-level like the other FreeCAD scripts.
main()
