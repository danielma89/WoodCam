"""Regressões do segundo estágio de nesting por contornos reais."""

from panelnest.models import LayoutPlacement, LayoutSheet, PanelPart
from panelnest.nesting import _layout_sheet_used_area_mm2
from panelnest.shape_optimizer import (
    SHAPE_LAYOUT_SUFFIX,
    layout_shape_score,
    optimize_layout_sheet_shapes,
    validate_shape_placements,
)


def _part(part_id, length, width, profile=None, allow_rotation=True):
    return PanelPart(
        part_id=part_id,
        object_name=part_id,
        label=part_id,
        length_mm=float(length),
        width_mm=float(width),
        thickness_mm=15.0,
        cut_method="CNC",
        allow_rotation=allow_rotation,
        profile_points=list(profile or []),
    )


def _sheet(placements, length=160.0, width=120.0):
    return LayoutSheet(
        group_id="GRP-01",
        sheet_index=1,
        material="Compensado",
        thickness_mm=15.0,
        cut_method="CNC",
        layout_strategy="MaxRects - BSSF",
        source_label="Chapa 01",
        source_kind="Chapa inteira",
        source_length_mm=length,
        source_width_mm=width,
        source_thickness_mm=15.0,
        placements=placements,
    )


def test_shape_optimizer_places_small_part_inside_l_profile_notch():
    l_profile = [
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 30.0),
        (30.0, 30.0),
        (30.0, 100.0),
        (0.0, 100.0),
    ]
    host = _part("PN-L", 100, 100, profile=l_profile, allow_rotation=False)
    small = _part("PN-R", 40, 40, allow_rotation=False)
    baseline = [
        LayoutPlacement(host, 5.0, 5.0, 100.0, 100.0, False),
        LayoutPlacement(small, 110.0, 5.0, 40.0, 40.0, False),
    ]
    sheet = _sheet(baseline)

    result = optimize_layout_sheet_shapes(
        sheet,
        margin_mm=5.0,
        spacing_mm=5.0,
        orientation_options=[
            [(False, 100.0, 100.0)],
            [(False, 40.0, 40.0)],
        ],
        resolution_mm=2.5,
    )

    assert result.improved is True
    assert result.optimized_score < result.baseline_score
    assert result.optimized_score[0] == 100.0
    small_placement = next(
        placement for placement in result.placements if placement.part.part_id == "PN-R"
    )
    assert small_placement.x_mm < 100.0
    assert small_placement.y_mm > 30.0
    assert validate_shape_placements(
        result.placements,
        sheet.source_length_mm,
        sheet.source_width_mm,
        5.0,
        5.0,
    ) == (True, "")


def test_shape_validator_rejects_collision_and_insufficient_spacing():
    left = _part("PN-001", 40, 40)
    right = _part("PN-002", 40, 40)

    colliding = [
        LayoutPlacement(left, 5.0, 5.0, 40.0, 40.0, False),
        LayoutPlacement(right, 35.0, 5.0, 40.0, 40.0, False),
    ]
    valid, reason = validate_shape_placements(colliding, 120.0, 80.0, 5.0, 5.0)
    assert valid is False
    assert "Espacamento insuficiente" in reason

    too_close = [
        LayoutPlacement(left, 5.0, 5.0, 40.0, 40.0, False),
        LayoutPlacement(right, 48.0, 5.0, 40.0, 40.0, False),
    ]
    valid, reason = validate_shape_placements(too_close, 120.0, 80.0, 5.0, 5.0)
    assert valid is False
    assert "3.000 mm" in reason


def test_shape_optimizer_respects_supplied_rotation_constraints():
    trapezoid = [
        (0.0, 80.0),
        (120.0, 80.0),
        (120.0, 30.0),
        (0.0, 0.0),
    ]
    shaped = _part("PN-001", 120, 80, profile=trapezoid, allow_rotation=False)
    small = _part("PN-002", 30, 30, allow_rotation=False)
    baseline = [
        LayoutPlacement(shaped, 5.0, 5.0, 120.0, 80.0, False),
        LayoutPlacement(small, 130.0, 5.0, 30.0, 30.0, False),
    ]
    sheet = _sheet(baseline, length=180.0, width=120.0)

    result = optimize_layout_sheet_shapes(
        sheet,
        margin_mm=5.0,
        spacing_mm=5.0,
        orientation_options=[
            [(False, 120.0, 80.0)],
            [(False, 30.0, 30.0)],
        ],
        resolution_mm=2.5,
    )

    assert all(placement.rotated is False for placement in result.placements)


def test_irregular_profile_uses_real_area_in_utilization():
    triangle = [(0.0, 0.0), (100.0, 0.0), (0.0, 100.0)]
    part = _part("PN-001", 100, 100, profile=triangle)
    sheet = _sheet(
        [LayoutPlacement(part, 5.0, 5.0, 100.0, 100.0, False)],
    )

    assert _layout_sheet_used_area_mm2(sheet) == 5000.0


def test_shape_score_prioritizes_larger_contiguous_right_remnant():
    part = _part("PN-001", 40, 40)
    compact = [LayoutPlacement(part, 5.0, 5.0, 40.0, 40.0, False)]
    spread = [LayoutPlacement(part, 80.0, 5.0, 40.0, 40.0, False)]

    assert layout_shape_score(compact, 5.0) < layout_shape_score(spread, 5.0)
    assert SHAPE_LAYOUT_SUFFIX == " + Formas"


def test_complementary_90_and_270_degree_profiles_interlock():
    trapezoid = [
        (0.0, 260.0),
        (790.0, 260.0),
        (790.0, 100.0),
        (0.0, 0.0),
    ]
    left = _part("PN-001", 790, 260, profile=trapezoid)
    right = _part("PN-002", 790, 260, profile=trapezoid)
    baseline = [
        LayoutPlacement(left, 5.0, 5.0, 260.0, 790.0, True, 90),
        LayoutPlacement(right, 270.0, 5.0, 260.0, 790.0, True, 90),
    ]
    sheet = _sheet(baseline, length=600.0, width=800.0)

    result = optimize_layout_sheet_shapes(
        sheet,
        margin_mm=5.0,
        spacing_mm=5.0,
        orientation_options=[
            [(90, 260.0, 790.0), (270, 260.0, 790.0)],
            [(90, 260.0, 790.0), (270, 260.0, 790.0)],
        ],
        resolution_mm=2.5,
    )

    assert result.improved is True
    assert result.optimized_score[0] < 500.0
    assert {placement.rotation_deg for placement in result.placements} == {90, 270}
    assert validate_shape_placements(
        result.placements,
        sheet.source_length_mm,
        sheet.source_width_mm,
        5.0,
        5.0,
    ) == (True, "")
