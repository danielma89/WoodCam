"""Pure polygon offsets used by common-line planning and nesting validation."""

from __future__ import annotations

import math
from typing import Iterable, Sequence, Tuple


Point = Tuple[float, float]


def _area(points: Sequence[Point]) -> float:
    return 0.5 * sum(
        points[index][0] * points[(index + 1) % len(points)][1]
        - points[(index + 1) % len(points)][0] * points[index][1]
        for index in range(len(points))
    )


def _line_intersection(a1, a2, b1, b2):
    x1, y1 = a1
    x2, y2 = a2
    x3, y3 = b1
    x4, y4 = b2
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
    if abs(denominator) <= 1.0e-12:
        return None
    return (
        (
            (x1 * y2 - y1 * x2) * (x3 - x4)
            - (x1 - x2) * (x3 * y4 - y3 * x4)
        )
        / denominator,
        (
            (x1 * y2 - y1 * x2) * (y3 - y4)
            - (y1 - y2) * (x3 * y4 - y3 * x4)
        )
        / denominator,
    )


def _cross(first: Point, second: Point) -> float:
    return first[0] * second[1] - first[1] * second[0]


def _proper_or_touching_intersection(
    first_start: Point,
    first_end: Point,
    second_start: Point,
    second_end: Point,
    tolerance: float,
):
    """Return one finite non-collinear segment intersection, if present."""

    first_vector = (
        first_end[0] - first_start[0],
        first_end[1] - first_start[1],
    )
    second_vector = (
        second_end[0] - second_start[0],
        second_end[1] - second_start[1],
    )
    denominator = _cross(first_vector, second_vector)
    scale = max(
        1.0,
        math.hypot(*first_vector),
        math.hypot(*second_vector),
    )
    if abs(denominator) <= tolerance * scale:
        return None
    offset = (
        second_start[0] - first_start[0],
        second_start[1] - first_start[1],
    )
    first_parameter = _cross(offset, second_vector) / denominator
    second_parameter = _cross(offset, first_vector) / denominator
    parameter_tolerance = tolerance / scale
    if not (
        -parameter_tolerance <= first_parameter <= 1.0 + parameter_tolerance
        and -parameter_tolerance <= second_parameter <= 1.0 + parameter_tolerance
    ):
        return None
    first_parameter = max(0.0, min(1.0, first_parameter))
    return (
        first_start[0] + first_vector[0] * first_parameter,
        first_start[1] + first_vector[1] * first_parameter,
    )


def _first_self_crossing(points: Sequence[Point], tolerance: float):
    """Find a non-adjacent crossing using a sweep on the longest axis."""

    if len(points) < 4:
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    axis = 0 if max(xs) - min(xs) >= max(ys) - min(ys) else 1
    other_axis = 1 - axis
    segments = []
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        segments.append(
            (
                min(start[axis], end[axis]),
                max(start[axis], end[axis]),
                min(start[other_axis], end[other_axis]),
                max(start[other_axis], end[other_axis]),
                index,
                start,
                end,
            )
        )
    segments.sort(key=lambda value: (value[0], value[1], value[4]))
    active = []
    segment_count = len(points)
    for candidate in segments:
        minimum = candidate[0]
        active = [value for value in active if value[1] >= minimum - tolerance]
        for other in active:
            first_index, second_index = sorted((candidate[4], other[4]))
            if second_index == first_index + 1 or (
                first_index == 0 and second_index == segment_count - 1
            ):
                continue
            if (
                candidate[3] < other[2] - tolerance
                or other[3] < candidate[2] - tolerance
            ):
                continue
            intersection = _proper_or_touching_intersection(
                candidate[5],
                candidate[6],
                other[5],
                other[6],
                tolerance,
            )
            if intersection is not None:
                return first_index, second_index, intersection
        active.append(candidate)
    return None


def _dedupe_consecutive(points: Sequence[Point], tolerance: float):
    cleaned = []
    for point in points:
        if not cleaned or math.hypot(
            point[0] - cleaned[-1][0], point[1] - cleaned[-1][1]
        ) > tolerance:
            cleaned.append(point)
    if len(cleaned) > 1 and math.hypot(
        cleaned[0][0] - cleaned[-1][0], cleaned[0][1] - cleaned[-1][1]
    ) <= tolerance:
        cleaned.pop()
    return cleaned


def _trim_outward_offset_loops(points: Sequence[Point], tolerance: float):
    """Remove local loops produced when an offset exceeds a recess radius.

    The mathematical outside buffer of one connected polygon stays connected.
    A raw sequence of shifted edges, however, walks small reverse loops around
    concave details whose radius is smaller than the cutter.  At each crossing
    the loop with the original winding and greatest area is the physical outer
    boundary; the other branch is unreachable by the tool and is discarded.
    """

    cleaned = _dedupe_consecutive(points, tolerance)
    if len(cleaned) < 3:
        return cleaned
    expected_sign = 1.0 if _area(cleaned) >= 0.0 else -1.0
    maximum_iterations = max(8, len(cleaned))
    for _iteration in range(maximum_iterations):
        crossing = _first_self_crossing(cleaned, tolerance)
        if crossing is None:
            return cleaned
        first_index, second_index, intersection = crossing
        candidates = (
            _dedupe_consecutive(
                [intersection] + cleaned[first_index + 1 : second_index + 1],
                tolerance,
            ),
            _dedupe_consecutive(
                [intersection]
                + cleaned[second_index + 1 :]
                + cleaned[: first_index + 1],
                tolerance,
            ),
        )
        candidates = tuple(
            candidate
            for candidate in candidates
            if len(candidate) >= 3 and abs(_area(candidate)) > tolerance * tolerance
        )
        if not candidates:
            raise ValueError("A compensação externa colapsou o contorno.")
        replacement = max(
            candidates,
            key=lambda candidate: (
                1 if _area(candidate) * expected_sign > 0.0 else 0,
                abs(_area(candidate)),
                len(candidate),
            ),
        )
        if len(replacement) >= len(cleaned):
            raise ValueError(
                "Não foi possível aparar uma volta inválida da compensação externa."
            )
        cleaned = replacement
    raise ValueError("A compensação externa excedeu o limite seguro de limpeza.")


def round_offset_closed_polygon(
    points: Iterable[Sequence[float]],
    offset_distance: float,
    maximum_arc_step_degrees: float = 10.0,
    maximum_chord_error: float = 0.02,
) -> Tuple[Point, ...]:
    """Offset a simple polygon with physically correct round outer corners.

    A router cutter centre follows a radius around a convex part corner. An
    unlimited miter extends beyond the cutter radius at acute vertices and can
    create collisions that the physical tool does not require. Concave joins
    remain intersections of the two shifted edge lines.
    """

    source = [(float(point[0]), float(point[1])) for point in points]
    if len(source) > 1 and math.hypot(
        source[0][0] - source[-1][0], source[0][1] - source[-1][1]
    ) <= 1.0e-12:
        source.pop()
    if len(source) < 3:
        raise ValueError("O contorno precisa ter pelo menos três pontos.")
    distance = float(offset_distance)
    if not math.isfinite(distance):
        raise ValueError("A compensação precisa ser finita.")
    if abs(distance) <= 1.0e-12:
        return tuple(source)
    area = _area(source)
    if abs(area) <= 1.0e-12:
        raise ValueError("O contorno não possui área suficiente para compensação.")

    orientation = 1.0 if area > 0.0 else -1.0
    # Positive distance means outside regardless of winding.
    normal_sign = orientation if distance > 0.0 else -orientation
    radius = abs(distance)
    edges = []
    for index, start in enumerate(source):
        end = source[(index + 1) % len(source)]
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length <= 1.0e-12:
            raise ValueError("O contorno possui segmento de comprimento zero.")
        nx = dy / length * normal_sign
        ny = -dx / length * normal_sign
        edges.append(
            (
                (start[0] + nx * radius, start[1] + ny * radius),
                (end[0] + nx * radius, end[1] + ny * radius),
            )
        )

    result = []
    maximum_step = math.radians(
        max(1.0, min(45.0, float(maximum_arc_step_degrees)))
    )
    chord_error = max(1.0e-6, float(maximum_chord_error))
    if chord_error < radius:
        maximum_step = min(
            maximum_step,
            2.0 * math.acos(max(-1.0, 1.0 - chord_error / radius)),
        )
    for index, vertex in enumerate(source):
        previous_start, previous_end = edges[index - 1]
        current_start, current_end = edges[index]
        incoming = (
            vertex[0] - source[index - 1][0],
            vertex[1] - source[index - 1][1],
        )
        outgoing = (
            source[(index + 1) % len(source)][0] - vertex[0],
            source[(index + 1) % len(source)][1] - vertex[1],
        )
        turn = incoming[0] * outgoing[1] - incoming[1] * outgoing[0]
        convex_for_requested_side = turn * orientation * distance > 0.0
        if not convex_for_requested_side:
            intersection = _line_intersection(
                previous_start,
                previous_end,
                current_start,
                current_end,
            )
            result.append(intersection if intersection is not None else current_start)
            continue

        start_angle = math.atan2(
            previous_end[1] - vertex[1], previous_end[0] - vertex[0]
        )
        end_angle = math.atan2(
            current_start[1] - vertex[1], current_start[0] - vertex[0]
        )
        sweep_sign = orientation if distance > 0.0 else -orientation
        if sweep_sign > 0.0:
            sweep = (end_angle - start_angle) % math.tau
        else:
            sweep = -((start_angle - end_angle) % math.tau)
        steps = max(1, int(math.ceil(abs(sweep) / maximum_step)))
        for step in range(steps + 1):
            angle = start_angle + sweep * step / steps
            candidate = (
                vertex[0] + math.cos(angle) * radius,
                vertex[1] + math.sin(angle) * radius,
            )
            if not result or math.hypot(
                candidate[0] - result[-1][0], candidate[1] - result[-1][1]
            ) > 1.0e-10:
                result.append(candidate)
    if len(result) > 1 and math.hypot(
        result[0][0] - result[-1][0], result[0][1] - result[-1][1]
    ) <= 1.0e-10:
        result.pop()
    if distance > 0.0:
        result = _trim_outward_offset_loops(
            result,
            max(1.0e-9, radius * 1.0e-9),
        )
    return tuple(result)


def _has_only_orthogonal_corners(points: Sequence[Point]) -> bool:
    clean = list(points)
    if len(clean) > 1 and math.hypot(
        clean[0][0] - clean[-1][0], clean[0][1] - clean[-1][1]
    ) <= 1.0e-9:
        clean.pop()
    if len(clean) < 3:
        return False
    for index, point in enumerate(clean):
        previous = clean[index - 1]
        following = clean[(index + 1) % len(clean)]
        incoming = (point[0] - previous[0], point[1] - previous[1])
        outgoing = (following[0] - point[0], following[1] - point[1])
        incoming_length = math.hypot(*incoming)
        outgoing_length = math.hypot(*outgoing)
        if incoming_length <= 1.0e-9 or outgoing_length <= 1.0e-9:
            return False
        cosine = (
            incoming[0] * outgoing[0] + incoming[1] * outgoing[1]
        ) / (incoming_length * outgoing_length)
        if abs(cosine) > 1.0e-7:
            return False
    return True


def _miter_offset_closed_polygon(
    points: Sequence[Point],
    offset_distance: float,
) -> Tuple[Point, ...]:
    """Return the legacy miter offset used at orthogonal common-line nodes."""

    source = [(float(point[0]), float(point[1])) for point in points]
    if len(source) > 1 and math.hypot(
        source[0][0] - source[-1][0], source[0][1] - source[-1][1]
    ) <= 1.0e-12:
        source.pop()
    distance = float(offset_distance)
    if abs(distance) < 1.0e-9:
        return tuple(source)
    if len(source) < 3:
        raise ValueError("O contorno precisa ter pelo menos três pontos.")
    area = _area(source)
    if abs(area) < 1.0e-9:
        raise ValueError("O contorno não possui área suficiente para compensação.")

    normal_sign = 1.0 if area > 0.0 else -1.0
    edges = []
    for index, start in enumerate(source):
        end = source[(index + 1) % len(source)]
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length = math.hypot(dx, dy)
        if length < 1.0e-9:
            raise ValueError("O contorno possui segmento de comprimento zero.")
        nx = dy / length * normal_sign
        ny = -dx / length * normal_sign
        edges.append(
            (
                (start[0] + nx * distance, start[1] + ny * distance),
                (end[0] + nx * distance, end[1] + ny * distance),
                (nx, ny),
            )
        )

    result = []
    for index, point in enumerate(source):
        previous_start, previous_end, previous_normal = edges[index - 1]
        current_start, current_end, current_normal = edges[index]
        intersection = _line_intersection(
            previous_start,
            previous_end,
            current_start,
            current_end,
        )
        if intersection is None:
            nx = (previous_normal[0] + current_normal[0]) * 0.5
            ny = (previous_normal[1] + current_normal[1]) * 0.5
            normal_length = math.hypot(nx, ny)
            if normal_length < 1.0e-9:
                nx, ny = current_normal
            else:
                nx /= normal_length
                ny /= normal_length
            intersection = (
                point[0] + nx * distance,
                point[1] + ny * distance,
            )
        result.append(intersection)
    return tuple(result)


def common_line_offset_closed_polygon(
    points: Iterable[Sequence[float]],
    offset_distance: float,
) -> Tuple[Point, ...]:
    """Build the exact outside centre-line used by common-line cutting.

    Orthogonal contours retain miter nodes so shared T/cross junctions form a
    connected network. Other contours use the cutter's physical round corner.
    Nesting validation and final CAM generation must both call this function;
    otherwise a diagonal corner placement can pass the preview and cross only
    after the final common-line compensation.
    """

    source = tuple((float(point[0]), float(point[1])) for point in points)
    if _has_only_orthogonal_corners(source):
        return _miter_offset_closed_polygon(source, offset_distance)
    return round_offset_closed_polygon(source, offset_distance)


__all__ = [
    "common_line_offset_closed_polygon",
    "round_offset_closed_polygon",
]
