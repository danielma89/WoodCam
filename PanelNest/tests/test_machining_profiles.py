"""Testes para panelnest.machining_profiles."""

from panelnest.machining_profiles import (
    MACHINING_PROFILES,
    MACHINING_CATEGORIES,
    get_profile_by_id,
    get_profiles_by_category,
    compute_absolute_operations,
    MachiningProfile,
    MachiningOperation,
)


def test_all_profiles_have_operations():
    for p in MACHINING_PROFILES:
        assert len(p.operations) > 0, f"Profile {p.profile_id} has no operations"


def test_get_profile_by_id():
    h35 = get_profile_by_id("HINGE_35MM")
    assert h35 is not None
    assert h35.name == "Dobradiça 35mm"
    assert h35.category == "Dobradiça"


def test_get_profile_by_id_missing():
    assert get_profile_by_id("NONEXISTENT") is None


def test_get_profiles_by_category():
    hinges = get_profiles_by_category("Dobradiça")
    assert len(hinges) >= 2
    for p in hinges:
        assert p.category == "Dobradiça"


def test_all_categories_have_profiles():
    for cat in MACHINING_CATEGORIES:
        profiles = get_profiles_by_category(cat)
        assert len(profiles) > 0, f"Category {cat} has no profiles"


def test_compute_absolute_operations_hinge():
    h35 = get_profile_by_id("HINGE_35MM")
    ops = compute_absolute_operations(h35, 600, 400, 18, 100, mirror=False)
    assert len(ops) == 3
    # Cup at x=21.5
    assert ops[0]["x_mm"] == 21.5
    assert ops[0]["y_mm"] == 100
    assert ops[0]["diameter_mm"] == 35.0


def test_compute_absolute_operations_mirror():
    h35 = get_profile_by_id("HINGE_35MM")
    ops_normal = compute_absolute_operations(h35, 600, 400, 18, 100, mirror=False)
    ops_mirror = compute_absolute_operations(h35, 600, 400, 18, 100, mirror=True)
    # Mirrored: x = 600 - 21.5 = 578.5
    assert ops_mirror[0]["x_mm"] == 578.5
    # Y stays the same
    assert ops_mirror[0]["y_mm"] == ops_normal[0]["y_mm"]


def test_passthrough_hole_uses_part_thickness():
    handle = get_profile_by_id("HANDLE_HOLE")
    ops = compute_absolute_operations(handle, 400, 300, 15, 150, mirror=False)
    # depth_mm=0 means passthrough -> should equal part thickness
    assert ops[0]["depth_mm"] == 15.0


def test_minifix_has_two_operations():
    minifix = get_profile_by_id("MINIFIX_15MM")
    assert len(minifix.operations) == 2
    ops = compute_absolute_operations(minifix, 500, 300, 18, 100, mirror=False)
    # Body hole (15mm) and screw hole (5mm)
    diameters = sorted([o["diameter_mm"] for o in ops])
    assert diameters == [5.0, 15.0]
