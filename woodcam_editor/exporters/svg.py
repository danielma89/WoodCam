"""SVG exporter preserving exact lines, circular arcs, Béziers and ellipses."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional, Sequence
import xml.etree.ElementTree as ET


SVG_NAMESPACE = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NAMESPACE)


def _number(value: float) -> str:
    value = 0.0 if abs(float(value)) < 5e-13 else float(value)
    return format(value, ".12g")


def _xy(point: Any) -> tuple[float, float]:
    return float(point.x), -float(point.y)


def _path_data(entity: Any) -> str:
    spans = list(getattr(entity, "spans", ()) or ())
    if not spans:
        return ""
    start_x, start_y = _xy(spans[0].start)
    commands = [f"M {_number(start_x)} {_number(start_y)}"]
    for span in spans:
        class_name = type(span).__name__.lower()
        end_x, end_y = _xy(span.end)
        if "arc" in class_name or hasattr(span, "center"):
            radius = float(getattr(span, "radius", span.start.distance_to(span.center)))
            sweep_angle = float(getattr(span, "sweep_angle", 0.0))
            large = 1 if abs(sweep_angle) > math.pi else 0
            # Y is reflected for SVG; domain clockwise becomes SVG sweep=1.
            sweep = 1 if bool(getattr(span, "clockwise", False)) else 0
            commands.append(
                "A {} {} 0 {} {} {} {}".format(
                    _number(radius),
                    _number(radius),
                    large,
                    sweep,
                    _number(end_x),
                    _number(end_y),
                )
            )
        elif "bezier" in class_name or hasattr(span, "control1"):
            control_1 = getattr(span, "control1", getattr(span, "control_1", None))
            control_2 = getattr(span, "control2", getattr(span, "control_2", None))
            c1_x, c1_y = _xy(control_1)
            c2_x, c2_y = _xy(control_2)
            commands.append(
                "C {} {} {} {} {} {}".format(
                    _number(c1_x),
                    _number(c1_y),
                    _number(c2_x),
                    _number(c2_y),
                    _number(end_x),
                    _number(end_y),
                )
            )
        else:
            commands.append(f"L {_number(end_x)} {_number(end_y)}")
    if bool(getattr(entity, "closed", False)):
        commands.append("Z")
    return " ".join(commands)


def _entity_bounds(entity: Any):
    method = getattr(entity, "bounds", None)
    return method() if callable(method) else None


def _selected_entities(document: Any, entity_ids: Optional[Sequence[str]], visible_only: bool):
    entities = getattr(document, "entities_by_id", {}) or {}
    layers = getattr(document, "layers_by_id", {}) or {}
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
        if visible_only and layer is not None and not bool(getattr(layer, "visible", True)):
            return
        result.append(entity)
    requested_ids = (
        tuple(str(item) for item in entity_ids)
        if entity_ids is not None
        else tuple(str(item) for item in entities)
    )
    for requested_id in requested_ids:
        append_leaf(requested_id)
    return result


def _append_svg_entities(root: Any, document: Any, entities: Sequence[Any]) -> None:
    layers = getattr(document, "layers_by_id", {}) or {}
    groups = {}
    for entity in entities:
        layer = layers.get(entity.layer_id)
        group = groups.get(entity.layer_id)
        if group is None:
            color = str(getattr(layer, "color", "#2563eb") or "#2563eb")
            group = ET.SubElement(
                root,
                f"{{{SVG_NAMESPACE}}}g",
                {
                    "id": str(entity.layer_id),
                    "data-layer-name": str(getattr(layer, "name", "Desenho")),
                    "data-layer-purpose": str(getattr(layer, "purpose", "design") or "design"),
                    "fill": "none",
                    "stroke": color,
                    "stroke-width": "0.2",
                    "vector-effect": "non-scaling-stroke",
                },
            )
            groups[entity.layer_id] = group
        class_name = type(entity).__name__.lower()
        common = {"id": str(entity.id), "data-woodcam-entity-id": str(entity.id)}
        if "path" in class_name or hasattr(entity, "spans"):
            data = _path_data(entity)
            if data:
                ET.SubElement(group, f"{{{SVG_NAMESPACE}}}path", {**common, "d": data})
        elif "circle" in class_name or hasattr(entity, "radius"):
            center_x, center_y = _xy(entity.center)
            ET.SubElement(
                group,
                f"{{{SVG_NAMESPACE}}}circle",
                {
                    **common,
                    "cx": _number(center_x),
                    "cy": _number(center_y),
                    "r": _number(entity.radius),
                },
            )
        elif "ellipse" in class_name or hasattr(entity, "radius_x"):
            center_x, center_y = _xy(entity.center)
            rotation = -math.degrees(float(getattr(entity, "rotation", 0.0)))
            attributes = {
                **common,
                "cx": _number(center_x),
                "cy": _number(center_y),
                "rx": _number(entity.radius_x),
                "ry": _number(entity.radius_y),
            }
            if abs(rotation) > 1e-12:
                attributes["transform"] = "rotate({} {} {})".format(
                    _number(rotation), _number(center_x), _number(center_y)
                )
            ET.SubElement(group, f"{{{SVG_NAMESPACE}}}ellipse", attributes)


def _bounds_intersect(entity_bounds: Any, sheet_bounds: Sequence[float]) -> bool:
    if entity_bounds is None:
        return False
    min_x, min_y, max_x, max_y = map(float, sheet_bounds)
    return not (
        float(entity_bounds.max_x) < min_x - 1.0e-9
        or float(entity_bounds.min_x) > max_x + 1.0e-9
        or float(entity_bounds.max_y) < min_y - 1.0e-9
        or float(entity_bounds.min_y) > max_y + 1.0e-9
    )


def _toolpath_svg_path(segments: Sequence[Any]) -> str:
    commands = []
    for segment in tuple(segments or ()):
        if not isinstance(segment, (tuple, list)) or len(segment) != 2:
            continue
        try:
            start, end = segment
            x0, y0 = float(start[0]), -float(start[1])
            x1, y1 = float(end[0]), -float(end[1])
        except (IndexError, TypeError, ValueError):
            continue
        commands.append(
            "M {} {} L {} {}".format(
                _number(x0), _number(y0), _number(x1), _number(y1)
            )
        )
    return " ".join(commands)


def _append_toolpath(root: Any, components: Any) -> None:
    values = dict(components or {})
    group = ET.SubElement(
        root,
        f"{{{SVG_NAMESPACE}}}g",
        {"id": "woodcam-toolpath", "fill": "none"},
    )
    styles = (
        ("rapid", "#d946ef", "0.28", "2 1.5"),
        ("ramp", "#94a3b8", "0.34", None),
        ("cut", "#64748b", "0.32", None),
        ("corner", "#475569", "0.38", None),
    )
    for key, color, width, dash in styles:
        data = _toolpath_svg_path(values.get(key, ()))
        if not data:
            continue
        attributes = {
            "data-toolpath-kind": key,
            "d": data,
            "stroke": color,
            "stroke-width": width,
            "vector-effect": "non-scaling-stroke",
        }
        if dash:
            attributes["stroke-dasharray"] = dash
        ET.SubElement(group, f"{{{SVG_NAMESPACE}}}path", attributes)

    for value in tuple(values.get("entry_points", ()) or ()):
        try:
            x_value, y_value = float(value[0]), -float(value[1])
        except (IndexError, TypeError, ValueError):
            continue
        ET.SubElement(
            group,
            f"{{{SVG_NAMESPACE}}}circle",
            {
                "cx": _number(x_value),
                "cy": _number(y_value),
                "r": "1.8",
                "stroke": "#d946ef",
                "stroke-width": "0.35",
                "fill": "#d1fae5",
            },
        )

    cut_segments = tuple(values.get("cut", ()) or ())
    stride = max(1, int(math.ceil(len(cut_segments) / 24.0)))
    direction_group = ET.SubElement(
        group,
        f"{{{SVG_NAMESPACE}}}g",
        {"data-toolpath-kind": "direction", "fill": "#334155", "stroke": "none"},
    )
    for segment in cut_segments[::stride]:
        try:
            start, end = segment
            x0, y0 = float(start[0]), float(start[1])
            x1, y1 = float(end[0]), float(end[1])
        except (IndexError, TypeError, ValueError):
            continue
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length <= 1.0e-9:
            continue
        ux, uy = dx / length, dy / length
        size = min(5.0, max(1.6, length * 0.08))
        bx, by = x1 - ux * size, y1 - uy * size
        px, py = -uy * size * 0.42, ux * size * 0.42
        points = (
            (x1, -y1),
            (bx + px, -(by + py)),
            (bx - px, -(by - py)),
        )
        ET.SubElement(
            direction_group,
            f"{{{SVG_NAMESPACE}}}polygon",
            {"points": " ".join("%s,%s" % (_number(x), _number(y)) for x, y in points)},
        )


def document_to_svg(
    document: Any,
    *,
    entity_ids: Optional[Sequence[str]] = None,
    visible_only: bool = True,
    margin_mm: float = 0.0,
) -> str:
    entities = _selected_entities(document, entity_ids, visible_only)
    bounds = [_entity_bounds(entity) for entity in entities]
    bounds = [item for item in bounds if item is not None]
    if bounds:
        min_x = min(item.min_x for item in bounds) - margin_mm
        min_y = min(item.min_y for item in bounds) - margin_mm
        max_x = max(item.max_x for item in bounds) + margin_mm
        max_y = max(item.max_y for item in bounds) + margin_mm
    else:
        min_x = min_y = 0.0
        max_x = max_y = 1.0
    width = max(max_x - min_x, 1e-9)
    height = max(max_y - min_y, 1e-9)
    root = ET.Element(
        f"{{{SVG_NAMESPACE}}}svg",
        {
            "version": "1.1",
            "width": f"{_number(width)}mm",
            "height": f"{_number(height)}mm",
            "viewBox": "{} {} {} {}".format(
                _number(min_x),
                _number(-max_y),
                _number(width),
                _number(height),
            ),
        },
    )
    _append_svg_entities(root, document, entities)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def document_sheet_to_svg(
    document: Any,
    sheet_bounds: Sequence[float],
    *,
    visible_only: bool = True,
    toolpath_components: Any = None,
    include_sheet_fill: bool = True,
) -> str:
    """Render one physical sheet as an SVG suitable for TechDraw Symbol.

    The work-area rectangle is presentation geometry only.  It is never added
    to ``VectorDocument`` and therefore cannot become a CAM contour.
    """

    min_x, min_y, max_x, max_y = map(float, sheet_bounds)
    if max_x <= min_x or max_y <= min_y:
        raise ValueError("A chapa precisa ter largura e altura positivas.")
    width, height = max_x - min_x, max_y - min_y
    root = ET.Element(
        f"{{{SVG_NAMESPACE}}}svg",
        {
            "version": "1.1",
            "width": f"{_number(width)}mm",
            "height": f"{_number(height)}mm",
            "viewBox": "{} {} {} {}".format(
                _number(min_x), _number(-max_y), _number(width), _number(height)
            ),
            "overflow": "hidden",
            "data-woodcam-role": "print-sheet",
        },
    )
    ET.SubElement(
        root,
        f"{{{SVG_NAMESPACE}}}rect",
        {
            "id": "woodcam-sheet-boundary",
            "data-woodcam-role": "sheet-boundary",
            "x": _number(min_x),
            "y": _number(-max_y),
            "width": _number(width),
            "height": _number(height),
            "fill": "#eff8ff" if include_sheet_fill else "none",
            "stroke": "#0ea5e9",
            "stroke-width": "0.35",
            "stroke-dasharray": "2.5 1.5",
            "vector-effect": "non-scaling-stroke",
        },
    )
    entities = tuple(
        entity
        for entity in _selected_entities(document, None, visible_only)
        if _bounds_intersect(_entity_bounds(entity), sheet_bounds)
    )
    content_group = ET.SubElement(
        root,
        f"{{{SVG_NAMESPACE}}}g",
        {"id": "woodcam-sheet-content", "data-woodcam-role": "sheet-content"},
    )
    _append_svg_entities(content_group, document, entities)
    if toolpath_components:
        _append_toolpath(root, toolpath_components)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="unicode", xml_declaration=True)


def export_svg(
    document: Any,
    path: str | Path,
    *,
    entity_ids: Optional[Sequence[str]] = None,
    visible_only: bool = True,
    margin_mm: float = 0.0,
) -> Path:
    target = Path(path)
    target.write_text(
        document_to_svg(
            document,
            entity_ids=entity_ids,
            visible_only=visible_only,
            margin_mm=margin_mm,
        ),
        encoding="utf-8",
    )
    return target
