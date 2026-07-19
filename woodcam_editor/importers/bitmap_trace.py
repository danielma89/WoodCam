"""Bitmap-to-vector tracing through the local Potrace executable.

The Editor keeps raster pixels out of ``VectorDocument``.  A bitmap is reduced
to a deterministic black/white mask, Potrace fits closed cubic paths, and the
existing SVG importer converts those paths to exact domain spans.  Applying the
result remains a normal preview-first Editor command.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
from typing import Any

from .part_shape import ImportIssue, ImportResult
from .svg import import_svg


@dataclass(frozen=True)
class BitmapTraceOptions:
    threshold: int = 128
    invert: bool = False
    noise_pixels: int = 2
    corner_fit: float = 1.0
    curve_tolerance: float = 0.2
    target_width_mm: float = 200.0

    def __post_init__(self) -> None:
        if not 0 <= int(self.threshold) <= 255:
            raise ValueError("O limiar precisa ficar entre 0 e 255.")
        if int(self.noise_pixels) < 0:
            raise ValueError("O filtro de ruído não pode ser negativo.")
        if not 0.0 <= float(self.corner_fit) <= 1.34:
            raise ValueError("O ajuste de cantos precisa ficar entre 0 e 1,34.")
        if float(self.curve_tolerance) < 0.0:
            raise ValueError("A tolerância de curva não pode ser negativa.")
        if not math.isfinite(float(self.target_width_mm)) or float(self.target_width_mm) <= 0.0:
            raise ValueError("A largura final precisa ser positiva.")


def _pillow_image(path: str | Path):
    try:
        from PIL import Image
    except ImportError as error:  # pragma: no cover - deployment dependency
        raise RuntimeError("A vetorização de imagem requer Pillow.") from error
    source = Image.open(str(path)).convert("RGBA")
    background = Image.new("RGBA", source.size, (255, 255, 255, 255))
    background.alpha_composite(source)
    return background.convert("L")


def threshold_mask(path: str | Path, options: BitmapTraceOptions):
    """Return a Pillow 1-bit image where the desired region is black."""

    from PIL import Image

    gray = _pillow_image(path)
    threshold = int(options.threshold)
    if options.invert:
        mask = gray.point(lambda value: 0 if value >= threshold else 255, mode="1")
    else:
        mask = gray.point(lambda value: 0 if value <= threshold else 255, mode="1")
    return mask.convert("1", dither=getattr(Image, "Dither", Image).NONE)


def threshold_preview_png(path: str | Path, options: BitmapTraceOptions) -> bytes:
    output = BytesIO()
    threshold_mask(path, options).convert("L").save(output, format="PNG")
    return output.getvalue()


def trace_bitmap(
    path: str | Path,
    *,
    layer_id: str,
    options: BitmapTraceOptions | None = None,
) -> ImportResult:
    """Trace one bitmap into closed PathEntity objects in millimetres."""

    options = options or BitmapTraceOptions()
    source_path = Path(path)
    if not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    executable = shutil.which("potrace")
    if not executable:
        raise RuntimeError(
            "Potrace não foi encontrado. Instale o pacote 'potrace' para vetorizar imagens."
        )
    mask = threshold_mask(source_path, options)
    with tempfile.TemporaryDirectory(prefix="woodcam-trace-") as directory:
        bitmap_path = Path(directory) / "mask.pbm"
        svg_path = Path(directory) / "trace.svg"
        mask.save(str(bitmap_path), format="PPM")
        command = [
            executable,
            "--svg",
            "--tight",
            "--width",
            "%.9gmm" % float(options.target_width_mm),
            "--turdsize",
            str(int(options.noise_pixels)),
            "--alphamax",
            "%.9g" % float(options.corner_fit),
            "--opttolerance",
            "%.9g" % float(options.curve_tolerance),
            "--output",
            str(svg_path),
            str(bitmap_path),
        ]
        process = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
        if process.returncode != 0 or not svg_path.is_file():
            reason = (process.stderr or process.stdout or "falha desconhecida").strip()
            raise RuntimeError("Potrace não conseguiu vetorizar a imagem: %s" % reason)
        imported = import_svg(svg_path, layer_id=layer_id)
    trace_metadata = {
        "source_format": "bitmap_trace",
        "bitmap_source": str(source_path),
        "trace_threshold": int(options.threshold),
        "trace_invert": bool(options.invert),
        "trace_noise_pixels": int(options.noise_pixels),
        "trace_corner_fit": float(options.corner_fit),
        "trace_curve_tolerance": float(options.curve_tolerance),
        "trace_target_width_mm": float(options.target_width_mm),
    }
    entities = []
    for entity in imported.entities:
        metadata = dict(getattr(entity, "metadata", {}) or {})
        metadata.update(trace_metadata)
        entities.append(replace(entity, metadata=metadata))
    issues = list(imported.issues)
    if not entities:
        issues.append(
            ImportIssue(-1, "bitmap", "Nenhuma região atravessou o limiar/filtro de ruído.")
        )
    return ImportResult(
        tuple(entities),
        tuple(issues),
        {
            **dict(imported.source_metadata),
            **trace_metadata,
            "pixel_width": int(mask.width),
            "pixel_height": int(mask.height),
        },
        {},
    )


__all__ = [
    "BitmapTraceOptions",
    "threshold_mask",
    "threshold_preview_png",
    "trace_bitmap",
]
