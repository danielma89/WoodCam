"""Transformações completas de orientação usadas por layout, CAM e exportação."""

from panelnest.layout_model import _placement_edge_band_sides
from panelnest.models import LayoutPlacement, PanelPart
from panelnest.orientation import (
    placement_rotation_deg,
    transform_placement_point,
)


def _placement(rotation_deg):
    part = PanelPart(
        part_id="PN-001",
        object_name="Part",
        label="Painel",
        length_mm=100.0,
        width_mm=60.0,
        thickness_mm=15.0,
        edge_band_top=True,
        edge_band_left=True,
    )
    return LayoutPlacement(
        part=part,
        x_mm=0.0,
        y_mm=0.0,
        placed_length_mm=60.0 if rotation_deg in (90, 270) else 100.0,
        placed_width_mm=100.0 if rotation_deg in (90, 270) else 60.0,
        rotated=rotation_deg in (90, 270),
        rotation_deg=rotation_deg,
    )


def test_local_point_transform_for_all_quarter_turns():
    source = (20.0, 15.0)
    expected = {
        0: (20.0, 15.0),
        90: (45.0, 20.0),
        180: (80.0, 45.0),
        270: (15.0, 80.0),
    }
    for rotation_deg, expected_point in expected.items():
        placement = _placement(rotation_deg)
        assert placement_rotation_deg(placement) == rotation_deg
        assert transform_placement_point(placement, *source) == expected_point


def test_legacy_rotated_flag_still_means_90_degrees():
    placement = _placement(0)
    placement.rotation_deg = None
    placement.rotated = True
    assert placement_rotation_deg(placement) == 90


def test_edge_band_sides_follow_180_and_270_degree_rotations():
    sides_180 = {
        item["source_side_key"]: item["layout_side_key"]
        for item in _placement_edge_band_sides(_placement(180))
    }
    assert sides_180 == {"top": "bottom", "left": "right"}

    sides_270 = {
        item["source_side_key"]: item["layout_side_key"]
        for item in _placement_edge_band_sides(_placement(270))
    }
    assert sides_270 == {"top": "right", "left": "top"}
