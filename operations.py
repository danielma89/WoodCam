import math


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


def compensated_polygon(points, tool_diameter, cut_side):
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


def _vertex_distances(path):
    distances = [0.0]
    accumulated = 0.0
    for idx in range(len(path) - 1):
        accumulated += distance(path[idx], path[idx + 1])
        distances.append(accumulated)
    return distances


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
    """Divide segmentos ao redor de vértices para desacelerar só perto do canto."""
    current_xy = (float(start_xy[0]), float(start_xy[1]))
    contour = list(contour_points)
    vertex_indexes = {
        (round(float(point[0]), 6), round(float(point[1]), 6)): index
        for index, point in enumerate(contour)
    }

    for target in targets:
        target_xy = (float(target[0]), float(target[1]))
        vertex_index = vertex_indexes.get(
            (round(target_xy[0], 6), round(target_xy[1], 6))
        )
        is_slow_corner = False
        following = None
        if enabled and vertex_index is not None and len(contour) >= 3:
            previous = contour[vertex_index - 1]
            following = contour[(vertex_index + 1) % len(contour)]
            direction_change = _direction_change_degrees(
                previous,
                target_xy,
                following,
            )
            is_slow_corner = direction_change + 1e-7 >= float(angle_threshold)

        if not is_slow_corner:
            moves.append(
                {
                    "type": "feed_cut",
                    "x": target_xy[0],
                    "y": target_xy[1],
                    "z": z_target,
                }
            )
            current_xy = target_xy
            continue

        incoming_length = distance(current_xy, target_xy)
        approach_distance = min(
            float(slowdown_distance),
            incoming_length * 0.40,
        )
        if incoming_length > 1e-9 and approach_distance > 1e-9:
            approach_ratio = (incoming_length - approach_distance) / incoming_length
            approach = (
                current_xy[0] + (target_xy[0] - current_xy[0]) * approach_ratio,
                current_xy[1] + (target_xy[1] - current_xy[1]) * approach_ratio,
            )
            if distance(current_xy, approach) > 1e-7:
                moves.append(
                    {
                        "type": "feed_cut",
                        "x": approach[0],
                        "y": approach[1],
                        "z": z_target,
                    }
                )

        moves.append(
            {
                "type": "feed_cut",
                "x": target_xy[0],
                "y": target_xy[1],
                "z": z_target,
                "feed_scale": float(feed_scale),
                "corner_slowdown": True,
            }
        )

        outgoing_length = distance(target_xy, following)
        exit_distance = min(
            float(slowdown_distance),
            outgoing_length * 0.40,
        )
        if outgoing_length > 1e-9 and exit_distance > 1e-9:
            exit_ratio = exit_distance / outgoing_length
            exit_point = (
                target_xy[0] + (following[0] - target_xy[0]) * exit_ratio,
                target_xy[1] + (following[1] - target_xy[1]) * exit_ratio,
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
            current_xy = exit_point
        else:
            current_xy = target_xy


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

        step_value = float(peck_step or stepdown) if peck_enabled else float(stepdown)
        depth_steps = generate_depth_steps(
            target_depth,
            step_value,
            material_thickness=min(material_depth, target_depth),
            start_depth=initial_depth,
        )
        if not depth_steps:
            raise ValueError("A profundidade do furo deve ser maior que zero.")

        hole_diameter = abs(float(hole.get("diameter_mm", 0.0) or 0.0))
        cutter_diameter = abs(float(tool_diameter))
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
    tab_ranges = (
        _tab_ranges(
            total_contour_length,
            tab_length,
            tab_count,
            _tab_position_ratios_for_path(
                full_path,
                tab_positions,
                total_contour_length,
            ),
        )
        if tabs_enabled
        else []
    )
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
        tab_z = -abs(
            max(
                float(start_depth),
                float(depth) - max(0.0, float(tab_thickness)),
            )
        )
        tabs_on_this_pass = pass_idx == len(depth_steps) - 1
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
                if tabs_3d and is_tab_segment:
                    for tab_start, tab_end in tab_ranges:
                        if tab_start - 1e-7 <= target_distance <= tab_end + 1e-7:
                            ratio = (target_distance - tab_start) / (tab_end - tab_start)
                            lift_ratio = max(0.0, min(1.0, ratio * 2.0, (1.0 - ratio) * 2.0))
                            target_z = z_target + (tab_z - z_target) * lift_ratio
                            break
                if abs(current_z - target_z) > 1e-7:
                    if not tabs_3d:
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

    moves.append({"type": "rapid", "x": None, "y": None, "z": safe_height})

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
):
    if not contours:
        return []

    moves = []
    remaining = [list(contour) for contour in contours if contour]
    current_xy = start_xy

    while remaining:
        candidates = []
        for contour in remaining:
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
            candidates.append((prepared, contour))
        _prepared_contour, original_contour = min(
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
            tab_positions=tab_positions,
            tabs_3d=tabs_3d,
            ramp_type=ramp_type,
            pass_depths=pass_depths,
        )
        moves.extend(contour_moves)
        remaining.remove(original_contour)

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
        tabs_3d=tabs_3d,
        ramp_type=ramp_type,
        pass_depths=pass_depths,
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
        tabs_3d=tabs_3d,
        ramp_type=ramp_type,
        pass_depths=pass_depths,
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
            tabs_3d=tabs_3d,
            ramp_type=ramp_type,
            pass_depths=pass_depths,
        )
        moves.extend(outer_moves)

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
