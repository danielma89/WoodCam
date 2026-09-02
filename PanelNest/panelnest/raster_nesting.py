"""Nesting por rasterização para peças de formas irregulares (CNC).

Cada peça é convertida num bitmap binário (máscara 2D) a partir do contorno
real da shape FreeCAD.  O placement usa heurística bottom-left: varre a chapa
como grid e encontra a primeira posição onde a máscara cabe sem colisão.

Resolução padrão: 1mm por pixel.

Engine usa integer bitmasks (um int por linha) para operações bitwise rápidas.

Este módulo é completamente independente do nesting retangular (MaxRects/
Guilhotina/Strip) — não altera nem depende de nesting.py.
"""

import math
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# Estruturas de dados
# ---------------------------------------------------------------------------

@dataclass
class RasterMask:
    """Máscara binária 2D de uma peça."""
    part_id: str
    grid: list             # list[list[bool]], [row][col], True = ocupado
    rows: int
    cols: int
    area_pixels: int       # Número de pixels ocupados
    resolution_mm: float   # mm por pixel


@dataclass
class RasterPlacement:
    """Resultado de posicionamento de uma peça na chapa."""
    part_id: str
    object_name: str
    label: str
    col: int               # Coluna (X) no grid da chapa
    row: int               # Linha (Y) no grid da chapa
    x_mm: float            # Posição X em mm
    y_mm: float            # Posição Y em mm
    bbox_length_mm: float
    bbox_width_mm: float
    rotation_deg: float    # Rotação aplicada (0, 90, 180, 270)
    area_mm2: float        # Área real da peça


@dataclass
class RasterSheet:
    """Resultado de uma chapa com peças posicionadas."""
    sheet_index: int
    length_mm: float
    width_mm: float
    thickness_mm: float
    material: str
    placements: list = field(default_factory=list)  # list[RasterPlacement]
    utilization_pct: float = 0.0


# ---------------------------------------------------------------------------
# Extração de contorno 2D
# ---------------------------------------------------------------------------

def extract_contour_from_shape(shape, resolution_mm=1.0):
    """Extrai máscara binária da projeção 2D (face superior) de uma shape FreeCAD.

    Retorna (grid, rows, cols, area_pixels) ou None se falhar.
    """
    if shape is None:
        return None
    try:
        if shape.isNull():
            return None
    except Exception:
        return None

    bbox = getattr(shape, "BoundBox", None)
    if bbox is None:
        return None

    # Eixo de espessura = menor dimensão
    dims = {
        "x": float(getattr(bbox, "XLength", 0.0)),
        "y": float(getattr(bbox, "YLength", 0.0)),
        "z": float(getattr(bbox, "ZLength", 0.0)),
    }
    t_axis = min(dims, key=dims.get)
    plane_axes = sorted([ax for ax in ("x", "y", "z") if ax != t_axis])
    ax0, ax1 = plane_axes

    # Encontrar maior face plana
    best_face = _find_top_face(shape, t_axis)
    if best_face is None:
        return None

    # Dimensões no plano
    length_mm = dims[ax0]
    width_mm = dims[ax1]
    cols = max(1, int(math.ceil(length_mm / resolution_mm)))
    rows = max(1, int(math.ceil(width_mm / resolution_mm)))

    origin_ax0 = float(getattr(bbox, ax0.upper() + "Min", 0.0))
    origin_ax1 = float(getattr(bbox, ax1.upper() + "Min", 0.0))

    # Extrair wire externo
    wires = list(getattr(best_face, "Wires", []) or [])
    if not wires:
        return None
    outer_wire = max(wires, key=lambda w: float(getattr(w, "Length", 0.0) or 0.0))

    # Discretizar wire INTEIRO de uma vez (garante ordem correta dos pontos)
    n_pts = max(24, int(outer_wire.Length / resolution_mm))
    try:
        raw_pts = outer_wire.discretize(Number=n_pts)
    except Exception:
        return None

    if len(raw_pts) < 3:
        return None

    # Extrair coordenadas 2D e normalizar para grid
    grid_verts = []
    for pt in raw_pts:
        lx = (float(getattr(pt, ax0, 0.0)) - origin_ax0) / resolution_mm
        ly = (float(getattr(pt, ax1, 0.0)) - origin_ax1) / resolution_mm
        grid_verts.append((lx, ly))

    # Rasterizar o polígono
    grid = [[False] * cols for _ in range(rows)]
    area_pixels = 0
    for r in range(rows):
        cy = r + 0.5
        for c in range(cols):
            cx = c + 0.5
            if _point_in_polygon_fast(cx, cy, grid_verts):
                grid[r][c] = True
                area_pixels += 1

    return grid, rows, cols, area_pixels


def _find_top_face(shape, thickness_axis):
    """Encontra a maior face plana perpendicular ao eixo de espessura."""
    best_face = None
    best_area = 0.0
    for face in list(getattr(shape, "Faces", []) or []):
        surface = getattr(face, "Surface", None)
        if surface is None:
            continue
        if "Plane" not in type(surface).__name__:
            continue
        normal = getattr(surface, "Axis", None)
        if normal is None:
            continue
        if abs(float(getattr(normal, thickness_axis, 0.0))) < 0.85:
            continue
        area = float(getattr(face, "Area", 0.0) or 0.0)
        if area > best_area:
            best_area = area
            best_face = face
    return best_face


def _wire_to_vertices(wire, ax0, ax1, origin, resolution_mm=2.0):
    """Converte wire FreeCAD em lista de vértices 2D [(x, y), ...].

    Curvas são discretizadas. Coordenadas são relativas à origem do bbox.
    """
    vertices = []
    for edge in wire.Edges:
        curve = getattr(edge, "Curve", None)
        curve_type = type(curve).__name__ if curve is not None else ""
        is_curved = any(k in curve_type for k in ("Circle", "Ellipse", "BSpline", "BezierCurve"))

        if is_curved:
            try:
                n_points = max(8, int(edge.Length / resolution_mm))
                pts = edge.discretize(Number=n_points)
            except Exception:
                pts = [v.Point for v in edge.Vertexes]
        else:
            pts = [v.Point for v in edge.Vertexes]

        for pt in pts:
            x = float(getattr(pt, ax0, 0.0)) - origin[ax0]
            y = float(getattr(pt, ax1, 0.0)) - origin[ax1]
            if vertices and abs(vertices[-1][0] - x) < 0.01 and abs(vertices[-1][1] - y) < 0.01:
                continue
            vertices.append((x, y))

    return vertices


def _point_in_polygon_fast(px, py, vertices):
    """Ray-casting point-in-polygon."""
    n = len(vertices)
    inside = False
    j = n - 1
    for i in range(n):
        xi, yi = vertices[i]
        xj, yj = vertices[j]
        if ((yi > py) != (yj > py)) and (px < (xj - xi) * (py - yi) / (yj - yi + 1e-12) + xi):
            inside = not inside
        j = i
    return inside


# ---------------------------------------------------------------------------
# Rotação de máscara
# ---------------------------------------------------------------------------

def rotate_mask_90(mask):
    """Rotaciona máscara 90° no sentido horário. Retorna nova máscara."""
    new_rows = mask.cols
    new_cols = mask.rows
    new_grid = [[False] * new_cols for _ in range(new_rows)]
    area = 0
    for r in range(mask.rows):
        for c in range(mask.cols):
            if mask.grid[r][c]:
                nr = c
                nc = mask.rows - 1 - r
                new_grid[nr][nc] = True
                area += 1
    return RasterMask(
        part_id=mask.part_id,
        grid=new_grid,
        rows=new_rows,
        cols=new_cols,
        area_pixels=area,
        resolution_mm=mask.resolution_mm,
    )


def generate_rotations(mask, angles=(0, 90, 180, 270)):
    """Gera lista de (angle, mask) para cada rotação solicitada."""
    rotations = [(0, mask)]
    current = mask
    for angle in (90, 180, 270):
        current = rotate_mask_90(current)
        if angle in angles:
            rotations.append((angle, current))
    return rotations


# ---------------------------------------------------------------------------
# Bitmask helpers — cada linha é um int Python, bit c = coluna c ocupada
# ---------------------------------------------------------------------------

def _grid_to_bits(grid, rows, cols):
    """Converte grid list[list[bool]] em list[int] de bitmasks."""
    bits = []
    for r in range(rows):
        val = 0
        row = grid[r]
        for c in range(cols):
            if row[c]:
                val |= (1 << c)
        bits.append(val)
    return bits


def _mask_to_bits(mask):
    """Converte RasterMask.grid em list[int] de bitmasks."""
    return _grid_to_bits(mask.grid, mask.rows, mask.cols)


def _expand_bits_cols(bitmask, spacing_px):
    """Expande bitmask horizontalmente por spacing_px pixels."""
    expanded = bitmask
    for d in range(1, spacing_px + 1):
        expanded |= (bitmask << d) | (bitmask >> d)
    return expanded


# ---------------------------------------------------------------------------
# Engine de nesting (bitmask-based)
# ---------------------------------------------------------------------------

def raster_nest(masks, sheet_length_mm, sheet_width_mm, margin_mm=5.0,
                spacing_mm=3.0, resolution_mm=1.0, allow_rotation=True,
                progress_fn=None):
    """Posiciona peças na chapa usando shelf packing + bottom-left com bitmasks.

    Usa integer bitmasks (um int por linha) para colisão O(1) por linha.

    masks: lista de RasterMask
    progress_fn: callback(current, total, part_id) — retorna False para cancelar.
    Retorna (placements, unplaced)
    """
    margin_px = int(math.ceil(margin_mm / resolution_mm))
    spacing_px = int(math.ceil(spacing_mm / resolution_mm))
    sheet_cols = int(math.floor(sheet_length_mm / resolution_mm))
    sheet_rows = int(math.floor(sheet_width_mm / resolution_mm))

    # Grid da chapa como bitmasks (um int por linha)
    margin_mask = 0
    for c in range(margin_px):
        margin_mask |= (1 << c)
    for c in range(sheet_cols - margin_px, sheet_cols):
        margin_mask |= (1 << c)
    full_row = (1 << sheet_cols) - 1  # Todos os bits

    sheet_bits = []
    for r in range(sheet_rows):
        if r < margin_px or r >= sheet_rows - margin_px:
            sheet_bits.append(full_row)
        else:
            sheet_bits.append(margin_mask)

    # Ordenar: maior dimensão primeiro
    sorted_masks = sorted(
        masks,
        key=lambda m: (max(m.rows, m.cols), m.area_pixels),
        reverse=True,
    )

    placements = []
    unplaced = []
    total = len(sorted_masks)

    for idx, mask in enumerate(sorted_masks):
        if progress_fn is not None and idx % max(1, total // 50) == 0:
            if progress_fn(idx, total, mask.part_id) is False:
                break

        if allow_rotation:
            candidates = generate_rotations(mask, (0, 90, 180, 270))
        else:
            candidates = [(0, mask)]

        best_pos = None
        best_angle = 0
        best_mask = None
        best_bits = None
        best_score = None

        for angle, rot_mask in candidates:
            rot_bits = _mask_to_bits(rot_mask)
            pos = _find_position_bits(
                sheet_bits, sheet_rows, sheet_cols,
                rot_bits, rot_mask.rows, rot_mask.cols,
            )
            if pos is not None:
                r, c = pos
                score = (r, c, rot_mask.rows)
                if best_score is None or score < best_score:
                    best_pos = pos
                    best_angle = angle
                    best_mask = rot_mask
                    best_bits = rot_bits
                    best_score = score

        if best_pos is not None and best_mask is not None:
            row, col = best_pos
            _stamp_bits(sheet_bits, sheet_rows, sheet_cols,
                        best_bits, best_mask.rows, row, col, spacing_px)

            placements.append(RasterPlacement(
                part_id=mask.part_id,
                object_name="",
                label="",
                col=col,
                row=row,
                x_mm=col * resolution_mm,
                y_mm=row * resolution_mm,
                bbox_length_mm=best_mask.cols * resolution_mm,
                bbox_width_mm=best_mask.rows * resolution_mm,
                rotation_deg=best_angle,
                area_mm2=best_mask.area_pixels * resolution_mm * resolution_mm,
            ))
        else:
            unplaced.append(mask.part_id)

    return placements, unplaced


def _find_position_bits(sheet_bits, sheet_rows, sheet_cols,
                        mask_bits, mask_rows, mask_cols):
    """Encontra posição bottom-left usando bitmask collision.

    Cada _mask_fits_bits é O(mask_rows) — uma operação AND por linha.
    """
    max_row = sheet_rows - mask_rows
    max_col = sheet_cols - mask_cols

    if max_row < 0 or max_col < 0:
        return None

    # Encontrar primeiro bitmask não-zero da máscara (para quick reject)
    first_mr = 0
    first_mb = 0
    for mr in range(mask_rows):
        if mask_bits[mr]:
            first_mr = mr
            first_mb = mask_bits[mr]
            break

    for r in range(max_row + 1):
        sr = r + first_mr
        sheet_row = sheet_bits[sr]

        for c in range(max_col + 1):
            # Quick reject: check first occupied mask row
            if sheet_row & (first_mb << c):
                continue
            # Full check
            if _mask_fits_bits(sheet_bits, mask_bits, mask_rows, r, c):
                return (r, c)

    return None


def _mask_fits_bits(sheet_bits, mask_bits, mask_rows, start_row, start_col):
    """Verifica se máscara cabe — uma operação AND por linha da máscara."""
    for mr in range(mask_rows):
        shifted = mask_bits[mr] << start_col
        if sheet_bits[start_row + mr] & shifted:
            return False
    return True


def _stamp_bits(sheet_bits, sheet_rows, sheet_cols,
                mask_bits, mask_rows, start_row, start_col, spacing_px):
    """Marca máscara + halo de spacing no grid usando bitwise OR."""
    col_mask = (1 << sheet_cols) - 1  # Clip mask

    for mr in range(mask_rows):
        mb = mask_bits[mr]
        if mb == 0:
            continue
        # Expandir horizontalmente pelo spacing
        expanded = _expand_bits_cols(mb, spacing_px) << start_col
        expanded &= col_mask

        # Aplicar em linhas [sr - spacing, sr + spacing]
        sr = start_row + mr
        r_min = max(0, sr - spacing_px)
        r_max = min(sheet_rows - 1, sr + spacing_px)
        for r in range(r_min, r_max + 1):
            sheet_bits[r] |= expanded


# ---------------------------------------------------------------------------
# Compat: _mask_fits / _stamp_mask / _find_bottom_left_position para testes
# ---------------------------------------------------------------------------

def _mask_fits(sheet_grid, sheet_rows, sheet_cols, mask, start_row, start_col):
    """Compat: verifica colisão usando grid list[list[bool]]."""
    if start_row + mask.rows > sheet_rows or start_col + mask.cols > sheet_cols:
        return False
    if start_row < 0 or start_col < 0:
        return False
    for mr in range(mask.rows):
        sheet_row = sheet_grid[start_row + mr]
        mask_row = mask.grid[mr]
        for mc in range(mask.cols):
            if mask_row[mc] and sheet_row[start_col + mc]:
                return False
    return True


def _stamp_mask(sheet_grid, sheet_rows, sheet_cols, mask, start_row, start_col, spacing_px):
    """Compat: marca pixels no grid list[list[bool]]."""
    for mr in range(mask.rows):
        mask_row = mask.grid[mr]
        for mc in range(mask.cols):
            if not mask_row[mc]:
                continue
            sr = start_row + mr
            sc = start_col + mc
            dr_min = max(-spacing_px, -sr)
            dr_max = min(spacing_px, sheet_rows - 1 - sr)
            dc_min = max(-spacing_px, -sc)
            dc_max = min(spacing_px, sheet_cols - 1 - sc)
            for dr in range(dr_min, dr_max + 1):
                row = sheet_grid[sr + dr]
                for dc in range(dc_min, dc_max + 1):
                    row[sc + dc] = True


def _find_bottom_left_position(sheet_grid, sheet_rows, sheet_cols, mask, spacing_px):
    """Compat: encontra posição usando grid list[list[bool]]."""
    mask_rows = mask.rows
    mask_cols = mask.cols
    max_row = sheet_rows - mask_rows
    max_col = sheet_cols - mask_cols
    if max_row < 0 or max_col < 0:
        return None
    for r in range(max_row + 1):
        for c in range(max_col + 1):
            if _mask_fits(sheet_grid, sheet_rows, sheet_cols, mask, r, c):
                return (r, c)
    return None


# ---------------------------------------------------------------------------
# Múltiplas chapas
# ---------------------------------------------------------------------------

def raster_nest_multi_sheet(masks, sheet_length_mm, sheet_width_mm,
                            thickness_mm=18.0, material="",
                            margin_mm=5.0, spacing_mm=3.0,
                            resolution_mm=1.0, allow_rotation=True,
                            max_sheets=20, progress_fn=None):
    """Faz nesting em múltiplas chapas até encaixar todas as peças.

    progress_fn: callback(current, total, message) — propagado para raster_nest.
    Retorna lista de RasterSheet.
    """
    remaining = list(masks)
    sheets = []
    total_masks = len(masks)
    placed_so_far = 0

    def _sheet_progress(current, total, part_id):
        if progress_fn is None:
            return None
        return progress_fn(placed_so_far + current, total_masks, part_id)

    for sheet_idx in range(max_sheets):
        if not remaining:
            break

        placed, unplaced_ids = raster_nest(
            remaining, sheet_length_mm, sheet_width_mm,
            margin_mm=margin_mm, spacing_mm=spacing_mm,
            resolution_mm=resolution_mm, allow_rotation=allow_rotation,
            progress_fn=_sheet_progress,
        )
        placed_so_far += len(placed)

        if not placed:
            break

        sheet = RasterSheet(
            sheet_index=sheet_idx + 1,
            length_mm=sheet_length_mm,
            width_mm=sheet_width_mm,
            thickness_mm=thickness_mm,
            material=material,
            placements=placed,
        )

        sheet_area = sheet_length_mm * sheet_width_mm
        used_area = sum(p.area_mm2 for p in placed)
        sheet.utilization_pct = round(used_area / sheet_area * 100, 1) if sheet_area > 0 else 0

        sheets.append(sheet)

        placed_ids = {p.part_id for p in placed}
        remaining = [m for m in remaining if m.part_id not in placed_ids]

    return sheets
