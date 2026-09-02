"""Otimização conservadora de layouts CNC usando o contorno real das peças.

O MaxRects continua responsável por escolher a chapa e garantir uma solução
inicial. Este módulo tenta reempacotar as peças já atribuídas a cada chapa em
uma grade raster. O resultado só é aceito quando:

* todas as peças continuam na mesma chapa;
* as rotações respeitam as opções fornecidas pelo nesting principal;
* os polígonos reais ficam dentro da área útil;
* não há colisão e o espaçamento mínimo é respeitado;
* a faixa ocupada fica menor que a solução original.

Essa estratégia deixa o modo por formas seguro para o fluxo de produção:
qualquer aproximação ruim da grade resulta em fallback automático.
"""

import math
import random
from dataclasses import dataclass

from .models import LayoutPlacement
from .orientation import (
    normalize_rotation_deg,
    placement_rotation_deg,
    rotation_swaps_axes,
    transform_part_point,
)
from .raster_nesting import _mask_fits_bits, _stamp_bits


SHAPE_LAYOUT_SUFFIX = " + Formas"
_GEOMETRY_TOLERANCE_MM = 1e-6


@dataclass
class ShapeOptimizationResult:
    placements: list
    improved: bool
    baseline_score: tuple
    optimized_score: tuple
    resolution_mm: float
    reason: str = ""


@dataclass
class _RasterOption:
    rotation_deg: int
    length_mm: float
    width_mm: float
    polygon: list
    bits: list
    rows: int
    cols: int


@dataclass
class _RasterItem:
    original_index: int
    placement: object
    options: list
    actual_area_mm2: float
    fill_ratio: float


def _clean_profile_points(points):
    cleaned = []
    for point in list(points or []):
        try:
            x_mm = float(point[0])
            y_mm = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if cleaned and (
            abs(cleaned[-1][0] - x_mm) <= _GEOMETRY_TOLERANCE_MM
            and abs(cleaned[-1][1] - y_mm) <= _GEOMETRY_TOLERANCE_MM
        ):
            continue
        cleaned.append((x_mm, y_mm))

    if len(cleaned) >= 2 and (
        abs(cleaned[0][0] - cleaned[-1][0]) <= _GEOMETRY_TOLERANCE_MM
        and abs(cleaned[0][1] - cleaned[-1][1]) <= _GEOMETRY_TOLERANCE_MM
    ):
        cleaned.pop()
    return cleaned


def part_profile_points(part):
    """Retorna o perfil canônico da peça, usando retângulo como fallback."""
    length_mm = float(part.length_mm)
    width_mm = float(part.width_mm)
    profile = _clean_profile_points(getattr(part, "profile_points", None))
    if len(profile) < 3:
        return [
            (0.0, 0.0),
            (length_mm, 0.0),
            (length_mm, width_mm),
            (0.0, width_mm),
        ]
    return profile


def oriented_part_profile_points(part, rotation_deg=0):
    """Aplica uma rotação canônica 0°/90°/180°/270° ao perfil."""
    if isinstance(rotation_deg, bool):
        rotation_deg = 90 if rotation_deg else 0
    rotation_deg = normalize_rotation_deg(rotation_deg)
    profile = part_profile_points(part)
    return [
        transform_part_point(part, x_mm, y_mm, rotation_deg)
        for x_mm, y_mm in profile
    ]


def placement_profile_points(placement):
    """Contorno real de um placement em coordenadas absolutas da chapa."""
    return [
        (placement.x_mm + x_mm, placement.y_mm + y_mm)
        for x_mm, y_mm in oriented_part_profile_points(
            placement.part,
            placement_rotation_deg(placement),
        )
    ]


def polygon_area_mm2(points):
    if len(points) < 3:
        return 0.0
    doubled_area = 0.0
    for index, (x_mm, y_mm) in enumerate(points):
        next_x_mm, next_y_mm = points[(index + 1) % len(points)]
        doubled_area += (x_mm * next_y_mm) - (next_x_mm * y_mm)
    return abs(doubled_area) * 0.5


def placement_actual_area_mm2(placement):
    return polygon_area_mm2(
        oriented_part_profile_points(
            placement.part,
            placement_rotation_deg(placement),
        )
    )


def layout_shape_score(placements, margin_mm=0.0):
    """Pontuação focada no maior retalho retangular à direita da chapa."""
    if not placements:
        return (0.0, 0.0, 0.0)
    max_right_mm = max(
        placement.x_mm + placement.placed_length_mm
        for placement in placements
    )
    max_top_mm = max(
        placement.y_mm + placement.placed_width_mm
        for placement in placements
    )
    occupied_length_mm = max(0.0, max_right_mm - margin_mm)
    occupied_width_mm = max(0.0, max_top_mm - margin_mm)
    return (
        round(occupied_length_mm, 6),
        round(occupied_width_mm, 6),
        round(occupied_length_mm * occupied_width_mm, 6),
    )


def _point_in_polygon(x_mm, y_mm, polygon):
    inside = False
    previous_index = len(polygon) - 1
    for index, (point_x_mm, point_y_mm) in enumerate(polygon):
        previous_x_mm, previous_y_mm = polygon[previous_index]
        if (
            (point_y_mm > y_mm) != (previous_y_mm > y_mm)
            and x_mm
            < (
                (previous_x_mm - point_x_mm)
                * (y_mm - point_y_mm)
                / (previous_y_mm - point_y_mm + 1e-15)
                + point_x_mm
            )
        ):
            inside = not inside
        previous_index = index
    return inside


def _is_rectangular_profile(part):
    profile = _clean_profile_points(getattr(part, "profile_points", None))
    if len(profile) < 3:
        return True
    expected_area = float(part.length_mm) * float(part.width_mm)
    if expected_area <= 0.0:
        return False
    return abs(polygon_area_mm2(profile) - expected_area) <= max(
        0.01,
        expected_area * 1e-7,
    )


def _polygon_to_bits(polygon, length_mm, width_mm, resolution_mm, rectangular=False):
    cols = max(1, int(math.ceil(length_mm / resolution_mm)))
    rows = max(1, int(math.ceil(width_mm / resolution_mm)))

    if rectangular:
        full_row = (1 << cols) - 1
        return [full_row] * rows, rows, cols

    bits = []
    for row in range(rows):
        y_mm = min(width_mm - _GEOMETRY_TOLERANCE_MM, (row + 0.5) * resolution_mm)
        row_bits = 0
        for col in range(cols):
            x_mm = min(length_mm - _GEOMETRY_TOLERANCE_MM, (col + 0.5) * resolution_mm)
            if _point_in_polygon(x_mm, y_mm, polygon):
                row_bits |= 1 << col
        bits.append(row_bits)
    return bits, rows, cols


def _adaptive_resolution_mm(spacing_mm):
    if spacing_mm > 0.0:
        return max(2.0, min(4.0, spacing_mm * 0.5))
    return 2.0


def _build_items(placements, orientation_options, resolution_mm):
    items = []
    for index, placement in enumerate(placements):
        part = placement.part
        rectangular = _is_rectangular_profile(part)
        options = []
        seen_options = set()
        for rotation_value, length_mm, width_mm in orientation_options[index]:
            if isinstance(rotation_value, bool):
                rotation_deg = 90 if rotation_value else 0
            else:
                rotation_deg = normalize_rotation_deg(rotation_value)
            option_key = (
                rotation_deg,
                round(float(length_mm), 6),
                round(float(width_mm), 6),
            )
            if option_key in seen_options:
                continue
            seen_options.add(option_key)
            polygon = oriented_part_profile_points(part, rotation_deg)
            bits, rows, cols = _polygon_to_bits(
                polygon,
                float(length_mm),
                float(width_mm),
                resolution_mm,
                rectangular=rectangular,
            )
            options.append(
                _RasterOption(
                    rotation_deg=rotation_deg,
                    length_mm=float(length_mm),
                    width_mm=float(width_mm),
                    polygon=polygon,
                    bits=bits,
                    rows=rows,
                    cols=cols,
                )
            )

        if not options:
            return []

        actual_area_mm2 = polygon_area_mm2(part_profile_points(part))
        bbox_area_mm2 = max(
            _GEOMETRY_TOLERANCE_MM,
            float(part.length_mm) * float(part.width_mm),
        )
        items.append(
            _RasterItem(
                original_index=index,
                placement=placement,
                options=options,
                actual_area_mm2=actual_area_mm2,
                fill_ratio=actual_area_mm2 / bbox_area_mm2,
            )
        )
    return items


def _candidate_orders(items):
    orders = [
        sorted(items, key=lambda item: (item.placement.y_mm, item.placement.x_mm)),
        sorted(
            items,
            key=lambda item: (
                item.actual_area_mm2,
                max(item.placement.part.length_mm, item.placement.part.width_mm),
            ),
            reverse=True,
        ),
        sorted(
            items,
            key=lambda item: (
                max(item.placement.part.length_mm, item.placement.part.width_mm),
                item.actual_area_mm2,
            ),
            reverse=True,
        ),
        sorted(
            items,
            key=lambda item: (
                item.placement.part.length_mm,
                item.placement.part.width_mm,
                item.actual_area_mm2,
            ),
            reverse=True,
        ),
        sorted(
            items,
            key=lambda item: (
                item.placement.part.width_mm,
                item.placement.part.length_mm,
                item.actual_area_mm2,
            ),
            reverse=True,
        ),
        sorted(
            items,
            key=lambda item: (
                item.fill_ratio,
                -item.actual_area_mm2,
            ),
        ),
        sorted(
            items,
            key=lambda item: (
                -item.fill_ratio,
                -item.actual_area_mm2,
            ),
        ),
        list(reversed(sorted(items, key=lambda item: item.original_index))),
    ]

    unique_orders = []
    seen = set()
    for order in orders:
        key = tuple(item.original_index for item in order)
        if key in seen:
            continue
        seen.add(key)
        unique_orders.append(order)

    # Multi-início determinístico: o greedy é muito sensível à ordem. Estas
    # permutações permitem explorar vizinhanças que uma ordenação por área ou
    # dimensão não alcança, mantendo resultados reproduzíveis.
    randomizer = random.Random(0x504E455354)
    randomized_trials = min(14, max(8, len(items)))
    base_items = list(items)
    for _ in range(randomized_trials):
        order = list(base_items)
        randomizer.shuffle(order)
        key = tuple(item.original_index for item in order)
        if key in seen:
            continue
        seen.add(key)
        unique_orders.append(order)
    return unique_orders


def _empty_sheet_bits(sheet_rows, sheet_cols):
    return [0] * sheet_rows


def _find_best_option_position(
    sheet_bits,
    sheet_rows,
    sheet_cols,
    option,
    resolution_mm,
    margin_mm,
    sheet_length_mm,
    sheet_width_mm,
    current_right_col,
    current_top_row,
):
    min_col = int(math.ceil((margin_mm - _GEOMETRY_TOLERANCE_MM) / resolution_mm))
    min_row = min_col
    max_col = int(
        math.floor(
            (
                sheet_length_mm
                - margin_mm
                - option.length_mm
                + _GEOMETRY_TOLERANCE_MM
            )
            / resolution_mm
        )
    )
    max_row = int(
        math.floor(
            (
                sheet_width_mm
                - margin_mm
                - option.width_mm
                + _GEOMETRY_TOLERANCE_MM
            )
            / resolution_mm
        )
    )
    max_col = min(max_col, sheet_cols - option.cols)
    max_row = min(max_row, sheet_rows - option.rows)
    if max_col < min_col or max_row < min_row:
        return None

    first_mask_row = next(
        ((row_index, row_bits) for row_index, row_bits in enumerate(option.bits) if row_bits),
        None,
    )
    if first_mask_row is None:
        return None
    first_row_index, first_row_bits = first_mask_row

    best = None
    for col in range(min_col, max_col + 1):
        new_right_col = max(current_right_col, col + option.cols)
        if best is not None and new_right_col > best[0][0]:
            break
        for row in range(min_row, max_row + 1):
            if sheet_bits[row + first_row_index] & (first_row_bits << col):
                continue
            if not _mask_fits_bits(
                sheet_bits,
                option.bits,
                option.rows,
                row,
                col,
            ):
                continue
            new_top_row = max(current_top_row, row + option.rows)
            score = (
                new_right_col,
                new_top_row,
                new_right_col * new_top_row,
                col,
                row,
                option.rotation_deg,
            )
            if best is None or score < best[0]:
                best = (score, row, col)
            # Para uma coluna fixa, a primeira posição livre já minimiza Y.
            break
    return best


def _pack_order(
    order,
    sheet_length_mm,
    sheet_width_mm,
    margin_mm,
    spacing_mm,
    resolution_mm,
    conservative_padding,
    orientation_policy="immediate",
):
    sheet_cols = max(1, int(math.floor(sheet_length_mm / resolution_mm)))
    sheet_rows = max(1, int(math.floor(sheet_width_mm / resolution_mm)))
    sheet_bits = _empty_sheet_bits(sheet_rows, sheet_cols)
    spacing_pixels = int(math.ceil(spacing_mm / resolution_mm))
    if conservative_padding:
        spacing_pixels += 1

    current_right_col = int(math.ceil(margin_mm / resolution_mm))
    current_top_row = current_right_col
    placed = []

    for item in order:
        best = None
        for option in item.options:
            candidate = _find_best_option_position(
                sheet_bits,
                sheet_rows,
                sheet_cols,
                option,
                resolution_mm,
                margin_mm,
                sheet_length_mm,
                sheet_width_mm,
                current_right_col,
                current_top_row,
            )
            if candidate is None:
                continue
            candidate_score, row, col = candidate
            current_orientation_penalty = (
                0
                if option.rotation_deg == placement_rotation_deg(item.placement)
                else 1
            )
            if orientation_policy == "narrow":
                option_score = (
                    option.cols,
                    candidate_score,
                    current_orientation_penalty,
                )
            elif orientation_policy == "wide":
                option_score = (
                    -option.cols,
                    candidate_score,
                    current_orientation_penalty,
                )
            else:
                option_score = (
                    candidate_score,
                    current_orientation_penalty,
                )
            if best is None or option_score < best[0]:
                best = (option_score, row, col, option)

        if best is None:
            return None

        _, row, col, option = best
        _stamp_bits(
            sheet_bits,
            sheet_rows,
            sheet_cols,
            option.bits,
            option.rows,
            row,
            col,
            spacing_pixels,
        )
        current_right_col = max(current_right_col, col + option.cols)
        current_top_row = max(current_top_row, row + option.rows)
        placed.append(
            (
                item.original_index,
                LayoutPlacement(
                    part=item.placement.part,
                    x_mm=col * resolution_mm,
                    y_mm=row * resolution_mm,
                    placed_length_mm=option.length_mm,
                    placed_width_mm=option.width_mm,
                    rotated=rotation_swaps_axes(option.rotation_deg),
                    rotation_deg=option.rotation_deg,
                ),
            )
        )

    return [
        placement
        for _, placement in sorted(placed, key=lambda current: current[0])
    ]


def _orientation(a, b, c):
    value = ((b[0] - a[0]) * (c[1] - a[1])) - (
        (b[1] - a[1]) * (c[0] - a[0])
    )
    if abs(value) <= _GEOMETRY_TOLERANCE_MM:
        return 0
    return 1 if value > 0.0 else -1


def _point_on_segment(point, start, end):
    return (
        min(start[0], end[0]) - _GEOMETRY_TOLERANCE_MM
        <= point[0]
        <= max(start[0], end[0]) + _GEOMETRY_TOLERANCE_MM
        and min(start[1], end[1]) - _GEOMETRY_TOLERANCE_MM
        <= point[1]
        <= max(start[1], end[1]) + _GEOMETRY_TOLERANCE_MM
        and _orientation(start, end, point) == 0
    )


def _segments_intersect(start_a, end_a, start_b, end_b):
    o1 = _orientation(start_a, end_a, start_b)
    o2 = _orientation(start_a, end_a, end_b)
    o3 = _orientation(start_b, end_b, start_a)
    o4 = _orientation(start_b, end_b, end_a)
    if (o1 * o2) < 0 and (o3 * o4) < 0:
        return True
    return (
        (o1 == 0 and _point_on_segment(start_b, start_a, end_a))
        or (o2 == 0 and _point_on_segment(end_b, start_a, end_a))
        or (o3 == 0 and _point_on_segment(start_a, start_b, end_b))
        or (o4 == 0 and _point_on_segment(end_a, start_b, end_b))
    )


def _point_segment_distance(point, start, end):
    dx = end[0] - start[0]
    dy = end[1] - start[1]
    length_squared = (dx * dx) + (dy * dy)
    if length_squared <= _GEOMETRY_TOLERANCE_MM:
        return math.hypot(point[0] - start[0], point[1] - start[1])
    projection = (
        ((point[0] - start[0]) * dx)
        + ((point[1] - start[1]) * dy)
    ) / length_squared
    projection = max(0.0, min(1.0, projection))
    nearest = (start[0] + projection * dx, start[1] + projection * dy)
    return math.hypot(point[0] - nearest[0], point[1] - nearest[1])


def _segment_distance(start_a, end_a, start_b, end_b):
    if _segments_intersect(start_a, end_a, start_b, end_b):
        return 0.0
    return min(
        _point_segment_distance(start_a, start_b, end_b),
        _point_segment_distance(end_a, start_b, end_b),
        _point_segment_distance(start_b, start_a, end_a),
        _point_segment_distance(end_b, start_a, end_a),
    )


def _polygons_intersect(left_polygon, right_polygon):
    for left_index, left_start in enumerate(left_polygon):
        left_end = left_polygon[(left_index + 1) % len(left_polygon)]
        for right_index, right_start in enumerate(right_polygon):
            right_end = right_polygon[(right_index + 1) % len(right_polygon)]
            if _segments_intersect(left_start, left_end, right_start, right_end):
                return True
    return (
        _point_in_polygon(left_polygon[0][0], left_polygon[0][1], right_polygon)
        or _point_in_polygon(right_polygon[0][0], right_polygon[0][1], left_polygon)
    )


def _polygon_distance(left_polygon, right_polygon):
    if _polygons_intersect(left_polygon, right_polygon):
        return 0.0
    distance = float("inf")
    for left_index, left_start in enumerate(left_polygon):
        left_end = left_polygon[(left_index + 1) % len(left_polygon)]
        for right_index, right_start in enumerate(right_polygon):
            right_end = right_polygon[(right_index + 1) % len(right_polygon)]
            distance = min(
                distance,
                _segment_distance(left_start, left_end, right_start, right_end),
            )
    return distance


def validate_shape_placements(
    placements,
    sheet_length_mm,
    sheet_width_mm,
    margin_mm,
    spacing_mm,
):
    """Valida limites, colisão e distância usando os polígonos reais."""
    min_x_mm = margin_mm
    min_y_mm = margin_mm
    max_x_mm = sheet_length_mm - margin_mm
    max_y_mm = sheet_width_mm - margin_mm
    polygons = []

    for placement in placements:
        polygon = placement_profile_points(placement)
        if len(polygon) < 3:
            return False, f"Perfil invalido em {placement.part.part_id}."
        if (
            min(point[0] for point in polygon) < min_x_mm - _GEOMETRY_TOLERANCE_MM
            or min(point[1] for point in polygon) < min_y_mm - _GEOMETRY_TOLERANCE_MM
            or max(point[0] for point in polygon) > max_x_mm + _GEOMETRY_TOLERANCE_MM
            or max(point[1] for point in polygon) > max_y_mm + _GEOMETRY_TOLERANCE_MM
        ):
            return False, f"A peca {placement.part.part_id} ultrapassa a area util."
        polygons.append((placement, polygon))

    for left_index, (left_placement, left_polygon) in enumerate(polygons):
        for right_placement, right_polygon in polygons[left_index + 1:]:
            distance_mm = _polygon_distance(left_polygon, right_polygon)
            if distance_mm + _GEOMETRY_TOLERANCE_MM < spacing_mm:
                return (
                    False,
                    "Espacamento insuficiente entre "
                    f"{left_placement.part.part_id} e {right_placement.part.part_id}: "
                    f"{distance_mm:.3f} mm.",
                )
            if spacing_mm <= _GEOMETRY_TOLERANCE_MM and _polygons_intersect(
                left_polygon,
                right_polygon,
            ):
                return (
                    False,
                    "Colisao entre "
                    f"{left_placement.part.part_id} e {right_placement.part.part_id}.",
                )
    return True, ""


def _pack_and_validate(
    order,
    layout_sheet,
    margin_mm,
    spacing_mm,
    resolution_mm,
    orientation_policy,
):
    validation_error = ""
    for conservative_padding in (False, True):
        candidate_placements = _pack_order(
            order,
            float(layout_sheet.source_length_mm),
            float(layout_sheet.source_width_mm),
            float(margin_mm),
            float(spacing_mm),
            resolution_mm,
            conservative_padding=conservative_padding,
            orientation_policy=orientation_policy,
        )
        if candidate_placements is None:
            continue
        valid, validation_error = validate_shape_placements(
            candidate_placements,
            float(layout_sheet.source_length_mm),
            float(layout_sheet.source_width_mm),
            float(margin_mm),
            float(spacing_mm),
        )
        if valid:
            return candidate_placements, ""
    return None, validation_error


def optimize_layout_sheet_shapes(
    layout_sheet,
    margin_mm,
    spacing_mm,
    orientation_options,
    resolution_mm=None,
):
    """Tenta melhorar uma chapa, retornando sempre um resultado seguro."""
    baseline_placements = list(layout_sheet.placements)
    baseline_score = layout_shape_score(baseline_placements, margin_mm)
    if len(baseline_placements) < 2:
        return ShapeOptimizationResult(
            placements=baseline_placements,
            improved=False,
            baseline_score=baseline_score,
            optimized_score=baseline_score,
            resolution_mm=resolution_mm or _adaptive_resolution_mm(spacing_mm),
            reason="A chapa tem menos de duas pecas.",
        )
    if not any(
        not _is_rectangular_profile(placement.part)
        for placement in baseline_placements
    ):
        return ShapeOptimizationResult(
            placements=baseline_placements,
            improved=False,
            baseline_score=baseline_score,
            optimized_score=baseline_score,
            resolution_mm=resolution_mm or _adaptive_resolution_mm(spacing_mm),
            reason="Nao ha perfis irregulares nesta chapa.",
        )

    resolution_mm = float(resolution_mm or _adaptive_resolution_mm(spacing_mm))
    if resolution_mm <= 0.0:
        raise ValueError("A resolucao do otimizador por formas deve ser positiva.")
    if len(orientation_options) != len(baseline_placements):
        raise ValueError("As opcoes de rotacao nao correspondem aos placements da chapa.")

    search_resolution_mm = (
        max(5.0, resolution_mm)
        if len(baseline_placements) >= 8
        else resolution_mm
    )
    search_items = _build_items(
        baseline_placements,
        orientation_options,
        search_resolution_mm,
    )
    if len(search_items) != len(baseline_placements):
        return ShapeOptimizationResult(
            placements=baseline_placements,
            improved=False,
            baseline_score=baseline_score,
            optimized_score=baseline_score,
            resolution_mm=resolution_mm,
            reason="Uma ou mais pecas ficaram sem orientacao permitida.",
        )

    validation_error = ""
    coarse_candidates = []
    candidate_orders = _candidate_orders(search_items)
    for order_index, order in enumerate(candidate_orders):
        orientation_policies = ["immediate"]
        # As ordenações estruturadas também são avaliadas com a menor largura
        # de orientação primeiro. Isso evita a armadilha gulosa de deitar uma
        # peça cedo só porque ela melhora alguns milímetros naquele instante.
        if order_index < 8:
            orientation_policies.append("narrow")

        for orientation_policy in orientation_policies:
            candidate_placements, validation_error = _pack_and_validate(
                order,
                layout_sheet,
                margin_mm,
                spacing_mm,
                search_resolution_mm,
                orientation_policy,
            )
            if candidate_placements is None:
                continue
            candidate_score = layout_shape_score(candidate_placements, margin_mm)
            coarse_candidates.append(
                (
                    candidate_score,
                    tuple(item.original_index for item in order),
                    orientation_policy,
                    candidate_placements,
                )
            )

    coarse_candidates.sort(key=lambda candidate: candidate[0])
    best_placements = None
    best_score = None
    if search_resolution_mm == resolution_mm:
        if coarse_candidates:
            best_score, _, _, best_placements = coarse_candidates[0]
    elif coarse_candidates:
        fine_items = _build_items(
            baseline_placements,
            orientation_options,
            resolution_mm,
        )
        fine_item_map = {
            item.original_index: item
            for item in fine_items
        }
        refinement_candidates = []
        seen_refinements = set()
        for _, order_key, orientation_policy, _ in coarse_candidates:
            refinement_key = (order_key, orientation_policy)
            if refinement_key in seen_refinements:
                continue
            seen_refinements.add(refinement_key)
            refinement_candidates.append(refinement_key)
            if len(refinement_candidates) >= 8:
                break

        for order_key, orientation_policy in refinement_candidates:
            fine_order = [fine_item_map[index] for index in order_key]
            candidate_placements, validation_error = _pack_and_validate(
                fine_order,
                layout_sheet,
                margin_mm,
                spacing_mm,
                resolution_mm,
                orientation_policy,
            )
            if candidate_placements is None:
                continue
            candidate_score = layout_shape_score(candidate_placements, margin_mm)
            if best_score is None or candidate_score < best_score:
                best_placements = candidate_placements
                best_score = candidate_score

    if best_placements is None:
        return ShapeOptimizationResult(
            placements=baseline_placements,
            improved=False,
            baseline_score=baseline_score,
            optimized_score=baseline_score,
            resolution_mm=resolution_mm,
            reason=validation_error or "Nenhuma alternativa valida foi encontrada.",
        )

    if best_score >= baseline_score:
        return ShapeOptimizationResult(
            placements=baseline_placements,
            improved=False,
            baseline_score=baseline_score,
            optimized_score=best_score,
            resolution_mm=resolution_mm,
            reason="A alternativa por formas nao superou o layout rapido.",
        )

    return ShapeOptimizationResult(
        placements=best_placements,
        improved=True,
        baseline_score=baseline_score,
        optimized_score=best_score,
        resolution_mm=resolution_mm,
    )


def is_shape_optimized_layout(layout_sheet):
    return SHAPE_LAYOUT_SUFFIX.strip() in str(layout_sheet.layout_strategy or "")
