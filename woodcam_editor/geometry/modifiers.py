"""Conservative, preview-first vector modifiers.

All functions are pure: they return a :class:`ModifierPreview` and never mutate
the input entity or document.  The first implementation intentionally accepts
only exact line-span cases where the result can be proven.  Unsupported curves,
parallel offsets, invalid corners and ambiguous trim/extend operations raise
``ModifierError`` instead of guessing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
import math
from typing import Any, Iterable, Mapping, Sequence, Tuple

from ..domain.entities import CircleEntity, EllipseEntity, PathEntity, VectorEntity
from ..domain.primitives import DEFAULT_EPSILON, GeometryError, Vec2, new_id
from ..domain.spans import ArcSpan, LineSpan
from .math2d import clamp, segment_intersections


class ModifierError(GeometryError):
    """A requested modifier is ambiguous or unsupported conservatively."""


class NoApplicableCornersError(ModifierError):
    """Raised when an automatic relief batch has no safe applicable corner."""

    def __init__(self, message: str, metadata: Mapping[str, Any], warnings=()):
        super().__init__(message)
        self.metadata = dict(metadata)
        self.warnings = tuple(warnings)


class FilletKind(str, Enum):
    NORMAL = "normal"
    DOGBONE = "dogbone"
    TBONE = "tbone"


@dataclass(frozen=True, slots=True)
class ModifierWarning:
    code: str
    message: str


@dataclass(frozen=True)
class ModifierPreview:
    operation: str
    original_entities: Tuple[VectorEntity, ...]
    result_entities: Tuple[VectorEntity, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)
    construction_points: Tuple[Vec2, ...] = ()
    warnings: Tuple[ModifierWarning, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "original_entities", tuple(self.original_entities))
        object.__setattr__(self, "result_entities", tuple(self.result_entities))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        object.__setattr__(self, "construction_points", tuple(self.construction_points))
        object.__setattr__(self, "warnings", tuple(self.warnings))
        original_ids = [entity.id for entity in self.original_entities]
        result_ids = [entity.id for entity in self.result_entities]
        if not self.operation or not original_ids:
            raise ModifierError("preview requires an operation and source entities")
        if len(set(original_ids)) != len(original_ids):
            raise ModifierError("preview source IDs must be unique")
        if len(set(result_ids)) != len(result_ids):
            raise ModifierError("preview result IDs must be unique")

    @property
    def original_ids(self) -> frozenset[str]:
        return frozenset(entity.id for entity in self.original_entities)

    @property
    def result_ids(self) -> frozenset[str]:
        return frozenset(entity.id for entity in self.result_entities)

    @property
    def added_ids(self) -> frozenset[str]:
        return self.result_ids - self.original_ids

    @property
    def removed_ids(self) -> frozenset[str]:
        return self.original_ids - self.result_ids

    @property
    def changed_ids(self) -> frozenset[str]:
        return self.original_ids & self.result_ids


def _modifier_metadata(path: PathEntity, operation: str, details: Mapping[str, Any]) -> dict:
    metadata = dict(path.metadata)
    history = list(metadata.get("woodcam_modifier_history", ()))
    history.append({"operation": operation, **dict(details)})
    metadata["woodcam_modifier_history"] = history
    metadata["last_woodcam_modifier"] = operation
    return metadata


def _stable_node_ids(original: PathEntity, spans: Sequence, closed: bool) -> Tuple[str, ...]:
    points = [span.start for span in spans]
    if not closed and spans:
        points.append(spans[-1].end)
    available = [(node.id, node.point) for node in original.nodes()]
    result = []
    used = set()
    for point in points:
        matching = next(
            (
                node_id
                for node_id, old_point in available
                if node_id not in used and point.almost_equals(old_point, DEFAULT_EPSILON)
            ),
            None,
        )
        node_id = matching or new_id("node")
        used.add(node_id)
        result.append(node_id)
    return tuple(result)


def _path_from_spans(
    original: PathEntity,
    spans: Sequence,
    *,
    closed: bool,
    entity_id: str,
    operation: str,
    details: Mapping[str, Any],
) -> PathEntity:
    spans = tuple(spans)
    if not spans:
        raise ModifierError("cannot construct an empty result path")
    return PathEntity(
        id=entity_id,
        layer_id=original.layer_id,
        spans=spans,
        closed=closed,
        node_ids=_stable_node_ids(original, spans, closed),
        metadata=_modifier_metadata(original, operation, details),
    )


def _line_span(path: PathEntity, span_id: str) -> Tuple[int, LineSpan]:
    for index, span in enumerate(path.spans):
        if span.id == span_id:
            if not isinstance(span, LineSpan):
                raise ModifierError("the selected span is not linear")
            return index, span
    raise KeyError("unknown span ID %s" % span_id)


def preview_trim_line_span(
    path: PathEntity,
    span_id: str,
    start_parameter: float,
    end_parameter: float,
    *,
    tolerance: float = DEFAULT_EPSILON,
) -> ModifierPreview:
    """Preview removal of an interval from one line span.

    A middle trim on an open path deliberately yields two independent paths.
    Removing an interval from a closed path yields one open path ordered from
    the second trim point around to the first.
    """

    if not isinstance(path, PathEntity):
        raise TypeError("trim requires PathEntity")
    index, target = _line_span(path, span_id)
    start_parameter = float(start_parameter)
    end_parameter = float(end_parameter)
    if start_parameter > end_parameter:
        start_parameter, end_parameter = end_parameter, start_parameter
    start_parameter = clamp(start_parameter, 0.0, 1.0)
    end_parameter = clamp(end_parameter, 0.0, 1.0)
    if end_parameter - start_parameter <= tolerance:
        raise ModifierError("trim interval has zero length")

    trim_start = target.point_at(start_parameter)
    trim_end = target.point_at(end_parameter)
    left = None
    right = None
    if start_parameter > tolerance:
        left = LineSpan(target.start, trim_start, id=target.id)
    if end_parameter < 1.0 - tolerance:
        right = LineSpan(
            trim_end,
            target.end,
            id=target.id if left is None else new_id("span"),
        )
    details = {
        "source_path_id": path.id,
        "source_span_id": span_id,
        "start_parameter": start_parameter,
        "end_parameter": end_parameter,
        "removed_length_mm": target.length() * (end_parameter - start_parameter),
    }

    results = []
    if path.closed:
        # Start immediately after the removed interval and traverse the former
        # cycle until reaching its first trim point.
        remaining = []
        if right is not None:
            remaining.append(right)
        remaining.extend(path.spans[index + 1 :])
        remaining.extend(path.spans[:index])
        if left is not None:
            remaining.append(left)
        if remaining:
            results.append(
                _path_from_spans(
                    path,
                    remaining,
                    closed=False,
                    entity_id=path.id,
                    operation="trim_line",
                    details=details,
                )
            )
    else:
        prefix = list(path.spans[:index])
        suffix = list(path.spans[index + 1 :])
        if left is not None:
            prefix.append(left)
        if right is not None:
            suffix.insert(0, right)
        nonempty = [spans for spans in (prefix, suffix) if spans]
        for result_index, spans in enumerate(nonempty):
            results.append(
                _path_from_spans(
                    path,
                    spans,
                    closed=False,
                    entity_id=path.id if result_index == 0 else new_id("path"),
                    operation="trim_line",
                    details={**details, "result_part": result_index + 1},
                )
            )
    return ModifierPreview(
        operation="trim_line",
        original_entities=(path,),
        result_entities=tuple(results),
        metadata=details,
        construction_points=(trim_start, trim_end),
    )


def _iter_line_spans(values: Iterable[Any]) -> Tuple[LineSpan, ...]:
    result = []
    for value in values:
        if isinstance(value, LineSpan):
            result.append(value)
        elif isinstance(value, PathEntity):
            for span in value.spans:
                if isinstance(span, LineSpan):
                    result.append(span)
        else:
            raise ModifierError("cutters/boundaries must be line spans or paths")
    return tuple(result)


def preview_trim_at_point(
    path: PathEntity,
    span_id: str,
    click_point: Vec2,
    cutters: Iterable[Any],
    *,
    tolerance: float = 1.0e-9,
) -> ModifierPreview:
    """Choose the interval around a click bounded by neighbouring cutters."""

    _, target = _line_span(path, span_id)
    click_parameter = target.nearest_point(click_point).parameter
    parameters = [0.0, 1.0]
    for cutter in _iter_line_spans(cutters):
        for intersection in segment_intersections(
            target.start, target.end, cutter.start, cutter.end, tolerance
        ):
            if intersection.kind == "overlap":
                raise ModifierError("collinear overlap makes interactive trim ambiguous")
            if tolerance < intersection.parameter_a < 1.0 - tolerance:
                parameters.append(intersection.parameter_a)
    parameters = sorted(set(round(value, 14) for value in parameters))
    if len(parameters) <= 2:
        raise ModifierError("the target span has no internal cutter intersections")
    if any(abs(click_parameter - parameter) <= tolerance for parameter in parameters[1:-1]):
        raise ModifierError("click lies on an intersection; choose one side")
    for start, end in zip(parameters, parameters[1:]):
        if start < click_parameter < end or (
            abs(click_parameter) <= tolerance and start == 0.0
        ) or (
            abs(click_parameter - 1.0) <= tolerance and end == 1.0
        ):
            preview = preview_trim_line_span(path, span_id, start, end, tolerance=tolerance)
            metadata = dict(preview.metadata)
            metadata["click_parameter"] = click_parameter
            return ModifierPreview(
                preview.operation,
                preview.original_entities,
                preview.result_entities,
                metadata,
                preview.construction_points,
                preview.warnings,
            )
    raise ModifierError("could not determine the trim interval")


def _infinite_line_parameters(a0: Vec2, a1: Vec2, b0: Vec2, b1: Vec2):
    first = a1 - a0
    second = b1 - b0
    denominator = first.cross(second)
    scale = max(1.0, first.length(), second.length())
    if abs(denominator) <= DEFAULT_EPSILON * scale:
        return None
    difference = b0 - a0
    return difference.cross(second) / denominator, difference.cross(first) / denominator


def preview_extend_line_span(
    path: PathEntity,
    span_id: str,
    endpoint: str,
    boundaries: Iterable[Any],
    *,
    tolerance: float = 1.0e-9,
) -> ModifierPreview:
    """Extend an open path endpoint to the nearest finite boundary line."""

    if path.closed:
        raise ModifierError("extend is only defined at an open path endpoint")
    index, target = _line_span(path, span_id)
    endpoint = str(endpoint).lower()
    if endpoint == "start":
        if index != 0:
            raise ModifierError("only the first span start can be extended")
    elif endpoint == "end":
        if index != len(path.spans) - 1:
            raise ModifierError("only the final span end can be extended")
    else:
        raise ValueError("endpoint must be 'start' or 'end'")

    candidates = []
    for boundary in _iter_line_spans(boundaries):
        parameters = _infinite_line_parameters(
            target.start, target.end, boundary.start, boundary.end
        )
        if parameters is None:
            continue
        target_parameter, boundary_parameter = parameters
        if not -tolerance <= boundary_parameter <= 1.0 + tolerance:
            continue
        if endpoint == "end" and target_parameter <= 1.0 + tolerance:
            continue
        if endpoint == "start" and target_parameter >= -tolerance:
            continue
        point = target.start + (target.end - target.start) * target_parameter
        reference = target.end if endpoint == "end" else target.start
        candidates.append((reference.distance_to(point), point, target_parameter, boundary.id))
    if not candidates:
        raise ModifierError("no boundary intersects the selected extension ray")
    distance, point, parameter, boundary_id = min(candidates, key=lambda value: value[0])
    node_id = path.node_ids[-1] if endpoint == "end" else path.node_ids[0]
    result = path.with_node_moved(node_id, point)
    details = {
        "source_path_id": path.id,
        "source_span_id": span_id,
        "endpoint": endpoint,
        "boundary_span_id": boundary_id,
        "extension_length_mm": distance,
        "target_parameter": parameter,
    }
    result = PathEntity(
        id=result.id,
        layer_id=result.layer_id,
        spans=result.spans,
        closed=result.closed,
        node_ids=result.node_ids,
        metadata=_modifier_metadata(path, "extend_line", details),
    )
    return ModifierPreview(
        "extend_line",
        (path,),
        (result,),
        details,
        (point,),
    )


def _require_closed_linear(path: PathEntity, operation: str) -> None:
    if not isinstance(path, PathEntity) or not path.closed:
        raise ModifierError("%s requires a closed path" % operation)
    if not all(isinstance(span, LineSpan) for span in path.spans):
        raise ModifierError("%s currently supports line spans only" % operation)
    if len(path.spans) < 3:
        raise ModifierError("%s requires at least three line spans" % operation)


def _proper_self_intersections(spans: Sequence[LineSpan], tolerance: float) -> Tuple[Vec2, ...]:
    points = []
    count = len(spans)
    for left_index, left in enumerate(spans):
        for right_index in range(left_index + 1, count):
            if right_index == left_index + 1 or (left_index == 0 and right_index == count - 1):
                continue
            right = spans[right_index]
            for intersection in segment_intersections(
                left.start, left.end, right.start, right.end, tolerance
            ):
                if not any(intersection.point.almost_equals(existing, tolerance) for existing in points):
                    points.append(intersection.point)
    return tuple(points)


def preview_offset_closed_path(
    path: PathEntity,
    distance: float,
    *,
    miter_limit: float = 10.0,
    tolerance: float = 1.0e-9,
) -> ModifierPreview:
    """Preview a mitered offset of a simple closed linear path.

    Positive distance is outward for either path orientation; negative is
    inward.  Parallel/collinear corners, excessive miters and self-intersecting
    results are rejected rather than bevelled silently.
    """

    _require_closed_linear(path, "offset")
    distance = float(distance)
    if abs(distance) <= tolerance:
        raise ModifierError("offset distance must be non-zero")
    if miter_limit < 1.0:
        raise ValueError("miter_limit must be at least 1")
    area = path.signed_area()
    if abs(area) <= tolerance * tolerance:
        raise ModifierError("cannot offset a zero-area contour")
    orientation = 1.0 if area > 0.0 else -1.0
    offset_lines = []
    for span in path.spans:
        direction = (span.end - span.start).normalized()
        outward = direction.perpendicular_left() * -orientation
        delta = outward * distance
        offset_lines.append((span.start + delta, span.end + delta))

    vertices = []
    for index in range(len(offset_lines)):
        previous = offset_lines[index - 1]
        current = offset_lines[index]
        parameters = _infinite_line_parameters(previous[0], previous[1], current[0], current[1])
        if parameters is None:
            raise ModifierError("offset encountered a parallel or collinear corner")
        previous_parameter, _ = parameters
        vertex = previous[0] + (previous[1] - previous[0]) * previous_parameter
        original_vertex = path.spans[index].start
        if vertex.distance_to(original_vertex) > abs(distance) * miter_limit + tolerance:
            raise ModifierError("offset miter exceeds the configured miter limit")
        vertices.append(vertex)
    result_spans = tuple(
        LineSpan(vertices[index], vertices[(index + 1) % len(vertices)], id=path.spans[index].id)
        for index in range(len(vertices))
    )
    if any(
        (result.end - result.start).dot(original.end - original.start) <= tolerance
        for result, original in zip(result_spans, path.spans)
    ):
        raise ModifierError("inward offset crossed the contour medial limit")
    intersections = _proper_self_intersections(result_spans, tolerance)
    if intersections:
        raise ModifierError("offset result self-intersects")
    result_area = 0.5 * sum(
        vertices[index].cross(vertices[(index + 1) % len(vertices)])
        for index in range(len(vertices))
    )
    if abs(result_area) <= tolerance * tolerance or result_area * area <= 0.0:
        raise ModifierError("inward offset collapsed or inverted the contour")
    details = {
        "source_path_id": path.id,
        "distance_mm": distance,
        "side": "outward" if distance > 0.0 else "inward",
        "join": "miter",
        "miter_limit": float(miter_limit),
    }
    result = PathEntity(
        id=path.id,
        layer_id=path.layer_id,
        spans=result_spans,
        closed=True,
        node_ids=path.node_ids,
        metadata=_modifier_metadata(path, "offset", details),
    )
    return ModifierPreview("offset", (path,), (result,), details, tuple(vertices))


def _corner_geometry(path: PathEntity, node_id: str, angle_tolerance: float | None = None):
    if not isinstance(path, PathEntity) or not path.closed:
        raise ModifierError("fillet requires a closed path")
    if len(path.spans) < 3:
        raise ModifierError("fillet requires at least three spans")
    try:
        node_index = path.node_ids.index(node_id)
    except ValueError:
        raise KeyError("unknown node ID %s" % node_id)
    previous_index = (node_index - 1) % len(path.spans)
    next_index = node_index
    previous = path.spans[previous_index]
    following = path.spans[next_index]
    if not isinstance(previous, LineSpan) or not isinstance(following, LineSpan):
        raise ModifierError("fillet corner must be formed by two line spans")
    corner = following.start
    incoming = (corner - previous.start).normalized()
    outgoing = (following.end - corner).normalized()
    turn = incoming.cross(outgoing)
    orientation = 1.0 if path.signed_area() > 0.0 else -1.0
    is_convex = turn * orientation > DEFAULT_EPSILON
    interior_angle = math.acos(clamp((-incoming).dot(outgoing), -1.0, 1.0))
    if interior_angle <= DEFAULT_EPSILON or interior_angle >= math.pi - DEFAULT_EPSILON:
        raise ModifierError("corner angle cannot be filleted")
    if angle_tolerance is not None and abs(interior_angle - math.pi * 0.5) > angle_tolerance:
        raise ModifierError("dogbone/T-bone automatic mode supports 90 degree corners only")
    return node_index, previous_index, next_index, previous, following, corner, incoming, outgoing, orientation, interior_angle, is_convex


def _normalize_contour_role(value: Any) -> str | None:
    value = str(value or "").strip().lower()
    if value in ("inner", "hole", "pocket", "cutout", "recorte", "furo"):
        return "inner"
    if value in ("outer", "external", "part", "profile", "contorno_externo"):
        return "outer"
    return None


def _path_contour_role(path: PathEntity) -> str | None:
    for key in ("contour_role", "woodcam_contour_role", "piece_role", "role"):
        if key in path.metadata:
            return _normalize_contour_role(path.metadata.get(key))
    return None


def _insert_between_corner_spans(
    spans: Sequence,
    node_index: int,
    inserted: Sequence,
) -> Tuple:
    # The corner at node 0 lies between the final and first span, so inserted
    # spans belong at the end of the cyclic representation.
    if node_index == 0:
        return tuple(spans) + tuple(inserted)
    return tuple(spans[:node_index]) + tuple(inserted) + tuple(spans[node_index:])


def preview_corner_fillet(
    path: PathEntity,
    node_id: str,
    radius: float,
    *,
    kind: FilletKind | str = FilletKind.NORMAL,
    contour_role: str = "inner",
    tbone_side: str = "auto",
    angle_tolerance_degrees: float = 1.0,
    tolerance: float = 1.0e-9,
) -> ModifierPreview:
    """Preview a normal fillet or an intentional circular corner relief.

    Dogbone/T-bone are deliberately limited to machining-internal 90° corners:
    convex vertices of an ``inner`` contour or concave re-entrant vertices of
    an ``outer`` contour. Adjacent line spans are clipped and one exact arc is
    inserted in path order; the result has no full-circle loop or self-touch.
    """

    kind = kind if isinstance(kind, FilletKind) else FilletKind(str(kind).lower())
    radius = float(radius)
    if radius <= tolerance:
        raise ModifierError("fillet radius must be positive")
    angle_tolerance = None
    if kind is not FilletKind.NORMAL:
        angle_tolerance = math.radians(float(angle_tolerance_degrees))
    (
        node_index,
        previous_index,
        next_index,
        previous,
        following,
        corner,
        incoming,
        outgoing,
        orientation,
        interior_angle,
        is_convex,
    ) = _corner_geometry(path, node_id, angle_tolerance)
    spans = list(path.spans)
    warnings = []

    if kind is FilletKind.NORMAL:
        if not is_convex:
            raise ModifierError("normal fillet currently supports convex corners only")
        tangent_distance = radius / math.tan(interior_angle * 0.5)
        if tangent_distance >= previous.length() - tolerance or tangent_distance >= following.length() - tolerance:
            raise ModifierError("fillet radius does not fit on the adjacent spans")
        tangent_in = corner - incoming * tangent_distance
        tangent_out = corner + outgoing * tangent_distance
        interior_normal = incoming.perpendicular_left() * orientation
        center = tangent_in + interior_normal * radius
        if abs(center.distance_to(tangent_out) - radius) > max(tolerance, radius * 1.0e-7):
            raise ModifierError("fillet tangent construction is numerically inconsistent")
        spans[previous_index] = previous.with_end(tangent_in)
        spans[next_index] = following.with_start(tangent_out)
        arc = ArcSpan(
            tangent_in,
            tangent_out,
            center,
            clockwise=orientation < 0.0,
        )
        result_spans = _insert_between_corner_spans(spans, node_index, (arc,))
        construction = (corner, tangent_in, center, tangent_out)
        details = {
            "kind": kind.value,
            "source_path_id": path.id,
            "source_node_id": node_id,
            "radius_mm": radius,
            "original_corner": list(corner.to_tuple()),
            "tangent_distance_mm": tangent_distance,
            "tangent_points": [
                list(tangent_in.to_tuple()),
                list(tangent_out.to_tuple()),
            ],
        }
    else:
        role = _normalize_contour_role(contour_role)
        if role is None:
            raise ModifierError("dogbone/T-bone requires explicit inner/outer contour role")
        is_internal_machining_corner = (
            (role == "inner" and is_convex)
            or (role == "outer" and not is_convex)
        )
        if not is_internal_machining_corner:
            raise ModifierError("selected node is an external corner; relief was not applied")
        if kind is FilletKind.DOGBONE:
            # Vectric-style dogbone: the cutter circle centre is one radius
            # along the bisector of the machined 90-degree region, so the
            # theoretical sharp corner lies on its circumference.  The two
            # distant line intersections are sqrt(2) radii from the corner.
            if min(previous.length(), following.length()) < radius * 2.0 - tolerance:
                raise ModifierError("adjacent spans are shorter than the relief diameter")
            interior_bisector = ((-incoming) + outgoing).normalized()
            center = corner + interior_bisector * radius
            trim_distance = radius * math.sqrt(2.0)
            entry_point = corner - incoming * trim_distance
            exit_point = corner + outgoing * trim_distance
            selected_side = "bisector"
            # The endpoints are diametrically opposed.  Select the semicircle
            # whose midpoint is the original sharp corner, independent of
            # contour orientation and inner/outer role.
            counterclockwise_arc = ArcSpan(
                entry_point,
                exit_point,
                center,
                clockwise=False,
            )
            clockwise_arc = ArcSpan(
                entry_point,
                exit_point,
                center,
                clockwise=True,
            )
            clockwise = (
                clockwise_arc.point_at(0.5).distance_to(corner)
                < counterclockwise_arc.point_at(0.5).distance_to(corner)
            )
            tangent_points = ()
            warnings.append(
                ModifierWarning(
                    "SLOT_WIDTH_NOT_VERIFIED",
                    "Dogbone criado geometricamente; a largura oposta da ranhura ainda deve ser validada.",
                )
            )
        else:
            side = str(tbone_side).lower()
            if side == "auto":
                side = "incoming" if previous.length() >= following.length() else "outgoing"
            if side not in ("incoming", "outgoing"):
                raise ValueError("tbone_side must be auto, incoming or outgoing")
            # A T-bone is a semicircle whose centre lies on the selected edge.
            # Its endpoint at the theoretical corner is tangent to the other
            # edge.  The selected line is clipped by one diameter.
            selected_length = previous.length() if side == "incoming" else following.length()
            if selected_length <= radius * 2.0 + tolerance:
                raise ModifierError("selected span is shorter than the relief diameter")
            if side == "incoming":
                center = corner - incoming * radius
                entry_point = corner - incoming * (radius * 2.0)
                exit_point = corner
            else:
                center = corner + outgoing * radius
                entry_point = corner
                exit_point = corner + outgoing * (radius * 2.0)
            selected_side = side
            clockwise = orientation < 0.0 if role == "inner" else orientation > 0.0
            tangent_points = (corner,)

        spans[previous_index] = previous.with_end(entry_point)
        spans[next_index] = following.with_start(exit_point)
        relief_arc = ArcSpan(
            entry_point,
            exit_point,
            center,
            clockwise=clockwise,
        )
        result_spans = _insert_between_corner_spans(
            spans,
            node_index,
            (relief_arc,),
        )
        construction = (
            corner,
            entry_point,
            center,
            relief_arc.point_at(0.5),
            exit_point,
        )
        details = {
            "kind": kind.value,
            "source_path_id": path.id,
            "source_node_id": node_id,
            "radius_mm": radius,
            "original_corner": list(corner.to_tuple()),
            "center": list(center.to_tuple()),
            "placement_side": selected_side,
            "contour_role": role,
            "trim_points": [
                list(entry_point.to_tuple()),
                list(exit_point.to_tuple()),
            ],
            "trim_distance_mm": entry_point.distance_to(corner),
            "tangent_points": [list(point.to_tuple()) for point in tangent_points],
            "arc_sweep_degrees": math.degrees(relief_arc.sweep_angle),
            "relief_arc_id": relief_arc.id,
            "trimmed_span_ids": [previous.id, following.id],
            "continuous_relief": True,
        }

    metadata = _modifier_metadata(path, "corner_fillet", details)
    if kind is not FilletKind.NORMAL:
        # Remove the obsolete prototype marker if an old document is edited:
        # new reliefs never rely on a self-touch validation exception.
        metadata.pop("intentional_relief_touch_points", None)
    result = PathEntity(
        id=path.id,
        layer_id=path.layer_id,
        spans=result_spans,
        closed=True,
        node_ids=_stable_node_ids(path, result_spans, True),
        metadata=metadata,
    )
    return ModifierPreview(
        "corner_fillet",
        (path,),
        (result,),
        details,
        construction,
        tuple(warnings),
    )


def preview_dogbone(
    path: PathEntity,
    node_id: str,
    radius: float,
    **kwargs,
) -> ModifierPreview:
    return preview_corner_fillet(path, node_id, radius, kind=FilletKind.DOGBONE, **kwargs)


def preview_tbone(
    path: PathEntity,
    node_id: str,
    radius: float,
    *,
    side: str = "auto",
    **kwargs,
) -> ModifierPreview:
    return preview_corner_fillet(
        path,
        node_id,
        radius,
        kind=FilletKind.TBONE,
        tbone_side=side,
        **kwargs,
    )


def _auto_rejection_code(message: str) -> str:
    lowered = message.lower()
    if "external corner" in lowered:
        return "EXTERNAL_CORNER"
    if "90 degree" in lowered:
        return "ANGLE_NOT_90"
    if "line spans" in lowered:
        return "NON_LINEAR_CORNER"
    if "shorter than the relief diameter" in lowered or "does not fit" in lowered:
        return "RADIUS_DOES_NOT_FIT"
    if "closed path" in lowered:
        return "OPEN_PATH"
    return "UNSUPPORTED_CORNER"


def preview_auto_corner_reliefs(
    paths: Iterable[PathEntity],
    radius: float,
    kind: FilletKind | str = FilletKind.DOGBONE,
    *,
    tbone_side: str = "auto",
    angle_tolerance_degrees: float = 1.0,
    contour_roles: Mapping[str, str] | None = None,
) -> ModifierPreview:
    """Preview automatic dogbone/T-bone on explicitly classified contours.

    ``contour_roles`` lets callers supply the role already derived by their
    piece/containment classifier without mutating an entity just to add a tag.
    A mapping entry for a path ID takes precedence over entity metadata.  When
    no entry exists, ``path.metadata['contour_role']`` (or a documented alias)
    remains mandatory.

    For ``inner`` contours only convex 90° vertices are eligible. For ``outer``
    contours only concave 90° re-entrant vertices are eligible. This rule is
    independent of CW/CCW direction and prevents automatic relief on an
    external piece corner.

    Rejections are data, not hidden failures: every rejected corner appears in
    preview metadata and as a warning. If no corner is safe, a
    :class:`NoApplicableCornersError` carries the same report.
    """

    paths = tuple(paths)
    if not paths:
        raise ValueError("automatic relief requires at least one path")
    if len({path.id for path in paths}) != len(paths):
        raise ValueError("automatic relief path IDs must be unique")
    if not all(isinstance(path, PathEntity) for path in paths):
        raise TypeError("automatic relief accepts PathEntity values only")
    kind = kind if isinstance(kind, FilletKind) else FilletKind(str(kind).lower())
    if kind not in (FilletKind.DOGBONE, FilletKind.TBONE):
        raise ValueError("automatic relief kind must be dogbone or tbone")
    if kind is FilletKind.TBONE and str(tbone_side).lower() not in (
        "auto",
        "incoming",
        "outgoing",
    ):
        raise ValueError("tbone_side must be auto, incoming or outgoing")
    radius = float(radius)
    if radius <= DEFAULT_EPSILON:
        raise ModifierError("automatic relief radius must be positive")
    if float(angle_tolerance_degrees) < 0.0:
        raise ValueError("angle tolerance cannot be negative")
    if contour_roles is not None and not isinstance(contour_roles, Mapping):
        raise TypeError("contour_roles must be a mapping of path ID to inner/outer")
    contour_roles = contour_roles or {}
    for path_id, declared_role in contour_roles.items():
        if _normalize_contour_role(declared_role) not in ("inner", "outer"):
            raise ValueError(
                "contour_roles[%r] must be inner or outer" % str(path_id)
            )

    applied = []
    rejected = []
    warnings = []
    construction_points = []
    original_modified = []
    results = []
    resolved_roles = {}
    role_sources = {}

    for original in paths:
        if original.id in contour_roles:
            role = _normalize_contour_role(contour_roles[original.id])
            role_source = "contour_roles"
        else:
            role = _path_contour_role(original)
            role_source = "metadata"
        if role is None:
            rejection = {
                "path_id": original.id,
                "node_id": None,
                "point": None,
                "code": "ROLE_NOT_DECLARED",
                "reason": "contour_role inner/outer is required for automatic relief",
            }
            rejected.append(rejection)
            warnings.append(
                ModifierWarning(
                    rejection["code"],
                    "%s: %s" % (original.id, rejection["reason"]),
                )
            )
            continue
        resolved_roles[original.id] = role
        role_sources[original.id] = role_source
        if not original.closed:
            rejection = {
                "path_id": original.id,
                "node_id": None,
                "point": None,
                "code": "OPEN_PATH",
                "reason": "automatic relief requires a closed contour",
            }
            rejected.append(rejection)
            warnings.append(
                ModifierWarning(
                    rejection["code"],
                    "%s: %s" % (original.id, rejection["reason"]),
                )
            )
            continue

        current = original
        path_applied = []
        original_nodes = tuple(original.nodes())
        for node in original_nodes:
            try:
                corner_preview = preview_corner_fillet(
                    current,
                    node.id,
                    radius,
                    kind=kind,
                    contour_role=role,
                    tbone_side=tbone_side,
                    angle_tolerance_degrees=angle_tolerance_degrees,
                )
            except (ModifierError, KeyError, ValueError) as error:
                reason = str(error)
                rejection = {
                    "path_id": original.id,
                    "node_id": node.id,
                    "point": list(node.point.to_tuple()),
                    "code": _auto_rejection_code(reason),
                    "reason": reason,
                }
                rejected.append(rejection)
                warnings.append(
                    ModifierWarning(
                        rejection["code"],
                        "%s/%s: %s" % (original.id, node.id, reason),
                    )
                )
                continue
            current = corner_preview.result_entities[0]
            record = {
                "path_id": original.id,
                "node_id": node.id,
                "point": list(node.point.to_tuple()),
                "kind": kind.value,
                "radius_mm": radius,
                "placement_side": corner_preview.metadata.get("placement_side"),
                "center": corner_preview.metadata.get("center"),
                "trim_points": corner_preview.metadata.get("trim_points"),
                "tangent_points": corner_preview.metadata.get("tangent_points"),
                "arc_sweep_degrees": corner_preview.metadata.get(
                    "arc_sweep_degrees"
                ),
                "relief_arc_id": corner_preview.metadata.get("relief_arc_id"),
                "continuous_relief": corner_preview.metadata.get(
                    "continuous_relief", False
                ),
                "contour_role": role,
                "contour_role_source": role_source,
            }
            applied.append(record)
            path_applied.append(record)
            construction_points.extend(corner_preview.construction_points)
            warnings.extend(corner_preview.warnings)

        if path_applied:
            batch_details = {
                "kind": kind.value,
                "radius_mm": radius,
                "contour_role": role,
                "contour_role_source": role_source,
                "applied_node_ids": [item["node_id"] for item in path_applied],
                "applied_count": len(path_applied),
            }
            current = PathEntity(
                id=current.id,
                layer_id=current.layer_id,
                spans=current.spans,
                closed=current.closed,
                node_ids=current.node_ids,
                metadata=_modifier_metadata(
                    current, "auto_corner_reliefs", batch_details
                ),
            )
            original_modified.append(original)
            results.append(current)

    metadata = {
        "kind": kind.value,
        "radius_mm": radius,
        "tbone_side": str(tbone_side).lower(),
        "angle_tolerance_degrees": float(angle_tolerance_degrees),
        "applied": applied,
        "rejected": rejected,
        "applied_count": len(applied),
        "rejected_count": len(rejected),
        "modified_path_ids": [path.id for path in original_modified],
        "resolved_contour_roles": resolved_roles,
        "contour_role_sources": role_sources,
    }
    if not applied:
        raise NoApplicableCornersError(
            "no safe internal 90 degree corner accepted automatic relief",
            metadata,
            warnings,
        )
    return ModifierPreview(
        "auto_corner_reliefs",
        tuple(original_modified),
        tuple(results),
        metadata,
        tuple(construction_points),
        tuple(warnings),
    )


@dataclass(frozen=True, slots=True)
class GeometryProjection:
    point: Vec2
    distance: float
    parameter: float
    geometry_id: str
    exact: bool = True


def project_point_to_geometry(point: Vec2, geometry: Any) -> GeometryProjection:
    """Project to exact geometry supported by the current span model.

    A non-circular ellipse is deliberately rejected: although its closest point
    can be found numerically, splitting it would require an exact elliptic-arc
    span which is not part of schema v1.  A circular ``EllipseEntity`` is exact
    and is therefore supported.
    """

    if isinstance(geometry, (LineSpan, ArcSpan)):
        nearest = geometry.nearest_point(point)
        return GeometryProjection(
            nearest.point,
            nearest.distance,
            nearest.parameter,
            geometry.id,
        )
    if isinstance(geometry, CircleEntity) or (
        isinstance(geometry, EllipseEntity)
        and abs(geometry.radius_x - geometry.radius_y)
        <= max(DEFAULT_EPSILON, geometry.radius_x * 1.0e-10)
    ):
        radius = geometry.radius if isinstance(geometry, CircleEntity) else geometry.radius_x
        relative = point - geometry.center
        if relative.length() <= DEFAULT_EPSILON:
            raise ModifierError("projection from the circle centre is ambiguous")
        projected = geometry.center + relative.normalized() * radius
        angle = (projected - geometry.center).angle() % (2.0 * math.pi)
        return GeometryProjection(
            projected,
            point.distance_to(projected),
            angle / (2.0 * math.pi),
            geometry.id,
        )
    if isinstance(geometry, EllipseEntity):
        raise ModifierError(
            "non-circular ellipse cannot be split exactly in schema v1"
        )
    raise ModifierError("unsupported projection target %s" % type(geometry).__name__)


def _target_path_projection(
    point: Vec2,
    target: PathEntity,
    target_span_id: str | None,
    tolerance: float,
):
    candidates = []
    for index, span in enumerate(target.spans):
        if target_span_id is not None and span.id != target_span_id:
            continue
        if not isinstance(span, (LineSpan, ArcSpan)):
            if target_span_id == span.id:
                raise ModifierError("selected target span is not line or circular arc")
            continue
        projection = project_point_to_geometry(point, span)
        candidates.append((projection.distance, index, span, projection))
    if not candidates:
        if target_span_id is not None:
            raise KeyError("unknown supported target span ID %s" % target_span_id)
        raise ModifierError("target path has no exact line/arc span")
    candidates.sort(key=lambda value: (value[0], value[2].id))
    best = candidates[0]
    if target_span_id is None and len(candidates) > 1:
        second = candidates[1]
        if abs(best[0] - second[0]) <= tolerance and not best[3].point.almost_equals(
            second[3].point, tolerance
        ):
            raise ModifierError(
                "two target spans are equally near; select target_span_id explicitly"
            )
    return best[1], best[2], best[3]


def _split_target_path_at_projection(
    target: PathEntity,
    span_index: int,
    span,
    projection: GeometryProjection,
    tolerance: float,
):
    parameter_tolerance = tolerance / max(span.length(), tolerance)
    if projection.parameter <= parameter_tolerance:
        node_index = span_index
        return target, target.node_ids[node_index], False, 0.0
    if projection.parameter >= 1.0 - parameter_tolerance:
        node_index = (span_index + 1) % len(target.node_ids)
        return target, target.node_ids[node_index], False, 1.0
    updated = target.with_span_replaced(span.id, span.split(projection.parameter))
    split_node_index = span_index + 1
    return updated, updated.node_ids[split_node_index], True, projection.parameter


def _circle_as_split_path(
    target: CircleEntity | EllipseEntity,
    split_point: Vec2,
    operation: str,
) -> PathEntity:
    radius = target.radius if isinstance(target, CircleEntity) else target.radius_x
    opposite = target.center * 2.0 - split_point
    first = ArcSpan(split_point, opposite, target.center, clockwise=False)
    second = ArcSpan(opposite, split_point, target.center, clockwise=False)
    right_handle = target.center + Vec2(radius, 0.0)
    split_node_id = (
        getattr(target, "radius_node_id", None)
        if split_point.almost_equals(right_handle, DEFAULT_EPSILON)
        else None
    ) or new_id("node")
    metadata = dict(target.metadata)
    metadata.update(
        {
            "converted_from": target.type,
            "conversion_reason": operation,
            "source_center": list(target.center.to_tuple()),
            "source_radius_mm": radius,
        }
    )
    return PathEntity(
        id=target.id,
        layer_id=target.layer_id,
        spans=(first, second),
        closed=True,
        node_ids=(split_node_id, new_id("node")),
        metadata=metadata,
    )


def preview_connect_endpoint_to_geometry(
    source_path: PathEntity,
    endpoint: str,
    target: PathEntity | CircleEntity | EllipseEntity,
    *,
    target_span_id: str | None = None,
    tolerance: float = 0.2,
) -> ModifierPreview:
    """Project one open endpoint and split the explicitly selected target.

    Connecting to an interior point of a contour reports
    ``creates_branch=True`` and keeps the explicitly split entities.  If the
    projection lands on an open target endpoint, the two paths are merged into
    one ordered open path because no branch is required.  Use
    :func:`preview_splice_open_path_to_contour` when both source endpoints must
    be merged with a closed contour without a branch.
    """

    if not isinstance(source_path, PathEntity) or source_path.closed:
        raise ModifierError("source must be an open PathEntity")
    if source_path.id == target.id:
        raise ModifierError("source and target must be different entities")
    endpoint = str(endpoint).lower()
    if endpoint not in ("start", "end"):
        raise ValueError("endpoint must be 'start' or 'end'")
    tolerance = float(tolerance)
    if tolerance < 0.0:
        raise ValueError("connection tolerance cannot be negative")
    source_node_id = (
        source_path.node_ids[0] if endpoint == "start" else source_path.node_ids[-1]
    )
    source_point = source_path.node_position(source_node_id)

    target_span = None
    if isinstance(target, PathEntity):
        span_index, target_span, projection = _target_path_projection(
            source_point, target, target_span_id, tolerance
        )
        updated_target, target_node_id, did_split, parameter = _split_target_path_at_projection(
            target, span_index, target_span, projection, tolerance
        )
        if not did_split:
            # When the nearest point falls inside the endpoint tolerance, snap
            # to the existing logical node itself.  Keeping the unsnapped point
            # would look connected while still producing two degree-1 nodes.
            existing_point = target.node_position(target_node_id)
            projection = GeometryProjection(
                existing_point,
                source_point.distance_to(existing_point),
                parameter,
                target_span.id,
            )
        target_was_endpoint = (
            not target.closed
            and not did_split
            and target_node_id in (target.node_ids[0], target.node_ids[-1])
        )
        creates_branch = not target_was_endpoint
    else:
        if target_span_id is not None:
            raise ValueError("target_span_id applies only to PathEntity")
        projection = project_point_to_geometry(source_point, target)
        updated_target = _circle_as_split_path(
            target, projection.point, "connect_endpoint_to_geometry"
        )
        target_node_id = updated_target.node_ids[0]
        did_split = True
        parameter = projection.parameter
        creates_branch = True

    if projection.distance > tolerance:
        raise ModifierError(
            "endpoint is %.6g mm from target (tolerance %.6g mm)"
            % (projection.distance, tolerance)
        )
    updated_source = source_path.with_node_moved(source_node_id, projection.point)
    details = {
        "source_path_id": source_path.id,
        "source_endpoint": endpoint,
        "source_node_id": source_node_id,
        "target_entity_id": target.id,
        "target_span_id": target_span.id if target_span is not None else target.id,
        "target_node_id": target_node_id,
        "target_parameter": parameter,
        "target_split": did_split,
        "projection_point": list(projection.point.to_tuple()),
        "distance_mm": projection.distance,
        "tolerance_mm": tolerance,
        "creates_branch": creates_branch,
        "result": "branch" if creates_branch else "joined_open_path",
    }
    updated_source = PathEntity(
        id=updated_source.id,
        layer_id=updated_source.layer_id,
        spans=updated_source.spans,
        closed=False,
        node_ids=updated_source.node_ids,
        metadata=_modifier_metadata(
            source_path, "connect_endpoint_to_geometry", details
        ),
    )
    if not creates_branch and isinstance(target, PathEntity):
        left = updated_source.reversed() if endpoint == "start" else updated_source
        right = (
            updated_target
            if target_node_id == updated_target.node_ids[0]
            else updated_target.reversed()
        )
        joined = PathEntity(
            id=source_path.id,
            layer_id=source_path.layer_id,
            spans=left.spans + right.spans,
            closed=False,
            node_ids=left.node_ids + right.node_ids[1:],
            metadata=_modifier_metadata(
                source_path, "connect_endpoint_to_geometry", details
            ),
        )
        return ModifierPreview(
            "connect_endpoint_to_geometry",
            (source_path, target),
            (joined,),
            details,
            (source_point, projection.point),
            (
                ModifierWarning(
                    "TARGET_CONSUMED",
                    "O caminho alvo aberto será incorporado ao caminho de origem.",
                ),
            ),
        )

    target_metadata = _modifier_metadata(
        updated_target, "split_for_endpoint_connection", details
    )
    updated_target = PathEntity(
        id=updated_target.id,
        layer_id=updated_target.layer_id,
        spans=updated_target.spans,
        closed=updated_target.closed,
        node_ids=updated_target.node_ids,
        metadata=target_metadata,
    )
    warnings = ()
    if creates_branch:
        warnings = (
            ModifierWarning(
                "CREATES_BRANCH",
                "A conexão cria nó de grau 3; use splice para obter um contorno fechado único.",
            ),
        )
    return ModifierPreview(
        "connect_endpoint_to_geometry",
        (source_path, target),
        (updated_source, updated_target),
        details,
        (source_point, projection.point),
        warnings,
    )


def _slice_exact_span(span, start_parameter: float, end_parameter: float, used_ids: set):
    if end_parameter - start_parameter <= DEFAULT_EPSILON:
        return None
    start = span.point_at(start_parameter)
    end = span.point_at(end_parameter)
    span_id = span.id if span.id not in used_ids else new_id("span")
    used_ids.add(span_id)
    if isinstance(span, LineSpan):
        return LineSpan(start, end, id=span_id)
    if isinstance(span, ArcSpan):
        return ArcSpan(start, end, span.center, span.clockwise, id=span_id)
    raise ModifierError("splice supports line and circular arc target spans only")


def _closed_path_route(
    path: PathEntity,
    start_position: Tuple[int, float],
    end_position: Tuple[int, float],
) -> Tuple:
    """Return exact spans following path order from start position to end."""

    start_index, start_parameter = start_position
    end_index, end_parameter = end_position
    used_ids = set()
    result = []
    index = start_index
    parameter = start_parameter
    first_iteration = True
    while True:
        span = path.spans[index]
        if index == end_index and (not first_iteration or start_index != end_index):
            sliced = _slice_exact_span(span, parameter, end_parameter, used_ids)
            if sliced is not None:
                result.append(sliced)
            break
        if index == end_index and start_index == end_index and start_parameter < end_parameter:
            sliced = _slice_exact_span(span, start_parameter, end_parameter, used_ids)
            if sliced is not None:
                result.append(sliced)
            break
        sliced = _slice_exact_span(span, parameter, 1.0, used_ids)
        if sliced is not None:
            result.append(sliced)
        index = (index + 1) % len(path.spans)
        parameter = 0.0
        first_iteration = False
        if index == end_index:
            sliced = _slice_exact_span(path.spans[index], 0.0, end_parameter, used_ids)
            if sliced is not None:
                result.append(sliced)
            break
        if index == start_index:
            raise ModifierError("splice route unexpectedly traversed a full contour")
    return tuple(result)


def _canonical_closed_projection(
    target: PathEntity,
    span_index: int,
    span,
    projection: GeometryProjection,
    source_point: Vec2,
    tolerance: float,
):
    parameter_tolerance = tolerance / max(span.length(), tolerance)
    if projection.parameter <= parameter_tolerance:
        point = span.start
        return (
            (span_index, 0.0),
            GeometryProjection(
                point, source_point.distance_to(point), 0.0, span.id
            ),
        )
    if projection.parameter >= 1.0 - parameter_tolerance:
        next_index = (span_index + 1) % len(target.spans)
        point = span.end
        return (
            (next_index, 0.0),
            GeometryProjection(
                point,
                source_point.distance_to(point),
                0.0,
                target.spans[next_index].id,
            ),
        )
    return (span_index, projection.parameter), projection


def _route_length(route: Sequence) -> float:
    return sum(span.length() for span in route)


def _route_distance_to_point(route: Sequence, point: Vec2) -> float:
    return min(span.nearest_point(point).distance for span in route)


def _choose_route(
    first: Tuple,
    second: Tuple,
    choice: str,
    guide_point: Vec2 | None,
    tolerance: float,
):
    first_length = _route_length(first)
    second_length = _route_length(second)
    if guide_point is not None:
        first_distance = _route_distance_to_point(first, guide_point)
        second_distance = _route_distance_to_point(second, guide_point)
        if abs(first_distance - second_distance) <= tolerance:
            raise ModifierError("guide point does not distinguish the two target routes")
        return (
            (first, first_length, "guide-first")
            if first_distance < second_distance
            else (second, second_length, "guide-second")
        )
    choice = str(choice).lower()
    if choice not in ("short", "long"):
        raise ValueError("route must be 'short' or 'long', or provide guide_point")
    if abs(first_length - second_length) <= tolerance:
        raise ModifierError("target routes have equal length; provide guide_point")
    if choice == "short":
        return (
            (first, first_length, "short-first")
            if first_length < second_length
            else (second, second_length, "short-second")
        )
    return (
        (first, first_length, "long-first")
        if first_length > second_length
        else (second, second_length, "long-second")
    )


def _multiple_stable_node_ids(originals: Sequence[VectorEntity], spans: Sequence) -> Tuple[str, ...]:
    available = []
    for entity in originals:
        nodes_method = getattr(entity, "nodes", None)
        if callable(nodes_method):
            available.extend((node.id, node.point) for node in nodes_method())
    result = []
    used = set()
    for span in spans:
        match = next(
            (
                node_id
                for node_id, point in available
                if node_id not in used
                and span.start.almost_equals(point, DEFAULT_EPSILON)
            ),
            None,
        )
        node_id = match or new_id("node")
        used.add(node_id)
        result.append(node_id)
    return tuple(result)


def _circle_routes(
    target: CircleEntity | EllipseEntity,
    route_start: Vec2,
    route_end: Vec2,
):
    center = target.center
    counterclockwise = (ArcSpan(route_start, route_end, center, False),)
    clockwise = (ArcSpan(route_start, route_end, center, True),)
    return counterclockwise, clockwise


def preview_splice_open_path_to_contour(
    source_path: PathEntity,
    target: PathEntity | CircleEntity | EllipseEntity,
    *,
    start_target_span_id: str | None = None,
    end_target_span_id: str | None = None,
    route: str = "short",
    guide_point: Vec2 | None = None,
    tolerance: float = 0.2,
) -> ModifierPreview:
    """Close an open path using one explicitly selected target-contour route.

    Both source endpoints are projected independently.  The target is consumed
    and exactly one ordered closed ``PathEntity`` remains; no degree-3 branch is
    created.  ``route='short'``/``'long'`` selects by exact route length, while
    ``guide_point`` selects the route geometrically nearest that point.
    """

    if not isinstance(source_path, PathEntity) or source_path.closed:
        raise ModifierError("source must be an open PathEntity")
    if source_path.id == target.id:
        raise ModifierError("source and target must be different entities")
    tolerance = float(tolerance)
    if tolerance < 0.0:
        raise ValueError("splice tolerance cannot be negative")
    source_start = source_path.start
    source_end = source_path.end

    if isinstance(target, PathEntity):
        if not target.closed:
            raise ModifierError("splice target PathEntity must be closed")
        start_index, start_span, start_projection = _target_path_projection(
            source_start, target, start_target_span_id, tolerance
        )
        end_index, end_span, end_projection = _target_path_projection(
            source_end, target, end_target_span_id, tolerance
        )
        start_position, start_projection = _canonical_closed_projection(
            target,
            start_index,
            start_span,
            start_projection,
            source_start,
            tolerance,
        )
        end_position, end_projection = _canonical_closed_projection(
            target,
            end_index,
            end_span,
            end_projection,
            source_end,
            tolerance,
        )
    else:
        start_projection = project_point_to_geometry(source_start, target)
        end_projection = project_point_to_geometry(source_end, target)

    if start_projection.distance > tolerance or end_projection.distance > tolerance:
        raise ModifierError(
            "source endpoints are %.6g/%.6g mm from target (tolerance %.6g mm)"
            % (start_projection.distance, end_projection.distance, tolerance)
        )
    if start_projection.point.almost_equals(end_projection.point, tolerance):
        raise ModifierError("both source endpoints project to the same target point")
    if isinstance(target, PathEntity):
        forward_end_to_start = _closed_path_route(
            target, end_position, start_position
        )
        forward_start_to_end = _closed_path_route(
            target, start_position, end_position
        )
        reverse_complement = tuple(
            span.reversed() for span in reversed(forward_start_to_end)
        )
        routes = (forward_end_to_start, reverse_complement)
    else:
        routes = _circle_routes(
            target, end_projection.point, start_projection.point
        )
    selected_route, route_length, route_selection = _choose_route(
        routes[0], routes[1], route, guide_point, tolerance
    )

    moved = source_path.with_node_moved(
        source_path.node_ids[0], start_projection.point
    )
    moved = moved.with_node_moved(
        moved.node_ids[-1], end_projection.point
    )
    spans = moved.spans + tuple(selected_route)
    details = {
        "source_path_id": source_path.id,
        "target_entity_id": target.id,
        "start_projection": list(start_projection.point.to_tuple()),
        "end_projection": list(end_projection.point.to_tuple()),
        "start_distance_mm": start_projection.distance,
        "end_distance_mm": end_projection.distance,
        "tolerance_mm": tolerance,
        "route_request": "guide" if guide_point is not None else str(route).lower(),
        "route_selection": route_selection,
        "route_length_mm": route_length,
        "target_consumed": True,
        "creates_branch": False,
        "result": "single_closed_path",
    }
    result = PathEntity(
        id=source_path.id,
        layer_id=source_path.layer_id,
        spans=spans,
        closed=True,
        node_ids=_multiple_stable_node_ids((moved, target), spans),
        metadata=_modifier_metadata(
            source_path, "splice_open_path_to_contour", details
        ),
    )
    return ModifierPreview(
        "splice_open_path_to_contour",
        (source_path, target),
        (result,),
        details,
        (
            source_start,
            start_projection.point,
            source_end,
            end_projection.point,
        ),
        (
            ModifierWarning(
                "TARGET_CONSUMED",
                "O contorno alvo será consumido pelo caminho fechado resultante.",
            ),
        ),
    )
