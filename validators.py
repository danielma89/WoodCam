import math

from presets import MACHINE_LIMITS


GEOMETRY_EPSILON = 1e-7
CUT_SIDE_OUTSIDE = "outside"
CUT_SIDE_ON_LINE = "on_line"
CUT_SIDE_INSIDE = "inside"
VALID_CUT_SIDES = {
    CUT_SIDE_OUTSIDE,
    CUT_SIDE_ON_LINE,
    CUT_SIDE_INSIDE,
}
VALID_JOB_TYPES = {"single_sided", "double_sided"}
SUPPORTED_JOB_TYPES = {"single_sided"}
VALID_Z_ZERO_MODES = {"material_surface", "machine_bed"}


def _cut_side(settings):
    if not settings:
        return CUT_SIDE_OUTSIDE

    configured_side = settings.get("cut_side")
    if configured_side:
        return str(configured_side)

    # Compatibilidade com arquivos/configurações da primeira versão.
    return CUT_SIDE_OUTSIDE if settings.get("compensate_external", True) else CUT_SIDE_ON_LINE


def _point_distance(a, b):
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _same_point(a, b):
    return _point_distance(a, b) <= GEOMETRY_EPSILON


def _clean_contour_points(points):
    clean_points = []

    for point in points:
        xy = (float(point[0]), float(point[1]))
        if not clean_points or not _same_point(xy, clean_points[-1]):
            clean_points.append(xy)

    if len(clean_points) > 1 and _same_point(clean_points[0], clean_points[-1]):
        clean_points.pop()

    return clean_points


def _closed_segments(points):
    clean_points = _clean_contour_points(points)
    return [
        (clean_points[index], clean_points[(index + 1) % len(clean_points)])
        for index in range(len(clean_points))
    ]


def _orientation(a, b, c):
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _on_segment(a, b, point):
    if abs(_orientation(a, b, point)) > GEOMETRY_EPSILON:
        return False
    return (
        min(a[0], b[0]) - GEOMETRY_EPSILON <= point[0] <= max(a[0], b[0]) + GEOMETRY_EPSILON
        and min(a[1], b[1]) - GEOMETRY_EPSILON <= point[1] <= max(a[1], b[1]) + GEOMETRY_EPSILON
    )


def _segments_intersect(a1, a2, b1, b2):
    oa = _orientation(a1, a2, b1)
    ob = _orientation(a1, a2, b2)
    oc = _orientation(b1, b2, a1)
    od = _orientation(b1, b2, a2)

    if (
        ((oa > GEOMETRY_EPSILON and ob < -GEOMETRY_EPSILON) or (oa < -GEOMETRY_EPSILON and ob > GEOMETRY_EPSILON))
        and ((oc > GEOMETRY_EPSILON and od < -GEOMETRY_EPSILON) or (oc < -GEOMETRY_EPSILON and od > GEOMETRY_EPSILON))
    ):
        return True

    return (
        _on_segment(a1, a2, b1)
        or _on_segment(a1, a2, b2)
        or _on_segment(b1, b2, a1)
        or _on_segment(b1, b2, a2)
    )


def _point_to_segment_distance(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = dx * dx + dy * dy

    if length_squared <= GEOMETRY_EPSILON:
        return _point_distance(point, start)

    projection = ((point[0] - start[0]) * dx + (point[1] - start[1]) * dy) / length_squared
    projection = max(0.0, min(1.0, projection))
    projected = (start[0] + projection * dx, start[1] + projection * dy)
    return _point_distance(point, projected)


def _segment_distance(a1, a2, b1, b2):
    if _segments_intersect(a1, a2, b1, b2):
        return 0.0

    return min(
        _point_to_segment_distance(a1, b1, b2),
        _point_to_segment_distance(a2, b1, b2),
        _point_to_segment_distance(b1, a1, a2),
        _point_to_segment_distance(b2, a1, a2),
    )


def _points_bounds(points):
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def _bounds_distance(first, second):
    dx = max(first[0] - second[2], second[0] - first[2], 0.0)
    dy = max(first[1] - second[3], second[1] - first[3], 0.0)
    return math.hypot(dx, dy)


def _segment_bounds(segment):
    return _points_bounds(segment)


def _near_segment_pairs(first_bounds, second_bounds, maximum_distance):
    """Broad phase for segment distance checks, in deterministic order."""

    maximum_distance = max(0.0, float(maximum_distance))
    events = []
    for index, bounds in enumerate(first_bounds):
        events.append((bounds[0] - maximum_distance, 0, 0, index))
        events.append((bounds[2] + maximum_distance, 1, 0, index))
    for index, bounds in enumerate(second_bounds):
        events.append((bounds[0] - maximum_distance, 0, 1, index))
        events.append((bounds[2] + maximum_distance, 1, 1, index))

    active_first = set()
    active_second = set()
    candidates = set()
    for _x, event_kind, collection, index in sorted(events):
        if event_kind == 1:
            (active_first if collection == 0 else active_second).discard(index)
            continue
        if collection == 0:
            for other_index in active_second:
                if (
                    _bounds_distance(first_bounds[index], second_bounds[other_index])
                    <= maximum_distance + GEOMETRY_EPSILON
                ):
                    candidates.add((index, other_index))
            active_first.add(index)
        else:
            for other_index in active_first:
                if (
                    _bounds_distance(first_bounds[other_index], second_bounds[index])
                    <= maximum_distance + GEOMETRY_EPSILON
                ):
                    candidates.add((other_index, index))
            active_second.add(index)
    return tuple(sorted(candidates))


def _point_in_polygon(point, polygon):
    points = _clean_contour_points(polygon)
    inside = False
    point_count = len(points)

    for index in range(point_count):
        start = points[index]
        end = points[(index + 1) % point_count]

        if _on_segment(start, end, point):
            return True

        crosses_ray = (start[1] > point[1]) != (end[1] > point[1])
        if crosses_ray:
            x_at_y = (end[0] - start[0]) * (point[1] - start[1]) / (end[1] - start[1]) + start[0]
            if point[0] < x_at_y:
                inside = not inside

    return inside


def _contour_distance(first, second, maximum_relevant_distance=None):
    first_points = _clean_contour_points(first)
    second_points = _clean_contour_points(second)
    if _point_in_polygon(first_points[0], second_points) or _point_in_polygon(second_points[0], first_points):
        return 0.0

    first_segments = _closed_segments(first)
    second_segments = _closed_segments(second)
    min_distance = None

    if maximum_relevant_distance is None:
        segment_pairs = (
            (first_index, second_index)
            for first_index in range(len(first_segments))
            for second_index in range(len(second_segments))
        )
    else:
        segment_pairs = _near_segment_pairs(
            tuple(_segment_bounds(segment) for segment in first_segments),
            tuple(_segment_bounds(segment) for segment in second_segments),
            maximum_relevant_distance,
        )

    for first_index, second_index in segment_pairs:
        a1, a2 = first_segments[first_index]
        b1, b2 = second_segments[second_index]
        distance = _segment_distance(a1, a2, b1, b2)
        if min_distance is None or distance < min_distance:
            min_distance = distance
        if min_distance <= GEOMETRY_EPSILON:
            return 0.0

    if min_distance is not None:
        return min_distance
    if maximum_relevant_distance is not None:
        return float(maximum_relevant_distance)
    return 0.0


def validate_external_cut_clearance(contours, settings):
    if not settings or _cut_side(settings) != CUT_SIDE_OUTSIDE:
        return
    if len(contours) < 2:
        return

    common_line_mode = str(
        settings.get("common_line_mode", "preserve_dimensions")
    )
    if (
        settings.get("common_line_enabled")
        and common_line_mode == "on_vector"
        and "common_line_shared_pairs" not in settings
    ):
        # The dedicated common-line planner validates crossings, area overlap
        # and ownership first. It calls this validator again with the exact
        # contour pairs that genuinely share a positive-length boundary.
        return

    tool_diameter = float(settings.get("tool_diameter", 0.0))
    if settings.get("common_line_enabled"):
        tool_diameter += 2.0 * float(
            settings.get("cut_allowance_offset", 0.0) or 0.0
        )
    if tool_diameter <= 0.0:
        return

    required_clearance = tool_diameter
    shared_pairs = {
        tuple(sorted((int(pair[0]), int(pair[1]))))
        for pair in (settings.get("common_line_shared_pairs", ()) or ())
        if len(pair) >= 2
    }
    clean_contours = [_clean_contour_points(contour) for contour in contours]
    contour_bounds = [_points_bounds(contour) for contour in clean_contours]
    for first_index in range(len(contours)):
        for second_index in range(first_index + 1, len(contours)):
            if (first_index, second_index) in shared_pairs:
                continue
            if (
                _bounds_distance(
                    contour_bounds[first_index], contour_bounds[second_index]
                )
                + GEOMETRY_EPSILON
                >= required_clearance
            ):
                continue
            clearance = _contour_distance(
                clean_contours[first_index],
                clean_contours[second_index],
                maximum_relevant_distance=required_clearance,
            )
            if clearance + GEOMETRY_EPSILON < required_clearance:
                if settings.get("common_line_enabled") and common_line_mode == "on_vector":
                    explanation = (
                        "A folga é menor que o diâmetro efetivo e este par não "
                        "forma uma fronteira comum coincidente. O corte foi "
                        "bloqueado para não converter silenciosamente corte "
                        "externo em corte sobre a linha."
                    )
                elif settings.get("common_line_enabled"):
                    explanation = (
                        "Para linha comum preservando medidas, a folga deve "
                        "coincidir com o diâmetro efetivo da fresa."
                    )
                else:
                    explanation = (
                        "Ative linha comum somente para um layout preparado "
                        "para essa estratégia."
                    )
                raise ValueError(
                    "Espaçamento insuficiente entre os contornos "
                    f"{first_index + 1} e {second_index + 1}: {clearance:.2f} mm. "
                    "Para corte externo independente com compensação de fresa, deixe pelo menos "
                    f"{required_clearance:.2f} mm entre as peças. "
                    + explanation
                )


def validate_selected_contour(points, settings=None):
    if not points:
        raise ValueError("Nenhum contorno foi lido do Sketch selecionado.")
    if len(points) < 3:
        raise ValueError("O contorno selecionado precisa ter pelo menos três vértices.")

    clean_points = _clean_contour_points(points)
    if len(clean_points) < 3:
        raise ValueError("O contorno selecionado precisa ter pelo menos três vértices distintos.")

    if settings:
        xs = [point[0] for point in clean_points]
        ys = [point[1] for point in clean_points]
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)

        machine_x_size = float(settings.get("machine_x_size", MACHINE_LIMITS["x_size"]))
        machine_y_size = float(settings.get("machine_y_size", MACHINE_LIMITS["y_size"]))

        if machine_x_size > 0 and width > machine_x_size:
            raise ValueError(
                f"A largura do contorno ({width:.1f} mm) excede a área útil X "
                f"configurada ({machine_x_size:.1f} mm). "
                "Ajuste 'Área útil X' nas configurações da máquina se esse curso estiver incorreto."
            )
        if machine_y_size > 0 and height > machine_y_size:
            raise ValueError(
                f"A altura do contorno ({height:.1f} mm) excede a área útil Y "
                f"configurada ({machine_y_size:.1f} mm). "
                "Ajuste 'Área útil Y' nas configurações da máquina se esse curso estiver incorreto."
            )


def validate_selected_contours(contours, settings=None):
    if not contours:
        raise ValueError("Nenhum contorno foi lido da seleção.")

    for index, points in enumerate(contours, start=1):
        try:
            validate_selected_contour(points, settings)
        except ValueError as error:
            raise ValueError(f"Contorno {index}: {error}")

    if settings and len(contours) > 1:
        all_points = [
            point
            for contour in contours
            for point in _clean_contour_points(contour)
        ]
        total_width = max(point[0] for point in all_points) - min(point[0] for point in all_points)
        total_height = max(point[1] for point in all_points) - min(point[1] for point in all_points)
        machine_x_size = float(settings.get("machine_x_size", MACHINE_LIMITS["x_size"]))
        machine_y_size = float(settings.get("machine_y_size", MACHINE_LIMITS["y_size"]))
        exceeds_x = machine_x_size > 0 and total_width > machine_x_size
        exceeds_y = machine_y_size > 0 and total_height > machine_y_size
        if exceeds_x or exceeds_y:
            raise ValueError(
                "O conjunto selecionado ocupa "
                f"{total_width:.1f} x {total_height:.1f} mm, acima da área útil configurada "
                f"de {machine_x_size:.1f} x {machine_y_size:.1f} mm."
            )

    # Intersections are normally a blocking safety check.  The UI may offer
    # an explicit one-time override after warning the operator; it is never
    # enabled implicitly.
    if not (settings and settings.get("ignore_contour_intersections")):
        validate_external_cut_clearance(contours, settings)


def validate_settings(settings):
    job_type = str(settings.get("job_type", "single_sided"))
    if job_type not in VALID_JOB_TYPES:
        raise ValueError("O tipo de trabalho selecionado é inválido.")
    if job_type not in SUPPORTED_JOB_TYPES:
        raise ValueError(
            "Por enquanto o WoodCAM 2D gera percursos apenas para trabalho de face única."
        )
    z_zero_mode = str(settings.get("z_zero_mode", "material_surface"))
    if z_zero_mode not in VALID_Z_ZERO_MODES:
        raise ValueError("A posição de Z zero selecionada é inválida.")
    cut_side = _cut_side(settings)
    if cut_side not in VALID_CUT_SIDES:
        raise ValueError("O tipo de corte selecionado é inválido.")
    if settings["material_thickness"] <= 0:
        raise ValueError("A espessura do material deve ser maior que zero.")
    if settings["depth_extra"] < 0:
        raise ValueError("A profundidade extra não pode ser negativa.")
    if settings["tool_diameter"] <= 0:
        raise ValueError("O diâmetro da fresa deve ser maior que zero.")
    operation_mode = str(settings.get("operation_mode", ""))
    if operation_mode == "rough3d":
        if str(settings.get("rough3d_strategy")) not in {"z_level", "raster_3d"}:
            raise ValueError("A estratégia de desbaste 3D é inválida.")
        if float(settings.get("rough3d_allowance", 0.0)) < 0.0:
            raise ValueError("A folga/sobremetal do modelo não pode ser negativa.")
        stepover = float(settings.get("rough3d_stepover_percent", 0.0))
        if not 0.1 <= stepover <= 100.0:
            raise ValueError("O passo lateral do desbaste 3D deve ficar entre 0,1% e 100%.")
    if operation_mode == "finish3d":
        if str(settings.get("finish3d_strategy")) not in {"raster", "offset"}:
            raise ValueError("A estratégia de acabamento 3D é inválida.")
        stepover = float(settings.get("finish3d_stepover_percent", 0.0))
        if not 0.1 <= stepover <= 100.0:
            raise ValueError("O passo lateral do acabamento 3D deve ficar entre 0,1% e 100%.")
        if not math.isfinite(float(settings.get("finish3d_raster_angle", 0.0))):
            raise ValueError("O ângulo do raster 3D precisa ser um número finito.")
    if settings.get("operation_mode") == "cut":
        allowance_offset = float(settings.get("cut_allowance_offset", 0.0))
        if settings["tool_diameter"] + allowance_offset * 2.0 <= 1e-6:
            raise ValueError(
                "A compensação dimensional negativa não pode ser maior que "
                "metade do diâmetro da fresa."
            )
        if float(settings.get("cut_last_pass_allowance", 0.0)) < 0.0:
            raise ValueError("O sobre-metal da última passada não pode ser negativo.")
        if settings.get("cut_tabs_enabled", False):
            tab_height = float(settings.get("tab_thickness", 0.0) or 0.0)
            material_height = float(settings["material_thickness"])
            if tab_height < 0.0:
                raise ValueError("A altura intacta da tab não pode ser negativa.")
            if tab_height > material_height + 1.0e-7:
                raise ValueError(
                    "A altura intacta da tab não pode ultrapassar a espessura do material."
                )
        waste_mode = str(settings.get("loose_waste_fixation", "disabled"))
        if waste_mode not in {"disabled", "tabs", "screws"}:
            raise ValueError("A fixação de restos soltos selecionada é inválida.")
        if waste_mode != "disabled" and not settings.get(
            "common_line_enabled", False
        ):
            raise ValueError(
                "A fixação automática de restos exige o plano de Linha comum."
            )
        if waste_mode == "screws":
            for key, label in (
                ("screw_pilot_diameter", "diâmetro piloto"),
                ("screw_pilot_depth", "profundidade piloto"),
                ("screw_head_diameter", "diâmetro da cabeça"),
                ("screw_safety_margin", "margem do parafuso"),
                ("screw_head_height", "altura da cabeça"),
            ):
                if float(settings.get(key, 0.0) or 0.0) < 0.0:
                    raise ValueError("O %s não pode ser negativo." % label)
            if float(settings.get("screw_head_diameter", 0.0) or 0.0) <= 0.0:
                raise ValueError(
                    "O diâmetro da cabeça/arruela do parafuso deve ser positivo."
                )
            pilot_diameter = float(
                settings.get("screw_pilot_diameter", 0.0) or 0.0
            )
            if (
                pilot_diameter > 1.0e-9
                and pilot_diameter
                < float(settings.get("tool_diameter", 0.0)) - 1.0e-7
            ):
                raise ValueError(
                    "O diâmetro piloto não pode ser menor que a fresa atual; "
                    "use 0 para adotar o diâmetro da fresa."
                )
            if str(settings.get("tool_type", "end_mill")) not in {
                "end_mill",
                "compression",
            }:
                raise ValueError(
                    "Os furos piloto exigem fresa de topo/compressão com "
                    "mergulho vertical confirmado."
                )
    if settings["stepdown"] <= 0:
        raise ValueError("O stepdown deve ser maior que zero.")
    if settings["feed_xy"] <= 0:
        raise ValueError("O avanço de corte XY deve ser maior que zero.")
    if settings["feed_z"] <= 0:
        raise ValueError("O avanço de descida Z deve ser maior que zero.")
    if settings["rapid_feed"] <= 0:
        raise ValueError("O avanço rápido deve ser maior que zero.")
    if settings["safe_height"] <= 0 or settings["retract_height"] <= 0:
        raise ValueError("As alturas segura e de retração devem ser maiores que zero.")
    if settings["ramp_length"] < 0:
        raise ValueError("O comprimento da rampa não pode ser negativo.")
    if settings.get("helix_pitch", 1.0) <= 0:
        raise ValueError("A descida por volta da hélice deve ser maior que zero.")
    if settings.get("start_depth", 0.0) < 0:
        raise ValueError("A cota inicial não pode ser negativa.")
    if settings.get("cut_depth", settings.get("final_depth", 0.0)) <= 0:
        raise ValueError("A profundidade de corte deve ser maior que zero.")
    start_depth = float(settings.get("start_depth", 0.0) or 0.0)
    final_depth = float(
        settings.get(
            "final_depth",
            settings.get("cut_depth", 0.0),
        )
        or 0.0
    )
    if final_depth <= start_depth + 1.0e-9:
        raise ValueError(
            "A profundidade final Z deve ser maior que a profundidade inicial."
        )
    if not 1.0 <= settings.get("helix_stepover_percent", 40.0) <= 100.0:
        raise ValueError("O passo lateral da hélice deve ficar entre 1% e 100%.")
    if settings.get("peck_enabled", False) and settings.get("peck_step", 0.0) <= 0:
        raise ValueError("O passo da furação faseada deve ser maior que zero.")
    if settings.get("peck_retract_clearance", 0.0) < 0:
        raise ValueError("A folga de retração não pode ser negativa.")
    if settings.get("dwell_seconds", 0.0) < 0:
        raise ValueError("O tempo de permanência não pode ser negativo.")
    if (
        settings.get("operation_mode") == "holes"
        and settings.get("hole_counterbore_enabled", False)
    ):
        counterbore_diameter = float(
            settings.get("hole_counterbore_diameter", 0.0)
        )
        counterbore_depth = float(settings.get("hole_counterbore_depth", 0.0))
        if counterbore_diameter <= 0.0:
            raise ValueError("O diâmetro do rebaixo da cabeça deve ser maior que zero.")
        if counterbore_diameter + 1.0e-9 < float(settings["tool_diameter"]):
            raise ValueError("O diâmetro do rebaixo não pode ser menor que a fresa.")
        if counterbore_depth <= 0.0:
            raise ValueError(
                "A profundidade do rebaixo da cabeça deve ser maior que zero."
            )
        available_hole_depth = max(0.0, final_depth - start_depth)
        if counterbore_depth > available_hole_depth + 1.0e-9:
            raise ValueError(
                "A profundidade do rebaixo da cabeça não pode ultrapassar a "
                "profundidade restante do furo."
            )
        if str(settings.get("tool_type", "end_mill")) not in {
            "end_mill",
            "compression",
        }:
            raise ValueError(
                "O rebaixo da cabeça exige fresa de topo ou de compressão."
            )
    if settings["rpm"] <= 0:
        raise ValueError("O RPM deve ser maior que zero.")
    if settings.get("simulation_speed_multiplier", 1.0) <= 0:
        raise ValueError("A velocidade da simulação deve ser maior que zero.")
    if settings.get("machine_x_size", MACHINE_LIMITS["x_size"]) < 0:
        raise ValueError("A área útil X da máquina não pode ser negativa.")
    if settings.get("machine_y_size", MACHINE_LIMITS["y_size"]) < 0:
        raise ValueError("A área útil Y da máquina não pode ser negativa.")
    if settings.get("operation_mode") == "pocket":
        stepover_percent = float(settings.get("pocket_stepover_percent", 0.0))
        if not 1.0 <= stepover_percent <= 100.0:
            raise ValueError(
                "O passo lateral do rebaixo deve ficar entre 1% e 100%."
            )
        if float(settings.get("pocket_allowance", 0.0)) < 0.0:
            raise ValueError("O sobremetal lateral do rebaixo não pode ser negativo.")
        if not math.isfinite(float(settings.get("pocket_raster_angle", 0.0))):
            raise ValueError("O ângulo do raster precisa ser um número finito.")
        if settings.get("pocket_strategy") not in {"offset", "raster"}:
            raise ValueError("A estratégia de preenchimento do rebaixo é inválida.")
    if settings.get("corner_slowdown_enabled", False):
        corner_angle = float(settings.get("corner_angle_threshold", 45.0))
        corner_feed_percent = float(settings.get("corner_feed_percent", 40.0))
        corner_distance = float(settings.get("corner_slowdown_distance", 8.0))
        if not 0.0 < corner_angle <= 180.0:
            raise ValueError(
                "A mudança mínima de direção deve ficar entre 0° e 180°."
            )
        if not 0.0 < corner_feed_percent <= 100.0:
            raise ValueError("O avanço no canto deve ficar entre 0% e 100%.")
        if corner_distance <= 0.0:
            raise ValueError(
                "A distância de desaceleração nos cantos deve ser maior que zero."
            )
    if not math.isfinite(float(settings.get("start_x", 0.0))):
        raise ValueError("O ponto inicial X precisa ser um número finito.")
    if not math.isfinite(float(settings.get("start_y", 0.0))):
        raise ValueError("O ponto inicial Y precisa ser um número finito.")
    if not math.isfinite(float(settings.get("job_width", 0.0))):
        raise ValueError("A largura do trabalho precisa ser um número finito.")
    if not math.isfinite(float(settings.get("job_height", 0.0))):
        raise ValueError("A altura do trabalho precisa ser um número finito.")
    if not math.isfinite(float(settings.get("job_depth", 0.0))):
        raise ValueError("O Z da área de trabalho precisa ser um número finito.")
    if float(settings.get("job_width", 0.0)) < 0.0:
        raise ValueError("A largura do trabalho não pode ser negativa.")
    if float(settings.get("job_height", 0.0)) < 0.0:
        raise ValueError("A altura do trabalho não pode ser negativa.")
    if float(settings.get("job_depth", 0.0)) < 0.0:
        raise ValueError("O Z da área de trabalho não pode ser negativo.")
    if float(settings.get("model_gap_above", 0.0)) < 0.0:
        raise ValueError("A folga acima do modelo não pode ser negativa.")
    if float(settings.get("model_gap_below", 0.0)) < 0.0:
        raise ValueError("A folga abaixo do modelo não pode ser negativa.")
    if float(settings.get("model_gap_above", 0.0)) > settings["material_thickness"]:
        raise ValueError("A folga acima do modelo não pode passar da espessura do material.")
    if float(settings.get("model_gap_below", 0.0)) > settings["material_thickness"]:
        raise ValueError("A folga abaixo do modelo não pode passar da espessura do material.")

    if settings["feed_xy"] > MACHINE_LIMITS["max_feed_xy"]:
        raise ValueError(f"O avanço de corte XY excede o limite da máquina ({MACHINE_LIMITS['max_feed_xy']} mm/min).")
    if settings["feed_z"] > MACHINE_LIMITS["max_plunge"]:
        raise ValueError(f"O avanço de descida Z excede o limite da máquina ({MACHINE_LIMITS['max_plunge']} mm/min).")
    final_depth = float(
        settings.get(
            "final_depth",
            settings["material_thickness"] + settings["depth_extra"],
        )
    )
    if final_depth <= 0:
        raise ValueError("A profundidade final calculada deve ser maior que zero.")

    if settings["retract_height"] <= settings["safe_height"]:
        raise ValueError("A altura de retração deve ser maior que a altura segura.")

    z_offset = (
        float(settings["material_thickness"])
        if z_zero_mode == "machine_bed"
        else 0.0
    )
    output_final_z = z_offset - final_depth
    output_retract_z = z_offset + float(settings["retract_height"])
    if output_final_z < MACHINE_LIMITS["z_min"]:
        raise ValueError(
            f"A profundidade final (Z {output_final_z:.2f} mm) ultrapassa o limite minimo de Z "
            f"({MACHINE_LIMITS['z_min']:.2f} mm)."
        )
    if output_retract_z > MACHINE_LIMITS["z_max"]:
        raise ValueError(
            f"A altura de retracao (Z {output_retract_z:.2f} mm) ultrapassa o limite maximo de Z "
            f"({MACHINE_LIMITS['z_max']:.2f} mm)."
        )
