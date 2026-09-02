import math
import re

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

try:
    import Part
except ImportError:
    Part = None

from .constants import (
    PART_VISUAL_PALETTE,
    SHEET_SOURCE_COLORS,
    LAYOUT_SHEET_GAP_MM,
    LAYOUT_GROUP_GAP_MM,
    INTERNAL_PROPERTY_NAME,
    LAYOUT_ROOT_NAME,
    CUT_MARKER_DIGIT_WIDTH_MM,
    CUT_MARKER_DIGIT_HEIGHT_MM,
    CUT_MARKER_DIGIT_GAP_MM,
    CUT_MARKER_STROKE_MM,
    CUT_MARKER_BADGE_PADDING_X_MM,
    CUT_MARKER_BADGE_PADDING_Y_MM,
    CUT_MARKER_SIDE_GAP_MM,
    CUT_MARKER_LANE_GAP_MM,
    CUT_MARKER_PLATE_DEPTH_MM,
    CUT_MARKER_TEXT_DEPTH_MM,
    CUT_MARKER_SCREEN_FONT_SIZE_MM,
    CUT_MARKER_SCREEN_LAYOUT_WIDTH_FACTOR,
    CUT_MARKER_SCREEN_LAYOUT_HEIGHT_FACTOR,
    CUT_MARKER_ELBOW_GAP_MM,
    PART_LABEL_FONT_SIZE_MM,
    PART_LABEL_FALLBACK_FONT_SIZE_MM,
    PART_LABEL_COMPACT_FONT_SIZE_MM,
    PART_LABEL_MINI_FONT_SIZE_MM,
    PART_LABEL_TINY_FONT_SIZE_MM,
    PART_LABEL_MICRO_FONT_SIZE_MM,
    PART_DIMENSION_FONT_SIZE_MM,
    PART_DIMENSION_FALLBACK_FONT_SIZE_MM,
    PART_DIMENSION_COMPACT_FONT_SIZE_MM,
    PART_DIMENSION_MINI_FONT_SIZE_MM,
    PART_DIMENSION_TINY_FONT_SIZE_MM,
    PART_ANNOTATION_SCREEN_CHAR_WIDTH_FACTOR,
    PART_ANNOTATION_SCREEN_LINE_HEIGHT_FACTOR,
    PART_ANNOTATION_PADDING_X_MM,
    PART_ANNOTATION_PADDING_Y_MM,
    PART_DIMENSION_EDGE_GAP_MM,
    PART_LABEL_MIN_LENGTH_MM,
    PART_LABEL_MIN_WIDTH_MM,
    PART_LABEL_MIN_AREA_MM2,
    PART_LABEL_CODE_ONLY_MIN_LENGTH_MM,
    PART_LABEL_CODE_ONLY_MIN_WIDTH_MM,
    PART_LABEL_CODE_ONLY_MIN_AREA_MM2,
    PART_DIMENSION_MIN_LENGTH_MM,
    PART_DIMENSION_MIN_WIDTH_MM,
    PART_DIMENSION_MIN_AREA_MM2,
    LAYOUT_EDGE_BAND_MARKER_PREFERRED_WIDTH_MM,
    LAYOUT_EDGE_BAND_MARKER_MIN_WIDTH_MM,
    LAYOUT_EDGE_BAND_MARKER_MAX_WIDTH_MM,
    LAYOUT_EDGE_BAND_MARKER_HEIGHT_MM,
    EDGE_BAND_SIDE_LABELS,
    EDGE_BAND_SIDE_ABBREVIATIONS,
    EDGE_BAND_HIGHLIGHT_COLOR,
    DIMENSION_EQUALITY_TOLERANCE_MM,
    PART_LABEL_PATTERN,
)
from .freecad_utils import (
    ensure_document,
    _mark_internal_object,
    _is_internal_object,
    _remove_object_tree,
    _add_object_to_group,
    _group_member_count,
    _safe_gui_refresh,
)
from .metadata import (
    _format_mm,
    _format_percent,
    _format_thickness_label,
    _material_value,
    normalize_cut_method,
    get_sheet_settings,
)
from .edge_band import (
    _normalize_color_triplet,
    _part_edge_band_flags_dict,
    _normalize_occurrence_edge_band_flags,
    _object_occurrence_edge_band_map,
    _allow_rotation_summary,
    _part_is_square,
)
from .orientation import (
    placement_rotation_deg,
    rotated_layout_side,
    transform_local_point,
    transform_placement_point,
)
from .nesting import (
    create_layout_sheets,
    _layout_mode_label,
    _layout_sheet_display_name,
    _layout_sheet_used_area_mm2,
    _layout_sheet_utilization_ratio,
    _cut_kind_for_orientation,
    _cut_phase_info,
    _cut_phase_rank,
    _cut_phase_label,
    _sequence_zone_display_label,
    _layout_sheet_sequence_zone_labels,
    _cut_phase_style,
    _cut_line_color,
    _cut_line_width,
    _cut_draw_style,
    _layout_sheet_usable_rect,
)

def _apply_cnc_origin_corner(layout_sheets, corner):
    """Reflete as posições das peças e cortes para o canto escolhido.

    O nesting sempre empacota a partir de (0,0) = inferior-esquerdo. Se o
    CNC do usuário zera em outro canto, espelhamos as coordenadas:
      - inferior_esquerdo: identidade (sem mudança)
      - inferior_direito:  x' = L - x - placed_length
      - superior_esquerdo: y' = W - y - placed_width
      - superior_direito:  ambos

    Aplica também aos cut_steps (plano de corte), para que os pontos de
    início/fim fiquem coerentes com as peças.
    """
    if not layout_sheets:
        return
    corner = str(corner or "inferior_esquerdo")
    flip_x = corner in ("inferior_direito", "superior_direito")
    flip_y = corner in ("superior_esquerdo", "superior_direito")
    if not flip_x and not flip_y:
        return

    for sheet in layout_sheets:
        L = float(sheet.source_length_mm)
        W = float(sheet.source_width_mm)
        for p in sheet.placements:
            if flip_x:
                p.x_mm = L - p.x_mm - p.placed_length_mm
            if flip_y:
                p.y_mm = W - p.y_mm - p.placed_width_mm
        for step in sheet.cut_steps:
            if flip_x:
                step.start_x_mm = L - step.start_x_mm
                step.end_x_mm = L - step.end_x_mm
                if step.orientation == "horizontal":
                    # span em X é invariante; position em X vira L - position
                    pass
                elif step.orientation == "vertical":
                    step.position_mm = L - step.position_mm
            if flip_y:
                step.start_y_mm = W - step.start_y_mm
                step.end_y_mm = W - step.end_y_mm
                if step.orientation == "horizontal":
                    step.position_mm = W - step.position_mm


def _remove_object_tree(doc, obj):
    for child in list(getattr(obj, "Group", [])):
        _remove_object_tree(doc, child)
    if doc.getObject(obj.Name) is not None:
        doc.removeObject(obj.Name)


def _clamp_color_component(value):
    return max(0.0, min(1.0, float(value)))


def _mix_color(color, target_value, amount):
    return tuple(
        _clamp_color_component(component + ((target_value - component) * amount))
        for component in color
    )


def _darken_color(color, amount=0.25):
    return _mix_color(color, 0.0, amount)


def _lighten_color(color, amount=0.20):
    return _mix_color(color, 1.0, amount)


def _interpolate_color(color_a, color_b, amount):
    return tuple(
        _clamp_color_component(component_a + ((component_b - component_a) * amount))
        for component_a, component_b in zip(color_a, color_b)
    )


def _stable_palette_index(value, palette_size):
    text = value or "panelnest"
    total = 0
    for character in text:
        total = ((total * 131) + ord(character)) % 2147483647
    return total % palette_size if palette_size else 0


def _part_visual_signature(part):
    longer_side_mm = round(max(part.length_mm, part.width_mm), 4)
    shorter_side_mm = round(min(part.length_mm, part.width_mm), 4)
    thickness_mm = round(part.thickness_mm, 4)
    material = _material_value(part.material) or "sem-material"
    area_mm2 = round(longer_side_mm * shorter_side_mm, 4)
    return material, thickness_mm, area_mm2, longer_side_mm, shorter_side_mm


def _part_visual_key_from_signature(signature):
    material, thickness_mm, _area_mm2, longer_side_mm, shorter_side_mm = signature
    return f"{material}|{thickness_mm:.4f}|{longer_side_mm:.4f}|{shorter_side_mm:.4f}"


def _part_visual_key(part):
    return _part_visual_key_from_signature(_part_visual_signature(part))


def _palette_color_at(position):
    if not PART_VISUAL_PALETTE:
        return (0.40, 0.62, 0.56)

    palette_max_index = len(PART_VISUAL_PALETTE) - 1
    clamped_position = max(0.0, min(float(position), float(palette_max_index)))
    lower_index = int(clamped_position)
    upper_index = min(palette_max_index, lower_index + 1)
    if lower_index == upper_index:
        return PART_VISUAL_PALETTE[lower_index]
    blend_amount = clamped_position - lower_index
    return _interpolate_color(
        PART_VISUAL_PALETTE[lower_index],
        PART_VISUAL_PALETTE[upper_index],
        blend_amount,
    )


def _build_layout_part_color_map(layout_sheets):
    unique_signatures = sorted(
        {
            _part_visual_signature(placement.part)
            for layout_sheet in layout_sheets
            for placement in layout_sheet.placements
        }
    )
    if not unique_signatures:
        return {}

    if len(PART_VISUAL_PALETTE) <= 1 or len(unique_signatures) == 1:
        middle_position = max(0.0, (len(PART_VISUAL_PALETTE) - 1) / 2.0)
        return {
            _part_visual_key_from_signature(signature): _palette_color_at(middle_position)
            for signature in unique_signatures
        }

    palette_start = 0.5 if len(PART_VISUAL_PALETTE) > 3 else 0.0
    palette_end = (len(PART_VISUAL_PALETTE) - 1.5) if len(PART_VISUAL_PALETTE) > 3 else (len(PART_VISUAL_PALETTE) - 1.0)
    divisor = max(1, len(unique_signatures) - 1)
    color_map = {}

    for index, signature in enumerate(unique_signatures):
        position = palette_start + (((palette_end - palette_start) * index) / divisor)
        color_map[_part_visual_key_from_signature(signature)] = _palette_color_at(position)

    return color_map


def _placed_profile_points(placement):
    """Transforma o perfil canônico da peça para sua orientação no layout."""
    profile = getattr(placement.part, "profile_points", None) or []
    if not profile:
        return []

    points = [(float(point[0]), float(point[1])) for point in profile]
    return [
        transform_placement_point(placement, point_x, point_y)
        for point_x, point_y in points
    ]


def _try_add_shape_contour(doc, container, placement, layout_sheet,
                           group_idx, sheet_pos, part_idx, line_color):
    """Adiciona wire de contorno real da peça no layout (para peças não-retangulares).

    Projeta a face superior (maior face plana) do objeto FreeCAD original,
    translada para a posição no layout, e cria um Part::Feature com o wire.
    Peças retangulares são ignoradas (o Part::Box já mostra o contorno).
    """
    if App is None or Part is None or doc is None:
        return
    part = placement.part
    z_contour = max(layout_sheet.thickness_mm, 1.0) * 2.0 + 0.05

    # O perfil extraído já está normalizado nos mesmos eixos comprimento ×
    # largura usados pelo nesting. Reutilizá-lo evita que o sólido e o wire
    # sejam desenhados com orientações diferentes.
    placed_profile = _placed_profile_points(placement)
    if placed_profile:
        try:
            contour_edges = []
            for index, point in enumerate(placed_profile):
                next_point = placed_profile[(index + 1) % len(placed_profile)]
                start = App.Vector(
                    placement.x_mm + point[0],
                    placement.y_mm + point[1],
                    z_contour,
                )
                end = App.Vector(
                    placement.x_mm + next_point[0],
                    placement.y_mm + next_point[1],
                    z_contour,
                )
                if start.distanceToPoint(end) > 0.01:
                    contour_edges.append(Part.LineSegment(start, end).toShape())
            if contour_edges:
                _create_layout_shape_contour(
                    doc,
                    container,
                    placement,
                    group_idx,
                    sheet_pos,
                    part_idx,
                    line_color,
                    contour_edges,
                )
            return
        except Exception:
            return

    obj_name = getattr(part, "object_name", "") or ""
    if not obj_name:
        return

    try:
        source_obj = doc.getObject(obj_name)
        if source_obj is None:
            return
        shape = getattr(source_obj, "Shape", None)
        if shape is None or shape.isNull():
            return

        # Verificar se é não-retangular: comparar volume com bounding box
        bbox = shape.BoundBox
        bbox_vol = bbox.XLength * bbox.YLength * bbox.ZLength
        if bbox_vol <= 0:
            return
        fill_ratio = shape.Volume / bbox_vol
        if fill_ratio > 0.95:
            return  # Essencialmente retangular, skip

        # Encontrar a maior face plana (face superior)
        dims = {"x": bbox.XLength, "y": bbox.YLength, "z": bbox.ZLength}
        t_axis = min(dims, key=dims.get)
        best_face = None
        best_area = 0.0
        for face in shape.Faces:
            surface = getattr(face, "Surface", None)
            if surface is None:
                continue
            if "Plane" not in type(surface).__name__:
                continue
            normal = getattr(surface, "Axis", None)
            if normal is None:
                continue
            if abs(getattr(normal, t_axis, 0.0)) < 0.85:
                continue
            area = face.Area
            if area > best_area:
                best_area = area
                best_face = face

        if best_face is None:
            # Fallback: projetar toda a shape no plano XY
            try:
                best_face = shape.Faces[0]
            except Exception:
                return

        # Extrair wires da face e obter os edges do contorno externo
        wires = best_face.Wires
        if not wires:
            return
        outer_wire = max(wires, key=lambda w: w.Length)

        # Usar a mesma convenção canônica de extract_part_profile:
        # maior eixo do plano = comprimento/X; menor = largura/Y.
        plane_axes = [axis for axis in ("x", "y", "z") if axis != t_axis]
        length_axis = max(plane_axes, key=lambda axis: dims[axis])
        width_axis = min(plane_axes, key=lambda axis: dims[axis])
        original_length_mm = dims[length_axis]
        original_width_mm = dims[width_axis]
        rotation_deg = placement_rotation_deg(placement)

        # Calcular offset: shape bbox min -> placement position
        shape_min = {
            "x": bbox.XMin, "y": bbox.YMin, "z": bbox.ZMin,
        }

        # Escalar/transformar os edges para posição no layout
        contour_edges = []
        for edge in outer_wire.Edges:
            new_vertices = []
            for vertex in edge.Vertexes:
                pt = vertex.Point
                # Coordenadas locais (relativas ao bbox da peça)
                local_vals = {
                    "x": pt.x - shape_min["x"],
                    "y": pt.y - shape_min["y"],
                    "z": pt.z - shape_min["z"],
                }
                lx = local_vals[length_axis]
                ly = local_vals[width_axis]

                lx, ly = transform_local_point(
                    lx,
                    ly,
                    original_length_mm,
                    original_width_mm,
                    rotation_deg,
                )

                # Posição absoluta no layout
                abs_x = placement.x_mm + lx
                abs_y = placement.y_mm + ly
                new_vertices.append(App.Vector(abs_x, abs_y, z_contour))

            if len(new_vertices) == 2:
                # Verificar se o edge é curvo (arco/círculo)
                curve = getattr(edge, "Curve", None)
                curve_type = type(curve).__name__ if curve is not None else ""
                if "Circle" in curve_type or "Ellipse" in curve_type or "BSpline" in curve_type:
                    # Discretizar curva em segmentos de linha
                    try:
                        pts = edge.discretize(Number=24)
                        for i in range(len(pts) - 1):
                            p0 = pts[i]
                            p1 = pts[i + 1]
                            lx0 = getattr(p0, length_axis) - shape_min[length_axis]
                            ly0 = getattr(p0, width_axis) - shape_min[width_axis]
                            lx1 = getattr(p1, length_axis) - shape_min[length_axis]
                            ly1 = getattr(p1, width_axis) - shape_min[width_axis]
                            lx0, ly0 = transform_local_point(
                                lx0,
                                ly0,
                                original_length_mm,
                                original_width_mm,
                                rotation_deg,
                            )
                            lx1, ly1 = transform_local_point(
                                lx1,
                                ly1,
                                original_length_mm,
                                original_width_mm,
                                rotation_deg,
                            )
                            v0 = App.Vector(placement.x_mm + lx0, placement.y_mm + ly0, z_contour)
                            v1 = App.Vector(placement.x_mm + lx1, placement.y_mm + ly1, z_contour)
                            if v0.distanceToPoint(v1) > 0.01:
                                contour_edges.append(Part.LineSegment(v0, v1).toShape())
                        continue
                    except Exception:
                        pass
                # Edge reta simples
                if new_vertices[0].distanceToPoint(new_vertices[1]) > 0.01:
                    contour_edges.append(Part.LineSegment(new_vertices[0], new_vertices[1]).toShape())

        if not contour_edges:
            return

        _create_layout_shape_contour(
            doc,
            container,
            placement,
            group_idx,
            sheet_pos,
            part_idx,
            line_color,
            contour_edges,
        )

    except Exception:
        pass  # Silencioso — contorno é opcional


def _create_layout_shape_contour(
    doc,
    container,
    placement,
    group_idx,
    sheet_pos,
    part_idx,
    line_color,
    contour_edges,
):
    """Cria e estiliza o wire visual de um perfil colocado no layout."""
    compound = Part.makeCompound(contour_edges)
    contour_name = (
        f"PanelNestContorno{group_idx:02d}_{sheet_pos:02d}_{part_idx:02d}"
    )
    contour_obj = doc.addObject("Part::Feature", contour_name)
    contour_obj.Shape = compound
    contour_obj.Label = f"Contorno {placement.part.part_id}"
    _mark_internal_object(contour_obj, "layout_part_contour")
    container.addObject(contour_obj)

    view = getattr(contour_obj, "ViewObject", None)
    if view is not None:
        try:
            view.LineColor = line_color
            view.LineWidth = 2.0
            view.PointSize = 0.0
        except Exception:
            pass
    return contour_obj


def _part_fill_color(part, color_map=None, rotated=False):
    color_key = _part_visual_key(part)
    base_color = (color_map or {}).get(color_key)
    if base_color is None:
        palette_index = _stable_palette_index(color_key, len(PART_VISUAL_PALETTE))
        base_color = PART_VISUAL_PALETTE[palette_index] if PART_VISUAL_PALETTE else (0.40, 0.62, 0.56)
    return _darken_color(base_color, 0.12) if rotated else base_color


def _sheet_fill_color(layout_sheet):
    base_color = SHEET_SOURCE_COLORS.get(layout_sheet.source_kind, (0.77, 0.82, 0.86))
    return _lighten_color(base_color, 0.08)


def _apply_view_style(obj, fill_color=None, line_color=None, transparency=None):
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return

    if fill_color is not None and hasattr(view_object, "ShapeColor"):
        try:
            view_object.ShapeColor = fill_color
        except Exception:
            pass
    if line_color is not None and hasattr(view_object, "LineColor"):
        try:
            view_object.LineColor = line_color
        except Exception:
            pass
    if line_color is not None and hasattr(view_object, "PointColor"):
        try:
            view_object.PointColor = line_color
        except Exception:
            pass
    if transparency is not None and hasattr(view_object, "Transparency"):
        try:
            view_object.Transparency = int(max(0, min(100, round(transparency))))
        except Exception:
            pass
    if hasattr(view_object, "DisplayMode"):
        try:
            view_object.DisplayMode = "Flat Lines"
        except Exception:
            pass


def _apply_cut_view_style(obj, line_color, line_width=3.0, draw_style="Solid"):
    _apply_view_style(obj, line_color=line_color, transparency=0)
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return
    if hasattr(view_object, "LineWidth"):
        try:
            view_object.LineWidth = float(line_width)
        except Exception:
            pass
    if hasattr(view_object, "DrawStyle"):
        try:
            view_object.DrawStyle = str(draw_style)
        except Exception:
            pass


def _cut_step_midpoint(cut_step):
    return (
        (cut_step.start_x_mm + cut_step.end_x_mm) / 2.0,
        (cut_step.start_y_mm + cut_step.end_y_mm) / 2.0,
    )


def _clamp_mm(value, minimum, maximum):
    return max(minimum, min(maximum, value))


CUT_MARKER_DIGIT_WIDTH_MM = 14.0
CUT_MARKER_DIGIT_HEIGHT_MM = 22.0
CUT_MARKER_DIGIT_GAP_MM = 3.0
CUT_MARKER_STROKE_MM = 2.4
CUT_MARKER_BADGE_PADDING_X_MM = 6.0
CUT_MARKER_BADGE_PADDING_Y_MM = 5.0
CUT_MARKER_SIDE_GAP_MM = 18.0
CUT_MARKER_LANE_GAP_MM = 8.0
CUT_MARKER_PLATE_DEPTH_MM = 0.6
CUT_MARKER_TEXT_DEPTH_MM = 0.9
CUT_MARKER_SCREEN_FONT_SIZE_MM = 22.0
CUT_MARKER_SCREEN_LAYOUT_WIDTH_FACTOR = 1.6
CUT_MARKER_SCREEN_LAYOUT_HEIGHT_FACTOR = 1.35
CUT_MARKER_ELBOW_GAP_MM = 10.0
PART_LABEL_FONT_SIZE_MM = 24.0
PART_LABEL_FALLBACK_FONT_SIZE_MM = 18.0
PART_LABEL_COMPACT_FONT_SIZE_MM = 14.0
PART_LABEL_MINI_FONT_SIZE_MM = 11.5
PART_LABEL_TINY_FONT_SIZE_MM = 9.0
PART_LABEL_MICRO_FONT_SIZE_MM = 7.2
PART_DIMENSION_FONT_SIZE_MM = 22.0
PART_DIMENSION_FALLBACK_FONT_SIZE_MM = 16.5
PART_DIMENSION_COMPACT_FONT_SIZE_MM = 13.0
PART_DIMENSION_MINI_FONT_SIZE_MM = 10.0
PART_DIMENSION_TINY_FONT_SIZE_MM = 7.8
PART_ANNOTATION_SCREEN_CHAR_WIDTH_FACTOR = 0.72
PART_ANNOTATION_SCREEN_LINE_HEIGHT_FACTOR = 1.30
PART_ANNOTATION_PADDING_X_MM = 18.0
PART_ANNOTATION_PADDING_Y_MM = 14.0
PART_DIMENSION_EDGE_GAP_MM = 12.0
PART_LABEL_MIN_LENGTH_MM = 180.0
PART_LABEL_MIN_WIDTH_MM = 90.0
PART_LABEL_MIN_AREA_MM2 = 42000.0
PART_LABEL_CODE_ONLY_MIN_LENGTH_MM = 60.0
PART_LABEL_CODE_ONLY_MIN_WIDTH_MM = 24.0
PART_LABEL_CODE_ONLY_MIN_AREA_MM2 = 2600.0
PART_DIMENSION_MIN_LENGTH_MM = 52.0
PART_DIMENSION_MIN_WIDTH_MM = 18.0
PART_DIMENSION_MIN_AREA_MM2 = 2200.0
LAYOUT_EDGE_BAND_MARKER_PREFERRED_WIDTH_MM = 10.0
LAYOUT_EDGE_BAND_MARKER_MIN_WIDTH_MM = 3.0
LAYOUT_EDGE_BAND_MARKER_MAX_WIDTH_MM = 14.0
LAYOUT_EDGE_BAND_MARKER_HEIGHT_MM = 0.8
LAYOUT_EDGE_BAND_MARKER_INSET_MM = 2.2
LAYOUT_EDGE_BAND_LABEL_FONT_SIZE_MM = 8.0
LAYOUT_EDGE_BAND_LABEL_MINI_FONT_SIZE_MM = 6.4
LAYOUT_EDGE_BAND_LABEL_TINY_FONT_SIZE_MM = 5.2


def _cut_marker_size(text):
    digit_width_mm = CUT_MARKER_DIGIT_WIDTH_MM
    digit_height_mm = CUT_MARKER_DIGIT_HEIGHT_MM
    digit_gap_mm = CUT_MARKER_DIGIT_GAP_MM
    width_mm = (len(text) * digit_width_mm) + (max(0, len(text) - 1) * digit_gap_mm)
    return width_mm, digit_height_mm


def _cut_marker_badge_size(text):
    marker_width_mm, marker_height_mm = _cut_marker_size(text)
    return (
        marker_width_mm + (2.0 * CUT_MARKER_BADGE_PADDING_X_MM),
        marker_height_mm + (2.0 * CUT_MARKER_BADGE_PADDING_Y_MM),
    )


def _cut_marker_layout_size(text):
    badge_width_mm, badge_height_mm = _cut_marker_badge_size(text)
    screen_width_mm = len(text) * CUT_MARKER_SCREEN_FONT_SIZE_MM * CUT_MARKER_SCREEN_LAYOUT_WIDTH_FACTOR
    screen_height_mm = CUT_MARKER_SCREEN_FONT_SIZE_MM * CUT_MARKER_SCREEN_LAYOUT_HEIGHT_FACTOR
    return (
        max(badge_width_mm, screen_width_mm),
        max(badge_height_mm, screen_height_mm),
    )


def _cut_marker_badge_interval(side, cut_step, badge_width_mm, badge_height_mm, layout_sheet):
    if side in {"top", "bottom"}:
        origin_mm = _clamp_mm(
            cut_step.position_mm - (badge_width_mm / 2.0),
            0.0,
            max(0.0, layout_sheet.source_length_mm - badge_width_mm),
        )
        return origin_mm, badge_width_mm

    origin_mm = _clamp_mm(
        cut_step.position_mm - (badge_height_mm / 2.0),
        0.0,
        max(0.0, layout_sheet.source_width_mm - badge_height_mm),
    )
    return origin_mm, badge_height_mm


def _cut_marker_intervals_conflict(interval_start_mm, interval_size_mm, existing_start_mm, existing_size_mm):
    return not (
        interval_start_mm + interval_size_mm + CUT_MARKER_LANE_GAP_MM
        <= existing_start_mm + DIMENSION_EQUALITY_TOLERANCE_MM
        or existing_start_mm + existing_size_mm + CUT_MARKER_LANE_GAP_MM
        <= interval_start_mm + DIMENSION_EQUALITY_TOLERANCE_MM
    )


def _cut_marker_adjusted_interval_start(lane_intervals, desired_start_mm, interval_size_mm, max_extent_mm):
    max_start_mm = max(0.0, max_extent_mm - interval_size_mm)
    clamped_start_mm = _clamp_mm(desired_start_mm, 0.0, max_start_mm)
    candidate_starts_mm = {clamped_start_mm, 0.0, max_start_mm}

    for existing_start_mm, existing_size_mm in lane_intervals:
        candidate_starts_mm.add(
            _clamp_mm(
                existing_start_mm - interval_size_mm - CUT_MARKER_LANE_GAP_MM,
                0.0,
                max_start_mm,
            )
        )
        candidate_starts_mm.add(
            _clamp_mm(
                existing_start_mm + existing_size_mm + CUT_MARKER_LANE_GAP_MM,
                0.0,
                max_start_mm,
            )
        )

    best_start_mm = None
    best_score = None

    for candidate_start_mm in sorted(candidate_starts_mm):
        conflict_found = False
        for existing_start_mm, existing_size_mm in lane_intervals:
            if _cut_marker_intervals_conflict(
                candidate_start_mm,
                interval_size_mm,
                existing_start_mm,
                existing_size_mm,
            ):
                conflict_found = True
                break
        if conflict_found:
            continue

        score = round(abs(candidate_start_mm - desired_start_mm), 4)
        if best_score is None or score < best_score:
            best_score = score
            best_start_mm = candidate_start_mm

    return best_start_mm, (best_score if best_score is not None else float("inf"))


def _append_cut_marker_lane_interval(intervals_by_lane, lane_index, interval_start_mm, interval_size_mm):
    while lane_index >= len(intervals_by_lane):
        intervals_by_lane.append([])
    intervals_by_lane[lane_index].append((interval_start_mm, interval_size_mm))


def _cut_marker_side_load(intervals_by_lane):
    return sum(len(lane_intervals) for lane_intervals in intervals_by_lane)


def _cut_marker_preferred_side(cut_step, layout_sheet):
    start_x_mm = min(cut_step.start_x_mm, cut_step.end_x_mm)
    end_x_mm = max(cut_step.start_x_mm, cut_step.end_x_mm)
    start_y_mm = min(cut_step.start_y_mm, cut_step.end_y_mm)
    end_y_mm = max(cut_step.start_y_mm, cut_step.end_y_mm)

    if cut_step.orientation == "Vertical":
        distance_top_mm = max(0.0, layout_sheet.source_width_mm - end_y_mm)
        distance_bottom_mm = max(0.0, start_y_mm)
        return "top" if distance_top_mm <= distance_bottom_mm else "bottom"

    distance_left_mm = max(0.0, start_x_mm)
    distance_right_mm = max(0.0, layout_sheet.source_length_mm - end_x_mm)
    return "right" if distance_right_mm <= distance_left_mm else "left"


def _cut_marker_badge_origin(
    side,
    lane_index,
    interval_start_mm,
    badge_width_mm,
    badge_height_mm,
    layout_sheet,
):
    if side == "top":
        return (
            interval_start_mm,
            layout_sheet.source_width_mm + CUT_MARKER_SIDE_GAP_MM + (
                lane_index * (badge_height_mm + CUT_MARKER_LANE_GAP_MM)
            ),
        )
    if side == "bottom":
        return (
            interval_start_mm,
            -CUT_MARKER_SIDE_GAP_MM - badge_height_mm - (
                lane_index * (badge_height_mm + CUT_MARKER_LANE_GAP_MM)
            ),
        )
    if side == "right":
        return (
            layout_sheet.source_length_mm + CUT_MARKER_SIDE_GAP_MM + (
                lane_index * (badge_width_mm + CUT_MARKER_LANE_GAP_MM)
            ),
            interval_start_mm,
        )
    return (
        -CUT_MARKER_SIDE_GAP_MM - badge_width_mm - (
            lane_index * (badge_width_mm + CUT_MARKER_LANE_GAP_MM)
        ),
        interval_start_mm,
    )


def _cut_marker_best_lane_choice(
    side,
    intervals_by_lane,
    desired_start_mm,
    interval_size_mm,
    lane_span_mm,
    max_extent_mm,
):
    best_choice = None

    for lane_index, lane_intervals in enumerate(intervals_by_lane):
        adjusted_start_mm, shift_distance_mm = _cut_marker_adjusted_interval_start(
            lane_intervals,
            desired_start_mm,
            interval_size_mm,
            max_extent_mm,
        )
        if adjusted_start_mm is None:
            continue
        guide_extra_mm = shift_distance_mm + (lane_index * (lane_span_mm + CUT_MARKER_LANE_GAP_MM))
        choice = {
            "side": side,
            "lane_index": lane_index,
            "interval_start_mm": adjusted_start_mm,
            "interval_size_mm": interval_size_mm,
            "score": (
                lane_index,
                round(shift_distance_mm, 4),
                round(guide_extra_mm, 4),
                _cut_marker_side_load(intervals_by_lane),
            ),
        }
        if best_choice is None or choice["score"] < best_choice["score"]:
            best_choice = choice

    new_lane_index = len(intervals_by_lane)
    new_lane_start_mm = _clamp_mm(
        desired_start_mm,
        0.0,
        max(0.0, max_extent_mm - interval_size_mm),
    )
    new_lane_choice = {
        "side": side,
        "lane_index": new_lane_index,
        "interval_start_mm": new_lane_start_mm,
        "interval_size_mm": interval_size_mm,
        "score": (
            round(new_lane_index * (lane_span_mm + CUT_MARKER_LANE_GAP_MM), 4),
            new_lane_index,
            _cut_marker_side_load(intervals_by_lane),
        ),
    }

    if best_choice is not None:
        return best_choice
    return new_lane_choice


def _build_cut_marker_positions(layout_sheet, settings, z_mm):
    del settings

    positions = {}
    vertical_lanes = {"top": [], "bottom": []}
    horizontal_lanes = {"left": [], "right": []}
    badge_width_mm, badge_height_mm = _cut_marker_layout_size("99")

    vertical_steps = sorted(
        (cut_step for cut_step in layout_sheet.cut_steps if cut_step.orientation == "Vertical"),
        key=lambda current: (current.position_mm, current.step_index),
    )
    for cut_step in vertical_steps:
        preferred_side = _cut_marker_preferred_side(cut_step, layout_sheet)
        candidate_sides = [preferred_side, "bottom" if preferred_side == "top" else "top"]
        best_choice = None

        for preference_index, side in enumerate(candidate_sides):
            desired_start_mm, interval_size_mm = _cut_marker_badge_interval(
                side,
                cut_step,
                badge_width_mm,
                badge_height_mm,
                layout_sheet,
            )
            choice = _cut_marker_best_lane_choice(
                side,
                vertical_lanes[side],
                desired_start_mm,
                interval_size_mm,
                badge_height_mm,
                layout_sheet.source_length_mm,
            )
            choice["score"] = (preference_index,) + choice["score"]
            if best_choice is None or choice["score"] < best_choice["score"]:
                best_choice = choice

        _append_cut_marker_lane_interval(
            vertical_lanes[best_choice["side"]],
            best_choice["lane_index"],
            best_choice["interval_start_mm"],
            best_choice["interval_size_mm"],
        )
        origin_x_mm, origin_y_mm = _cut_marker_badge_origin(
            best_choice["side"],
            best_choice["lane_index"],
            best_choice["interval_start_mm"],
            badge_width_mm,
            badge_height_mm,
            layout_sheet,
        )
        positions[cut_step.step_index] = {
            "origin": App.Vector(origin_x_mm, origin_y_mm, z_mm),
            "side": best_choice["side"],
            "width_mm": badge_width_mm,
            "height_mm": badge_height_mm,
        }

    horizontal_steps = sorted(
        (cut_step for cut_step in layout_sheet.cut_steps if cut_step.orientation == "Horizontal"),
        key=lambda current: (current.position_mm, current.step_index),
    )
    for cut_step in horizontal_steps:
        preferred_side = _cut_marker_preferred_side(cut_step, layout_sheet)
        candidate_sides = [preferred_side, "left" if preferred_side == "right" else "right"]
        best_choice = None

        for preference_index, side in enumerate(candidate_sides):
            desired_start_mm, interval_size_mm = _cut_marker_badge_interval(
                side,
                cut_step,
                badge_width_mm,
                badge_height_mm,
                layout_sheet,
            )
            choice = _cut_marker_best_lane_choice(
                side,
                horizontal_lanes[side],
                desired_start_mm,
                interval_size_mm,
                badge_width_mm,
                layout_sheet.source_width_mm,
            )
            choice["score"] = (preference_index,) + choice["score"]
            if best_choice is None or choice["score"] < best_choice["score"]:
                best_choice = choice

        _append_cut_marker_lane_interval(
            horizontal_lanes[best_choice["side"]],
            best_choice["lane_index"],
            best_choice["interval_start_mm"],
            best_choice["interval_size_mm"],
        )
        origin_x_mm, origin_y_mm = _cut_marker_badge_origin(
            best_choice["side"],
            best_choice["lane_index"],
            best_choice["interval_start_mm"],
            badge_width_mm,
            badge_height_mm,
            layout_sheet,
        )
        positions[cut_step.step_index] = {
            "origin": App.Vector(origin_x_mm, origin_y_mm, z_mm),
            "side": best_choice["side"],
            "width_mm": badge_width_mm,
            "height_mm": badge_height_mm,
        }

    return positions


def _digit_segments(character):
    segment_map = {
        "0": "ABCFED",
        "1": "BC",
        "2": "ABGED",
        "3": "ABGCD",
        "4": "FGBC",
        "5": "AFGCD",
        "6": "AFGECD",
        "7": "ABC",
        "8": "ABCDEFG",
        "9": "ABFGCD",
    }
    return segment_map.get(character, "")


def _segment_box(origin, segment_name, digit_width_mm, digit_height_mm, stroke_mm, depth_mm):
    upper_height_mm = (digit_height_mm - (3.0 * stroke_mm)) / 2.0
    lower_height_mm = upper_height_mm
    middle_y_mm = lower_height_mm + stroke_mm

    definitions = {
        "A": (stroke_mm, digit_height_mm - stroke_mm, digit_width_mm - (2.0 * stroke_mm), stroke_mm),
        "B": (digit_width_mm - stroke_mm, middle_y_mm + stroke_mm, stroke_mm, upper_height_mm),
        "C": (digit_width_mm - stroke_mm, 0.0, stroke_mm, lower_height_mm),
        "D": (stroke_mm, 0.0, digit_width_mm - (2.0 * stroke_mm), stroke_mm),
        "E": (0.0, 0.0, stroke_mm, lower_height_mm),
        "F": (0.0, middle_y_mm + stroke_mm, stroke_mm, upper_height_mm),
        "G": (stroke_mm, middle_y_mm, digit_width_mm - (2.0 * stroke_mm), stroke_mm),
    }
    x_mm, y_mm, length_mm, width_mm = definitions[segment_name]
    return Part.makeBox(
        length_mm,
        width_mm,
        depth_mm,
        origin.add(App.Vector(x_mm, y_mm, 0)),
    )


def _cut_marker_plate_shape(text, origin, depth_mm=CUT_MARKER_PLATE_DEPTH_MM):
    badge_width_mm, badge_height_mm = _cut_marker_badge_size(text)
    return Part.makeBox(
        badge_width_mm,
        badge_height_mm,
        depth_mm,
        origin,
    )


def _apply_badge_view_style(obj):
    _apply_view_style(
        obj,
        fill_color=(0.99, 0.96, 0.78),
        line_color=(0.17, 0.24, 0.43),
        transparency=0,
    )
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return
    if hasattr(view_object, "LineWidth"):
        try:
            view_object.LineWidth = 2.0
        except Exception:
            pass


def _apply_screen_text_style(obj):
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return
    if hasattr(view_object, "TextColor"):
        try:
            view_object.TextColor = (0.07, 0.20, 0.52)
        except Exception:
            pass
    if hasattr(view_object, "FontSize"):
        try:
            view_object.FontSize = CUT_MARKER_SCREEN_FONT_SIZE_MM
        except Exception:
            pass
    if hasattr(view_object, "LineColor"):
        try:
            view_object.LineColor = (0.07, 0.20, 0.52)
        except Exception:
            pass


def _color_luminance(color):
    red, green, blue = color
    return (0.2126 * red) + (0.7152 * green) + (0.0722 * blue)


def _apply_part_annotation_text_style(obj, text_color=(0.22, 0.16, 0.15), font_size_mm=PART_LABEL_FONT_SIZE_MM):
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return
    if hasattr(view_object, "TextColor"):
        try:
            view_object.TextColor = text_color
        except Exception:
            pass
    if hasattr(view_object, "FontSize"):
        try:
            view_object.FontSize = font_size_mm
        except Exception:
            pass
    if hasattr(view_object, "LineColor"):
        try:
            view_object.LineColor = text_color
        except Exception:
            pass


def _compact_part_annotation_label(label):
    compact_label = PART_LABEL_PATTERN.sub("", label).strip()
    compact_label = compact_label.replace("_", " ").replace("-", " ")
    compact_label = re.sub(r"\s+", " ", compact_label).strip()
    if len(compact_label) > 16:
        compact_label = compact_label[:13].rstrip() + "..."
    return compact_label


def _part_annotation_layout_size(lines, font_size_mm):
    if not lines:
        return 0.0, 0.0
    max_line_chars = max((len(str(line)) for line in lines), default=0)
    width_mm = max_line_chars * font_size_mm * PART_ANNOTATION_SCREEN_CHAR_WIDTH_FACTOR
    height_mm = len(lines) * font_size_mm * PART_ANNOTATION_SCREEN_LINE_HEIGHT_FACTOR
    return width_mm, height_mm


def _part_annotation_variant_dimensions(lines, font_size_mm, rotation_deg=0.0):
    text_width_mm, text_height_mm = _part_annotation_layout_size(lines, font_size_mm)
    if abs(rotation_deg) % 180.0 == 90.0:
        return text_height_mm, text_width_mm
    return text_width_mm, text_height_mm


def _part_annotation_padding(font_size_mm):
    scale = max(0.38, min(1.0, float(font_size_mm) / max(PART_LABEL_FONT_SIZE_MM, 1.0)))
    return (
        max(6.0, PART_ANNOTATION_PADDING_X_MM * scale),
        max(5.0, PART_ANNOTATION_PADDING_Y_MM * scale),
    )


def _part_dimension_edge_gap(font_size_mm):
    scale = max(0.38, min(1.0, float(font_size_mm) / max(PART_DIMENSION_FONT_SIZE_MM, 1.0)))
    return max(4.0, PART_DIMENSION_EDGE_GAP_MM * scale)


def _rotated_annotation_offset(x_mm, y_mm, rotation_deg):
    if App is None:
        return None
    radians_value = math.radians(float(rotation_deg) % 360.0)
    cos_value = math.cos(radians_value)
    sin_value = math.sin(radians_value)
    return App.Vector(
        (x_mm * cos_value) - (y_mm * sin_value),
        (x_mm * sin_value) + (y_mm * cos_value),
        0.0,
    )


def _annotation_origin_from_center(center, layout_width_mm, layout_height_mm, rotation_deg):
    if App is None:
        return center
    offset = _rotated_annotation_offset(
        -(layout_width_mm / 2.0),
        -(layout_height_mm / 2.0),
        -rotation_deg,
    )
    if offset is None:
        return center
    return App.Vector(
        center.x + offset.x,
        center.y + offset.y,
        center.z,
    )


def _annotation_shape_center(annotation):
    shape = getattr(annotation, "Shape", None)
    bbox = getattr(shape, "BoundBox", None)
    if bbox is None:
        return None
    try:
        return App.Vector(
            (bbox.XMin + bbox.XMax) / 2.0,
            (bbox.YMin + bbox.YMax) / 2.0,
            (bbox.ZMin + bbox.ZMax) / 2.0,
        )
    except Exception:
        return None


def _recenter_annotation_from_bbox(annotation, annotation_spec, rotation):
    if App is None or annotation is None:
        return
    target_center = annotation_spec.get("center_target")
    if target_center is None:
        return
    document = getattr(annotation, "Document", None)
    for _ in range(4):
        if document is not None:
            try:
                document.recompute()
            except Exception:
                pass
        current_center = _annotation_shape_center(annotation)
        if current_center is None:
            return
        delta_x = target_center.x - current_center.x
        delta_y = target_center.y - current_center.y
        if abs(delta_x) < 0.05 and abs(delta_y) < 0.05:
            return
        try:
            current_base = annotation.Placement.Base
            annotation.Placement = App.Placement(
                App.Vector(
                    current_base.x + delta_x,
                    current_base.y + delta_y,
                    current_base.z,
                ),
                rotation,
            )
        except Exception:
            return
    if document is not None:
        try:
            document.recompute()
        except Exception:
            pass


def _part_label_line_variants(placement, settings):
    if not bool(getattr(settings, "show_layout_part_labels", True)):
        return []
    longer_side_mm = max(placement.placed_length_mm, placement.placed_width_mm)
    shorter_side_mm = min(placement.placed_length_mm, placement.placed_width_mm)
    base_label = _compact_part_annotation_label(placement.part.label)
    large_enough_for_name = (
        base_label
        and longer_side_mm >= 420.0
        and shorter_side_mm >= 180.0
    )

    variants = []
    if large_enough_for_name:
        variants.append([placement.part.part_id, base_label])
    variants.append([placement.part.part_id])

    unique_variants = []
    seen = set()
    for lines in variants:
        normalized = tuple(str(line).strip() for line in lines if str(line).strip())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_variants.append(list(normalized))
    return unique_variants


def _part_dimension_value(placement, settings, axis):
    if not bool(getattr(settings, "show_layout_part_dimensions", True)):
        return ""
    if axis == "x":
        return _format_mm(placement.placed_length_mm)
    return _format_mm(placement.placed_width_mm)


def _part_center_annotation_candidate(
    placement,
    lines,
    z_mm,
    font_size_mm,
    rotation_deg=0.0,
    min_length_mm=PART_LABEL_MIN_LENGTH_MM,
    min_width_mm=PART_LABEL_MIN_WIDTH_MM,
    min_area_mm2=PART_LABEL_MIN_AREA_MM2,
):
    if App is None:
        return None
    longer_side_mm = max(placement.placed_length_mm, placement.placed_width_mm)
    shorter_side_mm = min(placement.placed_length_mm, placement.placed_width_mm)
    if (
        longer_side_mm < min_length_mm
        or shorter_side_mm < min_width_mm
        or (placement.placed_length_mm * placement.placed_width_mm) < min_area_mm2
    ):
        return None
    padding_x_mm, padding_y_mm = _part_annotation_padding(font_size_mm)
    text_width_mm, text_height_mm = _part_annotation_variant_dimensions(lines, font_size_mm, rotation_deg)
    if (
        text_width_mm + (2.0 * padding_x_mm) > placement.placed_length_mm
        or text_height_mm + (2.0 * padding_y_mm) > placement.placed_width_mm
    ):
        return None

    available_length_mm = max(placement.placed_length_mm - (2.0 * padding_x_mm), 1.0)
    available_width_mm = max(placement.placed_width_mm - (2.0 * padding_y_mm), 1.0)
    fit_score = min(
        available_length_mm / max(text_width_mm, 1.0),
        available_width_mm / max(text_height_mm, 1.0),
    )
    if placement.placed_width_mm > placement.placed_length_mm and abs(rotation_deg) % 180.0 == 90.0:
        fit_score += 0.8
    if placement.placed_length_mm >= placement.placed_width_mm and abs(rotation_deg) % 180.0 == 0.0:
        fit_score += 0.8

    center = App.Vector(
        placement.x_mm + (placement.placed_length_mm / 2.0),
        placement.y_mm + (placement.placed_width_mm / 2.0),
        z_mm,
    )
    return {
        "lines": lines,
        "rotation_deg": rotation_deg,
        "placement": center,
        "justification": "Center",
        "center_target": center,
        "recenter_via_bbox": True,
        "fit_score": fit_score,
        "font_size_mm": font_size_mm,
    }


def _part_axis_dimension_candidate(placement, text_value, z_mm, font_size_mm, axis):
    if App is None:
        return None
    longer_side_mm = max(placement.placed_length_mm, placement.placed_width_mm)
    shorter_side_mm = min(placement.placed_length_mm, placement.placed_width_mm)
    if (
        longer_side_mm < PART_DIMENSION_MIN_LENGTH_MM
        or shorter_side_mm < PART_DIMENSION_MIN_WIDTH_MM
        or (placement.placed_length_mm * placement.placed_width_mm) < PART_DIMENSION_MIN_AREA_MM2
    ):
        return None

    lines = [text_value]
    rotation_deg = 0.0 if axis == "x" else 90.0
    padding_x_mm, padding_y_mm = _part_annotation_padding(font_size_mm)
    edge_gap_mm = _part_dimension_edge_gap(font_size_mm)
    text_width_mm, text_height_mm = _part_annotation_variant_dimensions(lines, font_size_mm, rotation_deg)
    if (
        text_width_mm + (2.0 * padding_x_mm) > placement.placed_length_mm
        or text_height_mm + (2.0 * padding_y_mm) > placement.placed_width_mm
    ):
        return None

    candidate_specs = []
    if axis == "y":
        candidate_specs = [
            {
                "placement": App.Vector(
                    placement.x_mm + edge_gap_mm + (text_width_mm / 2.0),
                    placement.y_mm + (placement.placed_width_mm / 2.0),
                    z_mm,
                ),
                "justification": "Center",
            },
            {
                "placement": App.Vector(
                    placement.x_mm + placement.placed_length_mm - edge_gap_mm - (text_width_mm / 2.0),
                    placement.y_mm + (placement.placed_width_mm / 2.0),
                    z_mm,
                ),
                "justification": "Center",
            },
        ]
    else:
        candidate_specs = [
            {
                "placement": App.Vector(
                    placement.x_mm + (placement.placed_length_mm / 2.0),
                    placement.y_mm + placement.placed_width_mm - edge_gap_mm - (text_height_mm / 2.0),
                    z_mm,
                ),
                "justification": "Center",
            },
            {
                "placement": App.Vector(
                    placement.x_mm + (placement.placed_length_mm / 2.0),
                    placement.y_mm + edge_gap_mm + (text_height_mm / 2.0),
                    z_mm,
                ),
                "justification": "Center",
            },
        ]

    best_candidate = None
    for index, candidate_spec in enumerate(candidate_specs):
        score = 10.0 - index
        if axis == "y" and placement.placed_width_mm > placement.placed_length_mm:
            score += 2.0
        if axis == "x" and placement.placed_length_mm >= placement.placed_width_mm:
            score += 2.0
        candidate = {
            "lines": lines,
            "rotation_deg": rotation_deg,
            "placement": candidate_spec["placement"],
            "justification": candidate_spec["justification"],
            "fit_score": score,
            "font_size_mm": font_size_mm,
        }
        if best_candidate is None or candidate["fit_score"] > best_candidate["fit_score"]:
            best_candidate = candidate
    return best_candidate


def _part_label_annotation_spec(placement, settings, z_mm):
    best_candidate = None
    for lines in _part_label_line_variants(placement, settings):
        is_code_only = len(lines) == 1
        min_length_mm = PART_LABEL_CODE_ONLY_MIN_LENGTH_MM if is_code_only else PART_LABEL_MIN_LENGTH_MM
        min_width_mm = PART_LABEL_CODE_ONLY_MIN_WIDTH_MM if is_code_only else PART_LABEL_MIN_WIDTH_MM
        min_area_mm2 = PART_LABEL_CODE_ONLY_MIN_AREA_MM2 if is_code_only else PART_LABEL_MIN_AREA_MM2
        if is_code_only:
            font_sizes_mm = (
                PART_LABEL_FONT_SIZE_MM,
                PART_LABEL_FALLBACK_FONT_SIZE_MM,
                PART_LABEL_COMPACT_FONT_SIZE_MM,
                PART_LABEL_MINI_FONT_SIZE_MM,
                PART_LABEL_TINY_FONT_SIZE_MM,
                PART_LABEL_MICRO_FONT_SIZE_MM,
            )
        else:
            font_sizes_mm = (
                PART_LABEL_FONT_SIZE_MM,
                PART_LABEL_FALLBACK_FONT_SIZE_MM,
                PART_LABEL_COMPACT_FONT_SIZE_MM,
            )
        line_best_candidate = None
        for font_size_mm in font_sizes_mm:
            for rotation_deg in (0.0, 90.0):
                candidate = _part_center_annotation_candidate(
                    placement,
                    lines,
                    z_mm,
                    font_size_mm,
                    rotation_deg=rotation_deg,
                    min_length_mm=min_length_mm,
                    min_width_mm=min_width_mm,
                    min_area_mm2=min_area_mm2,
                )
                if candidate is None:
                    continue
                if line_best_candidate is None or candidate["fit_score"] > line_best_candidate["fit_score"]:
                    line_best_candidate = candidate
            if line_best_candidate is not None:
                break
        if line_best_candidate is not None:
            best_candidate = line_best_candidate
            break
    return best_candidate


def _part_dimension_annotation_specs(placement, settings, z_mm):
    specs = []
    for axis in ("x", "y"):
        text_value = _part_dimension_value(placement, settings, axis)
        if not text_value:
            continue
        best_candidate = None
        for font_size_mm in (
            PART_DIMENSION_FONT_SIZE_MM,
            PART_DIMENSION_FALLBACK_FONT_SIZE_MM,
            PART_DIMENSION_COMPACT_FONT_SIZE_MM,
            PART_DIMENSION_MINI_FONT_SIZE_MM,
            PART_DIMENSION_TINY_FONT_SIZE_MM,
        ):
            candidate = _part_axis_dimension_candidate(
                placement,
                text_value,
                z_mm,
                font_size_mm,
                axis,
            )
            if candidate is None:
                continue
            best_candidate = candidate
            break
        if best_candidate is not None:
            best_candidate["axis"] = axis
            specs.append(best_candidate)
    return specs


def _placement_edge_band_sides(placement):
    rotation_deg = placement_rotation_deg(placement)

    active_sides = []
    for source_side_key, is_active in (
        ("top", bool(placement.part.edge_band_top)),
        ("bottom", bool(placement.part.edge_band_bottom)),
        ("left", bool(placement.part.edge_band_left)),
        ("right", bool(placement.part.edge_band_right)),
    ):
        if not is_active:
            continue
        active_sides.append(
            {
                "source_side_key": source_side_key,
                "source_side_label": EDGE_BAND_SIDE_LABELS[source_side_key],
                "layout_side_key": rotated_layout_side(source_side_key, rotation_deg),
            }
        )
    return active_sides


def _layout_edge_band_marker_width(placement):
    shorter_side_mm = min(placement.placed_length_mm, placement.placed_width_mm)
    preferred_width_mm = max(
        LAYOUT_EDGE_BAND_MARKER_MIN_WIDTH_MM,
        min(
            LAYOUT_EDGE_BAND_MARKER_PREFERRED_WIDTH_MM,
            shorter_side_mm * 0.12,
        ),
    )
    max_allowed_mm = max(
        LAYOUT_EDGE_BAND_MARKER_MIN_WIDTH_MM,
        (shorter_side_mm / 2.0) - 1.0,
    )
    return min(preferred_width_mm, min(LAYOUT_EDGE_BAND_MARKER_MAX_WIDTH_MM, max_allowed_mm))


def _layout_edge_band_marker_specs(placement, settings, z_mm=0.0):
    if not bool(getattr(settings, "show_layout_edge_bands", True)):
        return []

    active_sides = _placement_edge_band_sides(placement)
    if not active_sides:
        return []

    marker_width_mm = _layout_edge_band_marker_width(placement)
    if marker_width_mm <= 0.0:
        return []

    inset_mm = min(
        max(1.0, LAYOUT_EDGE_BAND_MARKER_INSET_MM),
        max(1.0, marker_width_mm * 0.45),
    )
    usable_length_mm = placement.placed_length_mm - (2.0 * inset_mm)
    usable_width_mm = placement.placed_width_mm - (2.0 * inset_mm)
    if usable_length_mm <= 0.0 or usable_width_mm <= 0.0:
        return []

    specs = []
    for side_info in active_sides:
        layout_side_key = side_info["layout_side_key"]
        if layout_side_key == "top":
            x_mm = placement.x_mm + inset_mm
            y_mm = placement.y_mm + placement.placed_width_mm - inset_mm - marker_width_mm
            length_mm = usable_length_mm
            width_mm = marker_width_mm
        elif layout_side_key == "bottom":
            x_mm = placement.x_mm + inset_mm
            y_mm = placement.y_mm + inset_mm
            length_mm = usable_length_mm
            width_mm = marker_width_mm
        elif layout_side_key == "left":
            x_mm = placement.x_mm + inset_mm
            y_mm = placement.y_mm + inset_mm
            length_mm = marker_width_mm
            width_mm = usable_width_mm
        else:
            x_mm = placement.x_mm + placement.placed_length_mm - inset_mm - marker_width_mm
            y_mm = placement.y_mm + inset_mm
            length_mm = marker_width_mm
            width_mm = usable_width_mm

        if length_mm <= 0.0 or width_mm <= 0.0:
            continue

        specs.append(
            {
                "x_mm": x_mm,
                "y_mm": y_mm,
                "z_mm": z_mm,
                "length_mm": length_mm,
                "width_mm": width_mm,
                "source_side_key": side_info["source_side_key"],
                "source_side_label": side_info["source_side_label"],
                "source_side_abbreviation": EDGE_BAND_SIDE_ABBREVIATIONS[side_info["source_side_key"]],
                "layout_side_key": layout_side_key,
            }
        )
    return specs


def _layout_edge_band_label_spec(edge_spec, z_mm):
    if App is None:
        return None

    length_mm = float(edge_spec.get("length_mm", 0.0) or 0.0)
    width_mm = float(edge_spec.get("width_mm", 0.0) or 0.0)
    if length_mm <= 0.0 or width_mm <= 0.0:
        return None

    text_value = str(edge_spec.get("source_side_abbreviation", "") or "").strip()
    if not text_value:
        return None

    if length_mm >= width_mm:
        rotation_deg = 0.0
        available_length_mm = length_mm
        available_width_mm = width_mm
    else:
        rotation_deg = 90.0
        available_length_mm = width_mm
        available_width_mm = length_mm

    center = App.Vector(
        edge_spec["x_mm"] + (length_mm / 2.0),
        edge_spec["y_mm"] + (width_mm / 2.0),
        z_mm,
    )

    for font_size_mm in (
        LAYOUT_EDGE_BAND_LABEL_FONT_SIZE_MM,
        LAYOUT_EDGE_BAND_LABEL_MINI_FONT_SIZE_MM,
        LAYOUT_EDGE_BAND_LABEL_TINY_FONT_SIZE_MM,
    ):
        text_width_mm, text_height_mm = _part_annotation_variant_dimensions(
            [text_value],
            font_size_mm,
            rotation_deg,
        )
        if text_width_mm <= available_length_mm - 1.0 and text_height_mm <= available_width_mm - 0.8:
            return {
                "lines": [text_value],
                "rotation_deg": rotation_deg,
                "placement": center,
                "justification": "Center",
                "center_target": center,
                "recenter_via_bbox": True,
                "font_size_mm": font_size_mm,
            }

    return None


def _apply_layout_edge_band_style(obj):
    line_color = _darken_color(EDGE_BAND_HIGHLIGHT_COLOR, 0.42)
    _apply_view_style(
        obj,
        fill_color=EDGE_BAND_HIGHLIGHT_COLOR,
        line_color=line_color,
        transparency=0,
    )
    view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return
    if hasattr(view_object, "LineWidth"):
        try:
            view_object.LineWidth = 2.0
        except Exception:
            pass


def _create_part_text_annotation(
    sheet_container,
    object_name,
    annotation_spec,
    label_text,
    internal_kind,
    text_color=(0.22, 0.16, 0.15),
):
    try:
        import Draft
    except ImportError:
        return None

    if annotation_spec is None:
        return None

    lines = annotation_spec["lines"]
    text_placement = annotation_spec["placement"]
    rotation = App.Rotation(App.Vector(0, 0, 1), annotation_spec["rotation_deg"])
    placement_value = App.Placement(text_placement, rotation)

    annotation = None
    try:
        annotation = Draft.make_text(
            lines,
            placement=placement_value,
            screen=False,
        )
    except TypeError:
        try:
            annotation = Draft.make_text(
                lines,
                placement=text_placement,
                screen=False,
            )
        except Exception:
            annotation = None
    except Exception:
        annotation = None

    if annotation is None:
        try:
            annotation = Draft.make_text(
                "\n".join(lines),
                placement=placement_value,
                screen=False,
            )
        except TypeError:
            try:
                annotation = Draft.make_text(
                    "\n".join(lines),
                    placement=text_placement,
                    screen=False,
                )
            except Exception:
                annotation = None
        except Exception:
            annotation = None

    if annotation is None:
        return None

    try:
        annotation.Placement = placement_value
    except Exception:
        pass
    if hasattr(annotation, "Justification"):
        try:
            annotation.Justification = annotation_spec.get("justification", "Center")
        except Exception:
            pass
    if hasattr(annotation, "Angle"):
        try:
            annotation.Angle = float(annotation_spec["rotation_deg"])
        except Exception:
            pass
    annotation.Label = label_text
    _mark_internal_object(annotation, internal_kind)
    sheet_container.addObject(annotation)
    _apply_part_annotation_text_style(
        annotation,
        text_color=text_color,
        font_size_mm=annotation_spec.get("font_size_mm", PART_LABEL_FONT_SIZE_MM),
    )
    if annotation_spec.get("recenter_via_bbox"):
        _recenter_annotation_from_bbox(annotation, annotation_spec, rotation)
    return annotation


def _cut_marker_shape(text, origin, depth_mm=CUT_MARKER_TEXT_DEPTH_MM):
    digit_width_mm = CUT_MARKER_DIGIT_WIDTH_MM
    digit_height_mm = CUT_MARKER_DIGIT_HEIGHT_MM
    stroke_mm = CUT_MARKER_STROKE_MM
    digit_gap_mm = CUT_MARKER_DIGIT_GAP_MM
    shapes = []

    for index, character in enumerate(text):
        digit_origin = origin.add(App.Vector(index * (digit_width_mm + digit_gap_mm), 0, 0))
        for segment_name in _digit_segments(character):
            shapes.append(
                _segment_box(
                    digit_origin,
                    segment_name,
                    digit_width_mm,
                    digit_height_mm,
                    stroke_mm,
                    depth_mm,
                )
            )

    return Part.makeCompound(shapes) if shapes else None


def _create_screen_text_annotation(
    sheet_container,
    cut_step,
    marker_origin,
):
    try:
        import Draft
    except ImportError:
        return None

    marker_text = f"{cut_step.step_index:02d}"
    marker = None
    try:
        marker = Draft.make_text(
            marker_text,
            placement=App.Placement(marker_origin, App.Rotation()),
            screen=True,
        )
    except TypeError:
        try:
            marker = Draft.make_text(
                marker_text,
                placement=marker_origin,
                screen=True,
            )
        except Exception:
            marker = None
    except Exception:
        marker = None

    if marker is None:
        return None

    try:
        marker.Placement = App.Placement(marker_origin, App.Rotation())
    except Exception:
        pass
    marker.Label = f"Passo {cut_step.step_index:02d} - {cut_step.cut_kind}"
    _mark_internal_object(marker, "layout_cut_annotation")
    sheet_container.addObject(marker)
    _apply_screen_text_style(marker)
    return marker


def _create_cut_marker_guide(
    doc,
    sheet_container,
    object_name,
    cut_step,
    marker_origin,
    marker_side,
    marker_width_mm,
    marker_height_mm,
    layout_sheet,
):
    if Part is None or not marker_side:
        return None

    z_mm = marker_origin.z
    elbow_gap_mm = max(6.0, min(CUT_MARKER_SIDE_GAP_MM - 2.0, CUT_MARKER_ELBOW_GAP_MM))
    guide_points = []

    if marker_side == "top":
        attach_x_mm = marker_origin.x + (marker_width_mm / 2.0)
        attach_y_mm = marker_origin.y - 4.0
        guide_points = [
            App.Vector(cut_step.position_mm, layout_sheet.source_width_mm, z_mm),
            App.Vector(cut_step.position_mm, layout_sheet.source_width_mm + elbow_gap_mm, z_mm),
            App.Vector(attach_x_mm, layout_sheet.source_width_mm + elbow_gap_mm, z_mm),
            App.Vector(attach_x_mm, attach_y_mm, z_mm),
        ]
    elif marker_side == "bottom":
        attach_x_mm = marker_origin.x + (marker_width_mm / 2.0)
        attach_y_mm = marker_origin.y + marker_height_mm + 4.0
        guide_points = [
            App.Vector(cut_step.position_mm, 0.0, z_mm),
            App.Vector(cut_step.position_mm, -elbow_gap_mm, z_mm),
            App.Vector(attach_x_mm, -elbow_gap_mm, z_mm),
            App.Vector(attach_x_mm, attach_y_mm, z_mm),
        ]
    elif marker_side == "right":
        attach_x_mm = marker_origin.x - 4.0
        attach_y_mm = marker_origin.y + (marker_height_mm / 2.0)
        guide_points = [
            App.Vector(layout_sheet.source_length_mm, cut_step.position_mm, z_mm),
            App.Vector(layout_sheet.source_length_mm + elbow_gap_mm, cut_step.position_mm, z_mm),
            App.Vector(layout_sheet.source_length_mm + elbow_gap_mm, attach_y_mm, z_mm),
            App.Vector(attach_x_mm, attach_y_mm, z_mm),
        ]
    elif marker_side == "left":
        attach_x_mm = marker_origin.x + marker_width_mm + 4.0
        attach_y_mm = marker_origin.y + (marker_height_mm / 2.0)
        guide_points = [
            App.Vector(0.0, cut_step.position_mm, z_mm),
            App.Vector(-elbow_gap_mm, cut_step.position_mm, z_mm),
            App.Vector(-elbow_gap_mm, attach_y_mm, z_mm),
            App.Vector(attach_x_mm, attach_y_mm, z_mm),
        ]

    if not guide_points:
        return None

    simplified_points = []
    for point in guide_points:
        if simplified_points:
            last_point = simplified_points[-1]
            if (
                abs(point.x - last_point.x) <= DIMENSION_EQUALITY_TOLERANCE_MM
                and abs(point.y - last_point.y) <= DIMENSION_EQUALITY_TOLERANCE_MM
            ):
                continue
        simplified_points.append(point)
    if len(simplified_points) < 2:
        return None

    try:
        guide = doc.addObject("Part::Feature", object_name)
    except Exception:
        return None

    guide.Shape = Part.makePolygon(simplified_points)
    guide.Label = f"Guia {cut_step.step_index:02d} - {cut_step.cut_kind}"
    _mark_internal_object(guide, "layout_cut_annotation_guide")
    sheet_container.addObject(guide)
    _apply_cut_view_style(guide, (0.15, 0.32, 0.73))
    return guide


def _create_cut_annotation(
    doc,
    sheet_container,
    object_name,
    cut_step,
    marker_data,
    layout_sheet,
):
    marker_origin = marker_data.get("origin") if isinstance(marker_data, dict) else marker_data
    marker_side = marker_data.get("side") if isinstance(marker_data, dict) else ""
    marker_width_mm = marker_data.get("width_mm") if isinstance(marker_data, dict) else None
    marker_height_mm = marker_data.get("height_mm") if isinstance(marker_data, dict) else None
    marker_text = f"{cut_step.step_index:02d}"
    if marker_width_mm is None or marker_height_mm is None:
        marker_width_mm, marker_height_mm = _cut_marker_layout_size(marker_text)
    _create_cut_marker_guide(
        doc,
        sheet_container,
        f"{object_name}Guide",
        cut_step,
        marker_origin,
        marker_side,
        marker_width_mm,
        marker_height_mm,
        layout_sheet,
    )
    screen_marker = _create_screen_text_annotation(
        sheet_container,
        cut_step,
        marker_origin,
    )
    if screen_marker is not None:
        return screen_marker

    if Part is None:
        return None

    badge_shape = _cut_marker_plate_shape(marker_text, marker_origin)
    marker_shape = _cut_marker_shape(
        marker_text,
        marker_origin.add(
            App.Vector(
                CUT_MARKER_BADGE_PADDING_X_MM,
                CUT_MARKER_BADGE_PADDING_Y_MM,
                CUT_MARKER_PLATE_DEPTH_MM,
            )
        ),
    )
    if marker_shape is None:
        return None

    try:
        badge = doc.addObject("Part::Feature", f"{object_name}Badge")
        marker = doc.addObject("Part::Feature", object_name)
    except Exception:
        return None

    badge.Shape = badge_shape
    badge.Label = f"Etiqueta {cut_step.step_index:02d} - {cut_step.cut_kind}"
    _mark_internal_object(badge, "layout_cut_annotation_badge")
    sheet_container.addObject(badge)
    _apply_badge_view_style(badge)

    marker.Shape = marker_shape
    marker.Label = f"Passo {cut_step.step_index:02d} - {cut_step.cut_kind}"
    _mark_internal_object(marker, "layout_cut_annotation")
    sheet_container.addObject(marker)
    _apply_view_style(
        marker,
        fill_color=(0.07, 0.20, 0.52),
        line_color=(0.03, 0.12, 0.35),
        transparency=0,
    )
    return marker


def create_layout_model(parts, settings=None, root_name=LAYOUT_ROOT_NAME):
    doc = ensure_document()
    settings = settings or get_sheet_settings()
    layout_sheets = create_layout_sheets(parts, settings=settings)
    _cnc_origin_corner = str(getattr(settings, "cnc_origin_corner", "inferior_esquerdo") or "inferior_esquerdo")
    _apply_cnc_origin_corner(layout_sheets, _cnc_origin_corner)
    part_color_map = _build_layout_part_color_map(layout_sheets)

    existing_root = doc.getObject(root_name)
    if existing_root is not None:
        _remove_object_tree(doc, existing_root)

    # Compounds CAM ficam no topo do documento (fora do root) para aparecer
    # na lista do "New Job" do CAM. Limpar os órfãos da execução anterior.
    for _obj in list(doc.Objects):
        if str(getattr(_obj, INTERNAL_PROPERTY_NAME, "") or "") == "layout_cam_compound":
            try:
                doc.removeObject(_obj.Name)
            except Exception:
                pass

    root = doc.addObject("App::Part", root_name)
    root.Label = "Layout PanelNest"
    _mark_internal_object(root, "layout_root")

    _pending_sheet_textures = []  # lista de (sheet_box, png_str) para aplicar após recompute
    _diag_part_materials = {}  # debug: contagem de material por peça
    _diag_textures_applied = 0
    _diag_textures_failed = 0
    _pending_cam_compounds = []  # lista de (name, label, boxes, global_placement) para criar após recompute

    sheets_by_group = {}
    for layout_sheet in layout_sheets:
        sheets_by_group.setdefault(layout_sheet.group_id, []).append(layout_sheet)

    group_offset_y_mm = 0.0
    for group_index, (group_id, group_sheets) in enumerate(sheets_by_group.items(), start=1):
        first_sheet = group_sheets[0]
        group_container = doc.addObject("App::Part", f"PanelNestGrupoLayout{group_index:02d}")
        group_container.Label = (
            f"{group_id} | {first_sheet.material} | {_format_mm(first_sheet.thickness_mm)} mm | "
            f"{first_sheet.cut_method} | {first_sheet.layout_strategy} | "
            f"{_layout_mode_label(first_sheet.cut_method, settings, first_sheet.layout_strategy)}"
        )
        group_container.Placement.Base = App.Vector(0, group_offset_y_mm, 0)
        _mark_internal_object(group_container, "layout_group")
        root.addObject(group_container)

        sheet_offset_x_mm = 0.0
        for sheet_position, layout_sheet in enumerate(group_sheets, start=1):
            utilization_ratio = _layout_sheet_utilization_ratio(layout_sheet, settings)
            utilization_text = f"{_format_percent(utilization_ratio)}% ap."
            sheet_container = doc.addObject(
                "App::Part",
                f"PanelNestChapaLayout{group_index:02d}_{sheet_position:02d}",
            )
            sheet_container.Label = (
                f"{group_id} - {layout_sheet.source_label} "
                f"({_format_mm(layout_sheet.source_length_mm)} x {_format_mm(layout_sheet.source_width_mm)} x "
                f"{_format_thickness_label(layout_sheet.source_thickness_mm)}) | {utilization_text}"
            )
            sheet_container.Placement.Base = App.Vector(
                sheet_offset_x_mm,
                0,
                0,
            )
            _mark_internal_object(sheet_container, "layout_sheet_container")
            group_container.addObject(sheet_container)

            sheet_box = doc.addObject(
                "Part::Box",
                f"PanelNestBaseChapa{group_index:02d}_{sheet_position:02d}",
            )
            sheet_box.Label = (
                f"Base {group_id} - {layout_sheet.source_label} "
                f"({_format_mm(layout_sheet.source_length_mm)} x {_format_mm(layout_sheet.source_width_mm)} x "
                f"{_format_thickness_label(layout_sheet.source_thickness_mm)}) | {utilization_text}"
            )
            sheet_box.Length = layout_sheet.source_length_mm
            sheet_box.Width = layout_sheet.source_width_mm
            sheet_box.Height = max(layout_sheet.thickness_mm, 1.0)
            _mark_internal_object(sheet_box, "layout_sheet")
            sheet_container.addObject(sheet_box)
            # Cor da base da chapa baseada no material do grupo:
            #   - Material com textura (madeira): ShapeColor=branco (neutro em
            #     MODULATE) + textura PNG — mostra a textura natural.
            #   - Material sólido (MDF Preto, Branco, etc.): ShapeColor=cor
            #     do material, sem textura — a chapa aparece na cor real.
            #   - Sem material definido: cai no padrão cinza-azulado.
            try:
                from panelnest.materials import get_by_name as _get_mat_lm
                from panelnest.textures import texture_path as _tex_path
                from panelnest.textures import apply_texture_to_object as _lm_apply
            except ImportError:
                _get_mat_lm = None
                _tex_path = None
                _lm_apply = None
            _lm_mat = _get_mat_lm(layout_sheet.material) if (_get_mat_lm and layout_sheet.material) else None
            if _lm_mat is not None and _lm_mat.texture_id:
                sheet_fill = (1.0, 1.0, 1.0)
            elif _lm_mat is not None:
                sheet_fill = _lm_mat.color
            else:
                sheet_fill = _sheet_fill_color(layout_sheet)
            _apply_view_style(
                sheet_box,
                fill_color=sheet_fill,
                line_color=_darken_color(sheet_fill, 0.38),
                transparency=0,
            )
            if _lm_mat is not None and _lm_mat.texture_id and _tex_path and _lm_apply:
                try:
                    _lm_png = _tex_path(_lm_mat.texture_id)
                    if _lm_png is not None:
                        _png_str = str(_lm_png)
                        # Usa apply_texture_to_object (registra no
                        # _texture_registry). Isso permite reaplicar depois
                        # que as planilhas rodarem doc.recompute() e
                        # descartarem os nós Coin3D do ViewObject.
                        try:
                            _lm_apply(sheet_box, _png_str)
                        except Exception:
                            pass
                        _pending_sheet_textures.append((sheet_box, _png_str))
                except Exception:
                    pass

            sheet_placed_boxes = []
            sheet_box_holes = {}  # name do placed_box -> lista de objetos furo
            for part_index, placement in enumerate(layout_sheet.placements, start=1):
                _pname = f"PanelNestPecaLayout{group_index:02d}_{sheet_position:02d}_{part_index:02d}"
                _pthick = max(layout_sheet.thickness_mm, 1.0)
                _profile = _placed_profile_points(placement)
                placed_box = None

                # Tenta criar sólido extrudado do perfil real da peça
                if _profile and Part is not None:
                    try:
                        _vecs = [App.Vector(px, py, 0) for px, py in _profile]
                        _vecs.append(_vecs[0])  # fechar o wire
                        _wire = Part.makePolygon(_vecs)
                        _face = Part.Face(_wire)
                        _solid = _face.extrude(App.Vector(0, 0, _pthick))
                        placed_box = doc.addObject("Part::Feature", _pname)
                        placed_box.Shape = _solid
                        placed_box.Placement.Base = App.Vector(
                            placement.x_mm,
                            placement.y_mm,
                            _pthick,
                        )
                    except Exception:
                        placed_box = None

                # Fallback: Part::Box retangular
                if placed_box is None:
                    placed_box = doc.addObject("Part::Box", _pname)
                    placed_box.Length = placement.placed_length_mm
                    placed_box.Width = placement.placed_width_mm
                    placed_box.Height = _pthick
                    placed_box.Placement.Base = App.Vector(
                        placement.x_mm,
                        placement.y_mm,
                        _pthick,
                    )
                rotation_deg = placement_rotation_deg(placement)
                suffix = f" (rot. {rotation_deg}°)" if rotation_deg else ""
                placed_box.Label = f"{placement.part.part_id} - {PART_LABEL_PATTERN.sub('', placement.part.label)}{suffix}"
                _mark_internal_object(placed_box, "layout_part")
                sheet_container.addObject(placed_box)
                sheet_placed_boxes.append(placed_box)
                # Cor da peça: prefere a cor REAL do material; se não houver
                # material conhecido, usa a paleta visual antiga.
                _placed_mat = None
                try:
                    from panelnest.materials import get_by_name as _get_mat_pl
                    if placement.part.material:
                        _placed_mat = _get_mat_pl(placement.part.material)
                except Exception:
                    _placed_mat = None
                # Debug: registra material lido da peça
                _diag_key = (
                    str(placement.part.material or "<vazio>"),
                    "OK" if _placed_mat is not None else "DESCONHECIDO",
                    bool(_placed_mat and _placed_mat.texture_id),
                )
                _diag_part_materials[_diag_key] = _diag_part_materials.get(_diag_key, 0) + 1
                if _placed_mat is not None and _placed_mat.texture_id:
                    # Material com textura: ShapeColor branco (neutro em
                    # MODULATE), textura PNG mostra o veio real.
                    part_fill = (1.0, 1.0, 1.0)
                elif _placed_mat is not None:
                    part_fill = _placed_mat.color
                else:
                    part_fill = _part_fill_color(
                        placement.part,
                        color_map=part_color_map,
                        rotated=placement.rotated,
                    )
                _apply_view_style(
                    placed_box,
                    fill_color=part_fill,
                    line_color=_darken_color(part_fill, 0.44),
                    transparency=8,
                )
                # Aplica textura na peça do layout, espelhando o que é feito
                # na chapa-base. Rotação do veio = placement.rotated XOR
                # grain_rotated (peça girada na nesting + veio rotacionado).
                if _placed_mat is not None and _placed_mat.texture_id:
                    try:
                        from panelnest.textures import texture_path as _tex_path_pl
                        from panelnest.textures import apply_texture_to_object as _apply_tex_pl
                        _png_pl = _tex_path_pl(_placed_mat.texture_id)
                        if _png_pl is not None:
                            _png_pl_str = str(_png_pl)
                            try:
                                if _apply_tex_pl(placed_box, _png_pl_str):
                                    _diag_textures_applied += 1
                                else:
                                    _diag_textures_failed += 1
                            except Exception:
                                _diag_textures_failed += 1
                            _grain_rotated_part = bool(getattr(placement.part, "grain_rotated", False))
                            _final_rotated = bool(placement.rotated) ^ _grain_rotated_part
                            try:
                                if not hasattr(placed_box, "PanelNestGrainRotated"):
                                    placed_box.addProperty(
                                        "App::PropertyBool",
                                        "PanelNestGrainRotated",
                                        "PanelNest",
                                        "Veio rotacionado 90 graus",
                                    )
                                placed_box.PanelNestGrainRotated = _final_rotated
                                # Reaplica textura agora que a propriedade
                                # de rotação está setada (a primeira chamada
                                # leu False antes da prop existir).
                                if _final_rotated:
                                    from panelnest.textures import _apply_coin_texture as _act_pl
                                    try:
                                        if placed_box.ViewObject is not None:
                                            _act_pl(placed_box.ViewObject, _png_pl_str, rotated=True)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            _pending_sheet_textures.append((placed_box, _png_pl_str))
                    except Exception:
                        pass

                # Contorno real para peças não-retangulares (CNC)
                _try_add_shape_contour(
                    doc, sheet_container, placement, layout_sheet,
                    group_index, sheet_position, part_index,
                    _darken_color(part_fill, 0.55),
                )

                annotation_text_color = (0.19, 0.13, 0.12)
                if _color_luminance(part_fill) < 0.63:
                    annotation_text_color = (0.98, 0.96, 0.93)
                annotation_z_mm = max(layout_sheet.thickness_mm, 1.0) * 2.0 + 0.8
                label_spec = _part_label_annotation_spec(placement, settings, annotation_z_mm)
                if label_spec is not None:
                    _create_part_text_annotation(
                        sheet_container,
                        f"PanelNestPecaLabel{group_index:02d}_{sheet_position:02d}_{part_index:02d}",
                        label_spec,
                        f"{placement.part.part_id} | etiqueta da peca",
                        "layout_part_annotation",
                        text_color=annotation_text_color,
                    )
                dimension_specs = _part_dimension_annotation_specs(placement, settings, annotation_z_mm)
                for dimension_index, dimension_spec in enumerate(dimension_specs, start=1):
                    axis_label = str(dimension_spec.get("axis", dimension_index)).upper()
                    _create_part_text_annotation(
                        sheet_container,
                        (
                            f"PanelNestPecaDim{group_index:02d}_{sheet_position:02d}_"
                            f"{part_index:02d}_{axis_label}"
                        ),
                        dimension_spec,
                        f"{placement.part.part_id} | medida {axis_label} da peca",
                        "layout_part_annotation_dimension",
                        text_color=annotation_text_color,
                    )

                edge_band_z_mm = max(layout_sheet.thickness_mm, 1.0) * 2.0 + 0.15
                for edge_index, edge_spec in enumerate(
                    _layout_edge_band_marker_specs(placement, settings, z_mm=edge_band_z_mm),
                    start=1,
                ):
                    edge_band_marker = doc.addObject(
                        "Part::Box",
                        (
                            f"PanelNestFitaLayout{group_index:02d}_{sheet_position:02d}_"
                            f"{part_index:02d}_{edge_index:02d}"
                        ),
                    )
                    edge_band_marker.Length = edge_spec["length_mm"]
                    edge_band_marker.Width = edge_spec["width_mm"]
                    edge_band_marker.Height = LAYOUT_EDGE_BAND_MARKER_HEIGHT_MM
                    edge_band_marker.Placement.Base = App.Vector(
                        edge_spec["x_mm"],
                        edge_spec["y_mm"],
                        edge_spec["z_mm"],
                    )
                    edge_band_marker.Label = (
                        f"{placement.part.part_id} | fita {edge_spec['source_side_label']}"
                    )
                    _mark_internal_object(edge_band_marker, "layout_part_edge_band")
                    sheet_container.addObject(edge_band_marker)
                    _apply_layout_edge_band_style(edge_band_marker)
                    edge_label_spec = _layout_edge_band_label_spec(
                        edge_spec,
                        edge_band_z_mm + LAYOUT_EDGE_BAND_MARKER_HEIGHT_MM + 0.12,
                    )
                    if edge_label_spec is not None:
                        _create_part_text_annotation(
                            sheet_container,
                            (
                                f"PanelNestFitaLabel{group_index:02d}_{sheet_position:02d}_"
                                f"{part_index:02d}_{edge_index:02d}"
                            ),
                            edge_label_spec,
                            (
                                f"{placement.part.part_id} | fita "
                                f"{edge_spec['source_side_abbreviation']}"
                            ),
                            "layout_part_edge_band_label",
                            text_color=(0.10, 0.24, 0.27),
                        )

                # Furos no layout 3D
                if Part is not None:
                    holes = getattr(placement.part, "holes", None) or []
                    part_z_top = max(layout_sheet.thickness_mm, 1.0) * 2.0
                    for hole_index, hole in enumerate(holes, start=1):
                        radius_mm = float(hole.get("diameter_mm", 0)) / 2.0
                        depth_mm = float(hole.get("depth_mm", 0)) or max(layout_sheet.thickness_mm, 1.0)
                        if radius_mm <= 0:
                            continue
                        hx_src = float(hole.get("x_mm", 0))
                        hy_src = float(hole.get("y_mm", 0))
                        local_x, local_y = transform_placement_point(
                            placement,
                            hx_src,
                            hy_src,
                        )
                        hx = placement.x_mm + local_x
                        hy = placement.y_mm + local_y
                        try:
                            hole_obj = doc.addObject(
                                "Part::Feature",
                                (
                                    f"PanelNestFuroLayout{group_index:02d}_{sheet_position:02d}_"
                                    f"{part_index:02d}_{hole_index:02d}"
                                ),
                            )
                            hole_obj.Shape = Part.makeCylinder(
                                radius_mm,
                                depth_mm,
                                App.Vector(hx, hy, part_z_top),
                                App.Vector(0, 0, -1),
                            )
                            hole_obj.Label = (
                                f"{placement.part.part_id} | furo D{hole.get('diameter_mm', 0):.1f}"
                            )
                            _mark_internal_object(hole_obj, "layout_part_hole")
                            sheet_container.addObject(hole_obj)
                            sheet_box_holes.setdefault(placed_box.Name, []).append(hole_obj)
                            _apply_view_style(
                                hole_obj,
                                fill_color=(0.15, 0.15, 0.55),
                                line_color=(0.05, 0.05, 0.40),
                                transparency=30,
                            )
                        except Exception:
                            pass

            if Part is not None and layout_sheet.cut_steps:
                cut_z_mm = max(layout_sheet.thickness_mm, 1.0) * 2.0 + 0.5
                cut_marker_positions = _build_cut_marker_positions(
                    layout_sheet,
                    settings,
                    cut_z_mm + 1.0,
                )
                for cut_index, cut_step in enumerate(layout_sheet.cut_steps, start=1):
                    cut_feature = doc.addObject(
                        "Part::Feature",
                        f"PanelNestCorteLayout{group_index:02d}_{sheet_position:02d}_{cut_index:02d}",
                    )
                    cut_feature.Label = (
                        f"Corte {cut_step.step_index:02d} - {cut_step.cut_kind} | {cut_step.description}"
                    )
                    cut_feature.Shape = Part.makeLine(
                        App.Vector(cut_step.start_x_mm, cut_step.start_y_mm, cut_z_mm),
                        App.Vector(cut_step.end_x_mm, cut_step.end_y_mm, cut_z_mm),
                    )
                    _mark_internal_object(cut_feature, "layout_cut")
                    sheet_container.addObject(cut_feature)
                    _apply_cut_view_style(
                        cut_feature,
                        _cut_line_color(cut_step),
                        line_width=_cut_line_width(cut_step),
                        draw_style=_cut_draw_style(cut_step),
                    )
                    _create_cut_annotation(
                        doc,
                        sheet_container,
                        f"PanelNestCorteLabel{group_index:02d}_{sheet_position:02d}_{cut_index:02d}",
                        cut_step,
                        cut_marker_positions.get(
                            cut_step.step_index,
                            {
                                "origin": App.Vector(cut_step.start_x_mm, cut_step.start_y_mm, cut_z_mm + 1.0),
                                "side": "",
                            },
                        ),
                        layout_sheet,
                    )

            # Guarda dados para criar o sólido CAM depois do recompute (ver
            # bloco no fim da função). O Shape dos placed_box só existe após
            # recompute, e queremos um Part::Feature independente (não um
            # Part::Compound) para não "consumir" visualmente os filhos.
            # As shapes são copiadas com z corrigido: no layout as peças ficam
            # em z=espessura (visualmente sobre a chapa base), mas no CAM
            # compound subtraímos esse offset para que a face superior das
            # peças fique em z=espessura e a face inferior em z=0.
            if sheet_placed_boxes:
                length_int = int(round(layout_sheet.source_length_mm))
                width_int = int(round(layout_sheet.source_width_mm))
                thickness_label = _format_thickness_label(layout_sheet.source_thickness_mm)
                material_label = layout_sheet.material or "material ?"
                _pending_cam_compounds.append((
                    f"PanelNestCAMChapa{group_index:02d}_{sheet_position:02d}",
                    (
                        f"CAM Chapa {group_index:02d}.{sheet_position:02d} - "
                        f"{material_label} {thickness_label} "
                        f"[{length_int}x{width_int}]"
                    ),
                    list(sheet_placed_boxes),
                    float(layout_sheet.source_length_mm),
                    float(layout_sheet.source_width_mm),
                    float(max(layout_sheet.thickness_mm, 1.0)),
                    dict(sheet_box_holes),
                ))

            sheet_offset_x_mm += layout_sheet.source_length_mm + LAYOUT_SHEET_GAP_MM

        group_offset_y_mm += max(
            (layout_sheet.source_width_mm for layout_sheet in group_sheets),
            default=settings.width_mm,
        ) + LAYOUT_GROUP_GAP_MM

    doc.recompute()

    # Criar um Part::Feature por chapa no topo do documento, com Shape
    # independente (cópia das peças em coords LOCAIS da chapa), para
    # aparecer individualmente na lista "New Job" do CAM. Oculto por padrão.
    # Coords locais = cada Chapa começa em (0,0,0), assim o Job do CAM já
    # nasce com zero-peça no canto da chapa — não importa em que offset
    # visual ela está no layout.
    if Part is not None and _pending_cam_compounds:
        # Translada o compound inteiro para que o canto escolhido da chapa
        # fique em (0,0,0) do CAM, mantendo a orientação visual igual à do
        # FreeCAD (sem espelhar). Peças podem ficar em quadrantes negativos
        # — o Job do CAM aceita, e o zero-peça corresponde ao canto físico
        # onde a CNC prende a chapa.
        _cam_dx = -1.0 if _cnc_origin_corner in ("inferior_direito", "superior_direito") else 0.0
        _cam_dy = -1.0 if _cnc_origin_corner in ("superior_esquerdo", "superior_direito") else 0.0
        for _cam_name, _cam_label, _cam_boxes, _cam_L, _cam_W, _cam_thickness, _cam_holes_map in _pending_cam_compounds:
            try:
                child_shapes = []
                for _box in _cam_boxes:
                    shape = getattr(_box, "Shape", None)
                    if shape is None or shape.isNull():
                        continue
                    shape_copy = shape.copy()
                    # Subtrai os furos desta peça para o CAM ver os pockets/drills reais
                    for _hole_obj in _cam_holes_map.get(_box.Name, []):
                        _hole_shape = getattr(_hole_obj, "Shape", None)
                        if _hole_shape is None or _hole_shape.isNull():
                            continue
                        try:
                            shape_copy = shape_copy.cut(_hole_shape.copy())
                        except Exception:
                            pass
                    pl = shape_copy.Placement
                    pl.Base = App.Vector(
                        pl.Base.x + _cam_dx * _cam_L,
                        pl.Base.y + _cam_dy * _cam_W,
                        pl.Base.z - _cam_thickness,
                    )
                    shape_copy.Placement = pl
                    child_shapes.append(shape_copy)
                if not child_shapes:
                    continue
                cam_feature = doc.addObject("Part::Feature", _cam_name)
                cam_feature.Shape = Part.makeCompound(child_shapes)
                cam_feature.Label = _cam_label
                _mark_internal_object(cam_feature, "layout_cam_compound")
                try:
                    if cam_feature.ViewObject is not None:
                        cam_feature.ViewObject.Visibility = False
                except Exception:
                    pass
                # Agrupa todos os CAMs gerados num único container para
                # organização visual da tree.
                try:
                    _cam_group_name = "PanelNest_CAM_Chapas"
                    _cam_group = doc.getObject(_cam_group_name)
                    if _cam_group is None or getattr(_cam_group, "TypeId", "") != "App::DocumentObjectGroup":
                        _cam_group = doc.addObject("App::DocumentObjectGroup", _cam_group_name)
                        try:
                            _cam_group.Label = "PanelNest — CAM Chapas"
                        except Exception:
                            pass
                    if cam_feature not in list(getattr(_cam_group, "Group", [])):
                        _cam_group.addObject(cam_feature)
                except Exception:
                    pass
            except Exception:
                pass
        doc.recompute()

    # Reaplicar texturas nas chapas base APÓS recompute (recompute pode
    # invalidar/recriar nós Coin3D). Aqui reforçamos para TODAS as chapas —
    # não só a primeira. Entre aplicações forçamos processEvents() para
    # garantir que cada ViewObject.RootNode esteja pronto antes de inserir.
    try:
        from panelnest.textures import _apply_coin_texture as _act
        try:
            from PySide import QtCore as _QtCore, QtGui as _QtGui
            _QApp = _QtGui.QApplication
        except ImportError:
            try:
                from PySide2 import QtCore as _QtCore
                from PySide2.QtWidgets import QApplication as _QApp
            except ImportError:
                _QApp = None
        for _sb, _png in _pending_sheet_textures:
            try:
                if _QApp is not None:
                    _QApp.processEvents()
                _act(_sb.ViewObject, _png)
            except Exception:
                pass
    except Exception:
        pass

    # Reaplica TODAS as texturas registradas em _texture_registry — pega as
    # peças do modelo original (cabinet) que tiveram textura aplicada via
    # apply_texture_to_object e que podem ter perdido o nó Coin3D nos
    # recomputes disparados aqui.
    try:
        from panelnest.textures import reapply_registered_textures as _reapply
        _reapplied = _reapply(doc)
    except Exception:
        _reapplied = -1

    # Diagnóstico de textura no layout
    try:
        App.Console.PrintMessage(
            f"PanelNest DIAG: textura - aplicadas {_diag_textures_applied}, "
            f"falhas {_diag_textures_failed}, pendentes {len(_pending_sheet_textures)}, "
            f"reapplied {_reapplied}.\n"
        )
        for (mat_name, status, has_tex), count in _diag_part_materials.items():
            App.Console.PrintMessage(
                f"PanelNest DIAG: peca material='{mat_name}' status={status} "
                f"tem_textura={has_tex} -> {count} peca(s).\n"
            )
        # Listar objetos do documento que têm PanelNestMaterial setada
        try:
            _objs_with_mat = []
            for _o in doc.Objects:
                _v = getattr(_o, "PanelNestMaterial", None)
                if _v:
                    _objs_with_mat.append(f"{_o.Name}({getattr(_o,'TypeId','?')})={_v}")
            App.Console.PrintMessage(
                f"PanelNest DIAG: objetos com PanelNestMaterial setada ({len(_objs_with_mat)}): "
                f"{', '.join(_objs_with_mat) if _objs_with_mat else '<nenhum>'}\n"
            )
        except Exception:
            pass
        # Listar primeiros 5 candidatos do layout: nome, TypeId, valor de PanelNestMaterial
        try:
            from panelnest.geometry import get_part_source_objects
            _cands = get_part_source_objects(None)[:8]
            for _c in _cands:
                _val = getattr(_c, "PanelNestMaterial", "<sem-prop>") if hasattr(_c, "PanelNestMaterial") else "<sem-prop>"
                _parents = ",".join(
                    f"{p.Name}({getattr(p,'TypeId','?')})"
                    for p in (getattr(_c, "InList", []) or [])[:3]
                )
                App.Console.PrintMessage(
                    f"PanelNest DIAG: candidato {_c.Name}({getattr(_c,'TypeId','?')}) "
                    f"PanelNestMaterial='{_val}' InList=[{_parents}]\n"
                )
        except Exception as _e:
            App.Console.PrintMessage(f"PanelNest DIAG: erro listando candidatos: {_e}\n")
    except Exception:
        pass

    return layout_sheets, root
