"""Dependency-free SVG importer for the Editor 2D domain.

Supported path commands: M/L/H/V/C/Q/A/Z (absolute and relative), plus
line/rect/circle/ellipse/polyline/polygon, nested transforms and common units.
SVG's Y-down viewport is converted to the domain's Cartesian Y-up system.
"""

from __future__ import annotations

import hashlib
import math
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from woodcam_editor.domain.primitives import Affine2D, Vec2
from woodcam_editor.importers.part_shape import (
    ImportIssue,
    ImportLayerDescriptor,
    ImportResult,
    _arc_span,
    _bezier_span,
    _circle_entity,
    _domain_api,
    _ellipse_entity,
    _line_span,
    _path_entity,
)


PX_TO_MM = 25.4 / 96.0
NUMBER = r"[-+]?(?:\d+\.?(?:\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
TOKEN_RE = re.compile(r"[MmLlHhVvCcQqAaZz]|" + NUMBER)
TRANSFORM_RE = re.compile(r"([A-Za-z]+)\s*\(([^)]*)\)")
LENGTH_RE = re.compile(r"^\s*(" + NUMBER + r")\s*([A-Za-z%]*)\s*$")


def _length_mm(value: Optional[str], *, default_scale: float = PX_TO_MM) -> Optional[float]:
    if value is None or str(value).strip() == "":
        return None
    match = LENGTH_RE.match(str(value))
    if not match:
        raise ValueError(f"Comprimento SVG inválido: {value!r}")
    number = float(match.group(1))
    unit = match.group(2).lower()
    factors = {
        "": default_scale,
        "px": PX_TO_MM,
        "mm": 1.0,
        "cm": 10.0,
        "q": 0.25,
        "in": 25.4,
        "pt": 25.4 / 72.0,
        "pc": 25.4 / 6.0,
    }
    if unit == "%":
        raise ValueError("Comprimento percentual exige viewport explícito.")
    if unit not in factors:
        raise ValueError(f"Unidade SVG não suportada: {unit}")
    return number * factors[unit]


def _numbers(value: str) -> list[float]:
    return [float(item) for item in re.findall(NUMBER, value or "")]


def _parse_transform(value: Optional[str]) -> Affine2D:
    result = Affine2D.identity()
    for name, arguments in TRANSFORM_RE.findall(value or ""):
        values = _numbers(arguments)
        lowered = name.lower()
        if lowered == "matrix" and len(values) == 6:
            transform = Affine2D(*values)
        elif lowered == "translate" and len(values) in (1, 2):
            transform = Affine2D.translation(values[0], values[1] if len(values) == 2 else 0.0)
        elif lowered == "scale" and len(values) in (1, 2):
            transform = Affine2D.scaling(values[0], values[1] if len(values) == 2 else values[0])
        elif lowered == "rotate" and len(values) in (1, 3):
            origin = Vec2(values[1], values[2]) if len(values) == 3 else None
            transform = Affine2D.rotation(math.radians(values[0]), origin)
        elif lowered == "skewx" and len(values) == 1:
            transform = Affine2D(c=math.tan(math.radians(values[0])))
        elif lowered == "skewy" and len(values) == 1:
            transform = Affine2D(b=math.tan(math.radians(values[0])))
        else:
            raise ValueError(f"Transform SVG inválido/não suportado: {name}({arguments})")
        # SVG lists compose as matrices from left to right: T S -> T @ S.
        result = result @ transform
    return result


def _root_transform(root: ET.Element) -> tuple[Affine2D, float, float]:
    view_box_values = _numbers(root.get("viewBox", ""))
    if view_box_values and len(view_box_values) != 4:
        raise ValueError("viewBox SVG precisa de minX minY largura altura.")
    width_mm = _length_mm(root.get("width"))
    height_mm = _length_mm(root.get("height"))
    if view_box_values:
        min_x, min_y, view_width, view_height = view_box_values
        if view_width <= 0.0 or view_height <= 0.0:
            raise ValueError("viewBox SVG precisa de dimensões positivas.")
        width_mm = width_mm if width_mm is not None else view_width * PX_TO_MM
        height_mm = height_mm if height_mm is not None else view_height * PX_TO_MM
        scale_x = width_mm / view_width
        scale_y = height_mm / view_height
        transform = (
            Affine2D.translation(0.0, height_mm)
            @ Affine2D.scaling(scale_x, -scale_y)
            @ Affine2D.translation(-min_x, -min_y)
        )
        return transform, width_mm, height_mm
    width_mm = width_mm or 0.0
    height_mm = height_mm or 0.0
    return Affine2D(PX_TO_MM, 0.0, 0.0, -PX_TO_MM, 0.0, height_mm), width_mm, height_mm


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1].lower()


def _hidden(element: ET.Element) -> bool:
    style = str(element.get("style", "") or "").replace(" ", "").lower()
    return (
        str(element.get("display", "")).lower() == "none"
        or str(element.get("visibility", "")).lower() == "hidden"
        or "display:none" in style
        or "visibility:hidden" in style
    )


def _style_values(element: ET.Element) -> dict[str, str]:
    result = {}
    for declaration in str(element.get("style", "") or "").split(";"):
        if ":" not in declaration:
            continue
        name, value = declaration.split(":", 1)
        result[name.strip().lower()] = value.strip()
    return result


def _svg_color(element: ET.Element, inherited: Optional[str]) -> Optional[str]:
    style = _style_values(element)
    value = style.get("stroke", element.get("stroke"))
    if value is None or str(value).strip() == "":
        return inherited
    value = str(value).strip()
    if value.lower() == "currentcolor":
        value = str(style.get("color", element.get("color", inherited or "")) or "")
    if value.lower() in {"", "none", "transparent", "inherit"}:
        return inherited
    if re.fullmatch(r"#[0-9a-fA-F]{3}", value):
        value = "#" + "".join(character * 2 for character in value[1:])
    return value.lower() if value.startswith("#") else value


def _svg_layer_purpose(element: ET.Element, inherited: str = "design") -> str:
    value = str(element.get("data-layer-purpose", "") or "").strip().lower()
    return value or inherited or "design"


def _metadata(
    element: ET.Element,
    source_layer_key: str,
    source_layer_name: str,
    source_color: Optional[str],
) -> dict:
    metadata = {
        "source_format": "svg",
        "source_layer_key": source_layer_key,
        "svg_layer_name": source_layer_name,
        "svg_id": str(element.get("id", "") or ""),
        "svg_class": str(element.get("class", "") or ""),
        "svg_tag": _tag(element),
    }
    if source_color:
        metadata["svg_stroke"] = source_color
    return metadata


def _point(transform: Affine2D, x_value: float, y_value: float) -> Vec2:
    return transform.apply_to_point(Vec2(x_value, y_value))


def _matrix_is_axis_ellipse(transform: Affine2D, tolerance: float = 1e-9) -> bool:
    first = Vec2(transform.a, transform.b)
    second = Vec2(transform.c, transform.d)
    return abs(first.dot(second)) <= tolerance * max(1.0, first.length() * second.length())


def _svg_arc_center(
    start: Vec2,
    end: Vec2,
    radius_x: float,
    radius_y: float,
    rotation_degrees: float,
    large_arc: bool,
    sweep: bool,
):
    radius_x = abs(float(radius_x))
    radius_y = abs(float(radius_y))
    if radius_x <= 1e-15 or radius_y <= 1e-15 or start.almost_equals(end, 1e-15):
        return None
    phi = math.radians(rotation_degrees % 360.0)
    cosine = math.cos(phi)
    sine = math.sin(phi)
    dx = (start.x - end.x) * 0.5
    dy = (start.y - end.y) * 0.5
    x_prime = cosine * dx + sine * dy
    y_prime = -sine * dx + cosine * dy
    scale = (x_prime * x_prime) / (radius_x * radius_x) + (y_prime * y_prime) / (radius_y * radius_y)
    if scale > 1.0:
        scale_root = math.sqrt(scale)
        radius_x *= scale_root
        radius_y *= scale_root
    numerator = max(
        0.0,
        radius_x * radius_x * radius_y * radius_y
        - radius_x * radius_x * y_prime * y_prime
        - radius_y * radius_y * x_prime * x_prime,
    )
    denominator = radius_x * radius_x * y_prime * y_prime + radius_y * radius_y * x_prime * x_prime
    coefficient = 0.0 if denominator <= 1e-30 else math.sqrt(numerator / denominator)
    if large_arc == sweep:
        coefficient = -coefficient
    center_prime_x = coefficient * radius_x * y_prime / radius_y
    center_prime_y = -coefficient * radius_y * x_prime / radius_x
    center = Vec2(
        cosine * center_prime_x - sine * center_prime_y + (start.x + end.x) * 0.5,
        sine * center_prime_x + cosine * center_prime_y + (start.y + end.y) * 0.5,
    )

    def vector_angle(first: Vec2, second: Vec2) -> float:
        return math.atan2(first.cross(second), first.dot(second))

    first_vector = Vec2((x_prime - center_prime_x) / radius_x, (y_prime - center_prime_y) / radius_y)
    second_vector = Vec2((-x_prime - center_prime_x) / radius_x, (-y_prime - center_prime_y) / radius_y)
    start_angle = vector_angle(Vec2(1.0, 0.0), first_vector)
    delta = vector_angle(first_vector, second_vector)
    if not sweep and delta > 0.0:
        delta -= math.tau
    elif sweep and delta < 0.0:
        delta += math.tau
    return center, radius_x, radius_y, phi, start_angle, delta


def _ellipse_point(center: Vec2, rx: float, ry: float, phi: float, angle: float) -> Vec2:
    cosine = math.cos(phi)
    sine = math.sin(phi)
    local_x = rx * math.cos(angle)
    local_y = ry * math.sin(angle)
    return Vec2(
        center.x + cosine * local_x - sine * local_y,
        center.y + sine * local_x + cosine * local_y,
    )


def _ellipse_derivative(rx: float, ry: float, phi: float, angle: float) -> Vec2:
    cosine = math.cos(phi)
    sine = math.sin(phi)
    local_x = -rx * math.sin(angle)
    local_y = ry * math.cos(angle)
    return Vec2(cosine * local_x - sine * local_y, sine * local_x + cosine * local_y)


def _arc_to_spans(
    api: Mapping[str, Any],
    start: Vec2,
    end: Vec2,
    rx: float,
    ry: float,
    rotation: float,
    large_arc: bool,
    sweep: bool,
    transform: Affine2D,
):
    if start.almost_equals(end, 1e-15):
        return []
    if abs(rx) <= 1e-15 or abs(ry) <= 1e-15:
        return [_line_span(api, transform.apply_to_point(start), transform.apply_to_point(end))]
    parameters = _svg_arc_center(start, end, rx, ry, rotation, large_arc, sweep)
    if parameters is None:
        return [_line_span(api, transform.apply_to_point(start), transform.apply_to_point(end))]
    center, rx, ry, phi, start_angle, delta = parameters
    if abs(rx - ry) <= max(rx, ry) * 1e-10 and transform.is_similarity():
        transformed_start = transform.apply_to_point(start)
        transformed_end = transform.apply_to_point(end)
        transformed_center = transform.apply_to_point(center)
        clockwise = delta < 0.0
        if transform.determinant < 0.0:
            clockwise = not clockwise
        return [
            _arc_span(
                api,
                transformed_start,
                transformed_end,
                transformed_center,
                clockwise,
            )
        ]

    segment_count = max(1, int(math.ceil(abs(delta) / (math.pi * 0.5))))
    spans = []
    for index in range(segment_count):
        angle_0 = start_angle + delta * index / segment_count
        angle_1 = start_angle + delta * (index + 1) / segment_count
        step = angle_1 - angle_0
        alpha = 4.0 / 3.0 * math.tan(step * 0.25)
        point_0 = _ellipse_point(center, rx, ry, phi, angle_0)
        point_1 = _ellipse_point(center, rx, ry, phi, angle_1)
        control_0 = point_0 + _ellipse_derivative(rx, ry, phi, angle_0) * alpha
        control_1 = point_1 - _ellipse_derivative(rx, ry, phi, angle_1) * alpha
        spans.append(
            _bezier_span(
                api,
                transform.apply_to_point(point_0),
                transform.apply_to_point(control_0),
                transform.apply_to_point(control_1),
                transform.apply_to_point(point_1),
            )
        )
    return spans


def _parse_path(data: str, api: Mapping[str, Any], transform: Affine2D):
    tokens = TOKEN_RE.findall(data or "")
    index = 0
    command = None
    current = Vec2(0.0, 0.0)
    sub_start = current
    spans = []
    subpaths = []

    def number() -> float:
        nonlocal index
        if index >= len(tokens) or tokens[index].isalpha():
            raise ValueError("Comando SVG sem parâmetros suficientes.")
        value = float(tokens[index])
        index += 1
        return value

    def endpoint(x_value: float, y_value: float, relative: bool) -> Vec2:
        value = Vec2(x_value, y_value)
        return current + value if relative else value

    def finish(closed: bool = False):
        nonlocal spans
        if spans:
            subpaths.append((tuple(spans), bool(closed)))
            spans = []

    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
            if command in "Zz":
                if not current.almost_equals(sub_start, 1e-12):
                    spans.append(
                        _line_span(
                            api,
                            transform.apply_to_point(current),
                            transform.apply_to_point(sub_start),
                        )
                    )
                current = sub_start
                finish(True)
                command = None
                continue
        if command is None:
            raise ValueError("Path SVG inicia sem comando.")
        relative = command.islower()
        upper = command.upper()
        if upper == "M":
            target = endpoint(number(), number(), relative)
            finish(False)
            current = target
            sub_start = target
            command = "l" if relative else "L"
        elif upper == "L":
            target = endpoint(number(), number(), relative)
            spans.append(_line_span(api, transform.apply_to_point(current), transform.apply_to_point(target)))
            current = target
        elif upper == "H":
            x_value = number() + (current.x if relative else 0.0)
            target = Vec2(x_value, current.y)
            spans.append(_line_span(api, transform.apply_to_point(current), transform.apply_to_point(target)))
            current = target
        elif upper == "V":
            y_value = number() + (current.y if relative else 0.0)
            target = Vec2(current.x, y_value)
            spans.append(_line_span(api, transform.apply_to_point(current), transform.apply_to_point(target)))
            current = target
        elif upper == "C":
            control_1 = endpoint(number(), number(), relative)
            control_2 = endpoint(number(), number(), relative)
            target = endpoint(number(), number(), relative)
            spans.append(
                _bezier_span(
                    api,
                    transform.apply_to_point(current),
                    transform.apply_to_point(control_1),
                    transform.apply_to_point(control_2),
                    transform.apply_to_point(target),
                )
            )
            current = target
        elif upper == "Q":
            control = endpoint(number(), number(), relative)
            target = endpoint(number(), number(), relative)
            cubic_1 = current + (control - current) * (2.0 / 3.0)
            cubic_2 = target + (control - target) * (2.0 / 3.0)
            spans.append(
                _bezier_span(
                    api,
                    transform.apply_to_point(current),
                    transform.apply_to_point(cubic_1),
                    transform.apply_to_point(cubic_2),
                    transform.apply_to_point(target),
                )
            )
            current = target
        elif upper == "A":
            rx, ry, rotation = number(), number(), number()
            large_arc, sweep = bool(int(number())), bool(int(number()))
            target = endpoint(number(), number(), relative)
            spans.extend(
                _arc_to_spans(api, current, target, rx, ry, rotation, large_arc, sweep, transform)
            )
            current = target
        else:
            raise ValueError(f"Comando SVG não suportado: {command}")
    finish(False)
    return subpaths


def _poly_points(value: str) -> list[Vec2]:
    values = _numbers(value)
    if len(values) % 2:
        raise ValueError("Lista points SVG precisa de pares X/Y.")
    return [Vec2(values[index], values[index + 1]) for index in range(0, len(values), 2)]


def _path_from_points(
    api: Mapping[str, Any],
    points: Iterable[Vec2],
    transform: Affine2D,
    layer_id: str,
    closed: bool,
    metadata: Mapping[str, Any],
):
    points = [transform.apply_to_point(point) for point in points]
    if closed and points and points[0].almost_equals(points[-1], 1e-12):
        points.pop()
    spans = [_line_span(api, points[index], points[index + 1]) for index in range(len(points) - 1)]
    if closed and len(points) >= 3:
        spans.append(_line_span(api, points[-1], points[0]))
    return _path_entity(api, layer_id, spans, closed, metadata) if spans else None


def import_svg(path: str | Path, *, layer_id: str) -> ImportResult:
    source_path = Path(path)
    root = ET.parse(str(source_path)).getroot()
    api = _domain_api()
    root_matrix, width_mm, height_mm = _root_transform(root)
    entities = []
    issues = []
    layers: dict[str, ImportLayerDescriptor] = {}

    def register_layer(
        source_key: str,
        name: str,
        color: Optional[str],
        purpose: str,
        metadata: Optional[Mapping[str, Any]] = None,
    ) -> ImportLayerDescriptor:
        source_key = str(source_key or "svg:default")
        current = layers.get(source_key)
        if current is not None:
            # First declaration owns identity/order; later declarations may
            # fill information that was unavailable on the opening group.
            if current.color is None and color:
                current = ImportLayerDescriptor(
                    source_key=current.source_key,
                    name=current.name,
                    color=color,
                    purpose=current.purpose,
                    metadata=current.metadata,
                )
                layers[source_key] = current
            return current
        descriptor = ImportLayerDescriptor(
            source_key=source_key,
            name=str(name or source_key),
            color=color,
            purpose=purpose or "design",
            metadata=dict(metadata or {}),
        )
        layers[source_key] = descriptor
        return descriptor

    def visit(
        element: ET.Element,
        parent_matrix: Affine2D,
        parent_layer_key: Optional[str] = None,
        parent_color: Optional[str] = None,
        parent_purpose: str = "design",
        trail: tuple[int, ...] = (),
    ):
        if _hidden(element):
            return
        tag = _tag(element)
        try:
            matrix = parent_matrix @ _parse_transform(element.get("transform"))
        except Exception as error:
            issues.append(ImportIssue(-1, tag, f"Transform ignorado: {error}"))
            matrix = parent_matrix
        color = _svg_color(element, parent_color)
        purpose = _svg_layer_purpose(element, parent_purpose)
        source_layer_key = parent_layer_key

        if tag == "g":
            explicit_name = str(element.get("data-layer-name", "") or "").strip()
            explicit_id = str(element.get("id", "") or "").strip()
            has_own_stroke = (
                element.get("stroke") is not None
                or "stroke" in _style_values(element)
            )
            if explicit_name or explicit_id or (has_own_stroke and color != parent_color):
                source_layer_key = (
                    explicit_id
                    or explicit_name
                    or "svg-group:" + ".".join(str(index) for index in trail)
                )
                parent_descriptor = layers.get(parent_layer_key or "")
                layer_name = (
                    explicit_name
                    or explicit_id
                    or (parent_descriptor.name if parent_descriptor is not None else "Grupo SVG")
                )
                register_layer(
                    source_layer_key,
                    layer_name,
                    color,
                    purpose,
                    {
                        "svg_group_id": explicit_id,
                        "svg_data_layer_name": explicit_name,
                    },
                )

        geometry_tags = {
            "path",
            "line",
            "polyline",
            "polygon",
            "rect",
            "circle",
            "ellipse",
        }
        if tag in geometry_tags and source_layer_key is None:
            source_layer_key = "svg:default"
            register_layer(
                source_layer_key,
                str(root.get("data-layer-name", "") or root.get("id", "") or source_path.stem or "Desenho"),
                color,
                purpose,
                {"svg_group_id": "", "svg_data_layer_name": ""},
            )
        descriptor = layers.get(source_layer_key or "")
        source_layer_name = descriptor.name if descriptor is not None else str(source_layer_key or "Desenho")
        metadata = _metadata(
            element,
            str(source_layer_key or "svg:default"),
            source_layer_name,
            color,
        )
        try:
            if tag == "path":
                for spans, closed in _parse_path(element.get("d", ""), api, matrix):
                    entities.append(_path_entity(api, layer_id, spans, closed, metadata))
            elif tag == "line":
                points = [
                    Vec2(float(element.get("x1", 0.0)), float(element.get("y1", 0.0))),
                    Vec2(float(element.get("x2", 0.0)), float(element.get("y2", 0.0))),
                ]
                entity = _path_from_points(api, points, matrix, layer_id, False, metadata)
                if entity:
                    entities.append(entity)
            elif tag in {"polyline", "polygon"}:
                entity = _path_from_points(
                    api,
                    _poly_points(element.get("points", "")),
                    matrix,
                    layer_id,
                    tag == "polygon",
                    metadata,
                )
                if entity:
                    entities.append(entity)
            elif tag == "rect":
                x_value = float(element.get("x", 0.0))
                y_value = float(element.get("y", 0.0))
                width = float(element.get("width", 0.0))
                height = float(element.get("height", 0.0))
                if width <= 0.0 or height <= 0.0:
                    raise ValueError("Retângulo SVG precisa de largura/altura positivas.")
                rx = float(element.get("rx", 0.0) or 0.0)
                ry = float(element.get("ry", rx) or rx)
                if rx > 0.0 or ry > 0.0:
                    rx = min(rx or ry, width * 0.5)
                    ry = min(ry or rx, height * 0.5)
                    data = (
                        f"M{x_value + rx},{y_value} H{x_value + width - rx} "
                        f"A{rx},{ry} 0 0 1 {x_value + width},{y_value + ry} "
                        f"V{y_value + height - ry} A{rx},{ry} 0 0 1 {x_value + width - rx},{y_value + height} "
                        f"H{x_value + rx} A{rx},{ry} 0 0 1 {x_value},{y_value + height - ry} "
                        f"V{y_value + ry} A{rx},{ry} 0 0 1 {x_value + rx},{y_value} Z"
                    )
                    for spans, closed in _parse_path(data, api, matrix):
                        entities.append(_path_entity(api, layer_id, spans, closed, metadata))
                else:
                    entity = _path_from_points(
                        api,
                        [
                            Vec2(x_value, y_value),
                            Vec2(x_value + width, y_value),
                            Vec2(x_value + width, y_value + height),
                            Vec2(x_value, y_value + height),
                        ],
                        matrix,
                        layer_id,
                        True,
                        metadata,
                    )
                    entities.append(entity)
            elif tag in {"circle", "ellipse"}:
                center = Vec2(float(element.get("cx", 0.0)), float(element.get("cy", 0.0)))
                radius_x = float(element.get("r", element.get("rx", 0.0)))
                radius_y = float(element.get("r", element.get("ry", 0.0)))
                if radius_x <= 0.0 or radius_y <= 0.0:
                    raise ValueError("Círculo/elipse SVG precisa de raio positivo.")
                transformed_center = matrix.apply_to_point(center)
                basis_x = matrix.apply_to_vector(Vec2(radius_x, 0.0))
                basis_y = matrix.apply_to_vector(Vec2(0.0, radius_y))
                if _matrix_is_axis_ellipse(matrix):
                    transformed_rx = basis_x.length()
                    transformed_ry = basis_y.length()
                    if abs(transformed_rx - transformed_ry) <= max(transformed_rx, transformed_ry) * 1e-10:
                        entities.append(
                            _circle_entity(api, layer_id, transformed_center, transformed_rx, metadata)
                        )
                    else:
                        entities.append(
                            _ellipse_entity(
                                api,
                                layer_id,
                                transformed_center,
                                transformed_rx,
                                transformed_ry,
                                basis_x.angle(),
                                metadata,
                            )
                        )
                else:
                    # General affine ellipse (with shear) is represented by an
                    # accurate four-cubic path; no shape is silently discarded.
                    kappa = 4.0 * (math.sqrt(2.0) - 1.0) / 3.0
                    raw = [
                        (Vec2(radius_x, 0), Vec2(radius_x, kappa * radius_y), Vec2(kappa * radius_x, radius_y), Vec2(0, radius_y)),
                        (Vec2(0, radius_y), Vec2(-kappa * radius_x, radius_y), Vec2(-radius_x, kappa * radius_y), Vec2(-radius_x, 0)),
                        (Vec2(-radius_x, 0), Vec2(-radius_x, -kappa * radius_y), Vec2(-kappa * radius_x, -radius_y), Vec2(0, -radius_y)),
                        (Vec2(0, -radius_y), Vec2(kappa * radius_x, -radius_y), Vec2(radius_x, -kappa * radius_y), Vec2(radius_x, 0)),
                    ]
                    spans = []
                    for start, c1, c2, end in raw:
                        spans.append(
                            _bezier_span(
                                api,
                                matrix.apply_to_point(center + start),
                                matrix.apply_to_point(center + c1),
                                matrix.apply_to_point(center + c2),
                                matrix.apply_to_point(center + end),
                            )
                        )
                    entities.append(_path_entity(api, layer_id, spans, True, metadata))
                    issues.append(ImportIssue(-1, tag, "Elipse com cisalhamento convertida em Béziers cúbicas.", "info"))
            elif tag not in {"svg", "g", "defs", "metadata", "title", "desc"}:
                issues.append(ImportIssue(-1, tag, f"Elemento SVG não suportado: {tag}."))
        except Exception as error:
            issues.append(ImportIssue(-1, tag, f"Falha ao importar {tag}: {error}"))

        if tag not in {"defs"}:
            for child_index, child in enumerate(list(element)):
                visit(
                    child,
                    matrix,
                    source_layer_key,
                    color,
                    purpose,
                    trail + (child_index,),
                )

    visit(root, root_matrix)
    file_bytes = source_path.read_bytes()
    return ImportResult(
        tuple(entity for entity in entities if entity is not None),
        tuple(issues),
        {
            "source_kind": "svg",
            "source_path": str(source_path),
            "source_fingerprint": hashlib.sha256(file_bytes).hexdigest(),
            "viewport_width_mm": width_mm,
            "viewport_height_mm": height_mm,
        },
        layers,
    )
