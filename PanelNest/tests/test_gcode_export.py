"""Testes para panelnest.gcode_export."""

import tempfile
import os

from panelnest.gcode_export import (
    export_nbm_sheet,
    export_nbm_bundle,
    _nbm_content,
    _nbm_slugify,
    _nbm_part_block,
    _nbm_drill_section,
)
from panelnest.models import (
    LayoutSheet,
    LayoutPlacement,
    PanelPart,
    SheetSettings,
)


def _make_sheet(placements=None):
    return LayoutSheet(
        group_id="G1",
        sheet_index=1,
        material="MDF",
        thickness_mm=18,
        cut_method="CNC",
        layout_strategy="MaxRects",
        source_label="Chapa 1",
        source_kind="Chapa",
        source_length_mm=2750,
        source_width_mm=1830,
        source_thickness_mm=18,
        placements=placements or [],
    )


def _make_part(part_id="PN-001", label="Lateral", length=600, width=400, thickness=18):
    return PanelPart(
        part_id=part_id,
        object_name="Body",
        label=label,
        length_mm=length,
        width_mm=width,
        thickness_mm=thickness,
    )


def test_nbm_content_has_sections():
    part = _make_part()
    placement = LayoutPlacement(
        part=part, x_mm=10, y_mm=10,
        placed_length_mm=600, placed_width_mm=400,
    )
    sheet = _make_sheet([placement])
    content = _nbm_content(sheet, SheetSettings())
    assert "[PROGRAMA]" in content
    assert "[PECAS]" in content
    assert "[FIM]" in content
    assert "RETANGULO" in content


def test_nbm_content_empty_sheet():
    sheet = _make_sheet([])
    content = _nbm_content(sheet, SheetSettings())
    assert "[PECAS]" in content
    assert "RETANGULO" not in content


def test_nbm_content_multiple_parts():
    parts = [_make_part(f"PN-{i:03d}", f"Part {i}") for i in range(5)]
    placements = [
        LayoutPlacement(
            part=p, x_mm=i * 100, y_mm=10,
            placed_length_mm=90, placed_width_mm=50,
        )
        for i, p in enumerate(parts)
    ]
    sheet = _make_sheet(placements)
    content = _nbm_content(sheet, SheetSettings())
    assert content.count("RETANGULO") == 5
    assert "Pecas: 5" in content


def test_nbm_drill_section():
    part = _make_part()
    part.holes = [
        {"x_mm": 21.5, "y_mm": 100, "diameter_mm": 35, "depth_mm": 12.5, "face": "top", "description": "Copo"},
        {"x_mm": 50, "y_mm": 50, "diameter_mm": 5, "depth_mm": 10, "face": "top", "description": "Parafuso"},
    ]
    placement = LayoutPlacement(
        part=part, x_mm=10, y_mm=10,
        placed_length_mm=600, placed_width_mm=400,
    )
    sheet = _make_sheet([placement])
    content = _nbm_content(sheet, SheetSettings())
    assert "[FUROS]" in content
    assert "REBAIXO" in content  # 35mm >= 15mm -> REBAIXO
    assert "DRILL" in content    # 5mm < 15mm -> DRILL


def test_export_nbm_sheet_creates_file():
    sheet = _make_sheet([
        LayoutPlacement(
            part=_make_part(), x_mm=10, y_mm=10,
            placed_length_mm=600, placed_width_mm=400,
        )
    ])
    with tempfile.NamedTemporaryFile(suffix=".nbm", delete=False) as f:
        path = f.name
    try:
        export_nbm_sheet(sheet, SheetSettings(), path)
        assert os.path.exists(path)
        with open(path, "r") as f:
            content = f.read()
        assert "[PROGRAMA]" in content
    finally:
        os.unlink(path)


def test_export_nbm_bundle():
    sheets = [_make_sheet([
        LayoutPlacement(
            part=_make_part(f"PN-{i:03d}"), x_mm=10, y_mm=10,
            placed_length_mm=600, placed_width_mm=400,
        )
    ]) for i in range(3)]

    with tempfile.TemporaryDirectory() as tmpdir:
        paths = export_nbm_bundle(sheets, SheetSettings(), tmpdir)
        assert len(paths) == 3
        for p in paths:
            assert os.path.exists(p)
            assert p.endswith(".nbm")


def test_slugify():
    assert _nbm_slugify("MDF 18mm") == "MDF_18mm"
    assert _nbm_slugify("") == "chapa"
    assert _nbm_slugify("Compensado Pinus 12/mm") == "Compensado_Pinus_12mm"


def test_part_block_rotated():
    part = _make_part()
    placement = LayoutPlacement(
        part=part, x_mm=10, y_mm=10,
        placed_length_mm=400, placed_width_mm=600,
        rotated=True,
    )
    block = _nbm_part_block(placement, 1)
    assert "[ROTACIONADO]" in block


def test_nbm_holes_follow_180_and_270_degree_rotations():
    part = _make_part()
    part.holes = [
        {"x_mm": 50, "y_mm": 100, "diameter_mm": 5, "depth_mm": 10},
    ]
    placement_180 = LayoutPlacement(
        part=part,
        x_mm=10,
        y_mm=20,
        placed_length_mm=600,
        placed_width_mm=400,
        rotation_deg=180,
    )
    drill_180 = "\n".join(_nbm_drill_section([placement_180]))
    assert "X=560.000 Y=320.000" in drill_180

    placement_270 = LayoutPlacement(
        part=part,
        x_mm=10,
        y_mm=20,
        placed_length_mm=400,
        placed_width_mm=600,
        rotated=True,
        rotation_deg=270,
    )
    drill_270 = "\n".join(_nbm_drill_section([placement_270]))
    assert "X=110.000 Y=570.000" in drill_270
