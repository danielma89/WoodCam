"""Testes para panelnest.parametric_cabinet."""

from panelnest.parametric_cabinet import (
    CABINET_TEMPLATES,
    CabinetTemplate,
    CabinetPart,
    get_template_by_id,
    list_template_categories,
    templates_by_category,
    generate_cabinet_parts,
)


def test_all_templates_have_id_and_name():
    for t in CABINET_TEMPLATES:
        assert t.template_id, f"Template missing ID"
        assert t.name, f"Template {t.template_id} missing name"
        assert t.category, f"Template {t.template_id} missing category"


def test_unique_template_ids():
    ids = [t.template_id for t in CABINET_TEMPLATES]
    assert len(ids) == len(set(ids)), "Duplicate template IDs found"


def test_get_template_by_id():
    t = get_template_by_id("BASE_1DOOR")
    assert t is not None
    assert t.name == "Gabinete Base - 1 porta"


def test_get_template_missing():
    assert get_template_by_id("NONEXISTENT") is None


def test_list_categories():
    cats = list_template_categories()
    assert len(cats) >= 4
    assert "Base" in cats
    assert "Aereo" in cats


def test_templates_by_category():
    base = templates_by_category("Base")
    assert len(base) >= 2
    for t in base:
        assert t.category == "Base"


def test_generate_base_1door():
    t = get_template_by_id("BASE_1DOOR")
    parts = generate_cabinet_parts(t, 600, 720, 560, 18, 3, "MDF")
    # Must have at least: 2 sides, top, bottom, back, shelf, base board, door = 8
    assert len(parts) >= 8
    names = [p.name for p in parts]
    assert "Lateral esquerda" in names
    assert "Lateral direita" in names
    assert "Tampo superior" in names
    assert "Base inferior" in names
    assert "Fundo" in names
    assert "Porta" in names


def test_generate_base_2door():
    t = get_template_by_id("BASE_2DOOR")
    parts = generate_cabinet_parts(t, 800, 720, 560, 18, 3, "MDF")
    names = [p.name for p in parts]
    assert "Porta 1" in names
    assert "Porta 2" in names


def test_generate_drawer():
    t = get_template_by_id("DRAWER_3")
    parts = generate_cabinet_parts(t, 600, 720, 560, 18, 3, "MDF")
    names = [p.name for p in parts]
    assert "Frente gaveta 1" in names
    assert "Frente gaveta 3" in names
    assert "Lateral gaveta 1" in names
    assert "Fundo gaveta 1" in names


def test_generate_niche_no_back():
    t = get_template_by_id("NICHE")
    parts = generate_cabinet_parts(t, 400, 300, 200, 18, 3, "MDF")
    names = [p.name for p in parts]
    # Niche has no back, no doors, no base board
    assert "Fundo" not in names
    assert "Porta" not in names
    assert "Rodape frontal" not in names
    # Should have: 2 sides, top, bottom = 4
    assert len(parts) == 4


def test_generate_bookshelf_shelves():
    t = get_template_by_id("BOOKSHELF")
    parts = generate_cabinet_parts(t, 800, 1800, 300, 18, 3, "MDF", shelf_count=6)
    shelf_parts = [p for p in parts if p.role == "shelf"]
    assert len(shelf_parts) == 6


def test_all_parts_have_thickness():
    t = get_template_by_id("BASE_1DOOR")
    parts = generate_cabinet_parts(t, 600, 720, 560, 18, 3, "MDF")
    for p in parts:
        assert p.thickness_mm > 0, f"Part {p.name} has zero thickness"


def test_material_propagated():
    t = get_template_by_id("WALL_1DOOR")
    parts = generate_cabinet_parts(t, 600, 700, 350, 18, 3, "MDP Branco")
    for p in parts:
        assert p.material == "MDP Branco"


def test_edge_bands_on_doors():
    t = get_template_by_id("BASE_1DOOR")
    parts = generate_cabinet_parts(t, 600, 720, 560, 18, 3, "MDF")
    doors = [p for p in parts if p.role == "door"]
    for d in doors:
        assert d.edge_top is True
        assert d.edge_bottom is True
        assert d.edge_left is True
        assert d.edge_right is True
