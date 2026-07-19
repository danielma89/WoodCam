"""Exact path spans used by the vector document."""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Tuple, Union

from .primitives import (
    DEFAULT_EPSILON,
    Affine2D,
    BBox2D,
    GeometryError,
    Vec2,
    new_id,
)
from ..geometry.math2d import (
    TAU,
    angle_on_sweep,
    clamp,
    closest_point_on_segment,
    directed_sweep,
    normalize_angle,
)


@dataclass(frozen=True, slots=True)
class NearestPoint:
    point: Vec2
    parameter: float
    distance: float


def _validate_parameter(t: float, *, allow_ends: bool = True) -> float:
    t = float(t)
    minimum, maximum = ((0.0, 1.0) if allow_ends else (DEFAULT_EPSILON, 1.0 - DEFAULT_EPSILON))
    if not minimum <= t <= maximum:
        raise GeometryError("span parameter must be between 0 and 1")
    return t


@dataclass(frozen=True, slots=True)
class LineSpan:
    start: Vec2
    end: Vec2
    id: str = field(default_factory=lambda: new_id("span"))

    def __post_init__(self) -> None:
        if not isinstance(self.start, Vec2) or not isinstance(self.end, Vec2):
            raise TypeError("LineSpan endpoints must be Vec2")
        if self.start.almost_equals(self.end, DEFAULT_EPSILON):
            raise GeometryError("LineSpan cannot have zero length")
        if not self.id:
            raise GeometryError("LineSpan id cannot be empty")

    def point_at(self, t: float) -> Vec2:
        return self.start.lerp(self.end, _validate_parameter(t))

    def tangent_at(self, t: float) -> Vec2:
        _validate_parameter(t)
        return (self.end - self.start).normalized()

    def length(self) -> float:
        return self.start.distance_to(self.end)

    def bounds(self) -> BBox2D:
        return BBox2D.from_points((self.start, self.end))

    def nearest_point(self, point: Vec2) -> NearestPoint:
        nearest, parameter = closest_point_on_segment(point, self.start, self.end)
        return NearestPoint(nearest, parameter, nearest.distance_to(point))

    def split(self, t: float) -> Tuple["LineSpan", "LineSpan"]:
        t = _validate_parameter(t, allow_ends=False)
        point = self.point_at(t)
        return (
            LineSpan(self.start, point, id=self.id),
            LineSpan(point, self.end),
        )

    def reversed(self) -> "LineSpan":
        return LineSpan(self.end, self.start, id=self.id)

    def transformed(self, transform: Affine2D) -> "LineSpan":
        return LineSpan(
            transform.apply_to_point(self.start),
            transform.apply_to_point(self.end),
            id=self.id,
        )

    def flatten(self, deflection: float = 0.01) -> Tuple[Vec2, Vec2]:
        if deflection <= 0.0:
            raise GeometryError("deflection must be positive")
        return (self.start, self.end)

    def with_start(self, point: Vec2) -> "LineSpan":
        return LineSpan(point, self.end, id=self.id)

    def with_end(self, point: Vec2) -> "LineSpan":
        return LineSpan(self.start, point, id=self.id)


@dataclass(frozen=True, slots=True)
class ArcSpan:
    start: Vec2
    end: Vec2
    center: Vec2
    clockwise: bool = False
    id: str = field(default_factory=lambda: new_id("span"))

    def __post_init__(self) -> None:
        if not all(isinstance(point, Vec2) for point in (self.start, self.end, self.center)):
            raise TypeError("ArcSpan points must be Vec2")
        start_radius = self.start.distance_to(self.center)
        end_radius = self.end.distance_to(self.center)
        if start_radius <= DEFAULT_EPSILON:
            raise GeometryError("ArcSpan radius must be positive")
        radius_tolerance = max(DEFAULT_EPSILON, start_radius * 1.0e-8)
        if abs(start_radius - end_radius) > radius_tolerance:
            raise GeometryError("ArcSpan endpoints must share the same radius")
        if self.start.almost_equals(self.end, radius_tolerance):
            raise GeometryError("ArcSpan cannot represent a full circle; use CircleEntity")
        if not self.id:
            raise GeometryError("ArcSpan id cannot be empty")

    @property
    def radius(self) -> float:
        return self.start.distance_to(self.center)

    @property
    def start_angle(self) -> float:
        return (self.start - self.center).angle()

    @property
    def end_angle(self) -> float:
        return (self.end - self.center).angle()

    @property
    def sweep_angle(self) -> float:
        return directed_sweep(self.start_angle, self.end_angle, self.clockwise)

    def point_at(self, t: float) -> Vec2:
        t = _validate_parameter(t)
        angle = self.start_angle + self.sweep_angle * t
        return self.center + Vec2(math.cos(angle), math.sin(angle)) * self.radius

    def tangent_at(self, t: float) -> Vec2:
        radial = (self.point_at(t) - self.center).normalized()
        tangent = radial.perpendicular_left()
        return -tangent if self.clockwise else tangent

    def length(self) -> float:
        return abs(self.sweep_angle) * self.radius

    def bounds(self) -> BBox2D:
        points = [self.start, self.end]
        for angle in (0.0, math.pi * 0.5, math.pi, math.pi * 1.5):
            if angle_on_sweep(angle, self.start_angle, self.sweep_angle):
                points.append(self.center + Vec2(math.cos(angle), math.sin(angle)) * self.radius)
        return BBox2D.from_points(points)

    def nearest_point(self, point: Vec2) -> NearestPoint:
        relative = point - self.center
        if relative.length() <= DEFAULT_EPSILON:
            start_distance = point.distance_to(self.start)
            return NearestPoint(self.start, 0.0, start_distance)
        angle = relative.angle()
        if angle_on_sweep(angle, self.start_angle, self.sweep_angle):
            projected = self.center + relative.normalized() * self.radius
            if self.sweep_angle >= 0.0:
                travelled = (normalize_angle(angle) - normalize_angle(self.start_angle)) % TAU
            else:
                travelled = -((normalize_angle(self.start_angle) - normalize_angle(angle)) % TAU)
            parameter = clamp(travelled / self.sweep_angle, 0.0, 1.0)
            return NearestPoint(projected, parameter, point.distance_to(projected))
        distance_start = point.distance_to(self.start)
        distance_end = point.distance_to(self.end)
        if distance_start <= distance_end:
            return NearestPoint(self.start, 0.0, distance_start)
        return NearestPoint(self.end, 1.0, distance_end)

    def split(self, t: float) -> Tuple["ArcSpan", "ArcSpan"]:
        t = _validate_parameter(t, allow_ends=False)
        point = self.point_at(t)
        return (
            ArcSpan(self.start, point, self.center, self.clockwise, id=self.id),
            ArcSpan(point, self.end, self.center, self.clockwise),
        )

    def reversed(self) -> "ArcSpan":
        return ArcSpan(self.end, self.start, self.center, not self.clockwise, id=self.id)

    def transformed(self, transform: Affine2D) -> "ArcSpan":
        if not transform.is_similarity():
            raise GeometryError("a non-uniform transform cannot preserve a circular arc")
        clockwise = self.clockwise if transform.determinant > 0.0 else not self.clockwise
        return ArcSpan(
            transform.apply_to_point(self.start),
            transform.apply_to_point(self.end),
            transform.apply_to_point(self.center),
            clockwise,
            id=self.id,
        )

    def flatten(self, deflection: float = 0.01) -> Tuple[Vec2, ...]:
        deflection = float(deflection)
        if deflection <= 0.0:
            raise GeometryError("deflection must be positive")
        if deflection >= self.radius:
            max_step = math.pi
        else:
            max_step = 2.0 * math.acos(clamp(1.0 - deflection / self.radius, -1.0, 1.0))
        if max_step <= DEFAULT_EPSILON:
            max_step = abs(self.sweep_angle)
        segment_count = max(1, int(math.ceil(abs(self.sweep_angle) / max_step)))
        return tuple(self.point_at(index / segment_count) for index in range(segment_count + 1))

    def with_start(self, point: Vec2) -> "ArcSpan":
        return self._with_endpoints(point, self.end)

    def with_end(self, point: Vec2) -> "ArcSpan":
        return self._with_endpoints(self.start, point)

    def _with_endpoints(self, start: Vec2, end: Vec2) -> "ArcSpan":
        """Move an endpoint while preserving the signed arc sweep (bulge).

        Keeping the old centre would generally give the two endpoints different
        radii.  Reconstructing the circle from chord + signed bulge is exact,
        deterministic and supports both minor and major arcs.
        """

        chord = end - start
        chord_length = chord.length()
        if chord_length <= DEFAULT_EPSILON:
            raise GeometryError("moving an arc endpoint collapsed its chord")
        bulge = math.tan(self.sweep_angle * 0.25)
        if abs(bulge) <= DEFAULT_EPSILON:
            raise GeometryError("arc sweep is too small to preserve")
        midpoint = start.lerp(end, 0.5)
        offset = chord_length * (1.0 - bulge * bulge) / (4.0 * bulge)
        center = midpoint + chord.normalized().perpendicular_left() * offset
        return ArcSpan(start, end, center, bulge < 0.0, id=self.id)


def _quadratic_roots(a: float, b: float, c: float) -> Tuple[float, ...]:
    if abs(a) <= DEFAULT_EPSILON:
        if abs(b) <= DEFAULT_EPSILON:
            return ()
        return (-c / b,)
    discriminant = b * b - 4.0 * a * c
    if discriminant < -DEFAULT_EPSILON:
        return ()
    discriminant = max(0.0, discriminant)
    root = math.sqrt(discriminant)
    return ((-b - root) / (2.0 * a), (-b + root) / (2.0 * a))


@dataclass(frozen=True, slots=True)
class CubicBezierSpan:
    start: Vec2
    control1: Vec2
    control2: Vec2
    end: Vec2
    id: str = field(default_factory=lambda: new_id("span"))

    def __post_init__(self) -> None:
        if not all(isinstance(point, Vec2) for point in (self.start, self.control1, self.control2, self.end)):
            raise TypeError("CubicBezierSpan points must be Vec2")
        if (
            self.start.almost_equals(self.end)
            and self.start.almost_equals(self.control1)
            and self.start.almost_equals(self.control2)
        ):
            raise GeometryError("CubicBezierSpan cannot have zero length")
        if not self.id:
            raise GeometryError("CubicBezierSpan id cannot be empty")

    def point_at(self, t: float) -> Vec2:
        t = _validate_parameter(t)
        u = 1.0 - t
        return (
            self.start * (u * u * u)
            + self.control1 * (3.0 * u * u * t)
            + self.control2 * (3.0 * u * t * t)
            + self.end * (t * t * t)
        )

    def _derivative(self, t: float) -> Vec2:
        u = 1.0 - t
        return (
            (self.control1 - self.start) * (3.0 * u * u)
            + (self.control2 - self.control1) * (6.0 * u * t)
            + (self.end - self.control2) * (3.0 * t * t)
        )

    def tangent_at(self, t: float) -> Vec2:
        t = _validate_parameter(t)
        derivative = self._derivative(t)
        if derivative.length() <= DEFAULT_EPSILON:
            step = 1.0e-6
            before = self.point_at(max(0.0, t - step))
            after = self.point_at(min(1.0, t + step))
            derivative = after - before
        return derivative.normalized()

    def length(self) -> float:
        # 24-point Gauss-Legendre would be faster, but adaptive flattening gives
        # a deterministic and sufficiently precise dependency-free result.
        points = self.flatten(1.0e-5)
        return sum(points[index].distance_to(points[index + 1]) for index in range(len(points) - 1))

    def bounds(self) -> BBox2D:
        parameters = {0.0, 1.0}
        for p0, p1, p2, p3 in (
            (self.start.x, self.control1.x, self.control2.x, self.end.x),
            (self.start.y, self.control1.y, self.control2.y, self.end.y),
        ):
            # derivative / 3 = a*t^2 + b*t + c
            a = -p0 + 3.0 * p1 - 3.0 * p2 + p3
            b = 2.0 * (p0 - 2.0 * p1 + p2)
            c = p1 - p0
            for root in _quadratic_roots(a, b, c):
                if 0.0 < root < 1.0:
                    parameters.add(root)
        return BBox2D.from_points(self.point_at(parameter) for parameter in sorted(parameters))

    def nearest_point(self, point: Vec2) -> NearestPoint:
        # Locate a basin with a dense deterministic scan, then minimise squared
        # distance with golden-section search.  This avoids optional numeric deps.
        samples = 80
        best_index = min(
            range(samples + 1),
            key=lambda index: self.point_at(index / samples).distance_squared_to(point),
        )
        left = max(0.0, (best_index - 1) / samples)
        right = min(1.0, (best_index + 1) / samples)
        ratio = (math.sqrt(5.0) - 1.0) * 0.5
        x1 = right - ratio * (right - left)
        x2 = left + ratio * (right - left)
        for _ in range(48):
            distance1 = self.point_at(x1).distance_squared_to(point)
            distance2 = self.point_at(x2).distance_squared_to(point)
            if distance1 <= distance2:
                right, x2 = x2, x1
                x1 = right - ratio * (right - left)
            else:
                left, x1 = x1, x2
                x2 = left + ratio * (right - left)
        parameter = (left + right) * 0.5
        nearest = self.point_at(parameter)
        return NearestPoint(nearest, parameter, nearest.distance_to(point))

    def split(self, t: float) -> Tuple["CubicBezierSpan", "CubicBezierSpan"]:
        t = _validate_parameter(t, allow_ends=False)
        p01 = self.start.lerp(self.control1, t)
        p12 = self.control1.lerp(self.control2, t)
        p23 = self.control2.lerp(self.end, t)
        p012 = p01.lerp(p12, t)
        p123 = p12.lerp(p23, t)
        point = p012.lerp(p123, t)
        return (
            CubicBezierSpan(self.start, p01, p012, point, id=self.id),
            CubicBezierSpan(point, p123, p23, self.end),
        )

    def reversed(self) -> "CubicBezierSpan":
        return CubicBezierSpan(self.end, self.control2, self.control1, self.start, id=self.id)

    def transformed(self, transform: Affine2D) -> "CubicBezierSpan":
        return CubicBezierSpan(
            transform.apply_to_point(self.start),
            transform.apply_to_point(self.control1),
            transform.apply_to_point(self.control2),
            transform.apply_to_point(self.end),
            id=self.id,
        )

    def flatten(self, deflection: float = 0.01) -> Tuple[Vec2, ...]:
        deflection = float(deflection)
        if deflection <= 0.0:
            raise GeometryError("deflection must be positive")
        result = [self.start]

        def recurse(curve: "CubicBezierSpan", depth: int) -> None:
            chord = curve.end - curve.start
            chord_length = chord.length()
            if chord_length <= DEFAULT_EPSILON:
                flatness = max(
                    curve.control1.distance_to(curve.start),
                    curve.control2.distance_to(curve.start),
                )
            else:
                flatness = max(
                    abs((curve.control1 - curve.start).cross(chord)) / chord_length,
                    abs((curve.control2 - curve.start).cross(chord)) / chord_length,
                )
            if flatness <= deflection or depth >= 24:
                result.append(curve.end)
                return
            left, right = curve.split(0.5)
            recurse(left, depth + 1)
            recurse(right, depth + 1)

        recurse(self, 0)
        return tuple(result)

    def with_start(self, point: Vec2) -> "CubicBezierSpan":
        return CubicBezierSpan(point, self.control1, self.control2, self.end, id=self.id)

    def with_end(self, point: Vec2) -> "CubicBezierSpan":
        return CubicBezierSpan(self.start, self.control1, self.control2, point, id=self.id)


Span = Union[LineSpan, ArcSpan, CubicBezierSpan]


def span_type_name(span: Span) -> str:
    if isinstance(span, LineSpan):
        return "line"
    if isinstance(span, ArcSpan):
        return "arc"
    if isinstance(span, CubicBezierSpan):
        return "cubic_bezier"
    raise TypeError("unsupported span type: %r" % type(span))
