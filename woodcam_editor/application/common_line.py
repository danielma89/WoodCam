"""Pure planning for straight-segment common-line cutting.

This module deliberately knows nothing about Qt, FreeCAD or CAM moves.  It
receives closed polygonal centre-lines and separates their remaining perimeter
from boundary spans owned by exactly two contours.  A shared span is emitted
once, with both owners recorded.

The caller remains responsible for cutter compensation.  In particular, raw
part outlines that merely touch are *not* dimensionally equivalent to two
already compensated centre-lines.  The planner only answers the geometric
question "which supplied line segments coincide?" and refuses unsafe or
ambiguous topology before a toolpath can consume the result.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import itertools
import math
from typing import Iterable, Sequence, Tuple

from woodcam_editor.domain.primitives import Vec2


MAX_PHYSICAL_COINCIDENCE_TOLERANCE = 1.0e-3
from woodcam_editor.geometry.math2d import PointLocation, point_in_polygon, signed_area


def _numerical_epsilon(tolerance: float) -> float:
    """Precision floor that must never inherit the user's match tolerance."""

    return min(abs(float(tolerance)), 1.0e-7)


def _parameter_epsilon(length: float, tolerance: float) -> float:
    """Merge nearby split parameters without collapsing a complete edge.

    Common-line tolerance answers whether *different* borders coincide.  It
    is not a minimum machinable segment length.  In particular, compensated
    Dogbone/T-bone arcs can contain chords shorter than a user-facing 0.2 mm
    match tolerance.  Capping the dimensionless epsilon below half an edge
    keeps the mandatory 0 -> 1 interval while still suppressing tiny split
    slivers created by nearby overlap endpoints.
    """

    length = abs(float(length))
    tolerance = abs(float(tolerance))
    return min(0.25, tolerance / max(length, tolerance, 1.0e-12))


def _angular_epsilon(first_length: float, second_length: float, tolerance: float) -> float:
    """Scale-independent parallelism threshold for two non-null segments."""

    return max(
        1.0e-9,
        min(
            1.0e-3,
            abs(float(tolerance))
            / max(abs(float(first_length)), abs(float(second_length)), abs(float(tolerance)), 1.0e-12),
        ),
    )


class CommonLineIssueCode(str, Enum):
    """Stable machine-readable reasons why a plan is not safe to execute."""

    INVALID_CONTOUR = "invalid_contour"
    DUPLICATE_CONTOUR = "duplicate_contour"
    SELF_INTERSECTION = "self_intersection"
    CROSSING = "crossing"
    AREA_OVERLAP = "area_overlap"
    AMBIGUOUS_SHARED_LINE = "ambiguous_shared_line"
    INSUFFICIENT_CLEARANCE = "insufficient_clearance"


@dataclass(frozen=True, slots=True)
class CommonLineContour:
    """A named closed polygon.

    ``points`` may omit or repeat the closing point.  Consecutive duplicate and
    redundant collinear vertices are normalised by :func:`plan_common_line_cut`
    without mutating this source object.
    """

    contour_id: str
    points: Tuple[Vec2, ...]

    def __init__(self, contour_id: str, points: Iterable[Vec2 | Sequence[float]]):
        converted = []
        for point in points:
            converted.append(
                point if isinstance(point, Vec2) else Vec2.from_sequence(point)
            )
        object.__setattr__(self, "contour_id", str(contour_id))
        object.__setattr__(self, "points", tuple(converted))


@dataclass(frozen=True, slots=True)
class CommonLineSegment:
    """One atomic or coalesced straight cut span."""

    start: Vec2
    end: Vec2
    owner_ids: Tuple[str, ...]
    source_segments: Tuple[Tuple[str, int], ...] = ()

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)

    @property
    def shared(self) -> bool:
        return len(self.owner_ids) == 2


@dataclass(frozen=True, slots=True)
class CommonLineCutPath:
    """One edge-disjoint open path ready for the CAM adapter.

    ``tab`` means that this complete (already split) interval must remain at
    tab height on the final pass.  This low-level builder chooses owner-only
    tabs by default.  The sheet-wide planner may subsequently split a shared
    interval into a logical ``SharedTab`` when its RetentionGraph proves that
    indirect retention is required and safe.
    """

    points: Tuple[Vec2, ...]
    owner_ids: Tuple[str, ...]
    shared: bool = False
    tab: bool = False


@dataclass(frozen=True, slots=True)
class CommonLineCutTrail:
    """A continuous edge-disjoint trail with per-edge machining state.

    Tabs are attributes of edges, not separate toolpaths.  This distinction is
    essential for a router: the cutter follows the connected contour and only
    changes Z over the retained span instead of retracting and plunging at
    every tab boundary.
    """

    points: Tuple[Vec2, ...]
    edge_tabs: Tuple[bool, ...]
    edge_shared: Tuple[bool, ...]
    edge_owner_ids: Tuple[Tuple[str, ...], ...]

    def __post_init__(self):
        edge_count = max(0, len(self.points) - 1)
        if not self.points or edge_count == 0:
            raise ValueError("uma trilha de linha comum precisa de pelo menos uma aresta")
        if not (
            len(self.edge_tabs)
            == len(self.edge_shared)
            == len(self.edge_owner_ids)
            == edge_count
        ):
            raise ValueError("os estados da trilha precisam corresponder às arestas")

    @property
    def length(self) -> float:
        return sum(
            self.points[index].distance_to(self.points[index + 1])
            for index in range(len(self.points) - 1)
        )


@dataclass(frozen=True, slots=True)
class CommonLineIssue:
    code: CommonLineIssueCode
    message: str
    contour_ids: Tuple[str, ...] = ()
    points: Tuple[Vec2, ...] = ()


class CommonLinePlanningError(ValueError):
    """Raised by :meth:`CommonLinePlan.require_valid` for a blocked plan."""

    def __init__(self, issues: Sequence[CommonLineIssue]):
        self.issues = tuple(issues)
        super(CommonLinePlanningError, self).__init__(
            "; ".join(issue.message for issue in self.issues)
            or "plano de linha comum inválido"
        )


@dataclass(frozen=True, slots=True)
class CommonLinePlan:
    """Validated result ready to be converted into a preview/toolpath."""

    perimeter_segments: Tuple[CommonLineSegment, ...]
    shared_segments: Tuple[CommonLineSegment, ...]
    touch_points: Tuple[Vec2, ...]
    issues: Tuple[CommonLineIssue, ...]
    tolerance: float

    @property
    def is_valid(self) -> bool:
        return not self.issues

    @property
    def has_common_lines(self) -> bool:
        return bool(self.shared_segments)

    @property
    def total_cut_length(self) -> float:
        return sum(
            segment.length
            for segment in self.perimeter_segments + self.shared_segments
        )

    def perimeter_for(self, contour_id: str) -> Tuple[CommonLineSegment, ...]:
        contour_id = str(contour_id)
        return tuple(
            segment
            for segment in self.perimeter_segments
            if segment.owner_ids == (contour_id,)
        )

    def require_valid(self) -> "CommonLinePlan":
        if self.issues:
            raise CommonLinePlanningError(self.issues)
        return self


@dataclass(frozen=True, slots=True)
class _NormalisedContour:
    contour_id: str
    points: Tuple[Vec2, ...]
    area: float


@dataclass(frozen=True, slots=True)
class _SegmentRef:
    contour_id: str
    contour_index: int
    segment_index: int
    start: Vec2
    end: Vec2
    area: float

    @property
    def vector(self) -> Vec2:
        return self.end - self.start

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)


@dataclass(frozen=True, slots=True)
class _SegmentRelation:
    kind: str
    point: Vec2 | None = None
    overlap_start: Vec2 | None = None
    overlap_end: Vec2 | None = None
    a0: float = 0.0
    a1: float = 0.0
    b0: float = 0.0
    b1: float = 0.0


@dataclass(frozen=True, slots=True)
class _Atom:
    start: Vec2
    end: Vec2
    owner_id: str
    source_segment: Tuple[str, int]


def _issue(
    code: CommonLineIssueCode,
    message: str,
    contour_ids: Iterable[str] = (),
    points: Iterable[Vec2] = (),
) -> CommonLineIssue:
    return CommonLineIssue(
        code=code,
        message=message,
        contour_ids=tuple(sorted(set(str(value) for value in contour_ids))),
        points=tuple(points),
    )


def _dedupe_points(points: Iterable[Vec2], tolerance: float) -> Tuple[Vec2, ...]:
    result = []
    for point in points:
        if not any(point.almost_equals(existing, tolerance) for existing in result):
            result.append(point)
    return tuple(result)


def _normalise_ring(
    contour: CommonLineContour,
    tolerance: float,
) -> _NormalisedContour | CommonLineIssue:
    contour_id = contour.contour_id.strip()
    if not contour_id:
        return _issue(
            CommonLineIssueCode.INVALID_CONTOUR,
            "Contorno sem identificador.",
        )

    points = list(contour.points)
    # Matching tolerance belongs to comparisons between physical borders. It
    # must not simplify the source geometry: values such as 0.2 mm otherwise
    # flatten the short chords of a compensated round corner and can erase an
    # exact straight SharedEdge. Only numerical duplicates/collinearity are
    # removed here.
    normalisation_epsilon = min(tolerance, 1.0e-7)
    while len(points) > 1 and points[0].almost_equals(
        points[-1], normalisation_epsilon
    ):
        points.pop()

    consecutive = []
    for point in points:
        if not consecutive or not point.almost_equals(
            consecutive[-1], normalisation_epsilon
        ):
            consecutive.append(point)
    points = consecutive
    if len(points) > 1 and points[0].almost_equals(
        points[-1], normalisation_epsilon
    ):
        points.pop()

    # Remove only a middle point that lies between its neighbours.  A reversal
    # (A -> B -> A) is deliberately retained so self-overlap is diagnosed.
    changed = True
    while changed and len(points) >= 3:
        changed = False
        for index in range(len(points)):
            previous = points[index - 1]
            current = points[index]
            following = points[(index + 1) % len(points)]
            baseline = following - previous
            baseline_length = baseline.length()
            if baseline_length <= tolerance:
                continue
            distance = abs((current - previous).cross(baseline)) / baseline_length
            projection = (current - previous).dot(baseline) / (
                baseline_length * baseline_length
            )
            if (
                distance <= normalisation_epsilon
                and -normalisation_epsilon
                <= projection
                <= 1.0 + normalisation_epsilon
            ):
                del points[index]
                changed = True
                break

    if len(points) < 3:
        return _issue(
            CommonLineIssueCode.INVALID_CONTOUR,
            "O contorno %s precisa de pelo menos três vértices distintos."
            % contour_id,
            (contour_id,),
            points,
        )

    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        if start.distance_to(end) <= normalisation_epsilon:
            return _issue(
                CommonLineIssueCode.INVALID_CONTOUR,
                "O contorno %s contém um segmento nulo." % contour_id,
                (contour_id,),
                (start,),
            )

    area = signed_area(points)
    candidate = _NormalisedContour(contour_id, tuple(points), area)
    candidate_segments = _segments(candidate, 0)
    candidate_segment_bounds = tuple(
        _segment_bounds(segment) for segment in candidate_segments
    )
    self_intersections = []
    # The user-facing common-line tolerance compares *different* borders.
    # Applying 0.2 mm to neighbouring chords of the same flattened arc makes
    # a valid Dogbone look self-touching.  Self-intersection is an exact
    # topological check and therefore uses the capped physical coincidence
    # tolerance already enforced when shared atoms are identified.
    self_intersection_tolerance = min(
        tolerance,
        MAX_PHYSICAL_COINCIDENCE_TOLERANCE,
    )
    for first_index, second_index in _candidate_segment_pairs(
        candidate_segment_bounds,
        tolerance=self_intersection_tolerance,
    ):
        adjacent = (
            second_index == first_index + 1
            or (
                first_index == 0
                and second_index == len(candidate_segments) - 1
            )
        )
        relation = _segment_relation(
            candidate_segments[first_index],
            candidate_segments[second_index],
            self_intersection_tolerance,
        )
        if relation is None or (adjacent and relation.kind == "touch"):
            continue
        if relation.kind == "overlap":
            self_intersections.extend(
                (relation.overlap_start, relation.overlap_end)
            )
        elif relation.point is not None:
            self_intersections.append(relation.point)
    if self_intersections:
        return _issue(
            CommonLineIssueCode.SELF_INTERSECTION,
            "O contorno %s cruza ou sobrepõe a si próprio." % contour_id,
            (contour_id,),
            _dedupe_points(
                (point for point in self_intersections if point is not None),
                tolerance,
            ),
        )
    if abs(area) <= tolerance * tolerance:
        return _issue(
            CommonLineIssueCode.INVALID_CONTOUR,
            "O contorno %s possui área nula ou menor que a tolerância."
            % contour_id,
            (contour_id,),
            points,
        )
    return candidate


def _segments(contour: _NormalisedContour, contour_index: int) -> Tuple[_SegmentRef, ...]:
    return tuple(
        _SegmentRef(
            contour.contour_id,
            contour_index,
            index,
            point,
            contour.points[(index + 1) % len(contour.points)],
            contour.area,
        )
        for index, point in enumerate(contour.points)
    )


def _segment_bounds(segment: _SegmentRef) -> Tuple[float, float, float, float]:
    """Axis-aligned broad-phase bounds for one exact segment test."""

    return (
        min(segment.start.x, segment.end.x),
        min(segment.start.y, segment.end.y),
        max(segment.start.x, segment.end.x),
        max(segment.start.y, segment.end.y),
    )


def _closest_endpoint_pair(a: _SegmentRef, b: _SegmentRef):
    candidates = (
        (a.start.distance_to(b.start), a.start, b.start),
        (a.start.distance_to(b.end), a.start, b.end),
        (a.end.distance_to(b.start), a.end, b.start),
        (a.end.distance_to(b.end), a.end, b.end),
    )
    return min(candidates, key=lambda value: value[0])


def _segment_relation(
    a: _SegmentRef,
    b: _SegmentRef,
    tolerance: float,
) -> _SegmentRelation | None:
    u = a.vector
    v = b.vector
    length_a = a.length
    length_b = b.length
    numerical_epsilon = _numerical_epsilon(tolerance)
    if length_a <= numerical_epsilon or length_b <= numerical_epsilon:
        return None

    unit_a = u / length_a
    unit_b = v / length_b
    distance_b0 = abs((b.start - a.start).cross(unit_a))
    distance_b1 = abs((b.end - a.start).cross(unit_a))
    coincidence_tolerance = min(
        tolerance,
        MAX_PHYSICAL_COINCIDENCE_TOLERANCE,
    )
    angular_tolerance = _angular_epsilon(length_a, length_b, tolerance)
    collinear = (
        abs(unit_a.cross(unit_b)) <= angular_tolerance
        and distance_b0 <= coincidence_tolerance
        and distance_b1 <= coincidence_tolerance
    )

    if collinear:
        projected_b0 = (b.start - a.start).dot(unit_a)
        projected_b1 = (b.end - a.start).dot(unit_a)
        low = max(0.0, min(projected_b0, projected_b1))
        high = min(length_a, max(projected_b0, projected_b1))
        overlap_length = high - low
        if overlap_length > coincidence_tolerance:
            start = a.start + unit_a * low
            end = a.start + unit_a * high
            b_vector_squared = v.length_squared()
            b_start_parameter = (start - b.start).dot(v) / b_vector_squared
            b_end_parameter = (end - b.start).dot(v) / b_vector_squared
            return _SegmentRelation(
                "overlap",
                overlap_start=start,
                overlap_end=end,
                a0=max(0.0, min(1.0, low / length_a)),
                a1=max(0.0, min(1.0, high / length_a)),
                b0=max(0.0, min(1.0, b_start_parameter)),
                b1=max(0.0, min(1.0, b_end_parameter)),
            )
        closest_distance, first, second = _closest_endpoint_pair(a, b)
        if overlap_length >= -tolerance or closest_distance <= tolerance:
            return _SegmentRelation("touch", point=first.lerp(second, 0.5))
        return None

    denominator = u.cross(v)
    if abs(unit_a.cross(unit_b)) <= angular_tolerance:
        closest_distance, first, second = _closest_endpoint_pair(a, b)
        if closest_distance <= tolerance:
            return _SegmentRelation("touch", point=first.lerp(second, 0.5))
        return None

    offset = b.start - a.start
    parameter_a = offset.cross(v) / denominator
    parameter_b = offset.cross(u) / denominator
    epsilon_a = _parameter_epsilon(length_a, tolerance)
    epsilon_b = _parameter_epsilon(length_b, tolerance)
    if (
        -epsilon_a <= parameter_a <= 1.0 + epsilon_a
        and -epsilon_b <= parameter_b <= 1.0 + epsilon_b
    ):
        parameter_a = max(0.0, min(1.0, parameter_a))
        parameter_b = max(0.0, min(1.0, parameter_b))
        point_a = a.start + u * parameter_a
        point_b = b.start + v * parameter_b
        point = point_a.lerp(point_b, 0.5)
        at_endpoint_a = parameter_a <= epsilon_a or parameter_a >= 1.0 - epsilon_a
        at_endpoint_b = parameter_b <= epsilon_b or parameter_b >= 1.0 - epsilon_b
        return _SegmentRelation(
            "touch" if at_endpoint_a or at_endpoint_b else "cross",
            point=point,
            a0=parameter_a,
            b0=parameter_b,
        )

    closest_distance, first, second = _closest_endpoint_pair(a, b)
    if closest_distance <= tolerance:
        return _SegmentRelation("touch", point=first.lerp(second, 0.5))
    return None


def _rings_equal(
    first: _NormalisedContour,
    second: _NormalisedContour,
    tolerance: float,
) -> bool:
    left = first.points
    right = second.points
    if len(left) != len(right):
        return False
    candidates = [
        index for index, point in enumerate(right) if left[0].almost_equals(point, tolerance)
    ]
    for offset in candidates:
        if all(
            left[index].almost_equals(right[(offset + index) % len(right)], tolerance)
            for index in range(len(left))
        ):
            return True
        if all(
            left[index].almost_equals(right[(offset - index) % len(right)], tolerance)
            for index in range(len(left))
        ):
            return True
    return False


def _contour_bounds(contour: _NormalisedContour) -> Tuple[float, float, float, float]:
    return (
        min(point.x for point in contour.points),
        min(point.y for point in contour.points),
        max(point.x for point in contour.points),
        max(point.y for point in contour.points),
    )


def _bounds_may_contact(first, second, tolerance: float) -> bool:
    return not (
        first[2] < second[0] - tolerance
        or second[2] < first[0] - tolerance
        or first[3] < second[1] - tolerance
        or second[3] < first[1] - tolerance
    )


def _candidate_segment_pairs(first_bounds, second_bounds=None, tolerance=0.0):
    """Return only segment pairs whose expanded X/Y bounds can meet.

    A sweep line avoids the previous quadratic Python loop over every chord.
    The returned indices retain the original nested-loop order so diagnostics
    and generated plans remain deterministic.
    """

    same_collection = second_bounds is None
    if same_collection:
        second_bounds = first_bounds

    events = []
    for index, bounds in enumerate(first_bounds):
        events.append((bounds[0] - tolerance, 0, 0, index))
        events.append((bounds[2] + tolerance, 1, 0, index))
    if not same_collection:
        for index, bounds in enumerate(second_bounds):
            events.append((bounds[0] - tolerance, 0, 1, index))
            events.append((bounds[2] + tolerance, 1, 1, index))

    active_first = set()
    active_second = set()
    candidates = set()
    for _x, event_kind, collection, index in sorted(events):
        if event_kind == 1:
            (active_first if collection == 0 else active_second).discard(index)
            continue

        if same_collection:
            for other_index in active_first:
                pair = (min(index, other_index), max(index, other_index))
                if _bounds_may_contact(
                    first_bounds[pair[0]], first_bounds[pair[1]], tolerance
                ):
                    candidates.add(pair)
            active_first.add(index)
            continue

        if collection == 0:
            for other_index in active_second:
                if _bounds_may_contact(
                    first_bounds[index], second_bounds[other_index], tolerance
                ):
                    candidates.add((index, other_index))
            active_first.add(index)
        else:
            for other_index in active_first:
                if _bounds_may_contact(
                    first_bounds[other_index], second_bounds[index], tolerance
                ):
                    candidates.add((other_index, index))
            active_second.add(index)
    return tuple(sorted(candidates))


def _inward_normal(segment: _SegmentRef) -> Vec2:
    direction = segment.vector.normalized()
    left = direction.perpendicular_left()
    return left if segment.area > 0.0 else -left


def _interior_probes(
    contour: _NormalisedContour,
    tolerance: float,
) -> Tuple[Vec2, ...]:
    points = contour.points
    mean = Vec2(
        sum(point.x for point in points) / len(points),
        sum(point.y for point in points) / len(points),
    )
    if point_in_polygon(mean, points, tolerance) == PointLocation.INSIDE:
        return (mean,)

    xs = [point.x for point in points]
    ys = [point.y for point in points]
    diagonal = math.hypot(max(xs) - min(xs), max(ys) - min(ys))
    inset = max(tolerance * 2.1, diagonal * 1.0e-7)
    for segment in _segments(contour, 0):
        midpoint = segment.start.lerp(segment.end, 0.5)
        candidate = midpoint + _inward_normal(segment) * inset
        if point_in_polygon(candidate, points, tolerance) == PointLocation.INSIDE:
            return (candidate,)
    return ()


def _first_off_boundary_location(points, polygon, tolerance):
    """Classify one representative vertex after exact edge checks passed.

    Without a boundary crossing, all vertices away from coincident boundary
    spans have the same inside/outside relation.  Continuing after the first
    such vertex only repeats an O(n) point-in-polygon calculation.
    """

    for point in points:
        location = point_in_polygon(point, polygon, tolerance)
        if location != PointLocation.BOUNDARY:
            return location
    return PointLocation.BOUNDARY


def _has_positive_area_overlap(
    first: _NormalisedContour,
    second: _NormalisedContour,
    first_segments: Sequence[_SegmentRef],
    second_segments: Sequence[_SegmentRef],
    overlap_pairs: Sequence[Tuple[_SegmentRef, _SegmentRef]],
    tolerance: float,
) -> bool:
    # Coincident boundaries whose local interiors point to the same half-plane
    # necessarily overlap in area.  This also catches containment where all
    # vertices happen to lie on the containing polygon's boundary.
    for segment_a, segment_b in overlap_pairs:
        if _inward_normal(segment_a).dot(_inward_normal(segment_b)) > 0.0:
            return True

    first_location = _first_off_boundary_location(
        first.points, second.points, tolerance
    )
    if first_location == PointLocation.INSIDE:
        return True
    second_location = _first_off_boundary_location(
        second.points, first.points, tolerance
    )
    if second_location == PointLocation.INSIDE:
        return True

    # If every source vertex lies on a boundary, use one verified interior
    # point as the conservative fallback for re-segmented coincident rings.
    if first_location == PointLocation.BOUNDARY:
        for probe in _interior_probes(first, tolerance):
            if point_in_polygon(probe, second.points, tolerance) == PointLocation.INSIDE:
                return True
    if second_location == PointLocation.BOUNDARY:
        for probe in _interior_probes(second, tolerance):
            if point_in_polygon(probe, first.points, tolerance) == PointLocation.INSIDE:
                return True
    return False


def _append_cut(cuts, segment: _SegmentRef, parameter: float):
    cuts[(segment.contour_index, segment.segment_index)].append(
        max(0.0, min(1.0, float(parameter)))
    )


def _unique_parameters(values: Iterable[float], epsilon: float) -> Tuple[float, ...]:
    result = []
    for value in sorted(values):
        if not result or abs(value - result[-1]) > epsilon:
            result.append(value)
    if result:
        result[0] = 0.0
        result[-1] = 1.0
    return tuple(result)


def _same_unordered_segment(first: _Atom, second: _Atom, tolerance: float) -> bool:
    coincidence_tolerance = min(
        tolerance,
        MAX_PHYSICAL_COINCIDENCE_TOLERANCE,
    )
    return (
        first.start.almost_equals(second.start, coincidence_tolerance)
        and first.end.almost_equals(second.end, coincidence_tolerance)
    ) or (
        first.start.almost_equals(second.end, coincidence_tolerance)
        and first.end.almost_equals(second.start, coincidence_tolerance)
    )


def _point_on_output_segment(
    point: Vec2,
    segment: CommonLineSegment,
    tolerance: float,
) -> bool:
    vector = segment.end - segment.start
    length_squared = vector.length_squared()
    if length_squared <= _numerical_epsilon(tolerance) ** 2:
        return point.almost_equals(segment.start, tolerance)
    parameter = (point - segment.start).dot(vector) / length_squared
    if parameter < -tolerance or parameter > 1.0 + tolerance:
        return False
    nearest = segment.start + vector * max(0.0, min(1.0, parameter))
    return point.distance_to(nearest) <= tolerance


def _canonical_direction(start: Vec2, end: Vec2) -> Tuple[Vec2, Vec2]:
    return (start, end) if (start.x, start.y) <= (end.x, end.y) else (end, start)


def _can_merge(first: CommonLineSegment, second: CommonLineSegment, tolerance: float):
    if first.owner_ids != second.owner_ids:
        return None
    candidates = (
        (first.end, second.start, first.start, second.end),
        (first.end, second.end, first.start, second.start),
        (first.start, second.start, first.end, second.end),
        (first.start, second.end, first.end, second.start),
    )
    coincidence_tolerance = min(
        abs(float(tolerance)),
        MAX_PHYSICAL_COINCIDENCE_TOLERANCE,
    )
    for shared_a, shared_b, outer_a, outer_b in candidates:
        if not shared_a.almost_equals(shared_b, coincidence_tolerance):
            continue
        vector_a = shared_a - outer_a
        vector_b = outer_b - shared_b
        length_a = vector_a.length()
        length_b = vector_b.length()
        if (
            length_a > _numerical_epsilon(tolerance)
            and length_b > _numerical_epsilon(tolerance)
            and abs(
                (vector_a / length_a).cross(vector_b / length_b)
            )
            <= _angular_epsilon(length_a, length_b, tolerance)
        ):
            start, end = _canonical_direction(outer_a, outer_b)
            return CommonLineSegment(
                start,
                end,
                first.owner_ids,
                tuple(sorted(set(first.source_segments + second.source_segments))),
            )
    return None


def _coalesce_shared(
    segments: Sequence[CommonLineSegment],
    tolerance: float,
) -> Tuple[CommonLineSegment, ...]:
    result = list(segments)
    changed = True
    while changed:
        changed = False
        for first_index in range(len(result)):
            for second_index in range(first_index + 1, len(result)):
                merged = _can_merge(result[first_index], result[second_index], tolerance)
                if merged is None:
                    continue
                result[first_index] = merged
                del result[second_index]
                changed = True
                break
            if changed:
                break
    return tuple(
        sorted(
            result,
            key=lambda segment: (
                segment.owner_ids,
                segment.start.x,
                segment.start.y,
                segment.end.x,
                segment.end.y,
            ),
        )
    )


def _dedupe_issues(issues: Iterable[CommonLineIssue]) -> Tuple[CommonLineIssue, ...]:
    result = []
    keys = set()
    for issue in issues:
        key = (
            issue.code,
            issue.contour_ids,
            tuple((round(point.x, 9), round(point.y, 9)) for point in issue.points),
        )
        if key in keys:
            continue
        keys.add(key)
        result.append(issue)
    return tuple(result)


def plan_common_line_cut(
    contours: Iterable[CommonLineContour],
    tolerance: float = 1.0e-6,
    *,
    raise_on_error: bool = False,
) -> CommonLinePlan:
    """Build a safe straight-segment common-line plan.

    Point-only contacts (including a T contact at an endpoint) are recorded in
    ``touch_points`` and remain ordinary perimeter.  A positive-length
    collinear overlap is a common line only when exactly two otherwise
    non-overlapping polygon interiors own it.  Invalid plans intentionally
    expose no cut segments, preventing a caller from accidentally executing a
    partial result.
    """

    tolerance = float(tolerance)
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        raise ValueError("tolerance must be finite and positive")

    source_contours = tuple(contours)
    issues = []
    normalised = []
    seen_ids = set()
    for source in source_contours:
        if not isinstance(source, CommonLineContour):
            raise TypeError("contours must contain CommonLineContour values")
        contour_id = source.contour_id.strip()
        if contour_id in seen_ids:
            issues.append(
                _issue(
                    CommonLineIssueCode.INVALID_CONTOUR,
                    "Identificador de contorno repetido: %s." % contour_id,
                    (contour_id,),
                )
            )
            continue
        seen_ids.add(contour_id)
        result = _normalise_ring(source, tolerance)
        if isinstance(result, CommonLineIssue):
            issues.append(result)
        else:
            normalised.append(result)

    if issues:
        plan = CommonLinePlan((), (), (), _dedupe_issues(issues), tolerance)
        if raise_on_error:
            plan.require_valid()
        return plan

    all_segments = [
        _segments(contour, index) for index, contour in enumerate(normalised)
    ]
    all_segment_bounds = [
        tuple(_segment_bounds(segment) for segment in segments)
        for segments in all_segments
    ]
    contour_bounds = [_contour_bounds(contour) for contour in normalised]
    cuts = {
        (segment.contour_index, segment.segment_index): [0.0, 1.0]
        for segments in all_segments
        for segment in segments
    }
    touch_points = []

    for first_index, first in enumerate(normalised):
        for second_index in range(first_index + 1, len(normalised)):
            second = normalised[second_index]
            if not _bounds_may_contact(
                contour_bounds[first_index],
                contour_bounds[second_index],
                tolerance,
            ):
                continue
            if _rings_equal(first, second, tolerance):
                issues.append(
                    _issue(
                        CommonLineIssueCode.DUPLICATE_CONTOUR,
                        "Os contornos %s e %s são duplicados."
                        % (first.contour_id, second.contour_id),
                        (first.contour_id, second.contour_id),
                    )
                )
                continue

            pair_crossings = []
            pair_overlaps = []
            for segment_a_index, segment_b_index in _candidate_segment_pairs(
                all_segment_bounds[first_index],
                all_segment_bounds[second_index],
                tolerance,
            ):
                segment_a = all_segments[first_index][segment_a_index]
                segment_b = all_segments[second_index][segment_b_index]
                relation = _segment_relation(segment_a, segment_b, tolerance)
                if relation is None:
                    continue
                if relation.kind == "cross":
                    pair_crossings.append(relation.point)
                elif relation.kind == "touch":
                    if relation.point is not None:
                        touch_points.append(relation.point)
                else:
                    pair_overlaps.append((segment_a, segment_b))
                    _append_cut(cuts, segment_a, relation.a0)
                    _append_cut(cuts, segment_a, relation.a1)
                    _append_cut(cuts, segment_b, relation.b0)
                    _append_cut(cuts, segment_b, relation.b1)

            if pair_crossings:
                issues.append(
                    _issue(
                        CommonLineIssueCode.CROSSING,
                        "Os contornos %s e %s possuem cruzamento real."
                        % (first.contour_id, second.contour_id),
                        (first.contour_id, second.contour_id),
                        _dedupe_points(
                            (point for point in pair_crossings if point is not None),
                            tolerance,
                        ),
                    )
                )
            elif _has_positive_area_overlap(
                first,
                second,
                all_segments[first_index],
                all_segments[second_index],
                pair_overlaps,
                tolerance,
            ):
                issues.append(
                    _issue(
                        CommonLineIssueCode.AREA_OVERLAP,
                        "Os contornos %s e %s sobrepõem área de material."
                        % (first.contour_id, second.contour_id),
                        (first.contour_id, second.contour_id),
                    )
                )

    atoms = []
    atom_epsilon = _numerical_epsilon(tolerance)
    for segments in all_segments:
        for segment in segments:
            parameter_epsilon = _parameter_epsilon(
                segment.length,
                tolerance,
            )
            parameters = _unique_parameters(
                cuts[(segment.contour_index, segment.segment_index)],
                parameter_epsilon,
            )
            vector = segment.vector
            for start_parameter, end_parameter in zip(parameters, parameters[1:]):
                start = segment.start + vector * start_parameter
                end = segment.start + vector * end_parameter
                if start.distance_to(end) <= atom_epsilon:
                    continue
                atoms.append(
                    _Atom(
                        start,
                        end,
                        segment.contour_id,
                        (segment.contour_id, segment.segment_index),
                    )
                )

    atom_bounds = tuple(
        (
            min(atom.start.x, atom.end.x),
            min(atom.start.y, atom.end.y),
            max(atom.start.x, atom.end.x),
            max(atom.start.y, atom.end.y),
        )
        for atom in atoms
    )
    coincidence_tolerance = min(
        tolerance,
        MAX_PHYSICAL_COINCIDENCE_TOLERANCE,
    )
    earlier_candidates = {}
    for first_index, second_index in _candidate_segment_pairs(
        atom_bounds,
        tolerance=coincidence_tolerance,
    ):
        earlier_candidates.setdefault(second_index, []).append(first_index)

    groups = []
    atom_group_indices = []
    for atom_index, atom in enumerate(atoms):
        candidate_group_indices = sorted(
            {
                atom_group_indices[earlier_index]
                for earlier_index in earlier_candidates.get(atom_index, ())
            }
        )
        for group_index in candidate_group_indices:
            if _same_unordered_segment(atom, groups[group_index][0], tolerance):
                groups[group_index].append(atom)
                atom_group_indices.append(group_index)
                break
        else:
            atom_group_indices.append(len(groups))
            groups.append([atom])

    perimeter = []
    shared = []
    for group in groups:
        owner_ids = tuple(sorted(set(atom.owner_id for atom in group)))
        source_segments = tuple(
            sorted(set(atom.source_segment for atom in group))
        )
        if len(owner_ids) > 2:
            representative = group[0]
            start, end = _canonical_direction(
                representative.start, representative.end
            )
            issues.append(
                _issue(
                    CommonLineIssueCode.AMBIGUOUS_SHARED_LINE,
                    "Uma linha é compartilhada por mais de duas peças: %s."
                    % ", ".join(owner_ids),
                    owner_ids,
                    (start, end),
                )
            )
            continue
        representative = group[0]
        if len(owner_ids) == 2:
            start, end = _canonical_direction(
                representative.start, representative.end
            )
            shared.append(
                CommonLineSegment(
                    start, end, owner_ids, source_segments
                )
            )
        else:
            perimeter.append(
                CommonLineSegment(
                    representative.start,
                    representative.end,
                    owner_ids,
                    source_segments,
                )
            )

    issues_tuple = _dedupe_issues(issues)
    if issues_tuple:
        plan = CommonLinePlan(
            (),
            (),
            _dedupe_points(touch_points, tolerance),
            issues_tuple,
            tolerance,
        )
    else:
        shared_segments = _coalesce_shared(shared, tolerance)
        isolated_touches = tuple(
            point
            for point in _dedupe_points(touch_points, tolerance)
            if not any(
                _point_on_output_segment(point, segment, tolerance)
                for segment in shared_segments
            )
        )
        plan = CommonLinePlan(
            tuple(perimeter),
            shared_segments,
            isolated_touches,
            (),
            tolerance,
        )
    if raise_on_error:
        plan.require_valid()
    return plan


def _project_to_segment(point: Vec2, segment: CommonLineSegment):
    vector = segment.end - segment.start
    length_squared = vector.length_squared()
    if length_squared <= 1.0e-18:
        return 0.0, segment.start, point.distance_to(segment.start)
    parameter = max(
        0.0,
        min(1.0, (point - segment.start).dot(vector) / length_squared),
    )
    projected = segment.start + vector * parameter
    return parameter, projected, point.distance_to(projected)


def _tab_centres_for_owner(
    segments: Sequence[CommonLineSegment],
    count: int,
    tab_length: float,
) -> list[Tuple[int, float]]:
    """Distribute centres over usable owner-only straight intervals."""

    usable = [
        (index, segment, segment.length)
        for index, segment in enumerate(segments)
        if segment.length >= tab_length + 2.0e-7
    ]
    total = sum(length for _index, _segment, length in usable)
    if count <= 0 or total <= 1.0e-9:
        return []
    result = []
    for tab_index in range(count):
        target = (tab_index + 0.5) * total / count
        accumulated = 0.0
        for segment_index, _segment, length in usable:
            if target <= accumulated + length + 1.0e-9:
                local = max(
                    tab_length * 0.5,
                    min(length - tab_length * 0.5, target - accumulated),
                )
                result.append((segment_index, local / length))
                break
            accumulated += length
    return result


def _best_fixation_tab_centres_for_owner(
    segments: Sequence[CommonLineSegment],
    all_owner_segments: Sequence[CommonLineSegment],
    count: int,
    tab_length: float,
    retention_rules=None,
) -> list[Tuple[int, float]]:
    """Choose separated supports around the piece, away from vertices.

    Three non-collinear retained regions resist XY translation and rotation
    better than equal arclength spacing that happens to cluster on one long
    side.  Elongated pieces receive at least four regions.  If that geometric
    minimum cannot be achieved on owner-only material, planning is blocked so
    the operator can reduce the tab width or place tabs manually.
    """

    owner_points = [
        point
        for segment in all_owner_segments
        for point in (segment.start, segment.end)
    ]
    if not owner_points:
        raise ValueError("não há perímetro suficiente para analisar a fixação")
    min_x = min(point.x for point in owner_points)
    max_x = max(point.x for point in owner_points)
    min_y = min(point.y for point in owner_points)
    max_y = max(point.y for point in owner_points)
    width = max_x - min_x
    height = max_y - min_y
    short_side = max(min(width, height), 1.0e-9)
    aspect = max(width, height) / short_side
    elongated_ratio = float(
        getattr(retention_rules, "elongated_aspect_ratio", 2.5)
    )
    regular_minimum = int(
        getattr(retention_rules, "minimum_tabs_regular", 4)
    )
    elongated_minimum = int(
        getattr(retention_rules, "minimum_tabs_elongated", 4)
    )
    target_count = max(
        int(count),
        elongated_minimum if aspect >= elongated_ratio else regular_minimum,
    )
    centre = Vec2((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)

    # Rebuild the complete owner boundary (exclusive + shared atoms) as one
    # physical loop.  Arc positions below are therefore measured along the
    # actual perimeter instead of by chord distance.
    remaining_loop = list(all_owner_segments)
    ordered_loop = []
    if remaining_loop:
        current = min(
            [segment.start for segment in remaining_loop]
            + [segment.end for segment in remaining_loop],
            key=lambda point: (round(point.x, 9), round(point.y, 9)),
        )
        ordered_loop.append(current)
        while remaining_loop:
            choices = []
            for index, segment in enumerate(remaining_loop):
                if segment.start.almost_equals(current, 1.0e-7):
                    choices.append((index, segment.end))
                elif segment.end.almost_equals(current, 1.0e-7):
                    choices.append((index, segment.start))
            if not choices:
                ordered_loop = []
                break
            index, target = min(
                choices,
                key=lambda item: (
                    round(item[1].x, 9),
                    round(item[1].y, 9),
                    item[0],
                ),
            )
            remaining_loop.pop(index)
            ordered_loop.append(target)
            current = target
    loop_lengths = []
    loop_perimeter = 0.0
    for start, end in zip(ordered_loop, ordered_loop[1:]):
        loop_lengths.append(loop_perimeter)
        loop_perimeter += start.distance_to(end)

    # ``ceil(perimeter / maximum_gap)`` is the mathematical minimum only
    # when centres may land exactly anywhere, including vertices.  Real tabs
    # have width and must keep away from corners, so reserve one tab width of
    # placement tolerance.  This may add one support to long pieces and keeps
    # the *actual* centre-to-centre free span below the configured maximum.
    maximum_gap = float(
        getattr(retention_rules, "maximum_unsupported_perimeter_mm", 300.0)
    )
    if loop_perimeter > 1.0e-7 and maximum_gap > 1.0e-7:
        usable_gap = max(maximum_gap - tab_length, maximum_gap * 0.75)
        target_count = max(
            target_count,
            int(math.ceil(loop_perimeter / usable_gap)),
        )

    def perimeter_position(point):
        best = None
        for edge_index, (start, end) in enumerate(
            zip(ordered_loop, ordered_loop[1:])
        ):
            vector = end - start
            length = start.distance_to(end)
            if length <= 1.0e-12:
                continue
            parameter = max(
                0.0,
                min(1.0, (point - start).dot(vector) / (length * length)),
            )
            projected = start + vector * parameter
            candidate = (
                point.distance_to(projected),
                loop_lengths[edge_index] + parameter * length,
            )
            if best is None or candidate < best:
                best = candidate
        return 0.0 if best is None else best[1]

    candidates = []
    for segment_index, segment in enumerate(segments):
        length = segment.length
        if length < tab_length + 2.0e-7:
            continue
        half = tab_length * 0.5
        extra = max(0.0, (length - tab_length) * 0.5)
        corner_margin = min(max(tab_length * 0.5, 1.0), extra)
        low = half + corner_margin
        high = length - half - corner_margin
        if high < low:
            low = high = length * 0.5
        sample_count = max(
            1,
            min(12, int(math.ceil(length / max(tab_length * 1.5, 1.0)))),
        )
        for sample_index in range(sample_count):
            local = (
                (low + high) * 0.5
                if sample_count == 1
                else low + (high - low) * sample_index / (sample_count - 1)
            )
            parameter = local / length
            point = segment.start + (segment.end - segment.start) * parameter
            candidates.append(
                (segment_index, parameter, point, perimeter_position(point))
            )

    def compatible(first, second):
        spacing_factor = float(
            getattr(retention_rules, "minimum_tab_spacing_factor", 1.25)
        )
        return first[2].distance_to(second[2]) >= (
            tab_length * spacing_factor - 1.0e-9
        )

    candidates.sort(key=lambda value: (value[0], value[1]))
    if len(candidates) > 72:
        candidates = [
            candidates[int(round(index * (len(candidates) - 1) / 71.0))]
            for index in range(72)
        ]
    if len(candidates) < target_count:
        raise ValueError(
            "Melhor fixação não encontrou %d regiões exclusivas para tabs; "
            "reduza o comprimento ou posicione manualmente." % target_count
        )

    if loop_perimeter > 1.0e-7:
        best_even = None
        best_even_score = None
        for start_candidate in candidates:
            selected_even = []
            for target_index in range(target_count):
                target = (
                    start_candidate[3]
                    + target_index * loop_perimeter / target_count
                ) % loop_perimeter
                available = [
                    candidate
                    for candidate in candidates
                    if candidate not in selected_even
                    and all(
                        compatible(candidate, existing)
                        for existing in selected_even
                    )
                ]
                if not available:
                    break
                selected_even.append(
                    min(
                        available,
                        key=lambda candidate: (
                            min(
                                abs(candidate[3] - target),
                                loop_perimeter - abs(candidate[3] - target),
                            ),
                            candidate[0],
                            candidate[1],
                        ),
                    )
                )
            if len(selected_even) != target_count:
                continue
            positions = sorted(candidate[3] for candidate in selected_even)
            gaps = [
                second - first
                for first, second in zip(positions, positions[1:])
            ]
            gaps.append(loop_perimeter - positions[-1] + positions[0])
            twice_area = max(
                (
                    abs((second[2] - first[2]).cross(third[2] - first[2]))
                    for first, second, third in itertools.combinations(
                        selected_even, 3
                    )
                ),
                default=0.0,
            )
            separation = min(
                (
                    first[2].distance_to(second[2])
                    for first, second in itertools.combinations(
                        selected_even, 2
                    )
                ),
                default=0.0,
            )
            score = (-max(gaps), twice_area, separation)
            if best_even_score is None or score > best_even_score:
                best_even_score = score
                best_even = selected_even
        if (
            best_even is not None
            and -best_even_score[0] <= maximum_gap + 1.0e-6
            and best_even_score[1] > 1.0e-7
        ):
            return sorted((value[0], value[1]) for value in best_even)

    best_triple = None
    best_score = None
    for first_index in range(len(candidates)):
        first = candidates[first_index]
        for second_index in range(first_index + 1, len(candidates)):
            second = candidates[second_index]
            if not compatible(first, second):
                continue
            for third_index in range(second_index + 1, len(candidates)):
                third = candidates[third_index]
                if not compatible(first, third) or not compatible(second, third):
                    continue
                twice_area = abs(
                    (second[2] - first[2]).cross(third[2] - first[2])
                )
                if twice_area <= 1.0e-7:
                    continue
                triangle_area = (
                    abs((second[2] - centre).cross(third[2] - centre))
                    + abs((third[2] - centre).cross(first[2] - centre))
                    + abs((first[2] - centre).cross(second[2] - centre))
                )
                surrounds_centre = triangle_area <= twice_area + 1.0e-7
                separation = min(
                    first[2].distance_to(second[2]),
                    first[2].distance_to(third[2]),
                    second[2].distance_to(third[2]),
                )
                score = (bool(surrounds_centre), twice_area, separation)
                if best_score is None or score > best_score:
                    best_score = score
                    best_triple = [first, second, third]
    if best_triple is None:
        raise ValueError(
            "Melhor fixação não conseguiu distribuir três tabs em regiões "
            "não colineares; posicione as pontes manualmente."
        )

    selected = list(best_triple)
    while len(selected) < target_count:
        available = [
            candidate
            for candidate in candidates
            if candidate not in selected
            and all(compatible(candidate, existing) for existing in selected)
        ]
        if not available:
            raise ValueError(
                "Melhor fixação não conseguiu manter as tabs afastadas; "
                "reduza o comprimento ou posicione manualmente."
            )
        selected.append(
            max(
                available,
                key=lambda candidate: (
                    min(
                        candidate[2].distance_to(existing[2])
                        for existing in selected
                    ),
                    candidate[2].distance_to(centre),
                    -candidate[0],
                    -candidate[1],
                ),
            )
        )
    return sorted((value[0], value[1]) for value in selected)


def build_common_line_cut_paths(
    plan: CommonLinePlan,
    *,
    tab_count: int = 0,
    tab_length: float = 0.0,
    manual_tab_positions: Iterable[Vec2 | Sequence[float] | dict] = (),
    best_fixation: bool = False,
    retention_rules=None,
) -> Tuple[CommonLineCutPath, ...]:
    """Split a valid plan into edge-disjoint CAM paths with safe tabs.

    Every geometric interval from ``plan`` occurs exactly once in the result.
    Automatic and manual tabs are projected only onto owner-only perimeter
    lines and split those lines at the tab limits.  Consequently no subsequent
    path can cross the same common edge and remove a bridge that was just left.
    """

    plan.require_valid()
    try:
        count_by_owner = (
            {str(owner): max(0, int(value)) for owner, value in tab_count.items()}
            if hasattr(tab_count, "items")
            else None
        )
        requested_count = (
            0
            if count_by_owner is not None
            else max(0, int(round(float(tab_count))))
        )
        requested_length = max(0.0, float(tab_length))
    except (TypeError, ValueError):
        raise ValueError("quantidade e comprimento de tabs devem ser numéricos")
    if not math.isfinite(requested_length):
        raise ValueError("comprimento de tab deve ser finito")

    perimeter = tuple(plan.perimeter_segments)
    shared = tuple(plan.shared_segments)
    manual_positions = tuple(manual_tab_positions or ())
    manual_centres: dict[int, list[float]] = {}
    manual_shared_centres: dict[int, list[float]] = {}
    for raw_position in manual_positions:
        try:
            if isinstance(raw_position, dict):
                point = Vec2(float(raw_position["x"]), float(raw_position["y"]))
            elif isinstance(raw_position, Vec2):
                point = raw_position
            else:
                point = Vec2.from_sequence(raw_position)
        except (KeyError, TypeError, ValueError):
            continue
        candidates = []
        for shared_rank, segments in enumerate((perimeter, shared)):
            for index, segment in enumerate(segments):
                if requested_length > segment.length + 1.0e-7:
                    continue
                parameter, _projected, distance = _project_to_segment(point, segment)
                # Prefer an owner-only edge only for an exact tie at a
                # junction.  A click in the body of a shared edge therefore
                # becomes one physical SharedTab instead of jumping to an
                # unrelated outside border.
                candidates.append((distance, shared_rank, index, parameter))
        if candidates:
            _distance, shared_rank, segment_index, parameter = min(candidates)
            target = manual_shared_centres if shared_rank else manual_centres
            target.setdefault(segment_index, []).append(parameter)

    centres: dict[int, list[float]] = manual_centres
    if (
        not manual_positions
        and (requested_count or count_by_owner)
        and requested_length > 1.0e-7
    ):
        owner_ids = sorted(
            {segment.owner_ids[0] for segment in perimeter if segment.owner_ids}
        )
        for owner_id in owner_ids:
            owner_count = (
                count_by_owner.get(owner_id, 0)
                if count_by_owner is not None
                else requested_count
            )
            if owner_count <= 0:
                continue
            owner_segments = tuple(
                segment for segment in perimeter if segment.owner_ids == (owner_id,)
            )
            owner_indexes = tuple(
                index for index, segment in enumerate(perimeter)
                if segment.owner_ids == (owner_id,)
            )
            all_owner_segments = tuple(
                segment
                for segment in plan.perimeter_segments + plan.shared_segments
                if owner_id in segment.owner_ids
            )
            if best_fixation:
                owner_centres = _best_fixation_tab_centres_for_owner(
                    owner_segments,
                    all_owner_segments,
                    owner_count,
                    requested_length,
                    retention_rules,
                )
            else:
                # Mechanical fallback for common-line networks in which a
                # piece has too little exclusive, non-collinear boundary.
                # The sheet planner subsequently supplements shared tabs.
                owner_centres = _tab_centres_for_owner(
                    owner_segments,
                    owner_count,
                    requested_length,
                )
            for local_index, parameter in owner_centres:
                centres.setdefault(owner_indexes[local_index], []).append(parameter)

    def split_segment(segment, segment_centres, *, shared_edge):
        length = segment.length
        ranges = []
        if requested_length > 1.0e-7 and length > 1.0e-9:
            half_parameter = min(0.5, requested_length * 0.5 / length)
            for centre in segment_centres:
                ranges.append(
                    (
                        max(0.0, centre - half_parameter),
                        min(1.0, centre + half_parameter),
                    )
                )
        cuts = {0.0, 1.0}
        for start, end in ranges:
            cuts.add(start)
            cuts.add(end)
        ordered = sorted(cuts)
        vector = segment.end - segment.start
        for start, end in zip(ordered, ordered[1:]):
            if end - start <= 1.0e-9:
                continue
            middle = (start + end) * 0.5
            is_tab = any(
                range_start - 1.0e-9 <= middle <= range_end + 1.0e-9
                for range_start, range_end in ranges
            )
            yield CommonLineCutPath(
                (
                    segment.start + vector * start,
                    segment.start + vector * end,
                ),
                segment.owner_ids,
                shared=shared_edge,
                tab=is_tab,
            )

    paths = []
    for segment_index, segment in enumerate(perimeter):
        paths.extend(
            split_segment(
                segment,
                centres.get(segment_index, ()),
                shared_edge=False,
            )
        )

    shared_paths = []
    for segment_index, segment in enumerate(shared):
        shared_paths.extend(
            split_segment(
                segment,
                manual_shared_centres.get(segment_index, ()),
                shared_edge=True,
            )
        )

    # Shared edges are emitted once and first. Owner-only perimeter (including
    # every retained tab interval) follows, keeping each part attached longer.
    return tuple(shared_paths) + tuple(paths)


def _trail_node_key(point: Vec2, tolerance: float) -> Tuple[int, int]:
    scale = max(float(tolerance), 1.0e-9)
    return (int(round(point.x / scale)), int(round(point.y / scale)))


def _join_cut_paths_into_trails(
    paths: Sequence[CommonLineCutPath],
    tolerance: float,
) -> Tuple[CommonLineCutTrail, ...]:
    """Join adjacent atomic intervals without ever duplicating an edge."""

    if not paths:
        return ()
    edges = []
    adjacency: dict[Tuple[int, int], list[int]] = {}
    for index, path in enumerate(paths):
        if len(path.points) != 2:
            raise ValueError("intervalo atômico de linha comum inválido")
        start_key = _trail_node_key(path.points[0], tolerance)
        end_key = _trail_node_key(path.points[1], tolerance)
        edges.append((path, start_key, end_key))
        adjacency.setdefault(start_key, []).append(index)
        adjacency.setdefault(end_key, []).append(index)

    unused = set(range(len(edges)))
    trails = []
    while unused:
        degrees = {
            node: sum(edge_index in unused for edge_index in incident)
            for node, incident in adjacency.items()
        }
        endpoints = sorted(node for node, degree in degrees.items() if degree == 1)
        odd_nodes = sorted(node for node, degree in degrees.items() if degree % 2)
        if endpoints:
            current_key = endpoints[0]
        elif odd_nodes:
            current_key = odd_nodes[0]
        else:
            current_key = min(
                node for node, degree in degrees.items() if degree > 0
            )

        points = []
        edge_tabs = []
        edge_shared = []
        edge_owner_ids = []
        while True:
            candidates = sorted(
                edge_index
                for edge_index in adjacency.get(current_key, ())
                if edge_index in unused
            )
            if not candidates:
                break
            edge_index = candidates[0]
            path, start_key, end_key = edges[edge_index]
            if current_key == start_key:
                start, end = path.points
                next_key = end_key
            else:
                end, start = path.points
                next_key = start_key
            if not points:
                points.append(start)
            points.append(end)
            edge_tabs.append(bool(path.tab))
            edge_shared.append(bool(path.shared))
            edge_owner_ids.append(tuple(path.owner_ids))
            unused.remove(edge_index)
            current_key = next_key

        trails.append(
            CommonLineCutTrail(
                points=tuple(points),
                edge_tabs=tuple(edge_tabs),
                edge_shared=tuple(edge_shared),
                edge_owner_ids=tuple(edge_owner_ids),
            )
        )
    return tuple(trails)


def build_common_line_cut_trails(
    plan: CommonLinePlan,
    *,
    tab_count: int = 0,
    tab_length: float = 0.0,
    manual_tab_positions: Iterable[Vec2 | Sequence[float] | dict] = (),
    best_fixation: bool = False,
) -> Tuple[CommonLineCutTrail, ...]:
    """Return CNC-ready continuous trails with tabs embedded in their edges.

    Shared lines are machined first.  The remaining perimeter is grouped by
    owner so a piece keeps all of its bridges until its own continuous trail
    is completed.  Every atomic interval still occurs exactly once.
    """

    atomic = build_common_line_cut_paths(
        plan,
        tab_count=tab_count,
        tab_length=tab_length,
        manual_tab_positions=manual_tab_positions,
        best_fixation=best_fixation,
    )
    shared = tuple(path for path in atomic if path.shared)
    owner_ids = sorted(
        {
            path.owner_ids[0]
            for path in atomic
            if not path.shared and len(path.owner_ids) == 1
        }
    )
    trails = list(_join_cut_paths_into_trails(shared, plan.tolerance))
    for owner_id in owner_ids:
        trails.extend(
            _join_cut_paths_into_trails(
                tuple(
                    path
                    for path in atomic
                    if not path.shared and path.owner_ids == (owner_id,)
                ),
                plan.tolerance,
            )
        )
    return tuple(trails)


__all__ = [
    "CommonLineCutPath",
    "CommonLineCutTrail",
    "CommonLineContour",
    "CommonLineIssue",
    "CommonLineIssueCode",
    "CommonLinePlan",
    "CommonLinePlanningError",
    "CommonLineSegment",
    "build_common_line_cut_paths",
    "build_common_line_cut_trails",
    "plan_common_line_cut",
]
