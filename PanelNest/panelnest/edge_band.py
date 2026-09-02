import json

try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

from .constants import (
    EDGE_BAND_SIDE_LABELS,
    EDGE_BAND_SIDE_KEYS,
    EDGE_BAND_SIDE_ABBREVIATIONS,
    EDGE_BAND_HIGHLIGHT_COLOR,
    DIMENSION_EQUALITY_TOLERANCE_MM,
    PART_LABEL_PATTERN,
)
from .freecad_utils import _has_property, ensure_document
from .geometry import (
    _shape_local_axis_extents,
    _ordered_local_axes,
    _solid_contexts_for_shape,
    _solid_context_for_face,
    _face_side_key_from_context,
    _object_dimensions_and_quantity,
    _is_candidate_object,
    get_part_source_objects,
    _vector_axis_value,
    _face_normal_vector,
    _is_planar_face,
    _dominant_axis_name,
    _transform_vector_to_local,
    _transform_point_to_local,
    _face_index_from_name,
    _candidate_object_from_selection_object,
    _get_gui_selection_ex,
    _face_touches_axis_boundary,
    _shape_axis_extents_in_local_coordinates,
    _bbox_axis_center_value,
    _face_matches_candidate,
)


def _clamp_color_component(value):
    return max(0.0, min(1.0, float(value)))


def _lazy_ensure_part_properties(obj):
    from .parts import ensure_part_properties
    return ensure_part_properties(obj)


def _lazy_base_label_for_object(obj):
    from .parts import _base_label_for_object
    return _base_label_for_object(obj)


def _lazy_collect_parts(objects=None):
    from .parts import collect_parts
    return collect_parts(objects)

def _edge_band_flags(obj):
    return (
        bool(getattr(obj, "PanelNestEdgeBandTop", False)),
        bool(getattr(obj, "PanelNestEdgeBandBottom", False)),
        bool(getattr(obj, "PanelNestEdgeBandLeft", False)),
        bool(getattr(obj, "PanelNestEdgeBandRight", False)),
    )


def _edge_band_summary(part):
    edges = []
    if part.edge_band_top:
        edges.append(EDGE_BAND_SIDE_LABELS["top"])
    if part.edge_band_bottom:
        edges.append(EDGE_BAND_SIDE_LABELS["bottom"])
    if part.edge_band_left:
        edges.append(EDGE_BAND_SIDE_LABELS["left"])
    if part.edge_band_right:
        edges.append(EDGE_BAND_SIDE_LABELS["right"])
    return ", ".join(edges)


def _edge_band_abbreviation_summary(part):
    edges = []
    if part.edge_band_top:
        edges.append(EDGE_BAND_SIDE_ABBREVIATIONS["top"])
    if part.edge_band_bottom:
        edges.append(EDGE_BAND_SIDE_ABBREVIATIONS["bottom"])
    if part.edge_band_left:
        edges.append(EDGE_BAND_SIDE_ABBREVIATIONS["left"])
    if part.edge_band_right:
        edges.append(EDGE_BAND_SIDE_ABBREVIATIONS["right"])
    return ", ".join(edges)


def _edge_band_side_property_name(side_key):
    side_map = {
        "top": "PanelNestEdgeBandTop",
        "bottom": "PanelNestEdgeBandBottom",
        "left": "PanelNestEdgeBandLeft",
        "right": "PanelNestEdgeBandRight",
    }
    return side_map.get(side_key, "")


def _edge_band_flags_dict_from_object(obj):
    return {
        "top": bool(getattr(obj, "PanelNestEdgeBandTop", False)),
        "bottom": bool(getattr(obj, "PanelNestEdgeBandBottom", False)),
        "left": bool(getattr(obj, "PanelNestEdgeBandLeft", False)),
        "right": bool(getattr(obj, "PanelNestEdgeBandRight", False)),
    }


def _normalize_occurrence_edge_band_flags(payload):
    if not isinstance(payload, dict):
        payload = {}
    return {side_key: bool(payload.get(side_key, False)) for side_key in EDGE_BAND_SIDE_KEYS}


def _deserialize_occurrence_edge_band_map(text, occurrence_count):
    if occurrence_count <= 1:
        return {}
    try:
        raw_payload = json.loads(str(text or "").strip())
    except Exception:
        raw_payload = {}
    if not isinstance(raw_payload, dict):
        return {}

    normalized = {}
    for raw_key, raw_flags in raw_payload.items():
        try:
            occurrence_index = int(raw_key)
        except Exception:
            continue
        if occurrence_index < 1 or occurrence_index > occurrence_count:
            continue
        normalized_flags = _normalize_occurrence_edge_band_flags(raw_flags)
        if any(normalized_flags.values()):
            normalized[occurrence_index] = normalized_flags
    return normalized


def _serialize_occurrence_edge_band_map(payload):
    cleaned = {}
    for raw_key, raw_flags in dict(payload or {}).items():
        try:
            occurrence_index = int(raw_key)
        except Exception:
            continue
        normalized_flags = _normalize_occurrence_edge_band_flags(raw_flags)
        if any(normalized_flags.values()):
            cleaned[str(occurrence_index)] = normalized_flags
    if not cleaned:
        return ""
    return json.dumps(cleaned, ensure_ascii=True, sort_keys=True)


def _object_occurrence_edge_band_map(obj, occurrence_count=None):
    if occurrence_count is None:
        try:
            _length_mm, _width_mm, _thickness_mm, occurrence_count = _object_dimensions_and_quantity(obj)
        except Exception:
            occurrence_count = 1
    raw_text = getattr(obj, "PanelNestEdgeBandOccurrencesJson", "") or ""
    return _deserialize_occurrence_edge_band_map(raw_text, occurrence_count)


def _set_object_occurrence_edge_band_map(obj, payload):
    if _has_property(obj, "PanelNestEdgeBandOccurrencesJson"):
        obj.PanelNestEdgeBandOccurrencesJson = _serialize_occurrence_edge_band_map(payload)


def _part_edge_band_flags_dict(part):
    return {
        "top": bool(getattr(part, "edge_band_top", False)),
        "bottom": bool(getattr(part, "edge_band_bottom", False)),
        "left": bool(getattr(part, "edge_band_left", False)),
        "right": bool(getattr(part, "edge_band_right", False)),
    }


def _allow_rotation_summary(part):
    return "Sim" if part.allow_rotation else "Nao"


def _part_is_square(part):
    return abs(part.length_mm - part.width_mm) <= DIMENSION_EQUALITY_TOLERANCE_MM



def resolve_edge_band_face_selection():
    selection_items = _get_gui_selection_ex()
    if not selection_items:
        raise ValueError(
            "Selecione uma ou mais faces laterais das pecas antes de aplicar fita por face."
        )

    resolved_items = []
    whole_objects = []
    rejected_messages = []
    seen_keys = set()

    for selection_item in selection_items:
        raw_obj = getattr(selection_item, "Object", None)
        if raw_obj is None:
            continue
        obj = _candidate_object_from_selection_object(raw_obj)
        if not _is_candidate_object(obj):
            rejected_messages.append(
                f"{getattr(raw_obj, 'Label', getattr(raw_obj, 'Name', 'objeto'))}: objeto nao elegivel."
            )
            continue

        sub_objects = list(getattr(selection_item, "SubObjects", []) or [])
        sub_names = list(getattr(selection_item, "SubElementNames", []) or [])

        shape = getattr(obj, "Shape", None)
        solid_contexts = _solid_contexts_for_shape(shape)
        if shape is None or shape.isNull() or not solid_contexts:
            rejected_messages.append(
                f"{getattr(obj, 'Label', getattr(obj, 'Name', 'objeto'))}: nao foi possivel identificar comprimento, largura e espessura."
            )
            continue

        _lazy_ensure_part_properties(obj)
        base_label = _lazy_base_label_for_object(obj)
        panelnest_id = str(getattr(obj, "PanelNestId", "") or "")
        part_label = f"{panelnest_id} - {base_label}" if panelnest_id else base_label
        try:
            length_mm, width_mm, _thickness_mm, _quantity = _object_dimensions_and_quantity(obj)
        except Exception:
            length_mm = 0.0
            width_mm = 0.0

        # Objeto selecionado sem faces → modo seleção interativa no preview
        if not sub_objects:
            whole_objects.append(
                {
                    "object_name": obj.Name,
                    "object_label": getattr(obj, "Label", obj.Name),
                    "part_id": panelnest_id,
                    "part_label": part_label,
                    "length_mm": length_mm,
                    "width_mm": width_mm,
                    "occurrence_count": len(solid_contexts),
                }
            )
            continue

        for index, sub_object in enumerate(sub_objects):
            face_name = sub_names[index] if index < len(sub_names) else f"Face{index + 1}"
            if not face_name.startswith("Face"):
                continue
            face_index = _face_index_from_name(face_name)
            local_face = None
            if face_index is not None and 0 <= face_index < len(getattr(shape, "Faces", []) or []):
                local_face = shape.Faces[face_index]
            if local_face is None:
                local_face = sub_object

            if not _is_planar_face(local_face):
                rejected_messages.append(f"{part_label}: {face_name} nao e plana.")
                continue

            try:
                solid_context = _solid_context_for_face(local_face, solid_contexts)
                occurrence_index = int(solid_context.get("solid_index", 0)) + 1
                occurrence_count = len(solid_contexts)
                side_key = _face_side_key_from_context(
                    local_face,
                    solid_context["axis_extents"],
                    solid_context["length_axis"],
                    solid_context["width_axis"],
                    solid_context["thickness_axis"],
                )
            except Exception as exc:
                rejected_messages.append(f"{part_label}: {face_name} invalida ({exc}).")
                continue

            if not side_key:
                rejected_messages.append(
                    f"{part_label}: {face_name} e uma face grande do painel; selecione uma face lateral."
                )
                continue

            dedupe_key = (obj.Name, occurrence_index, side_key)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            occurrence_label = (
                f"{part_label} [{occurrence_index:02d}/{occurrence_count:02d}]"
                if occurrence_count > 1
                else part_label
            )
            resolved_items.append(
                {
                    "object_name": obj.Name,
                    "object_label": getattr(obj, "Label", obj.Name),
                    "part_id": panelnest_id,
                    "part_label": occurrence_label,
                    "face_name": face_name,
                    "length_mm": length_mm,
                    "width_mm": width_mm,
                    "occurrence_index": occurrence_index,
                    "occurrence_count": occurrence_count,
                    "side_key": side_key,
                    "side_label": EDGE_BAND_SIDE_LABELS[side_key],
                }
            )

    if not resolved_items and not whole_objects:
        details = " ".join(rejected_messages[:3]).strip()
        if details:
            raise ValueError(details)
        raise ValueError(
            "Nenhuma face lateral valida foi encontrada. Selecione faces laterais planas das pecas."
        )

    return {
        "items": resolved_items,
        "whole_objects": whole_objects,
        "rejected_messages": rejected_messages,
    }


def apply_edge_band_face_selection(selection_items, operation="mark"):
    doc = ensure_document()
    normalized_operation = str(operation or "mark").strip().lower()
    if normalized_operation not in {"mark", "remove", "toggle"}:
        raise ValueError("Operacao de fita invalida.")

    updated_objects = []
    seen_objects = set()

    for item in selection_items:
        object_name = str(item.get("object_name", "") or "")
        side_key = str(item.get("side_key", "") or "")
        occurrence_index = int(item.get("occurrence_index", 0) or 0)
        property_name = _edge_band_side_property_name(side_key)
        if not object_name or not property_name:
            continue

        obj = doc.getObject(object_name)
        if obj is None:
            continue

        _lazy_ensure_part_properties(obj)
        _length_mm, _width_mm, _thickness_mm, occurrence_count = _object_dimensions_and_quantity(obj)
        if occurrence_count > 1 and occurrence_index > 0:
            object_level_flags = _edge_band_flags_dict_from_object(obj)
            occurrence_map = _object_occurrence_edge_band_map(obj, occurrence_count)
            if not occurrence_map and any(object_level_flags.values()):
                for current_occurrence_index in range(1, occurrence_count + 1):
                    occurrence_map[current_occurrence_index] = dict(object_level_flags)
                for current_side_key in EDGE_BAND_SIDE_KEYS:
                    property_name_for_side = _edge_band_side_property_name(current_side_key)
                    if property_name_for_side:
                        setattr(obj, property_name_for_side, False)
            current_flags = occurrence_map.get(occurrence_index, _edge_band_flags_dict_from_object(obj))
            current_flags = dict(current_flags)
            current_value = bool(current_flags.get(side_key, False))
            if normalized_operation == "mark":
                current_flags[side_key] = True
            elif normalized_operation == "remove":
                current_flags[side_key] = False
            else:
                current_flags[side_key] = not current_value

            if any(current_flags.values()):
                occurrence_map[occurrence_index] = _normalize_occurrence_edge_band_flags(current_flags)
            else:
                occurrence_map.pop(occurrence_index, None)
            _set_object_occurrence_edge_band_map(obj, occurrence_map)
        else:
            current_value = bool(getattr(obj, property_name, False))
            if normalized_operation == "mark":
                new_value = True
            elif normalized_operation == "remove":
                new_value = False
            else:
                new_value = not current_value
            setattr(obj, property_name, new_value)

        if obj.Name not in seen_objects:
            seen_objects.add(obj.Name)
            updated_objects.append(obj)

    if not updated_objects:
        raise ValueError("Nenhuma peca valida foi atualizada pela selecao atual.")

    for obj in updated_objects:
        length_mm, width_mm, thickness_mm, _quantity = _object_dimensions_and_quantity(obj)
        obj.PanelNestLengthMm = length_mm
        obj.PanelNestWidthMm = width_mm
        obj.PanelNestThicknessMm = thickness_mm
        if not getattr(obj, "PanelNestBaseLabel", ""):
            obj.PanelNestBaseLabel = PART_LABEL_PATTERN.sub("", getattr(obj, "Label", obj.Name))

    doc.recompute()
    refresh_part_edge_band_visuals(updated_objects)
    return _lazy_collect_parts(updated_objects)


def _object_primary_axes_data(obj):
    shape, axis_extents, inverse_placement = _shape_axis_extents_in_local_coordinates(obj)
    ordered_axes = _ordered_local_axes(axis_extents)
    return shape, axis_extents, inverse_placement, ordered_axes[0], ordered_axes[1], ordered_axes[2]


def _shape_local_axis_extents(shape):
    bbox = getattr(shape, "BoundBox", None)
    if bbox is None:
        raise ValueError("Shape invalido para resolver as faces da peca.")
    return {
        "x": {
            "min": float(getattr(bbox, "XMin", 0.0)),
            "max": float(getattr(bbox, "XMax", 0.0)),
            "center": _bbox_axis_center_value(bbox, "x"),
            "length": float(getattr(bbox, "XLength", 0.0)),
        },
        "y": {
            "min": float(getattr(bbox, "YMin", 0.0)),
            "max": float(getattr(bbox, "YMax", 0.0)),
            "center": _bbox_axis_center_value(bbox, "y"),
            "length": float(getattr(bbox, "YLength", 0.0)),
        },
        "z": {
            "min": float(getattr(bbox, "ZMin", 0.0)),
            "max": float(getattr(bbox, "ZMax", 0.0)),
            "center": _bbox_axis_center_value(bbox, "z"),
            "length": float(getattr(bbox, "ZLength", 0.0)),
        },
    }


def _face_axis_center_value(face, axis_name):
    bbox = getattr(face, "BoundBox", None)
    if bbox is None:
        face_center = getattr(face, "CenterOfMass", None)
        if face_center is None:
            raise ValueError("A face selecionada nao expoe centro geometrico.")
        return _vector_axis_value(face_center, axis_name)
    return _bbox_axis_center_value(bbox, axis_name)



def _face_side_key_for_object(face, axis_extents, inverse_placement, length_axis, width_axis, thickness_axis):
    if not _is_planar_face(face):
        return ""

    local_normal = _transform_vector_to_local(_face_normal_vector(face), inverse_placement)
    dominant_axis = _dominant_axis_name(local_normal)
    if dominant_axis == thickness_axis:
        return ""

    face_center = getattr(face, "CenterOfMass", None)
    if face_center is None:
        return ""

    local_face_center = _transform_point_to_local(face_center, inverse_placement)
    face_axis_value = _vector_axis_value(local_face_center, dominant_axis)
    object_axis_center = axis_extents[dominant_axis]["center"]

    if dominant_axis == width_axis:
        return "top" if face_axis_value >= object_axis_center else "bottom"
    if dominant_axis == length_axis:
        return "right" if face_axis_value >= object_axis_center else "left"
    return ""


def _part_edge_band_occurrence_face_index_map(obj):
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return shape, {}

    solid_contexts = _solid_contexts_for_shape(shape)
    face_map = {}

    for face_index, face in enumerate(getattr(shape, "Faces", []) or []):
        try:
            solid_context = _solid_context_for_face(face, solid_contexts)
            if solid_context is None:
                continue
            occurrence_index = int(solid_context.get("solid_index", 0)) + 1
            side_key = _face_side_key_from_context(
                face,
                solid_context["axis_extents"],
                solid_context["length_axis"],
                solid_context["width_axis"],
                solid_context["thickness_axis"],
            )
        except Exception:
            side_key = ""
        if side_key:
            occurrence_map = face_map.setdefault(
                occurrence_index,
                {current_side_key: [] for current_side_key in EDGE_BAND_SIDE_KEYS},
            )
            occurrence_map[side_key].append(face_index)

    return shape, face_map


def _normalize_color_triplet(value, fallback=(0.80, 0.80, 0.80)):
    fallback_triplet = tuple(float(component) for component in fallback[:3])
    try:
        raw_values = list(value)
    except Exception:
        raw_values = []

    if len(raw_values) < 3:
        raw_values = list(fallback_triplet)

    return tuple(_clamp_color_component(raw_values[index]) for index in range(3))


def _serialize_color_triplet(color):
    return json.dumps(list(_normalize_color_triplet(color)), ensure_ascii=True)


def _deserialize_color_triplet(text, fallback=(0.80, 0.80, 0.80)):
    fallback_triplet = _normalize_color_triplet(fallback)
    try:
        raw_value = json.loads(str(text or "").strip())
    except Exception:
        raw_value = None
    return _normalize_color_triplet(raw_value, fallback_triplet)


def _serialize_color_list(colors):
    payload = [list(_normalize_color_triplet(color)) for color in list(colors or [])]
    return json.dumps(payload, ensure_ascii=True)


def _deserialize_color_list(text, face_count, fallback_color=(0.80, 0.80, 0.80)):
    normalized_fallback = _normalize_color_triplet(fallback_color)
    if face_count <= 0:
        return []

    try:
        raw_values = json.loads(str(text or "").strip())
    except Exception:
        raw_values = []

    if not isinstance(raw_values, list):
        raw_values = []

    colors = [_normalize_color_triplet(value, normalized_fallback) for value in raw_values[:face_count]]
    if not colors:
        colors = [normalized_fallback]
    if len(colors) < face_count:
        colors.extend([colors[-1]] * (face_count - len(colors)))
    return colors[:face_count]


def _current_view_shape_color(view_object):
    if view_object is None:
        return _normalize_color_triplet(None)
    return _normalize_color_triplet(getattr(view_object, "ShapeColor", None))


def _current_view_diffuse_colors(view_object, face_count, fallback_color):
    normalized_fallback = _normalize_color_triplet(fallback_color)
    if face_count <= 0:
        return []
    if view_object is None or not hasattr(view_object, "DiffuseColor"):
        return [normalized_fallback] * face_count

    try:
        raw_diffuse = list(getattr(view_object, "DiffuseColor"))
    except Exception:
        raw_diffuse = []

    if raw_diffuse and all(isinstance(component, (int, float)) for component in raw_diffuse[:3]):
        return [_normalize_color_triplet(raw_diffuse, normalized_fallback)] * face_count

    colors = [_normalize_color_triplet(value, normalized_fallback) for value in raw_diffuse[:face_count]]
    if not colors:
        colors = [normalized_fallback]
    if len(colors) < face_count:
        colors.extend([colors[-1]] * (face_count - len(colors)))
    return colors[:face_count]


def _capture_part_base_visual_style(obj, face_count, view_object):
    base_shape_color = _current_view_shape_color(view_object)
    base_diffuse_colors = _current_view_diffuse_colors(view_object, face_count, base_shape_color)
    obj.PanelNestVisualBaseShapeColorJson = _serialize_color_triplet(base_shape_color)
    obj.PanelNestVisualBaseDiffuseColorsJson = _serialize_color_list(base_diffuse_colors)
    return base_shape_color, base_diffuse_colors


def _load_part_base_visual_style(obj, face_count, view_object):
    current_shape_color = _current_view_shape_color(view_object)
    shape_color_json = getattr(obj, "PanelNestVisualBaseShapeColorJson", "") or ""
    diffuse_colors_json = getattr(obj, "PanelNestVisualBaseDiffuseColorsJson", "") or ""

    base_shape_color = (
        _deserialize_color_triplet(shape_color_json, current_shape_color)
        if shape_color_json
        else current_shape_color
    )
    current_diffuse_colors = _current_view_diffuse_colors(view_object, face_count, base_shape_color)
    base_diffuse_colors = (
        _deserialize_color_list(diffuse_colors_json, face_count, base_shape_color)
        if diffuse_colors_json
        else current_diffuse_colors
    )
    return base_shape_color, base_diffuse_colors


def _clear_part_base_visual_style(obj):
    if _has_property(obj, "PanelNestVisualBaseShapeColorJson"):
        obj.PanelNestVisualBaseShapeColorJson = ""
    if _has_property(obj, "PanelNestVisualBaseDiffuseColorsJson"):
        obj.PanelNestVisualBaseDiffuseColorsJson = ""


def _resolve_cut_edge_color(obj):
    """Retorna a cor do miolo exposto (bordas sem fita) baseada no material do objeto.

    Lê PanelNestMaterial (já registrado) e busca get_cut_edge_color() no catálogo.
    Retorna None se o objeto não tiver material definido.
    """
    mat_name = str(getattr(obj, "PanelNestMaterial", "") or "").strip()
    if not mat_name:
        return None
    try:
        from panelnest.materials import get_by_name
        mat = get_by_name(mat_name)
        if mat is not None:
            return mat.get_cut_edge_color()
    except Exception:
        pass
    return None


def _resolve_tape_color_for_side(obj, side_key: str) -> tuple:
    """Retorna a cor da fita para um lado específico do objeto.

    Lê PanelNestEdgeBandMaterialJson (por lado) primeiro,
    depois PanelNestEdgeBandMaterial (global) como fallback,
    senão retorna EDGE_BAND_HIGHLIGHT_COLOR.
    """
    import json as _json
    # 1. Por lado
    per_side_json = str(getattr(obj, "PanelNestEdgeBandMaterialJson", "") or "").strip()
    if per_side_json:
        try:
            per_side = _json.loads(per_side_json)
            mat_name = per_side.get(side_key, "")
            if mat_name:
                from panelnest.materials import get_by_name
                mat = get_by_name(mat_name)
                if mat is not None:
                    return mat.color
        except Exception:
            pass
    # 2. Global (legado)
    mat_name = str(getattr(obj, "PanelNestEdgeBandMaterial", "") or "").strip()
    if mat_name:
        try:
            from panelnest.materials import get_by_name
            mat = get_by_name(mat_name)
            if mat is not None:
                return mat.color
        except Exception:
            pass
    return EDGE_BAND_HIGHLIGHT_COLOR


def _resolve_tape_color(obj) -> tuple:
    """Retorna a cor da fita para o objeto (fallback sem lado específico).

    Usa PanelNestEdgeBandMaterial se definido e encontrado no catálogo,
    senão retorna EDGE_BAND_HIGHLIGHT_COLOR.
    """
    mat_name = str(getattr(obj, "PanelNestEdgeBandMaterial", "") or "")
    if mat_name:
        try:
            from panelnest.materials import get_by_name
            mat = get_by_name(mat_name)
            if mat is not None:
                return mat.color
        except Exception:
            pass
    return EDGE_BAND_HIGHLIGHT_COLOR


def refresh_part_edge_band_visuals(objects=None):
    if Gui is None:
        return []

    target_objects = get_part_source_objects(objects)
    refreshed_objects = []

    for obj in target_objects:
        try:
            _lazy_ensure_part_properties(obj)
            view_object = getattr(obj, "ViewObject", None)
            shape = getattr(obj, "Shape", None)
            faces = list(getattr(shape, "Faces", []) or [])
            face_count = len(faces)
            if view_object is None or shape is None or shape.isNull() or face_count <= 0:
                continue

            edge_flags = _edge_band_flags_dict_from_object(obj)
            _length_mm, _width_mm, _thickness_mm, occurrence_count = _object_dimensions_and_quantity(obj)
            occurrence_map = _object_occurrence_edge_band_map(obj, occurrence_count)
            has_edge_band = any(edge_flags.values()) or any(
                any(flags.values()) for flags in occurrence_map.values()
            )
            has_saved_base_style = bool(
                (getattr(obj, "PanelNestVisualBaseShapeColorJson", "") or "").strip()
                or (getattr(obj, "PanelNestVisualBaseDiffuseColorsJson", "") or "").strip()
            )
            cut_edge_color = _resolve_cut_edge_color(obj)
            has_cut_edge_color = cut_edge_color is not None

            if not has_edge_band and not has_cut_edge_color:
                if has_saved_base_style:
                    base_shape_color, base_diffuse_colors = _load_part_base_visual_style(
                        obj,
                        face_count,
                        view_object,
                    )
                    # Se há textura de madeira registrada, ShapeColor/DiffuseColor
                    # devem ficar brancos para MODULATE mostrar a textura como é.
                    # Sem isso, textura × cor_do_material = cor escurecida sem grão.
                    try:
                        from panelnest.textures import _texture_registry as _treg
                        _has_tex = bool(getattr(obj, "Name", None) in _treg)
                    except Exception:
                        _has_tex = False
                    if _has_tex:
                        base_shape_color = (1.0, 1.0, 1.0)
                        base_diffuse_colors = [(1.0, 1.0, 1.0)] * len(base_diffuse_colors)
                    if hasattr(view_object, "ShapeColor"):
                        view_object.ShapeColor = base_shape_color
                    if hasattr(view_object, "DiffuseColor"):
                        view_object.DiffuseColor = tuple(base_diffuse_colors)
                    _clear_part_base_visual_style(obj)
                    refreshed_objects.append(obj)
                continue

            if has_saved_base_style:
                base_shape_color, base_diffuse_colors = _load_part_base_visual_style(
                    obj,
                    face_count,
                    view_object,
                )
            else:
                base_shape_color, base_diffuse_colors = _capture_part_base_visual_style(
                    obj,
                    face_count,
                    view_object,
                )

            highlighted_diffuse_colors = list(base_diffuse_colors)
            _shape, occurrence_face_map = _part_edge_band_occurrence_face_index_map(obj)

            if not occurrence_face_map and any(edge_flags.values()):
                occurrence_face_map = {
                    1: {side_key: [] for side_key in EDGE_BAND_SIDE_KEYS}
                }

            # cut_edge_color já resolvido acima

            for occurrence_index, side_face_map in occurrence_face_map.items():
                effective_flags = dict(edge_flags)
                if occurrence_count > 1 and occurrence_index in occurrence_map:
                    effective_flags = _normalize_occurrence_edge_band_flags(occurrence_map[occurrence_index])
                for side_key, face_indices in side_face_map.items():
                    has_tape = effective_flags.get(side_key, False)
                    tape_color = _resolve_tape_color_for_side(obj, side_key) if has_tape else None
                    for face_index in face_indices:
                        if 0 <= face_index < len(highlighted_diffuse_colors):
                            if has_tape and tape_color is not None:
                                highlighted_diffuse_colors[face_index] = tape_color
                            elif cut_edge_color is not None:
                                highlighted_diffuse_colors[face_index] = cut_edge_color

            # Se há textura de madeira registrada, ShapeColor e as faces sem
            # fita/corte devem ficar brancos para MODULATE renderizar a textura.
            # Faces com tape/cut_edge_color mantêm sua cor (será multiplicada
            # pela textura, dando o efeito visual desejado).
            try:
                from panelnest.textures import _texture_registry as _treg
                _has_tex = bool(getattr(obj, "Name", None) in _treg)
            except Exception:
                _has_tex = False
            if _has_tex:
                shape_color_to_set = (1.0, 1.0, 1.0)
                diffuse_to_set = []
                for i, c in enumerate(highlighted_diffuse_colors):
                    is_modified = (
                        i < len(base_diffuse_colors)
                        and tuple(c) != tuple(base_diffuse_colors[i])
                    )
                    diffuse_to_set.append(c if is_modified else (1.0, 1.0, 1.0))
            else:
                shape_color_to_set = base_shape_color
                diffuse_to_set = highlighted_diffuse_colors

            if hasattr(view_object, "ShapeColor"):
                view_object.ShapeColor = shape_color_to_set
            if hasattr(view_object, "DiffuseColor"):
                view_object.DiffuseColor = tuple(diffuse_to_set)
            elif hasattr(view_object, "ShapeColor"):
                view_object.ShapeColor = tape_color

            # Quando há fita ou cor de corte: só remover textura Coin3D se
            # NÃO houver textura de madeira registrada. Materiais texturados
            # (Nogueira, Carvalho, etc.) convivem com DiffuseColor via MODULATE:
            # faces planas mostram a textura, faces de fita mostram a cor da
            # fita modulada pela textura. Removemos só quando é cor sólida
            # (apply_solid_color_as_texture desregistra do _texture_registry).
            if has_edge_band or has_cut_edge_color:
                try:
                    from panelnest.textures import _remove_coin_texture, _texture_registry
                    obj_name = getattr(obj, "Name", None)
                    has_wood_texture = bool(obj_name and obj_name in _texture_registry)
                    if not has_wood_texture:
                        _remove_coin_texture(view_object)
                except Exception:
                    pass

            refreshed_objects.append(obj)
        except Exception:
            continue

    return refreshed_objects
