"""Nesting de formas não-retangulares para CNC.

Em marcenaria, peças em L, U ou com recortes internos podem desperdiçar
material significativo se tratadas apenas pelo bounding box.  Este módulo:

1. Detecta concavidades (notches) no perfil 2D da peça.
2. Decompõe a área livre da concavidade em sub-retângulos.
3. Durante o nesting CNC (MaxRects), permite colocar peças pequenas
   dentro dos notches, economizando material.

Limitações intencionais:
- Somente peças cortadas por CNC (router pode seguir contorno).
- Notches devem ser retangulares ou decomponíveis em retângulos.
- Peças de serra continuam sendo tratadas pelo bounding box.
"""

from dataclasses import dataclass, field


@dataclass
class NotchRegion:
    """Região retangular livre dentro do bounding box de uma peça não-retangular."""
    x_offset_mm: float   # Posição X relativa à origem da peça (canto inferior esquerdo)
    y_offset_mm: float   # Posição Y relativa
    length_mm: float      # Comprimento do notch
    width_mm: float       # Largura do notch
    host_part_id: str = ""  # ID da peça hospedeira


@dataclass
class NonRectProfile:
    """Perfil 2D de uma peça não-retangular."""
    part_id: str
    bbox_length_mm: float
    bbox_width_mm: float
    contour_vertices: list = field(default_factory=list)  # [(x, y), ...] polígono fechado
    notches: list = field(default_factory=list)  # [NotchRegion, ...]
    fill_ratio: float = 1.0  # área real / área do bbox


# ---------------------------------------------------------------------------
# Detecção de perfil 2D a partir de shape FreeCAD
# ---------------------------------------------------------------------------

def extract_2d_profile(shape, thickness_axis="z"):
    """Extrai o contorno 2D (projeção no plano comprimento×largura) de uma shape FreeCAD.

    Retorna lista de vértices [(x_mm, y_mm), ...] do polígono externo,
    ou None se a peça for retangular simples.
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

    # Determinar eixos do plano (os dois maiores)
    dims = {
        "x": float(getattr(bbox, "XLength", 0.0)),
        "y": float(getattr(bbox, "YLength", 0.0)),
        "z": float(getattr(bbox, "ZLength", 0.0)),
    }
    # Eixo de espessura = menor dimensão
    t_axis = min(dims, key=dims.get)
    plane_axes = sorted([ax for ax in ("x", "y", "z") if ax != t_axis])
    ax0, ax1 = plane_axes  # ex: ("x", "y")

    # Encontrar a face superior (maior face plana perpendicular ao eixo de espessura)
    top_face = _find_largest_planar_face(shape, t_axis)
    if top_face is None:
        return None

    # Extrair wires (contornos) da face
    wires = list(getattr(top_face, "Wires", []) or [])
    if not wires:
        return None

    # Usar o wire externo (maior perímetro)
    outer_wire = max(wires, key=lambda w: float(getattr(w, "Length", 0.0) or 0.0))

    vertices = []
    for vertex in list(getattr(outer_wire, "Vertexes", []) or []):
        point = getattr(vertex, "Point", None)
        if point is None:
            continue
        x_val = float(getattr(point, ax0, 0.0))
        y_val = float(getattr(point, ax1, 0.0))
        vertices.append((x_val, y_val))

    if len(vertices) < 3:
        return None

    # Normalizar: transladar para origem (0,0)
    min_x = min(v[0] for v in vertices)
    min_y = min(v[1] for v in vertices)
    vertices = [(v[0] - min_x, v[1] - min_y) for v in vertices]

    return vertices


def _find_largest_planar_face(shape, thickness_axis):
    """Encontra a maior face plana perpendicular ao eixo de espessura."""
    best_face = None
    best_area = 0.0

    for face in list(getattr(shape, "Faces", []) or []):
        surface = getattr(face, "Surface", None)
        if surface is None:
            continue
        # Verificar se é face plana
        surface_type = type(surface).__name__
        if "Plane" not in surface_type:
            continue

        # Verificar se normal é paralela ao eixo de espessura
        normal = getattr(surface, "Axis", None)
        if normal is None:
            continue
        axis_val = abs(float(getattr(normal, thickness_axis, 0.0)))
        if axis_val < 0.85:
            continue

        area = float(getattr(face, "Area", 0.0) or 0.0)
        if area > best_area:
            best_area = area
            best_face = face

    return best_face


def analyze_part_profile(part_id, shape, bbox_length_mm, bbox_width_mm):
    """Analisa o perfil 2D de uma peça e detecta notches.

    Retorna NonRectProfile ou None se a peça for retangular.
    """
    vertices = extract_2d_profile(shape)
    if vertices is None:
        return None

    # Calcular área do polígono
    poly_area = _polygon_area(vertices)
    bbox_area = bbox_length_mm * bbox_width_mm
    if bbox_area <= 0:
        return None

    fill_ratio = poly_area / bbox_area if bbox_area > 0 else 1.0

    # Se fill_ratio > 0.95, peça é essencialmente retangular
    if fill_ratio > 0.95:
        return None

    # Detectar notches (regiões retangulares livres)
    notches = _detect_rectangular_notches(vertices, bbox_length_mm, bbox_width_mm, part_id)

    if not notches:
        return None

    return NonRectProfile(
        part_id=part_id,
        bbox_length_mm=bbox_length_mm,
        bbox_width_mm=bbox_width_mm,
        contour_vertices=vertices,
        notches=notches,
        fill_ratio=fill_ratio,
    )


def _polygon_area(vertices):
    """Calcula a área de um polígono simples pelo método do Shoelace."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0


def _detect_rectangular_notches(vertices, bbox_length, bbox_width, host_part_id=""):
    """Detecta regiões retangulares livres no bounding box que não fazem parte da peça.

    Abordagem: discretiza o bbox em grid e identifica regiões retangulares
    que estão fora do polígono da peça.
    """
    if not vertices or bbox_length <= 0 or bbox_width <= 0:
        return []

    # Usar abordagem de varredura: encontrar os cantos do bbox que estão
    # fora do polígono — estes formam as regiões de notch
    notches = []

    # Para peças L-shaped e U-shaped, os notches tipicamente estão nos cantos
    # Testar os 4 cantos e suas extensões
    corners = [
        (0.0, 0.0),                        # inferior esquerdo
        (bbox_length, 0.0),                 # inferior direito
        (0.0, bbox_width),                  # superior esquerdo
        (bbox_length, bbox_width),          # superior direito
    ]

    # Grid sampling para encontrar a maior região retangular livre
    # Resolução: 5mm ou 1% do menor lado, o que for maior
    step = max(5.0, min(bbox_length, bbox_width) * 0.01)
    grid_cols = max(2, int(bbox_length / step))
    grid_rows = max(2, int(bbox_width / step))
    actual_step_x = bbox_length / grid_cols
    actual_step_y = bbox_width / grid_rows

    # Criar grid de ocupação
    occupied = [[False] * grid_cols for _ in range(grid_rows)]
    for row in range(grid_rows):
        for col in range(grid_cols):
            # Centro da célula
            cx = (col + 0.5) * actual_step_x
            cy = (row + 0.5) * actual_step_y
            occupied[row][col] = _point_in_polygon(cx, cy, vertices)

    # Encontrar retângulos máximos de células não-ocupadas (livres)
    free_rects = _maximal_free_rectangles(occupied, grid_rows, grid_cols)

    min_notch_area = 2500.0  # 50mm × 50mm mínimo para ser útil
    for (r0, c0, r1, c1) in free_rects:
        x_mm = c0 * actual_step_x
        y_mm = r0 * actual_step_y
        length_mm = (c1 - c0) * actual_step_x
        width_mm = (r1 - r0) * actual_step_y

        if length_mm * width_mm < min_notch_area:
            continue
        if length_mm < 30.0 or width_mm < 30.0:
            continue

        notches.append(NotchRegion(
            x_offset_mm=round(x_mm, 1),
            y_offset_mm=round(y_mm, 1),
            length_mm=round(length_mm, 1),
            width_mm=round(width_mm, 1),
            host_part_id=host_part_id,
        ))

    return notches


def _point_in_polygon(px, py, vertices):
    """Teste ponto-em-polígono via ray-casting."""
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


def _maximal_free_rectangles(occupied, rows, cols):
    """Encontra retângulos máximos de células False (livres) na grid.

    Usa o algoritmo de histograma (maximal rectangle in histogram)
    para encontrar os maiores retângulos livres.

    Retorna lista de (row_start, col_start, row_end, col_end).
    """
    if rows == 0 or cols == 0:
        return []

    # Construir histograma de alturas (células consecutivas livres para cima)
    heights = [0] * cols
    best_rects = []
    best_area = 0

    for row in range(rows):
        for col in range(cols):
            if not occupied[row][col]:
                heights[col] += 1
            else:
                heights[col] = 0

        # Encontrar maior retângulo no histograma atual
        rects = _largest_rectangle_in_histogram(heights, row)
        for (r0, c0, r1, c1, area) in rects:
            if area > best_area * 0.3:  # Manter retângulos significativos
                best_rects.append((r0, c0, r1, c1))
                if area > best_area:
                    best_area = area

    # Filtrar retângulos muito pequenos
    result = []
    for rect in best_rects:
        r0, c0, r1, c1 = rect
        area = (r1 - r0) * (c1 - c0)
        if area >= best_area * 0.3:
            result.append(rect)

    # Deduplicate / merge overlapping
    return _dedupe_rects(result)


def _largest_rectangle_in_histogram(heights, current_row):
    """Encontra os maiores retângulos num histograma de alturas.

    Retorna lista de (row_start, col_start, row_end, col_end, area).
    """
    n = len(heights)
    results = []
    stack = []  # (col_index, height)

    for i in range(n + 1):
        h = heights[i] if i < n else 0
        start = i
        while stack and stack[-1][1] > h:
            col_start, height = stack.pop()
            col_end = i
            row_start = current_row - height + 1
            row_end = current_row + 1
            area = height * (col_end - col_start)
            if area >= 4:  # Mínimo 4 células
                results.append((row_start, col_start, row_end, col_end, area))
            start = col_start
        stack.append((start, h))

    return results


def _dedupe_rects(rects):
    """Remove retângulos duplicados ou contidos em outros."""
    if not rects:
        return []
    # Ordenar por área decrescente
    rects = sorted(rects, key=lambda r: (r[2] - r[0]) * (r[3] - r[1]), reverse=True)
    result = []
    for rect in rects:
        contained = False
        for existing in result:
            if (rect[0] >= existing[0] and rect[1] >= existing[1] and
                    rect[2] <= existing[2] and rect[3] <= existing[3]):
                contained = True
                break
        if not contained:
            result.append(rect)
    return result


# ---------------------------------------------------------------------------
# Integração com o nesting engine
# ---------------------------------------------------------------------------

def build_notch_placement_map(parts, shapes_by_part_id=None):
    """Analisa todas as peças e retorna mapa de notches disponíveis.

    parts: lista de PanelPart
    shapes_by_part_id: dict {part_id: FreeCAD.Shape} (opcional)

    Retorna dict {part_id: NonRectProfile} para peças não-retangulares.
    """
    if shapes_by_part_id is None:
        return {}

    profiles = {}
    for part in parts:
        shape = shapes_by_part_id.get(part.part_id)
        if shape is None:
            continue
        profile = analyze_part_profile(
            part.part_id, shape, part.length_mm, part.width_mm
        )
        if profile is not None:
            profiles[part.part_id] = profile

    return profiles


def find_notch_candidates(placement, notch_profiles, small_part, spacing_mm=0.0):
    """Verifica se uma peça pequena cabe dentro do notch de uma peça já posicionada.

    placement: LayoutPlacement (peça hospedeira já no layout)
    notch_profiles: dict {part_id: NonRectProfile}
    small_part: PanelPart a ser colocada
    spacing_mm: espaçamento mínimo ao redor da peça

    Retorna lista de candidatos:
        [{x_mm, y_mm, placed_length_mm, placed_width_mm, rotated, notch_index}]
    """
    host_part = getattr(placement, "part", None)
    if host_part is None:
        return []

    host_id = getattr(host_part, "part_id", "")
    profile = notch_profiles.get(host_id)
    if profile is None:
        return []

    candidates = []
    host_rotated = getattr(placement, "rotated", False)

    for notch_idx, notch in enumerate(profile.notches):
        # Ajustar coordenadas do notch para posição absoluta no layout
        if host_rotated:
            # Se hospedeira rotacionada, rotacionar coordenadas do notch
            abs_x = placement.x_mm + notch.y_offset_mm + spacing_mm
            abs_y = placement.y_mm + notch.x_offset_mm + spacing_mm
            avail_length = notch.width_mm - 2 * spacing_mm
            avail_width = notch.length_mm - 2 * spacing_mm
        else:
            abs_x = placement.x_mm + notch.x_offset_mm + spacing_mm
            abs_y = placement.y_mm + notch.y_offset_mm + spacing_mm
            avail_length = notch.length_mm - 2 * spacing_mm
            avail_width = notch.width_mm - 2 * spacing_mm

        if avail_length <= 0 or avail_width <= 0:
            continue

        # Tentar encaixar a peça pequena (normal e rotacionada)
        orientations = []
        if small_part.length_mm <= avail_length and small_part.width_mm <= avail_width:
            orientations.append((False, small_part.length_mm, small_part.width_mm))
        if (small_part.allow_rotation and
                small_part.width_mm <= avail_length and small_part.length_mm <= avail_width):
            orientations.append((True, small_part.width_mm, small_part.length_mm))

        for rotated, pl, pw in orientations:
            candidates.append({
                "x_mm": abs_x,
                "y_mm": abs_y,
                "placed_length_mm": pl,
                "placed_width_mm": pw,
                "rotated": rotated,
                "notch_index": notch_idx,
                "host_part_id": host_id,
                # Waste score: quanto espaço sobra no notch (menor = melhor fit)
                "waste_mm2": (avail_length * avail_width) - (pl * pw),
            })

    return candidates


def try_notch_placements(layout_sheet, notch_profiles, unplaced_parts, spacing_mm=0.0):
    """Tenta colocar peças não-alocadas dentro dos notches de peças já posicionadas.

    Modifica layout_sheet.placements in-place.

    Retorna lista de part_ids que foram colocados com sucesso.
    """
    if not notch_profiles or not unplaced_parts:
        return []

    from .models import LayoutPlacement

    placed_ids = []

    # Ordenar peças pequenas por área (menor primeiro — mais fácil de encaixar)
    sorted_parts = sorted(unplaced_parts, key=lambda p: p.length_mm * p.width_mm)

    for small_part in sorted_parts:
        best_candidate = None

        for placement in layout_sheet.placements:
            candidates = find_notch_candidates(placement, notch_profiles, small_part, spacing_mm)
            for candidate in candidates:
                # Verificar que não colide com outras peças já posicionadas
                if _placement_collides(
                    candidate["x_mm"], candidate["y_mm"],
                    candidate["placed_length_mm"], candidate["placed_width_mm"],
                    layout_sheet.placements, spacing_mm
                ):
                    continue

                if best_candidate is None or candidate["waste_mm2"] < best_candidate["waste_mm2"]:
                    best_candidate = candidate

        if best_candidate is not None:
            new_placement = LayoutPlacement(
                part=small_part,
                x_mm=best_candidate["x_mm"],
                y_mm=best_candidate["y_mm"],
                placed_length_mm=best_candidate["placed_length_mm"],
                placed_width_mm=best_candidate["placed_width_mm"],
                rotated=best_candidate["rotated"],
            )
            layout_sheet.placements.append(new_placement)
            placed_ids.append(small_part.part_id)

    return placed_ids


def _placement_collides(x, y, length, width, existing_placements, spacing_mm=0.0):
    """Verifica se um novo retângulo colide com placements existentes."""
    for p in existing_placements:
        # Verificar sobreposição de retângulos (com spacing)
        if (x < p.x_mm + p.placed_length_mm + spacing_mm and
                x + length + spacing_mm > p.x_mm and
                y < p.y_mm + p.placed_width_mm + spacing_mm and
                y + width + spacing_mm > p.y_mm):
            return True
    return False
