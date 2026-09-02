"""Regressões de perfis não retangulares no layout."""

import xml.etree.ElementTree as ET

from panelnest.geometry import _profile_is_axis_aligned_rectangle
from panelnest.layout_model import _placed_profile_points
from panelnest.models import LayoutPlacement, LayoutSheet, PanelPart
from panelnest.reports import _layout_sheet_svg_document


def _placement(profile, rotated=False, length=700.0, width=260.0, rotation_deg=None):
    part = PanelPart(
        part_id="PN-001",
        object_name="Support",
        label="Painel trapezoidal",
        length_mm=length,
        width_mm=width,
        thickness_mm=18.0,
        profile_points=profile,
    )
    return LayoutPlacement(
        part=part,
        x_mm=10.0,
        y_mm=20.0,
        placed_length_mm=width if rotated else length,
        placed_width_mm=length if rotated else width,
        rotated=rotated,
        rotation_deg=rotation_deg,
    )


def test_axis_aligned_rectangle_is_not_kept_as_custom_profile():
    rectangle = [(0.0, 0.0), (700.0, 0.0), (700.0, 260.0), (0.0, 260.0)]
    assert _profile_is_axis_aligned_rectangle(rectangle) is True


def test_rectangle_with_collinear_edge_points_is_still_recognized():
    rectangle = [
        (0.0, 0.0),
        (350.0, 0.0),
        (700.0, 0.0),
        (700.0, 260.0),
        (0.0, 260.0),
    ]
    assert _profile_is_axis_aligned_rectangle(rectangle) is True


def test_four_corner_trapezoid_is_kept_as_custom_profile():
    trapezoid = [(0.0, 0.0), (700.0, 0.0), (700.0, 130.0), (0.0, 260.0)]
    assert _profile_is_axis_aligned_rectangle(trapezoid) is False


def test_rotated_profile_uses_same_canonical_counterclockwise_transform():
    trapezoid = [(0.0, 0.0), (700.0, 0.0), (700.0, 130.0), (0.0, 260.0)]
    placement = _placement(trapezoid, rotated=True)

    assert _placed_profile_points(placement) == [
        (260.0, 0.0),
        (260.0, 700.0),
        (130.0, 700.0),
        (0.0, 0.0),
    ]


def test_unrotated_profile_is_preserved():
    trapezoid = [(0.0, 0.0), (700.0, 0.0), (700.0, 130.0), (0.0, 260.0)]
    placement = _placement(trapezoid)
    assert _placed_profile_points(placement) == trapezoid


def test_profile_supports_180_and_270_degree_rotations():
    trapezoid = [(0.0, 0.0), (700.0, 0.0), (700.0, 130.0), (0.0, 260.0)]

    placement_180 = _placement(trapezoid, rotation_deg=180)
    assert _placed_profile_points(placement_180) == [
        (700.0, 260.0),
        (0.0, 260.0),
        (0.0, 130.0),
        (700.0, 0.0),
    ]

    placement_270 = _placement(
        trapezoid,
        rotated=True,
        rotation_deg=270,
    )
    assert _placed_profile_points(placement_270) == [
        (0.0, 700.0),
        (0.0, 0.0),
        (130.0, 0.0),
        (260.0, 700.0),
    ]


def test_standalone_svg_preserves_real_trapezoid_profile():
    trapezoid = [(0.0, 260.0), (700.0, 260.0), (700.0, 100.0), (0.0, 0.0)]
    placement = _placement(trapezoid)
    sheet = LayoutSheet(
        group_id="GRP-01",
        sheet_index=1,
        material="MDF",
        thickness_mm=15.0,
        cut_method="CNC",
        layout_strategy="MaxRects - BSSF",
        source_label="Chapa inteira",
        source_kind="Chapa inteira",
        source_length_mm=1000.0,
        source_width_mm=500.0,
        source_thickness_mm=15.0,
        placements=[placement],
    )

    svg_text = _layout_sheet_svg_document(sheet, document_label="Teste")
    root = ET.fromstring(svg_text)

    assert root.attrib["width"] == "1000mm"
    assert "M 10 280 L 710 280 L 710 120 L 10 20 Z" in svg_text


def test_standalone_svg_transforms_profile_and_hole_at_270_degrees():
    trapezoid = [(0.0, 260.0), (700.0, 260.0), (700.0, 100.0), (0.0, 0.0)]
    placement = _placement(
        trapezoid,
        rotated=True,
        rotation_deg=270,
    )
    placement.part.holes = [
        {"x_mm": 100.0, "y_mm": 50.0, "diameter_mm": 10.0},
    ]
    sheet = LayoutSheet(
        group_id="GRP-01",
        sheet_index=1,
        material="MDF",
        thickness_mm=15.0,
        cut_method="CNC",
        layout_strategy="MaxRects - BSSF + Formas",
        source_label="Chapa inteira",
        source_kind="Chapa inteira",
        source_length_mm=1000.0,
        source_width_mm=800.0,
        source_thickness_mm=15.0,
        placements=[placement],
    )

    svg_text = _layout_sheet_svg_document(sheet, document_label="Teste 270")

    assert "M 270 720 L 270 20 L 110 20 L 10 720 Z" in svg_text
    assert 'cx="60"' in svg_text
    assert 'cy="620"' in svg_text
