"""Tests for panelnest.raster_nesting — raster-based shape nesting engine."""
import sys, os, math
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from panelnest.raster_nesting import (
    RasterMask,
    RasterPlacement,
    RasterSheet,
    rotate_mask_90,
    generate_rotations,
    raster_nest,
    raster_nest_multi_sheet,
    _point_in_polygon_fast,
    _mask_fits,
    _stamp_mask,
    _find_bottom_left_position,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_rect_mask(part_id, cols, rows, resolution_mm=1.0):
    """Create a fully-filled rectangular mask."""
    grid = [[True] * cols for _ in range(rows)]
    return RasterMask(
        part_id=part_id,
        grid=grid,
        rows=rows,
        cols=cols,
        area_pixels=rows * cols,
        resolution_mm=resolution_mm,
    )


def _make_L_mask(part_id, resolution_mm=1.0):
    """Create an L-shaped mask (5x5 with top-right corner removed).

    Shape (X = occupied):
        X X X X X
        X X X X X
        X X X . .
        X X X . .
        X X X . .
    """
    grid = [
        [True, True, True, True, True],
        [True, True, True, True, True],
        [True, True, True, False, False],
        [True, True, True, False, False],
        [True, True, True, False, False],
    ]
    area = sum(cell for row in grid for cell in row)
    return RasterMask(
        part_id=part_id,
        grid=grid,
        rows=5,
        cols=5,
        area_pixels=area,
        resolution_mm=resolution_mm,
    )


# ---------------------------------------------------------------------------
# RasterMask dataclass
# ---------------------------------------------------------------------------

def test_raster_mask_creation():
    m = _make_rect_mask("p1", 10, 5)
    assert m.part_id == "p1"
    assert m.rows == 5
    assert m.cols == 10
    assert m.area_pixels == 50
    assert m.resolution_mm == 1.0


def test_raster_mask_L_area():
    m = _make_L_mask("L1")
    # 5x5 = 25, minus 3x2 = 6 removed corner
    assert m.area_pixels == 19


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------

def test_rotate_mask_90_dimensions():
    m = _make_rect_mask("r1", cols=10, rows=5)
    rotated = rotate_mask_90(m)
    assert rotated.rows == 10  # was cols
    assert rotated.cols == 5   # was rows
    assert rotated.area_pixels == m.area_pixels


def test_rotate_mask_90_preserves_area_L():
    m = _make_L_mask("L1")
    rotated = rotate_mask_90(m)
    assert rotated.area_pixels == m.area_pixels
    assert rotated.rows == 5
    assert rotated.cols == 5


def test_rotate_mask_360_identity():
    """Rotating 4 times should return to original."""
    m = _make_L_mask("L1")
    current = m
    for _ in range(4):
        current = rotate_mask_90(current)
    assert current.rows == m.rows
    assert current.cols == m.cols
    assert current.grid == m.grid


def test_generate_rotations_all():
    m = _make_rect_mask("r1", 10, 5)
    rots = generate_rotations(m, (0, 90, 180, 270))
    assert len(rots) == 4
    angles = [r[0] for r in rots]
    assert angles == [0, 90, 180, 270]


def test_generate_rotations_subset():
    m = _make_rect_mask("r1", 10, 5)
    rots = generate_rotations(m, (0, 90))
    assert len(rots) == 2
    assert rots[0][0] == 0
    assert rots[1][0] == 90


# ---------------------------------------------------------------------------
# Point-in-polygon
# ---------------------------------------------------------------------------

def test_point_in_square():
    # Unit square
    verts = [(0, 0), (10, 0), (10, 10), (0, 10)]
    assert _point_in_polygon_fast(5, 5, verts) is True
    assert _point_in_polygon_fast(0.5, 0.5, verts) is True
    assert _point_in_polygon_fast(-1, 5, verts) is False
    assert _point_in_polygon_fast(11, 5, verts) is False


def test_point_in_triangle():
    verts = [(0, 0), (10, 0), (5, 10)]
    assert _point_in_polygon_fast(5, 3, verts) is True
    assert _point_in_polygon_fast(0, 10, verts) is False


# ---------------------------------------------------------------------------
# Mask fitting and stamping
# ---------------------------------------------------------------------------

def test_mask_fits_empty_sheet():
    sheet_rows, sheet_cols = 20, 30
    sheet_grid = [[False] * sheet_cols for _ in range(sheet_rows)]
    mask = _make_rect_mask("p1", 5, 3)
    assert _mask_fits(sheet_grid, sheet_rows, sheet_cols, mask, 0, 0) is True
    assert _mask_fits(sheet_grid, sheet_rows, sheet_cols, mask, 17, 25) is True


def test_mask_fits_out_of_bounds():
    sheet_rows, sheet_cols = 20, 30
    sheet_grid = [[False] * sheet_cols for _ in range(sheet_rows)]
    mask = _make_rect_mask("p1", 5, 3)
    # Too far right
    assert _mask_fits(sheet_grid, sheet_rows, sheet_cols, mask, 0, 26) is False
    # Too far down
    assert _mask_fits(sheet_grid, sheet_rows, sheet_cols, mask, 18, 0) is False


def test_mask_fits_collision():
    sheet_rows, sheet_cols = 20, 30
    sheet_grid = [[False] * sheet_cols for _ in range(sheet_rows)]
    # Place an obstacle at (5, 5)
    sheet_grid[5][5] = True
    mask = _make_rect_mask("p1", 10, 10)
    # Mask at (0,0) covers (5,5) — should collide
    assert _mask_fits(sheet_grid, sheet_rows, sheet_cols, mask, 0, 0) is False
    # Mask at (6,0) does not cover (5,5)
    assert _mask_fits(sheet_grid, sheet_rows, sheet_cols, mask, 6, 0) is True


def test_stamp_mask_marks_occupied():
    sheet_rows, sheet_cols = 20, 30
    sheet_grid = [[False] * sheet_cols for _ in range(sheet_rows)]
    mask = _make_rect_mask("p1", 3, 2)
    _stamp_mask(sheet_grid, sheet_rows, sheet_cols, mask, 5, 10, spacing_px=0)
    # The exact mask area should be occupied
    assert sheet_grid[5][10] is True
    assert sheet_grid[5][11] is True
    assert sheet_grid[5][12] is True
    assert sheet_grid[6][10] is True
    assert sheet_grid[6][11] is True
    assert sheet_grid[6][12] is True
    # Just outside should be free
    assert sheet_grid[4][10] is False
    assert sheet_grid[7][10] is False


def test_stamp_mask_with_spacing():
    sheet_rows, sheet_cols = 20, 30
    sheet_grid = [[False] * sheet_cols for _ in range(sheet_rows)]
    mask = _make_rect_mask("p1", 1, 1)  # Single pixel
    _stamp_mask(sheet_grid, sheet_rows, sheet_cols, mask, 10, 15, spacing_px=2)
    # The halo of 2 around (10, 15) should be occupied
    for dr in range(-2, 3):
        for dc in range(-2, 3):
            assert sheet_grid[10 + dr][15 + dc] is True
    # Just outside halo should be free
    assert sheet_grid[10][18] is False
    assert sheet_grid[7][15] is False


# ---------------------------------------------------------------------------
# Bottom-left placement
# ---------------------------------------------------------------------------

def test_find_bottom_left_empty_sheet():
    sheet_rows, sheet_cols = 50, 80
    sheet_grid = [[False] * sheet_cols for _ in range(sheet_rows)]
    mask = _make_rect_mask("p1", 10, 5)
    pos = _find_bottom_left_position(sheet_grid, sheet_rows, sheet_cols, mask, spacing_px=0)
    assert pos == (0, 0)


def test_find_bottom_left_with_obstacle():
    sheet_rows, sheet_cols = 50, 80
    sheet_grid = [[False] * sheet_cols for _ in range(sheet_rows)]
    # Block top-left corner (5x5)
    for r in range(5):
        for c in range(5):
            sheet_grid[r][c] = True
    mask = _make_rect_mask("p1", 3, 3)
    pos = _find_bottom_left_position(sheet_grid, sheet_rows, sheet_cols, mask, spacing_px=0)
    # Should find (0, 5) — first free position scanning left-to-right, top-to-bottom
    assert pos == (0, 5)


def test_find_bottom_left_mask_too_large():
    sheet_rows, sheet_cols = 5, 5
    sheet_grid = [[False] * sheet_cols for _ in range(sheet_rows)]
    mask = _make_rect_mask("p1", 10, 10)
    pos = _find_bottom_left_position(sheet_grid, sheet_rows, sheet_cols, mask, spacing_px=0)
    assert pos is None


# ---------------------------------------------------------------------------
# Full nesting
# ---------------------------------------------------------------------------

def test_raster_nest_single_piece():
    mask = _make_rect_mask("p1", 100, 50)
    placements, unplaced = raster_nest(
        [mask], sheet_length_mm=200, sheet_width_mm=100,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
    )
    assert len(placements) == 1
    assert len(unplaced) == 0
    assert placements[0].part_id == "p1"


def test_raster_nest_multiple_pieces():
    masks = [_make_rect_mask(f"p{i}", 30, 20) for i in range(4)]
    placements, unplaced = raster_nest(
        masks, sheet_length_mm=200, sheet_width_mm=100,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
    )
    assert len(placements) == 4
    assert len(unplaced) == 0


def test_raster_nest_piece_too_large():
    mask = _make_rect_mask("big", 500, 300)
    placements, unplaced = raster_nest(
        [mask], sheet_length_mm=200, sheet_width_mm=100,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
    )
    assert len(placements) == 0
    assert "big" in unplaced


def test_raster_nest_with_margin():
    # Piece fills entire sheet except margin — should fit
    mask = _make_rect_mask("p1", 90, 40)
    placements, unplaced = raster_nest(
        [mask], sheet_length_mm=100, sheet_width_mm=50,
        margin_mm=5, spacing_mm=0, resolution_mm=1.0,
    )
    assert len(placements) == 1
    # Position should be at margin offset
    assert placements[0].x_mm >= 5.0
    assert placements[0].y_mm >= 5.0


def test_raster_nest_no_rotation():
    # Piece 100x50 on sheet 60x120 — only fits rotated
    mask = _make_rect_mask("p1", 100, 50)
    placements, unplaced = raster_nest(
        [mask], sheet_length_mm=60, sheet_width_mm=120,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
        allow_rotation=False,
    )
    assert len(placements) == 0
    assert "p1" in unplaced


def test_raster_nest_with_rotation():
    # Piece 100x50 on sheet 60x120 — fits when rotated 90
    mask = _make_rect_mask("p1", 100, 50)
    placements, unplaced = raster_nest(
        [mask], sheet_length_mm=60, sheet_width_mm=120,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
        allow_rotation=True,
    )
    assert len(placements) == 1
    assert len(unplaced) == 0


def test_raster_nest_L_shaped_pieces():
    """L-shaped pieces should nest and take advantage of irregular shape."""
    masks = [_make_L_mask(f"L{i}") for i in range(2)]
    placements, unplaced = raster_nest(
        masks, sheet_length_mm=20, sheet_width_mm=20,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
    )
    assert len(placements) == 2
    assert len(unplaced) == 0


# ---------------------------------------------------------------------------
# Multi-sheet nesting
# ---------------------------------------------------------------------------

def test_raster_nest_multi_sheet_single():
    masks = [_make_rect_mask("p1", 50, 30)]
    sheets = raster_nest_multi_sheet(
        masks, sheet_length_mm=100, sheet_width_mm=80,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
    )
    assert len(sheets) == 1
    assert sheets[0].sheet_index == 1
    assert len(sheets[0].placements) == 1
    assert sheets[0].utilization_pct > 0


def test_raster_nest_multi_sheet_overflow():
    """Pieces that don't fit on one sheet spill to the next."""
    # 4 pieces of 60x40 on a 100x80 sheet — 2 fit per sheet
    masks = [_make_rect_mask(f"p{i}", 60, 40) for i in range(4)]
    sheets = raster_nest_multi_sheet(
        masks, sheet_length_mm=100, sheet_width_mm=80,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
    )
    total_placed = sum(len(s.placements) for s in sheets)
    assert total_placed == 4
    assert len(sheets) >= 2


def test_raster_nest_multi_sheet_utilization():
    masks = [_make_rect_mask("p1", 50, 50)]
    sheets = raster_nest_multi_sheet(
        masks, sheet_length_mm=100, sheet_width_mm=100,
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
    )
    assert len(sheets) == 1
    # 50*50 / (100*100) = 25%
    assert abs(sheets[0].utilization_pct - 25.0) < 0.5


def test_raster_nest_multi_sheet_material():
    masks = [_make_rect_mask("p1", 10, 10)]
    sheets = raster_nest_multi_sheet(
        masks, sheet_length_mm=100, sheet_width_mm=100,
        thickness_mm=15.0, material="MDF Branco",
        margin_mm=0, spacing_mm=0, resolution_mm=1.0,
    )
    assert sheets[0].thickness_mm == 15.0
    assert sheets[0].material == "MDF Branco"


# ---------------------------------------------------------------------------
# RasterPlacement / RasterSheet dataclasses
# ---------------------------------------------------------------------------

def test_raster_placement_fields():
    p = RasterPlacement(
        part_id="test", object_name="Box", label="Lateral",
        col=10, row=5, x_mm=10.0, y_mm=5.0,
        bbox_length_mm=100.0, bbox_width_mm=50.0,
        rotation_deg=90.0, area_mm2=4500.0,
    )
    assert p.rotation_deg == 90.0
    assert p.area_mm2 == 4500.0


def test_raster_sheet_defaults():
    s = RasterSheet(
        sheet_index=1, length_mm=2750, width_mm=1830,
        thickness_mm=18.0, material="MDF",
    )
    assert s.placements == []
    assert s.utilization_pct == 0.0
