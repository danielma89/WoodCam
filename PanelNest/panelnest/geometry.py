try:
    import FreeCAD as App
except ImportError:
    App = None

try:
    import FreeCADGui as Gui
except ImportError:
    Gui = None

from .constants import DIMENSION_EQUALITY_TOLERANCE_MM
from .freecad_utils import (
    _has_property,
    _is_internal_object,
    ensure_document,
)


def _quantity_value(value):
    if hasattr(value, "Value"):
        return float(value.Value)
    return float(value)


def _shape_dimension_values(shape, owner_name="Shape"):
    if shape is None or shape.isNull() or getattr(shape, "Volume", 0) <= 0:
        raise ValueError(f"{owner_name} nao contem um solido valido.")
    bbox = shape.BoundBox
    dims = [float(bbox.XLength), float(bbox.YLength), float(bbox.ZLength)]
    if min(dims) <= 0:
        raise ValueError(f"{owner_name} tem dimensoes invalidas no bounding box.")
    return dims


def _shape_dimensions(obj):
    return _shape_dimension_values(getattr(obj, "Shape", None), getattr(obj, "Name", "objeto"))


def _sorted_dimension_triplet(values):
    dims = sorted((abs(float(value)) for value in values), reverse=True)
    if len(dims) < 3 or dims[2] <= 0:
        raise ValueError("Dimensoes nao positivas.")
    return dims[0], dims[1], dims[2]


def _shape_solid_list(shape):
    solids = []
    for solid in list(getattr(shape, "Solids", []) or []):
        try:
            if solid is None or solid.isNull() or getattr(solid, "Volume", 0) <= 0:
                continue
        except Exception:
            continue
        solids.append(solid)
    if solids:
        return solids
    if shape is not None and not shape.isNull() and getattr(shape, "Volume", 0) > 0:
        return [shape]
    return []


def _repeated_solid_profile(shape):
    solids = _shape_solid_list(shape)
    if len(solids) <= 1:
        return None

    first_dims = _sorted_dimension_triplet(_shape_dimension_values(solids[0], "solido"))
    for solid in solids[1:]:
        current_dims = _sorted_dimension_triplet(_shape_dimension_values(solid, "solido"))
        if any(abs(first_dims[index] - current_dims[index]) > 0.1 for index in range(3)):
            return None
    return {
        "quantity": len(solids),
        "dimensions": first_dims,
    }


def _object_dimensions_and_quantity(obj):
    shape = getattr(obj, "Shape", None)
    repeated_profile = _repeated_solid_profile(shape)
    if repeated_profile is not None:
        length_mm, width_mm, thickness_mm = repeated_profile["dimensions"]
        return length_mm, width_mm, thickness_mm, repeated_profile["quantity"]

    if all(hasattr(obj, attr) for attr in ("Length", "Width", "Height")):
        dims = [
            _quantity_value(getattr(obj, "Length")),
            _quantity_value(getattr(obj, "Width")),
            _quantity_value(getattr(obj, "Height")),
        ]
    else:
        dims = _shape_dimensions(obj)

    length_mm, width_mm, thickness_mm = _sorted_dimension_triplet(dims)
    return length_mm, width_mm, thickness_mm, 1


def _object_dimensions(obj):
    length_mm, width_mm, thickness_mm, _quantity = _object_dimensions_and_quantity(obj)
    return length_mm, width_mm, thickness_mm


def _face_index_from_name(face_name):
    raw_name = str(face_name or "").strip()
    if not raw_name.startswith("Face"):
        return None
    try:
        return int(raw_name[4:]) - 1
    except Exception:
        return None


def _face_center_point(face):
    center = getattr(face, "CenterOfMass", None)
    if center is not None:
        return center
    bbox = getattr(face, "BoundBox", None)
    if bbox is None or App is None or not hasattr(App, "Vector"):
        return None
    return App.Vector(
        _bbox_axis_center_value(bbox, "x"),
        _bbox_axis_center_value(bbox, "y"),
        _bbox_axis_center_value(bbox, "z"),
    )


def _face_matches_candidate(face, candidate_face):
    if face is None or candidate_face is None:
        return False
    if face is candidate_face:
        return True

    for current_face, other_face in ((face, candidate_face), (candidate_face, face)):
        comparator = getattr(current_face, "isSame", None)
        if callable(comparator):
            try:
                if comparator(other_face):
                    return True
            except Exception:
                pass

    try:
        area_a = float(getattr(face, "Area", 0.0) or 0.0)
        area_b = float(getattr(candidate_face, "Area", 0.0) or 0.0)
        if abs(area_a - area_b) > max(0.2, max(area_a, area_b) * 0.002):
            return False
    except Exception:
        pass

    center_a = _face_center_point(face)
    center_b = _face_center_point(candidate_face)
    if center_a is None or center_b is None:
        return False
    return (
        abs(center_a.x - center_b.x) <= 0.15
        and abs(center_a.y - center_b.y) <= 0.15
        and abs(center_a.z - center_b.z) <= 0.15
    )


def _solid_contexts_for_shape(shape):
    contexts = []
    for solid_index, solid in enumerate(_shape_solid_list(shape)):
        try:
            axis_extents = _shape_local_axis_extents(solid)
            ordered_axes = _ordered_local_axes(axis_extents)
        except Exception:
            continue
        contexts.append(
            {
                "solid_index": solid_index,
                "solid": solid,
                "axis_extents": axis_extents,
                "length_axis": ordered_axes[0],
                "width_axis": ordered_axes[1],
                "thickness_axis": ordered_axes[2],
            }
        )
    return contexts


def _solid_context_for_face(face, solid_contexts):
    if not solid_contexts:
        return None
    if len(solid_contexts) == 1:
        return solid_contexts[0]

    for context in solid_contexts:
        for candidate_face in list(getattr(context["solid"], "Faces", []) or []):
            if _face_matches_candidate(face, candidate_face):
                return context
    return solid_contexts[0]


def _face_side_key_from_context(face, axis_extents, length_axis, width_axis, thickness_axis):
    if not _is_planar_face(face):
        return ""

    dominant_axis = _dominant_axis_name(_face_normal_vector(face))
    if dominant_axis == thickness_axis:
        return ""

    thickness_mm = max(axis_extents[thickness_axis]["length"], 0.0)
    tolerance_mm = max(
        0.25,
        thickness_mm * 0.18,
        axis_extents[dominant_axis]["length"] * 0.002,
    )
    if dominant_axis == width_axis:
        if _face_touches_axis_boundary(face, dominant_axis, axis_extents[dominant_axis]["max"], tolerance_mm):
            return "top"
        if _face_touches_axis_boundary(face, dominant_axis, axis_extents[dominant_axis]["min"], tolerance_mm):
            return "bottom"
        return ""
    if dominant_axis == length_axis:
        if _face_touches_axis_boundary(face, dominant_axis, axis_extents[dominant_axis]["max"], tolerance_mm):
            return "right"
        if _face_touches_axis_boundary(face, dominant_axis, axis_extents[dominant_axis]["min"], tolerance_mm):
            return "left"
    return ""
    dims = sorted((abs(value) for value in dims), reverse=True)
    if dims[2] <= 0:
        raise ValueError(f"{obj.Name} tem dimensoes nao positivas.")
    return dims[0], dims[1], dims[2]


def _is_body_child(obj):
    for parent in getattr(obj, "InList", []):
        if getattr(parent, "TypeId", "") == "PartDesign::Body":
            return True
    return False


_NON_PANEL_TYPE_IDS = frozenset({
    "Part::Cylinder", "Part::Sphere", "Part::Cone", "Part::Torus",
    "Part::Helix", "Part::Spiral", "Part::Wedge",
})


def _is_panel_like_shape(obj):
    """Verifica se a forma do objeto é compatível com um painel (chapa plana).

    Rejeita cilindros, esferas e outros sólidos que não são painéis.
    Critérios:
      1) Volume/BBox ratio > 0.78 (cilindro inscrito na bbox ≈ π/4 ≈ 0.785)
      2) Aspect ratio: espessura/largura < 0.6 (peça plana, não haste/cubo)
    """
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return True
    try:
        if shape.isNull() or getattr(shape, "Volume", 0) <= 0:
            return True
    except Exception:
        return True
    bbox = getattr(shape, "BoundBox", None)
    if bbox is None:
        return True
    dims = sorted([
        abs(float(bbox.XLength)),
        abs(float(bbox.YLength)),
        abs(float(bbox.ZLength)),
    ], reverse=True)
    if dims[2] <= 0:
        return True
    bbox_vol = dims[0] * dims[1] * dims[2]
    if bbox_vol <= 0:
        return True
    thickness, width = dims[2], dims[1]
    if width > 0 and thickness / width > 0.6:
        return False
    # Rejeita sólidos não-painéis (esferas, formas cilíndricas customizadas) pelo
    # ratio volume/bbox. Porém painéis planos com cortes ou perfis curvos também
    # têm vol_ratio baixo — diferenciados pela proporção de espessura:
    # painel: espessura << largura (ratio ≈ 0.04 para 18×400mm);
    # cilindro/sólido redondo: dims[2] ≈ dims[1] (ratio ≈ 1.0).
    vol_ratio = float(shape.Volume) / bbox_vol
    if vol_ratio < 0.78 and (width <= 0 or thickness / width >= 0.4):
        return False
    return True


def _is_candidate_object(obj):
    if _is_internal_object(obj):
        return False
    type_id = getattr(obj, "TypeId", "") or ""
    if type_id in {"App::Part", "App::DocumentObjectGroup"} or type_id.startswith("Assembly::"):
        return False
    if type_id in _NON_PANEL_TYPE_IDS:
        return False
    if getattr(obj, "TypeId", "") != "PartDesign::Body" and _is_body_child(obj):
        return False

    try:
        _shape_dimensions(obj)
    except ValueError:
        return False

    if not _is_panel_like_shape(obj):
        return False

    return True


def _own_visibility(obj):
    active_document = getattr(Gui, "ActiveDocument", None) if Gui is not None else None
    if Gui is None or active_document is None:
        return True

    view_object = None
    try:
        view_object = active_document.getObject(obj.Name)
    except Exception:
        view_object = None

    if view_object is None:
        view_object = getattr(obj, "ViewObject", None)
    if view_object is None:
        return True

    return bool(getattr(view_object, "Visibility", True))


def _is_visible_object(obj, _seen=None):
    if obj is None:
        return False
    if not _own_visibility(obj):
        return False

    # Container pai oculto → objeto não é visível visualmente,
    # mesmo que sua própria flag Visibility esteja True.
    if _seen is None:
        _seen = set()
    name = getattr(obj, "Name", None)
    if name in _seen:
        return True
    if name:
        _seen.add(name)

    parent_container_types = {
        "App::Part",
        "App::DocumentObjectGroup",
        "App::DocumentObjectGroupPython",
        "App::LinkGroup",
        "PartDesign::Body",
    }
    for parent in getattr(obj, "InList", []) or []:
        parent_type = str(getattr(parent, "TypeId", "") or "")
        if parent_type in parent_container_types or parent_type.startswith("Assembly::"):
            if not _is_visible_object(parent, _seen):
                return False
    return True


def _dedupe_objects(objects):
    unique = []
    seen = set()
    for obj in objects:
        name = getattr(obj, "Name", None)
        if not name or name in seen:
            continue
        seen.add(name)
        unique.append(obj)
    return unique


def _get_gui_selection():
    if Gui is None:
        return []

    selection = getattr(Gui, "Selection", None)
    if selection is None or not hasattr(selection, "getSelection"):
        return []

    try:
        return selection.getSelection()
    except Exception:
        return []


def _get_gui_selection_ex():
    if Gui is None:
        return []

    selection = getattr(Gui, "Selection", None)
    if selection is None or not hasattr(selection, "getSelectionEx"):
        return []

    try:
        return selection.getSelectionEx()
    except Exception:
        return []


def _expand_groups(objects):
    """Expande grupos/containers retornando todos os objetos filhos recursivamente."""
    expanded = []
    seen = set()
    stack = list(objects)
    while stack:
        obj = stack.pop(0)
        name = getattr(obj, "Name", None)
        if not name or name in seen:
            continue
        seen.add(name)
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id in {
            "App::DocumentObjectGroup",
            "App::DocumentObjectGroupPython",
            "App::LinkGroup",
            "App::Part",
        } or type_id.startswith("Assembly::"):
            children = (
                getattr(obj, "Group", None)
                or getattr(obj, "Links", None)
                or getattr(obj, "OutList", None)
                or []
            )
            stack.extend(children)
        else:
            expanded.append(obj)
    return expanded


def _is_user_project_container(obj):
    """Retorna True se o objeto é um container de projeto (grupo, Part, Body, etc.).

    Usado para detectar se o usuário selecionou um grupo intencionalmente,
    evitando o fallback para "todos os objetos visíveis".
    """
    if obj is None:
        return False
    type_id = str(getattr(obj, "TypeId", "") or "")
    container_types = {
        "App::Part",
        "App::DocumentObjectGroup",
        "PartDesign::Body",
        "Assembly::Assembly",
        "Part::Compound",
    }
    if type_id in container_types:
        return True
    # Objetos com filhos também são containers
    children = (
        list(getattr(obj, "Group", []) or [])
        or list(getattr(obj, "Links", []) or [])
    )
    return bool(children)


def get_part_source_objects(objects=None, include_hidden=False):
    doc = ensure_document()

    if objects is not None:
        # Subir Pad/filhos de Body → Body pai
        resolved = []
        for obj in objects:
            if _is_body_child(obj):
                for parent in getattr(obj, "InList", []):
                    if getattr(parent, "TypeId", "") == "PartDesign::Body":
                        obj = parent
                        break
            resolved.append(obj)
        expanded = _expand_groups(resolved)
        candidates = [
            obj for obj in _dedupe_objects(expanded)
            if _is_candidate_object(obj) and (include_hidden or _is_visible_object(obj))
        ]
        return candidates

    selection = [] if include_hidden else _get_gui_selection()
    if selection:
        # Subir Pad/Sketch → Body pai antes de expandir grupos
        resolved = []
        for obj in selection:
            body = None
            if getattr(obj, "TypeId", "").startswith("PartDesign::") or _is_body_child(obj):
                for parent in getattr(obj, "InList", []):
                    if getattr(parent, "TypeId", "") == "PartDesign::Body":
                        body = parent
                        break
            resolved.append(body if body is not None else obj)

        expanded = _expand_groups(resolved)
        selected_candidates = [
            obj for obj in _dedupe_objects(expanded)
            if _is_candidate_object(obj) and _is_visible_object(obj)
        ]
        # Só usa a seleção se encontrou peças válidas (evita cair no fallback
        # de "todos visíveis" quando o usuário selecionou só um grupo vazio)
        if selected_candidates:
            return selected_candidates
        # Se selecionou algo mas não achou peças, verifica se era intenção
        # de selecionar um grupo — nesse caso não faz fallback para todos os visíveis
        if any(_is_user_project_container(obj) for obj in resolved):
            return []

    candidates = list(doc.Objects)
    if not include_hidden:
        candidates = [obj for obj in candidates if _is_visible_object(obj)]
    return [obj for obj in _dedupe_objects(candidates) if _is_candidate_object(obj)]


def _candidate_object_from_selection_object(obj):
    if obj is None:
        return None
    if _is_candidate_object(obj):
        return obj

    def _candidate_parent_priority(parent):
        type_id = str(getattr(parent, "TypeId", "") or "")
        if type_id == "App::Link":
            return 0
        if type_id.startswith("Part::Feature") or "Array" in type_id or "Clone" in type_id:
            return 1
        if type_id == "PartDesign::Body":
            return 3
        return 2

    frontier = list(getattr(obj, "InList", []) or [])
    seen = set()
    while frontier:
        candidate_parents = []
        next_frontier = []
        for parent in frontier:
            parent_name = getattr(parent, "Name", None)
            if not parent_name or parent_name in seen:
                continue
            seen.add(parent_name)
            if _is_candidate_object(parent):
                candidate_parents.append(parent)
                continue
            next_frontier.extend(list(getattr(parent, "InList", []) or []))

        if candidate_parents:
            candidate_parents.sort(
                key=lambda parent: (
                    _candidate_parent_priority(parent),
                    0 if _is_visible_object(parent) else 1,
                    getattr(parent, "Name", ""),
                )
            )
            return candidate_parents[0]

        frontier = next_frontier

    return None


def _vector_axis_value(vector, axis_name):
    for attribute_name in (axis_name.lower(), axis_name.upper()):
        if hasattr(vector, attribute_name):
            return float(getattr(vector, attribute_name))
    raise AttributeError(axis_name)


def _bbox_axis_length_map(bbox):
    return {
        "x": float(getattr(bbox, "XLength", 0.0)),
        "y": float(getattr(bbox, "YLength", 0.0)),
        "z": float(getattr(bbox, "ZLength", 0.0)),
    }


def _identity_placement():
    if App is None or not hasattr(App, "Placement"):
        return None
    try:
        return App.Placement()
    except Exception:
        return None


def _object_global_placement(obj):
    if obj is None:
        return _identity_placement()

    getter = getattr(obj, "getGlobalPlacement", None)
    if callable(getter):
        try:
            placement = getter()
        except TypeError:
            placement = getter
        except Exception:
            placement = None
        if placement is not None:
            return placement

    placement = getattr(obj, "Placement", None)
    return placement if placement is not None else _identity_placement()


def _inverse_placement(placement):
    if placement is None:
        return _identity_placement()
    try:
        return placement.inverse()
    except Exception:
        return _identity_placement()


def _transform_point_to_local(point, inverse_placement):
    if point is None or inverse_placement is None or not hasattr(inverse_placement, "multVec"):
        return point
    try:
        return inverse_placement.multVec(point)
    except Exception:
        return point


def _transform_vector_to_local(vector, inverse_placement):
    if vector is None or inverse_placement is None:
        return vector
    rotation = getattr(inverse_placement, "Rotation", None)
    if rotation is None or not hasattr(rotation, "multVec"):
        return vector
    try:
        return rotation.multVec(vector)
    except Exception:
        return vector


def _bbox_corner_points(bbox):
    if bbox is None or App is None or not hasattr(App, "Vector"):
        return []
    corners = []
    for x_value in (float(getattr(bbox, "XMin", 0.0)), float(getattr(bbox, "XMax", 0.0))):
        for y_value in (float(getattr(bbox, "YMin", 0.0)), float(getattr(bbox, "YMax", 0.0))):
            for z_value in (float(getattr(bbox, "ZMin", 0.0)), float(getattr(bbox, "ZMax", 0.0))):
                corners.append(App.Vector(x_value, y_value, z_value))
    return corners


def _shape_axis_extents_in_local_coordinates(obj):
    shape = getattr(obj, "Shape", None)
    bbox = getattr(shape, "BoundBox", None)
    if shape is None or bbox is None:
        raise ValueError("Shape invalido para resolver as faces da peca.")

    inverse_placement = _inverse_placement(_object_global_placement(obj))
    points = []
    for vertex in list(getattr(shape, "Vertexes", []) or []):
        point = getattr(vertex, "Point", None)
        if point is None:
            continue
        points.append(_transform_point_to_local(point, inverse_placement))

    if not points:
        points = [
            _transform_point_to_local(point, inverse_placement)
            for point in _bbox_corner_points(bbox)
        ]

    if not points:
        raise ValueError("Nao foi possivel ler os vertices da peca.")

    axis_extents = {}
    for axis_name in ("x", "y", "z"):
        axis_values = [_vector_axis_value(point, axis_name) for point in points]
        axis_min = min(axis_values)
        axis_max = max(axis_values)
        axis_extents[axis_name] = {
            "min": axis_min,
            "max": axis_max,
            "center": (axis_min + axis_max) / 2.0,
            "length": axis_max - axis_min,
        }
    return shape, axis_extents, inverse_placement


def _ordered_local_axes(axis_extents):
    ordered_axes = [
        axis_name
        for axis_name, _axis_data in sorted(
            axis_extents.items(),
            key=lambda item: item[1]["length"],
            reverse=True,
        )
    ]
    if len(ordered_axes) < 3 or axis_extents[ordered_axes[2]]["length"] <= 0.0:
        raise ValueError("Nao foi possivel identificar comprimento, largura e espessura.")
    return ordered_axes


def _bbox_axis_center_value(bbox, axis_name):
    axis_upper = axis_name.upper()
    minimum = float(getattr(bbox, f"{axis_upper}Min"))
    maximum = float(getattr(bbox, f"{axis_upper}Max"))
    return (minimum + maximum) / 2.0


def _face_mid_parameters(face):
    parameter_range = getattr(face, "ParameterRange", None)
    if not parameter_range or len(parameter_range) < 4:
        return 0.0, 0.0
    u_min, u_max, v_min, v_max = parameter_range[:4]
    return ((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)


def _face_normal_vector(face):
    u_value, v_value = _face_mid_parameters(face)
    if hasattr(face, "normalAt"):
        return face.normalAt(u_value, v_value)
    raise ValueError("A face selecionada nao expoe normal geometrica.")


def _is_planar_face(face):
    surface = getattr(face, "Surface", None)
    surface_name = type(surface).__name__ if surface is not None else ""
    return "Plane" in surface_name


def _dominant_axis_name(vector, minimum_alignment=0.92):
    components = {
        "x": abs(_vector_axis_value(vector, "x")),
        "y": abs(_vector_axis_value(vector, "y")),
        "z": abs(_vector_axis_value(vector, "z")),
    }
    axis_name, axis_value = max(components.items(), key=lambda item: item[1])
    if axis_value < minimum_alignment:
        raise ValueError(
            "A face selecionada nao esta alinhada aos eixos principais da peca. "
            "Nesta primeira versao, selecione faces laterais ortogonais do painel."
        )
    return axis_name



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


def _face_touches_axis_boundary(face, axis_name, target_value, tolerance_mm):
    bbox = getattr(face, "BoundBox", None)
    if bbox is None:
        from .edge_band import _face_axis_center_value
        return abs(_face_axis_center_value(face, axis_name) - target_value) <= tolerance_mm

    axis_upper = axis_name.upper()
    face_min = float(getattr(bbox, f"{axis_upper}Min", 0.0))
    face_max = float(getattr(bbox, f"{axis_upper}Max", 0.0))
    face_center = (face_min + face_max) / 2.0
    return (
        abs(face_center - target_value) <= tolerance_mm
        or abs(face_min - target_value) <= tolerance_mm
        or abs(face_max - target_value) <= tolerance_mm
    )


def _is_cylindrical_face(face):
    surface = getattr(face, "Surface", None)
    surface_name = type(surface).__name__ if surface is not None else ""
    return "Cylinder" in surface_name


def _extract_holes_from_shape(shape, obj_placement=None):
    """Extrai furos (faces cilíndricas) de uma shape FreeCAD.

    Retorna lista de dicts:
        x_mm, y_mm      — posição no plano da peça (comprimento × largura)
        diameter_mm     — diâmetro do furo
        depth_mm        — profundidade estimada
        face            — 'top' | 'bottom' | 'front' | 'back' | 'left' | 'right'
    """
    if shape is None:
        try:
            if shape.isNull():
                return []
        except Exception:
            return []
        return []

    try:
        if shape.isNull():
            return []
    except Exception:
        return []

    bbox = getattr(shape, "BoundBox", None)
    if bbox is None:
        return []

    # Determinar qual eixo é a espessura (menor dimensão)
    dims = {
        "x": float(getattr(bbox, "XLength", 0.0)),
        "y": float(getattr(bbox, "YLength", 0.0)),
        "z": float(getattr(bbox, "ZLength", 0.0)),
    }
    thickness_axis = min(dims, key=dims.get)
    thickness_val = dims[thickness_axis]
    # Eixos do plano (comprimento e largura). Ordenados pela DIMENSÃO
    # (maior primeiro) para casar com a convenção de part.length_mm e
    # part.width_mm (length = maior, width = menor). Se usarmos a ordem
    # alfabética dos eixos, holes de peças com comprimento ao longo de Y
    # ficam com x_mm/y_mm trocados em relação a length/width — e no layout
    # acabam caindo fora da bbox da peça (em peças vizinhas).
    plane_axes = sorted(
        [ax for ax in ("x", "y", "z") if ax != thickness_axis],
        key=lambda ax: dims[ax],
        reverse=True,
    )

    holes = []
    seen = set()  # evitar duplicatas (furo detectado por múltiplas faces)

    for face in list(getattr(shape, "Faces", []) or []):
        try:
            if not _is_cylindrical_face(face):
                continue

            surface = face.Surface
            # Eixo do cilindro
            axis = getattr(surface, "Axis", None)
            if axis is None:
                continue

            # Só furos cujo eixo é aproximadamente perpendicular ao plano da peça
            # (paralelo ao eixo de espessura)
            axis_components = {
                "x": abs(float(getattr(axis, "x", 0.0))),
                "y": abs(float(getattr(axis, "y", 0.0))),
                "z": abs(float(getattr(axis, "z", 0.0))),
            }
            dominant = max(axis_components, key=axis_components.get)
            if axis_components[dominant] < 0.85:
                continue  # eixo inclinado, ignorar
            if dominant != thickness_axis:
                continue  # furo lateral (não passante pela face maior), ignorar por ora

            radius_mm = float(getattr(surface, "Radius", 0.0))
            if radius_mm <= 0.5:
                continue  # ignorar furos muito pequenos (artefatos)

            # Centro do furo: ponto na superfície do cilindro
            center = getattr(surface, "Center", None)
            if center is None:
                center = getattr(face, "CenterOfMass", None)
            if center is None:
                continue

            cx = float(getattr(center, "x", 0.0))
            cy = float(getattr(center, "y", 0.0))
            cz = float(getattr(center, "z", 0.0))

            # Coordenadas no plano da peça (relativas ao bbox)
            coord_map = {"x": cx, "y": cy, "z": cz}
            p0 = coord_map[plane_axes[0]] - float(getattr(bbox, f"{plane_axes[0].upper()}Min", 0.0))
            p1 = coord_map[plane_axes[1]] - float(getattr(bbox, f"{plane_axes[1].upper()}Min", 0.0))

            # Profundidade = extensão da face cilíndrica no eixo de espessura
            face_bbox = getattr(face, "BoundBox", None)
            if face_bbox is not None:
                depth_mm = float(getattr(face_bbox, f"{thickness_axis.upper()}Length", thickness_val))
            else:
                depth_mm = thickness_val

            # Qual face (top/bottom)
            thickness_center = coord_map[thickness_axis]
            thickness_min = float(getattr(bbox, f"{thickness_axis.upper()}Min", 0.0))
            thickness_max = float(getattr(bbox, f"{thickness_axis.upper()}Max", 0.0))
            if abs(thickness_center - thickness_max) < abs(thickness_center - thickness_min):
                face_label = "top"
            else:
                face_label = "bottom"

            # Chave de deduplicação
            key = (round(p0, 1), round(p1, 1), round(radius_mm, 1), face_label)
            if key in seen:
                continue
            seen.add(key)

            holes.append({
                "x_mm": round(p0, 3),
                "y_mm": round(p1, 3),
                "diameter_mm": round(radius_mm * 2.0, 3),
                "depth_mm": round(depth_mm, 3),
                "face": face_label,
            })
        except Exception:
            continue

    return holes


def extract_object_holes(obj):
    """Extrai furos de um objeto FreeCAD. Retorna lista de dicts."""
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return []
    try:
        return _extract_holes_from_shape(shape)
    except Exception:
        return []


def _profile_polygon_area(points):
    """Retorna a área absoluta de um polígono 2D pelo método shoelace."""
    if len(points) < 3:
        return 0.0
    area_twice = 0.0
    for index, point in enumerate(points):
        next_point = points[(index + 1) % len(points)]
        area_twice += (point[0] * next_point[1]) - (next_point[0] * point[1])
    return abs(area_twice) / 2.0


def _profile_is_axis_aligned_rectangle(points):
    """Distingue retângulo de outros quadriláteros pelo preenchimento do bbox.

    Contar vértices não é suficiente: trapézios e paralelogramos também têm
    quatro cantos. Um retângulo alinhado aos eixos ocupa integralmente o seu
    bounding box, inclusive quando há pontos colineares extras nas arestas.
    """
    if len(points) < 3:
        return False

    min_x = min(point[0] for point in points)
    max_x = max(point[0] for point in points)
    min_y = min(point[1] for point in points)
    max_y = max(point[1] for point in points)
    bbox_area = (max_x - min_x) * (max_y - min_y)
    if bbox_area <= 0.0:
        return False

    polygon_area = _profile_polygon_area(points)
    area_tolerance = max(0.5, bbox_area * 0.00001)
    return abs(bbox_area - polygon_area) <= area_tolerance


def extract_part_profile(obj):
    """Extrai o contorno 2D real da face maior do painel.

    Retorna lista de (x, y) em mm, com origem (0, 0) no canto inferior-esquerdo,
    onde X corre ao longo do comprimento (length_mm) e Y ao longo da largura
    (width_mm). Curvas são aproximadas em segmentos lineares (deflection=0.5mm).

    Retorna None se a peça for retangular simples ou se a extração falhar
    — nesse caso o código usa o bounding box como antes.
    """
    if App is None:
        return None
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return None
    try:
        if shape.isNull() or getattr(shape, "Volume", 0) <= 0:
            return None
    except Exception:
        return None

    bbox = getattr(shape, "BoundBox", None)
    if bbox is None:
        return None

    # Eixo com menor dimensão = espessura; os outros dois = perfil (comprimento e largura)
    dims_with_axis = sorted([
        (abs(float(getattr(bbox, "XLength", 0.0))), 0),
        (abs(float(getattr(bbox, "YLength", 0.0))), 1),
        (abs(float(getattr(bbox, "ZLength", 0.0))), 2),
    ])  # crescente: [menor, médio, maior]
    if dims_with_axis[0][0] <= 0:
        return None
    _width_axis = dims_with_axis[1][1]   # eixo médio  → Y do perfil (width_mm)
    _length_axis = dims_with_axis[2][1]  # eixo maior  → X do perfil (length_mm)

    # Face plana com maior área
    best_face = None
    best_area = 0.0
    for face in (getattr(shape, "Faces", None) or []):
        try:
            if not _is_planar_face(face):
                continue
            area = float(getattr(face, "Area", 0.0))
            if area > best_area:
                best_area = area
                best_face = face
        except Exception:
            continue
    if best_face is None:
        return None

    try:
        outer_wire = best_face.OuterWire
    except Exception:
        return None

    def _pt_2d(pt):
        c = [pt.x, pt.y, pt.z]
        return (c[_length_axis], c[_width_axis])

    # Discretiza o wire inteiro em ordem de percurso (garante sequência correta)
    # Wire.discretize retorna pontos em ordem ao longo do wire fechado,
    # evitando o problema de arestas fora de ordem em OuterWire.Edges
    points_2d = []
    try:
        wire_pts = outer_wire.discretize(Deflection=0.5) or []
        # O último ponto é igual ao primeiro (wire fechado) — remove duplicata
        if wire_pts and len(wire_pts) > 1:
            last = wire_pts[-1]
            first = wire_pts[0]
            if abs(last.x - first.x) < 0.05 and abs(last.y - first.y) < 0.05 and abs(last.z - first.z) < 0.05:
                wire_pts = wire_pts[:-1]
        points_2d = [_pt_2d(pt) for pt in wire_pts]
    except Exception:
        # Fallback: itera arestas individualmente
        for edge in (getattr(outer_wire, "Edges", None) or []):
            try:
                pts = edge.discretize(Deflection=0.5) or []
                for pt in pts[:-1]:
                    points_2d.append(_pt_2d(pt))
            except Exception:
                for v in (getattr(edge, "Vertexes", None) or []):
                    points_2d.append(_pt_2d(v.Point))

    if len(points_2d) < 3:
        return None

    # Remove pontos duplicados consecutivos
    deduped = [points_2d[0]]
    for pt in points_2d[1:]:
        prev = deduped[-1]
        if abs(pt[0] - prev[0]) > 0.05 or abs(pt[1] - prev[1]) > 0.05:
            deduped.append(pt)
    if len(deduped) < 3:
        return None

    # Normaliza para origem (0, 0)
    min_x = min(p[0] for p in deduped)
    min_y = min(p[1] for p in deduped)
    normalized = [(round(p[0] - min_x, 3), round(p[1] - min_y, 3)) for p in deduped]

    # Só descarta quando a geometria realmente preenche todo o bounding box.
    # Trapézios e paralelogramos também têm quatro vértices e precisam manter
    # o perfil real para o layout e para as exportações.
    if _profile_is_axis_aligned_rectangle(normalized):
        return None

    return normalized
