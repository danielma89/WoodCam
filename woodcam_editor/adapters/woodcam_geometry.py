"""Adapter from ``VectorDocument`` entities to the existing WoodCAM contract."""

from __future__ import annotations

import math
from typing import Any, Iterable, Optional, Sequence


class GeometryAdapterError(ValueError):
    """Raised when editor geometry is not safe/complete enough for CAM."""


def _xy(value: Any) -> tuple[float, float]:
    if hasattr(value, "x") and hasattr(value, "y"):
        return float(value.x), float(value.y)
    return float(value[0]), float(value[1])


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _clean_points(points: Iterable[Any], *, closed: bool = False) -> list[tuple[float, float]]:
    clean: list[tuple[float, float]] = []
    for value in points:
        point = _xy(value)
        if not clean or _distance(clean[-1], point) > 1e-10:
            clean.append(point)
    if closed and len(clean) > 1 and _distance(clean[0], clean[-1]) <= 1e-8:
        clean.pop()
    return clean


def _arc_points(span: Any, deflection: float) -> list[tuple[float, float]]:
    start = _xy(span.start)
    end = _xy(span.end)
    center = _xy(span.center)
    radius = _distance(start, center)
    if radius <= 1e-12:
        return [start, end]
    start_angle = math.atan2(start[1] - center[1], start[0] - center[0])
    end_angle = math.atan2(end[1] - center[1], end[0] - center[0])
    clockwise = bool(getattr(span, "clockwise", False))
    sweep = (
        -((start_angle - end_angle) % (2.0 * math.pi))
        if clockwise
        else ((end_angle - start_angle) % (2.0 * math.pi))
    )
    if abs(sweep) <= 1e-12:
        sweep = -2.0 * math.pi if clockwise else 2.0 * math.pi
    ratio = max(0.0, min(1.0, float(deflection) / radius))
    max_angle = 2.0 * math.acos(max(-1.0, 1.0 - ratio)) if ratio > 0.0 else 0.0
    if max_angle <= 1e-6:
        max_angle = math.radians(2.0)
    segments = max(2, int(math.ceil(abs(sweep) / max_angle)))
    return [
        (
            center[0] + radius * math.cos(start_angle + sweep * index / segments),
            center[1] + radius * math.sin(start_angle + sweep * index / segments),
        )
        for index in range(segments + 1)
    ]


def _bezier_points(span: Any, deflection: float) -> list[tuple[float, float]]:
    start = _xy(span.start)
    control_1 = _xy(getattr(span, "control1", getattr(span, "control_1", span.start)))
    control_2 = _xy(getattr(span, "control2", getattr(span, "control_2", span.end)))
    end = _xy(span.end)
    control_length = (
        _distance(start, control_1)
        + _distance(control_1, control_2)
        + _distance(control_2, end)
    )
    segments = max(4, min(4096, int(math.ceil(control_length / max(deflection * 4.0, 0.05)))))
    points = []
    for index in range(segments + 1):
        t_value = index / segments
        inverse = 1.0 - t_value
        points.append(
            (
                inverse ** 3 * start[0]
                + 3.0 * inverse ** 2 * t_value * control_1[0]
                + 3.0 * inverse * t_value ** 2 * control_2[0]
                + t_value ** 3 * end[0],
                inverse ** 3 * start[1]
                + 3.0 * inverse ** 2 * t_value * control_1[1]
                + 3.0 * inverse * t_value ** 2 * control_2[1]
                + t_value ** 3 * end[1],
            )
        )
    return points


def _span_points(span: Any, deflection: float) -> list[tuple[float, float]]:
    flatten = getattr(span, "flatten", None)
    if callable(flatten):
        try:
            values = flatten(deflection)
        except TypeError:
            values = flatten(deflection=deflection)
        return _clean_points(values)

    class_name = type(span).__name__.lower()
    if "arc" in class_name or hasattr(span, "center"):
        return _arc_points(span, deflection)
    if "bezier" in class_name or hasattr(span, "control1") or hasattr(span, "control_1"):
        return _bezier_points(span, deflection)
    return [_xy(span.start), _xy(span.end)]


def path_points(entity: Any, deflection: float = 0.01) -> list[tuple[float, float]]:
    """Flatten a path only at the CAM boundary, never in the domain document."""

    points: list[tuple[float, float]] = []
    for span in list(getattr(entity, "spans", ()) or ()):
        span_points = _span_points(span, deflection)
        if points and span_points and _distance(points[-1], span_points[0]) <= 1e-8:
            span_points = span_points[1:]
        points.extend(span_points)
    return _clean_points(points, closed=bool(getattr(entity, "closed", False)))


def _ellipse_points(entity: Any, deflection: float) -> list[tuple[float, float]]:
    center = _xy(entity.center)
    radius_x = float(getattr(entity, "radius_x", getattr(entity, "rx", 0.0)))
    radius_y = float(getattr(entity, "radius_y", getattr(entity, "ry", 0.0)))
    rotation = float(getattr(entity, "rotation", 0.0) or 0.0)
    largest_radius = max(radius_x, radius_y)
    if largest_radius <= 0.0:
        return []
    ratio = max(0.0, min(1.0, deflection / largest_radius))
    max_angle = 2.0 * math.acos(max(-1.0, 1.0 - ratio)) if ratio else math.radians(2.0)
    segments = max(12, int(math.ceil(2.0 * math.pi / max(max_angle, 1e-6))))
    cosine = math.cos(rotation)
    sine = math.sin(rotation)
    points = []
    for index in range(segments):
        angle = 2.0 * math.pi * index / segments
        local_x = radius_x * math.cos(angle)
        local_y = radius_y * math.sin(angle)
        points.append(
            (
                center[0] + local_x * cosine - local_y * sine,
                center[1] + local_x * sine + local_y * cosine,
            )
        )
    return points


def circle_points(entity: Any, deflection: float = 0.01) -> list[tuple[float, float]]:
    center = _xy(entity.center)
    radius = float(entity.radius)
    if radius <= 0.0:
        return []
    ratio = max(0.0, min(1.0, deflection / radius))
    max_angle = 2.0 * math.acos(max(-1.0, 1.0 - ratio)) if ratio else math.radians(2.0)
    segments = max(12, int(math.ceil(2.0 * math.pi / max(max_angle, 1e-6))))
    return [
        (
            center[0] + radius * math.cos(2.0 * math.pi * index / segments),
            center[1] + radius * math.sin(2.0 * math.pi * index / segments),
        )
        for index in range(segments)
    ]


def entity_points(entity: Any, deflection: float = 0.01) -> list[tuple[float, float]]:
    class_name = type(entity).__name__.lower()
    if "path" in class_name or hasattr(entity, "spans"):
        return path_points(entity, deflection)
    if "ellipse" in class_name or hasattr(entity, "radius_x"):
        return _ellipse_points(entity, deflection)
    if "circle" in class_name or hasattr(entity, "radius"):
        return circle_points(entity, deflection)
    return []


def _transform_point(point: tuple[float, float], transform: Any) -> tuple[float, float]:
    if transform is None:
        return point
    for method_name in ("apply", "apply_to_point", "transform_point"):
        method = getattr(transform, method_name, None)
        if callable(method):
            try:
                return _xy(method(point))
            except (AttributeError, TypeError):
                try:
                    from woodcam_editor.domain.primitives import Vec2

                    return _xy(method(Vec2(*point)))
                except (ImportError, AttributeError, TypeError):
                    pass
    # Affine2D commonly exposes a,b,c,d,tx,ty.
    if all(hasattr(transform, name) for name in ("a", "b", "c", "d", "tx", "ty")):
        return (
            float(transform.a) * point[0] + float(transform.c) * point[1] + float(transform.tx),
            float(transform.b) * point[0] + float(transform.d) * point[1] + float(transform.ty),
        )
    return point


def _entity_kind(entity: Any) -> str:
    class_name = type(entity).__name__.lower()
    if "path" in class_name or hasattr(entity, "spans"):
        return "path"
    if "ellipse" in class_name or hasattr(entity, "radius_x"):
        return "ellipse"
    if "circle" in class_name or hasattr(entity, "radius"):
        return "circle"
    return "unsupported"


def _layers(document: Any) -> dict:
    layers = getattr(document, "layers_by_id", {}) or {}
    return layers if hasattr(layers, "get") else {}


def _is_cam_layer(document: Any, entity: Any) -> bool:
    layer = _layers(document).get(getattr(entity, "layer_id", None))
    if layer is None:
        return True
    if not bool(getattr(layer, "visible", True)):
        return False
    return str(getattr(layer, "purpose", "design") or "design") not in {
        "construction",
        "reference",
    }


def _hole_record(entity: Any, deflection: float, transform: Any = None) -> dict:
    center = _transform_point(_xy(entity.center), transform)
    radius = float(entity.radius)
    points = [_transform_point(point, transform) for point in circle_points(entity, deflection)]
    return {
        "x": center[0],
        "y": center[1],
        "diameter_mm": radius * 2.0,
        "depth_mm": 0.0,
        "points": points,
    }


def _append_entity(
    result: dict,
    entity: Any,
    *,
    deflection: float,
    transform: Any = None,
    circle_as_hole: bool = True,
    strict: bool = True,
) -> None:
    kind = _entity_kind(entity)
    if kind == "path" and not bool(getattr(entity, "closed", False)):
        if strict:
            raise GeometryAdapterError(
                f"O vetor {getattr(entity, 'id', '') or '<sem id>'} está aberto e não pode ir ao CAM."
            )
        return
    if kind == "circle" and circle_as_hole:
        result["holes"].append(_hole_record(entity, deflection, transform))
        return
    points = entity_points(entity, deflection)
    points = [_transform_point(point, transform) for point in points]
    if len(points) < 3:
        if strict:
            raise GeometryAdapterError(
                f"A entidade {getattr(entity, 'id', '') or '<sem id>'} não forma um contorno CAM válido."
            )
        return
    result["contours"].append(points)


def document_to_woodcam_geometry(
    vector_document: Any,
    *,
    entity_ids: Optional[Sequence[str]] = None,
    piece_ids: Optional[Sequence[str]] = None,
    deflection: float = 0.01,
    strict: bool = True,
) -> dict:
    """Return the existing ``{"contours": ..., "holes": ...}`` contract."""

    if deflection <= 0.0:
        raise ValueError("A deflexão CAM precisa ser maior que zero.")
    entities = getattr(vector_document, "entities_by_id", {}) or {}
    result = {"contours": [], "holes": []}

    if piece_ids is not None:
        pieces = getattr(vector_document, "pieces_by_id", {}) or {}
        for piece_id in piece_ids:
            if piece_id not in pieces:
                raise GeometryAdapterError(f"Peça 2D inexistente: {piece_id}")
            piece = pieces[piece_id]
            transform = getattr(piece, "placement", None)
            outer_id = getattr(piece, "outer_path_id")
            if outer_id not in entities:
                raise GeometryAdapterError(f"Contorno externo ausente na peça {piece_id}.")
            _append_entity(
                result,
                entities[outer_id],
                deflection=deflection,
                transform=transform,
                circle_as_hole=False,
                strict=strict,
            )
            for inner_id in list(getattr(piece, "inner_path_ids", ()) or ()):
                if inner_id not in entities:
                    raise GeometryAdapterError(f"Contorno interno ausente na peça {piece_id}: {inner_id}")
                inner = entities[inner_id]
                _append_entity(
                    result,
                    inner,
                    deflection=deflection,
                    transform=transform,
                    circle_as_hole=_entity_kind(inner) == "circle",
                    strict=strict,
                )
        return result

    selected_ids = list(entity_ids) if entity_ids is not None else list(entities)
    for entity_id in selected_ids:
        if entity_id not in entities:
            raise GeometryAdapterError(f"Entidade 2D inexistente: {entity_id}")
        entity = entities[entity_id]
        if not _is_cam_layer(vector_document, entity):
            continue
        _append_entity(
            result,
            entity,
            deflection=deflection,
            circle_as_hole=_entity_kind(entity) == "circle",
            strict=strict,
        )
    return result


class EditorDocumentGeometryProvider:
    """Explicit provider consumed by the existing WoodCAM pipeline."""

    def __init__(
        self,
        vector_document: Any,
        *,
        entity_ids: Optional[Sequence[str]] = None,
        piece_ids: Optional[Sequence[str]] = None,
        deflection: float = 0.01,
    ):
        self.vector_document = vector_document
        self.entity_ids = tuple(entity_ids) if entity_ids is not None else None
        self.piece_ids = tuple(piece_ids) if piece_ids is not None else None
        self.deflection = float(deflection)

    def get_geometry(self) -> dict:
        return document_to_woodcam_geometry(
            self.vector_document,
            entity_ids=self.entity_ids,
            piece_ids=self.piece_ids,
            deflection=self.deflection,
        )

    def describe_source(self) -> str:
        if self.piece_ids is not None:
            return f"Editor 2D — {len(self.piece_ids)} peça(s)"
        if self.entity_ids is not None:
            return f"Editor 2D — {len(self.entity_ids)} entidade(s)"
        return "Editor 2D — documento vetorial"

    def revision_token(self) -> str:
        try:
            from woodcam_editor.domain.serialization import document_checksum

            checksum = document_checksum(self.vector_document)
        except Exception:
            checksum = ""
        return "{}:{}:{}".format(
            getattr(self.vector_document, "document_uuid", ""),
            int(getattr(self.vector_document, "revision", 0)),
            checksum,
        )
