"""Testes de compensação de espessura de fita de borda."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

for mod in ["FreeCAD", "FreeCADGui", "Part"]:
    if mod not in sys.modules:
        sys.modules[mod] = None  # type: ignore

from panelnest.models import PanelPart
from panelnest.edge_compensation import (
    compensated_cut_dimensions,
    apply_cut_compensation,
    compensation_summary,
)


def _part(**kwargs):
    defaults = dict(
        part_id="1", object_name="Box", label="PN-001",
        length_mm=600, width_mm=400, thickness_mm=18,
        edge_band_top=False, edge_band_bottom=False,
        edge_band_left=False, edge_band_right=False,
    )
    defaults.update(kwargs)
    return PanelPart(**defaults)


def test_no_tape_no_change():
    part = _part()
    assert compensated_cut_dimensions(part, 0.0) == (600, 400)


def test_two_length_faces():
    part = _part(edge_band_left=True, edge_band_right=True)
    cut_l, cut_w = compensated_cut_dimensions(part, 0.5)
    assert cut_l == 599.0  # 600 - 0.5 - 0.5
    assert cut_w == 400.0


def test_two_width_faces():
    part = _part(edge_band_top=True, edge_band_bottom=True)
    cut_l, cut_w = compensated_cut_dimensions(part, 0.5)
    assert cut_l == 600.0
    assert cut_w == 399.0  # 400 - 0.5 - 0.5


def test_all_four_faces():
    part = _part(
        edge_band_top=True, edge_band_bottom=True,
        edge_band_left=True, edge_band_right=True,
    )
    cut_l, cut_w = compensated_cut_dimensions(part, 1.0)
    assert cut_l == 598.0
    assert cut_w == 398.0


def test_apply_cut_compensation_does_not_mutate():
    part = _part(edge_band_left=True, edge_band_right=True)
    compensated = apply_cut_compensation(part, 0.5)
    assert part.length_mm == 600  # original inalterado
    assert compensated.length_mm == 599.0


def test_apply_cut_compensation_zero_tape_returns_same():
    part = _part(edge_band_left=True)
    result = apply_cut_compensation(part, 0.0)
    assert result is part  # mesma referência


def test_compensation_summary_no_band():
    part = _part()
    assert compensation_summary(part, 0.5) == ""


def test_compensation_summary_with_band():
    part = _part(edge_band_left=True, edge_band_right=True)
    s = compensation_summary(part, 0.5)
    assert "599" in s
    assert "600" in s


def test_never_below_one_mm():
    """Peça muito pequena não pode ter dimensão de corte < 1mm."""
    part = _part(
        length_mm=1.0, width_mm=1.0,
        edge_band_left=True, edge_band_right=True,
        edge_band_top=True, edge_band_bottom=True,
    )
    cut_l, cut_w = compensated_cut_dimensions(part, 10.0)
    assert cut_l >= 1.0
    assert cut_w >= 1.0
