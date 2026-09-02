"""Testes para panelnest.nfp_nesting."""

from panelnest.nfp_nesting import (
    _polygon_area,
    _point_in_polygon,
    _placement_collides,
    _detect_rectangular_notches,
    _maximal_free_rectangles,
    NotchRegion,
    NonRectProfile,
    find_notch_candidates,
)
from panelnest.models import PanelPart, LayoutPlacement


def test_polygon_area_rectangle():
    verts = [(0, 0), (100, 0), (100, 50), (0, 50)]
    assert abs(_polygon_area(verts) - 5000.0) < 0.1


def test_polygon_area_triangle():
    verts = [(0, 0), (100, 0), (50, 50)]
    assert abs(_polygon_area(verts) - 2500.0) < 0.1


def test_polygon_area_empty():
    assert _polygon_area([]) == 0.0
    assert _polygon_area([(0, 0)]) == 0.0
    assert _polygon_area([(0, 0), (1, 0)]) == 0.0


def test_polygon_area_l_shape():
    # L-shape: 100x100 with 50x50 notch in top-right
    verts = [(0, 0), (100, 0), (100, 50), (50, 50), (50, 100), (0, 100)]
    expected = 100 * 100 - 50 * 50  # 7500
    assert abs(_polygon_area(verts) - expected) < 0.1


def test_point_in_polygon_inside():
    square = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert _point_in_polygon(50, 50, square) is True


def test_point_in_polygon_outside():
    square = [(0, 0), (100, 0), (100, 100), (0, 100)]
    assert _point_in_polygon(150, 50, square) is False
    assert _point_in_polygon(-10, 50, square) is False


def test_point_in_polygon_l_shape():
    # L-shape notch in top-right corner
    l_shape = [(0, 0), (100, 0), (100, 50), (50, 50), (50, 100), (0, 100)]
    assert _point_in_polygon(25, 75, l_shape) is True   # Inside the L
    assert _point_in_polygon(75, 75, l_shape) is False   # In the notch area


def test_placement_collides_overlap():
    existing = [LayoutPlacement(
        part=PanelPart("PN-001", "B", "L", 100, 50, 18),
        x_mm=10, y_mm=10, placed_length_mm=100, placed_width_mm=50,
    )]
    # Overlapping
    assert _placement_collides(50, 20, 80, 30, existing) is True
    # Not overlapping
    assert _placement_collides(200, 200, 50, 30, existing) is False


def test_placement_collides_with_spacing():
    existing = [LayoutPlacement(
        part=PanelPart("PN-001", "B", "L", 100, 50, 18),
        x_mm=10, y_mm=10, placed_length_mm=100, placed_width_mm=50,
    )]
    # Would be adjacent without spacing, but collides with 5mm spacing
    assert _placement_collides(110, 10, 50, 50, existing, spacing_mm=5.0) is True
    # Far enough
    assert _placement_collides(120, 10, 50, 50, existing, spacing_mm=5.0) is False


def test_detect_notches_l_shape():
    # L-shape: 200x200 with notch at top-right (100x100)
    l_shape = [(0, 0), (200, 0), (200, 100), (100, 100), (100, 200), (0, 200)]
    notches = _detect_rectangular_notches(l_shape, 200, 200, "PN-001")
    # Should detect the notch region
    assert len(notches) >= 1
    # At least one notch should be reasonably large
    total_notch_area = sum(n.length_mm * n.width_mm for n in notches)
    assert total_notch_area > 2000  # Expected ~10000 (100x100)


def test_detect_notches_rectangle_no_notch():
    # A simple rectangle should produce no significant notches
    rect = [(0, 0), (100, 0), (100, 50), (0, 50)]
    notches = _detect_rectangular_notches(rect, 100, 50, "PN-001")
    assert len(notches) == 0


def test_find_notch_candidates_no_profile():
    part = PanelPart("PN-001", "B", "L", 200, 200, 18)
    placement = LayoutPlacement(
        part=part, x_mm=10, y_mm=10,
        placed_length_mm=200, placed_width_mm=200,
    )
    small = PanelPart("PN-002", "B2", "S", 30, 30, 18, allow_rotation=True)
    # No profiles -> no candidates
    candidates = find_notch_candidates(placement, {}, small)
    assert candidates == []


def test_find_notch_candidates_with_profile():
    part = PanelPart("PN-001", "B", "L", 200, 200, 18)
    placement = LayoutPlacement(
        part=part, x_mm=0, y_mm=0,
        placed_length_mm=200, placed_width_mm=200,
    )
    small = PanelPart("PN-002", "B2", "S", 50, 50, 18, allow_rotation=True)

    profile = NonRectProfile(
        part_id="PN-001",
        bbox_length_mm=200,
        bbox_width_mm=200,
        notches=[NotchRegion(
            x_offset_mm=100, y_offset_mm=100,
            length_mm=95, width_mm=95,
            host_part_id="PN-001",
        )],
        fill_ratio=0.75,
    )

    candidates = find_notch_candidates(placement, {"PN-001": profile}, small, spacing_mm=3)
    assert len(candidates) > 0
    # Small part (50x50) should fit in the 95x95 notch
    for c in candidates:
        assert c["placed_length_mm"] <= 95
        assert c["placed_width_mm"] <= 95
