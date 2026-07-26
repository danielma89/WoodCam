"""Qt font outlines converted into immutable WoodCAM vector entities.

The editor stores only paths and groups.  ``QPainterPath`` is used solely as a
font-outline reader at creation time; no Qt shape is retained in the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from woodcam_editor.domain import Affine2D, GroupEntity, PathEntity, Vec2, new_id

from .compat import QtCore, QtGui


@dataclass(frozen=True)
class TextVectorOptions:
    text: str
    family: str = "Sans Serif"
    height_mm: float = 20.0
    bold: bool = False
    italic: bool = False


@dataclass(frozen=True)
class TextVectorResult:
    entities: Tuple[object, ...]
    group_id: str
    bounds_height_mm: float


def _points_from_polygon(polygon, *, scale: float, left: float, bottom: float):
    points = []
    for point in polygon:
        value = Vec2(
            (float(point.x()) - left) * scale,
            (bottom - float(point.y())) * scale,
        )
        if not points or not value.almost_equals(points[-1], 1.0e-8):
            points.append(value)
    if len(points) > 1 and points[0].almost_equals(points[-1], 1.0e-8):
        points.pop()
    return tuple(points)


def create_text_outlines(
    options: TextVectorOptions,
    *,
    layer_id: str,
    origin: Vec2 | None = None,
    group_id: str | None = None,
) -> TextVectorResult:
    """Convert text to closed vector paths and one compound group.

    ``height_mm`` is the visible height of the whole generated block, not a
    platform-dependent point size.  Text is intentionally converted to curves
    at confirmation time so a saved FCStd remains portable even if the font is
    not installed on another machine.
    """

    text = str(options.text or "")
    if not text.strip():
        raise ValueError("Digite algum texto para criar os vetores.")
    height_mm = float(options.height_mm)
    if not 0.01 <= height_mm <= 100000.0:
        raise ValueError("A altura do texto deve estar entre 0,01 e 100000 mm.")
    if not layer_id:
        raise ValueError("A camada do texto é obrigatória.")
    if origin is None:
        origin = Vec2(0.0, 0.0)

    # A generous pixel font size gives Qt enough precision before the one-time
    # conversion to millimetres.  The final scale is derived from the actual
    # ink bounds, not a font's ascent/descent metrics.
    font = QtGui.QFont(str(options.family or "Sans Serif"))
    font.setPixelSize(2048)
    font.setBold(bool(options.bold))
    font.setItalic(bool(options.italic))
    painter_path = QtGui.QPainterPath()
    metrics = QtGui.QFontMetricsF(font)
    baseline = 0.0
    for line in text.splitlines() or (text,):
        painter_path.addText(QtCore.QPointF(0.0, baseline), font, line)
        baseline += float(metrics.lineSpacing())

    raw_bounds = painter_path.boundingRect()
    if raw_bounds.isEmpty() or raw_bounds.height() <= 1.0e-9:
        raise ValueError("A fonte escolhida não gerou contornos para este texto.")
    scale = height_mm / float(raw_bounds.height())
    metadata = {
        "source_kind": "text_outline",
        "text": text,
        "font_family": str(font.family()),
        "text_height_mm": height_mm,
        "bold": bool(options.bold),
        "italic": bool(options.italic),
    }
    paths = []
    transform = Affine2D.translation(origin)
    for polygon in painter_path.toSubpathPolygons():
        points = _points_from_polygon(
            polygon,
            scale=scale,
            left=float(raw_bounds.left()),
            bottom=float(raw_bounds.bottom()),
        )
        if len(points) < 3:
            continue
        paths.append(
            PathEntity.from_points(
                layer_id,
                points,
                closed=True,
                metadata=metadata,
            ).transformed(transform)
        )
    if not paths:
        raise ValueError("A fonte escolhida não gerou áreas fechadas utilizáveis.")

    group_id = str(group_id or new_id("text"))
    group = GroupEntity(
        id=group_id,
        layer_id=layer_id,
        child_ids=tuple(path.id for path in paths),
        metadata={
            **metadata,
            "name": "Texto: " + text.replace("\n", " ")[:48],
            "source_kind": "text_outline_group",
        },
    )
    return TextVectorResult(
        entities=tuple(paths) + (group,),
        group_id=group_id,
        bounds_height_mm=height_mm,
    )


def text_options_from_group(group) -> TextVectorOptions:
    """Recover editable text settings stored on a text-outline group."""

    metadata = dict(getattr(group, "metadata", {}) or {})
    if metadata.get("source_kind") != "text_outline_group":
        raise ValueError("O objeto selecionado não é um texto vetorial do WoodCAM.")
    return TextVectorOptions(
        text=str(metadata.get("text", "")),
        family=str(metadata.get("font_family", "Sans Serif")),
        height_mm=float(metadata.get("text_height_mm", 20.0)),
        bold=bool(metadata.get("bold", False)),
        italic=bool(metadata.get("italic", False)),
    )


__all__ = [
    "TextVectorOptions",
    "TextVectorResult",
    "create_text_outlines",
    "text_options_from_group",
]
