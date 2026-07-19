"""Immutable vector entities.

Entities own exact geometry and stable IDs.  Presentation state (selection,
hover, camera and handles) intentionally does not live here.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from typing import Any, Dict, Iterable, Mapping, Tuple, Union

from .primitives import (
    DEFAULT_EPSILON,
    Affine2D,
    BBox2D,
    GeometryError,
    InvariantError,
    Vec2,
    new_id,
)
from .spans import ArcSpan, CubicBezierSpan, LineSpan, Span
from ..geometry.math2d import deduplicate_consecutive, signed_area


def _metadata_copy(metadata: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(metadata or {})


@dataclass(frozen=True, slots=True)
class Node2D:
    id: str
    point: Vec2


@dataclass(frozen=True, slots=True)
class PathEntity:
    layer_id: str
    spans: Tuple[Span, ...]
    closed: bool = False
    id: str = field(default_factory=lambda: new_id("path"))
    node_ids: Tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        spans = tuple(self.spans)
        object.__setattr__(self, "spans", spans)
        object.__setattr__(self, "node_ids", tuple(self.node_ids))
        object.__setattr__(self, "metadata", _metadata_copy(self.metadata))
        if not self.id or not self.layer_id:
            raise InvariantError("PathEntity requires non-empty id and layer_id")
        if not spans:
            raise InvariantError("PathEntity requires at least one span")
        if len({span.id for span in spans}) != len(spans):
            raise InvariantError("span IDs must be unique inside a path")
        for index in range(len(spans) - 1):
            if not spans[index].end.almost_equals(spans[index + 1].start, DEFAULT_EPSILON):
                raise InvariantError("path spans are not contiguous at index %d" % index)
        if self.closed and not spans[-1].end.almost_equals(spans[0].start, DEFAULT_EPSILON):
            raise InvariantError("closed path does not connect its final and first span")
        if not self.closed and spans[-1].end.almost_equals(spans[0].start, DEFAULT_EPSILON) and len(spans) > 1:
            raise InvariantError("an open path cannot have coincident endpoints; mark it closed")

        expected_nodes = len(spans) if self.closed else len(spans) + 1
        if not self.node_ids:
            object.__setattr__(
                self,
                "node_ids",
                tuple(new_id("node") for _ in range(expected_nodes)),
            )
        elif len(self.node_ids) != expected_nodes:
            raise InvariantError(
                "PathEntity expected %d node IDs, received %d"
                % (expected_nodes, len(self.node_ids))
            )
        if len(set(self.node_ids)) != len(self.node_ids) or any(not node_id for node_id in self.node_ids):
            raise InvariantError("node IDs must be non-empty and unique inside a path")

    @classmethod
    def from_points(
        cls,
        layer_id: str,
        points: Iterable[Vec2],
        closed: bool = False,
        *,
        id: str | None = None,
        metadata: Mapping[str, Any] | None = None,
    ) -> "PathEntity":
        points = tuple(points)
        if closed and len(points) > 1 and points[0].almost_equals(points[-1]):
            points = points[:-1]
        minimum = 3 if closed else 2
        if len(points) < minimum:
            raise GeometryError("path requires at least %d points" % minimum)
        spans = [LineSpan(points[index], points[index + 1]) for index in range(len(points) - 1)]
        if closed:
            spans.append(LineSpan(points[-1], points[0]))
        kwargs = {
            "layer_id": layer_id,
            "spans": tuple(spans),
            "closed": closed,
            "metadata": metadata or {},
        }
        if id is not None:
            kwargs["id"] = id
        return cls(**kwargs)

    @property
    def type(self) -> str:
        return "path"

    @property
    def start(self) -> Vec2:
        return self.spans[0].start

    @property
    def end(self) -> Vec2:
        return self.spans[-1].end

    def bounds(self) -> BBox2D:
        result = self.spans[0].bounds()
        for span in self.spans[1:]:
            result = result.union(span.bounds())
        return result

    def length(self) -> float:
        return sum(span.length() for span in self.spans)

    def nodes(self) -> Tuple[Node2D, ...]:
        points = [span.start for span in self.spans]
        if not self.closed:
            points.append(self.spans[-1].end)
        return tuple(Node2D(node_id, point) for node_id, point in zip(self.node_ids, points))

    def node_position(self, node_id: str) -> Vec2:
        for node in self.nodes():
            if node.id == node_id:
                return node.point
        raise KeyError("unknown node ID %s" % node_id)

    def span_node_ids(self, span_id: str) -> Tuple[str, str]:
        for index, span in enumerate(self.spans):
            if span.id == span_id:
                end_index = (index + 1) % len(self.node_ids)
                return self.node_ids[index], self.node_ids[end_index]
        raise KeyError("unknown span ID %s" % span_id)

    def with_node_moved(self, node_id: str, new_position: Vec2) -> "PathEntity":
        if not isinstance(new_position, Vec2):
            raise TypeError("new_position must be Vec2")
        try:
            node_index = self.node_ids.index(node_id)
        except ValueError:
            raise KeyError("unknown node ID %s" % node_id)
        spans = list(self.spans)
        if node_index < len(spans):
            spans[node_index] = spans[node_index].with_start(new_position)
        previous_index = node_index - 1
        if self.closed or node_index > 0:
            spans[previous_index] = spans[previous_index].with_end(new_position)
        return replace(self, spans=tuple(spans))

    def with_span_replaced(self, span_id: str, replacements: Iterable[Span]) -> "PathEntity":
        replacements = tuple(replacements)
        if not replacements:
            raise GeometryError("replacement cannot be empty")
        index = next((i for i, span in enumerate(self.spans) if span.id == span_id), None)
        if index is None:
            raise KeyError("unknown span ID %s" % span_id)
        spans = self.spans[:index] + replacements + self.spans[index + 1 :]
        node_ids = list(self.node_ids)
        for offset in range(len(replacements) - 1):
            node_ids.insert(index + 1 + offset, new_id("node"))
        return replace(self, spans=spans, node_ids=tuple(node_ids))

    def reversed(self) -> "PathEntity":
        spans = tuple(span.reversed() for span in reversed(self.spans))
        if self.closed:
            node_ids = (self.node_ids[0],) + tuple(reversed(self.node_ids[1:]))
        else:
            node_ids = tuple(reversed(self.node_ids))
        return replace(self, spans=spans, node_ids=node_ids)

    def transformed(self, transform: Affine2D) -> "PathEntity":
        spans = tuple(span.transformed(transform) for span in self.spans)
        if transform.determinant < 0.0 and self.closed:
            # The transform itself already mirrors every span and therefore its
            # orientation.  Keep ordering/IDs; callers can query signed_area.
            pass
        return replace(self, spans=spans)

    def flatten(self, deflection: float = 0.01, include_closure: bool = True) -> Tuple[Vec2, ...]:
        points = []
        for span in self.spans:
            flattened = span.flatten(deflection)
            if points:
                flattened = flattened[1:]
            points.extend(flattened)
        points = list(deduplicate_consecutive(points, DEFAULT_EPSILON))
        if self.closed:
            if include_closure and not points[-1].almost_equals(points[0]):
                points.append(points[0])
            elif not include_closure and points[-1].almost_equals(points[0]):
                points.pop()
        return tuple(points)

    def signed_area(self, deflection: float = 0.01) -> float:
        if not self.closed:
            return 0.0
        points = self.flatten(deflection, include_closure=False)
        return signed_area(points)


@dataclass(frozen=True, slots=True)
class CircleEntity:
    layer_id: str
    center: Vec2
    radius: float
    id: str = field(default_factory=lambda: new_id("circle"))
    metadata: Mapping[str, Any] = field(default_factory=dict)
    center_node_id: str = field(default_factory=lambda: new_id("node"))
    radius_node_id: str = field(default_factory=lambda: new_id("node"))

    def __post_init__(self) -> None:
        object.__setattr__(self, "radius", float(self.radius))
        object.__setattr__(self, "metadata", _metadata_copy(self.metadata))
        if not self.id or not self.layer_id:
            raise InvariantError("CircleEntity requires non-empty id and layer_id")
        if not math.isfinite(self.radius) or self.radius <= DEFAULT_EPSILON:
            raise GeometryError("circle radius must be finite and positive")
        if self.center_node_id == self.radius_node_id:
            raise InvariantError("circle handle IDs must be unique")

    @property
    def type(self) -> str:
        return "circle"

    def bounds(self) -> BBox2D:
        return BBox2D(
            self.center.x - self.radius,
            self.center.y - self.radius,
            self.center.x + self.radius,
            self.center.y + self.radius,
        )

    def length(self) -> float:
        return 2.0 * math.pi * self.radius

    def area(self) -> float:
        return math.pi * self.radius * self.radius

    def point_at(self, t: float) -> Vec2:
        angle = float(t) * 2.0 * math.pi
        return self.center + Vec2(math.cos(angle), math.sin(angle)) * self.radius

    def nodes(self) -> Tuple[Node2D, Node2D]:
        return (
            Node2D(self.center_node_id, self.center),
            Node2D(self.radius_node_id, self.center + Vec2(self.radius, 0.0)),
        )

    def node_position(self, node_id: str) -> Vec2:
        for node in self.nodes():
            if node.id == node_id:
                return node.point
        raise KeyError("unknown node ID %s" % node_id)

    def with_node_moved(self, node_id: str, new_position: Vec2) -> "CircleEntity":
        if node_id == self.center_node_id:
            return replace(self, center=new_position)
        if node_id == self.radius_node_id:
            return replace(self, radius=self.center.distance_to(new_position))
        raise KeyError("unknown node ID %s" % node_id)

    def transformed(self, transform: Affine2D) -> "CircleEntity":
        if not transform.is_similarity():
            raise GeometryError("non-uniform transform converts a circle to an ellipse")
        return replace(
            self,
            center=transform.apply_to_point(self.center),
            radius=self.radius * transform.uniform_scale(),
        )

    def flatten(self, deflection: float = 0.01, include_closure: bool = True) -> Tuple[Vec2, ...]:
        if deflection <= 0.0:
            raise GeometryError("deflection must be positive")
        if deflection >= self.radius:
            count = 8
        else:
            max_step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - deflection / self.radius)))
            count = max(8, int(math.ceil(2.0 * math.pi / max_step)))
        stop = count + 1 if include_closure else count
        return tuple(self.point_at(index / count) for index in range(stop))


@dataclass(frozen=True, slots=True)
class EllipseEntity:
    layer_id: str
    center: Vec2
    radius_x: float
    radius_y: float
    rotation: float = 0.0
    id: str = field(default_factory=lambda: new_id("ellipse"))
    metadata: Mapping[str, Any] = field(default_factory=dict)
    center_node_id: str = field(default_factory=lambda: new_id("node"))

    def __post_init__(self) -> None:
        for name in ("radius_x", "radius_y", "rotation"):
            object.__setattr__(self, name, float(getattr(self, name)))
        object.__setattr__(self, "metadata", _metadata_copy(self.metadata))
        if not self.id or not self.layer_id:
            raise InvariantError("EllipseEntity requires non-empty id and layer_id")
        if not all(math.isfinite(value) for value in (self.radius_x, self.radius_y, self.rotation)):
            raise GeometryError("ellipse values must be finite")
        if self.radius_x <= DEFAULT_EPSILON or self.radius_y <= DEFAULT_EPSILON:
            raise GeometryError("ellipse radii must be positive")

    @property
    def type(self) -> str:
        return "ellipse"

    def point_at(self, t: float) -> Vec2:
        angle = 2.0 * math.pi * float(t)
        local = Vec2(self.radius_x * math.cos(angle), self.radius_y * math.sin(angle))
        return self.center + local.rotated(self.rotation)

    def bounds(self) -> BBox2D:
        cosine = math.cos(self.rotation)
        sine = math.sin(self.rotation)
        extent_x = math.sqrt((self.radius_x * cosine) ** 2 + (self.radius_y * sine) ** 2)
        extent_y = math.sqrt((self.radius_x * sine) ** 2 + (self.radius_y * cosine) ** 2)
        return BBox2D(
            self.center.x - extent_x,
            self.center.y - extent_y,
            self.center.x + extent_x,
            self.center.y + extent_y,
        )

    def nodes(self) -> Tuple[Node2D, ...]:
        return (Node2D(self.center_node_id, self.center),)

    def node_position(self, node_id: str) -> Vec2:
        if node_id != self.center_node_id:
            raise KeyError("unknown node ID %s" % node_id)
        return self.center

    def with_node_moved(self, node_id: str, new_position: Vec2) -> "EllipseEntity":
        if node_id != self.center_node_id:
            raise KeyError("unknown node ID %s" % node_id)
        return replace(self, center=new_position)

    def transformed(self, transform: Affine2D) -> "EllipseEntity":
        if not transform.is_similarity():
            raise GeometryError("general affine ellipse transformation is not yet exact")
        x_axis = transform.apply_to_vector(Vec2(math.cos(self.rotation), math.sin(self.rotation)))
        rotation = x_axis.angle()
        scale = transform.uniform_scale()
        return replace(
            self,
            center=transform.apply_to_point(self.center),
            radius_x=self.radius_x * scale,
            radius_y=self.radius_y * scale,
            rotation=rotation,
        )

    def flatten(self, deflection: float = 0.01, include_closure: bool = True) -> Tuple[Vec2, ...]:
        if deflection <= 0.0:
            raise GeometryError("deflection must be positive")
        maximum_radius = max(self.radius_x, self.radius_y)
        max_step = 2.0 * math.acos(max(-1.0, min(1.0, 1.0 - min(deflection, maximum_radius) / maximum_radius)))
        count = max(12, int(math.ceil(2.0 * math.pi / max(max_step, 1.0e-3))))
        stop = count + 1 if include_closure else count
        return tuple(self.point_at(index / count) for index in range(stop))


@dataclass(frozen=True, slots=True)
class GroupEntity:
    layer_id: str
    child_ids: Tuple[str, ...]
    transform: Affine2D = field(default_factory=Affine2D.identity)
    id: str = field(default_factory=lambda: new_id("group"))
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "child_ids", tuple(self.child_ids))
        object.__setattr__(self, "metadata", _metadata_copy(self.metadata))
        if not self.id or not self.layer_id:
            raise InvariantError("GroupEntity requires non-empty id and layer_id")
        if not self.child_ids or len(set(self.child_ids)) != len(self.child_ids):
            raise InvariantError("group child IDs must be non-empty and unique")
        if self.id in self.child_ids:
            raise InvariantError("group cannot contain itself")

    @property
    def type(self) -> str:
        return "group"

    def transformed(self, transform: Affine2D) -> "GroupEntity":
        return replace(self, transform=transform @ self.transform)


VectorEntity = Union[PathEntity, CircleEntity, EllipseEntity, GroupEntity]


def entity_type_name(entity: VectorEntity) -> str:
    return entity.type
