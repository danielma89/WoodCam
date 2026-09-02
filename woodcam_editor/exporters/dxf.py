"""Dependency-free ASCII DXF exporter (millimetres, exact common entities)."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional, Sequence


def _number(value: float) -> str:
    value = 0.0 if abs(float(value)) < 5e-13 else float(value)
    return format(value, ".12g")


def _pair(lines: list[str], code: int, value: Any) -> None:
    lines.extend((str(int(code)), str(value)))


def _layer_name(document: Any, entity: Any) -> str:
    layer = (getattr(document, "layers_by_id", {}) or {}).get(getattr(entity, "layer_id", None))
    value = str(getattr(layer, "name", getattr(entity, "layer_id", "0")) or "0")
    return value.replace("\r", " ").replace("\n", " ")[:255]


def _layer_color(document: Any, entity: Any) -> Optional[int]:
    layer = (getattr(document, "layers_by_id", {}) or {}).get(getattr(entity, "layer_id", None))
    value = str(getattr(layer, "color", "") or "").strip()
    if len(value) == 4 and value.startswith("#"):
        value = "#" + "".join(character * 2 for character in value[1:])
    if len(value) == 7 and value.startswith("#"):
        try:
            return int(value[1:], 16)
        except ValueError:
            return None
    return None


def _selected_entities(document: Any, entity_ids: Optional[Sequence[str]], visible_only: bool):
    entities = getattr(document, "entities_by_id", {}) or {}
    layers = getattr(document, "layers_by_id", {}) or {}
    requested_ids = (
        tuple(str(item) for item in entity_ids)
        if entity_ids is not None
        else tuple(str(item) for item in entities)
    )
    result = []
    visited = set()

    def append_leaf(entity_id: str) -> None:
        if entity_id in visited:
            return
        visited.add(entity_id)
        entity = entities.get(entity_id)
        if entity is None:
            return
        child_ids = tuple(getattr(entity, "child_ids", ()) or ())
        if child_ids:
            for child_id in child_ids:
                append_leaf(str(child_id))
            return
        if not hasattr(entity, "layer_id"):
            return
        layer = layers.get(entity.layer_id)
        if visible_only and layer is not None and not bool(
            getattr(layer, "visible", True)
        ):
            return
        result.append(entity)

    for requested_id in requested_ids:
        append_leaf(requested_id)
    return result


def _entity_header(lines: list[str], entity_type: str, layer: str, subclass: str) -> None:
    _pair(lines, 0, entity_type)
    _pair(lines, 100, "AcDbEntity")
    _pair(lines, 8, layer)
    _pair(lines, 100, subclass)


def _write_line(lines: list[str], span: Any, layer: str) -> None:
    _entity_header(lines, "LINE", layer, "AcDbLine")
    _pair(lines, 10, _number(span.start.x))
    _pair(lines, 20, _number(span.start.y))
    _pair(lines, 30, 0)
    _pair(lines, 11, _number(span.end.x))
    _pair(lines, 21, _number(span.end.y))
    _pair(lines, 31, 0)


def _write_arc(lines: list[str], span: Any, layer: str) -> None:
    _entity_header(lines, "ARC", layer, "AcDbCircle")
    _pair(lines, 10, _number(span.center.x))
    _pair(lines, 20, _number(span.center.y))
    _pair(lines, 30, 0)
    _pair(lines, 40, _number(span.radius))
    _pair(lines, 100, "AcDbArc")
    if bool(span.clockwise):
        start_angle = math.degrees(span.end_angle) % 360.0
        end_angle = math.degrees(span.start_angle) % 360.0
    else:
        start_angle = math.degrees(span.start_angle) % 360.0
        end_angle = math.degrees(span.end_angle) % 360.0
    _pair(lines, 50, _number(start_angle))
    _pair(lines, 51, _number(end_angle))


def _write_spline(lines: list[str], span: Any, layer: str) -> None:
    _entity_header(lines, "SPLINE", layer, "AcDbSpline")
    _pair(lines, 70, 8)
    _pair(lines, 71, 3)
    _pair(lines, 72, 8)
    _pair(lines, 73, 4)
    _pair(lines, 74, 0)
    for knot in (0, 0, 0, 0, 1, 1, 1, 1):
        _pair(lines, 40, knot)
    for point in (span.start, span.control1, span.control2, span.end):
        _pair(lines, 10, _number(point.x))
        _pair(lines, 20, _number(point.y))
        _pair(lines, 30, 0)


def _can_polyline(entity: Any) -> bool:
    return all(
        "line" in type(span).__name__.lower() or "arc" in type(span).__name__.lower()
        for span in entity.spans
    )


def _write_lwpolyline(lines: list[str], entity: Any, layer: str) -> None:
    spans = list(entity.spans)
    vertices = [(span.start, span) for span in spans]
    if not entity.closed:
        vertices.append((spans[-1].end, None))
    _entity_header(lines, "LWPOLYLINE", layer, "AcDbPolyline")
    _pair(lines, 90, len(vertices))
    _pair(lines, 70, 1 if entity.closed else 0)
    for point, span in vertices:
        _pair(lines, 10, _number(point.x))
        _pair(lines, 20, _number(point.y))
        if span is not None and "arc" in type(span).__name__.lower():
            _pair(lines, 42, _number(math.tan(float(span.sweep_angle) * 0.25)))


def _write_path(lines: list[str], entity: Any, layer: str) -> None:
    if _can_polyline(entity):
        _write_lwpolyline(lines, entity, layer)
        return
    for span in entity.spans:
        name = type(span).__name__.lower()
        if "line" in name:
            _write_line(lines, span, layer)
        elif "arc" in name:
            _write_arc(lines, span, layer)
        elif "bezier" in name:
            _write_spline(lines, span, layer)


def _write_circle(lines: list[str], entity: Any, layer: str) -> None:
    _entity_header(lines, "CIRCLE", layer, "AcDbCircle")
    _pair(lines, 10, _number(entity.center.x))
    _pair(lines, 20, _number(entity.center.y))
    _pair(lines, 30, 0)
    _pair(lines, 40, _number(entity.radius))


def _write_ellipse(lines: list[str], entity: Any, layer: str) -> None:
    _entity_header(lines, "ELLIPSE", layer, "AcDbEllipse")
    _pair(lines, 10, _number(entity.center.x))
    _pair(lines, 20, _number(entity.center.y))
    _pair(lines, 30, 0)
    major = max(float(entity.radius_x), float(entity.radius_y))
    minor = min(float(entity.radius_x), float(entity.radius_y))
    rotation = float(getattr(entity, "rotation", 0.0))
    if entity.radius_y > entity.radius_x:
        rotation += math.pi * 0.5
    _pair(lines, 11, _number(math.cos(rotation) * major))
    _pair(lines, 21, _number(math.sin(rotation) * major))
    _pair(lines, 31, 0)
    _pair(lines, 40, _number(minor / major))
    _pair(lines, 41, 0)
    _pair(lines, 42, _number(math.tau))


def document_to_dxf(
    document: Any,
    *,
    entity_ids: Optional[Sequence[str]] = None,
    visible_only: bool = True,
) -> str:
    entities = _selected_entities(document, entity_ids, visible_only)
    layer_specs = {}
    for entity in entities:
        name = _layer_name(document, entity)
        layer_specs.setdefault(name, _layer_color(document, entity))
    if not layer_specs:
        layer_specs["0"] = None
    layers = sorted(layer_specs)
    lines: list[str] = []
    for code, value in (
        (0, "SECTION"), (2, "HEADER"),
        (9, "$ACADVER"), (1, "AC1027"),
        (9, "$INSUNITS"), (70, 4),
        (0, "ENDSEC"),
        (0, "SECTION"), (2, "TABLES"),
        (0, "TABLE"), (2, "LAYER"), (70, len(layers)),
    ):
        _pair(lines, code, value)
    for layer in layers:
        _pair(lines, 0, "LAYER")
        _pair(lines, 100, "AcDbSymbolTableRecord")
        _pair(lines, 100, "AcDbLayerTableRecord")
        _pair(lines, 2, layer)
        _pair(lines, 70, 0)
        _pair(lines, 62, 7)
        if layer_specs[layer] is not None:
            _pair(lines, 420, layer_specs[layer])
        _pair(lines, 6, "CONTINUOUS")
    for code, value in ((0, "ENDTAB"), (0, "ENDSEC"), (0, "SECTION"), (2, "ENTITIES")):
        _pair(lines, code, value)
    for entity in entities:
        layer = _layer_name(document, entity)
        name = type(entity).__name__.lower()
        if "path" in name or hasattr(entity, "spans"):
            _write_path(lines, entity, layer)
        elif "ellipse" in name or hasattr(entity, "radius_x"):
            _write_ellipse(lines, entity, layer)
        elif "circle" in name or hasattr(entity, "radius"):
            _write_circle(lines, entity, layer)
    _pair(lines, 0, "ENDSEC")
    _pair(lines, 0, "EOF")
    return "\n".join(lines) + "\n"


def export_dxf(
    document: Any,
    path: str | Path,
    *,
    entity_ids: Optional[Sequence[str]] = None,
    visible_only: bool = True,
) -> Path:
    target = Path(path)
    target.write_text(
        document_to_dxf(document, entity_ids=entity_ids, visible_only=visible_only),
        encoding="utf-8",
        newline="\n",
    )
    return target
