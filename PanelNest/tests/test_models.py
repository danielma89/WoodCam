"""Testes dos modelos de dados principais."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Stub FreeCAD para testes sem FreeCAD instalado
import types
for mod in ["FreeCAD", "FreeCADGui", "Part", "Draft"]:
    if mod not in sys.modules:
        sys.modules[mod] = None  # type: ignore

from panelnest.models import (
    PanelPart, SheetSettings, CutProcessProfile,
    SheetStockPiece, LayoutPlacement, LayoutSheet, GeneratedRemnant,
)
from panelnest.constants import (
    DEFAULT_SHEET_LENGTH_MM, DEFAULT_SHEET_WIDTH_MM,
    DEFAULT_CNC_KERF_MM, DEFAULT_SAW_KERF_MM,
    DEFAULT_CNC_NESTING_MODE,
)


def test_panel_part_defaults():
    part = PanelPart(
        part_id="1", object_name="Box", label="PN-001 - Lateral",
        length_mm=600, width_mm=400, thickness_mm=18,
    )
    assert part.quantity == 1
    assert part.material == ""
    assert part.cut_method == "Auto"
    assert part.allow_rotation is True
    assert part.grain_direction == "Livre"
    assert part.edge_band_top is False
    assert part.edge_band_bottom is False
    assert part.edge_band_left is False
    assert part.edge_band_right is False


def test_sheet_settings_defaults():
    s = SheetSettings()
    assert s.length_mm == DEFAULT_SHEET_LENGTH_MM
    assert s.width_mm == DEFAULT_SHEET_WIDTH_MM
    assert s.cnc_kerf_mm == DEFAULT_CNC_KERF_MM
    assert s.saw_kerf_mm == DEFAULT_SAW_KERF_MM
    assert s.cnc_nesting_mode == DEFAULT_CNC_NESTING_MODE
    assert s.remnants == []
    assert s.edge_band_thickness_mm == 0.0


def test_sheet_settings_edge_band_thickness():
    s = SheetSettings(edge_band_thickness_mm=0.5)
    assert s.edge_band_thickness_mm == 0.5


def test_sheet_stock_piece_cost():
    piece = SheetStockPiece(
        label="MDF 18mm", length_mm=2750, width_mm=1850,
        thickness_mm=18, material="MDF", cost_per_sheet=120.0,
    )
    assert piece.cost_per_sheet == 120.0


def test_generated_remnant_fields():
    r = GeneratedRemnant(
        group_id="grp1", sheet_index=0,
        source_label="Chapa 1", source_kind="Chapa inteira",
        label="[Gerado] 500×300",
        x_mm=100, y_mm=200, length_mm=500, width_mm=300,
        area_mm2=150000, material="MDF", thickness_mm=18,
        cut_method="CNC", layout_strategy="MaxRects - BSSF",
    )
    assert r.area_mm2 == 150000
    assert r.length_mm == 500


def test_layout_sheet_empty():
    ls = LayoutSheet(
        group_id="g", sheet_index=0, material="MDF", thickness_mm=18,
        cut_method="CNC", layout_strategy="Auto",
        source_label="Chapa 1", source_kind="Chapa inteira",
        source_length_mm=2750, source_width_mm=1850, source_thickness_mm=18,
    )
    assert ls.placements == []
    assert ls.cut_steps == []
