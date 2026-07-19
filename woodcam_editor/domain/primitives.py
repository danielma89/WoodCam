"""Small immutable 2D primitives.

Coordinates are always millimetres in a Cartesian, Y-up coordinate system.
No function in this module performs implicit rounding.  Geometric comparisons
must pass an explicit tolerance, or consciously use :data:`DEFAULT_EPSILON`.
"""

from __future__ import annotations

from dataclasses import dataclass
import math
import uuid
from typing import Iterable, Sequence, Tuple


DEFAULT_EPSILON = 1.0e-9


class GeometryError(ValueError):
    """Raised when an operation cannot produce valid geometry."""


class InvariantError(GeometryError):
    """Raised when a domain object violates a structural invariant."""


def new_id(prefix: str) -> str:
    """Return a stable, serialisable ID with a human-readable type prefix."""

    prefix = str(prefix).strip().replace(" ", "-") or "id"
    return "%s-%s" % (prefix, uuid.uuid4())


def _finite(value: float, label: str) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise GeometryError("%s must be finite" % label)
    return value


@dataclass(frozen=True, slots=True)
class Vec2:
    """An immutable point/vector in millimetres."""

    x: float
    y: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "x", _finite(self.x, "x"))
        object.__setattr__(self, "y", _finite(self.y, "y"))

    @classmethod
    def from_sequence(cls, values: Sequence[float]) -> "Vec2":
        if len(values) != 2:
            raise GeometryError("Vec2 requires exactly two coordinates")
        return cls(values[0], values[1])

    def to_tuple(self) -> Tuple[float, float]:
        return (self.x, self.y)

    def __add__(self, other: "Vec2") -> "Vec2":
        if not isinstance(other, Vec2):
            return NotImplemented
        return Vec2(self.x + other.x, self.y + other.y)

    def __sub__(self, other: "Vec2") -> "Vec2":
        if not isinstance(other, Vec2):
            return NotImplemented
        return Vec2(self.x - other.x, self.y - other.y)

    def __neg__(self) -> "Vec2":
        return Vec2(-self.x, -self.y)

    def __mul__(self, scalar: float) -> "Vec2":
        scalar = _finite(scalar, "scalar")
        return Vec2(self.x * scalar, self.y * scalar)

    def __rmul__(self, scalar: float) -> "Vec2":
        return self * scalar

    def __truediv__(self, scalar: float) -> "Vec2":
        scalar = _finite(scalar, "scalar")
        if abs(scalar) <= DEFAULT_EPSILON:
            raise ZeroDivisionError("cannot divide Vec2 by zero")
        return Vec2(self.x / scalar, self.y / scalar)

    def dot(self, other: "Vec2") -> float:
        return self.x * other.x + self.y * other.y

    def cross(self, other: "Vec2") -> float:
        return self.x * other.y - self.y * other.x

    def length_squared(self) -> float:
        return self.dot(self)

    def length(self) -> float:
        return math.hypot(self.x, self.y)

    def normalized(self, tolerance: float = DEFAULT_EPSILON) -> "Vec2":
        size = self.length()
        if size <= tolerance:
            raise GeometryError("cannot normalize a zero-length vector")
        return self / size

    def distance_to(self, other: "Vec2") -> float:
        return (self - other).length()

    def distance_squared_to(self, other: "Vec2") -> float:
        return (self - other).length_squared()

    def almost_equals(self, other: "Vec2", tolerance: float = DEFAULT_EPSILON) -> bool:
        return self.distance_squared_to(other) <= float(tolerance) ** 2

    def lerp(self, other: "Vec2", t: float) -> "Vec2":
        t = _finite(t, "t")
        return self + (other - self) * t

    def perpendicular_left(self) -> "Vec2":
        return Vec2(-self.y, self.x)

    def angle(self) -> float:
        return math.atan2(self.y, self.x)

    def rotated(self, angle_radians: float, origin: "Vec2" | None = None) -> "Vec2":
        origin = origin or Vec2(0.0, 0.0)
        relative = self - origin
        cosine = math.cos(angle_radians)
        sine = math.sin(angle_radians)
        return origin + Vec2(
            cosine * relative.x - sine * relative.y,
            sine * relative.x + cosine * relative.y,
        )


@dataclass(frozen=True, slots=True)
class BBox2D:
    """Closed, finite, axis-aligned bounds."""

    min_x: float
    min_y: float
    max_x: float
    max_y: float

    def __post_init__(self) -> None:
        for name in ("min_x", "min_y", "max_x", "max_y"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))
        if self.max_x < self.min_x or self.max_y < self.min_y:
            raise GeometryError("invalid BBox2D limits")

    @classmethod
    def from_points(cls, points: Iterable[Vec2]) -> "BBox2D":
        points = tuple(points)
        if not points:
            raise GeometryError("cannot build bounds from no points")
        return cls(
            min(point.x for point in points),
            min(point.y for point in points),
            max(point.x for point in points),
            max(point.y for point in points),
        )

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    @property
    def center(self) -> Vec2:
        return Vec2((self.min_x + self.max_x) * 0.5, (self.min_y + self.max_y) * 0.5)

    def corners(self) -> Tuple[Vec2, Vec2, Vec2, Vec2]:
        return (
            Vec2(self.min_x, self.min_y),
            Vec2(self.max_x, self.min_y),
            Vec2(self.max_x, self.max_y),
            Vec2(self.min_x, self.max_y),
        )

    def union(self, other: "BBox2D") -> "BBox2D":
        return BBox2D(
            min(self.min_x, other.min_x),
            min(self.min_y, other.min_y),
            max(self.max_x, other.max_x),
            max(self.max_y, other.max_y),
        )

    def expanded(self, amount: float) -> "BBox2D":
        amount = _finite(amount, "amount")
        if amount < 0.0 and (-amount * 2.0 > min(self.width, self.height)):
            raise GeometryError("negative expansion collapses bounds")
        return BBox2D(
            self.min_x - amount,
            self.min_y - amount,
            self.max_x + amount,
            self.max_y + amount,
        )

    def translated(self, delta: Vec2) -> "BBox2D":
        return BBox2D(
            self.min_x + delta.x,
            self.min_y + delta.y,
            self.max_x + delta.x,
            self.max_y + delta.y,
        )

    def contains_point(self, point: Vec2, tolerance: float = 0.0) -> bool:
        return (
            self.min_x - tolerance <= point.x <= self.max_x + tolerance
            and self.min_y - tolerance <= point.y <= self.max_y + tolerance
        )

    def contains_bbox(self, other: "BBox2D", tolerance: float = 0.0) -> bool:
        return (
            self.min_x - tolerance <= other.min_x
            and self.min_y - tolerance <= other.min_y
            and self.max_x + tolerance >= other.max_x
            and self.max_y + tolerance >= other.max_y
        )

    def intersects(self, other: "BBox2D", tolerance: float = 0.0) -> bool:
        return not (
            self.max_x < other.min_x - tolerance
            or other.max_x < self.min_x - tolerance
            or self.max_y < other.min_y - tolerance
            or other.max_y < self.min_y - tolerance
        )

    def transformed(self, transform: "Affine2D") -> "BBox2D":
        return BBox2D.from_points(transform.apply_to_point(point) for point in self.corners())


@dataclass(frozen=True, slots=True)
class Affine2D:
    """A 2D affine transform.

    Stored as ``x' = a*x + c*y + tx`` and
    ``y' = b*x + d*y + ty``. ``left @ right`` applies ``right`` first.
    """

    a: float = 1.0
    b: float = 0.0
    c: float = 0.0
    d: float = 1.0
    tx: float = 0.0
    ty: float = 0.0

    def __post_init__(self) -> None:
        for name in ("a", "b", "c", "d", "tx", "ty"):
            object.__setattr__(self, name, _finite(getattr(self, name), name))

    @classmethod
    def identity(cls) -> "Affine2D":
        return cls()

    @classmethod
    def translation(cls, delta: Vec2 | float, y: float | None = None) -> "Affine2D":
        if isinstance(delta, Vec2):
            if y is not None:
                raise GeometryError("y must be omitted when delta is Vec2")
            return cls(tx=delta.x, ty=delta.y)
        if y is None:
            raise GeometryError("translation requires x and y")
        return cls(tx=float(delta), ty=float(y))

    @classmethod
    def rotation(cls, angle_radians: float, origin: Vec2 | None = None) -> "Affine2D":
        cosine = math.cos(angle_radians)
        sine = math.sin(angle_radians)
        rotation = cls(cosine, sine, -sine, cosine)
        if origin is None:
            return rotation
        return cls.translation(origin) @ rotation @ cls.translation(-origin)

    @classmethod
    def scaling(
        cls,
        scale_x: float,
        scale_y: float | None = None,
        origin: Vec2 | None = None,
    ) -> "Affine2D":
        scale_y = scale_x if scale_y is None else scale_y
        scaling = cls(a=scale_x, d=scale_y)
        if origin is None:
            return scaling
        return cls.translation(origin) @ scaling @ cls.translation(-origin)

    @classmethod
    def mirror_x(cls, x: float = 0.0) -> "Affine2D":
        return cls.translation(2.0 * x, 0.0) @ cls(a=-1.0)

    @classmethod
    def mirror_y(cls, y: float = 0.0) -> "Affine2D":
        return cls.translation(0.0, 2.0 * y) @ cls(d=-1.0)

    @property
    def determinant(self) -> float:
        return self.a * self.d - self.b * self.c

    def is_identity(self, tolerance: float = DEFAULT_EPSILON) -> bool:
        return self.almost_equals(Affine2D.identity(), tolerance)

    def is_similarity(self, tolerance: float = 1.0e-9) -> bool:
        column_x = Vec2(self.a, self.b)
        column_y = Vec2(self.c, self.d)
        scale_x = column_x.length()
        scale_y = column_y.length()
        return (
            scale_x > tolerance
            and scale_y > tolerance
            and abs(column_x.dot(column_y)) <= tolerance * max(1.0, scale_x * scale_y)
            and abs(scale_x - scale_y) <= tolerance * max(1.0, scale_x, scale_y)
        )

    def uniform_scale(self, tolerance: float = 1.0e-9) -> float:
        if not self.is_similarity(tolerance):
            raise GeometryError("transform is not a similarity")
        return Vec2(self.a, self.b).length()

    def apply_to_point(self, point: Vec2) -> Vec2:
        return Vec2(
            self.a * point.x + self.c * point.y + self.tx,
            self.b * point.x + self.d * point.y + self.ty,
        )

    def apply_to_vector(self, vector: Vec2) -> Vec2:
        return Vec2(
            self.a * vector.x + self.c * vector.y,
            self.b * vector.x + self.d * vector.y,
        )

    def __matmul__(self, other: "Affine2D") -> "Affine2D":
        if not isinstance(other, Affine2D):
            return NotImplemented
        return Affine2D(
            a=self.a * other.a + self.c * other.b,
            b=self.b * other.a + self.d * other.b,
            c=self.a * other.c + self.c * other.d,
            d=self.b * other.c + self.d * other.d,
            tx=self.a * other.tx + self.c * other.ty + self.tx,
            ty=self.b * other.tx + self.d * other.ty + self.ty,
        )

    def inverse(self, tolerance: float = DEFAULT_EPSILON) -> "Affine2D":
        determinant = self.determinant
        if abs(determinant) <= tolerance:
            raise GeometryError("transform is singular")
        inverse_linear = Affine2D(
            self.d / determinant,
            -self.b / determinant,
            -self.c / determinant,
            self.a / determinant,
        )
        inverse_translation = inverse_linear.apply_to_vector(Vec2(-self.tx, -self.ty))
        return Affine2D(
            inverse_linear.a,
            inverse_linear.b,
            inverse_linear.c,
            inverse_linear.d,
            inverse_translation.x,
            inverse_translation.y,
        )

    def almost_equals(self, other: "Affine2D", tolerance: float = DEFAULT_EPSILON) -> bool:
        return all(
            abs(a - b) <= tolerance
            for a, b in zip(
                (self.a, self.b, self.c, self.d, self.tx, self.ty),
                (other.a, other.b, other.c, other.d, other.tx, other.ty),
            )
        )

