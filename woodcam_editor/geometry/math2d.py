"""Dependency-free numerical geometry helpers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Iterable, Sequence, Tuple

DEFAULT_EPSILON = 1.0e-9
TAU = math.tau if hasattr(math, "tau") else 2.0 * math.pi


def clamp(value: float, minimum: float, maximum: float) -> float:
    if minimum > maximum:
        raise ValueError("minimum cannot exceed maximum")
    return max(minimum, min(maximum, value))


def almost_equal(a: float, b: float, tolerance: float = DEFAULT_EPSILON) -> bool:
    return abs(float(a) - float(b)) <= tolerance


def normalize_angle(angle: float) -> float:
    result = float(angle) % TAU
    return 0.0 if almost_equal(result, TAU) else result


def directed_sweep(start_angle: float, end_angle: float, clockwise: bool) -> float:
    """Return a signed sweep in ``(-2*pi, 2*pi)`` for a non-full arc."""

    start_angle = normalize_angle(start_angle)
    end_angle = normalize_angle(end_angle)
    if clockwise:
        amount = (start_angle - end_angle) % TAU
        return -amount
    return (end_angle - start_angle) % TAU


def angle_on_sweep(
    angle: float,
    start_angle: float,
    sweep: float,
    tolerance: float = DEFAULT_EPSILON,
) -> bool:
    angle = normalize_angle(angle)
    start_angle = normalize_angle(start_angle)
    if sweep >= 0.0:
        distance = (angle - start_angle) % TAU
        return distance <= sweep + tolerance
    distance = (start_angle - angle) % TAU
    return distance <= -sweep + tolerance


def orientation(a: Vec2, b: Vec2, c: Vec2) -> float:
    return (b - a).cross(c - a)


def projection_parameter(point: Vec2, start: Vec2, end: Vec2) -> float:
    direction = end - start
    denominator = direction.length_squared()
    if denominator <= DEFAULT_EPSILON ** 2:
        return 0.0
    return (point - start).dot(direction) / denominator


def closest_point_on_segment(point: Vec2, start: Vec2, end: Vec2) -> Tuple[Vec2, float]:
    parameter = clamp(projection_parameter(point, start, end), 0.0, 1.0)
    return start.lerp(end, parameter), parameter


def distance_point_to_segment(point: Vec2, start: Vec2, end: Vec2) -> float:
    return point.distance_to(closest_point_on_segment(point, start, end)[0])


def point_on_segment(
    point: Vec2,
    start: Vec2,
    end: Vec2,
    tolerance: float = DEFAULT_EPSILON,
) -> bool:
    nearest, _ = closest_point_on_segment(point, start, end)
    return point.distance_to(nearest) <= tolerance


@dataclass(frozen=True, slots=True)
class SegmentIntersection:
    point: Vec2
    parameter_a: float
    parameter_b: float
    kind: str = "cross"


def segment_intersections(
    a0: Vec2,
    a1: Vec2,
    b0: Vec2,
    b1: Vec2,
    tolerance: float = DEFAULT_EPSILON,
) -> Tuple[SegmentIntersection, ...]:
    """Return point intersections between two closed line segments.

    Collinear overlap is represented by its one or two boundary points with
    kind ``"overlap"``. Duplicate boundary points are removed.
    """

    r = a1 - a0
    s = b1 - b0
    rxs = r.cross(s)
    qmp = b0 - a0
    qmpxr = qmp.cross(r)
    scale = max(1.0, r.length(), s.length())
    epsilon = tolerance * scale

    if abs(rxs) <= epsilon and abs(qmpxr) <= epsilon:
        r2 = r.length_squared()
        s2 = s.length_squared()
        if r2 <= tolerance ** 2 and s2 <= tolerance ** 2:
            if a0.almost_equals(b0, tolerance):
                return (SegmentIntersection(a0, 0.0, 0.0, "touch"),)
            return ()
        if r2 <= tolerance ** 2:
            if point_on_segment(a0, b0, b1, tolerance):
                return (SegmentIntersection(a0, 0.0, projection_parameter(a0, b0, b1), "touch"),)
            return ()
        candidates = []
        for point, ta in ((a0, 0.0), (a1, 1.0), (b0, projection_parameter(b0, a0, a1)), (b1, projection_parameter(b1, a0, a1))):
            if point_on_segment(point, a0, a1, tolerance) and point_on_segment(point, b0, b1, tolerance):
                if not any(point.almost_equals(existing[0], tolerance) for existing in candidates):
                    candidates.append((point, ta))
        candidates.sort(key=lambda item: item[1])
        return tuple(
            SegmentIntersection(point, projection_parameter(point, a0, a1), projection_parameter(point, b0, b1), "overlap")
            for point, _ in candidates[:2]
        )

    if abs(rxs) <= epsilon:
        return ()

    ta = qmp.cross(s) / rxs
    tb = qmp.cross(r) / rxs
    parameter_epsilon = tolerance / max(scale, tolerance)
    if -parameter_epsilon <= ta <= 1.0 + parameter_epsilon and -parameter_epsilon <= tb <= 1.0 + parameter_epsilon:
        ta = clamp(ta, 0.0, 1.0)
        tb = clamp(tb, 0.0, 1.0)
        kind = "cross"
        if ta in (0.0, 1.0) or tb in (0.0, 1.0):
            kind = "touch"
        return (SegmentIntersection(a0 + r * ta, ta, tb, kind),)
    return ()


def signed_area(points: Sequence[Vec2]) -> float:
    if len(points) < 3:
        return 0.0
    area_twice = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        area_twice += point.cross(nxt)
    return area_twice * 0.5


def polygon_centroid(points: Sequence[Vec2], tolerance: float = DEFAULT_EPSILON) -> Vec2:
    if len(points) < 3:
        if not points:
            raise GeometryError("centroid requires at least one point")
        return Vec2(
            sum(point.x for point in points) / len(points),
            sum(point.y for point in points) / len(points),
        )
    area = signed_area(points)
    if abs(area) <= tolerance:
        return Vec2(
            sum(point.x for point in points) / len(points),
            sum(point.y for point in points) / len(points),
        )
    factor_sum_x = 0.0
    factor_sum_y = 0.0
    for index, point in enumerate(points):
        nxt = points[(index + 1) % len(points)]
        cross = point.cross(nxt)
        factor_sum_x += (point.x + nxt.x) * cross
        factor_sum_y += (point.y + nxt.y) * cross
    divisor = 6.0 * area
    return Vec2(factor_sum_x / divisor, factor_sum_y / divisor)


class PointLocation(str, Enum):
    OUTSIDE = "outside"
    BOUNDARY = "boundary"
    INSIDE = "inside"


def point_in_polygon(
    point: Vec2,
    polygon: Sequence[Vec2],
    tolerance: float = DEFAULT_EPSILON,
) -> PointLocation:
    if len(polygon) < 3:
        return PointLocation.OUTSIDE
    inside = False
    previous = polygon[-1]
    for current in polygon:
        if point_on_segment(point, previous, current, tolerance):
            return PointLocation.BOUNDARY
        crosses_y = (current.y > point.y) != (previous.y > point.y)
        if crosses_y:
            x_at_y = previous.x + (point.y - previous.y) * (current.x - previous.x) / (current.y - previous.y)
            if x_at_y > point.x:
                inside = not inside
        previous = current
    return PointLocation.INSIDE if inside else PointLocation.OUTSIDE


def polyline_length(points: Sequence[Vec2], closed: bool = False) -> float:
    if len(points) < 2:
        return 0.0
    total = sum(points[index].distance_to(points[index + 1]) for index in range(len(points) - 1))
    if closed:
        total += points[-1].distance_to(points[0])
    return total


def deduplicate_consecutive(points: Iterable[Vec2], tolerance: float = DEFAULT_EPSILON) -> Tuple[Vec2, ...]:
    result = []
    for point in points:
        if not result or not point.almost_equals(result[-1], tolerance):
            result.append(point)
    return tuple(result)


# Imported last on purpose.  ``domain.spans`` consumes the functions above,
# while this module consumes the primitive types.  Defining the dependency-free
# algorithms first keeps both ``import woodcam_editor.domain`` and
# ``import woodcam_editor.geometry`` valid regardless of import order.
from ..domain.primitives import GeometryError, Vec2  # noqa: E402
