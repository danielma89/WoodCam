"""Exact-copy importer from FreeCAD Part/OCC shapes into domain entities."""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class ImportIssue:
    index: int
    geometry_type: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class ImportLayerDescriptor:
    """Source-layer metadata captured before document-specific IDs exist."""

    source_key: str
    name: str
    color: Optional[str] = None
    purpose: str = "design"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_key = str(self.source_key or "").strip()
        name = str(self.name or source_key or "Desenho").strip()
        if not source_key:
            raise ValueError("ImportLayerDescriptor exige uma chave de camada fonte.")
        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "color", str(self.color) if self.color else None)
        object.__setattr__(self, "purpose", str(self.purpose or "design"))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict:
        return {
            "source_key": self.source_key,
            "name": self.name,
            "color": self.color,
            "purpose": self.purpose,
            "metadata": dict(self.metadata),
        }


def _import_layer_descriptor(source_key: str, value: Any) -> ImportLayerDescriptor:
    if isinstance(value, ImportLayerDescriptor):
        return value if value.source_key == source_key else replace(value, source_key=source_key)
    if isinstance(value, Mapping):
        return ImportLayerDescriptor(
            source_key=source_key,
            name=str(value.get("name", source_key) or source_key),
            color=value.get("color"),
            purpose=str(value.get("purpose", "design") or "design"),
            metadata=value.get("metadata", {}) or {},
        )
    return ImportLayerDescriptor(source_key=source_key, name=str(value or source_key))


@dataclass(frozen=True)
class ImportResult:
    entities: tuple[Any, ...]
    issues: tuple[ImportIssue, ...] = ()
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    layers: Mapping[str, ImportLayerDescriptor] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entities", tuple(self.entities or ()))
        object.__setattr__(self, "issues", tuple(self.issues or ()))
        object.__setattr__(self, "source_metadata", dict(self.source_metadata or {}))
        object.__setattr__(
            self,
            "layers",
            {
                str(source_key): _import_layer_descriptor(str(source_key), descriptor)
                for source_key, descriptor in dict(self.layers or {}).items()
            },
        )

    @property
    def source_layers(self) -> Mapping[str, ImportLayerDescriptor]:
        """Explicit alias for callers that prefer the longer, unambiguous name."""

        return self.layers


def _domain_api():
    from woodcam_editor.domain.primitives import Vec2, new_id
    from woodcam_editor.domain.spans import ArcSpan, CubicBezierSpan, LineSpan
    from woodcam_editor.domain.entities import (
        CircleEntity,
        EllipseEntity,
        PathEntity,
    )

    return {
        "Vec2": Vec2,
        "new_id": new_id,
        "LineSpan": LineSpan,
        "ArcSpan": ArcSpan,
        "CubicBezierSpan": CubicBezierSpan,
        "PathEntity": PathEntity,
        "CircleEntity": CircleEntity,
        "EllipseEntity": EllipseEntity,
    }


def _construct(cls: type, values: Mapping[str, Any]):
    """Construct domain dataclasses while tolerating harmless API additions."""

    parameters = inspect.signature(cls).parameters
    kwargs = {name: values[name] for name in parameters if name in values}
    return cls(**kwargs)


def _new_id(api: Mapping[str, Any], prefix: str) -> str:
    return str(api["new_id"](prefix))


def _vec2(api: Mapping[str, Any], value: Any):
    if hasattr(value, "x") and hasattr(value, "y"):
        return api["Vec2"](float(value.x), float(value.y))
    return api["Vec2"](float(value[0]), float(value[1]))


def _point3(value: Any) -> tuple[float, float, float]:
    if hasattr(value, "x") and hasattr(value, "y"):
        return (float(value.x), float(value.y), float(getattr(value, "z", 0.0)))
    return (
        float(value[0]),
        float(value[1]),
        float(value[2]) if len(value) > 2 else 0.0,
    )


def _transform_point(value: Any, placement: Any = None) -> tuple[float, float, float]:
    if placement is None:
        return _point3(value)
    try:
        import FreeCAD  # type: ignore

        source = value if hasattr(value, "x") else FreeCAD.Vector(*_point3(value))
        return _point3(placement.multVec(source))
    except Exception:
        return _point3(value)


def _span_values(api: Mapping[str, Any], start: Any, end: Any) -> dict:
    return {
        "id": _new_id(api, "span"),
        "span_id": _new_id(api, "span"),
        "start_node_id": _new_id(api, "node"),
        "end_node_id": _new_id(api, "node"),
        "start": start,
        "end": end,
        "metadata": {},
    }


def _line_span(api: Mapping[str, Any], start: Any, end: Any):
    return _construct(api["LineSpan"], _span_values(api, start, end))


def _arc_span(api: Mapping[str, Any], start: Any, end: Any, center: Any, clockwise: bool):
    values = _span_values(api, start, end)
    values.update(center=center, clockwise=bool(clockwise))
    return _construct(api["ArcSpan"], values)


def _bezier_span(
    api: Mapping[str, Any],
    start: Any,
    control_1: Any,
    control_2: Any,
    end: Any,
):
    values = _span_values(api, start, end)
    values.update(
        control1=control_1,
        control2=control_2,
        control_1=control_1,
        control_2=control_2,
    )
    return _construct(api["CubicBezierSpan"], values)


def _link_node_ids(spans: Iterable[Any], closed: bool) -> tuple[Any, ...]:
    """Share logical endpoint IDs when the domain exposes node ID fields."""

    linked = list(spans)
    for index in range(1, len(linked)):
        previous_id = getattr(linked[index - 1], "end_node_id", None)
        if previous_id is not None and hasattr(linked[index], "start_node_id"):
            try:
                linked[index] = replace(linked[index], start_node_id=previous_id)
            except (TypeError, ValueError):
                pass
    if closed and len(linked) > 1:
        first_id = getattr(linked[0], "start_node_id", None)
        if first_id is not None and hasattr(linked[-1], "end_node_id"):
            try:
                linked[-1] = replace(linked[-1], end_node_id=first_id)
            except (TypeError, ValueError):
                pass
    return tuple(linked)


def _orient_connected_spans(
    spans: Iterable[Any],
    closed: bool,
    tolerance: float = 1.0e-9,
) -> tuple[Any, ...]:
    """Orient an OCC wire copy without changing any geometric point.

    ``Wire.OrderedEdges`` guarantees traversal order, but individual edge
    orientations are not guaranteed to agree with that traversal.  Try both
    directions of every possible first span and accept only a chain whose
    existing endpoints already coincide.  No gap is healed here.
    """

    source = tuple(spans)
    if len(source) < 2:
        return source
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")

    def connected(left: Any, right: Any) -> bool:
        return bool(left.end.almost_equals(right.start, tolerance))

    for first_index in range(len(source)):
        for reverse_first in (False, True):
            first = source[first_index].reversed() if reverse_first else source[first_index]
            chain = [first]
            remaining = [
                (index, span)
                for index, span in enumerate(source)
                if index != first_index
            ]
            while remaining:
                match = None
                for position, (source_index, span) in enumerate(remaining):
                    if connected(chain[-1], span):
                        match = (position, source_index, span)
                        break
                    reversed_span = span.reversed()
                    if connected(chain[-1], reversed_span):
                        match = (position, source_index, reversed_span)
                        break
                if match is None:
                    break
                position, _source_index, oriented = match
                remaining.pop(position)
                chain.append(oriented)
            if remaining:
                continue
            if closed and not chain[-1].end.almost_equals(chain[0].start, tolerance):
                continue
            if not closed and chain[-1].end.almost_equals(chain[0].start, tolerance):
                continue
            return tuple(chain)
    raise ValueError(
        "As arestas do wire não formam uma cadeia contínua sem corrigir geometria."
    )


def _path_entity(
    api: Mapping[str, Any],
    layer_id: str,
    spans: Iterable[Any],
    closed: bool,
    metadata: Optional[Mapping[str, Any]] = None,
):
    spans = _orient_connected_spans(spans, closed)
    spans = _link_node_ids(spans, closed)
    values = {
        "id": _new_id(api, "path"),
        "entity_id": _new_id(api, "path"),
        "layer_id": layer_id,
        "spans": spans,
        "closed": bool(closed),
        "metadata": dict(metadata or {}),
    }
    return _construct(api["PathEntity"], values)


def _circle_entity(
    api: Mapping[str, Any],
    layer_id: str,
    center: Any,
    radius: float,
    metadata: Optional[Mapping[str, Any]] = None,
):
    values = {
        "id": _new_id(api, "circle"),
        "entity_id": _new_id(api, "circle"),
        "layer_id": layer_id,
        "center": center,
        "radius": float(radius),
        "metadata": dict(metadata or {}),
    }
    return _construct(api["CircleEntity"], values)


def _ellipse_entity(
    api: Mapping[str, Any],
    layer_id: str,
    center: Any,
    radius_x: float,
    radius_y: float,
    rotation: float,
    metadata: Optional[Mapping[str, Any]] = None,
):
    values = {
        "id": _new_id(api, "ellipse"),
        "entity_id": _new_id(api, "ellipse"),
        "layer_id": layer_id,
        "center": center,
        "radius_x": float(radius_x),
        "radius_y": float(radius_y),
        "rotation": float(rotation),
        "metadata": dict(metadata or {}),
    }
    return _construct(api["EllipseEntity"], values)


def _edge_endpoints(edge: Any, placement: Any = None):
    vertices = list(getattr(edge, "Vertexes", []) or [])
    if len(vertices) < 2:
        return None
    return (
        _transform_point(vertices[0].Point, placement),
        _transform_point(vertices[-1].Point, placement),
    )


def _edge_midpoint(edge: Any, placement: Any = None):
    first = float(getattr(edge, "FirstParameter", 0.0))
    last = float(getattr(edge, "LastParameter", 1.0))
    parameter = (first + last) * 0.5
    try:
        value = edge.valueAt(parameter)
    except Exception:
        value = edge.Curve.value(parameter)
    return _transform_point(value, placement)


def _clockwise(start: Any, forward_sample: Any, center: Any) -> bool:
    """Determine parametric direction, including arcs larger than 180 degrees."""

    start_x, start_y, _ = start
    sample_x, sample_y, _ = forward_sample
    center_x, center_y, _ = center
    return (
        (start_x - center_x) * (sample_y - center_y)
        - (start_y - center_y) * (sample_x - center_x)
    ) < 0.0


def _curve_name(curve: Any) -> str:
    return "{} {}".format(
        type(curve).__name__,
        getattr(curve, "TypeId", ""),
    ).lower()


def _edge_to_span(
    edge: Any,
    api: Mapping[str, Any],
    placement: Any,
):
    endpoints = _edge_endpoints(edge, placement)
    if endpoints is None:
        return None, "A aresta não possui duas extremidades."
    start_3d, end_3d = endpoints
    start = _vec2(api, start_3d)
    end = _vec2(api, end_3d)
    curve = getattr(edge, "Curve", None)
    curve_name = _curve_name(curve)

    if "line" in curve_name:
        return _line_span(api, start, end), None

    if "circle" in curve_name:
        center_3d = _transform_point(curve.Center, placement)
        first_parameter = float(getattr(edge, "FirstParameter", 0.0))
        last_parameter = float(getattr(edge, "LastParameter", 1.0))
        forward_parameter = first_parameter + (last_parameter - first_parameter) * 1.0e-6
        try:
            forward_value = edge.valueAt(forward_parameter)
        except Exception:
            forward_value = curve.value(forward_parameter)
        forward_3d = _transform_point(forward_value, placement)
        return (
            _arc_span(
                api,
                start,
                end,
                _vec2(api, center_3d),
                _clockwise(start_3d, forward_3d, center_3d),
            ),
            None,
        )

    if "bezier" in curve_name and "bspline" not in curve_name:
        poles = list(curve.getPoles())
        if len(poles) != 4:
            return None, f"Bézier de {len(poles) - 1} grau ainda não suportada."
        transformed = [_vec2(api, _transform_point(point, placement)) for point in poles]
        if transformed[0].distance_to(start) > transformed[-1].distance_to(start):
            transformed.reverse()
        return _bezier_span(api, start, transformed[1], transformed[2], end), None

    return None, f"Curva OCC não suportada: {type(curve).__name__}."


def _full_curve_entity(
    edge: Any,
    api: Mapping[str, Any],
    layer_id: str,
    placement: Any,
):
    curve = getattr(edge, "Curve", None)
    name = _curve_name(curve)
    if "circle" in name:
        center = _vec2(api, _transform_point(curve.Center, placement))
        return _circle_entity(api, layer_id, center, float(curve.Radius)), None
    if "ellipse" in name:
        center = _vec2(api, _transform_point(curve.Center, placement))
        x_axis = getattr(curve, "XAxis", None)
        rotation = math.atan2(float(x_axis.y), float(x_axis.x)) if x_axis is not None else 0.0
        return (
            _ellipse_entity(
                api,
                layer_id,
                center,
                float(curve.MajorRadius),
                float(curve.MinorRadius),
                rotation,
            ),
            None,
        )
    return None, f"Curva fechada não suportada: {type(curve).__name__}."


def import_part_shape(
    source: Any,
    *,
    layer_id: str,
    placement: Any = None,
) -> ImportResult:
    """Copy an OCC Shape without modifying/hiding the source object.

    ``placement`` is explicit to avoid accidentally applying an object's
    placement twice.  FreeCAD ``Shape`` coordinates are normally already in
    the object's shape placement.
    """

    api = _domain_api()
    shape = getattr(source, "Shape", source)
    if shape is None or bool(getattr(shape, "isNull", lambda: True)()):
        raise ValueError("O objeto selecionado não possui Shape importável.")

    # For 3D solids, copy one horizontal planar face per solid instead of
    # importing repeated top/bottom/side wires.  A PanelNest CAM compound can
    # contain many solids, and each one is an independent part that must reach
    # the editor. Explicit Face/wire input remains untouched.
    source_shapes = [shape]
    solids = list(getattr(shape, "Solids", []) or [])
    if solids:
        solid_faces = []
        for solid in solids:
            planar_faces = []
            for face in list(getattr(solid, "Faces", []) or []):
                try:
                    normal = face.normalAt(0.0, 0.0)
                    if abs(abs(float(normal.z)) - 1.0) <= 1.0e-8:
                        planar_faces.append(face)
                except Exception:
                    continue
            if planar_faces:
                solid_faces.append(
                    max(
                        planar_faces,
                        key=lambda face: float(getattr(face, "Area", 0.0)),
                    )
                )
        if solid_faces:
            source_shapes = solid_faces

    entities = []
    issues = []
    records = []
    for source_shape in source_shapes:
        local_covered_edges = []
        for wire in list(getattr(source_shape, "Wires", []) or []):
            wire_edges = list(
                getattr(wire, "OrderedEdges", [])
                or getattr(wire, "Edges", [])
                or []
            )
            if wire_edges:
                records.append((wire, wire_edges))
                local_covered_edges.extend(wire_edges)
        for edge in list(getattr(source_shape, "Edges", []) or []):
            is_covered = False
            for covered in local_covered_edges:
                try:
                    is_covered = bool(edge.isSame(covered))
                except Exception:
                    is_covered = edge == covered
                if is_covered:
                    break
            if not is_covered:
                records.append((None, [edge]))

    for wire_index, (wire, wire_edges) in enumerate(records):
        if len(wire_edges) == 1 and bool(getattr(wire_edges[0], "isClosed", lambda: False)()):
            entity, message = _full_curve_entity(
                wire_edges[0], api, layer_id, placement
            )
            if entity is not None:
                entities.append(entity)
            else:
                issues.append(
                    ImportIssue(wire_index, type(wire_edges[0].Curve).__name__, message or "Não suportada")
                )
            continue

        spans = []
        for edge_index, edge in enumerate(wire_edges):
            span, message = _edge_to_span(edge, api, placement)
            if span is not None:
                spans.append(span)
            else:
                issues.append(
                    ImportIssue(
                        edge_index,
                        type(getattr(edge, "Curve", edge)).__name__,
                        message or "Aresta não suportada.",
                    )
                )
        if spans:
            entities.append(
                _path_entity(
                    api,
                    layer_id,
                    spans,
                    bool(wire is not None and wire.isClosed()),
                )
            )

    metadata = {
        "source_kind": "part_shape",
        "source_name": str(getattr(source, "Name", "") or ""),
        "source_label": str(getattr(source, "Label", "") or ""),
    }
    source_layer_key = metadata["source_name"] or "part_shape:default"
    source_layer_name = metadata["source_label"] or metadata["source_name"] or "Forma importada"
    marked_entities = []
    for entity in entities:
        entity_metadata = dict(getattr(entity, "metadata", {}) or {})
        entity_metadata.update(
            {
                "source_format": "part_shape",
                "source_layer_key": source_layer_key,
            }
        )
        marked_entities.append(replace(entity, metadata=entity_metadata))
    layers = {
        source_layer_key: ImportLayerDescriptor(
            source_key=source_layer_key,
            name=source_layer_name,
            purpose="design",
        )
    }
    return ImportResult(tuple(marked_entities), tuple(issues), metadata, layers)
