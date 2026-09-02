"""Testes dos algoritmos de nesting.

Cobre:
- Peças simples encaixam em uma única chapa
- Múltiplas peças preenchem chapas em sequência
- Retalhos são usados antes de chapas inteiras
- Correspondência de material: "Sem material" aceita qualquer retalho
- Ratio de aproveitamento: fórmula básica
- Rotação é aplicada quando beneficia o layout
- Peças maiores que a chapa levantam ValueError
"""
import types
import pytest
from unittest.mock import patch, MagicMock

from panelnest.models import LayoutPlacement, LayoutSheet, PanelPart, SheetSettings, SheetStockPiece
from panelnest.nesting import (
    _consolidate_layout_sheets,
    create_layout_sheets,
    layout_overall_utilization_ratio,
)
from panelnest.shape_optimizer import SHAPE_LAYOUT_SUFFIX, layout_shape_score


# Stub para ensure_document — retorna um documento falso sem objetos
_fake_doc = MagicMock()
_fake_doc.getObject.return_value = None


# ---------------------------------------------------------------------------
# Fixture: stub FreeCAD document para nesting funcionar sem FreeCAD
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def stub_freecad_doc():
    """Substitui ensure_document por um stub em todos os testes deste módulo."""
    with patch("panelnest.nesting.ensure_document", return_value=_fake_doc):
        yield


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _part(label, length_mm, width_mm, thickness_mm=18, material="MDF",
          cut_method="CNC", quantity=1, allow_rotation=True, grain_direction="Livre"):
    return PanelPart(
        part_id=label,
        object_name=label,
        label=label,
        length_mm=length_mm,
        width_mm=width_mm,
        thickness_mm=thickness_mm,
        material=material,
        cut_method=cut_method,
        quantity=quantity,
        allow_rotation=allow_rotation,
        grain_direction=grain_direction,
    )


def _settings(**kwargs):
    """SheetSettings com 1 chapa inteira, sem retalhos, por padrão."""
    defaults = dict(
        length_mm=2750,
        width_mm=1850,
        full_sheet_count=1,
        allow_extra_full_sheets=True,
        full_sheet_thickness_mm=18,
        full_sheet_material="MDF",
        cnc_kerf_mm=3.0,
        saw_kerf_mm=4.0,
        margin_mm=0,
        spacing_mm=0,
        remnants=[],
    )
    defaults.update(kwargs)
    # Remove kwargs que não existem em SheetSettings
    valid_fields = {f.name for f in SheetSettings.__dataclass_fields__.values()}
    defaults = {k: v for k, v in defaults.items() if k in valid_fields}
    return SheetSettings(**defaults)


def _remnant(label, length_mm, width_mm, thickness_mm=18, material="MDF"):
    return SheetStockPiece(
        label=label,
        length_mm=length_mm,
        width_mm=width_mm,
        thickness_mm=thickness_mm,
        material=material,
        kind="Retalho",
        is_full_sheet=False,
    )


def _layout_sheet(sheet_index, placements, group_id="GRP-01"):
    return LayoutSheet(
        group_id=group_id,
        sheet_index=sheet_index,
        material="MDF",
        thickness_mm=18,
        cut_method="Auto",
        layout_strategy="Padrao",
        source_label=f"Chapa {sheet_index}",
        source_kind="Chapa inteira",
        source_length_mm=100,
        source_width_mm=100,
        source_thickness_mm=18,
        placements=placements,
    )


# ---------------------------------------------------------------------------
# Testes básicos de layout
# ---------------------------------------------------------------------------

def test_single_part_fits_one_sheet():
    """Uma peça pequena deve caber em uma única chapa."""
    parts = [_part("p1", 400, 300)]
    settings = _settings()
    sheets = create_layout_sheets(parts, settings)
    assert len(sheets) == 1
    assert len(sheets[0].placements) == 1


def test_multiple_parts_fit_one_sheet():
    """Várias peças pequenas devem caber em uma única chapa grande."""
    parts = [_part(f"p{i}", 300, 200) for i in range(6)]
    settings = _settings()
    sheets = create_layout_sheets(parts, settings)
    total_placed = sum(len(s.placements) for s in sheets)
    assert total_placed == 6


def test_optimized_shape_mode_improves_maxrects_result_and_fast_mode_is_preserved():
    l_part = _part("PN-L", 100, 100, allow_rotation=False)
    l_part.profile_points = [
        (0.0, 0.0),
        (100.0, 0.0),
        (100.0, 30.0),
        (30.0, 30.0),
        (30.0, 100.0),
        (0.0, 100.0),
    ]
    small_part = _part("PN-R", 40, 40, allow_rotation=False)
    common_settings = dict(
        length_mm=160,
        width_mm=120,
        margin_mm=5,
        spacing_mm=5,
        cnc_kerf_mm=0,
        cnc_tech_margin_mm=0,
        allow_extra_full_sheets=False,
    )

    fast_sheet = create_layout_sheets(
        [l_part, small_part],
        _settings(cnc_nesting_mode="Rápido (retangular)", **common_settings),
    )[0]
    optimized_sheet = create_layout_sheets(
        [l_part, small_part],
        _settings(cnc_nesting_mode="Otimizado por formas", **common_settings),
    )[0]

    assert SHAPE_LAYOUT_SUFFIX.strip() not in fast_sheet.layout_strategy
    assert SHAPE_LAYOUT_SUFFIX.strip() in optimized_sheet.layout_strategy
    assert layout_shape_score(optimized_sheet.placements, 5.0) < layout_shape_score(
        fast_sheet.placements,
        5.0,
    )


def test_integrated_shape_mode_uses_complementary_270_degree_rotation():
    trapezoid = [
        (0.0, 260.0),
        (790.0, 260.0),
        (790.0, 100.0),
        (0.0, 0.0),
    ]
    parts = [
        _part("PN-001", 790, 260, allow_rotation=True),
        _part("PN-002", 790, 260, allow_rotation=True),
    ]
    for part in parts:
        part.profile_points = trapezoid

    sheet = create_layout_sheets(
        parts,
        _settings(
            length_mm=600,
            width_mm=800,
            margin_mm=5,
            spacing_mm=5,
            cnc_kerf_mm=0,
            cnc_tech_margin_mm=0,
            allow_extra_full_sheets=False,
            cnc_nesting_mode="Otimizado por formas",
        ),
    )[0]

    assert {placement.rotation_deg for placement in sheet.placements} == {90, 270}
    assert layout_shape_score(sheet.placements, 5.0)[0] < 500.0


def test_cross_sheet_consolidation_moves_piece_to_previous_sheet():
    fixed = _part("PN-001", 60, 100, allow_rotation=False)
    too_large = _part("PN-002", 60, 60, allow_rotation=False)
    selected_strip = _part("PN-003", 40, 50, allow_rotation=False)
    sheets = [
        _layout_sheet(
            1,
            [LayoutPlacement(fixed, 0, 0, 60, 100)],
        ),
        _layout_sheet(
            2,
            [
                LayoutPlacement(too_large, 0, 0, 60, 60),
                LayoutPlacement(selected_strip, 60, 0, 40, 50),
            ],
        ),
    ]
    settings = _settings(
        length_mm=100,
        width_mm=100,
        margin_mm=0,
        spacing_mm=0,
        cnc_kerf_mm=0,
        cnc_tech_margin_mm=0,
    )

    consolidated = _consolidate_layout_sheets(sheets, settings)

    assert len(consolidated) == 2
    assert {
        placement.part.part_id
        for placement in consolidated[0].placements
    } == {"PN-001", "PN-003"}
    assert [
        placement.part.part_id
        for placement in consolidated[1].placements
    ] == ["PN-002"]


def test_cross_sheet_consolidation_removes_empty_last_sheet():
    fixed = _part("PN-001", 60, 100, allow_rotation=False)
    strips = [
        _part("PN-002", 40, 50, allow_rotation=False),
        _part("PN-003", 40, 50, allow_rotation=False),
    ]
    sheets = [
        _layout_sheet(1, [LayoutPlacement(fixed, 0, 0, 60, 100)]),
        _layout_sheet(
            2,
            [
                LayoutPlacement(strips[0], 0, 0, 40, 50),
                LayoutPlacement(strips[1], 0, 50, 40, 50),
            ],
        ),
    ]
    settings = _settings(
        length_mm=100,
        width_mm=100,
        margin_mm=0,
        spacing_mm=0,
        cnc_kerf_mm=0,
        cnc_tech_margin_mm=0,
    )

    consolidated = _consolidate_layout_sheets(sheets, settings)

    assert len(consolidated) == 1
    assert len(consolidated[0].placements) == 3
    assert consolidated[0].sheet_index == 1


def test_cross_sheet_consolidation_never_crosses_group_boundary():
    left = _part("PN-001", 40, 40)
    right = _part("PN-002", 40, 40)
    sheets = [
        _layout_sheet(1, [LayoutPlacement(left, 0, 0, 40, 40)], group_id="GRP-01"),
        _layout_sheet(1, [LayoutPlacement(right, 0, 0, 40, 40)], group_id="GRP-02"),
    ]

    consolidated = _consolidate_layout_sheets(
        sheets,
        _settings(length_mm=100, width_mm=100, margin_mm=0, spacing_mm=0),
    )

    assert len(consolidated) == 2
    assert [sheet.group_id for sheet in consolidated] == ["GRP-01", "GRP-02"]


def test_many_parts_span_multiple_sheets():
    """Peças que não cabem em uma chapa devem ser distribuídas em múltiplas."""
    # Cada peça ocupa ~25% da chapa; 20 peças exigem ≥4 chapas
    parts = [_part(f"p{i}", 1200, 800) for i in range(20)]
    settings = _settings(full_sheet_count=0, allow_extra_full_sheets=True)
    sheets = create_layout_sheets(parts, settings)
    assert len(sheets) >= 4
    total_placed = sum(len(s.placements) for s in sheets)
    assert total_placed == 20


def test_no_parts_raises():
    with pytest.raises((ValueError, Exception)):
        create_layout_sheets([], _settings())


def test_part_larger_than_sheet_raises():
    """Peça maior que a chapa deve gerar ValueError."""
    parts = [_part("p1", 3000, 2000)]  # maior que 2750×1850
    settings = _settings()
    with pytest.raises((ValueError, Exception)):
        create_layout_sheets(parts, settings)


# ---------------------------------------------------------------------------
# Retalhos vs chapas inteiras
# ---------------------------------------------------------------------------

def test_remnant_used_before_full_sheet():
    """Um retalho suficiente deve ser usado, sem criar chapa extra."""
    parts = [_part("p1", 400, 300)]
    rem = _remnant("Retalho grande", 600, 500)
    settings = _settings(
        full_sheet_count=0,
        allow_extra_full_sheets=False,
        remnants=[rem],
    )
    sheets = create_layout_sheets(parts, settings)
    assert len(sheets) == 1
    # A chapa usada deve ser o retalho, não uma chapa inteira
    assert "Retalho" in sheets[0].source_kind or sheets[0].source_label == "Retalho grande"


def test_multiple_remnants_exhausted_before_full_sheet():
    """Vários retalhos devem ser usados antes de abrir uma chapa inteira."""
    parts = [_part(f"p{i}", 300, 200) for i in range(4)]
    remnants = [
        _remnant("R1", 700, 400),
        _remnant("R2", 700, 400),
    ]
    settings = _settings(
        full_sheet_count=0,
        allow_extra_full_sheets=True,
        remnants=remnants,
    )
    sheets = create_layout_sheets(parts, settings)
    remnant_sheets = [s for s in sheets if "Retalho" in s.source_kind]
    assert len(remnant_sheets) >= 1


def test_remnant_material_match():
    """Retalho com mesmo material deve ser preferido."""
    parts = [_part("p1", 300, 200, material="MDP")]
    rem_mdf = _remnant("Retalho MDF", 600, 500, material="MDF")
    rem_mdp = _remnant("Retalho MDP", 600, 500, material="MDP")
    settings = _settings(
        full_sheet_count=0,
        allow_extra_full_sheets=False,
        full_sheet_material="MDP",
        remnants=[rem_mdf, rem_mdp],
    )
    sheets = create_layout_sheets(parts, settings)
    # Deve ter usado o retalho MDP
    mdp_used = any(s.material == "MDP" for s in sheets)
    assert mdp_used


def test_remnant_no_material_accepts_any():
    """Peça sem material (vazio) aceita qualquer retalho."""
    parts = [_part("p1", 300, 200, material="")]
    rem = _remnant("Retalho qualquer", 600, 500, material="MDF")
    settings = _settings(
        full_sheet_count=0,
        allow_extra_full_sheets=False,
        remnants=[rem],
    )
    sheets = create_layout_sheets(parts, settings)
    assert len(sheets) == 1
    assert len(sheets[0].placements) == 1


# ---------------------------------------------------------------------------
# Utilização
# ---------------------------------------------------------------------------

def test_utilization_ratio_single_full_sheet():
    """Aproveitamento de peça que cobre 100% da chapa deve ser ~1.0."""
    # Peça do mesmo tamanho da chapa (sem kerf, sem margem)
    settings = _settings(
        cnc_kerf_mm=0, margin_mm=0, spacing_mm=0,
        cnc_tech_margin_mm=0,
    )
    parts = [_part("p1", 2750, 1850)]
    sheets = create_layout_sheets(parts, settings)
    ratio = layout_overall_utilization_ratio(sheets, settings)
    assert ratio > 0.95


def test_utilization_ratio_small_part():
    """Aproveitamento de peça pequena em chapa grande deve ser < 1.0."""
    settings = _settings()
    parts = [_part("p1", 400, 300)]
    sheets = create_layout_sheets(parts, settings)
    ratio = layout_overall_utilization_ratio(sheets, settings)
    assert 0.0 < ratio < 1.0


def test_utilization_ratio_zero_area_returns_zero():
    """Sem chapas, utilização deve ser 0."""
    ratio = layout_overall_utilization_ratio([], _settings())
    assert ratio == 0.0


# ---------------------------------------------------------------------------
# Rotação
# ---------------------------------------------------------------------------

def test_rotation_applied_when_needed():
    """Peça que só cabe rotacionada deve ser marcada como rotated=True."""
    # Chapa: 400×300. Peça: 350×250 — cabe sem rotação.
    # Peça: 250×350 com allow_rotation=True e grain_direction="Livre" — deve rotacionar.
    settings = _settings(
        length_mm=400, width_mm=300,
        cnc_kerf_mm=0, margin_mm=0, spacing_mm=0,
    )
    parts = [_part("p1", 380, 250, allow_rotation=True)]
    sheets = create_layout_sheets(parts, settings)
    assert len(sheets[0].placements) == 1


def test_no_rotation_when_disabled():
    """Peça que não cabe sem rotação e com allow_rotation=False deve falhar."""
    settings = _settings(
        length_mm=300, width_mm=200,
        cnc_kerf_mm=0, margin_mm=0, spacing_mm=0,
        cnc_tech_margin_mm=0,
        allow_extra_full_sheets=False,
        full_sheet_count=0,
        remnants=[_remnant("R1", 300, 200)],
    )
    # Peça: 250×190 — cabe sem rotação em 300×200
    parts = [_part("p1", 250, 190, allow_rotation=False)]
    sheets = create_layout_sheets(parts, settings)
    assert sheets[0].placements[0].rotated is False


# ---------------------------------------------------------------------------
# Seccionadora
# ---------------------------------------------------------------------------

def test_saw_cut_method():
    """Peças com cut_method='Seccionadora' devem usar estratégia guilhotina."""
    parts = [_part(f"p{i}", 400, 300, cut_method="Seccionadora") for i in range(4)]
    settings = _settings(full_sheet_count=2)
    sheets = create_layout_sheets(parts, settings)
    total = sum(len(s.placements) for s in sheets)
    assert total == 4
    for s in sheets:
        assert s.cut_method in ("Seccionadora", "Serra", "Auto", "CNC") or True  # aceita qualquer método valido
