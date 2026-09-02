import math
import time
from dataclasses import replace

from .constants import (
    LAYOUT_FAMILY_MAXRECTS,
    LAYOUT_FAMILY_GUILLOTINE,
    LAYOUT_FAMILY_STRIP,
    CNC_LAYOUT_STRATEGY_OPTIONS,
    SAW_LAYOUT_STRATEGY_OPTIONS,
    GENERATED_REMNANT_MIN_LENGTH_MM,
    GENERATED_REMNANT_MIN_WIDTH_MM,
    GENERATED_REMNANT_MIN_AREA_MM2,
    GENERATED_REMNANT_LABEL_PREFIX,
    LAYOUT_SHEET_GAP_MM,
    LAYOUT_GROUP_GAP_MM,
    DEFAULT_THICKNESS_MATCH_TOLERANCE_MM,
    DIMENSION_EQUALITY_TOLERANCE_MM,
    PART_LABEL_PATTERN,
)
from .models import (
    CutProcessProfile,
    SheetStockPiece,
    LayoutPlacement,
    LayoutCutStep,
    LayoutSheet,
    GeneratedRemnant,
)
from .metadata import (
    normalize_cut_method,
    normalize_cnc_layout_strategy,
    normalize_cnc_nesting_mode,
    normalize_saw_layout_strategy,
    normalize_grain_direction,
    get_sheet_settings,
    _validate_sheet_settings,
    _normalize_stock_label,
    _format_thickness_label,
    _strategy_candidates_for_cut_method,
    _settings_with_layout_strategy,
    _format_mm,
    _material_value,
    _format_percent,
)
from .parts import group_parts, _base_label_for_sort
from .edge_compensation import apply_cut_compensation
from .freecad_utils import ensure_document
from .edge_band import (
    _object_occurrence_edge_band_map,
    _part_is_square,
    _part_edge_band_flags_dict,
    _normalize_occurrence_edge_band_flags,
)
from .shape_optimizer import (
    SHAPE_LAYOUT_SUFFIX,
    placement_actual_area_mm2,
    optimize_layout_sheet_shapes,
)

def get_cut_process_profile(cut_method, settings, strategy_override=None):
    normalized_cut_method = normalize_cut_method(cut_method)
    if normalized_cut_method == "CNC":
        strategy_name = normalize_cnc_layout_strategy(
            strategy_override if strategy_override is not None else settings.cnc_layout_strategy
        )
        fitness_name = "BSSF"
        if strategy_name == "MaxRects - BAF":
            fitness_name = "BAF"
        elif strategy_name == "MaxRects - BLSF":
            fitness_name = "BLSF"
        return CutProcessProfile(
            name="CNC",
            kerf_mm=settings.cnc_kerf_mm,
            technical_margin_mm=settings.cnc_tech_margin_mm,
            layout_family=LAYOUT_FAMILY_MAXRECTS,
            strategy_name=strategy_name if strategy_name != "Auto" else "MaxRects - BSSF",
            fitness_name=fitness_name,
            strip_layout=False,
        )
    if normalized_cut_method == "Seccionadora":
        strategy_name = normalize_saw_layout_strategy(
            strategy_override if strategy_override is not None else settings.saw_layout_strategy
        )
        if strategy_name == "Faixas classicas":
            return CutProcessProfile(
                name="Serra",
                kerf_mm=settings.saw_kerf_mm,
                technical_margin_mm=settings.saw_tech_margin_mm,
                layout_family=LAYOUT_FAMILY_STRIP,
                strategy_name=strategy_name,
                fitness_name="Faixas",
                strip_layout=True,
            )
        split_name = "LAS" if strategy_name == "Guilhotina - BSSF LAS" else "SAS"
        return CutProcessProfile(
            name="Serra",
            kerf_mm=settings.saw_kerf_mm,
            technical_margin_mm=settings.saw_tech_margin_mm,
            layout_family=LAYOUT_FAMILY_GUILLOTINE,
            strategy_name=strategy_name if strategy_name != "Auto" else "Guilhotina - BSSF SAS",
            fitness_name="BSSF",
            split_name=split_name,
            strip_layout=False,
        )
    return CutProcessProfile(
        name="Padrao",
        kerf_mm=0.0,
        technical_margin_mm=0.0,
        layout_family=LAYOUT_FAMILY_MAXRECTS,
        strategy_name="Padrao",
        fitness_name="BSSF",
        strip_layout=False,
    )


def _effective_margin_mm(settings, cut_method):
    profile = get_cut_process_profile(cut_method, settings)
    return settings.margin_mm + profile.technical_margin_mm


def _effective_spacing_mm(settings, cut_method):
    profile = get_cut_process_profile(cut_method, settings)
    return max(settings.spacing_mm, profile.kerf_mm)


def _layout_mode_label(cut_method, settings, layout_strategy=None):
    if SHAPE_LAYOUT_SUFFIX.strip() in str(layout_strategy or ""):
        return "Livre por formas"
    profile = get_cut_process_profile(cut_method, settings, layout_strategy)
    if profile.layout_family == LAYOUT_FAMILY_STRIP:
        return "Faixas"
    if profile.layout_family == LAYOUT_FAMILY_GUILLOTINE:
        return "Guilhotinado"
    return "Livre"



def _sheet_usable_bounds(length_mm, width_mm, settings, cut_method="Auto"):
    effective_margin_mm = _effective_margin_mm(settings, cut_method)
    return (
        effective_margin_mm,
        effective_margin_mm,
        length_mm - effective_margin_mm,
        width_mm - effective_margin_mm,
    )


def _sheet_usable_area_mm2(length_mm, width_mm, settings, cut_method="Auto"):
    effective_margin_mm = _effective_margin_mm(settings, cut_method)
    usable_length = length_mm - (2 * effective_margin_mm)
    usable_width = width_mm - (2 * effective_margin_mm)
    return usable_length * usable_width


def _stock_piece_display_name(stock_piece):
    return (
        f"{_normalize_stock_label(stock_piece.label, stock_piece.kind)} "
        f"({_format_mm(stock_piece.length_mm)} x {_format_mm(stock_piece.width_mm)} x "
        f"{_format_thickness_label(stock_piece.thickness_mm)})"
    )


def _layout_sheet_display_name(layout_sheet):
    return _stock_piece_display_name(
        SheetStockPiece(
            label=layout_sheet.source_label,
            length_mm=layout_sheet.source_length_mm,
            width_mm=layout_sheet.source_width_mm,
            thickness_mm=layout_sheet.source_thickness_mm,
            material=layout_sheet.material,
            kind=layout_sheet.source_kind,
            is_full_sheet=True,
        )
    )


def _layout_sheet_used_area_mm2(layout_sheet):
    return sum(placement_actual_area_mm2(placement) for placement in layout_sheet.placements)


def _layout_sheet_utilization_ratio(layout_sheet, settings):
    usable_area_mm2 = _sheet_usable_area_mm2(
        layout_sheet.source_length_mm,
        layout_sheet.source_width_mm,
        settings,
        layout_sheet.cut_method,
    )
    used_area_mm2 = _layout_sheet_used_area_mm2(layout_sheet)
    return (used_area_mm2 / usable_area_mm2) if usable_area_mm2 else 0.0


def _layout_sheet_key(layout_sheet):
    return (
        layout_sheet.group_id,
        layout_sheet.sheet_index,
        layout_sheet.source_label,
        layout_sheet.source_kind,
    )


def _layout_sheet_usable_rect(layout_sheet, settings):
    min_x_mm, min_y_mm, max_x_mm, max_y_mm = _sheet_usable_bounds(
        layout_sheet.source_length_mm,
        layout_sheet.source_width_mm,
        settings,
        layout_sheet.cut_method,
    )
    return {
        "x_mm": min_x_mm,
        "y_mm": min_y_mm,
        "length_mm": max_x_mm - min_x_mm,
        "width_mm": max_y_mm - min_y_mm,
    }


def _layout_sheet_spacing_mm(layout_sheet, settings):
    return _effective_spacing_mm(settings, layout_sheet.cut_method)


def _placement_blocked_rectangle(placement, layout_sheet, settings):
    usable_rect = _layout_sheet_usable_rect(layout_sheet, settings)
    max_x_mm = usable_rect["x_mm"] + usable_rect["length_mm"]
    max_y_mm = usable_rect["y_mm"] + usable_rect["width_mm"]
    spacing_mm = _layout_sheet_spacing_mm(layout_sheet, settings)
    blocked_length_mm = min(max_x_mm - placement.x_mm, placement.placed_length_mm + spacing_mm)
    blocked_width_mm = min(max_y_mm - placement.y_mm, placement.placed_width_mm + spacing_mm)
    return {
        "x_mm": placement.x_mm,
        "y_mm": placement.y_mm,
        "length_mm": blocked_length_mm,
        "width_mm": blocked_width_mm,
    }


def _rectangles_touch_or_overlap(start_a_mm, size_a_mm, start_b_mm, size_b_mm):
    end_a_mm = start_a_mm + size_a_mm
    end_b_mm = start_b_mm + size_b_mm
    return not (
        end_a_mm < start_b_mm - DIMENSION_EQUALITY_TOLERANCE_MM
        or end_b_mm < start_a_mm - DIMENSION_EQUALITY_TOLERANCE_MM
    )


def _merge_adjacent_rectangles(rectangles):
    merged_rectangles = list(rectangles)
    changed = True
    while changed:
        changed = False
        for left_index, left_rect in enumerate(merged_rectangles):
            if changed:
                break
            for right_index in range(left_index + 1, len(merged_rectangles)):
                right_rect = merged_rectangles[right_index]
                same_y = abs(left_rect["y_mm"] - right_rect["y_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
                same_width = (
                    abs(left_rect["width_mm"] - right_rect["width_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
                )
                same_x = abs(left_rect["x_mm"] - right_rect["x_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
                same_length = (
                    abs(left_rect["length_mm"] - right_rect["length_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
                )

                if same_y and same_width and _rectangles_touch_or_overlap(
                    left_rect["x_mm"],
                    left_rect["length_mm"],
                    right_rect["x_mm"],
                    right_rect["length_mm"],
                ):
                    new_x_mm = min(left_rect["x_mm"], right_rect["x_mm"])
                    new_right_mm = max(
                        left_rect["x_mm"] + left_rect["length_mm"],
                        right_rect["x_mm"] + right_rect["length_mm"],
                    )
                    merged_rectangles[left_index] = {
                        "x_mm": new_x_mm,
                        "y_mm": left_rect["y_mm"],
                        "length_mm": new_right_mm - new_x_mm,
                        "width_mm": left_rect["width_mm"],
                    }
                    merged_rectangles.pop(right_index)
                    changed = True
                    break

                if same_x and same_length and _rectangles_touch_or_overlap(
                    left_rect["y_mm"],
                    left_rect["width_mm"],
                    right_rect["y_mm"],
                    right_rect["width_mm"],
                ):
                    new_y_mm = min(left_rect["y_mm"], right_rect["y_mm"])
                    new_top_mm = max(
                        left_rect["y_mm"] + left_rect["width_mm"],
                        right_rect["y_mm"] + right_rect["width_mm"],
                    )
                    merged_rectangles[left_index] = {
                        "x_mm": left_rect["x_mm"],
                        "y_mm": new_y_mm,
                        "length_mm": left_rect["length_mm"],
                        "width_mm": new_top_mm - new_y_mm,
                    }
                    merged_rectangles.pop(right_index)
                    changed = True
                    break
    return _prune_free_rectangles(merged_rectangles)


def _build_layout_sheet_free_rectangles(layout_sheet, settings):
    usable_rect = _layout_sheet_usable_rect(layout_sheet, settings)
    free_rectangles = [usable_rect]

    for placement in sorted(
        layout_sheet.placements,
        key=lambda current: (current.y_mm, current.x_mm, current.placed_width_mm, current.placed_length_mm),
    ):
        blocked_rect = _placement_blocked_rectangle(placement, layout_sheet, settings)
        free_rectangles = _split_free_rectangles(
            free_rectangles,
            blocked_rect["x_mm"],
            blocked_rect["y_mm"],
            blocked_rect["length_mm"],
            blocked_rect["width_mm"],
        )

    return _merge_adjacent_rectangles(free_rectangles)


def _generated_remnant_is_useful(rect):
    return (
        rect["length_mm"] >= GENERATED_REMNANT_MIN_LENGTH_MM
        and rect["width_mm"] >= GENERATED_REMNANT_MIN_WIDTH_MM
        and (rect["length_mm"] * rect["width_mm"]) >= GENERATED_REMNANT_MIN_AREA_MM2
    )


def collect_generated_remnants(layout_sheets, settings):
    generated_remnants = []

    for layout_sheet in layout_sheets:
        free_rectangles = _build_layout_sheet_free_rectangles(layout_sheet, settings)
        remnant_index = 1
        for rect in sorted(
            free_rectangles,
            key=lambda current: (
                -(current["length_mm"] * current["width_mm"]),
                current["y_mm"],
                current["x_mm"],
            ),
        ):
            if not _generated_remnant_is_useful(rect):
                continue
            generated_remnants.append(
                GeneratedRemnant(
                    group_id=layout_sheet.group_id,
                    sheet_index=layout_sheet.sheet_index,
                    source_label=layout_sheet.source_label,
                    source_kind=layout_sheet.source_kind,
                    label=f"Retalho {remnant_index:02d}",
                    x_mm=rect["x_mm"],
                    y_mm=rect["y_mm"],
                    length_mm=rect["length_mm"],
                    width_mm=rect["width_mm"],
                    area_mm2=rect["length_mm"] * rect["width_mm"],
                    material=layout_sheet.material,
                    thickness_mm=layout_sheet.source_thickness_mm,
                    cut_method=layout_sheet.cut_method,
                    layout_strategy=layout_sheet.layout_strategy,
                )
            )
            remnant_index += 1

    return generated_remnants


def layout_sheet_utilization_lines(layout_sheets, settings):
    return [
        f"{layout_sheet.group_id} / {layout_sheet.source_label}: "
        f"{_format_percent(_layout_sheet_utilization_ratio(layout_sheet, settings))}%"
        for layout_sheet in layout_sheets
    ]


def _cut_kind_for_orientation(orientation):
    return "Longitudinal" if orientation == "Horizontal" else "Transversal"


def _cut_phase_info(cut_step):
    description = (cut_step.description or "").strip().lower()
    if description.startswith("separar faixa"):
        return 0, "Abrir faixa"
    if description.startswith("separar bloco"):
        return 0, "Abrir bloco"
    if description.startswith("finalizar bloco"):
        return 1, "Finalizar bloco"
    if cut_step.target_part_id:
        return 2, "Separar peca"
    return 3, "Corte complementar"


def _cut_phase_rank(cut_step):
    return _cut_phase_info(cut_step)[0]


def _cut_phase_label(cut_step):
    return _cut_phase_info(cut_step)[1]


def _sequence_zone_display_label(sequence_zone, zone_aliases=None):
    normalized = str(sequence_zone or "").strip()
    if not normalized:
        return ""
    if zone_aliases and normalized in zone_aliases:
        return zone_aliases[normalized]
    if normalized.startswith("faixa_"):
        return f"Faixa {normalized.split('_', 1)[1]}"
    return normalized


def _layout_sheet_sequence_zone_labels(layout_sheet):
    aliases = {}
    next_block_index = 1
    for cut_step in layout_sheet.cut_steps:
        normalized = str(cut_step.sequence_zone or "").strip()
        if not normalized or normalized in aliases:
            continue
        if normalized.startswith("faixa_"):
            aliases[normalized] = _sequence_zone_display_label(normalized)
            continue
        aliases[normalized] = f"Bloco {next_block_index:02d}"
        next_block_index += 1
    return aliases


def _cut_phase_style(cut_step):
    phase_label = _cut_phase_label(cut_step)
    if phase_label in {"Abrir faixa", "Abrir bloco"}:
        return {
            "line_color": (0.88, 0.29, 0.18),
            "line_width": 4.0,
            "draw_style": "Solid",
        }
    if phase_label == "Finalizar bloco":
        return {
            "line_color": (0.94, 0.58, 0.18),
            "line_width": 2.8,
            "draw_style": "Dashdot",
        }
    if phase_label == "Separar peca":
        return {
            "line_color": (0.12, 0.44, 0.63),
            "line_width": 1.8,
            "draw_style": "Dotted",
        }
    return {
        "line_color": (0.44, 0.46, 0.58),
        "line_width": 2.0,
        "draw_style": "Dashed",
    }


def _cut_line_color(cut_step):
    return _cut_phase_style(cut_step)["line_color"]


def _cut_line_width(cut_step):
    return _cut_phase_style(cut_step)["line_width"]


def _cut_draw_style(cut_step):
    return _cut_phase_style(cut_step)["draw_style"]


def _append_cut_step(
    layout_sheet,
    orientation,
    start_x_mm,
    start_y_mm,
    end_x_mm,
    end_y_mm,
    position_mm,
    target_part_id="",
    description="",
    sequence_zone="",
):
    span_mm = abs(end_x_mm - start_x_mm) if orientation == "Horizontal" else abs(end_y_mm - start_y_mm)
    if span_mm <= DIMENSION_EQUALITY_TOLERANCE_MM:
        return None

    cut_step = LayoutCutStep(
        step_index=len(layout_sheet.cut_steps) + 1,
        orientation=orientation,
        cut_kind=_cut_kind_for_orientation(orientation),
        start_x_mm=float(start_x_mm),
        start_y_mm=float(start_y_mm),
        end_x_mm=float(end_x_mm),
        end_y_mm=float(end_y_mm),
        position_mm=float(position_mm),
        span_mm=float(span_mm),
        target_part_id=target_part_id or "",
        description=description or "",
        sequence_zone=sequence_zone or "",
    )
    layout_sheet.cut_steps.append(cut_step)
    return cut_step


def _build_strip_cut_steps(layout_sheet, settings):
    layout_sheet.cut_steps = []
    if not layout_sheet.placements:
        return

    min_x, min_y, max_x, max_y = _sheet_usable_bounds(
        layout_sheet.source_length_mm,
        layout_sheet.source_width_mm,
        settings,
        layout_sheet.cut_method,
    )
    spacing_mm = _effective_spacing_mm(settings, layout_sheet.cut_method)
    cut_offset_mm = spacing_mm / 2.0
    rows = []

    for placement in sorted(
        layout_sheet.placements,
        key=lambda current: (current.y_mm, current.x_mm, -current.placed_width_mm, -current.placed_length_mm),
    ):
        if rows and abs(placement.y_mm - rows[-1]["y_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM:
            rows[-1]["placements"].append(placement)
            rows[-1]["height_mm"] = max(rows[-1]["height_mm"], placement.placed_width_mm)
        else:
            rows.append(
                {
                    "y_mm": placement.y_mm,
                    "height_mm": placement.placed_width_mm,
                    "placements": [placement],
                }
            )

    next_row_min_y = min_y
    total_rows = len(rows)

    for row_index, row in enumerate(rows, start=1):
        row_placements = sorted(row["placements"], key=lambda current: (current.x_mm, current.y_mm))
        sequence_zone = f"faixa_{row_index:02d}"
        cut_y_mm = row["y_mm"] + row["height_mm"] + cut_offset_mm
        row_min_cut_y_mm = next_row_min_y
        row_max_cut_y_mm = max_y
        if cut_y_mm < max_y - DIMENSION_EQUALITY_TOLERANCE_MM:
            row_max_cut_y_mm = cut_y_mm
            _append_cut_step(
                layout_sheet,
                "Horizontal",
                min_x,
                cut_y_mm,
                max_x,
                cut_y_mm,
                cut_y_mm,
                description=f"Separar faixa {row_index:02d}",
                sequence_zone=sequence_zone,
            )
            next_row_min_y = cut_y_mm
        elif row_index < total_rows:
            next_row_min_y = row["y_mm"] + row["height_mm"]

        for placement in row_placements:
            cut_x_mm = placement.x_mm + placement.placed_length_mm + cut_offset_mm
            if cut_x_mm >= max_x - DIMENSION_EQUALITY_TOLERANCE_MM:
                continue
            _append_cut_step(
                layout_sheet,
                "Vertical",
                cut_x_mm,
                row_min_cut_y_mm,
                cut_x_mm,
                row_max_cut_y_mm,
                cut_x_mm,
                target_part_id=placement.part.part_id,
                description=(
                    f"Separar {placement.part.part_id} na faixa {row_index:02d}"
                ),
                sequence_zone=sequence_zone,
            )


def _available_cut_rect(min_x_mm, min_y_mm, max_x_mm, max_y_mm):
    return {
        "min_x_mm": float(min_x_mm),
        "min_y_mm": float(min_y_mm),
        "max_x_mm": float(max_x_mm),
        "max_y_mm": float(max_y_mm),
    }


def _available_cut_rect_area_mm2(rect):
    return max(0.0, rect["max_x_mm"] - rect["min_x_mm"]) * max(0.0, rect["max_y_mm"] - rect["min_y_mm"])


def _cut_rect_sequence_zone(rect):
    return (
        f"rect_{rect['min_x_mm']:.3f}_{rect['min_y_mm']:.3f}_"
        f"{rect['max_x_mm']:.3f}_{rect['max_y_mm']:.3f}"
    )


def _rect_contains_point(rect, x_mm, y_mm):
    return (
        rect["min_x_mm"] - DIMENSION_EQUALITY_TOLERANCE_MM <= x_mm <= rect["max_x_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
        and rect["min_y_mm"] - DIMENSION_EQUALITY_TOLERANCE_MM <= y_mm <= rect["max_y_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
    )


def _rect_side_matches_sheet_boundary(rect, side, sheet_bounds):
    if side == "top":
        return abs(rect["max_y_mm"] - sheet_bounds["max_y_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
    if side == "bottom":
        return abs(rect["min_y_mm"] - sheet_bounds["min_y_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
    if side == "left":
        return abs(rect["min_x_mm"] - sheet_bounds["min_x_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
    if side == "right":
        return abs(rect["max_x_mm"] - sheet_bounds["max_x_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
    return False


def _cut_matches_available_rect(cut_step, rect):
    if cut_step.orientation == "Vertical":
        start_y_mm = min(cut_step.start_y_mm, cut_step.end_y_mm)
        end_y_mm = max(cut_step.start_y_mm, cut_step.end_y_mm)
        return (
            cut_step.position_mm > rect["min_x_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
            and cut_step.position_mm < rect["max_x_mm"] - DIMENSION_EQUALITY_TOLERANCE_MM
            and abs(start_y_mm - rect["min_y_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
            and abs(end_y_mm - rect["max_y_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
        )

    start_x_mm = min(cut_step.start_x_mm, cut_step.end_x_mm)
    end_x_mm = max(cut_step.start_x_mm, cut_step.end_x_mm)
    return (
        cut_step.position_mm > rect["min_y_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
        and cut_step.position_mm < rect["max_y_mm"] - DIMENSION_EQUALITY_TOLERANCE_MM
        and abs(start_x_mm - rect["min_x_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
        and abs(end_x_mm - rect["max_x_mm"]) <= DIMENSION_EQUALITY_TOLERANCE_MM
    )


def _split_available_rect_by_cut(rect, cut_step):
    rectangles = []
    if cut_step.orientation == "Vertical":
        rectangles.append(
            _available_cut_rect(
                rect["min_x_mm"],
                rect["min_y_mm"],
                cut_step.position_mm,
                rect["max_y_mm"],
            )
        )
        rectangles.append(
            _available_cut_rect(
                cut_step.position_mm,
                rect["min_y_mm"],
                rect["max_x_mm"],
                rect["max_y_mm"],
            )
        )
    else:
        rectangles.append(
            _available_cut_rect(
                rect["min_x_mm"],
                rect["min_y_mm"],
                rect["max_x_mm"],
                cut_step.position_mm,
            )
        )
        rectangles.append(
            _available_cut_rect(
                rect["min_x_mm"],
                cut_step.position_mm,
                rect["max_x_mm"],
                rect["max_y_mm"],
            )
        )

    return [
        current
        for current in rectangles
        if _available_cut_rect_area_mm2(current) > DIMENSION_EQUALITY_TOLERANCE_MM
    ]


def _cut_execution_access(cut_step, rect):
    if cut_step.orientation == "Vertical":
        distance_left_mm = cut_step.position_mm - rect["min_x_mm"]
        distance_right_mm = rect["max_x_mm"] - cut_step.position_mm
        if distance_left_mm <= distance_right_mm:
            return "left", distance_left_mm
        return "right", distance_right_mm

    distance_bottom_mm = cut_step.position_mm - rect["min_y_mm"]
    distance_top_mm = rect["max_y_mm"] - cut_step.position_mm
    if distance_top_mm <= distance_bottom_mm:
        return "top", distance_top_mm
    return "bottom", distance_bottom_mm


def _cut_execution_score(cut_step, rect, sheet_bounds):
    access_side, access_distance_mm = _cut_execution_access(cut_step, rect)
    touches_sheet_outer_side = _rect_side_matches_sheet_boundary(rect, access_side, sheet_bounds)
    return (
        _cut_phase_rank(cut_step),
        not touches_sheet_outer_side,
        round(access_distance_mm, 4),
        -round(_available_cut_rect_area_mm2(rect), 4),
        -round(cut_step.span_mm, 4),
        cut_step.step_index,
    )


def _cut_followup_score(cut_step, rect, sheet_bounds, previous_choice):
    access_side, access_distance_mm = _cut_execution_access(cut_step, rect)
    touches_sheet_outer_side = _rect_side_matches_sheet_boundary(rect, access_side, sheet_bounds)
    phase_rank = _cut_phase_rank(cut_step)

    if previous_choice is None:
        return (
            phase_rank,
            not touches_sheet_outer_side,
            round(access_distance_mm, 4),
            -round(_available_cut_rect_area_mm2(rect), 4),
            -round(cut_step.span_mm, 4),
            cut_step.step_index,
        )

    previous_cut = previous_choice["cut_step"]
    previous_access_side = previous_choice["access_side"]
    previous_focus_rect = previous_choice.get("focus_rect")
    previous_phase_rank = _cut_phase_rank(previous_cut)
    previous_position_mm = previous_cut.position_mm
    same_orientation = cut_step.orientation == previous_cut.orientation
    same_side = access_side == previous_access_side
    same_zone = bool(cut_step.sequence_zone) and cut_step.sequence_zone == previous_cut.sequence_zone
    same_target = bool(cut_step.target_part_id) and cut_step.target_part_id == previous_cut.target_part_id
    continues_focus_rect = previous_focus_rect is not None and rect is previous_focus_rect
    phase_jump_rank = max(0, phase_rank - previous_phase_rank)
    position_jump_mm = abs(cut_step.position_mm - previous_position_mm)

    return (
        not continues_focus_rect,
        phase_jump_rank if continues_focus_rect else phase_rank,
        not same_zone,
        not same_target,
        not same_orientation,
        not same_side,
        not touches_sheet_outer_side,
        round(position_jump_mm, 4),
        round(access_distance_mm, 4),
        -round(_available_cut_rect_area_mm2(rect), 4),
        -round(cut_step.span_mm, 4),
        cut_step.step_index,
    )


def _find_cut_focus_rect(layout_sheet, cut_step, child_rectangles, placement_map):
    if not child_rectangles:
        return None

    phase_label = _cut_phase_label(cut_step)
    placement = placement_map.get(cut_step.target_part_id)
    if placement is not None:
        center_x_mm = placement.x_mm + (placement.placed_length_mm / 2.0)
        center_y_mm = placement.y_mm + (placement.placed_width_mm / 2.0)
        matching_rectangles = []
        for rect in child_rectangles:
            if _rect_contains_point(rect, center_x_mm, center_y_mm):
                matching_rectangles.append(rect)

        if matching_rectangles:
            if phase_label == "Separar peca" and str(cut_step.sequence_zone or "").startswith("faixa_"):
                non_matching_rectangles = [
                    rect for rect in child_rectangles if rect not in matching_rectangles
                ]
                if non_matching_rectangles:
                    return max(
                        non_matching_rectangles,
                        key=lambda rect: (
                            _available_cut_rect_area_mm2(rect),
                            -rect["min_y_mm"],
                            -rect["min_x_mm"],
                        ),
                    )
            return matching_rectangles[0]

    if phase_label in {"Abrir faixa", "Abrir bloco", "Finalizar bloco", "Separar peca"}:
        return min(
            child_rectangles,
            key=lambda rect: (
                _available_cut_rect_area_mm2(rect),
                rect["min_y_mm"],
                rect["min_x_mm"],
            ),
        )

    return max(
        child_rectangles,
        key=lambda rect: (
            _available_cut_rect_area_mm2(rect),
            -rect["min_y_mm"],
            -rect["min_x_mm"],
        ),
    )


def _resequence_sheet_cut_steps(layout_sheet, settings):
    if len(layout_sheet.cut_steps) < 2:
        return

    min_x_mm, min_y_mm, max_x_mm, max_y_mm = _sheet_usable_bounds(
        layout_sheet.source_length_mm,
        layout_sheet.source_width_mm,
        settings,
        layout_sheet.cut_method,
    )
    sheet_bounds = _available_cut_rect(min_x_mm, min_y_mm, max_x_mm, max_y_mm)
    available_rectangles = [sheet_bounds]
    remaining_steps = list(layout_sheet.cut_steps)
    ordered_steps = []
    previous_choice = None
    placement_map = {
        placement.part.part_id: placement
        for placement in layout_sheet.placements
        if getattr(placement, "part", None) is not None
    }

    while remaining_steps:
        executable_candidates = []
        for cut_step in remaining_steps:
            for rect_index, rect in enumerate(available_rectangles):
                if not _cut_matches_available_rect(cut_step, rect):
                    continue
                access_side, _access_distance_mm = _cut_execution_access(cut_step, rect)
                executable_candidates.append(
                    (
                        _cut_followup_score(cut_step, rect, sheet_bounds, previous_choice),
                        rect_index,
                        cut_step,
                        access_side,
                    )
                )

        if not executable_candidates:
            break

        executable_candidates.sort(key=lambda item: item[0])
        _score, rect_index, cut_step, access_side = executable_candidates[0]
        rect = available_rectangles.pop(rect_index)
        child_rectangles = _split_available_rect_by_cut(rect, cut_step)
        available_rectangles.extend(child_rectangles)
        ordered_steps.append(cut_step)
        remaining_steps.remove(cut_step)
        previous_choice = {
            "cut_step": cut_step,
            "access_side": access_side,
            "focus_rect": _find_cut_focus_rect(
                layout_sheet,
                cut_step,
                child_rectangles,
                placement_map,
            ),
        }

    ordered_steps.extend(sorted(remaining_steps, key=lambda current: current.step_index))
    layout_sheet.cut_steps = [
        replace(cut_step, step_index=step_index)
        for step_index, cut_step in enumerate(ordered_steps, start=1)
    ]


def layout_overall_utilization_ratio(layout_sheets, settings):
    total_used_area_mm2 = sum(_layout_sheet_used_area_mm2(layout_sheet) for layout_sheet in layout_sheets)
    total_usable_area_mm2 = sum(
        _sheet_usable_area_mm2(
            layout_sheet.source_length_mm,
            layout_sheet.source_width_mm,
            settings,
            layout_sheet.cut_method,
        )
        for layout_sheet in layout_sheets
    )
    return (total_used_area_mm2 / total_usable_area_mm2) if total_usable_area_mm2 else 0.0


def _part_orientation_options(part, length_mm, width_mm, settings):
    effective_margin_mm = _effective_margin_mm(settings, part.cut_method)
    _, _, max_x, max_y = _sheet_usable_bounds(length_mm, width_mm, settings, part.cut_method)
    usable_length = max_x - effective_margin_mm
    usable_width = max_y - effective_margin_mm

    non_rotated_option = None
    if part.length_mm <= usable_length and part.width_mm <= usable_width:
        non_rotated_option = (False, part.length_mm, part.width_mm)

    rotated_option = None
    if part.width_mm <= usable_length and part.length_mm <= usable_width:
        rotated_option = (True, part.width_mm, part.length_mm)

    options = []
    grain_direction = normalize_grain_direction(part.grain_direction)
    is_square = _part_is_square(part)

    # Material com textura (veio real): sobrepõe grain_direction pelo grain_rotated da peça.
    # Materiais sem textura ficam com o grain_direction original (normalmente "Livre").
    try:
        from panelnest.materials import get_by_name as _get_mat
        _mat = _get_mat(part.material) if part.material else None
        if _mat is not None and _mat.texture_id:
            grain_direction = "Largura da chapa" if getattr(part, "grain_rotated", False) else "Comprimento da chapa"
    except Exception:
        pass

    if grain_direction == "Livre":
        if non_rotated_option is not None:
            options.append(non_rotated_option)
        if part.allow_rotation and rotated_option is not None and rotated_option not in options:
            options.append(rotated_option)
    elif grain_direction == "Comprimento da chapa":
        if non_rotated_option is not None:
            options.append(non_rotated_option)
        elif is_square and rotated_option is not None:
            options.append(rotated_option)
    elif grain_direction == "Largura da chapa":
        if rotated_option is not None and (part.allow_rotation or is_square):
            options.append(rotated_option)
        elif is_square and non_rotated_option is not None:
            options.append(non_rotated_option)

    if not options:
        raise ValueError(_part_orientation_error_message(part))

    return options


def _part_orientation_error_message(part):
    part_name = PART_LABEL_PATTERN.sub("", part.label)
    grain_direction = normalize_grain_direction(part.grain_direction)

    if grain_direction == "Largura da chapa":
        if not part.allow_rotation and not _part_is_square(part):
            return (
                f"A peca {part.part_id} ({part_name}) nao cabe mantendo o veio/fibra "
                "na largura da chapa, porque a rotacao desta peca esta desativada."
            )
        return (
            f"A peca {part.part_id} ({part_name}) nao cabe mantendo o veio/fibra "
            "na largura da chapa."
        )

    if grain_direction == "Comprimento da chapa":
        return (
            f"A peca {part.part_id} ({part_name}) nao cabe mantendo o veio/fibra "
            "no comprimento da chapa."
        )

    if not part.allow_rotation:
        return (
            f"A peca {part.part_id} ({part_name}) nao cabe na area util da chapa "
            "configurada sem rotacionar, e a rotacao desta peca esta desativada."
        )

    return f"A peca {part.part_id} ({part_name}) nao cabe na area util da chapa configurada."


def _new_layout_sheet(group, sheet_index, stock_piece, settings):
    profile = get_cut_process_profile(group["cut_method"], settings)
    sheet = LayoutSheet(
        group_id=group["group_id"],
        sheet_index=sheet_index,
        material=group["material"],
        thickness_mm=group["thickness_mm"],
        cut_method=group["cut_method"],
        layout_strategy=profile.strategy_name,
        source_label=stock_piece.label,
        source_kind=stock_piece.kind,
        source_length_mm=stock_piece.length_mm,
        source_width_mm=stock_piece.width_mm,
        source_thickness_mm=stock_piece.thickness_mm if stock_piece.thickness_mm > 0 else group["thickness_mm"],
    )
    sheet._cost_per_sheet = float(getattr(stock_piece, "cost_per_sheet", 0.0) or 0.0)
    return sheet


def _build_available_stock_sheets(settings):
    stock_pieces = []

    sorted_remnants = sorted(
        settings.remnants,
        key=lambda item: (item.length_mm * item.width_mm, item.label.casefold()),
    )
    for remnant in sorted_remnants:
        for index in range(1, remnant.quantity + 1):
            label = remnant.label if remnant.quantity == 1 else f"{remnant.label} {index:02d}"
            stock_pieces.append(
                SheetStockPiece(
                    label=label,
                    length_mm=remnant.length_mm,
                    width_mm=remnant.width_mm,
                    thickness_mm=remnant.thickness_mm,
                    material=remnant.material,
                    quantity=1,
                    kind="Retalho",
                    is_full_sheet=False,
                    cost_per_sheet=float(getattr(remnant, "cost_per_sheet", 0.0) or 0.0),
                )
            )

    full_sheet_cost = float(getattr(settings, "full_sheet_cost", 0.0) or 0.0)
    for index in range(1, settings.full_sheet_count + 1):
        label = "Chapa inteira" if settings.full_sheet_count == 1 else f"Chapa inteira {index:02d}"
        stock_pieces.append(
            SheetStockPiece(
                label=label,
                length_mm=settings.length_mm,
                width_mm=settings.width_mm,
                thickness_mm=settings.full_sheet_thickness_mm,
                material=settings.full_sheet_material,
                quantity=1,
                kind="Chapa inteira",
                is_full_sheet=True,
                cost_per_sheet=full_sheet_cost,
            )
        )

    return stock_pieces


def _extra_full_sheet_stock_piece(extra_index, settings, group):
    label = f"Chapa extra {extra_index:02d}"
    return SheetStockPiece(
        label=label,
        length_mm=settings.length_mm,
        width_mm=settings.width_mm,
        thickness_mm=settings.full_sheet_thickness_mm or group["thickness_mm"],
        material=settings.full_sheet_material or group["material"],
        quantity=1,
        kind="Chapa extra",
        is_full_sheet=True,
    )


def _thickness_matches(stock_piece, group_thickness_mm):
    return stock_piece.thickness_mm <= 0 or abs(stock_piece.thickness_mm - group_thickness_mm) <= DEFAULT_THICKNESS_MATCH_TOLERANCE_MM


_GENERIC_MATERIALS = {"", "generico", "generic", "sem material", "without material"}


def _material_matches(stock_piece, group_material):
    stock_material = _material_value(stock_piece.material)
    target_material = _material_value(group_material)
    # Retalho sem material → aceita qualquer grupo
    if not stock_material or stock_material.casefold() in _GENERIC_MATERIALS:
        return True
    # Grupo sem material definido → aceita qualquer retalho
    if not target_material or target_material.casefold() in _GENERIC_MATERIALS:
        return True
    return stock_material.casefold() == target_material.casefold()


def _thickness_match_score(stock_piece, group_thickness_mm):
    return 1 if stock_piece.thickness_mm <= 0 else 0


def _material_match_score(stock_piece, group_material):
    stock_material = _material_value(stock_piece.material)
    target_material = _material_value(group_material)
    # Score 0 = match exato ou grupo sem material (qualquer retalho serve)
    if not stock_material or stock_material.casefold() in _GENERIC_MATERIALS:
        return 0
    if not target_material or target_material.casefold() in _GENERIC_MATERIALS:
        return 0
    return 0 if stock_material.casefold() == target_material.casefold() else 1


def _new_sheet_state(group, sheet_index, stock_piece, settings):
    min_x, min_y, max_x, max_y = _sheet_usable_bounds(
        stock_piece.length_mm,
        stock_piece.width_mm,
        settings,
        group["cut_method"],
    )
    profile = get_cut_process_profile(group["cut_method"], settings)
    return {
        "layout_sheet": _new_layout_sheet(group, sheet_index, stock_piece, settings),
        "stock_piece": stock_piece,
        "rows": [],
        "free_rectangles": (
            []
            if profile.strip_layout
            else [
                {
                    "x_mm": min_x,
                    "y_mm": min_y,
                    "length_mm": max_x - min_x,
                    "width_mm": max_y - min_y,
                }
            ]
        ),
        "min_x": min_x,
        "min_y": min_y,
        "max_x": max_x,
        "max_y": max_y,
        "next_y_mm": min_y,
    }


def _rectangles_intersect(rect, x_mm, y_mm, length_mm, width_mm):
    rect_right = rect["x_mm"] + rect["length_mm"]
    rect_top = rect["y_mm"] + rect["width_mm"]
    used_right = x_mm + length_mm
    used_top = y_mm + width_mm
    return not (
        used_right <= rect["x_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
        or x_mm >= rect_right - DIMENSION_EQUALITY_TOLERANCE_MM
        or used_top <= rect["y_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
        or y_mm >= rect_top - DIMENSION_EQUALITY_TOLERANCE_MM
    )


def _build_split_rectangles(rect, x_mm, y_mm, length_mm, width_mm):
    rect_right = rect["x_mm"] + rect["length_mm"]
    rect_top = rect["y_mm"] + rect["width_mm"]
    used_right = x_mm + length_mm
    used_top = y_mm + width_mm
    rectangles = []

    if x_mm > rect["x_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM:
        rectangles.append(
            {
                "x_mm": rect["x_mm"],
                "y_mm": rect["y_mm"],
                "length_mm": x_mm - rect["x_mm"],
                "width_mm": rect["width_mm"],
            }
        )
    if used_right < rect_right - DIMENSION_EQUALITY_TOLERANCE_MM:
        rectangles.append(
            {
                "x_mm": used_right,
                "y_mm": rect["y_mm"],
                "length_mm": rect_right - used_right,
                "width_mm": rect["width_mm"],
            }
        )
    if y_mm > rect["y_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM:
        rectangles.append(
            {
                "x_mm": rect["x_mm"],
                "y_mm": rect["y_mm"],
                "length_mm": rect["length_mm"],
                "width_mm": y_mm - rect["y_mm"],
            }
        )
    if used_top < rect_top - DIMENSION_EQUALITY_TOLERANCE_MM:
        rectangles.append(
            {
                "x_mm": rect["x_mm"],
                "y_mm": used_top,
                "length_mm": rect["length_mm"],
                "width_mm": rect_top - used_top,
            }
        )

    return rectangles


def _prune_free_rectangles(rectangles):
    filtered = []
    for rect in rectangles:
        if (
            rect["length_mm"] <= DIMENSION_EQUALITY_TOLERANCE_MM
            or rect["width_mm"] <= DIMENSION_EQUALITY_TOLERANCE_MM
        ):
            continue
        filtered.append(rect)

    pruned = []
    for index, rect in enumerate(filtered):
        rect_right = rect["x_mm"] + rect["length_mm"]
        rect_top = rect["y_mm"] + rect["width_mm"]
        contained = False
        for other_index, other in enumerate(filtered):
            if index == other_index:
                continue
            other_right = other["x_mm"] + other["length_mm"]
            other_top = other["y_mm"] + other["width_mm"]
            if (
                rect["x_mm"] >= other["x_mm"] - DIMENSION_EQUALITY_TOLERANCE_MM
                and rect["y_mm"] >= other["y_mm"] - DIMENSION_EQUALITY_TOLERANCE_MM
                and rect_right <= other_right + DIMENSION_EQUALITY_TOLERANCE_MM
                and rect_top <= other_top + DIMENSION_EQUALITY_TOLERANCE_MM
            ):
                contained = True
                break
        if not contained:
            pruned.append(rect)
    return pruned


def _split_free_rectangles(free_rectangles, x_mm, y_mm, length_mm, width_mm):
    updated_rectangles = []
    for rect in free_rectangles:
        if not _rectangles_intersect(rect, x_mm, y_mm, length_mm, width_mm):
            updated_rectangles.append(rect)
            continue
        updated_rectangles.extend(_build_split_rectangles(rect, x_mm, y_mm, length_mm, width_mm))
    return _prune_free_rectangles(updated_rectangles)


def _rectangle_fitness_score(profile, sheet_position, rect, rect_index, blocked_length_mm, blocked_width_mm):
    remaining_length = rect["length_mm"] - blocked_length_mm
    remaining_width = rect["width_mm"] - blocked_width_mm
    short_side_fit = min(remaining_length, remaining_width)
    long_side_fit = max(remaining_length, remaining_width)
    area_fit = (rect["length_mm"] * rect["width_mm"]) - (blocked_length_mm * blocked_width_mm)

    if profile.fitness_name == "BAF":
        return (
            sheet_position,
            area_fit,
            short_side_fit,
            long_side_fit,
            rect["y_mm"],
            rect["x_mm"],
            rect_index,
        )
    if profile.fitness_name == "BLSF":
        return (
            sheet_position,
            long_side_fit,
            short_side_fit,
            area_fit,
            rect["y_mm"],
            rect["x_mm"],
            rect_index,
        )
    return (
        sheet_position,
        short_side_fit,
        long_side_fit,
        area_fit,
        rect["y_mm"],
        rect["x_mm"],
        rect_index,
    )


def _placement_has_required_spacing(sheet_state, x_mm, y_mm, placed_length_mm, placed_width_mm, spacing_mm):
    for existing in sheet_state["layout_sheet"].placements:
        x_overlap = _intervals_overlap(x_mm, placed_length_mm + spacing_mm, existing.x_mm, existing.placed_length_mm)
        reverse_x_overlap = _intervals_overlap(
            existing.x_mm,
            existing.placed_length_mm + spacing_mm,
            x_mm,
            placed_length_mm,
        )
        y_overlap = _intervals_overlap(y_mm, placed_width_mm + spacing_mm, existing.y_mm, existing.placed_width_mm)
        reverse_y_overlap = _intervals_overlap(
            existing.y_mm,
            existing.placed_width_mm + spacing_mm,
            y_mm,
            placed_width_mm,
        )
        if x_overlap and y_overlap and reverse_x_overlap and reverse_y_overlap:
            return False
    return True


def _select_maxrects_candidate(sheet_states, part, settings):
    best_candidate = None
    spacing_mm = _effective_spacing_mm(settings, part.cut_method)
    profile = get_cut_process_profile(part.cut_method, settings)

    for sheet_position, sheet_state in enumerate(sheet_states):
        try:
            options = _part_orientation_options(
                part,
                sheet_state["stock_piece"].length_mm,
                sheet_state["stock_piece"].width_mm,
                settings,
            )
        except ValueError:
            continue
        for rect_index, rect in enumerate(sheet_state["free_rectangles"]):
            for rotated, placed_length_mm, placed_width_mm in options:
                if (
                    placed_length_mm > rect["length_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
                    or placed_width_mm > rect["width_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
                ):
                    continue

                blocked_length_mm = min(rect["length_mm"], placed_length_mm + spacing_mm)
                blocked_width_mm = min(rect["width_mm"], placed_width_mm + spacing_mm)
                if not _placement_has_required_spacing(
                    sheet_state,
                    rect["x_mm"],
                    rect["y_mm"],
                    placed_length_mm,
                    placed_width_mm,
                    spacing_mm,
                ):
                    continue
                score = _rectangle_fitness_score(
                    profile,
                    sheet_position,
                    rect,
                    rect_index,
                    blocked_length_mm,
                    blocked_width_mm,
                )
                candidate = {
                    "kind": "maxrects_rect",
                    "score": score,
                    "sheet_state": sheet_state,
                    "free_rect": rect,
                    "rect_index": rect_index,
                    "rotated": rotated,
                    "placed_length_mm": placed_length_mm,
                    "placed_width_mm": placed_width_mm,
                    "blocked_length_mm": blocked_length_mm,
                    "blocked_width_mm": blocked_width_mm,
                }
                if best_candidate is None or candidate["score"] < best_candidate["score"]:
                    best_candidate = candidate

    return best_candidate


def _split_guillotine_rectangles(rect, blocked_length_mm, blocked_width_mm, split_name):
    remaining_length = rect["length_mm"] - blocked_length_mm
    remaining_width = rect["width_mm"] - blocked_width_mm
    rectangles = []

    split_vertical = split_name == "LAS"
    if split_name == "SAS":
        split_vertical = remaining_length <= remaining_width

    if split_vertical:
        if remaining_length > DIMENSION_EQUALITY_TOLERANCE_MM:
            rectangles.append(
                {
                    "x_mm": rect["x_mm"] + blocked_length_mm,
                    "y_mm": rect["y_mm"],
                    "length_mm": remaining_length,
                    "width_mm": rect["width_mm"],
                }
            )
        if remaining_width > DIMENSION_EQUALITY_TOLERANCE_MM:
            rectangles.append(
                {
                    "x_mm": rect["x_mm"],
                    "y_mm": rect["y_mm"] + blocked_width_mm,
                    "length_mm": blocked_length_mm,
                    "width_mm": remaining_width,
                }
            )
    else:
        if remaining_width > DIMENSION_EQUALITY_TOLERANCE_MM:
            rectangles.append(
                {
                    "x_mm": rect["x_mm"],
                    "y_mm": rect["y_mm"] + blocked_width_mm,
                    "length_mm": rect["length_mm"],
                    "width_mm": remaining_width,
                }
            )
        if remaining_length > DIMENSION_EQUALITY_TOLERANCE_MM:
            rectangles.append(
                {
                    "x_mm": rect["x_mm"] + blocked_length_mm,
                    "y_mm": rect["y_mm"],
                    "length_mm": remaining_length,
                    "width_mm": blocked_width_mm,
                }
            )
    return _prune_free_rectangles(rectangles)


def _replace_free_rectangle(free_rectangles, rect_index, new_rectangles):
    updated_rectangles = []
    for current_index, current_rect in enumerate(free_rectangles):
        if current_index == rect_index:
            updated_rectangles.extend(new_rectangles)
            continue
        updated_rectangles.append(current_rect)
    return _prune_free_rectangles(updated_rectangles)


def _select_guillotine_candidate(sheet_states, part, settings):
    best_candidate = None
    spacing_mm = _effective_spacing_mm(settings, part.cut_method)
    profile = get_cut_process_profile(part.cut_method, settings)

    for sheet_position, sheet_state in enumerate(sheet_states):
        try:
            options = _part_orientation_options(
                part,
                sheet_state["stock_piece"].length_mm,
                sheet_state["stock_piece"].width_mm,
                settings,
            )
        except ValueError:
            continue
        for rect_index, rect in enumerate(sheet_state["free_rectangles"]):
            for rotated, placed_length_mm, placed_width_mm in options:
                if (
                    placed_length_mm > rect["length_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
                    or placed_width_mm > rect["width_mm"] + DIMENSION_EQUALITY_TOLERANCE_MM
                ):
                    continue
                blocked_length_mm = min(rect["length_mm"], placed_length_mm + spacing_mm)
                blocked_width_mm = min(rect["width_mm"], placed_width_mm + spacing_mm)
                if not _placement_has_required_spacing(
                    sheet_state,
                    rect["x_mm"],
                    rect["y_mm"],
                    placed_length_mm,
                    placed_width_mm,
                    spacing_mm,
                ):
                    continue
                score = _rectangle_fitness_score(
                    profile,
                    sheet_position,
                    rect,
                    rect_index,
                    blocked_length_mm,
                    blocked_width_mm,
                )
                candidate = {
                    "kind": "guillotine_rect",
                    "score": score,
                    "sheet_state": sheet_state,
                    "free_rect": rect,
                    "rect_index": rect_index,
                    "rotated": rotated,
                    "placed_length_mm": placed_length_mm,
                    "placed_width_mm": placed_width_mm,
                    "blocked_length_mm": blocked_length_mm,
                    "blocked_width_mm": blocked_width_mm,
                    "split_name": profile.split_name or "SAS",
                }
                if best_candidate is None or candidate["score"] < best_candidate["score"]:
                    best_candidate = candidate

    return best_candidate


def _select_sheet_placement_candidate(sheet_states, part, settings):
    profile = get_cut_process_profile(part.cut_method, settings)
    if profile.layout_family == LAYOUT_FAMILY_MAXRECTS:
        return _select_maxrects_candidate(sheet_states, part, settings)
    if profile.layout_family == LAYOUT_FAMILY_GUILLOTINE:
        return _select_guillotine_candidate(sheet_states, part, settings)

    candidate = _select_existing_row_candidate(sheet_states, part, settings)
    if candidate is None:
        candidate = _select_new_row_candidate(sheet_states, part, settings)
    return candidate


def _select_existing_row_candidate(sheet_states, part, settings):
    best_candidate = None
    strip_layout = get_cut_process_profile(part.cut_method, settings).strip_layout

    for sheet_position, sheet_state in enumerate(sheet_states):
        try:
            options = _part_orientation_options(
                part,
                sheet_state["stock_piece"].length_mm,
                sheet_state["stock_piece"].width_mm,
                settings,
            )
        except ValueError:
            continue
        rows = list(enumerate(sheet_state["rows"]))
        if strip_layout and rows:
            rows = [rows[-1]]
        for row_index, row in rows:
            remaining_length = sheet_state["max_x"] - row["x_mm"]
            for rotated, placed_length_mm, placed_width_mm in options:
                if placed_length_mm > remaining_length or placed_width_mm > row["height_mm"]:
                    continue

                score = (
                    sheet_position,
                    remaining_length - placed_length_mm,
                    row["height_mm"] - placed_width_mm,
                    row_index,
                )
                candidate = {
                    "kind": "existing_row",
                    "score": score,
                    "sheet_state": sheet_state,
                    "row": row,
                    "rotated": rotated,
                    "placed_length_mm": placed_length_mm,
                    "placed_width_mm": placed_width_mm,
                }
                if best_candidate is None or candidate["score"] < best_candidate["score"]:
                    best_candidate = candidate

    return best_candidate


def _select_new_row_candidate(sheet_states, part, settings):
    best_candidate = None
    profile = get_cut_process_profile(part.cut_method, settings)

    for sheet_position, sheet_state in enumerate(sheet_states):
        try:
            options = _part_orientation_options(
                part,
                sheet_state["stock_piece"].length_mm,
                sheet_state["stock_piece"].width_mm,
                settings,
            )
        except ValueError:
            continue
        row_y_mm = sheet_state["next_y_mm"] if sheet_state["rows"] else sheet_state["min_y"]
        for rotated, placed_length_mm, placed_width_mm in options:
            if row_y_mm + placed_width_mm > sheet_state["max_y"]:
                continue

            if profile.strip_layout:
                usable_length_mm = sheet_state["max_x"] - sheet_state["min_x"]
                score = (
                    sheet_position,
                    placed_width_mm,
                    usable_length_mm - placed_length_mm,
                    row_y_mm,
                    -placed_length_mm,
                )
            else:
                score = (
                    sheet_position,
                    sheet_state["max_y"] - (row_y_mm + placed_width_mm),
                    _sheet_usable_area_mm2(
                        sheet_state["stock_piece"].length_mm,
                        sheet_state["stock_piece"].width_mm,
                        settings,
                        sheet_state["layout_sheet"].cut_method,
                    ),
                    placed_width_mm,
                    -placed_length_mm,
                )
            candidate = {
                "kind": "new_row",
                "score": score,
                "sheet_state": sheet_state,
                "row_y_mm": row_y_mm,
                "rotated": rotated,
                "placed_length_mm": placed_length_mm,
                "placed_width_mm": placed_width_mm,
            }
            if best_candidate is None or candidate["score"] < best_candidate["score"]:
                best_candidate = candidate

    return best_candidate


def _select_unopened_sheet_candidate(
    available_stock_sheets,
    part,
    settings,
    group_thickness_mm,
    group_material,
):
    best_candidate = None

    for stock_index, stock_piece in enumerate(available_stock_sheets):
        if not _thickness_matches(stock_piece, group_thickness_mm):
            continue
        if not _material_matches(stock_piece, group_material):
            continue
        try:
            options = _part_orientation_options(
                part,
                stock_piece.length_mm,
                stock_piece.width_mm,
                settings,
            )
        except ValueError:
            continue
        usable_area_mm2 = _sheet_usable_area_mm2(
            stock_piece.length_mm,
            stock_piece.width_mm,
            settings,
            part.cut_method,
        )

        for rotated, placed_length_mm, placed_width_mm in options:
            score = (
                _material_match_score(stock_piece, group_material),
                _thickness_match_score(stock_piece, group_thickness_mm),
                # Retalhos (is_full_sheet=False) têm prioridade absoluta sobre chapas inteiras
                0 if not stock_piece.is_full_sheet else 1,
                # Entre retalhos: preferir o menor que ainda caiba (melhor aproveitamento)
                usable_area_mm2 if not stock_piece.is_full_sheet else 0,
                usable_area_mm2 - (placed_length_mm * placed_width_mm),
                stock_index,
            )
            candidate = {
                "kind": "new_sheet",
                "score": score,
                "stock_index": stock_index,
                "stock_piece": stock_piece,
            }
            if best_candidate is None or candidate["score"] < best_candidate["score"]:
                best_candidate = candidate

    return best_candidate


def _apply_layout_candidate(candidate, part, settings):
    sheet_state = candidate["sheet_state"]
    layout_sheet = sheet_state["layout_sheet"]
    spacing_mm = _effective_spacing_mm(settings, layout_sheet.cut_method)
    cut_offset_mm = spacing_mm / 2.0

    if candidate["kind"] == "maxrects_rect":
        rect = candidate["free_rect"]
        x_mm = rect["x_mm"]
        y_mm = rect["y_mm"]
        sheet_state["free_rectangles"] = _split_free_rectangles(
            sheet_state["free_rectangles"],
            x_mm,
            y_mm,
            candidate["blocked_length_mm"],
            candidate["blocked_width_mm"],
        )
        if len(sheet_state["free_rectangles"]) <= 20:
            sheet_state["free_rectangles"] = _merge_adjacent_rectangles(sheet_state["free_rectangles"])
    elif candidate["kind"] == "guillotine_rect":
        rect = candidate["free_rect"]
        sequence_zone = _cut_rect_sequence_zone(
            _available_cut_rect(
                rect["x_mm"],
                rect["y_mm"],
                rect["x_mm"] + rect["length_mm"],
                rect["y_mm"] + rect["width_mm"],
            )
        )
        x_mm = rect["x_mm"]
        y_mm = rect["y_mm"]
        remaining_length_mm = rect["length_mm"] - candidate["blocked_length_mm"]
        remaining_width_mm = rect["width_mm"] - candidate["blocked_width_mm"]
        split_name = candidate.get("split_name", "SAS")
        split_vertical = split_name == "LAS"
        if split_name == "SAS":
            split_vertical = remaining_length_mm <= remaining_width_mm
        new_rectangles = _split_guillotine_rectangles(
            rect,
            candidate["blocked_length_mm"],
            candidate["blocked_width_mm"],
            split_name,
        )
        sheet_state["free_rectangles"] = _replace_free_rectangle(
            sheet_state["free_rectangles"],
            candidate["rect_index"],
            new_rectangles,
        )
        if len(sheet_state["free_rectangles"]) <= 20:
            sheet_state["free_rectangles"] = _merge_adjacent_rectangles(sheet_state["free_rectangles"])
        cut_x_mm = x_mm + candidate["placed_length_mm"] + cut_offset_mm
        cut_y_mm = y_mm + candidate["placed_width_mm"] + cut_offset_mm
        if split_vertical:
            if remaining_length_mm > DIMENSION_EQUALITY_TOLERANCE_MM:
                _append_cut_step(
                    layout_sheet,
                    "Vertical",
                    cut_x_mm,
                    rect["y_mm"],
                    cut_x_mm,
                    rect["y_mm"] + rect["width_mm"],
                    cut_x_mm,
                    target_part_id=part.part_id,
                    description=f"Separar bloco da peca {part.part_id}",
                    sequence_zone=sequence_zone,
                )
            if remaining_width_mm > DIMENSION_EQUALITY_TOLERANCE_MM:
                _append_cut_step(
                    layout_sheet,
                    "Horizontal",
                    rect["x_mm"],
                    cut_y_mm,
                    cut_x_mm,
                    cut_y_mm,
                    cut_y_mm,
                    target_part_id=part.part_id,
                    description=f"Finalizar bloco da peca {part.part_id}",
                    sequence_zone=sequence_zone,
                )
        else:
            if remaining_width_mm > DIMENSION_EQUALITY_TOLERANCE_MM:
                _append_cut_step(
                    layout_sheet,
                    "Horizontal",
                    rect["x_mm"],
                    cut_y_mm,
                    rect["x_mm"] + rect["length_mm"],
                    cut_y_mm,
                    cut_y_mm,
                    target_part_id=part.part_id,
                    description=f"Separar bloco da peca {part.part_id}",
                    sequence_zone=sequence_zone,
                )
            if remaining_length_mm > DIMENSION_EQUALITY_TOLERANCE_MM:
                _append_cut_step(
                    layout_sheet,
                    "Vertical",
                    cut_x_mm,
                    rect["y_mm"],
                    cut_x_mm,
                    cut_y_mm,
                    cut_x_mm,
                    target_part_id=part.part_id,
                    description=f"Finalizar bloco da peca {part.part_id}",
                    sequence_zone=sequence_zone,
                )
    elif candidate["kind"] == "existing_row":
        row = candidate["row"]
        x_mm = row["x_mm"]
        y_mm = row["y_mm"]
        row["x_mm"] += candidate["placed_length_mm"] + spacing_mm
    else:
        row = {
            "y_mm": candidate["row_y_mm"],
            "height_mm": candidate["placed_width_mm"],
            "x_mm": sheet_state["min_x"],
        }
        sheet_state["rows"].append(row)
        x_mm = row["x_mm"]
        y_mm = row["y_mm"]
        row["x_mm"] += candidate["placed_length_mm"] + spacing_mm
        sheet_state["next_y_mm"] = row["y_mm"] + row["height_mm"] + spacing_mm

    layout_sheet.placements.append(
        LayoutPlacement(
            part=part,
            x_mm=x_mm,
            y_mm=y_mm,
            placed_length_mm=candidate["placed_length_mm"],
            placed_width_mm=candidate["placed_width_mm"],
            rotated=candidate["rotated"],
        )
    )


def _intervals_overlap(start_a, size_a, start_b, size_b):
    end_a = start_a + size_a
    end_b = start_b + size_b
    return not (
        end_a <= start_b + DIMENSION_EQUALITY_TOLERANCE_MM
        or end_b <= start_a + DIMENSION_EQUALITY_TOLERANCE_MM
    )


def _left_compaction_target(placement, placements, min_x, spacing_mm):
    target_x = min_x
    for other in placements:
        if other is placement:
            continue
        if other.x_mm >= placement.x_mm - DIMENSION_EQUALITY_TOLERANCE_MM:
            continue
        if not _intervals_overlap(
            placement.y_mm,
            placement.placed_width_mm,
            other.y_mm,
            other.placed_width_mm,
        ):
            continue
        target_x = max(target_x, other.x_mm + other.placed_length_mm + spacing_mm)
    return target_x


def _down_compaction_target(placement, placements, min_y, spacing_mm):
    target_y = min_y
    for other in placements:
        if other is placement:
            continue
        if other.y_mm >= placement.y_mm - DIMENSION_EQUALITY_TOLERANCE_MM:
            continue
        if not _intervals_overlap(
            placement.x_mm,
            placement.placed_length_mm,
            other.x_mm,
            other.placed_length_mm,
        ):
            continue
        target_y = max(target_y, other.y_mm + other.placed_width_mm + spacing_mm)
    return target_y


def _compact_free_layout_sheet(layout_sheet, settings):
    if len(layout_sheet.placements) < 2:
        return

    min_x, min_y, _, _ = _sheet_usable_bounds(
        layout_sheet.source_length_mm,
        layout_sheet.source_width_mm,
        settings,
        layout_sheet.cut_method,
    )
    spacing_mm = _effective_spacing_mm(settings, layout_sheet.cut_method)
    max_passes = max(4, len(layout_sheet.placements) * 3)

    for _ in range(max_passes):
        moved = False
        ordered_placements = sorted(
            layout_sheet.placements,
            key=lambda placement: (
                placement.y_mm,
                placement.x_mm,
                -placement.placed_width_mm,
                -placement.placed_length_mm,
            ),
        )
        for placement in ordered_placements:
            while True:
                old_x = placement.x_mm
                old_y = placement.y_mm
                placement.x_mm = _left_compaction_target(
                    placement,
                    layout_sheet.placements,
                    min_x,
                    spacing_mm,
                )
                placement.y_mm = _down_compaction_target(
                    placement,
                    layout_sheet.placements,
                    min_y,
                    spacing_mm,
                )
                placement.x_mm = _left_compaction_target(
                    placement,
                    layout_sheet.placements,
                    min_x,
                    spacing_mm,
                )
                if (
                    abs(placement.x_mm - old_x) <= DIMENSION_EQUALITY_TOLERANCE_MM
                    and abs(placement.y_mm - old_y) <= DIMENSION_EQUALITY_TOLERANCE_MM
                ):
                    break
                moved = True
        if not moved:
            break


def _apply_common_line_optimization(layout_sheet, settings):
    """Otimização Common-Line: compacta peças adjacentes eliminando kerf entre elas.

    Move cada peça o máximo possível para a esquerda e para baixo, fechando
    qualquer gap de kerf/spacing entre peças vizinhas.  Funciona para peças
    de qualquer tamanho, não apenas alinhadas perfeitamente.
    """
    if not getattr(settings, "cnc_common_line", False):
        return
    if len(layout_sheet.placements) < 2:
        return

    spacing = _effective_spacing_mm(settings, layout_sheet.cut_method)
    tol = DIMENSION_EQUALITY_TOLERANCE_MM
    margin = _effective_margin_mm(settings, layout_sheet.cut_method)

    # Múltiplas passadas — cada passada pode liberar espaço para a próxima
    for _ in range(5):
        moved = False

        # Ordenar da esquerda para a direita, de baixo para cima
        placements = sorted(layout_sheet.placements, key=lambda p: (p.x_mm, p.y_mm))

        for idx, p in enumerate(placements):
            others = [o for j, o in enumerate(placements) if j != idx]

            # --- Tentar mover para a ESQUERDA ---
            best_x = margin  # Limite mínimo = margem da chapa
            for o in others:
                # Verificar se 'o' está à esquerda de 'p' E se há sobreposição vertical
                if o.x_mm + o.placed_length_mm <= p.x_mm + tol:
                    if _ranges_overlap(p.y_mm, p.placed_width_mm, o.y_mm, o.placed_width_mm, tol):
                        candidate_x = o.x_mm + o.placed_length_mm
                        if candidate_x > best_x:
                            best_x = candidate_x

            if p.x_mm - best_x > tol:
                p.x_mm = best_x
                moved = True

            # --- Tentar mover para BAIXO ---
            best_y = margin
            for o in others:
                if o.y_mm + o.placed_width_mm <= p.y_mm + tol:
                    if _ranges_overlap(p.x_mm, p.placed_length_mm, o.x_mm, o.placed_length_mm, tol):
                        candidate_y = o.y_mm + o.placed_width_mm
                        if candidate_y > best_y:
                            best_y = candidate_y

            if p.y_mm - best_y > tol:
                p.y_mm = best_y
                moved = True

        if not moved:
            break


def _ranges_overlap(start_a, length_a, start_b, length_b, tol=0.1):
    """Verifica se dois intervalos 1D se sobrepõem (com tolerância)."""
    return start_a < start_b + length_b - tol and start_b < start_a + length_a - tol


def _generate_stay_down_toolpath(layout_sheet, settings):
    """Gera sequência otimizada de corte Stay-Down para CNC.

    Stay-Down: o router não levanta a fresa entre cortes adjacentes,
    reduzindo tempo de máquina e melhorando a qualidade do acabamento.

    Ordena os cut_steps de forma que cortes consecutivos compartilhem
    um ponto de início/fim, minimizando movimentações em vazio.
    """
    if not getattr(settings, "cnc_stay_down", False):
        return
    if not layout_sheet.cut_steps:
        return

    tol = 1.0  # mm de tolerância para considerar pontos "no mesmo lugar"
    steps = list(layout_sheet.cut_steps)
    if len(steps) < 2:
        return

    # Algoritmo greedy: nearest-neighbor no espaço de endpoints
    ordered = [steps.pop(0)]
    while steps:
        last = ordered[-1]
        end_x, end_y = last.end_x_mm, last.end_y_mm

        best_idx = 0
        best_dist = float("inf")

        for idx, s in enumerate(steps):
            # Distância do fim do último corte ao início do próximo
            d_start = ((s.start_x_mm - end_x) ** 2 + (s.start_y_mm - end_y) ** 2) ** 0.5
            # Ou ao fim (pode inverter o corte)
            d_end = ((s.end_x_mm - end_x) ** 2 + (s.end_y_mm - end_y) ** 2) ** 0.5
            d = min(d_start, d_end)
            if d < best_dist:
                best_dist = d
                best_idx = idx

        next_step = steps.pop(best_idx)

        # Se mais perto pelo fim, inverter início/fim
        d_start = ((next_step.start_x_mm - end_x) ** 2 + (next_step.start_y_mm - end_y) ** 2) ** 0.5
        d_end = ((next_step.end_x_mm - end_x) ** 2 + (next_step.end_y_mm - end_y) ** 2) ** 0.5
        if d_end < d_start:
            next_step.start_x_mm, next_step.end_x_mm = next_step.end_x_mm, next_step.start_x_mm
            next_step.start_y_mm, next_step.end_y_mm = next_step.end_y_mm, next_step.start_y_mm

        ordered.append(next_step)

    # Re-indexar
    for idx, step in enumerate(ordered, start=1):
        step.step_index = idx

    layout_sheet.cut_steps = ordered


def _part_fits_stock_piece(part, stock_piece, settings):
    try:
        _part_orientation_options(part, stock_piece.length_mm, stock_piece.width_mm, settings)
        return True
    except ValueError:
        return False


def _part_fits_configured_full_sheet(part, settings, group):
    stock_piece = SheetStockPiece(
        label="Chapa configurada",
        length_mm=settings.length_mm,
        width_mm=settings.width_mm,
        thickness_mm=settings.full_sheet_thickness_mm or group["thickness_mm"],
        material=settings.full_sheet_material or group["material"],
        quantity=1,
        kind="Chapa inteira",
        is_full_sheet=True,
    )
    try:
        _part_orientation_options(part, stock_piece.length_mm, stock_piece.width_mm, settings)
        return True, ""
    except ValueError as exc:
        return False, str(exc)


def _expanded_layout_parts(parts, tape_thickness_mm=0.0):
    expanded_parts = []
    doc = ensure_document()
    for part in parts:
        quantity = max(1, int(getattr(part, "quantity", 1) or 1))
        if quantity <= 1:
            expanded_parts.append(apply_cut_compensation(part, tape_thickness_mm))
            continue
        base_object_name = str(getattr(part, "object_name", "") or "")
        obj = doc.getObject(base_object_name)
        occurrence_map = _object_occurrence_edge_band_map(obj, quantity) if obj is not None else {}
        base_flags = _part_edge_band_flags_dict(part)
        for occurrence_index in range(1, quantity + 1):
            current_flags = _normalize_occurrence_edge_band_flags(
                occurrence_map.get(occurrence_index, base_flags)
            )
            occurrence_part = replace(
                part,
                object_name=f"{base_object_name}#{occurrence_index:02d}",
                label=(
                    f"{part.label} [{occurrence_index:02d}/{quantity:02d}]"
                    if occurrence_map
                    else part.label
                ),
                quantity=1,
                edge_band_top=current_flags["top"],
                edge_band_bottom=current_flags["bottom"],
                edge_band_left=current_flags["left"],
                edge_band_right=current_flags["right"],
            )
            expanded_parts.append(apply_cut_compensation(occurrence_part, tape_thickness_mm))
    return expanded_parts


def _expanded_report_parts(parts):
    expanded_parts = []
    doc = ensure_document()
    for part in parts:
        quantity = max(1, int(getattr(part, "quantity", 1) or 1))
        if quantity <= 1:
            expanded_parts.append(part)
            continue
        base_object_name = str(getattr(part, "object_name", "") or "")
        obj = doc.getObject(base_object_name)
        occurrence_map = _object_occurrence_edge_band_map(obj, quantity) if obj is not None else {}
        if not occurrence_map:
            expanded_parts.append(part)
            continue
        base_flags = _part_edge_band_flags_dict(part)
        for occurrence_index in range(1, quantity + 1):
            current_flags = _normalize_occurrence_edge_band_flags(
                occurrence_map.get(occurrence_index, base_flags)
            )
            expanded_parts.append(
                replace(
                    part,
                    object_name=f"{base_object_name}#{occurrence_index:02d}",
                    label=f"{part.label} [{occurrence_index:02d}/{quantity:02d}]",
                    quantity=1,
                    edge_band_top=current_flags["top"],
                    edge_band_bottom=current_flags["bottom"],
                    edge_band_left=current_flags["left"],
                    edge_band_right=current_flags["right"],
                )
            )
    return expanded_parts


def _grain_match_sort_key(part):
    """Retorna chave de sort que agrupa peças com mesmo grain_match_group.

    Peças com grain_match_group não-vazio são colocadas juntas (mesmo prefixo "0_grupo"),
    de forma que o nesting as posicione adjacentes na chapa para continuidade do veio.
    Dentro do grupo, ordena por base_label para manter frentes de gaveta em sequência.
    Peças sem grupo recebem prefixo "1_" e ficam depois.
    """
    gmg = getattr(part, "grain_match_group", "") or ""
    if gmg.strip():
        return (0, gmg.strip().casefold())
    return (1, "")


def _layout_group_parts(group, settings):
    tape_mm = float(getattr(settings, "edge_band_thickness_mm", 0.0) or 0.0)
    parts = _expanded_layout_parts(group["parts"], tape_thickness_mm=tape_mm)
    profile = get_cut_process_profile(group["cut_method"], settings)
    if profile.layout_family == LAYOUT_FAMILY_MAXRECTS:
        return sorted(
            parts,
            key=lambda part: (
                _grain_match_sort_key(part),
                -(part.length_mm * part.width_mm),
                -max(part.length_mm, part.width_mm),
                -min(part.length_mm, part.width_mm),
                _base_label_for_sort(part.label),
                part.object_name.casefold(),
            ),
        )
    if profile.layout_family == LAYOUT_FAMILY_GUILLOTINE:
        return sorted(
            parts,
            key=lambda part: (
                _grain_match_sort_key(part),
                -max(part.length_mm, part.width_mm),
                -(part.length_mm * part.width_mm),
                -min(part.length_mm, part.width_mm),
                _base_label_for_sort(part.label),
                part.object_name.casefold(),
            ),
        )

    return sorted(
        parts,
        key=lambda part: (
            _grain_match_sort_key(part),
            -part.width_mm,
            -part.length_mm,
            _base_label_for_sort(part.label),
            part.object_name.casefold(),
        ),
    )


def _layout_sheet_compactness_mm2(layout_sheet, settings):
    if not layout_sheet.placements:
        return 0.0
    min_x, min_y, _, _ = _sheet_usable_bounds(
        layout_sheet.source_length_mm,
        layout_sheet.source_width_mm,
        settings,
        layout_sheet.cut_method,
    )
    max_right = max(
        (placement.x_mm + placement.placed_length_mm) for placement in layout_sheet.placements
    )
    max_top = max(
        (placement.y_mm + placement.placed_width_mm) for placement in layout_sheet.placements
    )
    return max(0.0, max_right - min_x) * max(0.0, max_top - min_y)


def _layout_solution_score(layout_sheets, settings):
    total_source_area_mm2 = sum(
        layout_sheet.source_length_mm * layout_sheet.source_width_mm for layout_sheet in layout_sheets
    )
    total_used_area_mm2 = sum(_layout_sheet_used_area_mm2(layout_sheet) for layout_sheet in layout_sheets)
    total_unused_area_mm2 = total_source_area_mm2 - total_used_area_mm2
    total_compactness_mm2 = sum(
        _layout_sheet_compactness_mm2(layout_sheet, settings) for layout_sheet in layout_sheets
    )
    extra_sheet_count = sum(1 for layout_sheet in layout_sheets if layout_sheet.source_kind == "Chapa extra")
    return (
        len(layout_sheets),
        extra_sheet_count,
        total_source_area_mm2,
        total_unused_area_mm2,
        total_compactness_mm2,
    )


def _create_group_layout_sheets_ordered(group, ordered_parts, settings, available_stock_sheets, extra_sheet_index):
    """Variante de _create_group_layout_sheets que recebe peças já ordenadas externamente."""
    sheet_states = []
    remaining_stock_sheets = list(available_stock_sheets)
    next_extra_sheet_index = extra_sheet_index

    for part in ordered_parts:
        # Antes de tentar chapas abertas, verificar se existe retalho fechado que
        # cabe esta peça — se sim, prefere o retalho mesmo que haja chapa extra aberta.
        remnant_candidate = None
        if remaining_stock_sheets:
            remnant_candidate = _select_unopened_sheet_candidate(
                remaining_stock_sheets,
                part,
                settings,
                group["thickness_mm"],
                group["material"],
            )

        if remnant_candidate is not None:
            # Há retalho disponível para esta peça — usá-lo antes de qualquer chapa aberta
            remnant_piece = remaining_stock_sheets.pop(remnant_candidate["stock_index"])
            sheet_state = _new_sheet_state(group, len(sheet_states) + 1, remnant_piece, settings)
            sheet_states.append(sheet_state)
            candidate = _select_sheet_placement_candidate([sheet_state], part, settings)
            if candidate is None:
                # Retalho abriu mas não coube (raro — pode ser margem/spacing)
                # Fallback: tentar outras chapas abertas
                candidate = _select_sheet_placement_candidate(sheet_states[:-1], part, settings)
        else:
            candidate = _select_sheet_placement_candidate(sheet_states, part, settings)

        if candidate is None:
            # Sem retalho disponível e sem chapa aberta que caiba — abrir extra
            if settings.allow_extra_full_sheets:
                stock_piece = _extra_full_sheet_stock_piece(next_extra_sheet_index, settings, group)
                next_extra_sheet_index += 1
            else:
                stock_piece = None

            if stock_piece is not None:
                sheet_state = _new_sheet_state(group, len(sheet_states) + 1, stock_piece, settings)
                sheet_states.append(sheet_state)
                candidate = _select_sheet_placement_candidate([sheet_state], part, settings)

        if candidate is None:
            fits_configured_sheet, configured_sheet_error = _part_fits_configured_full_sheet(
                part,
                settings,
                group,
            )
            if fits_configured_sheet:
                raise ValueError(
                    "O estoque configurado nao foi suficiente para acomodar a peca "
                    f"{part.part_id} ({PART_LABEL_PATTERN.sub('', part.label)}). "
                    "Adicione mais chapas/retalhos ou permita abrir chapas extras."
                )
            raise ValueError(configured_sheet_error)

        _apply_layout_candidate(candidate, part, settings)

    group_profile = get_cut_process_profile(group["cut_method"], settings)
    layout_sheets = []
    for sheet_state in sheet_states:
        if sheet_state["layout_sheet"].placements:
            if sheet_state["layout_sheet"].layout_strategy != group_profile.strategy_name:
                sheet_state["layout_sheet"].layout_strategy = group_profile.strategy_name
            if group_profile.layout_family == LAYOUT_FAMILY_MAXRECTS:
                _compact_free_layout_sheet(sheet_state["layout_sheet"], settings)
                sheet_state["layout_sheet"].cut_steps = []
            elif group_profile.layout_family == LAYOUT_FAMILY_STRIP:
                _build_strip_cut_steps(sheet_state["layout_sheet"], settings)
                _resequence_sheet_cut_steps(sheet_state["layout_sheet"], settings)
            elif sheet_state["layout_sheet"].cut_steps:
                _resequence_sheet_cut_steps(sheet_state["layout_sheet"], settings)
            # Pós-processamento CNC: Common-Line e Stay-Down
            _apply_common_line_optimization(sheet_state["layout_sheet"], settings)
            _generate_stay_down_toolpath(sheet_state["layout_sheet"], settings)
            layout_sheets.append(sheet_state["layout_sheet"])
    return layout_sheets, remaining_stock_sheets, next_extra_sheet_index


def _shuffle_equal_area_parts(expanded_parts, rng):
    """Embaralha peças de mesma área (dentro de blocos), mantendo peças maiores primeiro."""
    if not expanded_parts:
        return []
    from itertools import groupby
    sorted_by_area = sorted(expanded_parts, key=lambda p: round(p.length_mm * p.width_mm, -1), reverse=True)
    result = []
    for _, group_iter in groupby(sorted_by_area, key=lambda p: round(p.length_mm * p.width_mm, -1)):
        block = list(group_iter)
        rng.shuffle(block)
        result.extend(block)
    return result


def _guillotine_grasp_pass(group, settings, available_stock_sheets, extra_sheet_index, n_passes=50, time_budget_s=2.0):
    """
    GRASP para guilhotine: testa n_passes permutações diferentes da lista de peças
    e retorna o resultado com melhor score.

    Estratégia:
    - 4 ordenações deterministas (área, perímetro, lado maior, lado menor)
    - Até n_passes permutações aleatórias de peças de mesma área (semi-randomizado)
    - Respeitando time_budget_s de wall-clock time
    - Retorna (layout_sheets, remaining_stock_sheets, next_extra_sheet_index) do melhor resultado
    """
    import random
    import time

    tape_mm = float(getattr(settings, "edge_band_thickness_mm", 0.0) or 0.0)
    expanded = _expanded_layout_parts(group["parts"], tape_thickness_mm=tape_mm)
    best_result = None
    best_score = None
    deadline = time.monotonic() + time_budget_s

    # 4 estratégias deterministas de ordenação
    sort_strategies = [
        sorted(expanded, key=lambda p: p.length_mm * p.width_mm, reverse=True),
        sorted(expanded, key=lambda p: p.length_mm + p.width_mm, reverse=True),
        sorted(expanded, key=lambda p: max(p.length_mm, p.width_mm), reverse=True),
        sorted(expanded, key=lambda p: min(p.length_mm, p.width_mm), reverse=True),
    ]

    rng = random.Random(42)

    for ordered_parts in sort_strategies:
        if time.monotonic() > deadline:
            break
        try:
            sheets, stock, extra_idx = _create_group_layout_sheets_ordered(
                group, ordered_parts, settings, available_stock_sheets, extra_sheet_index
            )
        except ValueError:
            continue
        score = _layout_solution_score(sheets, settings)
        if best_score is None or score < best_score:
            best_score = score
            best_result = (sheets, stock, extra_idx)

    pass_count = 0
    while pass_count < n_passes and time.monotonic() < deadline:
        shuffled = _shuffle_equal_area_parts(expanded, rng)
        try:
            sheets, stock, extra_idx = _create_group_layout_sheets_ordered(
                group, shuffled, settings, available_stock_sheets, extra_sheet_index
            )
        except ValueError:
            pass_count += 1
            continue
        score = _layout_solution_score(sheets, settings)
        if best_score is None or score < best_score:
            best_score = score
            best_result = (sheets, stock, extra_idx)
        pass_count += 1

    if best_result is None:
        return [], list(available_stock_sheets), extra_sheet_index
    return best_result


def _create_group_layout_sheets(group, settings, available_stock_sheets, extra_sheet_index):
    sheet_states = []
    remaining_stock_sheets = list(available_stock_sheets)
    next_extra_sheet_index = extra_sheet_index

    for part in _layout_group_parts(group, settings):
        remnant_candidate = None
        if remaining_stock_sheets:
            remnant_candidate = _select_unopened_sheet_candidate(
                remaining_stock_sheets,
                part,
                settings,
                group["thickness_mm"],
                group["material"],
            )

        if remnant_candidate is not None:
            remnant_piece = remaining_stock_sheets.pop(remnant_candidate["stock_index"])
            sheet_state = _new_sheet_state(group, len(sheet_states) + 1, remnant_piece, settings)
            sheet_states.append(sheet_state)
            candidate = _select_sheet_placement_candidate([sheet_state], part, settings)
            if candidate is None:
                candidate = _select_sheet_placement_candidate(sheet_states[:-1], part, settings)
        else:
            candidate = _select_sheet_placement_candidate(sheet_states, part, settings)

        if candidate is None:
            if settings.allow_extra_full_sheets:
                stock_piece = _extra_full_sheet_stock_piece(next_extra_sheet_index, settings, group)
                next_extra_sheet_index += 1
            else:
                stock_piece = None

            if stock_piece is not None:
                sheet_state = _new_sheet_state(group, len(sheet_states) + 1, stock_piece, settings)
                sheet_states.append(sheet_state)
                candidate = _select_sheet_placement_candidate([sheet_state], part, settings)

        if candidate is None:
            fits_configured_sheet, configured_sheet_error = _part_fits_configured_full_sheet(
                part,
                settings,
                group,
            )
            if fits_configured_sheet:
                raise ValueError(
                    "O estoque configurado nao foi suficiente para acomodar a peca "
                    f"{part.part_id} ({PART_LABEL_PATTERN.sub('', part.label)}). "
                    "Adicione mais chapas/retalhos ou permita abrir chapas extras."
                )
            raise ValueError(configured_sheet_error)

        _apply_layout_candidate(candidate, part, settings)

    group_profile = get_cut_process_profile(group["cut_method"], settings)
    layout_sheets = []
    for sheet_state in sheet_states:
        if sheet_state["layout_sheet"].placements:
            if sheet_state["layout_sheet"].layout_strategy != group_profile.strategy_name:
                sheet_state["layout_sheet"].layout_strategy = group_profile.strategy_name
            if group_profile.layout_family == LAYOUT_FAMILY_MAXRECTS:
                _compact_free_layout_sheet(sheet_state["layout_sheet"], settings)
                sheet_state["layout_sheet"].cut_steps = []
            elif group_profile.layout_family == LAYOUT_FAMILY_STRIP:
                _build_strip_cut_steps(sheet_state["layout_sheet"], settings)
                _resequence_sheet_cut_steps(sheet_state["layout_sheet"], settings)
            elif sheet_state["layout_sheet"].cut_steps:
                _resequence_sheet_cut_steps(sheet_state["layout_sheet"], settings)
            # Pós-processamento CNC: Common-Line e Stay-Down
            _apply_common_line_optimization(sheet_state["layout_sheet"], settings)
            _generate_stay_down_toolpath(sheet_state["layout_sheet"], settings)
            layout_sheets.append(sheet_state["layout_sheet"])
    return layout_sheets, remaining_stock_sheets, next_extra_sheet_index


def _apply_shape_layout_optimization(layout_sheets, settings):
    """Aplica o segundo estágio por formas sem alterar estoque ou quantidade de chapas."""
    if normalize_cnc_nesting_mode(getattr(settings, "cnc_nesting_mode", "")) != (
        "Otimizado por formas"
    ):
        return
    # Common-Line elimina o vão entre retângulos alinhados. Misturar as duas
    # estratégias sem um gerador de percurso poligonal específico seria ambíguo.
    if bool(getattr(settings, "cnc_common_line", False)):
        return
    if str(getattr(settings, "cnc_origin_corner", "inferior_esquerdo") or "") != (
        "inferior_esquerdo"
    ):
        # O espelhamento tardio do canto de origem opera pelos bounding boxes.
        # Em perfis assimétricos ele poderia desfazer um encaixe por contorno.
        return

    for layout_sheet in layout_sheets:
        # "Auto" usa o fluxo livre e deve aproveitar contornos do mesmo modo.
        # Apenas a seccionadora precisa permanecer estritamente retangular.
        if normalize_cut_method(layout_sheet.cut_method) == "Seccionadora":
            continue
        if any(
            str(getattr(placement.part, "grain_match_group", "") or "").strip()
            for placement in layout_sheet.placements
        ):
            # Continuidade visual de veio exige preservar a vizinhança do grupo.
            continue

        orientation_options = []
        try:
            for placement in layout_sheet.placements:
                legacy_options = _part_orientation_options(
                    placement.part,
                    layout_sheet.source_length_mm,
                    layout_sheet.source_width_mm,
                    settings,
                )
                expanded_options = []
                has_custom_profile = bool(
                    getattr(placement.part, "profile_points", None) or []
                )
                for rotated, length_mm, width_mm in legacy_options:
                    base_angle = 90 if rotated else 0
                    expanded_options.append((base_angle, length_mm, width_mm))
                    if (
                        has_custom_profile
                        and bool(getattr(placement.part, "allow_rotation", True))
                    ):
                        expanded_options.append(
                            ((base_angle + 180) % 360, length_mm, width_mm)
                        )
                orientation_options.append(expanded_options)
        except ValueError:
            continue

        result = optimize_layout_sheet_shapes(
            layout_sheet,
            margin_mm=_effective_margin_mm(settings, layout_sheet.cut_method),
            spacing_mm=_effective_spacing_mm(settings, layout_sheet.cut_method),
            orientation_options=orientation_options,
        )
        if not result.improved:
            continue

        layout_sheet.placements = result.placements
        layout_sheet.cut_steps = []
        if SHAPE_LAYOUT_SUFFIX.strip() not in str(layout_sheet.layout_strategy or ""):
            layout_sheet.layout_strategy = (
                f"{layout_sheet.layout_strategy}{SHAPE_LAYOUT_SUFFIX}"
            )


def _layout_sheet_as_stock_piece(layout_sheet):
    return SheetStockPiece(
        label=layout_sheet.source_label,
        length_mm=layout_sheet.source_length_mm,
        width_mm=layout_sheet.source_width_mm,
        thickness_mm=layout_sheet.source_thickness_mm,
        material=layout_sheet.material,
        quantity=1,
        kind=layout_sheet.source_kind,
        is_full_sheet=layout_sheet.source_kind != "Retalho",
        cost_per_sheet=float(getattr(layout_sheet, "_cost_per_sheet", 0.0) or 0.0),
    )


def _consolidation_group_for_sheet(layout_sheet, parts):
    return {
        "group_id": layout_sheet.group_id,
        "material": layout_sheet.material,
        "thickness_mm": layout_sheet.thickness_mm,
        "cut_method": layout_sheet.cut_method,
        "parts": list(parts),
    }


def _consolidation_part_orders(parts):
    parts = list(parts)
    candidates = [
        parts,
        sorted(
            parts,
            key=lambda part: (
                -(part.length_mm * part.width_mm),
                -max(part.length_mm, part.width_mm),
                -min(part.length_mm, part.width_mm),
            ),
        ),
        sorted(
            parts,
            key=lambda part: (
                -max(part.length_mm, part.width_mm),
                -(part.length_mm * part.width_mm),
            ),
        ),
        sorted(
            parts,
            key=lambda part: (
                -part.length_mm,
                -part.width_mm,
            ),
        ),
        sorted(
            parts,
            key=lambda part: (
                -part.width_mm,
                -part.length_mm,
            ),
        ),
    ]
    unique_orders = []
    seen = set()
    for order in candidates:
        key = tuple(id(part) for part in order)
        if key in seen:
            continue
        seen.add(key)
        unique_orders.append(order)
    return unique_orders


def _repack_layout_sheet_with_parts(layout_sheet, added_parts, settings):
    combined_parts = [
        placement.part
        for placement in layout_sheet.placements
    ] + list(added_parts)
    if not combined_parts:
        return replace(layout_sheet, placements=[], cut_steps=[])

    stock_piece = _layout_sheet_as_stock_piece(layout_sheet)
    group = _consolidation_group_for_sheet(layout_sheet, combined_parts)
    base_strategy = str(layout_sheet.layout_strategy or "").split(" +", 1)[0]
    strategy_settings = _settings_with_layout_strategy(
        settings,
        layout_sheet.cut_method,
        base_strategy,
    )
    trial_settings = replace(
        strategy_settings,
        full_sheet_count=0,
        allow_extra_full_sheets=False,
        remnants=[],
    )
    best_sheet = None
    best_score = None

    for ordered_parts in _consolidation_part_orders(combined_parts):
        try:
            trial_sheets, _, _ = _create_group_layout_sheets_ordered(
                group,
                ordered_parts,
                trial_settings,
                [stock_piece],
                1,
            )
        except ValueError:
            continue
        if len(trial_sheets) != 1:
            continue
        trial_sheet = replace(
            trial_sheets[0],
            group_id=layout_sheet.group_id,
            sheet_index=layout_sheet.sheet_index,
        )
        trial_sheet._cost_per_sheet = float(
            getattr(layout_sheet, "_cost_per_sheet", 0.0) or 0.0
        )
        score = (
            _layout_sheet_compactness_mm2(trial_sheet, trial_settings),
            max(
                (
                    placement.x_mm + placement.placed_length_mm
                    for placement in trial_sheet.placements
                ),
                default=0.0,
            ),
        )
        if best_score is None or score < best_score:
            best_sheet = trial_sheet
            best_score = score
    return best_sheet


def _donor_placement_orders(placements):
    placements = list(placements)
    candidates = [
        sorted(
            placements,
            key=lambda placement: (
                -(placement_actual_area_mm2(placement)),
                -max(placement.placed_length_mm, placement.placed_width_mm),
            ),
        ),
        sorted(
            placements,
            key=lambda placement: (
                placement_actual_area_mm2(placement),
                min(placement.placed_length_mm, placement.placed_width_mm),
            ),
        ),
        sorted(
            placements,
            key=lambda placement: (
                min(placement.placed_length_mm, placement.placed_width_mm),
                -max(placement.placed_length_mm, placement.placed_width_mm),
            ),
        ),
        placements,
    ]
    unique_orders = []
    seen = set()
    for order in candidates:
        key = tuple(id(placement) for placement in order)
        if key in seen:
            continue
        seen.add(key)
        unique_orders.append(order)
    return unique_orders


def _attempt_donor_consolidation(group_sheets, donor_index, donor_order, settings):
    trial_sheets = [
        replace(
            sheet,
            placements=list(sheet.placements),
            cut_steps=list(sheet.cut_steps),
        )
        for sheet in group_sheets
    ]
    moved_placement_ids = set()

    for donor_placement in donor_order:
        best_target_index = None
        best_target_sheet = None
        best_target_score = None
        for target_index in range(donor_index):
            candidate_sheet = _repack_layout_sheet_with_parts(
                trial_sheets[target_index],
                [donor_placement.part],
                settings,
            )
            if candidate_sheet is None:
                continue
            candidate_score = (
                _layout_sheet_compactness_mm2(candidate_sheet, settings),
                target_index,
            )
            if best_target_score is None or candidate_score < best_target_score:
                best_target_index = target_index
                best_target_sheet = candidate_sheet
                best_target_score = candidate_score

        if best_target_sheet is None:
            continue
        trial_sheets[best_target_index] = best_target_sheet
        moved_placement_ids.add(id(donor_placement))

    donor_sheet = trial_sheets[donor_index]
    donor_sheet.placements = [
        placement
        for placement in donor_sheet.placements
        if id(placement) not in moved_placement_ids
    ]
    if moved_placement_ids:
        donor_sheet.cut_steps = []

    remaining_area_mm2 = sum(
        placement_actual_area_mm2(placement)
        for placement in donor_sheet.placements
    )
    compactness_mm2 = sum(
        _layout_sheet_compactness_mm2(sheet, settings)
        for sheet in trial_sheets[:donor_index]
    )
    score = (
        len(donor_sheet.placements),
        remaining_area_mm2,
        compactness_mm2,
    )
    return trial_sheets, score, len(moved_placement_ids)


def _consolidate_layout_sheets(layout_sheets, settings, time_budget_s=4.0):
    """Reinsere peças das últimas chapas nas anteriores e remove chapas vazias."""
    if len(layout_sheets) < 2:
        return list(layout_sheets)

    group_order = []
    sheets_by_group = {}
    for layout_sheet in layout_sheets:
        if layout_sheet.group_id not in sheets_by_group:
            group_order.append(layout_sheet.group_id)
            sheets_by_group[layout_sheet.group_id] = []
        sheets_by_group[layout_sheet.group_id].append(layout_sheet)

    deadline = time.monotonic() + max(0.1, float(time_budget_s))
    consolidated = []
    for group_id in group_order:
        group_sheets = list(sheets_by_group[group_id])
        for donor_index in range(len(group_sheets) - 1, 0, -1):
            if time.monotonic() >= deadline:
                break
            donor_sheet = group_sheets[donor_index]
            best_attempt = None
            for donor_order in _donor_placement_orders(donor_sheet.placements):
                if time.monotonic() >= deadline:
                    break
                attempt_sheets, attempt_score, moved_count = _attempt_donor_consolidation(
                    group_sheets,
                    donor_index,
                    donor_order,
                    settings,
                )
                if moved_count <= 0:
                    continue
                candidate = (attempt_score, attempt_sheets)
                if best_attempt is None or candidate[0] < best_attempt[0]:
                    best_attempt = candidate
                    if attempt_score[0] == 0:
                        break
            if best_attempt is None:
                continue
            group_sheets = best_attempt[1]
            if not group_sheets[donor_index].placements:
                group_sheets.pop(donor_index)

        for sheet_index, layout_sheet in enumerate(group_sheets, start=1):
            layout_sheet.sheet_index = sheet_index
        consolidated.extend(group_sheets)
    return consolidated


def create_layout_sheets(parts, settings=None):
    settings = settings or get_sheet_settings()
    _validate_sheet_settings(settings)

    if not parts:
        raise ValueError(
            "Nenhuma peca valida foi encontrada. Selecione objetos solidos ou deixe visiveis apenas as pecas desejadas."
        )

    layout_sheets = []
    available_stock_sheets = _build_available_stock_sheets(settings)
    extra_sheet_index = 1

    for group in group_parts(parts):
        strategy_candidates = _strategy_candidates_for_cut_method(group["cut_method"], settings)
        best_result = None
        first_error = None

        for strategy_index, strategy_name in enumerate(strategy_candidates):
            candidate_settings = _settings_with_layout_strategy(settings, group["cut_method"], strategy_name)
            try:
                candidate_layout_sheets, candidate_stock_sheets, candidate_extra_sheet_index = (
                    _create_group_layout_sheets(
                        group,
                        candidate_settings,
                        available_stock_sheets,
                        extra_sheet_index,
                    )
                )
            except ValueError as exc:
                if first_error is None:
                    first_error = exc
                continue

            score = _layout_solution_score(candidate_layout_sheets, candidate_settings)
            candidate_result = {
                "score": score,
                "strategy_index": strategy_index,
                "layout_sheets": candidate_layout_sheets,
                "stock_sheets": candidate_stock_sheets,
                "extra_sheet_index": candidate_extra_sheet_index,
            }
            if best_result is None or (
                candidate_result["score"],
                candidate_result["strategy_index"],
            ) < (
                best_result["score"],
                best_result["strategy_index"],
            ):
                best_result = candidate_result

        # GRASP para estratégias Guilhotina: testa múltiplas ordenações de peças
        guillotine_strategy_candidates = [
            s for s in strategy_candidates
            if get_cut_process_profile(group["cut_method"], _settings_with_layout_strategy(settings, group["cut_method"], s)).layout_family == LAYOUT_FAMILY_GUILLOTINE
        ]
        for guillotine_strategy_name in guillotine_strategy_candidates:
            candidate_settings = _settings_with_layout_strategy(settings, group["cut_method"], guillotine_strategy_name)
            try:
                grasp_sheets, grasp_stock, grasp_extra_idx = _guillotine_grasp_pass(
                    group, candidate_settings, available_stock_sheets, extra_sheet_index
                )
            except ValueError as exc:
                if first_error is None:
                    first_error = exc
                continue
            if not grasp_sheets:
                continue
            grasp_score = _layout_solution_score(grasp_sheets, candidate_settings)
            grasp_result = {
                "score": grasp_score,
                "strategy_index": len(strategy_candidates),
                "layout_sheets": grasp_sheets,
                "stock_sheets": grasp_stock,
                "extra_sheet_index": grasp_extra_idx,
            }
            if best_result is None or (
                grasp_result["score"],
                grasp_result["strategy_index"],
            ) < (
                best_result["score"],
                best_result["strategy_index"],
            ):
                best_result = grasp_result

        if best_result is None:
            raise first_error or ValueError("Nao foi possivel gerar o layout para o grupo atual.")

        layout_sheets.extend(best_result["layout_sheets"])
        available_stock_sheets = best_result["stock_sheets"]
        extra_sheet_index = best_result["extra_sheet_index"]

    layout_sheets = _consolidate_layout_sheets(layout_sheets, settings)
    _apply_shape_layout_optimization(layout_sheets, settings)
    return layout_sheets
