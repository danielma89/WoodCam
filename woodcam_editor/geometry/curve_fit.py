"""Conservative exact-primitive fitting for imported polyline vectors.

The editor never silently changes an imported drawing.  These helpers only
offer a replacement when every source vertex lies within the caller's stated
tolerance of one exact circle/arc; otherwise they return ``None``.
"""

from __future__ import annotations

import math
from dataclasses import replace
from typing import Optional, Sequence, Tuple

from woodcam_editor.domain.entities import CircleEntity, PathEntity
from woodcam_editor.domain.primitives import GeometryError, Vec2
from woodcam_editor.domain.spans import ArcSpan, LineSpan


def _solve_3x3(rows: Sequence[Sequence[float]], values: Sequence[float]):
    """Small deterministic Gaussian elimination, without a numeric dependency."""

    matrix = [list(map(float, row)) + [float(value)] for row, value in zip(rows, values)]
    if len(matrix) != 3 or any(len(row) != 4 for row in matrix):
        raise GeometryError("circle fit requires a 3×3 system")
    for column in range(3):
        pivot = max(range(column, 3), key=lambda row: abs(matrix[row][column]))
        if abs(matrix[pivot][column]) <= 1.0e-12:
            raise GeometryError("points do not define a stable circle")
        matrix[column], matrix[pivot] = matrix[pivot], matrix[column]
        divisor = matrix[column][column]
        matrix[column] = [value / divisor for value in matrix[column]]
        for row in range(3):
            if row == column:
                continue
            factor = matrix[row][column]
            matrix[row] = [
                matrix[row][index] - factor * matrix[column][index]
                for index in range(4)
            ]
    return tuple(matrix[index][3] for index in range(3))


def fit_circle(points: Sequence[Vec2]) -> Tuple[Vec2, float, float]:
    """Least-squares circle through 3+ points, returning centre/radius/error."""

    points = tuple(points)
    if len(points) < 3:
        raise GeometryError("at least three points are needed to fit a circle")
    sx = sum(point.x for point in points)
    sy = sum(point.y for point in points)
    sxx = sum(point.x * point.x for point in points)
    syy = sum(point.y * point.y for point in points)
    sxy = sum(point.x * point.y for point in points)
    sxq = sum(point.x * (point.x * point.x + point.y * point.y) for point in points)
    syq = sum(point.y * (point.x * point.x + point.y * point.y) for point in points)
    sq = sum(point.x * point.x + point.y * point.y for point in points)
    d, e, f = _solve_3x3(
        (
            (sxx, sxy, sx),
            (sxy, syy, sy),
            (sx, sy, float(len(points))),
        ),
        (-sxq, -syq, -sq),
    )
    centre = Vec2(-d * 0.5, -e * 0.5)
    radius_squared = centre.length_squared() - f
    if radius_squared <= 1.0e-12:
        raise GeometryError("fitted circle radius is not positive")
    radius = math.sqrt(radius_squared)
    maximum_error = max(abs(point.distance_to(centre) - radius) for point in points)
    return centre, radius, maximum_error


def _turn_direction(points: Sequence[Vec2], centre: Vec2, closed: bool) -> Optional[bool]:
    """Return clockwise status if every chord turns consistently."""

    vectors = tuple(point - centre for point in points)
    signs = []
    count = len(vectors) if closed else len(vectors) - 1
    for index in range(count):
        cross = vectors[index].cross(vectors[(index + 1) % len(vectors)])
        # Ignore numerically straight adjacent samples; reject actual reversals.
        if abs(cross) > 1.0e-9:
            signs.append(1 if cross > 0.0 else -1)
    if not signs or any(sign != signs[0] for sign in signs):
        return None
    return signs[0] < 0


def fit_polyline_to_arc(entity, tolerance: float):
    """Return an exact CircleEntity/ArcSpan path or ``None`` when unsafe.

    Only all-line paths are candidates.  This deliberately avoids touching
    hand-authored arcs, Béziers and mixed geometry.
    """

    tolerance = float(tolerance)
    if tolerance <= 0.0 or not isinstance(entity, PathEntity):
        return None
    if not entity.spans or not all(isinstance(span, LineSpan) for span in entity.spans):
        return None
    points = [span.start for span in entity.spans]
    if not entity.closed:
        points.append(entity.spans[-1].end)
    # Four corners of a rectangle are mathematically cocircular, but they are
    # plainly not a scanned circle.  Be deliberately conservative: this tool
    # is for dense traced/imported polylines, never for rewriting simple CAD
    # polygons based on a coincidence.
    minimum_vertices = 8 if entity.closed else 4
    if len(points) < minimum_vertices:
        return None
    try:
        centre, radius, maximum_error = fit_circle(points)
    except GeometryError:
        return None
    if maximum_error > tolerance:
        return None
    clockwise = _turn_direction(points, centre, entity.closed)
    if clockwise is None:
        return None
    metadata = dict(entity.metadata)
    metadata.update(
        {
            "curve_fit": "circle" if entity.closed else "arc",
            "curve_fit_max_error_mm": maximum_error,
            "curve_fit_source_vertices": len(points),
        }
    )
    if entity.closed:
        return CircleEntity(
            layer_id=entity.layer_id,
            center=centre,
            radius=radius,
            id=entity.id,
            metadata=metadata,
        )
    # A non-closed polyline cannot be represented by an arc if it travelled a
    # complete revolution. ArcSpan intentionally reserves that case for a
    # CircleEntity.
    start, end = points[0], points[-1]
    if start.almost_equals(end, tolerance):
        return None
    span = ArcSpan(start, end, centre, clockwise=clockwise)
    if abs(span.sweep_angle) <= 1.0e-9:
        return None
    return PathEntity(
        layer_id=entity.layer_id,
        spans=(span,),
        closed=False,
        id=entity.id,
        node_ids=entity.node_ids[0:1] + entity.node_ids[-1:],
        metadata=metadata,
    )


__all__ = ["fit_circle", "fit_polyline_to_arc"]
