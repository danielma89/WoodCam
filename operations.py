import heapq
import itertools
import math

from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.geometry.polygon_offset import (
    common_line_offset_closed_polygon,
    round_offset_closed_polygon,
)


CUT_SIDE_OUTSIDE = "outside"
CUT_SIDE_ON_LINE = "on_line"
CUT_SIDE_INSIDE = "inside"
VALID_CUT_SIDES = {
    CUT_SIDE_OUTSIDE,
    CUT_SIDE_ON_LINE,
    CUT_SIDE_INSIDE,
}


def distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def tab_retained_cut_depth(
    material_thickness,
    tab_height,
    *,
    final_depth=None,
    start_depth=0.0,
):
    """Return the deepest cut allowed inside a tab, measured from material top.

    ``tab_height`` is physical untouched material, not a value relative to the
    final tool depth.  The legacy fallback is intentionally retained for pure
    callers that do not yet provide material thickness.
    """

    height = max(0.0, float(tab_height or 0.0))
    initial = max(0.0, abs(float(start_depth or 0.0)))
    if material_thickness is None:
        if final_depth is None:
            return initial
        return max(initial, abs(float(final_depth)) - height)
    material = abs(float(material_thickness))
    if height > material + 1.0e-7:
        raise ValueError(
            "A altura intacta da tab não pode ultrapassar a espessura do material."
        )
    retained = max(0.0, material - height)
    # ``start_depth`` skips material on the ordinary cutting spans; it must not
    # lower a bridge measured from the original material top.  The cutter can
    # approach from a deeper pass and rise locally to ``retained``.  Assuming
    # that a previous, unrelated operation already removed the bridge would be
    # both unverifiable and contrary to the explicit physical tab height.
    return retained


def _tab_target_z(
    material_thickness,
    tab_height,
    *,
    final_depth,
    start_depth,
    previous_pass_depth,
    final_pass,
    surface_clearance=0.2,
):
    retained = tab_retained_cut_depth(
        material_thickness,
        tab_height,
        final_depth=final_depth,
        start_depth=start_depth,
    )
    material = (
        None if material_thickness is None else abs(float(material_thickness))
    )
    if material is not None and float(tab_height or 0.0) >= material - 1.0e-7:
        return max(0.0, float(surface_clearance or 0.0))
    if final_pass:
        allowed_depth = retained
    else:
        allowed_depth = min(retained, max(0.0, abs(float(previous_pass_depth))))
    return -allowed_depth


def _tab_uses_full_material_height(material_thickness, tab_height):
    return (
        material_thickness is not None
        and abs(float(tab_height or 0.0))
        >= abs(float(material_thickness)) - 1.0e-7
    )


def _normalize_vector(a, b):
    dx = b[0] - a[0]
    dy = b[1] - a[1]
    length = math.hypot(dx, dy)
    if length < 1e-9:
        return 0.0, 0.0, 0.0
    return dx / length, dy / length, length


def _ramp_endpoint(points, ramp_length):
    if len(points) < 2:
        return points[0]

    start = points[0]
    next_pt = points[1]
    dx, dy, seg_length = _normalize_vector(start, next_pt)
    if seg_length < 1e-9:
        return next_pt

    t = min(ramp_length / seg_length, 1.0)
    return (start[0] + dx * seg_length * t, start[1] + dy * seg_length * t)


def polygon_area(points):
    if len(points) < 3:
        return 0.0

    area = 0.0
    for idx, point in enumerate(points):
        next_point = points[(idx + 1) % len(points)]
        area += point[0] * next_point[1] - next_point[0] * point[1]
    return area * 0.5


def _point_in_polygon(point, polygon):
    inside = False
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        if (start[1] > point[1]) == (end[1] > point[1]):
            continue
        x_at_y = (
            (end[0] - start[0])
            * (point[1] - start[1])
            / (end[1] - start[1])
            + start[0]
        )
        if point[0] < x_at_y:
            inside = not inside
    return inside


def split_nested_contours(contours):
    """Separa contornos externos e internos pela paridade de contenção."""
    clean_contours = [_clean_closed_points(contour) for contour in contours if contour]
    outer = []
    inner = []

    for index, contour in enumerate(clean_contours):
        probe = contour[0]
        nesting_depth = sum(
            _point_in_polygon(probe, other)
            for other_index, other in enumerate(clean_contours)
            if other_index != index
        )
        (inner if nesting_depth % 2 else outer).append(contour)

    return outer, inner


def _line_intersection(a1, a2, b1, b2):
    x1, y1 = a1
    x2, y2 = a2
    x3, y3 = b1
    x4, y4 = b2
    denominator = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)

    if abs(denominator) < 1e-9:
        return None

    px = (
        (x1 * y2 - y1 * x2) * (x3 - x4)
        - (x1 - x2) * (x3 * y4 - y3 * x4)
    ) / denominator
    py = (
        (x1 * y2 - y1 * x2) * (y3 - y4)
        - (y1 - y2) * (x3 * y4 - y3 * x4)
    ) / denominator
    return px, py


def offset_closed_polygon(points, offset_distance):
    if abs(offset_distance) < 1e-9:
        return list(points)
    if len(points) < 3:
        raise ValueError("O contorno precisa ter pelo menos tres pontos para compensar a fresa.")

    area = polygon_area(points)
    if abs(area) < 1e-9:
        raise ValueError("O contorno nao tem area suficiente para calcular compensacao externa.")

    # Em um poligono anti-horario o lado externo fica a direita da aresta.
    normal_sign = 1.0 if area > 0 else -1.0
    edge_data = []
    point_count = len(points)

    for idx in range(point_count):
        start = points[idx]
        end = points[(idx + 1) % point_count]
        dx, dy, length = _normalize_vector(start, end)
        if length < 1e-9:
            raise ValueError("O contorno possui segmentos zerados. Limpe o Sketch e tente novamente.")

        nx = dy * normal_sign
        ny = -dx * normal_sign
        offset_start = (start[0] + nx * offset_distance, start[1] + ny * offset_distance)
        offset_end = (end[0] + nx * offset_distance, end[1] + ny * offset_distance)
        edge_data.append((offset_start, offset_end, (nx, ny)))

    offset_points = []
    for idx in range(point_count):
        previous_start, previous_end, previous_normal = edge_data[idx - 1]
        current_start, current_end, current_normal = edge_data[idx]
        intersection = _line_intersection(previous_start, previous_end, current_start, current_end)

        if intersection is None:
            original = points[idx]
            nx = (previous_normal[0] + current_normal[0]) * 0.5
            ny = (previous_normal[1] + current_normal[1]) * 0.5
            normal_length = math.hypot(nx, ny)
            if normal_length < 1e-9:
                nx, ny = current_normal
            else:
                nx /= normal_length
                ny /= normal_length
            intersection = (original[0] + nx * offset_distance, original[1] + ny * offset_distance)

        offset_points.append(intersection)

    return offset_points


def normalize_cut_side(cut_side=None, compensate_external=None):
    if cut_side is None:
        if compensate_external is False:
            return CUT_SIDE_ON_LINE
        return CUT_SIDE_OUTSIDE

    normalized = str(cut_side).strip().lower()
    if normalized not in VALID_CUT_SIDES:
        raise ValueError(f"Tipo de corte inválido: {cut_side!r}.")
    return normalized


def _orient_contour_for_cut(points, cut_side, climb=None):
    """Ajusta o sentido do contorno para usinagem subida/convencional."""
    if climb is None or len(points) < 3:
        return list(points)
    normalized_side = normalize_cut_side(cut_side)
    is_clockwise = polygon_area(points) < 0.0
    if normalized_side == CUT_SIDE_INSIDE:
        desired_clockwise = not bool(climb)
    else:
        desired_clockwise = bool(climb)
    if is_clockwise == desired_clockwise:
        return list(points)
    return list(reversed(points))


def compensated_polygon(
    points,
    tool_diameter,
    cut_side,
    *,
    common_line_join=False,
):
    normalized_side = normalize_cut_side(cut_side)
    radius = abs(float(tool_diameter)) * 0.5

    if normalized_side == CUT_SIDE_ON_LINE or radius < 1e-9:
        return list(points)

    if normalized_side == CUT_SIDE_INSIDE:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        if max(xs) - min(xs) <= radius * 2.0 or max(ys) - min(ys) <= radius * 2.0:
            raise ValueError(
                "Não foi possível compensar a fresa para dentro. "
                "O contorno pode ser menor que a fresa ou estreito demais."
            )

    offset_distance = radius if normalized_side == CUT_SIDE_OUTSIDE else -radius
    if normalized_side == CUT_SIDE_OUTSIDE and common_line_join:
        # The organizer validates this same shared geometry before presenting
        # a layout, including the miter nodes required by orthogonal T/cross
        # junctions and the physical round corners of other contours.
        offset_points = list(
            common_line_offset_closed_polygon(points, offset_distance)
        )
    elif normalized_side == CUT_SIDE_OUTSIDE:
        # A ferramenta contorna cantos convexos pelo próprio raio. Um miter
        # ilimitado cria pontas que a fresa física não percorre e pode cruzar
        # peças vizinhas em vértices agudos.
        offset_points = list(
            round_offset_closed_polygon(points, offset_distance)
        )
    else:
        offset_points = offset_closed_polygon(points, offset_distance)

    if normalized_side == CUT_SIDE_INSIDE:
        original_area = abs(polygon_area(points))
        offset_area = abs(polygon_area(offset_points))
        if offset_area <= 1e-7 or offset_area >= original_area - 1e-7:
            raise ValueError(
                "Não foi possível compensar a fresa para dentro. "
                "O contorno pode ser menor que a fresa ou estreito demais."
            )

    return offset_points


def _simplify_open_centerline(points, tolerance=1.0e-7):
    """Remove only redundant collinear samples from an open centre-line."""

    clean = []
    for point in points:
        candidate = (float(point[0]), float(point[1]))
        if clean and distance(clean[-1], candidate) <= tolerance:
            continue
        clean.append(candidate)
    if len(clean) <= 2:
        return tuple(clean)

    simplified = [clean[0]]
    for index, point in enumerate(clean[1:-1], start=1):
        previous = simplified[-1]
        following = clean[index + 1]
        first = (point[0] - previous[0], point[1] - previous[1])
        second = (following[0] - point[0], following[1] - point[1])
        first_length = math.hypot(*first)
        second_length = math.hypot(*second)
        cross = abs(first[0] * second[1] - first[1] * second[0])
        forward = first[0] * second[0] + first[1] * second[1]
        if (
            first_length > tolerance
            and second_length > tolerance
            and cross <= tolerance * first_length * second_length
            and forward >= 0.0
        ):
            continue
        simplified.append(point)
    simplified.append(clean[-1])
    return tuple(simplified)


def _collapsed_slot_medial_path(local_points, tool_diameter):
    """Return the minimum-width tool centre path inside an open slot.

    ``local_points`` follows one slot wall from the mouth to the bottom and
    returns along the opposite wall.  Cross-sections perpendicular to the
    mouth pair both walls and use their midpoint.  Unlike following the two
    design edges, this removes only the unavoidable difference between the
    physical cutter diameter and the local slot width.  Tapers and orthogonal
    steps therefore remain centred instead of producing two full-radius cuts.

    ``None`` means that the collapsed run is not reliably shaped like one
    open, monotonic slot; the caller must retain its conservative fallback.
    """

    local = []
    for point in local_points:
        candidate = (float(point[0]), float(point[1]))
        if not local or distance(local[-1], candidate) > 1.0e-9:
            local.append(candidate)
    if len(local) < 4:
        return None

    mouth_a = local[0]
    mouth_b = local[-1]
    mouth_dx = mouth_b[0] - mouth_a[0]
    mouth_dy = mouth_b[1] - mouth_a[1]
    mouth_width = math.hypot(mouth_dx, mouth_dy)
    if mouth_width <= 1.0e-7:
        return None
    lateral = (mouth_dx / mouth_width, mouth_dy / mouth_width)
    depth_axis = (-lateral[1], lateral[0])
    entrance = (
        (mouth_a[0] + mouth_b[0]) * 0.5,
        (mouth_a[1] + mouth_b[1]) * 0.5,
    )

    def project(point, axis):
        return (
            (point[0] - entrance[0]) * axis[0]
            + (point[1] - entrance[1]) * axis[1]
        )

    raw_depths = [project(point, depth_axis) for point in local]
    if abs(min(raw_depths)) > abs(max(raw_depths)):
        depth_axis = (-depth_axis[0], -depth_axis[1])
        raw_depths = [-value for value in raw_depths]
    max_depth = max(raw_depths)
    if max_depth <= 1.0e-7:
        return None

    # A little numerical drift at the mouth is harmless.  A run that travels
    # materially behind its own entrance is not a single open slot and must
    # not be converted to a medial trajectory automatically.
    backward_tolerance = max(1.0e-6, max_depth * 0.02)
    if min(raw_depths) < -backward_tolerance:
        return None

    depths = [max(0.0, min(max_depth, value)) for value in raw_depths]
    base_levels = []
    for value in sorted(depths + [0.0, max_depth]):
        if not base_levels or abs(value - base_levels[-1]) > 1.0e-7:
            base_levels.append(value)
    levels = list(base_levels)
    levels.extend(
        (start + end) * 0.5
        for start, end in zip(base_levels, base_levels[1:])
        if end - start > 1.0e-7
    )
    levels.sort()

    lateral_values = [project(point, lateral) for point in local]
    centre_samples = []
    for level in levels:
        intersections = []
        for index in range(len(local) - 1):
            start_depth = depths[index]
            end_depth = depths[index + 1]
            start_lateral = lateral_values[index]
            end_lateral = lateral_values[index + 1]
            delta_depth = end_depth - start_depth
            if abs(delta_depth) <= 1.0e-9:
                if abs(level - start_depth) <= 1.0e-7:
                    intersections.extend((start_lateral, end_lateral))
                continue
            ratio = (level - start_depth) / delta_depth
            if -1.0e-7 <= ratio <= 1.0 + 1.0e-7:
                ratio = max(0.0, min(1.0, ratio))
                intersections.append(
                    start_lateral
                    + (end_lateral - start_lateral) * ratio
                )

        unique = []
        for value in sorted(intersections):
            if not unique or abs(value - unique[-1]) > 1.0e-7:
                unique.append(value)
        if len(unique) >= 2:
            centre_lateral = (unique[0] + unique[-1]) * 0.5
        elif len(unique) == 1 and abs(level - max_depth) <= 1.0e-7:
            # A tapered slot may close in a single deepest vertex.
            centre_lateral = unique[0]
        else:
            continue
        centre_samples.append(
            (
                entrance[0]
                + depth_axis[0] * level
                + lateral[0] * centre_lateral,
                entrance[1]
                + depth_axis[1] * level
                + lateral[1] * centre_lateral,
            )
        )

    centreline = _simplify_open_centerline(centre_samples)
    if len(centreline) < 2 or distance(centreline[0], entrance) > 1.0e-5:
        return None

    # The local slot polygon is the two walls plus the implicit mouth chord.
    # Every interior centre sample must stay inside it; otherwise this was a
    # branched/looping collapsed detail rather than one slot.
    slot_polygon = tuple(local)
    for point in centreline[1:-1]:
        if not _point_in_polygon(point, slot_polygon):
            return None

    diameter = abs(float(tool_diameter))
    if diameter <= 1.0e-9:
        return None
    return centreline


def external_compensation_detail_loss(points, tool_diameter):
    """Return skipped outside details and their local cleanup paths.

    A cutter centre-line offset is allowed to round corners by its physical
    radius, but it must not silently bridge across a deep slot that is narrower
    than the cutter.  For each source edge we sample interior points and compare
    their distance to the compensated loop.  A distance materially greater
    than the radius means that a source region became unreachable.

    The result is presentation-neutral evidence for the caller's confirmation
    workflow.  A narrow open slot is reduced to its medial centre-line,
    including tapered and stepped walls.  Only collapsed details that cannot
    be interpreted safely as one slot retain their local source polyline.  The
    complete outer contour is never changed to an on-vector cut.

    ``None`` means that the normal compensated contour represents the complete
    source boundary within the cutter-radius envelope.
    """

    source = _clean_closed_points(points)
    diameter = abs(float(tool_diameter))
    radius = diameter * 0.5
    if len(source) < 3 or radius <= 1.0e-9:
        return None
    compensated = compensated_polygon(source, diameter, CUT_SIDE_OUTSIDE)
    compensated_path = _closed_path(compensated)
    if len(compensated_path) < 2:
        return None

    def point_segment_distance(point, start, end):
        dx = end[0] - start[0]
        dy = end[1] - start[1]
        length_squared = dx * dx + dy * dy
        if length_squared <= 1.0e-18:
            return distance(point, start)
        ratio = (
            (point[0] - start[0]) * dx + (point[1] - start[1]) * dy
        ) / length_squared
        ratio = max(0.0, min(1.0, ratio))
        projected = (start[0] + dx * ratio, start[1] + dy * ratio)
        return distance(point, projected)

    # Tessellated arcs and ordinary corner rounding can deviate by a few
    # hundredths.  Requiring 25% more than the radius prevents those harmless
    # effects from becoming warnings while still finding a collapsed slot.
    allowed_distance = radius * 1.25 + 0.02
    min_x = min(point[0] for point in compensated)
    min_y = min(point[1] for point in compensated)
    max_x = max(point[0] for point in compensated)
    max_y = max(point[1] for point in compensated)
    diagonal = math.hypot(max_x - min_x, max_y - min_y)
    cell_size = max(
        allowed_distance * 2.0,
        diagonal / max(math.sqrt(len(compensated)), 1.0),
        1.0e-6,
    )

    def cell_index(point):
        return (
            int(math.floor((point[0] - min_x) / cell_size)),
            int(math.floor((point[1] - min_y) / cell_size)),
        )

    compensated_segments = tuple(zip(compensated_path, compensated_path[1:]))
    spatial_segments = {}
    for segment_index, (segment_start, segment_end) in enumerate(
        compensated_segments
    ):
        first_cell = cell_index(
            (
                min(segment_start[0], segment_end[0]),
                min(segment_start[1], segment_end[1]),
            )
        )
        last_cell = cell_index(
            (
                max(segment_start[0], segment_end[0]),
                max(segment_start[1], segment_end[1]),
            )
        )
        for cell_x in range(first_cell[0], last_cell[0] + 1):
            for cell_y in range(first_cell[1], last_cell[1] + 1):
                spatial_segments.setdefault((cell_x, cell_y), []).append(
                    segment_index
                )

    neighbour_radius = int(math.ceil(allowed_distance / cell_size)) + 1
    first_issue = None
    affected_segments = set()
    for index, start in enumerate(source):
        end = source[(index + 1) % len(source)]
        if distance(start, end) <= 1.0e-9:
            continue
        for ratio in (0.25, 0.5, 0.75):
            sample = (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
            sample_cell = cell_index(sample)
            candidate_indices = set()
            for cell_x in range(
                sample_cell[0] - neighbour_radius,
                sample_cell[0] + neighbour_radius + 1,
            ):
                for cell_y in range(
                    sample_cell[1] - neighbour_radius,
                    sample_cell[1] + neighbour_radius + 1,
                ):
                    candidate_indices.update(
                        spatial_segments.get((cell_x, cell_y), ())
                    )
            if any(
                point_segment_distance(sample, *compensated_segments[segment_index])
                <= allowed_distance
                for segment_index in candidate_indices
            ):
                continue
            nearest = min(
                point_segment_distance(sample, segment_start, segment_end)
                for segment_start, segment_end in compensated_segments
            )
            affected_segments.add(index)
            if first_issue is None:
                first_issue = {
                    "point": sample,
                    "distance_to_compensated_path": nearest,
                    "tool_radius": radius,
                    "source_segment_index": index,
                }
            break
    if first_issue is None:
        return None

    segment_count = len(source)
    if len(affected_segments) == segment_count:
        runs = [tuple(range(segment_count))]
    else:
        starts = [
            index
            for index in sorted(affected_segments)
            if (index - 1) % segment_count not in affected_segments
        ]
        runs = []
        for start_index in starts:
            run = []
            index = start_index
            while index in affected_segments:
                run.append(index)
                index = (index + 1) % segment_count
            runs.append(tuple(run))

    cleanup_paths = []
    cleanup_modes = []
    for run in runs:
        local_points = [source[run[0]]]
        local_points.extend(source[(index + 1) % segment_count] for index in run)
        cleanup_path = tuple(local_points)
        cleanup_mode = "local_source_trace"

        # Following both design walls would make a 2 mm slot with a 4 mm
        # cutter approximately 6 mm wide.  Pairing the walls by depth and
        # following their local midpoint limits it to the physical cutter
        # width, even when the slot tapers or contains small orthogonal steps.
        medial_path = _collapsed_slot_medial_path(local_points, diameter)
        if medial_path is not None:
            cleanup_path = medial_path
            cleanup_mode = (
                "slot_centerline"
                if len(medial_path) == 2
                else "slot_medial_axis"
            )

        cleanup_paths.append(cleanup_path)
        cleanup_modes.append(cleanup_mode)

    first_issue["cleanup_paths"] = tuple(cleanup_paths)
    first_issue["cleanup_modes"] = tuple(cleanup_modes)
    first_issue["affected_segment_indices"] = tuple(sorted(affected_segments))
    return first_issue


def _clean_closed_points(path_points):
    points = list(path_points)
    if points and points[0] == points[-1]:
        points = points[:-1]

    if len(points) < 2:
        raise ValueError("O contorno precisa ter pelo menos 2 vertices distintos.")

    clean_points = [points[0]]
    for point in points[1:]:
        if point != clean_points[-1]:
            clean_points.append(point)

    if len(clean_points) < 3:
        raise ValueError("O contorno precisa ter pelo menos 3 pontos validos apos limpeza.")

    return clean_points


def _clean_open_points(path_points, allow_closed=False):
    """Normalize a network trail without adding an implicit closing edge."""
    points = []
    for point in path_points or []:
        try:
            candidate = (float(point[0]), float(point[1]))
        except (IndexError, TypeError, ValueError):
            raise ValueError("A linha comum possui um ponto XY inválido.")
        if not points or distance(points[-1], candidate) > 1e-9:
            points.append(candidate)

    if len(points) < 2:
        raise ValueError("A linha comum precisa ter pelo menos 2 pontos distintos.")
    if distance(points[0], points[-1]) <= 1e-9 and not allow_closed:
        raise ValueError(
            "A linha comum precisa permanecer aberta; o ponto final coincide com o inicial."
        )
    if _path_length(points) <= 1e-6:
        raise ValueError("A linha comum tem comprimento muito pequeno.")
    return points


def _closed_path(points):
    return list(points) + [points[0]]


def _path_length(path):
    return sum(distance(path[idx], path[idx + 1]) for idx in range(len(path) - 1))


def _point_at_distance(path, target_distance, total_length):
    if total_length <= 0.0:
        return path[0]

    target_distance = target_distance % total_length
    accumulated = 0.0

    for idx in range(len(path) - 1):
        start = path[idx]
        end = path[idx + 1]
        segment_length = distance(start, end)
        if segment_length < 1e-9:
            continue
        if accumulated + segment_length >= target_distance:
            ratio = (target_distance - accumulated) / segment_length
            ratio = max(0.0, min(1.0, ratio))
            return (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
        accumulated += segment_length

    return path[-1]


def _point_at_open_distance(path, target_distance, total_length=None):
    """Retorna um ponto numa polilinha sem o módulo circular de caminhos fechados."""
    if not path:
        raise ValueError("A linha comum não possui pontos.")
    if total_length is None:
        total_length = _path_length(path)
    if total_length <= 0.0:
        return path[0]

    target = max(0.0, min(float(target_distance), float(total_length)))
    accumulated = 0.0
    for start, end in zip(path, path[1:]):
        segment_length = distance(start, end)
        if segment_length <= 1e-9:
            continue
        if accumulated + segment_length >= target - 1e-9:
            ratio = (target - accumulated) / segment_length
            ratio = max(0.0, min(1.0, ratio))
            return (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
        accumulated += segment_length
    return path[-1]


def _vertex_distances(path):
    distances = [0.0]
    accumulated = 0.0
    for idx in range(len(path) - 1):
        accumulated += distance(path[idx], path[idx + 1])
        distances.append(accumulated)
    return distances


def _open_path_points_between(path, start_distance, end_distance, total_length=None):
    """Pontos/avanços de ``start`` até ``end`` sem fechar ou saltar vértices."""
    if total_length is None:
        total_length = _path_length(path)
    start = max(0.0, min(float(start_distance), float(total_length)))
    end = max(start, min(float(end_distance), float(total_length)))
    if end - start <= 1e-9:
        return []

    targets = [
        value
        for value in _vertex_distances(path)[1:]
        if start + 1e-7 < value < end - 1e-7
    ]
    targets.append(end)
    targets = sorted(set(round(value, 7) for value in targets))
    return [
        (
            _point_at_open_distance(path, value, total_length),
            value - start,
        )
        for value in targets
    ]


def _full_loop_points_from(path, start_distance, total_length):
    start_distance = start_distance % total_length
    distances = _vertex_distances(path)
    targets = []
    end_distance = start_distance + total_length

    for base_distance in distances[1:]:
        for lap_distance in (base_distance, base_distance + total_length):
            if start_distance < lap_distance < end_distance - 1e-7:
                targets.append(lap_distance)

    targets.append(end_distance)
    targets = sorted(set(round(target, 7) for target in targets))
    return [_point_at_distance(path, target, total_length) for target in targets]


def _direction_change_degrees(previous, current, following):
    incoming_x = current[0] - previous[0]
    incoming_y = current[1] - previous[1]
    outgoing_x = following[0] - current[0]
    outgoing_y = following[1] - current[1]
    incoming_length = math.hypot(incoming_x, incoming_y)
    outgoing_length = math.hypot(outgoing_x, outgoing_y)
    if incoming_length <= 1e-9 or outgoing_length <= 1e-9:
        return 0.0
    cosine = (
        incoming_x * outgoing_x + incoming_y * outgoing_y
    ) / (incoming_length * outgoing_length)
    cosine = max(-1.0, min(1.0, cosine))
    return math.degrees(math.acos(cosine))


def _append_corner_aware_loop(
    moves,
    start_xy,
    targets,
    contour_points,
    z_target,
    enabled=False,
    angle_threshold=45.0,
    feed_scale=0.40,
    slowdown_distance=8.0,
):
    """Divide segmentos ao redor de vértices sem sair da volta física.

    A entrada pode terminar no meio de uma aresta. Nesse caso, o próximo ponto
    físico depois de uma quina pode ser o próprio fechamento da volta, antes do
    próximo vértice original. A saída lenta precisa, portanto, usar a sequência
    real de ``targets``; avançar em direção ao vértice original ultrapassaria o
    fechamento e obrigaria a fresa a voltar pelo mesmo trecho.
    """

    del contour_points  # A sequência física já contém vértices e fechamento.
    start = (float(start_xy[0]), float(start_xy[1]))
    clean_targets = [
        (float(target[0]), float(target[1]))
        for target in targets
    ]
    if not clean_targets:
        return
    if not enabled:
        for target_x, target_y in clean_targets:
            moves.append(
                {
                    "type": "feed_cut",
                    "x": target_x,
                    "y": target_y,
                    "z": z_target,
                }
            )
        return

    closes_loop = distance(clean_targets[-1], start) <= 1.0e-6
    ring = [start] + (
        clean_targets[:-1] if closes_loop else clean_targets
    )
    compact_ring = []
    for point in ring:
        if not compact_ring or distance(compact_ring[-1], point) > 1.0e-9:
            compact_ring.append(point)
    ring = compact_ring
    if len(ring) < 2:
        return

    slow_corners = [False] * len(ring)
    if closes_loop and len(ring) >= 3:
        for index, point in enumerate(ring):
            direction_change = _direction_change_degrees(
                ring[index - 1],
                point,
                ring[(index + 1) % len(ring)],
            )
            slow_corners[index] = (
                direction_change + 1.0e-7 >= float(angle_threshold)
            )

    segment_count = len(ring) if closes_loop else len(ring) - 1
    maximum_slow_distance = max(0.0, float(slowdown_distance))
    for index in range(segment_count):
        segment_start = ring[index]
        next_index = (index + 1) % len(ring)
        segment_end = ring[next_index]
        segment_length = distance(segment_start, segment_end)
        if segment_length <= 1.0e-9:
            continue

        start_is_slow = slow_corners[index]
        end_is_slow = slow_corners[next_index]
        exit_distance = (
            min(maximum_slow_distance, segment_length * 0.40)
            if start_is_slow
            else 0.0
        )
        approach_distance = (
            min(maximum_slow_distance, segment_length * 0.40)
            if end_is_slow
            else 0.0
        )
        direction_x = (segment_end[0] - segment_start[0]) / segment_length
        direction_y = (segment_end[1] - segment_start[1]) / segment_length
        current = segment_start

        if exit_distance > 1.0e-9:
            exit_point = (
                segment_start[0] + direction_x * exit_distance,
                segment_start[1] + direction_y * exit_distance,
            )
            moves.append(
                {
                    "type": "feed_cut",
                    "x": exit_point[0],
                    "y": exit_point[1],
                    "z": z_target,
                    "feed_scale": float(feed_scale),
                    "corner_slowdown": True,
                }
            )
            current = exit_point

        if approach_distance > 1.0e-9:
            approach = (
                segment_end[0] - direction_x * approach_distance,
                segment_end[1] - direction_y * approach_distance,
            )
            if distance(current, approach) > 1.0e-7:
                moves.append(
                    {
                        "type": "feed_cut",
                        "x": approach[0],
                        "y": approach[1],
                        "z": z_target,
                    }
                )

        target_move = {
            "type": "feed_cut",
            "x": segment_end[0],
            "y": segment_end[1],
            "z": z_target,
        }
        if end_is_slow:
            target_move["feed_scale"] = float(feed_scale)
            target_move["corner_slowdown"] = True
        moves.append(target_move)


def _path_points_with_progress(path, start_distance, travel_distance, total_length):
    start_distance = start_distance % total_length
    end_distance = start_distance + travel_distance
    distances = _vertex_distances(path)
    targets = []

    for base_distance in distances:
        for lap_distance in (base_distance, base_distance + total_length):
            if start_distance < lap_distance < end_distance - 1e-7:
                targets.append(lap_distance)

    targets.append(end_distance)
    targets = sorted(set(round(target, 7) for target in targets))
    return [
        (_point_at_distance(path, target, total_length), target - start_distance)
        for target in targets
    ]


def _tab_ranges(total_length, tab_length, tab_count, tab_positions=None):
    try:
        count = int(round(float(tab_count)))
    except (TypeError, ValueError):
        count = 0
    try:
        length = float(tab_length)
    except (TypeError, ValueError):
        length = 0.0
    if total_length <= 1e-6 or length <= 1e-6:
        return []
    manual_centers = []
    for position in tab_positions or []:
        try:
            manual_centers.append(float(position) % 1.0 * total_length)
        except (TypeError, ValueError):
            continue
    centers = list(manual_centers)
    if count > 0:
        spacing = total_length / count
        centers.extend((index + 0.5) * spacing for index in range(count))
    centers = sorted(set(round(center, 7) for center in centers))
    if not centers:
        return []
    length = min(length, total_length / len(centers) * 0.60)
    ranges = []
    for center in centers:
        start = (center - length * 0.5) % total_length
        end = (center + length * 0.5) % total_length
        if start <= end:
            ranges.append((start, end))
        else:
            ranges.append((0.0, end))
            ranges.append((start, total_length))
    return ranges


def _best_fixation_tab_ranges(
    path,
    total_length,
    tab_length,
    requested_count,
    tool_diameter=0.0,
    material_thickness=None,
    tab_height=0.0,
):
    """Place a geometric support pattern instead of arclength-only tabs."""

    length = max(0.0, float(tab_length))
    if length <= 1.0e-7:
        return []
    points = list(path)
    if len(points) > 1 and distance(points[0], points[-1]) <= 1.0e-7:
        points.pop()
    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    width = max_x - min_x
    height = max_y - min_y
    aspect = max(width, height) / max(min(width, height), 1.0e-9)
    centre = ((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)
    from woodcam_editor.application.global_cut_plan import PieceMetrics, RetentionRules

    rules = RetentionRules().for_physical_tab_height(
        tab_height,
        material_thickness,
    )
    target_count = rules.required_tab_count(
        PieceMetrics(
            "profile",
            width,
            height,
            float(total_length),
            Vec2(centre[0], centre[1]),
        ),
        requested_count,
        tool_diameter,
        tab_length,
    )
    maximum_gap = float(rules.maximum_unsupported_perimeter_mm)
    if total_length > 1.0e-7 and maximum_gap > 1.0e-7:
        usable_gap = max(maximum_gap - length, maximum_gap * 0.75)
        target_count = max(
            target_count,
            int(math.ceil(float(total_length) / usable_gap)),
        )

    candidates = []
    progress = 0.0
    for segment_index, (start, end) in enumerate(zip(path, path[1:])):
        segment_length = distance(start, end)
        if segment_length < length + 2.0e-7:
            progress += segment_length
            continue
        half = length * 0.5
        extra = max(0.0, (segment_length - length) * 0.5)
        corner_margin = min(max(length * 0.5, 1.0), extra)
        low = half + corner_margin
        high = segment_length - half - corner_margin
        if high < low:
            low = high = segment_length * 0.5
        sample_count = max(
            1,
            min(12, int(math.ceil(segment_length / max(length * 1.5, 1.0)))),
        )
        for sample_index in range(sample_count):
            local = (
                (low + high) * 0.5
                if sample_count == 1
                else low + (high - low) * sample_index / (sample_count - 1)
            )
            ratio = local / segment_length
            point = (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
            candidates.append((progress + local, point, segment_index))
        progress += segment_length
    candidates.sort(key=lambda value: value[0])
    if len(candidates) > 72:
        candidates = [
            candidates[int(round(index * (len(candidates) - 1) / 71.0))]
            for index in range(72)
        ]
    if len(candidates) < target_count:
        raise ValueError(
            "Melhor fixação não encontrou %d regiões para tabs; reduza o "
            "comprimento ou posicione manualmente." % target_count
        )

    def compatible(first, second):
        delta = abs(first[0] - second[0])
        return min(delta, total_length - delta) >= length * 1.25 - 1.0e-9

    best_even = None
    best_even_score = None
    for start_candidate in candidates:
        selected_even = []
        for target_index in range(target_count):
            target = (
                start_candidate[0]
                + target_index * float(total_length) / target_count
            ) % float(total_length)
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
                            abs(candidate[0] - target),
                            float(total_length) - abs(candidate[0] - target),
                        ),
                        candidate[0],
                    ),
                )
            )
        if len(selected_even) != target_count:
            continue
        positions = sorted(candidate[0] for candidate in selected_even)
        gaps = [
            second - first
            for first, second in zip(positions, positions[1:])
        ]
        gaps.append(float(total_length) - positions[-1] + positions[0])
        twice_area = max(
            (
                abs(
                    (second[1][0] - first[1][0])
                    * (third[1][1] - first[1][1])
                    - (second[1][1] - first[1][1])
                    * (third[1][0] - first[1][0])
                )
                for first, second, third in itertools.combinations(
                    selected_even, 3
                )
            ),
            default=0.0,
        )
        score = (-max(gaps), twice_area)
        if best_even_score is None or score > best_even_score:
            best_even_score = score
            best_even = selected_even
    if (
        best_even is not None
        and -best_even_score[0] <= maximum_gap + 1.0e-6
        and best_even_score[1] > 1.0e-7
    ):
        return _tab_ranges(
            total_length,
            length,
            0,
            [candidate[0] / total_length for candidate in best_even],
        )

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
                    (second[1][0] - first[1][0]) * (third[1][1] - first[1][1])
                    - (second[1][1] - first[1][1]) * (third[1][0] - first[1][0])
                )
                if twice_area <= 1.0e-7:
                    continue
                triangle_area = (
                    abs(
                        (second[1][0] - centre[0]) * (third[1][1] - centre[1])
                        - (second[1][1] - centre[1]) * (third[1][0] - centre[0])
                    )
                    + abs(
                        (third[1][0] - centre[0]) * (first[1][1] - centre[1])
                        - (third[1][1] - centre[1]) * (first[1][0] - centre[0])
                    )
                    + abs(
                        (first[1][0] - centre[0]) * (second[1][1] - centre[1])
                        - (first[1][1] - centre[1]) * (second[1][0] - centre[0])
                    )
                )
                surrounds = triangle_area <= twice_area + 1.0e-7
                separation = min(
                    distance(first[1], second[1]),
                    distance(first[1], third[1]),
                    distance(second[1], third[1]),
                )
                score = (bool(surrounds), twice_area, separation)
                if best_score is None or score > best_score:
                    best_score = score
                    best_triple = [first, second, third]
    if best_triple is None:
        raise ValueError(
            "Melhor fixação não conseguiu três tabs não colineares; "
            "posicione as pontes manualmente."
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
                    min(distance(candidate[1], existing[1]) for existing in selected),
                    distance(candidate[1], centre),
                    -candidate[0],
                ),
            )
        )
    return _tab_ranges(
        total_length,
        length,
        0,
        [candidate[0] / total_length for candidate in selected],
    )


def _tab_position_ratios_for_path(path, tab_positions, total_length):
    ratios = []
    for position in tab_positions or []:
        if not isinstance(position, dict):
            ratios.append(position)
            continue
        try:
            target_x = float(position["x"])
            target_y = float(position["y"])
        except (KeyError, TypeError, ValueError):
            continue
        best_distance = None
        best_progress = 0.0
        progress = 0.0
        for start, end in zip(path, path[1:]):
            segment_x = end[0] - start[0]
            segment_y = end[1] - start[1]
            segment_length = math.hypot(segment_x, segment_y)
            if segment_length <= 1e-9:
                continue
            ratio = ((target_x - start[0]) * segment_x + (target_y - start[1]) * segment_y) / (segment_length ** 2)
            ratio = max(0.0, min(1.0, ratio))
            projected_x = start[0] + segment_x * ratio
            projected_y = start[1] + segment_y * ratio
            squared_distance = (target_x - projected_x) ** 2 + (target_y - projected_y) ** 2
            if best_distance is None or squared_distance < best_distance:
                best_distance = squared_distance
                best_progress = progress + segment_length * ratio
            progress += segment_length
        if best_distance is not None and total_length > 1e-9:
            ratios.append(best_progress / total_length)
    return ratios


def _tab_positions_by_path(paths, tab_positions):
    """Assign every manual XY marker to one nearest contour only.

    Numeric legacy ratios remain per-profile settings. Coordinate dictionaries
    come from clicks in the Editor and therefore represent one physical place
    on the sheet; projecting the same click independently onto every profile
    multiplied three markers into three tabs per piece.
    """
    paths = tuple(tuple(path or ()) for path in paths or ())
    buckets = [[] for _path in paths]
    for position in tuple(tab_positions or ()):
        if not isinstance(position, dict):
            for bucket in buckets:
                bucket.append(position)
            continue
        try:
            target_x = float(position["x"])
            target_y = float(position["y"])
        except (KeyError, TypeError, ValueError):
            continue
        candidates = []
        for path_index, points in enumerate(paths):
            best_distance = None
            if len(points) >= 2:
                closed = _closed_path(points)
                for start, end in zip(closed, closed[1:]):
                    segment_x = float(end[0]) - float(start[0])
                    segment_y = float(end[1]) - float(start[1])
                    length_squared = segment_x * segment_x + segment_y * segment_y
                    if length_squared <= 1.0e-18:
                        continue
                    parameter = (
                        (target_x - float(start[0])) * segment_x
                        + (target_y - float(start[1])) * segment_y
                    ) / length_squared
                    parameter = max(0.0, min(1.0, parameter))
                    projected_x = float(start[0]) + segment_x * parameter
                    projected_y = float(start[1]) + segment_y * parameter
                    squared_distance = (
                        (target_x - projected_x) ** 2
                        + (target_y - projected_y) ** 2
                    )
                    if best_distance is None or squared_distance < best_distance:
                        best_distance = squared_distance
            if best_distance is not None:
                candidates.append((best_distance, path_index))
        if candidates:
            _distance, path_index = min(candidates)
            buckets[path_index].append(position)
    return tuple(tuple(bucket) for bucket in buckets)


def _distance_in_ranges(distance_value, ranges):
    value = float(distance_value)
    return any(start - 1e-7 <= value <= end + 1e-7 for start, end in ranges)


def _full_loop_points_with_tab_state(path, start_distance, total_length, tab_ranges):
    start_distance = start_distance % total_length
    distances = _vertex_distances(path)
    targets = []
    end_distance = start_distance + total_length

    for base_distance in distances[1:]:
        for lap_distance in (base_distance, base_distance + total_length):
            if start_distance < lap_distance < end_distance - 1e-7:
                targets.append(lap_distance)
    for tab_start, tab_end in tab_ranges:
        for base_distance in (tab_start, tab_end):
            for lap_distance in (base_distance, base_distance + total_length):
                if start_distance < lap_distance < end_distance - 1e-7:
                    targets.append(lap_distance)

    targets.append(end_distance)
    targets = sorted(set(round(target, 7) for target in targets))
    result = []
    for target in targets:
        local_distance = target % total_length
        result.append(
            (
                _point_at_distance(path, target, total_length),
                _distance_in_ranges(local_distance, tab_ranges),
            )
        )
    return result


def _full_loop_segments_with_tab_state(path, start_distance, total_length, tab_ranges):
    start_distance = start_distance % total_length
    distances = _vertex_distances(path)
    targets = []
    end_distance = start_distance + total_length

    for base_distance in distances[1:]:
        for lap_distance in (base_distance, base_distance + total_length):
            if start_distance < lap_distance < end_distance - 1e-7:
                targets.append(lap_distance)
    for tab_start, tab_end in tab_ranges:
        for base_distance in (tab_start, (tab_start + tab_end) * 0.5, tab_end):
            for lap_distance in (base_distance, base_distance + total_length):
                if start_distance < lap_distance < end_distance - 1e-7:
                    targets.append(lap_distance)

    targets.append(end_distance)
    targets = sorted(set(round(target, 7) for target in targets))
    result = []
    previous_distance = start_distance
    for target in targets:
        midpoint = (previous_distance + target) * 0.5
        local_midpoint = midpoint % total_length
        result.append(
            (
                _point_at_distance(path, target, total_length),
                _distance_in_ranges(local_midpoint, tab_ranges),
                target % total_length,
            )
        )
        previous_distance = target
    return result


def _contour_from_distance(points, start_distance):
    full_path = _closed_path(points)
    total_length = _path_length(full_path)
    if total_length <= 0.0:
        return list(points)

    start_distance = start_distance % total_length
    distances = _vertex_distances(full_path)
    end_distance = start_distance + total_length
    targets = [start_distance]

    for base_distance in distances[:-1]:
        for lap_distance in (base_distance, base_distance + total_length):
            if start_distance < lap_distance < end_distance - 1e-7:
                targets.append(lap_distance)

    targets = sorted(set(round(target, 7) for target in targets))
    rotated = [_point_at_distance(full_path, target, total_length) for target in targets]
    clean = []
    for point in rotated:
        rounded = (round(point[0], 6), round(point[1], 6))
        if not clean or rounded != clean[-1]:
            clean.append(rounded)

    return clean if len(clean) >= 3 else list(points)


def _entry_candidate_distances(points, current_xy, ramp_length, tool_diameter):
    full_path = _closed_path(points)
    total_length = _path_length(full_path)
    distances = _vertex_distances(full_path)
    candidates = []

    min_corner_clearance = max(abs(tool_diameter) * 2.0, min(max(ramp_length * 0.25, 12.0), 40.0))
    min_segment_length = max(abs(tool_diameter) * 3.0, 12.0)

    for idx in range(len(full_path) - 1):
        start = full_path[idx]
        end = full_path[idx + 1]
        segment_length = distance(start, end)
        if segment_length < 1e-6:
            continue

        offsets = {segment_length * 0.5}
        if segment_length >= min_corner_clearance * 2.0:
            offsets.add(min_corner_clearance)
            offsets.add(segment_length - min_corner_clearance)
        if segment_length >= min_segment_length * 2.0:
            offsets.add(segment_length * 0.25)
            offsets.add(segment_length * 0.75)

        for offset in sorted(offsets):
            offset = max(0.0, min(segment_length, offset))
            candidate_distance = distances[idx] + offset
            point = _point_at_distance(full_path, candidate_distance, total_length)
            clearance = min(offset, segment_length - offset)
            travel = distance(current_xy, point)
            corner_penalty = max(0.0, min_corner_clearance - clearance) * 10.0
            short_segment_penalty = max(0.0, min_segment_length - segment_length) * 4.0
            straight_bonus = min(segment_length, 180.0) * 0.04

            candidates.append(
                {
                    "distance": candidate_distance,
                    "point": point,
                    "score": travel + corner_penalty + short_segment_penalty - straight_bonus,
                }
            )

    return candidates


def select_entry_distance(points, current_xy=(0.0, 0.0), ramp_length=0.0, tool_diameter=0.0):
    candidates = _entry_candidate_distances(points, current_xy, ramp_length, tool_diameter)
    if not candidates:
        full_path = _closed_path(points)
        total_length = _path_length(full_path)
        distances = _vertex_distances(full_path)
        nearest_vertex_index = min(range(len(points)), key=lambda idx: distance(points[idx], current_xy))
        return distances[nearest_vertex_index] % total_length

    return min(candidates, key=lambda candidate: candidate["score"])["distance"]


def generate_depth_steps(final_depth, stepdown, material_thickness=None, start_depth=0.0, pass_depths=None):
    total_depth = abs(final_depth)
    if total_depth <= 0.0:
        return []
    initial_depth = min(abs(float(start_depth)), total_depth)

    if pass_depths:
        steps = []
        current = initial_depth
        for value in pass_depths:
            try:
                increment = float(value)
            except (TypeError, ValueError):
                continue
            if increment <= 0.0:
                continue
            current = min(total_depth, current + increment)
            steps.append(current)
            if current >= total_depth - 1e-9:
                break
        if steps and abs(steps[-1] - total_depth) <= 1e-9:
            return steps

    material_depth = abs(material_thickness) if material_thickness is not None else total_depth
    material_depth = min(material_depth, total_depth)

    steps = []
    current = initial_depth + stepdown
    while current < material_depth - 1e-9:
        steps.append(current)
        current += stepdown

    if not steps or abs(steps[-1] - total_depth) > 1e-9:
        steps.append(total_depth)

    return steps


def build_drill_moves(
    holes,
    final_depth,
    stepdown,
    safe_height,
    tool_diameter=0.0,
    material_thickness=None,
    start_xy=(0.0, 0.0),
    use_helical=True,
    helix_pitch=1.0,
    helix_stepover_ratio=0.40,
    start_depth=0.0,
    use_model_depths=True,
    hole_depth_override=None,
    peck_enabled=False,
    peck_step=None,
    retract_mode="surface",
    retract_clearance=0.0,
    dwell_seconds=0.0,
    preserve_order=False,
    counterbore_enabled=False,
    counterbore_diameter=0.0,
    counterbore_depth=0.0,
    tool_type="end_mill",
):
    """Gera furos; interpola em hélice quando a ferramenta cabe dentro do diâmetro."""
    remaining = [dict(hole) for hole in holes or []]
    moves = []
    current_xy = start_xy

    while remaining:
        if preserve_order:
            hole = remaining[0]
        else:
            hole = min(
                remaining,
                key=lambda item: distance(
                    current_xy,
                    (float(item["x"]), float(item["y"])),
                ),
            )
        x = float(hole["x"])
        y = float(hole["y"])
        requested_depth = float(hole.get("depth_mm", 0.0) or 0.0)
        initial_depth = abs(float(start_depth))
        if use_model_depths:
            target_depth = initial_depth + (
                requested_depth
                if requested_depth > 0.0
                else max(0.0, abs(float(final_depth)) - initial_depth)
            )
        else:
            override = float(hole_depth_override or 0.0)
            target_depth = initial_depth + override

        if use_model_depths and material_thickness is not None:
            material_depth = abs(float(material_thickness))
            remaining_material = max(0.0, material_depth - initial_depth)
            if requested_depth >= remaining_material - 1e-6:
                target_depth = abs(float(final_depth))
        else:
            material_depth = (
                abs(float(material_thickness))
                if material_thickness is not None
                else target_depth
            )

        if target_depth <= initial_depth + 1e-9:
            raise ValueError("A profundidade do furo deve ser maior que a cota inicial.")

        hole_diameter = abs(float(hole.get("diameter_mm", 0.0) or 0.0))
        cutter_diameter = abs(float(tool_diameter))
        if counterbore_enabled:
            seat_diameter = abs(float(counterbore_diameter))
            seat_depth = abs(float(counterbore_depth))
            if str(tool_type or "end_mill") not in {"end_mill", "compression"}:
                raise ValueError(
                    "O rebaixo da cabeça exige fresa de topo ou de compressão "
                    "com corte lateral e fundo plano."
                )
            if cutter_diameter <= 1.0e-9:
                raise ValueError(
                    "Informe o diâmetro da fresa para criar o rebaixo da cabeça."
                )
            if seat_diameter + 1.0e-9 < cutter_diameter:
                raise ValueError(
                    "O diâmetro do rebaixo não pode ser menor que a fresa."
                )
            if hole_diameter > 1.0e-9 and seat_diameter <= hole_diameter + 1.0e-9:
                raise ValueError(
                    "O diâmetro do rebaixo deve ser maior que o diâmetro do furo."
                )
            if seat_depth <= 1.0e-9:
                raise ValueError(
                    "A profundidade do rebaixo da cabeça deve ser maior que zero."
                )
            counterbore_target = initial_depth + seat_depth
            if counterbore_target > target_depth + 1.0e-9:
                raise ValueError(
                    "A profundidade do rebaixo da cabeça não pode ultrapassar "
                    "a profundidade do furo."
                )
            counterbore_steps = generate_depth_steps(
                counterbore_target,
                stepdown,
                material_thickness=counterbore_target,
                start_depth=initial_depth,
            )
            counterbore_moves = _build_counterbore_moves(
                x,
                y,
                seat_diameter,
                cutter_diameter,
                counterbore_steps,
                safe_height,
                helix_pitch,
                helix_stepover_ratio,
                initial_depth,
            )
            for move in counterbore_moves:
                move["counterbore"] = True
                move["cut_phase"] = "hole_counterbore"
                move["counterbore_diameter"] = seat_diameter
                move["counterbore_depth"] = seat_depth
            moves.extend(counterbore_moves)

        step_value = float(peck_step or stepdown) if peck_enabled else float(stepdown)
        depth_steps = generate_depth_steps(
            target_depth,
            step_value,
            material_thickness=min(material_depth, target_depth),
            start_depth=initial_depth,
        )
        if not depth_steps:
            raise ValueError("A profundidade do furo deve ser maior que zero.")

        can_helix = (
            use_helical
            and cutter_diameter > 0.0
            and hole_diameter > cutter_diameter + 1e-6
        )

        if can_helix:
            moves.extend(
                _build_helical_hole_moves(
                    x,
                    y,
                    hole_diameter,
                    cutter_diameter,
                    depth_steps,
                    safe_height,
                    helix_pitch,
                    helix_stepover_ratio,
                    initial_depth,
                    dwell_seconds,
                )
            )
        else:
            if not peck_enabled:
                depth_steps = [target_depth]
            moves.append({"type": "rapid", "x": x, "y": y, "z": safe_height})
            for depth_index, depth in enumerate(depth_steps):
                moves.append({"type": "feed_drill", "x": x, "y": y, "z": -abs(depth)})
                if dwell_seconds > 0.0:
                    moves.append({"type": "dwell", "seconds": float(dwell_seconds)})
                is_last_step = depth_index == len(depth_steps) - 1
                if is_last_step:
                    retract_z = safe_height
                elif retract_mode == "previous_step":
                    previous_depth = (
                        initial_depth
                        if depth_index == 0
                        else depth_steps[depth_index - 1]
                    )
                    retract_z = -abs(previous_depth) + float(retract_clearance)
                else:
                    retract_z = -initial_depth + float(retract_clearance)
                retract_z = min(float(safe_height), retract_z)
                moves.append({"type": "rapid", "x": None, "y": None, "z": retract_z})

        current_xy = (x, y)
        remaining.remove(hole)

    return moves


def _build_counterbore_moves(
    center_x,
    center_y,
    counterbore_diameter,
    tool_diameter,
    depth_steps,
    safe_height,
    helix_pitch,
    helix_stepover_ratio,
    start_depth,
):
    """Open one flat-bottom screw-head seat before the main hole."""

    if counterbore_diameter > tool_diameter + 1.0e-6:
        return _build_helical_hole_moves(
            center_x,
            center_y,
            counterbore_diameter,
            tool_diameter,
            depth_steps,
            safe_height,
            helix_pitch,
            helix_stepover_ratio,
            start_depth,
            0.0,
        )
    target_depth = abs(float(depth_steps[-1]))
    return [
        {
            "type": "rapid",
            "x": float(center_x),
            "y": float(center_y),
            "z": float(safe_height),
        },
        {
            "type": "feed_plunge",
            "x": float(center_x),
            "y": float(center_y),
            "z": -target_depth,
        },
        {"type": "rapid", "x": None, "y": None, "z": float(safe_height)},
    ]


def _circle_segment_count(radius):
    circumference = 2.0 * math.pi * max(float(radius), 0.0)
    return max(24, min(180, int(math.ceil(circumference / 1.5))))


def _append_circle_moves(moves, center_x, center_y, radius, z_value, move_type):
    segment_count = _circle_segment_count(radius)
    for segment in range(1, segment_count + 1):
        angle = 2.0 * math.pi * segment / segment_count
        moves.append(
            {
                "type": move_type,
                "x": center_x + radius * math.cos(angle),
                "y": center_y + radius * math.sin(angle),
                "z": z_value,
            }
        )


def _build_helical_hole_moves(
    center_x,
    center_y,
    hole_diameter,
    tool_diameter,
    depth_steps,
    safe_height,
    helix_pitch,
    helix_stepover_ratio,
    start_depth,
    dwell_seconds,
):
    tool_radius = tool_diameter * 0.5
    final_path_radius = (hole_diameter - tool_diameter) * 0.5
    entry_radius = min(final_path_radius, max(0.25, min(tool_radius * 0.5, 1.5)))
    stepover = max(tool_diameter * float(helix_stepover_ratio), 0.25)
    entry_x = center_x + entry_radius
    entry_y = center_y

    moves = [
        {"type": "rapid", "x": entry_x, "y": entry_y, "z": safe_height},
        {
            "type": "feed_plunge",
            "x": entry_x,
            "y": entry_y,
            "z": -abs(float(start_depth)),
        },
    ]
    previous_depth = abs(float(start_depth))
    pitch = max(float(helix_pitch), 0.05)

    for depth in depth_steps:
        target_z = -abs(depth)
        previous_z = -abs(previous_depth)
        depth_delta = abs(target_z - previous_z)
        revolution_count = max(1, int(math.ceil(depth_delta / pitch)))
        segments_per_revolution = _circle_segment_count(entry_radius)
        total_segments = revolution_count * segments_per_revolution
        for segment in range(1, total_segments + 1):
            ratio = segment / total_segments
            angle = 2.0 * math.pi * revolution_count * ratio
            moves.append(
                {
                    "type": "feed_helix",
                    "x": center_x + entry_radius * math.cos(angle),
                    "y": center_y + entry_radius * math.sin(angle),
                    "z": previous_z + (target_z - previous_z) * ratio,
                }
            )

        current_radius = entry_radius
        while current_radius < final_path_radius - 1e-7:
            current_radius = min(final_path_radius, current_radius + stepover)
            moves.append(
                {
                    "type": "feed_cut",
                    "x": center_x + current_radius,
                    "y": center_y,
                    "z": target_z,
                }
            )
            _append_circle_moves(
                moves,
                center_x,
                center_y,
                current_radius,
                target_z,
                "feed_cut",
            )

        if depth != depth_steps[-1]:
            moves.append(
                {
                    "type": "feed_cut",
                    "x": entry_x,
                    "y": entry_y,
                    "z": target_z,
                }
            )
        previous_depth = depth

    if dwell_seconds > 0.0:
        moves.append({"type": "dwell", "seconds": float(dwell_seconds)})
    moves.append({"type": "rapid", "x": None, "y": None, "z": safe_height})
    return moves


def _rotate_contour_to_nearest_start(points, current_xy):
    if not points:
        return points

    nearest_index = min(range(len(points)), key=lambda idx: distance(points[idx], current_xy))
    return list(points[nearest_index:]) + list(points[:nearest_index])


def _prepare_cut_points(
    path_points,
    tool_diameter=0.0,
    compensate_external=True,
    cut_side=None,
    current_xy=(0.0, 0.0),
    ramp_length=0.0,
    smart_entry=True,
    climb=None,
):
    points = _clean_closed_points(path_points)
    normalized_side = normalize_cut_side(cut_side, compensate_external)
    points = compensated_polygon(points, tool_diameter, normalized_side)
    points = _orient_contour_for_cut(points, normalized_side, climb)

    if smart_entry and points:
        entry_distance = select_entry_distance(
            points,
            current_xy=current_xy,
            ramp_length=ramp_length,
            tool_diameter=tool_diameter,
        )
        points = _contour_from_distance(points, entry_distance)

    return points


def order_contours_by_nearest(contours, start_xy=(0.0, 0.0)):
    remaining = [list(contour) for contour in contours if contour]
    ordered = []
    current_xy = start_xy

    while remaining:
        candidates = [
            (_rotate_contour_to_nearest_start(contour, current_xy), contour)
            for contour in remaining
        ]
        selected_points, original_contour = min(
            candidates,
            key=lambda item: distance(item[0][0], current_xy),
        )
        ordered.append(selected_points)
        current_xy = selected_points[0]
        remaining.remove(original_contour)

    return ordered


def _build_common_line_polyline_moves(
    points,
    depth_steps,
    ramp_length,
    safe_height,
    start_depth=0.0,
    retain_tab=False,
    tab_thickness=0.0,
    tabs_3d=False,
    material_thickness=None,
    tab_surface_clearance=0.2,
):
    """Usina uma linha-centro aberta; o retorno da rampa refaz o mesmo traçado."""
    use_3d_tabs = bool(
        tabs_3d
        and not _tab_uses_full_material_height(
            material_thickness, tab_thickness
        )
    )
    total_length = _path_length(points)
    effective_ramp = min(
        max(float(ramp_length), 0.0),
        total_length * 0.5,
    )
    # Uma faixa de tab já é um intervalo exclusivo e curto. Fazer a entrada em
    # rampa sobre ela exigiria voltar pelo mesmo trecho depois e poderia
    # remover a ponte. Use entrada vertical nesse intervalo; o chamador pode
    # continuar usando rampa nas linhas comuns não marcadas.
    if retain_tab:
        effective_ramp = 0.0
    initial_depth = min(abs(float(start_depth)), abs(float(depth_steps[-1])))
    start_x, start_y = points[0]
    moves = [
        {
            "type": "rapid",
            "x": start_x,
            "y": start_y,
            "z": float(safe_height),
        },
        {
            "type": "feed_plunge",
            "x": start_x,
            "y": start_y,
            "z": -initial_depth,
            "common_line": True,
        },
    ]

    if effective_ramp > 1e-7:
        # Uma linha aberta não oferece uma volta fechada para limpar a porção
        # inclinada. Depois de alcançar a profundidade, voltar sobre a mesma
        # polilinha é seguro e garante que todo o trecho chegue ao Z alvo sem
        # inventar uma diagonal entre os dois extremos.
        for pass_index, depth in enumerate(depth_steps):
            target_z = -abs(float(depth))
            previous_z = (
                -initial_depth
                if pass_index == 0
                else -abs(float(depth_steps[pass_index - 1]))
            )
            for (x_value, y_value), progress in _open_path_points_between(
                points,
                0.0,
                effective_ramp,
                total_length,
            ):
                ratio = progress / effective_ramp
                moves.append(
                    {
                        "type": "feed_ramp",
                        "x": x_value,
                        "y": y_value,
                        "z": previous_z + (target_z - previous_z) * ratio,
                        "common_line": True,
                    }
                )
            for (x_value, y_value), _progress in _open_path_points_between(
                points,
                effective_ramp,
                total_length,
                total_length,
            ):
                moves.append(
                    {
                        "type": "feed_cut",
                        "x": x_value,
                        "y": y_value,
                        "z": target_z,
                        "common_line": True,
                    }
                )
            for x_value, y_value in reversed(points[:-1]):
                moves.append(
                    {
                        "type": "feed_cut",
                        "x": x_value,
                        "y": y_value,
                        "z": target_z,
                        "common_line": True,
                    }
                )
    else:
        # Alternar o sentido entre profundidades evita um rápido desnecessário
        # e mantém cada passe exatamente sobre a linha-centro planejada.
        active_points = list(points)
        final_depth = abs(float(depth_steps[-1]))
        for pass_index, depth in enumerate(depth_steps):
            start_x, start_y = active_points[0]
            raw_target_z = -abs(float(depth))
            previous_depth = (
                initial_depth
                if pass_index == 0
                else abs(float(depth_steps[pass_index - 1]))
            )
            if retain_tab:
                tab_z = _tab_target_z(
                    material_thickness,
                    tab_thickness,
                    final_depth=final_depth,
                    start_depth=start_depth,
                    previous_pass_depth=previous_depth,
                    final_pass=pass_index == len(depth_steps) - 1,
                    surface_clearance=tab_surface_clearance,
                )
                tab_active = tab_z > raw_target_z + 1.0e-7
            else:
                tab_z = raw_target_z
                tab_active = False
            target_z = tab_z if tab_active and not use_3d_tabs else raw_target_z
            moves.append(
                {
                    "type": "feed_plunge",
                    "x": start_x,
                    "y": start_y,
                    "z": target_z,
                    "common_line": True,
                    "tab": bool(tab_active and not use_3d_tabs),
                }
            )
            if tab_active and use_3d_tabs:
                total_length = _path_length(active_points)
                middle = _point_at_open_distance(
                    active_points,
                    total_length * 0.5,
                    total_length,
                )
                moves.append(
                    {
                        "type": "feed_ramp",
                        "x": middle[0],
                        "y": middle[1],
                        "z": tab_z,
                        "common_line": True,
                        "tab": True,
                    }
                )
                for x_value, y_value in active_points[1:]:
                    moves.append(
                        {
                            "type": "feed_ramp",
                            "x": x_value,
                            "y": y_value,
                            "z": raw_target_z,
                            "common_line": True,
                            "tab": True,
                        }
                    )
            else:
                for x_value, y_value in active_points[1:]:
                    moves.append(
                        {
                            "type": "feed_cut",
                            "x": x_value,
                            "y": y_value,
                            "z": target_z,
                            "common_line": True,
                            "tab": bool(tab_active),
                        }
                    )
            active_points.reverse()

    moves.append(
        {
            "type": "rapid",
            "x": None,
            "y": None,
            "z": float(safe_height),
        }
    )
    return moves


def _build_common_line_trail_moves(
    points,
    edge_tabs,
    depth_steps,
    safe_height,
    start_depth=0.0,
    tab_thickness=0.0,
    tabs_3d=False,
    final_job_depth=None,
    material_thickness=None,
    previous_pass_depth=None,
    final_pass=None,
    tab_surface_clearance=0.2,
    edge_tab_thicknesses=None,
):
    """Cut one connected network trail, changing only Z at tab spans."""

    if len(edge_tabs) != len(points) - 1:
        raise ValueError("Cada aresta da trilha precisa informar se contém tab.")
    if edge_tab_thicknesses is None:
        edge_heights = [float(tab_thickness)] * len(edge_tabs)
    else:
        edge_heights = [float(value or 0.0) for value in edge_tab_thicknesses]
        if len(edge_heights) != len(edge_tabs):
            raise ValueError(
                "Cada aresta da trilha precisa informar a altura de sua tab."
            )
    final_depth = abs(
        float(depth_steps[-1])
        if final_job_depth is None
        else float(final_job_depth)
    )
    active_points = list(points)
    active_tabs = [bool(value) for value in edge_tabs]
    start_x, start_y = active_points[0]
    first_depth = abs(float(depth_steps[0]))
    first_previous_depth = (
        abs(float(start_depth))
        if previous_pass_depth is None
        else abs(float(previous_pass_depth))
    )
    first_is_final = (
        len(depth_steps) == 1
        if final_pass is None
        else bool(final_pass)
    )
    if active_tabs[0]:
        first_tab_z = _tab_target_z(
            material_thickness,
            edge_heights[0],
            final_depth=final_depth,
            start_depth=start_depth,
            previous_pass_depth=first_previous_depth,
            final_pass=first_is_final,
            surface_clearance=tab_surface_clearance,
        )
        first_tab_active = first_tab_z > -first_depth + 1.0e-7
        first_uses_3d_tab = bool(
            tabs_3d
            and not _tab_uses_full_material_height(
                material_thickness, edge_heights[0]
            )
        )
    else:
        first_tab_z = -first_depth
        first_tab_active = False
        first_uses_3d_tab = False
    first_target_z = -first_depth
    if active_tabs[0] and first_tab_active and not first_uses_3d_tab:
        first_target_z = first_tab_z
    moves = [
        {"type": "rapid", "x": start_x, "y": start_y, "z": float(safe_height)},
        {
            "type": "feed_plunge",
            "x": start_x,
            "y": start_y,
            "z": first_target_z,
            "common_line": True,
        },
    ]
    current_z = first_target_z

    def move_z(point, target_z, tab=False):
        nonlocal current_z
        if abs(current_z - target_z) <= 1.0e-9:
            return
        moves.append(
            {
                "type": "feed_plunge",
                "x": point[0],
                "y": point[1],
                "z": target_z,
                "common_line": True,
                "tab": bool(tab),
            }
        )
        current_z = target_z

    for pass_index, depth in enumerate(depth_steps):
        raw_target_z = -abs(float(depth))
        prior_depth = (
            first_previous_depth
            if pass_index == 0
            else abs(float(depth_steps[pass_index - 1]))
        )
        is_final = (
            pass_index == len(depth_steps) - 1
            if final_pass is None
            else bool(final_pass) and pass_index == len(depth_steps) - 1
        )
        for edge_index, (start, end) in enumerate(
            zip(active_points, active_points[1:])
        ):
            edge_height = edge_heights[edge_index]
            if active_tabs[edge_index]:
                tab_z = _tab_target_z(
                    material_thickness,
                    edge_height,
                    final_depth=final_depth,
                    start_depth=start_depth,
                    previous_pass_depth=prior_depth,
                    final_pass=is_final,
                    surface_clearance=tab_surface_clearance,
                )
                tab_active = tab_z > raw_target_z + 1.0e-7
                use_3d_tab = bool(
                    tabs_3d
                    and not _tab_uses_full_material_height(
                        material_thickness, edge_height
                    )
                )
            else:
                tab_z = raw_target_z
                tab_active = False
                use_3d_tab = False
            is_tab = bool(tab_active)
            if is_tab and use_3d_tab:
                move_z(start, raw_target_z)
                middle = (
                    (float(start[0]) + float(end[0])) * 0.5,
                    (float(start[1]) + float(end[1])) * 0.5,
                )
                moves.append(
                    {
                        "type": "feed_ramp",
                        "x": middle[0],
                        "y": middle[1],
                        "z": tab_z,
                        "common_line": True,
                        "tab": True,
                    }
                )
                moves.append(
                    {
                        "type": "feed_ramp",
                        "x": end[0],
                        "y": end[1],
                        "z": raw_target_z,
                        "common_line": True,
                        "tab": True,
                    }
                )
                current_z = raw_target_z
            else:
                target_z = tab_z if is_tab else raw_target_z
                move_z(start, target_z, tab=is_tab)
                moves.append(
                    {
                        "type": "feed_cut",
                        "x": end[0],
                        "y": end[1],
                        "z": target_z,
                        "common_line": True,
                        "tab": is_tab,
                    }
                )
                current_z = target_z
        active_points.reverse()
        active_tabs.reverse()
        edge_heights.reverse()

    moves.append(
        {"type": "rapid", "x": None, "y": None, "z": float(safe_height)}
    )
    return moves


def _mark_profile_loop(
    moves,
    start_index,
    *,
    end_index=None,
    profile_id,
    pass_index,
    depth,
    expected_length,
    start_position,
):
    """Attach diagnostics to one physical traversal of a closed profile.

    The metadata is deliberately carried by the source movements instead of
    being inferred later from the preview.  The G-code writer ignores these
    keys, while tests/debug tools can distinguish a depth pass, a ramp and the
    local Z modulation used to preserve a tab.
    """

    loop_moves = moves[start_index:end_index]
    if not loop_moves:
        return
    loop_id = "%s:pass-%04d" % (profile_id, int(pass_index) + 1)
    previous_x = float(start_position[0])
    previous_y = float(start_position[1])
    previous_z = float(start_position[2])
    previous_tab = False
    for move in loop_moves:
        move["profile_id"] = str(profile_id)
        move["profile_loop_id"] = loop_id
        move["profile_loop"] = True
        move["profile_pass_index"] = int(pass_index)
        move["depth_pass"] = abs(float(depth))
        move["profile_loop_expected_length"] = float(expected_length)
        current_x = previous_x if move.get("x") is None else float(move["x"])
        current_y = previous_y if move.get("y") is None else float(move["y"])
        current_z = previous_z if move.get("z") is None else float(move["z"])
        current_tab = bool(move.get("tab"))
        if current_tab and not previous_tab:
            move["tab_entry"] = True
        elif previous_tab and not current_tab:
            move["tab_exit"] = True
        if (
            math.hypot(current_x - previous_x, current_y - previous_y) <= 1.0e-9
            and abs(current_z - previous_z) > 1.0e-9
            and (current_tab or previous_tab)
        ):
            move["tab_transition"] = True
        previous_x, previous_y, previous_z = current_x, current_y, current_z
        previous_tab = current_tab
    loop_moves[0]["profile_loop_start"] = True
    loop_moves[-1]["profile_loop_end"] = True


def audit_profile_cut_moves(moves, tolerance=1.0e-6):
    """Summarize closed-profile invariants from generated source movements.

    Results are grouped by ``profile_id + depth_pass``.  This makes repeated
    geometry at different depths explicit and reports only repeats occurring
    within the same physical pass as duplication.
    """

    tolerance = max(1.0e-9, abs(float(tolerance)))
    current = (None, None, None)
    groups = {}
    active_starts = {}
    completed = set()
    edge_counts = {}

    for movement_index, move in enumerate(moves or ()):
        target = (
            current[0] if move.get("x") is None else float(move["x"]),
            current[1] if move.get("y") is None else float(move["y"]),
            current[2] if move.get("z") is None else float(move["z"]),
        )
        if not move.get("profile_loop"):
            current = target
            continue

        profile_id = str(move.get("profile_id", "profile"))
        depth = abs(float(move.get("depth_pass", 0.0)))
        key = (profile_id, depth)
        report = groups.setdefault(
            key,
            {
                "profile_id": profile_id,
                "depth": depth,
                "loop_count": 0,
                "closed_loop_count": 0,
                "cut_movement_count": 0,
                "xy_length": 0.0,
                "expected_length": float(
                    move.get("profile_loop_expected_length", 0.0) or 0.0
                ),
                "tab_crossing_count": 0,
                "tab_transition_count": 0,
                "tab_retract_count": 0,
                "restart_count": 0,
                "duplicate_edge_count": 0,
                "movement_indexes": [],
            },
        )
        report["movement_indexes"].append(movement_index)
        if move.get("profile_loop_start"):
            if key in completed or key in active_starts:
                report["restart_count"] += 1
            report["loop_count"] += 1
            active_starts[key] = (current[0], current[1])

        if move.get("tab_entry"):
            report["tab_crossing_count"] += 1
        if move.get("tab_transition") or move.get("tab_exit"):
            report["tab_transition_count"] += 1
        if move["type"] == "rapid":
            report["tab_retract_count"] += 1

        if None not in current[:2] and None not in target[:2]:
            xy_length = math.hypot(
                target[0] - current[0], target[1] - current[1]
            )
            if xy_length > tolerance:
                report["cut_movement_count"] += 1
                report["xy_length"] += xy_length
                quantized = lambda value: int(round(float(value) / tolerance))
                start_xy = (quantized(current[0]), quantized(current[1]))
                end_xy = (quantized(target[0]), quantized(target[1]))
                edge = tuple(sorted((start_xy, end_xy)))
                counts = edge_counts.setdefault(key, {})
                counts[edge] = counts.get(edge, 0) + 1

        if move.get("profile_loop_end"):
            start_xy = active_starts.pop(key, None)
            if (
                start_xy is not None
                and None not in target[:2]
                and math.hypot(target[0] - start_xy[0], target[1] - start_xy[1])
                <= tolerance
            ):
                report["closed_loop_count"] += 1
            completed.add(key)
        current = target

    for key, report in groups.items():
        report["duplicate_edge_count"] = sum(
            count - 1
            for count in edge_counts.get(key, {}).values()
            if count > 1
        )
        expected = report["expected_length"]
        report["coverage_ratio"] = (
            report["xy_length"] / expected if expected > tolerance else 0.0
        )
    return tuple(groups[key] for key in sorted(groups))


def validate_profile_cut_moves(moves, tolerance=1.0e-6):
    """Reject a duplicated, reopened or incompletely closed profile pass."""

    reports = audit_profile_cut_moves(moves, tolerance=tolerance)
    failures = []
    for report in reports:
        label = "%s em Z -%g" % (report["profile_id"], report["depth"])
        if report["loop_count"] != 1:
            failures.append(
                "%s possui %d voltas" % (label, report["loop_count"])
            )
        if report["closed_loop_count"] != 1:
            failures.append(
                "%s possui %d fechamentos" % (
                    label, report["closed_loop_count"]
                )
            )
        if report["restart_count"]:
            failures.append("%s foi reiniciado após o fechamento" % label)
        if report["tab_retract_count"]:
            failures.append("%s contém retract dentro do loop" % label)
        if abs(report["coverage_ratio"] - 1.0) > tolerance:
            failures.append(
                "%s percorre %.6f voltas equivalentes" % (
                    label, report["coverage_ratio"]
                )
            )
        if report["duplicate_edge_count"]:
            failures.append(
                "%s repete %d trechos na mesma profundidade" % (
                    label, report["duplicate_edge_count"]
                )
            )
    if failures:
        raise ValueError("Percurso de perfil inválido: " + "; ".join(failures))
    return reports


def _global_point_key(point):
    return (round(float(point[0]), 8), round(float(point[1]), 8))


def _shortest_cleared_connector(plan, cleared_segment_ids, start, end):
    """Shortest walk over kerf already opened at the current depth.

    A connector is motion inside an existing groove, not a second cutting
    operation.  Keeping that distinction explicit preserves the structural
    ``segment_id + depth`` invariant while allowing N.CAD-style backtracking.
    """

    start_key = _global_point_key(start)
    end_key = _global_point_key(end)
    if start_key == end_key:
        return (), (), ()
    cleared = set(cleared_segment_ids)
    adjacency = {}
    for segment in plan.segments:
        if segment.segment_id not in cleared:
            continue
        first = _global_point_key(segment.start.to_tuple())
        second = _global_point_key(segment.end.to_tuple())
        adjacency.setdefault(first, []).append((second, segment))
        adjacency.setdefault(second, []).append((first, segment))
    if start_key not in adjacency or end_key not in adjacency:
        return None

    queue = [(0.0, start_key)]
    best = {start_key: 0.0}
    previous = {}
    while queue:
        cost, node = heapq.heappop(queue)
        if cost > best.get(node, float("inf")) + 1.0e-9:
            continue
        if node == end_key:
            break
        for neighbour, segment in adjacency.get(node, ()):
            candidate = cost + float(segment.length)
            if candidate + 1.0e-9 >= best.get(neighbour, float("inf")):
                continue
            best[neighbour] = candidate
            previous[neighbour] = (node, segment)
            heapq.heappush(queue, (candidate, neighbour))
    if end_key not in best:
        return None

    steps = []
    node = end_key
    while node != start_key:
        prior, segment = previous[node]
        steps.append((prior, node, segment))
        node = prior
    steps.reverse()
    points = [start_key]
    segment_ids = []
    edge_tabs = []
    for _prior, target, segment in steps:
        points.append(target)
        segment_ids.append(segment.segment_id)
        edge_tabs.append(bool(segment.retains_tab))
    return tuple(points), tuple(edge_tabs), tuple(segment_ids)


def _single_depth_trail_core(
    points,
    edge_tabs,
    depth,
    safe_height,
    start_depth,
    tab_thickness,
    tabs_3d,
    final_job_depth,
    material_thickness=None,
    previous_pass_depth=None,
    final_pass=False,
    tab_surface_clearance=0.2,
    edge_tab_thicknesses=None,
):
    generated = _build_common_line_trail_moves(
        points,
        edge_tabs,
        [float(depth)],
        float(safe_height),
        start_depth=float(start_depth),
        tab_thickness=float(tab_thickness),
        tabs_3d=bool(tabs_3d),
        final_job_depth=float(final_job_depth),
        material_thickness=material_thickness,
        previous_pass_depth=previous_pass_depth,
        final_pass=final_pass,
        tab_surface_clearance=tab_surface_clearance,
        edge_tab_thicknesses=edge_tab_thicknesses,
    )
    return float(generated[1]["z"]), [dict(move) for move in generated[2:-1]]


def _physical_trail_entry_ramp(
    points,
    edge_tabs,
    ramp_length,
    start_z,
    target_z,
    *,
    tabs_active=False,
    ramp_type="smooth",
    closed=False,
    cut_phase="external",
    tool_diameter=0.0,
    plan_segments=(),
    trail_segment_ids=(),
):
    """Descend on the physical trail and return to the same entry point.

    An open common-line trail has no safe off-profile lead-in.  A one-way ramp
    would either leave its first interval above the requested depth or require
    a second contour lap to clean it.  The constrained out-and-back ramp keeps
    the cutter on the already planned centre-line, reaches ``target_z`` at the
    original entry and lets the scheduled trail begin exactly once from there.
    """

    requested = max(0.0, float(ramp_length))
    if requested <= 1.0e-7 or len(points) < 2:
        return []
    if float(target_z) >= float(start_z) - 1.0e-9:
        return []

    requested_type = str(ramp_type or "smooth").strip().lower()
    if requested_type not in {"smooth", "zigzag", "spiral"}:
        requested_type = "smooth"

    # Uma hélice começa e termina exatamente na entrada, portanto preserva a
    # continuidade do scheduler. Ela só é permitida em loop fechado e depois
    # de provar que todo o círculo fica do lado sacrificial do perfil e longe
    # das demais linhas físicas. Em trail aberto/compartilhado não existe lado
    # livre comprovável; nesse caso o fallback permanece sobre o próprio kerf.
    if requested_type == "spiral" and closed and len(points) >= 4:
        polygon = list(points[:-1] if points[0] == points[-1] else points)
        tangent_x, tangent_y, tangent_length = _normalize_vector(points[0], points[1])
        radius = requested / (2.0 * math.pi)
        if tangent_length > 1.0e-9 and radius > 1.0e-4 and abs(polygon_area(polygon)) > 1.0e-7:
            ccw_polygon = polygon_area(polygon) > 0.0
            interior_normal = (
                (-tangent_y, tangent_x)
                if ccw_polygon
                else (tangent_y, -tangent_x)
            )
            safe_normal = (
                interior_normal
                if str(cut_phase or "external") == "internal"
                else (-interior_normal[0], -interior_normal[1])
            )
            start_x, start_y = float(points[0][0]), float(points[0][1])
            center_x = start_x + safe_normal[0] * radius
            center_y = start_y + safe_normal[1] * radius
            start_angle = math.atan2(start_y - center_y, start_x - center_x)
            ccw_tangent = (-math.sin(start_angle), math.cos(start_angle))
            turn_sign = (
                1.0
                if ccw_tangent[0] * tangent_x + ccw_tangent[1] * tangent_y >= 0.0
                else -1.0
            )
            segment_ids = set(trail_segment_ids)
            obstacle_segments = [
                segment
                for segment in plan_segments
                if getattr(segment, "segment_id", None) not in segment_ids
            ]
            clearance = max(0.0, float(tool_diameter or 0.0))

            def point_segment_distance_xy(point, start, end):
                dx = float(end[0]) - float(start[0])
                dy = float(end[1]) - float(start[1])
                squared = dx * dx + dy * dy
                if squared <= 1.0e-18:
                    return distance(point, start)
                ratio = max(
                    0.0,
                    min(
                        1.0,
                        ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy)
                        / squared,
                    ),
                )
                projection = (start[0] + dx * ratio, start[1] + dy * ratio)
                return distance(point, projection)

            spiral_points = []
            safe = True
            segment_count = 32
            for index in range(1, segment_count + 1):
                ratio = index / float(segment_count)
                angle = start_angle + turn_sign * 2.0 * math.pi * ratio
                point = (
                    center_x + math.cos(angle) * radius,
                    center_y + math.sin(angle) * radius,
                )
                # O último ponto é a própria entrada e pertence à fronteira.
                if index < segment_count:
                    inside = _point_in_polygon(point, polygon)
                    if (cut_phase == "internal" and not inside) or (
                        cut_phase != "internal" and inside
                    ):
                        safe = False
                        break
                    if clearance > 1.0e-7 and any(
                        point_segment_distance_xy(
                            point,
                            segment.start.to_tuple(),
                            segment.end.to_tuple(),
                        )
                        < clearance - 1.0e-7
                        for segment in obstacle_segments
                    ):
                        safe = False
                        break
                spiral_points.append((point, ratio))
            if safe and spiral_points:
                spiral_moves = []
                for (x_value, y_value), ratio in spiral_points:
                    spiral_moves.append(
                        {
                            "type": "feed_ramp",
                            "x": float(x_value),
                            "y": float(y_value),
                            "z": float(start_z)
                            + (float(target_z) - float(start_z)) * ratio,
                            "common_line": True,
                            "entry_ramp": True,
                            "ramp_requested_type": "spiral",
                            "ramp_effective_type": "spiral",
                            "ramp_geometry": "validated_tangent_spiral",
                        }
                    )
                spiral_moves[-1]["x"] = start_x
                spiral_moves[-1]["y"] = start_y
                spiral_moves[-1]["z"] = float(target_z)
                return spiral_moves

    available = 0.0
    for index, (start, end) in enumerate(zip(points, points[1:])):
        if tabs_active and bool(edge_tabs[index]):
            break
        available += distance(start, end)
    if requested_type == "smooth":
        # Uma rampa suave possui uma única direção de descida. Começamos à
        # frente no próprio trail e voltamos continuamente até a entrada do
        # corte. O chamador posiciona a ferramenta nesse ``ramp_origin`` em Z
        # seguro; assim não há a ida descendente + volta descendente que torna
        # Suave visual e fisicamente igual ao Zigue-zague.
        excursion = min(requested, available)
        if excursion <= 1.0e-7:
            return []
        forward = _open_path_points_between(
            points,
            0.0,
            excursion,
            _path_length(points),
        )
        if not forward:
            return []
        route_points = [
            (float(points[0][0]), float(points[0][1]))
        ] + [
            (float(point[0]), float(point[1]))
            for point, _progress in forward
        ]
        origin_x, origin_y = route_points[-1]
        previous = (origin_x, origin_y)
        travelled = 0.0
        moves = []
        for x_value, y_value in reversed(route_points[:-1]):
            travelled += distance(previous, (x_value, y_value))
            ratio = min(1.0, travelled / excursion)
            move = {
                "type": "feed_ramp",
                "x": x_value,
                "y": y_value,
                "z": float(start_z)
                + (float(target_z) - float(start_z)) * ratio,
                "common_line": True,
                "entry_ramp": True,
                "ramp_requested_type": "smooth",
                "ramp_effective_type": "smooth",
                "ramp_geometry": "physical_trail_single_slope",
                "ramp_fallback": False,
            }
            if not moves:
                move["ramp_origin_x"] = origin_x
                move["ramp_origin_y"] = origin_y
            moves.append(move)
            previous = (x_value, y_value)
        moves[-1]["z"] = float(target_z)
        return moves

    excursion = min(requested * 0.5, available)
    if excursion <= 1.0e-7:
        return []

    total_length = _path_length(points)
    forward = _open_path_points_between(
        points,
        0.0,
        excursion,
        total_length,
    )
    if not forward:
        return []

    total_ramp_travel = excursion * 2.0
    moves = []
    effective_type = "zigzag" if requested_type == "zigzag" else "smooth"
    fallback_from_spiral = requested_type == "spiral"
    ramp_points = [(float(points[0][0]), float(points[0][1]))]
    for (x_value, y_value), progress in forward:
        ratio = progress / total_ramp_travel
        moves.append(
            {
                "type": "feed_ramp",
                "x": float(x_value),
                "y": float(y_value),
                "z": float(start_z) + (float(target_z) - float(start_z)) * ratio,
                "common_line": True,
                "entry_ramp": True,
                "ramp_requested_type": requested_type,
                "ramp_effective_type": effective_type,
                "ramp_geometry": "physical_trail_out_and_back",
                "ramp_fallback": fallback_from_spiral,
            }
        )
        ramp_points.append((float(x_value), float(y_value)))

    travelled_back = 0.0
    previous = ramp_points[-1]
    for x_value, y_value in reversed(ramp_points[:-1]):
        travelled_back += distance(previous, (x_value, y_value))
        ratio = min(1.0, (excursion + travelled_back) / total_ramp_travel)
        moves.append(
            {
                "type": "feed_ramp",
                "x": x_value,
                "y": y_value,
                "z": float(start_z) + (float(target_z) - float(start_z)) * ratio,
                "common_line": True,
                "entry_ramp": True,
                "ramp_requested_type": requested_type,
                "ramp_effective_type": effective_type,
                "ramp_geometry": "physical_trail_out_and_back",
                "ramp_fallback": fallback_from_spiral,
            }
        )
        previous = (x_value, y_value)
    moves[-1]["z"] = float(target_z)
    return moves


def build_global_cut_plan_moves(
    plan,
    safe_height,
    *,
    start_xy=(0.0, 0.0),
    return_to_start=False,
    tabs_3d=False,
    start_depth=0.0,
    ramp_length=0.0,
    ramp_type="smooth",
    tool_diameter=0.0,
    stay_down_max_distance=0.0,
    material_thickness=None,
    tab_surface_clearance=0.2,
    tab_release_supervision="per_piece",
    spindle_spinup_seconds=1.0,
    screw_safe_z_margin=1.0,
    tab_release_stepdown=3.0,
    tab_release_ramp_angle_degrees=12.0,
    tab_release_minimum_depth_step=0.5,
):
    """Adapt a validated ``GlobalCutPlan`` to WoodCAM's existing move model.

    Every operation contains physical segment IDs fixed before this adapter is
    called.  This function therefore cannot silently deduplicate geometry and
    cannot reorder across declared safety dependencies.
    """

    plan.validate()
    trails = {trail.trail_id: trail for trail in plan.trails}
    tab_thickness_by_segment = {
        tab.segment_id: float(tab.thickness) for tab in plan.tabs
    }
    moves = []
    if plan.screw_anchors:
        required_safe_height = max(
            anchor.head_height + max(0.0, float(screw_safe_z_margin or 0.0))
            for anchor in plan.screw_anchors
        )
        if float(safe_height) + 1.0e-9 < required_safe_height:
            raise ValueError(
                "A altura segura precisa ficar acima das cabeças dos parafusos "
                "mais a margem configurada (mínimo %.2f mm)."
                % required_safe_height
            )
        for anchor in plan.screw_anchors:
            moves.extend(
                [
                    {
                        "type": "rapid",
                        "x": anchor.point.x,
                        "y": anchor.point.y,
                        "z": float(safe_height),
                        "cut_phase": "screw_pilot",
                        "screw_anchor_id": anchor.anchor_id,
                    },
                    {
                        "type": "feed_plunge",
                        "x": anchor.point.x,
                        "y": anchor.point.y,
                        "z": -abs(float(anchor.pilot_depth)),
                        "cut_phase": "screw_pilot",
                        "screw_anchor_id": anchor.anchor_id,
                        "screw_hole_diameter": anchor.hole_diameter,
                    },
                    {
                        "type": "rapid",
                        "x": None,
                        "y": None,
                        "z": float(safe_height),
                        "cut_phase": "screw_pilot",
                        "screw_anchor_id": anchor.anchor_id,
                    },
                ]
            )
        moves.extend(
            [
                {
                    "type": "rapid",
                    "x": float(start_xy[0]),
                    "y": float(start_xy[1]),
                    "z": float(safe_height),
                    "cut_phase": "screw_setup",
                },
                {
                    "type": "operator_pause",
                    "x": None,
                    "y": None,
                    "z": float(safe_height),
                    "cut_phase": "screw_setup",
                    "message": (
                        "Instale todos os parafusos nos furos piloto e pressione "
                        "Cycle Start para iniciar o nesting."
                    ),
                    "resume_spindle": True,
                    "spinup_seconds": max(
                        0.0, float(spindle_spinup_seconds or 0.0)
                    ),
                },
            ]
        )
    continuous_network = plan.strategy.value in {
        "piece_bidirectional",
        "hybrid_stability",
        "hybrid_piece_bidirectional",
    }
    cleared_by_depth = {}
    active_depth = None
    engaged = False
    current_xy = None
    current_z = float(safe_height)
    stay_down_limit = max(0.0, float(stay_down_max_distance or 0.0))

    def append_z(target_moves, target_z, *, operation=None, link=False):
        nonlocal current_z
        if abs(float(target_z) - current_z) <= 1.0e-9:
            return
        move = {
            "type": "feed_plunge",
            "x": current_xy[0],
            "y": current_xy[1],
            "z": float(target_z),
            "common_line": True,
        }
        if link:
            move["cleared_path_link"] = True
        if operation is not None:
            move["cut_operation_id"] = operation.operation_id
        target_moves.append(move)
        current_z = float(target_z)

    def retract():
        nonlocal engaged, current_z
        if not engaged:
            return
        moves.append(
            {"type": "rapid", "x": None, "y": None, "z": float(safe_height)}
        )
        engaged = False
        current_z = float(safe_height)

    for operation in plan.operations:
        trail = trails.get(operation.trail_id)
        if trail is None:
            raise ValueError(
                "O plano global referencia uma trilha inexistente: %s"
                % operation.trail_id
            )
        trail = trail.oriented(
            reverse=bool(operation.reverse_trail),
            start_edge_index=int(operation.start_edge_index),
        )
        if tuple(trail.segment_ids) != tuple(operation.segment_ids):
            raise ValueError(
                "A travessia da trilha não corresponde aos segmentos da operação."
            )
        edge_tab_heights = tuple(
            tab_thickness_by_segment.get(segment_id, 0.0)
            for segment_id in trail.segment_ids
        )
        thickness = max(edge_tab_heights, default=0.0)
        trail_points = [point.to_tuple() for point in trail.points]
        initial_z, cut_core = _single_depth_trail_core(
            trail_points,
            trail.edge_tabs,
            operation.depth,
            float(safe_height),
            start_depth,
            thickness,
            tabs_3d,
            plan.depths[-1],
            material_thickness,
            (
                start_depth
                if plan.depths.index(operation.depth) == 0
                else plan.depths[plan.depths.index(operation.depth) - 1]
            ),
            bool(operation.final_pass),
            tab_surface_clearance,
            edge_tab_thicknesses=edge_tab_heights,
        )
        depth_key = round(abs(float(operation.depth)), 9)
        cleared_at_depth = cleared_by_depth.setdefault(depth_key, set())

        operation_moves = []
        trail_start = trail_points[0]
        if (
            continuous_network
            and engaged
            and _global_point_key(current_xy) != _global_point_key(trail_start)
        ):
            connector = None
            connector_depth = depth_key
            # The experimental piece-by-piece route deliberately retracts
            # between distinct physical trails.  Reusing an already machined
            # edge as a low-Z connector would look (and sound) like another
            # cut of that edge, defeating the physical DONE contract.  The
            # outbound/return pair still remains engaged because both passes
            # meet at exactly the same endpoint.  Stability routing keeps the
            # established cleared-kerf connector behaviour.
            connector_candidates = []
            if operation.routing_mode == "final_sheet_pass":
                # A última passada nunca usa um segmento já executado nessa
                # mesma profundidade como corredor: isso repetiria fisicamente
                # o terceiro corte apenas para chegar a outro ponto. Somente
                # um kerf de camada anterior e realmente curto pode evitar a
                # retração; em qualquer outro caso sobe e usa G0.
                connector_candidates.extend(
                    (candidate_depth, cleared_by_depth[candidate_depth])
                    for candidate_depth in sorted(
                        (
                            value
                            for value in cleared_by_depth
                            if value != depth_key
                        ),
                        key=lambda value: (
                            value != active_depth,
                            abs(value - depth_key),
                            -value,
                        ),
                    )
                )
            elif operation.routing_mode != "piece_bidirectional":
                connector_candidates = [(depth_key, cleared_at_depth)]
                connector_candidates.extend(
                    (candidate_depth, cleared_by_depth[candidate_depth])
                    for candidate_depth in sorted(
                        (
                            value
                            for value in cleared_by_depth
                            if value != depth_key
                        ),
                        key=lambda value: (
                            value != active_depth,
                            abs(value - depth_key),
                            -value,
                        ),
                    )
                )
            for candidate_depth, candidate_segments in connector_candidates:
                candidate_connector = _shortest_cleared_connector(
                    plan,
                    candidate_segments,
                    current_xy,
                    trail_start,
                )
                if candidate_connector is None:
                    continue
                if operation.routing_mode == "final_sheet_pass":
                    link_points = candidate_connector[0]
                    link_length = sum(
                        distance(first, second)
                        for first, second in zip(link_points, link_points[1:])
                    )
                    if link_length > stay_down_limit + 1.0e-9:
                        continue
                connector = candidate_connector
                if connector is not None:
                    connector_depth = candidate_depth
                    break
            if connector is not None:
                link_points, link_tabs, link_segment_ids = connector
                link_thickness = max(
                    (
                        tab_thickness_by_segment.get(segment_id, 0.0)
                        for segment_id in link_segment_ids
                    ),
                    default=0.0,
                )
                link_edge_tab_heights = tuple(
                    tab_thickness_by_segment.get(segment_id, 0.0)
                    for segment_id in link_segment_ids
                )
                link_initial_z, link_core = _single_depth_trail_core(
                    link_points,
                    link_tabs,
                    connector_depth,
                    safe_height,
                    start_depth,
                    link_thickness,
                    tabs_3d,
                    plan.depths[-1],
                    material_thickness,
                    start_depth,
                    False,
                    tab_surface_clearance,
                    edge_tab_thicknesses=link_edge_tab_heights,
                )
                append_z(
                    operation_moves,
                    link_initial_z,
                    operation=operation,
                    link=True,
                )
                if operation_moves and operation_moves[-1].get("cleared_path_link"):
                    operation_moves[-1]["cleared_link_segment_ids"] = (
                        link_segment_ids
                    )
                    operation_moves[-1]["depth_pass"] = float(connector_depth)
                    operation_moves[-1]["link_target_depth_pass"] = float(
                        operation.depth
                    )
                    operation_moves[-1]["cleared_path_link_length"] = sum(
                        distance(first, second)
                        for first, second in zip(link_points, link_points[1:])
                    )
                for move in link_core:
                    move["cleared_path_link"] = True
                    move["cleared_link_segment_ids"] = link_segment_ids
                    move["global_cut"] = True
                    move["cut_strategy"] = plan.strategy.value
                    move["cut_phase"] = "cleared_path_link"
                    move["depth_pass"] = float(connector_depth)
                    move["link_target_depth_pass"] = float(operation.depth)
                    move["cleared_path_link_length"] = sum(
                        distance(first, second)
                        for first, second in zip(link_points, link_points[1:])
                    )
                    operation_moves.append(move)
                if link_core:
                    current_xy = (link_core[-1]["x"], link_core[-1]["y"])
                    current_z = float(link_core[-1]["z"])
            else:
                retract()

        if not engaged:
            operation_moves.append(
                {
                    "type": "rapid",
                    "x": trail_start[0],
                    "y": trail_start[1],
                    "z": float(safe_height),
                }
            )
            current_xy = trail_start
            current_z = float(safe_height)
            engaged = True
        pass_index = plan.depths.index(operation.depth)
        previous_depth = (
            abs(float(start_depth))
            if pass_index == 0
            else float(plan.depths[pass_index - 1])
        )
        retained_z = _tab_target_z(
            material_thickness,
            thickness,
            final_depth=plan.depths[-1],
            start_depth=start_depth,
            previous_pass_depth=previous_depth,
            final_pass=bool(operation.final_pass),
            surface_clearance=tab_surface_clearance,
        )
        tabs_active = bool(
            any(trail.edge_tabs)
            and retained_z > -abs(float(operation.depth)) + 1.0e-7
        )
        ramp_moves = []
        if max(0.0, float(ramp_length)) > 1.0e-7 and initial_z < current_z - 1.0e-9:
            surface_z = -abs(float(start_depth))
            ramp_start_z = min(float(current_z), float(surface_z))
            ramp_moves = _physical_trail_entry_ramp(
                trail_points,
                trail.edge_tabs,
                ramp_length,
                ramp_start_z,
                initial_z,
                tabs_active=tabs_active,
                ramp_type=ramp_type,
                closed=bool(trail.closed),
                cut_phase=operation.phase.value,
                tool_diameter=tool_diameter,
                plan_segments=plan.segments,
                trail_segment_ids=trail.segment_ids,
            )
            if (
                ramp_moves
                and ramp_moves[0].get("ramp_geometry")
                == "physical_trail_single_slope"
            ):
                ramp_origin = (
                    float(ramp_moves[0]["ramp_origin_x"]),
                    float(ramp_moves[0]["ramp_origin_y"]),
                )
                # A rampa suave começa adiante no trail e termina no ponto
                # inicial da operação. Nas camadas seguintes, a ida até sua
                # origem usa somente o kerf já limpo na profundidade anterior;
                # a descida continua sendo uma única inclinação na volta. Na
                # primeira camada (ou sem kerf comprovado), todo XY ocorre em
                # Z seguro.
                can_approach_in_previous_kerf = (
                    pass_index > 0
                    and current_z <= -abs(float(previous_depth)) + 1.0e-7
                    and _global_point_key(current_xy)
                    == _global_point_key(trail_start)
                )
                if can_approach_in_previous_kerf:
                    approach_points = list(
                        reversed(
                            [
                                (float(move["x"]), float(move["y"]))
                                for move in ramp_moves
                            ]
                        )
                    )
                    approach_points.append(ramp_origin)
                    for approach_x, approach_y in approach_points:
                        if _global_point_key(current_xy) == _global_point_key(
                            (approach_x, approach_y)
                        ):
                            continue
                        approach_length = distance(
                            current_xy,
                            (approach_x, approach_y),
                        )
                        operation_moves.append(
                            {
                                "type": "feed_cut",
                                "x": approach_x,
                                "y": approach_y,
                                "z": float(current_z),
                                "common_line": True,
                                "cleared_path_link": True,
                                "entry_ramp_approach": True,
                                "cut_operation_id": operation.operation_id,
                                "depth_pass": float(previous_depth),
                                "link_target_depth_pass": float(
                                    operation.depth
                                ),
                                "cleared_path_link_length": approach_length,
                                "cleared_link_segment_ids": tuple(
                                    trail.segment_ids
                                ),
                            }
                        )
                        current_xy = (approach_x, approach_y)
                else:
                    if current_z < float(safe_height) - 1.0e-9:
                        operation_moves.append(
                            {
                                "type": "rapid",
                                "x": None,
                                "y": None,
                                "z": float(safe_height),
                            }
                        )
                        current_z = float(safe_height)
                    if _global_point_key(current_xy) != _global_point_key(
                        ramp_origin
                    ):
                        if (
                            operation_moves
                            and operation_moves[-1].get("type") == "rapid"
                            and operation_moves[-1].get("x") is not None
                            and abs(
                                float(
                                    operation_moves[-1].get(
                                        "z", safe_height
                                    )
                                )
                                - float(safe_height)
                            )
                            <= 1.0e-9
                        ):
                            operation_moves[-1]["x"] = ramp_origin[0]
                            operation_moves[-1]["y"] = ramp_origin[1]
                        else:
                            operation_moves.append(
                                {
                                    "type": "rapid",
                                    "x": ramp_origin[0],
                                    "y": ramp_origin[1],
                                    "z": float(safe_height),
                                }
                            )
                        current_xy = ramp_origin
                    append_z(
                        operation_moves,
                        ramp_start_z,
                        operation=operation,
                    )
            elif current_z > surface_z + 1.0e-9:
                append_z(operation_moves, surface_z, operation=operation)
        if ramp_moves:
            operation_moves.extend(ramp_moves)
            current_xy = trail_start
            current_z = float(initial_z)
        else:
            append_z(operation_moves, initial_z, operation=operation)
        cut_start_index = len(operation_moves)
        operation_moves.extend(cut_core)
        if cut_core:
            current_xy = (cut_core[-1]["x"], cut_core[-1]["y"])
            current_z = float(cut_core[-1]["z"])
        cleared_at_depth.update(operation.segment_ids)
        active_depth = depth_key
        if not continuous_network:
            operation_moves.append(
                {"type": "rapid", "x": None, "y": None, "z": float(safe_height)}
            )
            engaged = False
            current_z = float(safe_height)
        if trail.closed:
            trail_length = sum(
                distance(start.to_tuple(), end.to_tuple())
                for start, end in zip(trail.points, trail.points[1:])
            )
            _mark_profile_loop(
                operation_moves,
                cut_start_index,
                end_index=cut_start_index + len(cut_core),
                profile_id="physical-profile:" + ",".join(
                    sorted(trail.segment_ids)
                ),
                pass_index=plan.depths.index(operation.depth),
                depth=operation.depth,
                expected_length=trail_length,
                start_position=(
                    trail.points[0].x,
                    trail.points[0].y,
                    initial_z,
                ),
            )
        for move in operation_moves:
            if move.get("cleared_path_link"):
                move["global_cut"] = True
                move["cut_strategy"] = plan.strategy.value
                move["cut_phase"] = "cleared_path_link"
                move.setdefault("depth_pass", float(operation.depth))
                move.setdefault("link_target_depth_pass", float(operation.depth))
                move["final_pass"] = bool(operation.final_pass)
                move["routing_mode"] = operation.routing_mode
                continue
            move["global_cut"] = True
            move["cut_strategy"] = plan.strategy.value
            move["cut_phase"] = operation.phase.value
            move["cut_operation_id"] = operation.operation_id
            move["segment_ids"] = operation.segment_ids
            move["depth_pass"] = float(operation.depth)
            move["final_pass"] = bool(operation.final_pass)
            move["routing_mode"] = operation.routing_mode
            move["trail_reversed"] = bool(operation.reverse_trail)
            move["trail_start_edge_index"] = int(operation.start_edge_index)
            move["executing_owner_id"] = operation.executing_owner_id
            move["physical_trail_closed"] = bool(trail.closed)
        moves.extend(operation_moves)

    retract()

    release_supervision = str(tab_release_supervision or "per_piece")
    if release_supervision not in {"all_tabs", "per_piece", "per_tab"}:
        raise ValueError("Modo de supervisão da remoção de tabs inválido.")
    release_tabs_by_id = {tab.tab_id: tab for tab in plan.tabs}
    previous_release_owner = None
    for release_index, release in enumerate(plan.tab_release_operations):
        plunge = release.plunge_point
        sweep_end = release.sweep_end
        pause_before = (
            (release_supervision == "all_tabs" and release_index == 0)
            or release_supervision == "per_tab"
            or (
                release_supervision == "per_piece"
                and release.owner_id != previous_release_owner
            )
        )
        release_moves = []
        if pause_before:
            release_moves.append(
                {
                    "type": "operator_pause",
                    "x": None,
                    "y": None,
                    "z": float(safe_height),
                    "message": (
                        "Prenda/segure todas as peças e pressione Cycle Start "
                        "para remover todas as tabs."
                        if release_supervision == "all_tabs"
                        else (
                            "Prenda/segure %s e pressione Cycle Start para "
                            "remover %s."
                            % (
                                release.owner_id,
                                "esta tab"
                                if release_supervision == "per_tab"
                                else "as tabs da peça",
                            )
                        )
                    ),
                    "resume_spindle": True,
                    "spinup_seconds": max(
                        0.0, float(spindle_spinup_seconds or 0.0)
                    ),
                }
            )
        target_depth = abs(float(release.target_depth))
        release_tab = release_tabs_by_id.get(release.tab_id)
        intact_height = max(
            0.0,
            float(getattr(release_tab, "thickness", 0.0) or 0.0),
        )
        if material_thickness is not None:
            bridge_top_depth = max(
                abs(float(start_depth)),
                abs(float(material_thickness)) - intact_height,
            )
        else:
            bridge_top_depth = max(
                abs(float(start_depth)),
                target_depth - intact_height,
            )
        bridge_top_depth = min(target_depth, bridge_top_depth)
        configured_stepdown = abs(float(tab_release_stepdown or 0.0))
        ramp_angle = max(
            1.0,
            min(45.0, abs(float(tab_release_ramp_angle_degrees or 0.0))),
        )
        minimum_depth_step = max(
            0.05,
            abs(float(tab_release_minimum_depth_step or 0.0)),
        )
        geometric_depth_step = (
            float(release.sweep_length) * math.tan(math.radians(ramp_angle))
        )
        supports_zigzag = bool(
            release.sweep_length > 1.0e-9
            and geometric_depth_step + 1.0e-9 >= minimum_depth_step
        )
        release_depth_step = (
            min(
                (
                    configured_stepdown
                    if configured_stepdown > 1.0e-6
                    else geometric_depth_step
                ),
                geometric_depth_step,
            )
            if supports_zigzag
            else max(1.0e-6, target_depth - bridge_top_depth)
        )
        release_depths = []
        next_depth = bridge_top_depth + release_depth_step
        while next_depth < target_depth - 1.0e-9:
            release_depths.append(next_depth)
            next_depth += release_depth_step
        release_depths.append(target_depth)
        # A última rampa precisa chegar ao fundo em ``plunge``. Depois dela,
        # uma travessia nivelada limpa toda a largura até ``sweep_end`` — o
        # extremo escolhido por _release_geometry para ficar afastado da peça.
        # Sem essa passada plana, a última diagonal deixaria uma cunha de MDF:
        # somente seu ponto final teria atingido a profundidade total.
        if len(release_depths) % 2:
            ramp_start, ramp_other = sweep_end, plunge
        else:
            ramp_start, ramp_other = plunge, sweep_end
        if supports_zigzag:
            release_moves.extend(
                [
                    {
                        "type": "rapid",
                        "x": ramp_start.x,
                        "y": ramp_start.y,
                        "z": float(safe_height),
                    },
                    {
                        "type": "feed_plunge",
                        "x": ramp_start.x,
                        "y": ramp_start.y,
                        "z": -bridge_top_depth,
                    },
                ]
            )
            current_endpoint = ramp_start
            for depth in release_depths:
                current_endpoint = (
                    ramp_other
                    if current_endpoint.almost_equals(ramp_start, 1.0e-9)
                    else ramp_start
                )
                release_moves.append(
                    {
                        "type": "feed_ramp",
                        "x": current_endpoint.x,
                        "y": current_endpoint.y,
                        "z": -abs(float(depth)),
                        "tab_release_zigzag": True,
                        "tab_release_bridge_top_depth": bridge_top_depth,
                        "tab_release_depth_step": release_depth_step,
                        "tab_release_ramp_angle_degrees": ramp_angle,
                    }
                )
            release_moves.append(
                {
                    # Usa o avanço cauteloso de rampa mesmo com Z constante.
                    # A semântica CAM adicional distingue esta limpeza plana.
                    "type": "feed_ramp",
                    "x": sweep_end.x,
                    "y": sweep_end.y,
                    "z": -target_depth,
                    "tab_release_zigzag": True,
                    "tab_release_cleanup": True,
                    "tab_release_bridge_top_depth": bridge_top_depth,
                    "tab_release_depth_step": release_depth_step,
                    "tab_release_ramp_angle_degrees": ramp_angle,
                }
            )
        else:
            # Uma tab no máximo tão larga quanto a fresa, ou cujo curso útil
            # produziria um passo menor que o mínimo operacional, não oferece
            # rampa XY real. Nesse caso o mergulho central direto continua
            # sendo geometricamente mais curto e não aplica esforço lateral.
            release_moves.extend(
                [
                    {
                        "type": "rapid",
                        "x": plunge.x,
                        "y": plunge.y,
                        "z": float(safe_height),
                    },
                    {
                        "type": "feed_plunge",
                        "x": plunge.x,
                        "y": plunge.y,
                        "z": -target_depth,
                        "tab_release_vertical_fallback": True,
                    },
                ]
            )
        # This retract is deliberately adjacent to the plunge/sweep.  In
        # particular, a last tab can never transition into another contour.
        release_moves.append(
            {"type": "rapid", "x": None, "y": None, "z": float(safe_height)}
        )
        for move in release_moves:
            move["global_cut"] = True
            move["cut_strategy"] = plan.strategy.value
            move["cut_phase"] = "tab_release"
            move["tab_release"] = True
            move["tab_id"] = release.tab_id
            move["tab_release_operation_id"] = release.operation_id
            move["tab_release_owner"] = release.owner_id
            move["tab_release_last_for_piece"] = bool(release.last_for_piece)
            move["tab_release_sweep_length"] = float(release.sweep_length)
        moves.extend(release_moves)
        previous_release_owner = release.owner_id

    validate_profile_cut_moves(moves)
    return _with_configured_start(
        moves,
        start_xy,
        float(safe_height),
        bool(return_to_start),
    )


def build_common_line_cut_job(
    polylines,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    material_thickness=None,
    start_xy=(0.0, 0.0),
    start_depth=0.0,
    pass_depths=None,
    return_to_start=False,
    tab_thickness=0.0,
    tabs_3d=False,
):
    """Gera corte de uma rede de trilhas abertas e perímetros exclusivos.

    ``polylines`` contém linhas-centro já planejadas pelo detector de fronteira
    comum. Cada item pode ser uma sequência de pontos ou um dicionário com
    ``points`` e ``tab``. Esta função deliberadamente não compensa a ferramenta
    nem fecha extremos implicitamente. Uma trilha com ``edge_tabs`` pode já ser
    fechada quando uma peça da organização não compartilha nenhuma de suas
    bordas. Cada intervalo aparece uma única vez no trabalho; uma faixa marcada
    como tab é limitada em Z e nunca é seguida por um segundo contorno que possa
    removê-la.
    """
    raw_polylines = list(polylines or [])
    if not raw_polylines:
        return []

    # Conveniência para o chamador: uma única polilinha pode ser fornecida
    # diretamente como [(x, y), ...], além da forma [[(x, y), ...], ...].
    first = raw_polylines[0]
    if isinstance(first, dict):
        first = first.get("points", ())
    try:
        first_is_point = len(first) >= 2 and all(
            isinstance(first[index], (int, float)) for index in (0, 1)
        )
    except TypeError:
        first_is_point = False
    if first_is_point:
        raw_polylines = [raw_polylines]

    try:
        stepdown_value = float(stepdown)
    except (TypeError, ValueError):
        stepdown_value = 0.0
    if stepdown_value <= 0.0:
        raise ValueError("O passo de profundidade da linha comum deve ser maior que zero.")

    initial_depth = min(abs(float(start_depth)), abs(float(final_depth)))
    depth_steps = generate_depth_steps(
        final_depth,
        stepdown_value,
        material_thickness=material_thickness,
        start_depth=initial_depth,
        pass_depths=pass_depths,
    )
    if not depth_steps:
        raise ValueError("Profundidade final da linha comum deve ser maior que zero.")

    remaining = []
    for index, specification in enumerate(raw_polylines):
        if isinstance(specification, dict):
            points = specification.get("points", ())
            retain_tab = bool(specification.get("tab", False))
            edge_tabs = specification.get("edge_tabs")
            priority = int(specification.get("priority", 0) or 0)
            preserve_direction = bool(
                specification.get("preserve_direction", False)
            )
        else:
            points = specification
            retain_tab = False
            edge_tabs = None
            priority = 0
            preserve_direction = False
        clean_points = _clean_open_points(
            points,
            allow_closed=edge_tabs is not None,
        )
        if edge_tabs is not None:
            edge_tabs = tuple(bool(value) for value in edge_tabs)
            if len(edge_tabs) != len(clean_points) - 1:
                raise ValueError(
                    "Cada aresta da trilha precisa informar se contém tab."
                )
        remaining.append(
            (
                index,
                clean_points,
                retain_tab,
                edge_tabs,
                priority,
                preserve_direction,
            )
        )
    current_xy = (float(start_xy[0]), float(start_xy[1]))
    moves = []

    while remaining:
        candidates = []
        for (
            original_index,
            points,
            retain_tab,
            edge_tabs,
            priority,
            preserve_direction,
        ) in remaining:
            direct_distance = distance(current_xy, points[0])
            reverse_distance = distance(current_xy, points[-1])
            if not preserve_direction and reverse_distance + 1e-9 < direct_distance:
                prepared = list(reversed(points))
                prepared_edge_tabs = (
                    tuple(reversed(edge_tabs)) if edge_tabs is not None else None
                )
                travel = reverse_distance
                reversed_path = True
            else:
                prepared = list(points)
                prepared_edge_tabs = edge_tabs
                travel = direct_distance
                reversed_path = False
            candidates.append(
                (
                    travel,
                    priority,
                    original_index,
                    reversed_path,
                    prepared,
                    retain_tab,
                    prepared_edge_tabs,
                    (
                        original_index,
                        points,
                        retain_tab,
                        edge_tabs,
                        priority,
                        preserve_direction,
                    ),
                )
            )

        (
            _travel,
            _priority,
            _index,
            _reversed,
            selected,
            retain_tab,
            selected_edge_tabs,
            original,
        ) = min(
            candidates,
            key=lambda item: (item[1], item[0], item[2], item[3]),
        )
        if selected_edge_tabs is None:
            line_moves = _build_common_line_polyline_moves(
                selected,
                depth_steps,
                ramp_length,
                safe_height,
                start_depth=initial_depth,
                retain_tab=retain_tab,
                tab_thickness=tab_thickness,
                tabs_3d=tabs_3d,
                material_thickness=material_thickness,
            )
        else:
            line_moves = _build_common_line_trail_moves(
                selected,
                selected_edge_tabs,
                depth_steps,
                safe_height,
                start_depth=initial_depth,
                tab_thickness=tab_thickness,
                tabs_3d=tabs_3d,
                material_thickness=material_thickness,
            )
        moves.extend(line_moves)
        for move in reversed(line_moves):
            if move.get("x") is not None and move.get("y") is not None:
                current_xy = (float(move["x"]), float(move["y"]))
                break
        remaining.remove(original)

    if return_to_start and moves:
        moves.append(
            {
                "type": "rapid",
                "x": float(start_xy[0]),
                "y": float(start_xy[1]),
                "z": float(safe_height),
            }
        )
    return moves


def build_external_cut_moves(
    path_points,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    tool_diameter=0.0,
    compensate_external=True,
    material_thickness=None,
    current_xy=(0.0, 0.0),
    smart_entry=True,
    cut_side=None,
    start_depth=0.0,
    corner_slowdown_enabled=False,
    corner_angle_threshold=45.0,
    corner_feed_percent=40.0,
    corner_slowdown_distance=8.0,
    climb=None,
    tabs_enabled=False,
    tab_length=12.0,
    tab_thickness=3.0,
    tab_count=4,
    tab_positions=None,
    tabs_3d=False,
    ramp_type="smooth",
    pass_depths=None,
    tab_best_fixation=False,
    profile_id="profile-0001",
    tab_surface_clearance=0.2,
):
    if not path_points:
        return []

    depth_steps = generate_depth_steps(
        final_depth,
        stepdown,
        material_thickness=material_thickness,
        start_depth=start_depth,
        pass_depths=pass_depths,
    )
    if not depth_steps:
        raise ValueError("Profundidade final deve ser maior que zero.")

    points = _prepare_cut_points(
        path_points,
        tool_diameter=tool_diameter,
        compensate_external=compensate_external,
        cut_side=cut_side,
        current_xy=current_xy,
        ramp_length=ramp_length,
        smart_entry=smart_entry,
        climb=climb,
    )

    full_path = _closed_path(points)
    total_contour_length = _path_length(full_path)

    if total_contour_length < 1e-6:
        raise ValueError("O contorno tem comprimento muito pequeno.")

    effective_ramp_length = 0.0
    if ramp_length > 0.0:
        # A rampa nao deve ocupar a volta toda; deixar pelo menos metade para corte plano.
        effective_ramp_length = min(ramp_length, total_contour_length * 0.5)
    ramp_type = str(ramp_type or "smooth").strip().lower()
    if ramp_type not in {"smooth", "zigzag", "spiral"}:
        ramp_type = "smooth"

    moves = []
    cursor_distance = 0.0
    manual_tab_ratios = _tab_position_ratios_for_path(
        full_path,
        tab_positions,
        total_contour_length,
    )
    if tabs_enabled and tab_best_fixation and tab_count and not manual_tab_ratios:
        tab_ranges = _best_fixation_tab_ranges(
            full_path,
            total_contour_length,
            tab_length,
            tab_count,
            tool_diameter,
            material_thickness,
            tab_thickness,
        )
    elif tabs_enabled:
        tab_ranges = _tab_ranges(
            total_contour_length,
            tab_length,
            tab_count,
            manual_tab_ratios,
        )
    else:
        tab_ranges = []
    start_x, start_y = _point_at_distance(full_path, cursor_distance, total_contour_length)
    moves.append({"type": "rapid", "x": start_x, "y": start_y, "z": safe_height})
    moves.append(
        {
            "type": "feed_plunge",
            "x": start_x,
            "y": start_y,
            "z": -abs(float(start_depth)),
        }
    )

    for pass_idx, depth in enumerate(depth_steps):
        z_target = -abs(depth)
        prev_depth = (
            -abs(float(start_depth))
            if pass_idx == 0
            else -abs(depth_steps[pass_idx - 1])
        )

        if effective_ramp_length and ramp_type == "spiral":
            # Hélice real: um círculo no plano XY que desce continuamente em
            # Z. Seguir o contorno inteiro aqui seria uma rampa de perfil,
            # não uma entrada helicoidal.
            tangent_x, tangent_y, _ = _normalize_vector(
                (start_x, start_y),
                _point_at_distance(full_path, cursor_distance + 0.001, total_contour_length),
            )
            if abs(tangent_x) < 1e-9 and abs(tangent_y) < 1e-9:
                tangent_x, tangent_y = 1.0, 0.0
            # O comprimento configurado representa aproximadamente uma volta
            # da hélice. O centro fica do lado livre da trajetória compensada.
            helix_radius = effective_ramp_length / (2.0 * math.pi)
            outward_sign = 1.0 if polygon_area(points) > 0.0 else -1.0
            outside_normal = (tangent_y * outward_sign, -tangent_x * outward_sign)
            normalized_side = normalize_cut_side(cut_side, compensate_external)
            safe_normal = (
                (-outside_normal[0], -outside_normal[1])
                if normalized_side == CUT_SIDE_INSIDE
                else outside_normal
            )
            center_x = start_x + safe_normal[0] * helix_radius
            center_y = start_y + safe_normal[1] * helix_radius
            start_angle = math.atan2(start_y - center_y, start_x - center_x)
            ccw_tangent = (-math.sin(start_angle), math.cos(start_angle))
            turn_sign = 1.0 if (ccw_tangent[0] * tangent_x + ccw_tangent[1] * tangent_y) >= 0.0 else -1.0
            helix_segments = 32
            for segment in range(1, helix_segments + 1):
                ratio = segment / float(helix_segments)
                angle = start_angle + turn_sign * (2.0 * math.pi * ratio)
                moves.append(
                    {
                        "type": "feed_ramp",
                        "x": center_x + math.cos(angle) * helix_radius,
                        "y": center_y + math.sin(angle) * helix_radius,
                        "z": prev_depth + (z_target - prev_depth) * ratio,
                    }
                )
        elif effective_ramp_length and ramp_type == "zigzag":
            # Uma ida e volta dá uma entrada progressiva sem riscar o mesmo
            # trecho repetidas vezes antes de começar o perfil.
            leg_count = 2
            excursion = min(effective_ramp_length / leg_count, total_contour_length * 0.25)
            forward = [
                (_point_at_distance(full_path, cursor_distance, total_contour_length), 0.0)
            ] + _path_points_with_progress(
                full_path,
                cursor_distance,
                excursion,
                total_contour_length,
            )
            total_progress = excursion * leg_count
            travelled = 0.0
            for leg in range(leg_count):
                leg_points = forward[1:] if leg % 2 == 0 else list(reversed(forward[:-1]))
                previous_point = forward[0][0] if leg % 2 == 0 else forward[-1][0]
                for (ramp_x, ramp_y), _local_progress in leg_points:
                    travelled += distance(previous_point, (ramp_x, ramp_y))
                    ratio = min(1.0, travelled / max(total_progress, 1e-9))
                    moves.append({"type": "feed_ramp", "x": ramp_x, "y": ramp_y, "z": prev_depth + (z_target - prev_depth) * ratio})
                    previous_point = (ramp_x, ramp_y)
        elif effective_ramp_length:
            ramp_points = _path_points_with_progress(
                full_path,
                cursor_distance,
                effective_ramp_length,
                total_contour_length,
            )
            for (ramp_x, ramp_y), progress in ramp_points:
                ratio = progress / effective_ramp_length
                ramp_z = prev_depth + (z_target - prev_depth) * ratio
                moves.append({"type": "feed_ramp", "x": ramp_x, "y": ramp_y, "z": ramp_z})

            cursor_distance = (cursor_distance + effective_ramp_length) % total_contour_length
        else:
            plunge_x, plunge_y = _point_at_distance(full_path, cursor_distance, total_contour_length)
            moves.append({"type": "feed_plunge", "x": plunge_x, "y": plunge_y, "z": z_target})

        loop_start = _point_at_distance(
            full_path,
            cursor_distance,
            total_contour_length,
        )
        loop_move_start = len(moves)
        if tab_ranges:
            tab_z = _tab_target_z(
                material_thickness,
                tab_thickness,
                final_depth=depth_steps[-1],
                start_depth=start_depth,
                previous_pass_depth=(
                    start_depth if pass_idx == 0 else depth_steps[pass_idx - 1]
                ),
                final_pass=pass_idx == len(depth_steps) - 1,
                surface_clearance=tab_surface_clearance,
            )
            use_3d_tabs = bool(
                tabs_3d
                and not _tab_uses_full_material_height(
                    material_thickness, tab_thickness
                )
            )
        else:
            # Disabled tabs may retain old field values in a saved operation.
            # Those dormant values must not validate or change an ordinary
            # profile cut until the operator enables tabs again.
            tab_z = z_target
            use_3d_tabs = False
        # Tabs passam a limitar Z assim que um passe tentaria ultrapassar sua
        # altura. Esperar apenas a última passada não pode restaurar material
        # já removido por um stepdown intermediário.
        tabs_on_this_pass = True
        if tabs_on_this_pass and tab_ranges and z_target < tab_z - 1e-7:
            current_x, current_y = loop_start
            current_z = z_target
            for (target_x, target_y), is_tab_segment, target_distance in _full_loop_segments_with_tab_state(
                full_path,
                cursor_distance,
                total_contour_length,
                tab_ranges,
            ):
                target_z = tab_z if is_tab_segment else z_target
                if use_3d_tabs and is_tab_segment:
                    for tab_start, tab_end in tab_ranges:
                        if tab_start - 1e-7 <= target_distance <= tab_end + 1e-7:
                            ratio = (target_distance - tab_start) / (tab_end - tab_start)
                            lift_ratio = max(0.0, min(1.0, ratio * 2.0, (1.0 - ratio) * 2.0))
                            target_z = z_target + (tab_z - z_target) * lift_ratio
                            break
                if abs(current_z - target_z) > 1e-7:
                    if not use_3d_tabs:
                        moves.append(
                            {
                                "type": "feed_cut",
                                "x": current_x,
                                "y": current_y,
                                "z": target_z,
                                "tab": bool(is_tab_segment),
                            }
                        )
                    current_z = target_z
                moves.append(
                    {
                        "type": "feed_cut",
                        "x": target_x,
                        "y": target_y,
                        "z": target_z,
                        "tab": bool(is_tab_segment),
                    }
                )
                if corner_slowdown_enabled and not is_tab_segment:
                    for vertex_index, vertex in enumerate(points):
                        if distance(vertex, (target_x, target_y)) > 1.0e-6:
                            continue
                        if (
                            _direction_change_degrees(
                                points[vertex_index - 1],
                                vertex,
                                points[(vertex_index + 1) % len(points)],
                            )
                            + 1.0e-7
                            >= float(corner_angle_threshold)
                        ):
                            moves[-1]["feed_scale"] = (
                                float(corner_feed_percent) / 100.0
                            )
                            moves[-1]["corner_slowdown"] = True
                        break
                current_x, current_y = target_x, target_y
        else:
            loop_targets = _full_loop_points_from(
                full_path,
                cursor_distance,
                total_contour_length,
            )
            _append_corner_aware_loop(
                moves,
                loop_start,
                loop_targets,
                points,
                z_target,
                enabled=corner_slowdown_enabled,
                angle_threshold=corner_angle_threshold,
                feed_scale=float(corner_feed_percent) / 100.0,
                slowdown_distance=corner_slowdown_distance,
            )

        _mark_profile_loop(
            moves,
            loop_move_start,
            profile_id=profile_id,
            pass_index=pass_idx,
            depth=depth,
            expected_length=total_contour_length,
            start_position=(loop_start[0], loop_start[1], z_target),
        )

    moves.append({"type": "rapid", "x": None, "y": None, "z": safe_height})

    validate_profile_cut_moves(moves)

    return moves


def build_external_cut_job(
    contours,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    tool_diameter=0.0,
    compensate_external=True,
    material_thickness=None,
    smart_entry=True,
    cut_side=None,
    start_xy=(0.0, 0.0),
    start_depth=0.0,
    corner_slowdown_enabled=False,
    corner_angle_threshold=45.0,
    corner_feed_percent=40.0,
    corner_slowdown_distance=8.0,
    climb=None,
    tabs_enabled=False,
    tab_length=12.0,
    tab_thickness=3.0,
    tab_count=4,
    tab_positions=None,
    tabs_3d=False,
    ramp_type="smooth",
    pass_depths=None,
    tab_best_fixation=False,
    profile_id_prefix="profile",
    tab_surface_clearance=0.2,
):
    if not contours:
        return []

    moves = []
    remaining = [
        (index, list(contour))
        for index, contour in enumerate(contours)
        if contour
    ]
    tab_positions_by_profile = {
        profile_index: positions
        for (profile_index, _contour), positions in zip(
            remaining,
            _tab_positions_by_path(
                [contour for _profile_index, contour in remaining],
                tab_positions,
            ),
        )
    }
    current_xy = start_xy

    while remaining:
        candidates = []
        for profile_index, contour in remaining:
            prepared = _prepare_cut_points(
                contour,
                tool_diameter=tool_diameter,
                compensate_external=compensate_external,
                cut_side=cut_side,
                current_xy=current_xy,
                ramp_length=ramp_length,
                smart_entry=smart_entry,
                climb=climb,
            )
            candidates.append((prepared, profile_index, contour))
        _prepared_contour, profile_index, original_contour = min(
            candidates,
            key=lambda item: distance(item[0][0], current_xy),
        )
        contour_moves = build_external_cut_moves(
            original_contour,
            final_depth,
            stepdown,
            ramp_length,
            safe_height,
            tool_diameter=tool_diameter,
            compensate_external=compensate_external,
            cut_side=cut_side,
            material_thickness=material_thickness,
            current_xy=current_xy,
            smart_entry=smart_entry,
            start_depth=start_depth,
            corner_slowdown_enabled=corner_slowdown_enabled,
            corner_angle_threshold=corner_angle_threshold,
            corner_feed_percent=corner_feed_percent,
            corner_slowdown_distance=corner_slowdown_distance,
            climb=climb,
            tabs_enabled=tabs_enabled,
            tab_length=tab_length,
            tab_thickness=tab_thickness,
            tab_count=tab_count,
            tab_positions=tab_positions_by_profile.get(profile_index, ()),
            tab_best_fixation=tab_best_fixation,
            tabs_3d=tabs_3d,
            ramp_type=ramp_type,
            pass_depths=pass_depths,
            profile_id="%s-%04d" % (profile_id_prefix, profile_index + 1),
            tab_surface_clearance=tab_surface_clearance,
        )
        moves.extend(contour_moves)
        remaining.remove((profile_index, original_contour))

        for move in reversed(contour_moves):
            if move.get("x") is not None and move.get("y") is not None:
                current_xy = (move["x"], move["y"])
                break

    return moves


def build_profile_cut_moves(
    path_points,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    tool_diameter=0.0,
    cut_side=CUT_SIDE_OUTSIDE,
    material_thickness=None,
    current_xy=(0.0, 0.0),
    smart_entry=True,
    start_depth=0.0,
    corner_slowdown_enabled=False,
    corner_angle_threshold=45.0,
    corner_feed_percent=40.0,
    corner_slowdown_distance=8.0,
    climb=None,
    tabs_enabled=False,
    tab_length=12.0,
    tab_thickness=3.0,
    tab_count=4,
    tab_positions=None,
    tabs_3d=False,
    ramp_type="smooth",
    pass_depths=None,
    tab_best_fixation=False,
    profile_id="profile-0001",
    tab_surface_clearance=0.2,
):
    return build_external_cut_moves(
        path_points,
        final_depth,
        stepdown,
        ramp_length,
        safe_height,
        tool_diameter=tool_diameter,
        compensate_external=cut_side == CUT_SIDE_OUTSIDE,
        material_thickness=material_thickness,
        current_xy=current_xy,
        smart_entry=smart_entry,
        cut_side=cut_side,
        start_depth=start_depth,
        corner_slowdown_enabled=corner_slowdown_enabled,
        corner_angle_threshold=corner_angle_threshold,
        corner_feed_percent=corner_feed_percent,
        corner_slowdown_distance=corner_slowdown_distance,
        climb=climb,
        tabs_enabled=tabs_enabled,
        tab_length=tab_length,
        tab_thickness=tab_thickness,
        tab_count=tab_count,
        tab_positions=tab_positions,
        tab_best_fixation=tab_best_fixation,
        tabs_3d=tabs_3d,
        ramp_type=ramp_type,
        pass_depths=pass_depths,
        profile_id=profile_id,
        tab_surface_clearance=tab_surface_clearance,
    )


def build_profile_cut_job(
    contours,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    tool_diameter=0.0,
    cut_side=CUT_SIDE_OUTSIDE,
    material_thickness=None,
    smart_entry=True,
    start_xy=(0.0, 0.0),
    start_depth=0.0,
    corner_slowdown_enabled=False,
    corner_angle_threshold=45.0,
    corner_feed_percent=40.0,
    corner_slowdown_distance=8.0,
    climb=None,
    tabs_enabled=False,
    tab_length=12.0,
    tab_thickness=3.0,
    tab_count=4,
    tab_positions=None,
    tabs_3d=False,
    ramp_type="smooth",
    pass_depths=None,
    tab_best_fixation=False,
    profile_id_prefix="profile",
    tab_surface_clearance=0.2,
):
    return build_external_cut_job(
        contours,
        final_depth,
        stepdown,
        ramp_length,
        safe_height,
        tool_diameter=tool_diameter,
        compensate_external=cut_side == CUT_SIDE_OUTSIDE,
        material_thickness=material_thickness,
        smart_entry=smart_entry,
        cut_side=cut_side,
        start_xy=start_xy,
        start_depth=start_depth,
        corner_slowdown_enabled=corner_slowdown_enabled,
        corner_angle_threshold=corner_angle_threshold,
        corner_feed_percent=corner_feed_percent,
        corner_slowdown_distance=corner_slowdown_distance,
        climb=climb,
        tabs_enabled=tabs_enabled,
        tab_length=tab_length,
        tab_thickness=tab_thickness,
        tab_count=tab_count,
        tab_positions=tab_positions,
        tab_best_fixation=tab_best_fixation,
        tabs_3d=tabs_3d,
        ramp_type=ramp_type,
        pass_depths=pass_depths,
        profile_id_prefix=profile_id_prefix,
        tab_surface_clearance=tab_surface_clearance,
    )


def build_machining_job(
    contours,
    holes,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    tool_diameter=0.0,
    cut_side=CUT_SIDE_OUTSIDE,
    material_thickness=None,
    smart_entry=True,
    drill_holes=True,
    use_helical_drilling=True,
    helix_pitch=1.0,
    roughing_enabled=False,
    roughing_allowance=1.0,
    cut_enabled=True,
    start_xy=(0.0, 0.0),
    return_to_start=True,
    start_depth=0.0,
    helix_stepover_ratio=0.40,
    use_model_depths=True,
    hole_depth_override=None,
    peck_enabled=False,
    peck_step=None,
    retract_mode="surface",
    retract_clearance=0.0,
    dwell_seconds=0.0,
    preserve_hole_order=False,
):
    """Monta o trabalho completo na ordem: furos, desbaste e acabamento."""
    stages = build_machining_stages(
        contours,
        holes,
        final_depth,
        stepdown,
        ramp_length,
        safe_height,
        tool_diameter=tool_diameter,
        cut_side=cut_side,
        material_thickness=material_thickness,
        smart_entry=smart_entry,
        drill_holes=drill_holes,
        use_helical_drilling=use_helical_drilling,
        helix_pitch=helix_pitch,
        roughing_enabled=roughing_enabled,
        roughing_allowance=roughing_allowance,
        cut_enabled=cut_enabled,
        start_xy=start_xy,
        return_to_start=return_to_start,
        start_depth=start_depth,
        helix_stepover_ratio=helix_stepover_ratio,
        use_model_depths=use_model_depths,
        hole_depth_override=hole_depth_override,
        peck_enabled=peck_enabled,
        peck_step=peck_step,
        retract_mode=retract_mode,
        retract_clearance=retract_clearance,
        dwell_seconds=dwell_seconds,
        preserve_hole_order=preserve_hole_order,
    )
    return [
        move
        for stage_moves in stages.values()
        for move in stage_moves
    ]


def _with_configured_start(moves, start_xy, safe_height, return_to_start):
    if not moves:
        return []

    start_x = float(start_xy[0])
    start_y = float(start_xy[1])
    wrapped = [
        {
            "type": "rapid",
            "x": start_x,
            "y": start_y,
            "z": safe_height,
        }
    ]
    wrapped.extend(moves)
    if return_to_start:
        wrapped.append({"type": "rapid", "x": None, "y": None, "z": safe_height})
        wrapped.append(
            {
                "type": "rapid",
                "x": start_x,
                "y": start_y,
                "z": safe_height,
            }
        )
    return wrapped


def _last_xy_from_moves(moves, fallback=(0.0, 0.0)):
    for move in reversed(moves):
        if move.get("x") is not None and move.get("y") is not None:
            return float(move["x"]), float(move["y"])
    return fallback


def _rotate_xy(point, angle_radians):
    cosine = math.cos(angle_radians)
    sine = math.sin(angle_radians)
    return (
        point[0] * cosine - point[1] * sine,
        point[0] * sine + point[1] * cosine,
    )


def _offset_pocket_boundaries(outer, islands, clearance):
    outer_centerline = offset_closed_polygon(outer, -abs(float(clearance)))
    if (
        abs(polygon_area(outer_centerline)) <= 1e-7
        or abs(polygon_area(outer_centerline)) >= abs(polygon_area(outer)) - 1e-7
    ):
        raise ValueError(
            "A área do rebaixo é pequena demais para a ferramenta e o sobremetal."
        )

    island_centerlines = []
    for island in islands:
        try:
            expanded = offset_closed_polygon(island, abs(float(clearance)))
        except ValueError:
            continue
        if abs(polygon_area(expanded)) > abs(polygon_area(island)) + 1e-7:
            island_centerlines.append(expanded)
    return outer_centerline, island_centerlines


def _scanline_intersections(rings, y_value):
    intersections = []
    for ring in rings:
        for index, start in enumerate(ring):
            end = ring[(index + 1) % len(ring)]
            if abs(end[1] - start[1]) <= 1e-9:
                continue
            crosses = (
                start[1] <= y_value < end[1]
                or end[1] <= y_value < start[1]
            )
            if not crosses:
                continue
            ratio = (y_value - start[1]) / (end[1] - start[1])
            intersections.append(start[0] + (end[0] - start[0]) * ratio)
    intersections.sort()
    return intersections


def _raster_pocket_paths(outer, islands, stepover, angle_degrees):
    angle = math.radians(float(angle_degrees))
    rotated_rings = [
        [_rotate_xy(point, -angle) for point in ring]
        for ring in [outer] + list(islands)
    ]
    outer_rotated = rotated_rings[0]
    min_y = min(point[1] for point in outer_rotated)
    max_y = max(point[1] for point in outer_rotated)
    height = max_y - min_y
    if height <= 1e-7:
        return []

    spacing = max(float(stepover), 1e-6)
    if height <= spacing:
        rows = [(min_y + max_y) * 0.5]
    else:
        rows = []
        y_value = min_y + spacing * 0.5
        while y_value < max_y - 1e-7:
            rows.append(y_value)
            y_value += spacing
        final_row = max_y - spacing * 0.5
        if rows and final_row - rows[-1] > spacing * 0.25:
            rows.append(final_row)

    paths = []
    reverse = False
    for y_value in rows:
        intersections = _scanline_intersections(rotated_rings, y_value)
        row_segments = []
        for index in range(0, len(intersections) - 1, 2):
            start_x = intersections[index]
            end_x = intersections[index + 1]
            if end_x - start_x <= 1e-7:
                continue
            start = _rotate_xy((start_x, y_value), angle)
            end = _rotate_xy((end_x, y_value), angle)
            row_segments.append([start, end])
        if reverse:
            row_segments = [list(reversed(segment)) for segment in reversed(row_segments)]
        paths.extend(row_segments)
        reverse = not reverse
    return paths


def _offset_pocket_paths(outer, clearance, stepover, clockwise=False):
    paths = []
    previous_area = None
    base_orientation = 1.0 if polygon_area(outer) >= 0.0 else -1.0

    for pass_index in range(10000):
        offset_distance = abs(float(clearance)) + pass_index * float(stepover)
        try:
            candidate = offset_closed_polygon(outer, -offset_distance)
        except ValueError:
            break
        candidate_area = polygon_area(candidate)
        absolute_area = abs(candidate_area)
        if absolute_area <= 1e-7 or candidate_area * base_orientation <= 0.0:
            break
        if previous_area is not None and absolute_area >= previous_area - 1e-7:
            break
        xs = [point[0] for point in candidate]
        ys = [point[1] for point in candidate]
        if max(xs) - min(xs) <= 1e-7 or max(ys) - min(ys) <= 1e-7:
            break

        is_clockwise = candidate_area < 0.0
        if is_clockwise != bool(clockwise):
            candidate = list(reversed(candidate))
        paths.append(candidate)
        previous_area = absolute_area

    return paths


def _append_pocket_path(
    moves,
    path,
    target_depth,
    previous_depth,
    safe_height,
    ramp_length,
    closed,
):
    if len(path) < 2:
        return
    start = path[0]
    moves.append(
        {
            "type": "rapid",
            "x": float(start[0]),
            "y": float(start[1]),
            "z": float(safe_height),
        }
    )
    moves.append(
        {
            "type": "feed_plunge",
            "x": float(start[0]),
            "y": float(start[1]),
            "z": -abs(float(previous_depth)),
        }
    )

    if closed:
        full_path = _closed_path(path)
        total_length = _path_length(full_path)
        effective_ramp = min(max(float(ramp_length), 0.0), total_length * 0.5)
        cursor_distance = 0.0
        if effective_ramp > 1e-7:
            for (x_value, y_value), progress in _path_points_with_progress(
                full_path,
                0.0,
                effective_ramp,
                total_length,
            ):
                ratio = progress / effective_ramp
                z_value = -abs(float(previous_depth)) + (
                    -abs(float(target_depth)) + abs(float(previous_depth))
                ) * ratio
                moves.append(
                    {
                        "type": "feed_ramp",
                        "x": x_value,
                        "y": y_value,
                        "z": z_value,
                    }
                )
            cursor_distance = effective_ramp
        else:
            moves.append(
                {
                    "type": "feed_plunge",
                    "x": float(start[0]),
                    "y": float(start[1]),
                    "z": -abs(float(target_depth)),
                }
            )
        for x_value, y_value in _full_loop_points_from(
            full_path,
            cursor_distance,
            total_length,
        ):
            moves.append(
                {
                    "type": "feed_cut",
                    "x": x_value,
                    "y": y_value,
                    "z": -abs(float(target_depth)),
                }
            )
    else:
        end = path[-1]
        segment_length = distance(start, end)
        effective_ramp = min(
            max(float(ramp_length), 0.0),
            segment_length * 0.5,
        )
        if effective_ramp > 1e-7:
            ratio = effective_ramp / segment_length
            ramp_end = (
                start[0] + (end[0] - start[0]) * ratio,
                start[1] + (end[1] - start[1]) * ratio,
            )
            moves.append(
                {
                    "type": "feed_ramp",
                    "x": ramp_end[0],
                    "y": ramp_end[1],
                    "z": -abs(float(target_depth)),
                }
            )
            moves.append(
                {
                    "type": "feed_cut",
                    "x": float(end[0]),
                    "y": float(end[1]),
                    "z": -abs(float(target_depth)),
                }
            )
            moves.append(
                {
                    "type": "feed_cut",
                    "x": float(start[0]),
                    "y": float(start[1]),
                    "z": -abs(float(target_depth)),
                }
            )
        else:
            moves.append(
                {
                    "type": "feed_plunge",
                    "x": float(start[0]),
                    "y": float(start[1]),
                    "z": -abs(float(target_depth)),
                }
            )
            moves.append(
                {
                    "type": "feed_cut",
                    "x": float(end[0]),
                    "y": float(end[1]),
                    "z": -abs(float(target_depth)),
                }
            )
    moves.append({"type": "rapid", "x": None, "y": None, "z": safe_height})


def _point_on_ring_boundary(point, ring, tolerance=1e-6):
    for index, segment_start in enumerate(ring):
        segment_end = ring[(index + 1) % len(ring)]
        dx = segment_end[0] - segment_start[0]
        dy = segment_end[1] - segment_start[1]
        length_squared = dx * dx + dy * dy
        if length_squared <= 1e-12:
            continue
        projection = (
            (point[0] - segment_start[0]) * dx
            + (point[1] - segment_start[1]) * dy
        ) / length_squared
        if not -tolerance <= projection <= 1.0 + tolerance:
            continue
        projected = (
            segment_start[0] + max(0.0, min(1.0, projection)) * dx,
            segment_start[1] + max(0.0, min(1.0, projection)) * dy,
        )
        if distance(point, projected) <= tolerance:
            return True
    return False


def _segment_stays_inside_pocket(start, end, outer, islands):
    for sample_index in range(1, 12):
        ratio = sample_index / 12.0
        point = (
            start[0] + (end[0] - start[0]) * ratio,
            start[1] + (end[1] - start[1]) * ratio,
        )
        if not (
            _point_in_polygon(point, outer)
            or _point_on_ring_boundary(point, outer)
        ):
            return False
        if any(
            _point_in_polygon(point, island)
            or _point_on_ring_boundary(point, island)
            for island in islands
        ):
            return False
    return True


def _append_pocket_entry_and_path(
    moves,
    path,
    target_depth,
    previous_depth,
    safe_height,
    ramp_length,
    closed,
):
    start = path[0]
    moves.append(
        {
            "type": "rapid",
            "x": float(start[0]),
            "y": float(start[1]),
            "z": float(safe_height),
        }
    )
    moves.append(
        {
            "type": "feed_plunge",
            "x": float(start[0]),
            "y": float(start[1]),
            "z": -abs(float(previous_depth)),
        }
    )

    if closed:
        full_path = _closed_path(path)
        total_length = _path_length(full_path)
        effective_ramp = min(max(float(ramp_length), 0.0), total_length * 0.5)
        cursor_distance = 0.0
        if effective_ramp > 1e-7:
            for (x_value, y_value), progress in _path_points_with_progress(
                full_path,
                0.0,
                effective_ramp,
                total_length,
            ):
                ratio = progress / effective_ramp
                z_value = -abs(float(previous_depth)) + (
                    -abs(float(target_depth)) + abs(float(previous_depth))
                ) * ratio
                moves.append(
                    {
                        "type": "feed_ramp",
                        "x": x_value,
                        "y": y_value,
                        "z": z_value,
                    }
                )
            cursor_distance = effective_ramp
        else:
            moves.append(
                {
                    "type": "feed_plunge",
                    "x": float(start[0]),
                    "y": float(start[1]),
                    "z": -abs(float(target_depth)),
                }
            )
        loop_targets = _full_loop_points_from(
            full_path,
            cursor_distance,
            total_length,
        )
        for x_value, y_value in loop_targets:
            moves.append(
                {
                    "type": "feed_cut",
                    "x": x_value,
                    "y": y_value,
                    "z": -abs(float(target_depth)),
                }
            )
        return (
            loop_targets[-1]
            if loop_targets
            else _point_at_distance(full_path, cursor_distance, total_length)
        )

    end = path[-1]
    segment_length = distance(start, end)
    effective_ramp = min(max(float(ramp_length), 0.0), segment_length * 0.5)
    if effective_ramp > 1e-7:
        ratio = effective_ramp / segment_length
        ramp_end = (
            start[0] + (end[0] - start[0]) * ratio,
            start[1] + (end[1] - start[1]) * ratio,
        )
        moves.append(
            {
                "type": "feed_ramp",
                "x": ramp_end[0],
                "y": ramp_end[1],
                "z": -abs(float(target_depth)),
            }
        )
        moves.append(
            {
                "type": "feed_cut",
                "x": float(start[0]),
                "y": float(start[1]),
                "z": -abs(float(target_depth)),
            }
        )
    else:
        moves.append(
            {
                "type": "feed_plunge",
                "x": float(start[0]),
                "y": float(start[1]),
                "z": -abs(float(target_depth)),
            }
        )
    moves.append(
        {
            "type": "feed_cut",
            "x": float(end[0]),
            "y": float(end[1]),
            "z": -abs(float(target_depth)),
        }
    )
    return end


def _append_pocket_path_at_depth(moves, path, target_depth, closed):
    start = path[0]
    moves.append(
        {
            "type": "feed_cut",
            "x": float(start[0]),
            "y": float(start[1]),
            "z": -abs(float(target_depth)),
            "pocket_link": True,
        }
    )
    if closed:
        full_path = _closed_path(path)
        targets = _full_loop_points_from(
            full_path,
            0.0,
            _path_length(full_path),
        )
        for x_value, y_value in targets:
            moves.append(
                {
                    "type": "feed_cut",
                    "x": x_value,
                    "y": y_value,
                    "z": -abs(float(target_depth)),
                }
            )
        return targets[-1] if targets else start

    end = path[-1]
    moves.append(
        {
            "type": "feed_cut",
            "x": float(end[0]),
            "y": float(end[1]),
            "z": -abs(float(target_depth)),
        }
    )
    return end


def _append_continuous_pocket_paths(
    moves,
    path_specs,
    target_depth,
    previous_depth,
    safe_height,
    ramp_length,
    outer,
    islands,
):
    current_xy = None
    tool_is_down = False

    for path, closed in path_specs:
        if len(path) < 2:
            continue
        start = path[0]
        can_link = (
            tool_is_down
            and current_xy is not None
            and _segment_stays_inside_pocket(
                current_xy,
                start,
                outer,
                islands,
            )
        )
        if can_link:
            current_xy = _append_pocket_path_at_depth(
                moves,
                path,
                target_depth,
                closed,
            )
            continue

        if tool_is_down:
            moves.append(
                {
                    "type": "rapid",
                    "x": None,
                    "y": None,
                    "z": safe_height,
                }
            )
        current_xy = _append_pocket_entry_and_path(
            moves,
            path,
            target_depth,
            previous_depth,
            safe_height,
            ramp_length,
            closed,
        )
        tool_is_down = True

    if tool_is_down:
        moves.append(
            {
                "type": "rapid",
                "x": None,
                "y": None,
                "z": safe_height,
            }
        )


def build_pocket_stage(
    outer_contours,
    inner_contours,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    tool_diameter,
    stepover_percent=40.0,
    strategy="offset",
    allowance=0.0,
    raster_angle=0.0,
    climb=None,
    profile_pass=True,
    material_thickness=None,
    start_xy=(0.0, 0.0),
    return_to_start=True,
    start_depth=0.0,
):
    """Remove toda a área fechada em camadas, por offset ou raster."""
    cutter_diameter = abs(float(tool_diameter))
    if cutter_diameter <= 0.0:
        raise ValueError("O diâmetro da ferramenta do rebaixo deve ser maior que zero.")
    stepover = cutter_diameter * float(stepover_percent) / 100.0
    clearance = cutter_diameter * 0.5 + max(0.0, float(allowance))
    depth_steps = generate_depth_steps(
        final_depth,
        stepdown,
        material_thickness=material_thickness,
        start_depth=start_depth,
    )
    if not depth_steps:
        raise ValueError("A profundidade final do rebaixo deve ser maior que zero.")

    pocket_areas = []
    for outer in outer_contours or []:
        clean_outer = _clean_closed_points(outer)
        islands = []
        seen_islands = set()
        for inner in inner_contours or []:
            if not inner or not _point_in_polygon(inner[0], clean_outer):
                continue
            clean_inner = _clean_closed_points(inner)
            signature = tuple(
                sorted(
                    (round(point[0], 5), round(point[1], 5))
                    for point in clean_inner
                )
            )
            if signature in seen_islands:
                continue
            seen_islands.add(signature)
            islands.append(clean_inner)
        outer_centerline, island_centerlines = _offset_pocket_boundaries(
            clean_outer,
            islands,
            clearance,
        )
        selected_strategy = str(strategy).strip().lower()
        if selected_strategy == "offset" and not island_centerlines:
            paths = _offset_pocket_paths(
                clean_outer,
                clearance,
                stepover,
                clockwise=not bool(climb),
            )
            closed_paths = True
        else:
            paths = _raster_pocket_paths(
                outer_centerline,
                island_centerlines,
                stepover,
                raster_angle,
            )
            closed_paths = False
        if not paths:
            raise ValueError(
                "Não foi possível criar passadas dentro da área selecionada."
            )
        pocket_areas.append(
            {
                "paths": paths,
                "closed": closed_paths,
                "profile_outer": outer_centerline,
                "profile_islands": island_centerlines,
                "allowed_outer": outer_centerline,
                "allowed_islands": island_centerlines,
            }
        )

    ordered_areas = []
    remaining_areas = list(pocket_areas)
    ordering_position = (float(start_xy[0]), float(start_xy[1]))
    while remaining_areas:
        area = min(
            remaining_areas,
            key=lambda candidate: distance(
                ordering_position,
                candidate["paths"][0][0],
            ),
        )
        ordered_areas.append(area)
        last_path = area["paths"][-1]
        ordering_position = (
            last_path[0]
            if area["closed"]
            else last_path[-1]
        )
        remaining_areas.remove(area)

    moves = []
    for area in ordered_areas:
        for depth_index, depth in enumerate(depth_steps):
            previous_depth = (
                abs(float(start_depth))
                if depth_index == 0
                else depth_steps[depth_index - 1]
            )
            path_specs = [
                (path, area["closed"])
                for path in area["paths"]
            ]
            if profile_pass and depth_index == len(depth_steps) - 1:
                profile_paths = [area["profile_outer"]] + area["profile_islands"]
                for profile_index, profile in enumerate(profile_paths):
                    desired_clockwise = (not bool(climb)) if profile_index == 0 else bool(climb)
                    is_clockwise = polygon_area(profile) < 0.0
                    if is_clockwise != desired_clockwise:
                        profile = list(reversed(profile))
                    path_specs.append((profile, True))
            _append_continuous_pocket_paths(
                moves,
                path_specs,
                depth,
                previous_depth,
                safe_height,
                ramp_length,
                area["allowed_outer"],
                area["allowed_islands"],
            )

    return _with_configured_start(
        moves,
        start_xy,
        safe_height,
        return_to_start,
    )


def build_contour_cut_stage(
    outer_contours,
    inner_contours,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    tool_diameter=0.0,
    outer_cut_side=CUT_SIDE_OUTSIDE,
    material_thickness=None,
    smart_entry=True,
    start_xy=(0.0, 0.0),
    return_to_start=True,
    inner_holes=None,
    start_depth=0.0,
    corner_slowdown_enabled=False,
    corner_angle_threshold=45.0,
    corner_feed_percent=40.0,
    corner_slowdown_distance=8.0,
    climb=None,
    tabs_enabled=False,
    tab_length=12.0,
    tab_thickness=3.0,
    tab_count=4,
    tab_positions=None,
    tabs_3d=False,
    ramp_type="smooth",
    pass_depths=None,
    tab_best_fixation=False,
    tab_surface_clearance=0.2,
):
    """Corta internos primeiro e usa furação central quando a fresa não cabe."""
    moves = []
    current_xy = start_xy
    profile_inner_contours = list(inner_contours or [])
    fallback_holes = []

    for hole in inner_holes or []:
        if float(hole.get("diameter_mm", 0.0) or 0.0) > float(tool_diameter) + 1e-6:
            points = hole.get("points")
            if points:
                profile_inner_contours.append(points)
        else:
            fallback_holes.append(hole)

    if fallback_holes:
        fallback_moves = build_drill_moves(
            fallback_holes,
            final_depth,
            stepdown,
            safe_height,
            tool_diameter=tool_diameter,
            material_thickness=material_thickness,
            start_xy=current_xy,
            use_helical=False,
            start_depth=start_depth,
        )
        moves.extend(fallback_moves)
        current_xy = _last_xy_from_moves(fallback_moves, current_xy)

    if profile_inner_contours:
        inner_moves = build_profile_cut_job(
            profile_inner_contours,
            final_depth,
            stepdown,
            ramp_length,
            safe_height,
            tool_diameter=tool_diameter,
            cut_side=CUT_SIDE_INSIDE,
            material_thickness=material_thickness,
            smart_entry=smart_entry,
            start_xy=current_xy,
            start_depth=start_depth,
            corner_slowdown_enabled=corner_slowdown_enabled,
            corner_angle_threshold=corner_angle_threshold,
            corner_feed_percent=corner_feed_percent,
            corner_slowdown_distance=corner_slowdown_distance,
            climb=climb,
            ramp_type=ramp_type,
            pass_depths=pass_depths,
            profile_id_prefix="internal",
        )
        moves.extend(inner_moves)
        current_xy = _last_xy_from_moves(inner_moves, current_xy)

    if outer_contours:
        outer_moves = build_profile_cut_job(
            outer_contours,
            final_depth,
            stepdown,
            ramp_length,
            safe_height,
            tool_diameter=tool_diameter,
            cut_side=outer_cut_side,
            material_thickness=material_thickness,
            smart_entry=smart_entry,
            start_xy=current_xy,
            start_depth=start_depth,
            corner_slowdown_enabled=corner_slowdown_enabled,
            corner_angle_threshold=corner_angle_threshold,
            corner_feed_percent=corner_feed_percent,
            corner_slowdown_distance=corner_slowdown_distance,
            climb=climb,
            tabs_enabled=tabs_enabled,
            tab_length=tab_length,
            tab_thickness=tab_thickness,
            tab_count=tab_count,
            tab_positions=tab_positions,
            tab_best_fixation=tab_best_fixation,
            tabs_3d=tabs_3d,
            ramp_type=ramp_type,
            pass_depths=pass_depths,
            profile_id_prefix="external",
            tab_surface_clearance=tab_surface_clearance,
        )
        moves.extend(outer_moves)

    validate_profile_cut_moves(moves)

    return _with_configured_start(
        moves,
        start_xy,
        safe_height,
        return_to_start,
    )


def build_machining_stages(
    contours,
    holes,
    final_depth,
    stepdown,
    ramp_length,
    safe_height,
    tool_diameter=0.0,
    cut_side=CUT_SIDE_OUTSIDE,
    material_thickness=None,
    smart_entry=True,
    drill_holes=True,
    use_helical_drilling=True,
    helix_pitch=1.0,
    roughing_enabled=False,
    roughing_allowance=1.0,
    cut_enabled=True,
    start_xy=(0.0, 0.0),
    return_to_start=True,
    start_depth=0.0,
    helix_stepover_ratio=0.40,
    use_model_depths=True,
    hole_depth_override=None,
    peck_enabled=False,
    peck_step=None,
    retract_mode="surface",
    retract_clearance=0.0,
    dwell_seconds=0.0,
    preserve_hole_order=False,
    counterbore_enabled=False,
    counterbore_diameter=0.0,
    counterbore_depth=0.0,
    tool_type="end_mill",
):
    """Retorna movimentos separados por etapa, sempre partindo do ponto configurado."""
    stages = {}

    if drill_holes and holes:
        hole_moves = build_drill_moves(
            holes,
            final_depth,
            stepdown,
            safe_height,
            tool_diameter=tool_diameter,
            material_thickness=material_thickness,
            start_xy=start_xy,
            use_helical=use_helical_drilling,
            helix_pitch=helix_pitch,
            start_depth=start_depth,
            helix_stepover_ratio=helix_stepover_ratio,
            use_model_depths=use_model_depths,
            hole_depth_override=hole_depth_override,
            peck_enabled=peck_enabled,
            peck_step=peck_step,
            retract_mode=retract_mode,
            retract_clearance=retract_clearance,
            dwell_seconds=dwell_seconds,
            preserve_order=preserve_hole_order,
            counterbore_enabled=counterbore_enabled,
            counterbore_diameter=counterbore_diameter,
            counterbore_depth=counterbore_depth,
            tool_type=tool_type,
        )
        stages["holes"] = _with_configured_start(
            hole_moves,
            start_xy,
            safe_height,
            return_to_start,
        )

    if roughing_enabled and contours:
        allowance = max(0.0, float(roughing_allowance))
        roughing_side = cut_side if cut_side != CUT_SIDE_ON_LINE else CUT_SIDE_OUTSIDE
        roughing_moves = build_profile_cut_job(
            contours,
            final_depth,
            stepdown,
            ramp_length,
            safe_height,
            tool_diameter=float(tool_diameter) + allowance * 2.0,
            cut_side=roughing_side,
            material_thickness=material_thickness,
            smart_entry=smart_entry,
            start_xy=start_xy,
            start_depth=start_depth,
        )
        stages["roughing"] = _with_configured_start(
            roughing_moves,
            start_xy,
            safe_height,
            return_to_start,
        )

    if cut_enabled and contours:
        cut_moves = build_profile_cut_job(
            contours,
            final_depth,
            stepdown,
            ramp_length,
            safe_height,
            tool_diameter=tool_diameter,
            cut_side=cut_side,
            material_thickness=material_thickness,
            smart_entry=smart_entry,
            start_xy=start_xy,
            start_depth=start_depth,
        )
        stages["cut"] = _with_configured_start(
            cut_moves,
            start_xy,
            safe_height,
            return_to_start,
        )

    return stages
