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
    selected = [entities[item] for item in entity_ids] if entity_ids is not None else list(entities.values())
    layers = getattr(document, "layers_by_id", {}) or {}
    result = []
    for entity in selected:
        if not hasattr(entity, "layer_id"):
            continue
        layer = layers.get(entity.layer_id)
        if visible_only and layer is not None and not bool(getattr(layer, "visible", True)):
            continue
        result.append(entity)
    return result


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
