"""Estratégias de desbaste e acabamento 3D sobre uma superfície 2.5D."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .surface import HeightField


@dataclass(frozen=True)
class RoughingOptions:
    tool_diameter: float
    stepdown: float
    stepover_percent: float = 40.0
    allowance: float = 0.5
    safe_height: float = 5.0
    gap_above: float = 0.0
    strategy: str = "z_level"
    raster_axis: str = "x"
    reverse: bool = False
    ramp: bool = True
    ramp_length: float = 20.0
    boundary_offset: float = 0.0
    boundary: tuple[tuple[float, float], ...] | None = None
    profile: str = "last"
    order: str = "level"
    pass_depths: tuple[float, ...] | None = None


@dataclass(frozen=True)
class FinishingOptions:
    tool_diameter: float
    stepover_percent: float = 10.0
    safe_height: float = 5.0
    gap_above: float = 0.0
    strategy: str = "raster"
    raster_angle: float = 0.0
    reverse: bool = False
    boundary_offset: float = 0.0
    boundary: tuple[tuple[float, float], ...] | None = None
    tool_type: str = "ball_nose"


def _point_in_polygon(point, polygon):
    if not polygon:
        return True
    inside = False
    x, y = point
    previous = polygon[-1]
    for current in polygon:
        if (current[1] > y) != (previous[1] > y):
            crossing_x = (previous[0] - current[0]) * (y - current[1]) / (previous[1] - current[1]) + current[0]
            if x < crossing_x:
                inside = not inside
        previous = current
    return inside


def _offset_polygon(points, amount):
    """Offset miter simples para fronteiras CAM não auto-intersectantes."""
    if not points or abs(float(amount)) <= 1e-9:
        return points
    polygon = list(points)
    area = 0.5 * sum(
        polygon[index][0] * polygon[(index + 1) % len(polygon)][1]
        - polygon[(index + 1) % len(polygon)][0] * polygon[index][1]
        for index in range(len(polygon))
    )
    direction = 1.0 if area > 0.0 else -1.0
    shifted = []
    for index, current in enumerate(polygon):
        previous = polygon[index - 1]
        following = polygon[(index + 1) % len(polygon)]
        normals = []
        for start, end in ((previous, current), (current, following)):
            dx, dy = end[0] - start[0], end[1] - start[1]
            length = math.hypot(dx, dy)
            if length > 1e-9:
                normals.append((dy / length * direction, -dx / length * direction))
        if not normals:
            shifted.append(current)
            continue
        nx = sum(item[0] for item in normals)
        ny = sum(item[1] for item in normals)
        length = math.hypot(nx, ny)
        if length <= 1e-9:
            nx, ny = normals[-1]
        else:
            nx, ny = nx / length, ny / length
        dot = max(0.2, abs(nx * normals[-1][0] + ny * normals[-1][1]))
        scale = float(amount) / dot
        shifted.append((current[0] + nx * scale, current[1] + ny * scale))
    return tuple(shifted)


def _validate_common(tool_diameter, stepover_percent, safe_height):
    if float(tool_diameter) <= 0.0:
        raise ValueError("O diâmetro da fresa 3D precisa ser maior que zero.")
    if not 0.1 <= float(stepover_percent) <= 100.0:
        raise ValueError("O passo lateral 3D precisa ficar entre 0,1% e 100%.")
    if float(safe_height) <= 0.0:
        raise ValueError("O Z seguro precisa ser maior que zero.")


def compensated_height_field(field, tool_diameter, tool_type="ball_nose"):
    """Calcula a profundidade segura da ponta considerando o corpo da fresa."""
    radius = float(tool_diameter) * 0.5
    if radius <= 1e-9:
        raise ValueError("O diâmetro da fresa 3D precisa ser maior que zero.")
    normalized_type = str(tool_type or "ball_nose").lower()
    output = []
    reach_x = max(1, int(math.ceil(radius / field.step_x)))
    reach_y = max(1, int(math.ceil(radius / field.step_y)))
    for row in range(field.rows):
        for column in range(field.columns):
            center = field.value(column, row)
            if center is None:
                output.append(None)
                continue
            safe_tip_z = None
            for dr in range(-reach_y, reach_y + 1):
                for dc in range(-reach_x, reach_x + 1):
                    surface_z = field.value(column + dc, row + dr)
                    if surface_z is None:
                        continue
                    dx = dc * field.step_x
                    dy = dr * field.step_y
                    radial = math.hypot(dx, dy)
                    if radial > radius + 1e-9:
                        continue
                    if normalized_type == "ball_nose":
                        sag = radius - math.sqrt(max(0.0, radius * radius - radial * radial))
                    else:
                        sag = 0.0
                    candidate = surface_z - sag
                    if safe_tip_z is None or candidate > safe_tip_z:
                        safe_tip_z = candidate
            output.append(center if safe_tip_z is None else safe_tip_z)
    return HeightField(
        field.origin_x,
        field.origin_y,
        field.step_x,
        field.step_y,
        field.columns,
        field.rows,
        tuple(output),
        field.source_min_z,
        field.source_max_z,
        field.source_hash,
    )


def _depth_from_z(field, z, gap_above):
    return max(0.0, float(gap_above) + field.source_max_z - float(z))


def _scan_coordinates(field, spacing, axis):
    axis = str(axis).lower()
    if axis == "y":
        count = max(2, int(math.ceil((field.max_x - field.origin_x) / spacing)) + 1)
        for index in range(count):
            x = min(field.max_x, field.origin_x + index * spacing)
            yield [(x, field.origin_y + row * field.step_y) for row in range(field.rows)]
    else:
        count = max(2, int(math.ceil((field.max_y - field.origin_y) / spacing)) + 1)
        for index in range(count):
            y = min(field.max_y, field.origin_y + index * spacing)
            yield [(field.origin_x + column * field.step_x, y) for column in range(field.columns)]


def _split_runs(points):
    runs, current = [], []
    for point in points:
        if point is None:
            if current:
                runs.append(current)
                current = []
        else:
            current.append(point)
    if current:
        runs.append(current)
    return [run for run in runs if len(run) >= 2]


def _append_cut_run(moves, run, safe_height, ramp=False, ramp_length=0.0):
    if len(run) < 2:
        return
    start_x, start_y, start_z = run[0]
    moves.append({"type": "rapid", "x": start_x, "y": start_y, "z": safe_height})
    if ramp:
        moves.append({"type": "feed_plunge", "x": start_x, "y": start_y, "z": min(0.0, start_z * 0.15)})
        traveled = 0.0
        previous = run[0]
        ramp_count = 1
        target_length = max(1e-6, float(ramp_length))
        for index, (x, y, z) in enumerate(run[1:], start=1):
            traveled += math.hypot(x - previous[0], y - previous[1])
            ratio = min(1.0, traveled / target_length)
            moves.append({"type": "feed_ramp", "x": x, "y": y, "z": z * ratio})
            ramp_count = index + 1
            previous = (x, y, z)
            if ratio >= 1.0:
                break
        remaining = run[ramp_count:]
    else:
        moves.append({"type": "feed_plunge", "x": start_x, "y": start_y, "z": start_z})
        remaining = run[1:]
    for x, y, z in remaining:
        moves.append({"type": "feed_cut", "x": x, "y": y, "z": z})
    moves.append({"type": "rapid", "x": None, "y": None, "z": safe_height})


def _surface_connector(field, boundary, start, end, gap_above, maximum_distance):
    """Liga duas passadas acompanhando a superfície, sem atravessar vazios.

    O acabamento raster alterna o sentido das linhas. Em uma superfície
    contínua, o fim de uma linha e o início da seguinte ficam lado a lado e
    podem ser unidos mantendo a ponta sobre a altura compensada. Se o trecho
    cruza uma fronteira, um furo ou uma região sem amostra, ``None`` força o
    recolhimento normal ao Z seguro.
    """
    distance = math.hypot(end[0] - start[0], end[1] - start[1])
    if distance > float(maximum_distance) + 1e-7:
        return None
    sample_step = max(0.01, min(field.step_x, field.step_y))
    count = max(1, int(math.ceil(distance / sample_step)))
    connector = []
    for index in range(1, count + 1):
        ratio = index / count
        x = start[0] + (end[0] - start[0]) * ratio
        y = start[1] + (end[1] - start[1]) * ratio
        z = field.sample(x, y)
        if z is None or not _point_in_polygon((x, y), boundary):
            return None
        connector.append((x, y, -_depth_from_z(field, z, gap_above)))
    return connector


def _append_linked_raster_runs(
    moves, field, boundary, runs, safe_height, gap_above, spacing
):
    """Emite raster contínuo e recolhe somente entre regiões desconectadas."""
    active_endpoint = None
    sample_step = max(0.01, min(field.step_x, field.step_y))
    maximum_link = max(float(spacing) * 1.5, float(spacing) + 2.0 * sample_step)
    for run in runs:
        if len(run) < 2:
            continue
        connector = None
        if active_endpoint is not None:
            connector = _surface_connector(
                field,
                boundary,
                active_endpoint,
                run[0],
                gap_above,
                maximum_link,
            )
        if connector is None:
            if active_endpoint is not None:
                moves.append({"type": "rapid", "x": None, "y": None, "z": safe_height})
            start_x, start_y, start_z = run[0]
            moves.append({"type": "rapid", "x": start_x, "y": start_y, "z": safe_height})
            moves.append({"type": "feed_plunge", "x": start_x, "y": start_y, "z": start_z})
        else:
            for x, y, z in connector:
                moves.append({"type": "feed_cut", "x": x, "y": y, "z": z})
        for x, y, z in run[1:]:
            moves.append({"type": "feed_cut", "x": x, "y": y, "z": z})
        active_endpoint = run[-1]
    if active_endpoint is not None:
        moves.append({"type": "rapid", "x": None, "y": None, "z": safe_height})


def _profile_line(field, boundary, sample_step):
    polygon = list(boundary or (
        (field.origin_x, field.origin_y),
        (field.max_x, field.origin_y),
        (field.max_x, field.max_y),
        (field.origin_x, field.max_y),
    ))
    points = []
    for start, end in zip(polygon, polygon[1:] + polygon[:1]):
        length = math.hypot(end[0] - start[0], end[1] - start[1])
        count = max(1, int(math.ceil(length / sample_step)))
        points.extend(
            (
                start[0] + (end[0] - start[0]) * index / count,
                start[1] + (end[1] - start[1]) * index / count,
            )
            for index in range(count)
        )
    if points:
        points.append(points[0])
    return points


def build_3d_roughing_moves(field: HeightField, options: RoughingOptions):
    _validate_common(options.tool_diameter, options.stepover_percent, options.safe_height)
    if options.stepdown <= 0.0:
        raise ValueError("A profundidade por passada do desbaste precisa ser positiva.")
    if options.allowance < 0.0:
        raise ValueError("O sobremetal do desbaste não pode ser negativo.")
    if options.strategy not in {"z_level", "raster_3d"}:
        raise ValueError("A estratégia de desbaste 3D é inválida.")
    compensated = compensated_height_field(field, options.tool_diameter, "end_mill")
    boundary = _offset_polygon(options.boundary, options.boundary_offset)
    spacing = max(0.05, options.tool_diameter * options.stepover_percent / 100.0)
    maximum_depth = max(
        _depth_from_z(compensated, value, options.gap_above) - options.allowance
        for value in compensated.heights if value is not None
    )
    if maximum_depth <= 1e-7:
        raise ValueError("O modelo não exige remoção com o sobremetal configurado.")
    levels = []
    if options.pass_depths:
        cumulative = 0.0
        for amount in options.pass_depths:
            cumulative += float(amount)
            if cumulative > 0.0:
                levels.append(min(cumulative, maximum_depth))
            if cumulative >= maximum_depth - 1e-7:
                break
    if not levels or levels[-1] < maximum_depth - 1e-7:
        current = (levels[-1] if levels else 0.0) + options.stepdown
        while current < maximum_depth - 1e-7:
            levels.append(current)
            current += options.stepdown
        levels.append(maximum_depth)
    moves = []
    lines = list(_scan_coordinates(compensated, spacing, options.raster_axis))

    def prepared_line(line, level, reversed_line):
            prepared = []
            for x, y in line:
                z = compensated.sample(x, y)
                if z is None or not _point_in_polygon((x, y), boundary):
                    prepared.append(None)
                    continue
                surface_depth = max(0.0, _depth_from_z(compensated, z, options.gap_above) - options.allowance)
                if options.strategy == "z_level" and surface_depth + 1e-7 < level:
                    prepared.append(None)
                else:
                    prepared.append((x, y, -min(level, surface_depth)))
            if reversed_line:
                prepared.reverse()
            return prepared

    def append_profile(level):
        profile_points = []
        for x, y in _profile_line(compensated, boundary, min(compensated.step_x, compensated.step_y)):
            z = compensated.sample(x, y)
            if z is None:
                profile_points.append(None)
                continue
            surface_depth = max(0.0, _depth_from_z(compensated, z, options.gap_above) - options.allowance)
            profile_points.append((x, y, -min(level, surface_depth)))
        for run in _split_runs(profile_points):
            _append_cut_run(
                moves, run, options.safe_height, options.ramp, options.ramp_length
            )

    if options.order == "depth":
        reverse_line = bool(options.reverse)
        for line in lines:
            for level in levels:
                for run in _split_runs(prepared_line(line, level, reverse_line)):
                    _append_cut_run(
                        moves, run, options.safe_height, options.ramp, options.ramp_length
                    )
            reverse_line = not reverse_line
        if options.strategy == "z_level" and options.profile != "none":
            for level in levels:
                append_profile(level)
    else:
        reverse_line = bool(options.reverse)
        for level in levels:
            if options.strategy == "z_level" and options.profile == "first":
                append_profile(level)
            for line in lines:
                for run in _split_runs(prepared_line(line, level, reverse_line)):
                    _append_cut_run(
                        moves, run, options.safe_height, options.ramp, options.ramp_length
                    )
                reverse_line = not reverse_line
            if options.strategy == "z_level" and options.profile == "last":
                append_profile(level)
    return moves


def _raster_finish_lines(field, spacing, angle):
    normalized = float(angle) % 180.0
    if abs(normalized - 90.0) <= 1e-6:
        return list(_scan_coordinates(field, spacing, "y"))
    if abs(normalized) <= 1e-6:
        return list(_scan_coordinates(field, spacing, "x"))
    # Varredura oblíqua: linhas paralelas no referencial rotacionado.
    theta = math.radians(normalized)
    direction = (math.cos(theta), math.sin(theta))
    normal = (-direction[1], direction[0])
    corners = (
        (field.origin_x, field.origin_y), (field.max_x, field.origin_y),
        (field.max_x, field.max_y), (field.origin_x, field.max_y),
    )
    projections = [x * normal[0] + y * normal[1] for x, y in corners]
    along = [x * direction[0] + y * direction[1] for x, y in corners]
    p_min, p_max = min(projections), max(projections)
    a_min, a_max = min(along), max(along)
    sample = min(field.step_x, field.step_y)
    lines = []
    offset = p_min
    while offset <= p_max + 1e-7:
        line = []
        travel = a_min
        while travel <= a_max + 1e-7:
            line.append((direction[0] * travel + normal[0] * offset, direction[1] * travel + normal[1] * offset))
            travel += sample
        lines.append(line)
        offset += spacing
    return lines


def _offset_finish_lines(field, spacing):
    lines = []
    inset = 0.0
    max_inset = min(field.max_x - field.origin_x, field.max_y - field.origin_y) * 0.5
    sample = min(field.step_x, field.step_y)
    while inset <= max_inset + 1e-7:
        x0, y0 = field.origin_x + inset, field.origin_y + inset
        x1, y1 = field.max_x - inset, field.max_y - inset
        if x1 <= x0 or y1 <= y0:
            break
        loop = []
        x = x0
        while x < x1:
            loop.append((x, y0)); x += sample
        y = y0
        while y < y1:
            loop.append((x1, y)); y += sample
        x = x1
        while x > x0:
            loop.append((x, y1)); x -= sample
        y = y1
        while y > y0:
            loop.append((x0, y)); y -= sample
        loop.append((x0, y0))
        lines.append(loop)
        inset += spacing
    return lines


def build_3d_finishing_moves(field: HeightField, options: FinishingOptions):
    _validate_common(options.tool_diameter, options.stepover_percent, options.safe_height)
    if options.strategy not in {"raster", "offset"}:
        raise ValueError("A estratégia de acabamento 3D é inválida.")
    compensated = compensated_height_field(field, options.tool_diameter, options.tool_type)
    boundary = _offset_polygon(options.boundary, options.boundary_offset)
    spacing = max(0.02, options.tool_diameter * options.stepover_percent / 100.0)
    lines = (
        _offset_finish_lines(compensated, spacing)
        if options.strategy == "offset"
        else _raster_finish_lines(compensated, spacing, options.raster_angle)
    )
    moves = []
    raster_runs = []
    reverse_line = bool(options.reverse)
    for line in lines:
        prepared = []
        for x, y in line:
            z = compensated.sample(x, y)
            if z is None or not _point_in_polygon((x, y), boundary):
                prepared.append(None)
                continue
            prepared.append((x, y, -_depth_from_z(compensated, z, options.gap_above)))
        if reverse_line:
            prepared.reverse()
        prepared_runs = _split_runs(prepared)
        if options.strategy == "raster":
            raster_runs.extend(prepared_runs)
        else:
            for run in prepared_runs:
                _append_cut_run(moves, run, options.safe_height, False)
        reverse_line = not reverse_line
    if options.strategy == "raster":
        _append_linked_raster_runs(
            moves,
            compensated,
            boundary,
            raster_runs,
            options.safe_height,
            options.gap_above,
            spacing,
        )
    if not moves:
        raise ValueError("O acabamento não encontrou uma superfície dentro da fronteira.")
    return moves
