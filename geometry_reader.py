import FreeCAD
import FreeCADGui
import math
import re


PANELNEST_MANAGED_PROPERTY = "PanelNestManagedType"
PANELNEST_CAM_TYPE = "layout_cam_compound"


def _is_plane_face(face):
    """Verifica se uma face é plana."""
    if not hasattr(face, "Surface"):
        return False
    surface = face.Surface

    if hasattr(surface, "isInstance"):
        try:
            return bool(surface.isInstance("Geom_Plane"))
        except Exception:
            pass

    surface_name = surface.__class__.__name__.lower()
    surface_type = getattr(surface, "TypeId", "").lower()
    return "plane" in surface_name or "plane" in surface_type


def _polygon_area_2d(points):
    if len(points) < 3:
        return 0.0

    area = 0.0
    for idx, point in enumerate(points):
        next_point = points[(idx + 1) % len(points)]
        area += point[0] * next_point[1] - next_point[0] * point[1]
    return area * 0.5


def _clean_xy_points(vectors):
    points = [(round(vector.x, 6), round(vector.y, 6)) for vector in vectors]
    if len(points) < 3:
        raise ValueError("O contorno precisa ter pelo menos três pontos.")

    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]

    clean_points = [points[0]]
    for point in points[1:]:
        if point != clean_points[-1]:
            clean_points.append(point)

    if len(clean_points) < 3:
        raise ValueError("Após remover duplicatas, o contorno precisa ter pelo menos 3 pontos distintos.")

    return clean_points


def _vector_distance(a, b):
    return math.sqrt((a.x - b.x) ** 2 + (a.y - b.y) ** 2 + (a.z - b.z) ** 2)


def _edge_vectors(edge):
    # A compensação do perfil é calculada a partir destes pontos. Uma
    # deflexão de 0,1 mm deixa arcos grandes visivelmente facetados e faz o
    # offset parecer variar entre os segmentos. 0,01 mm mantém o desvio muito
    # abaixo de ajustes dimensionais usuais de marcenaria/CNC.
    try:
        vectors = edge.discretize(Deflection=0.01)
    except TypeError:
        vectors = [vertex.Point for vertex in edge.Vertexes]

    if len(vectors) < 2:
        vectors = [vertex.Point for vertex in edge.Vertexes]

    return list(vectors)


def _wire_edges(wire):
    if wire.isClosed():
        try:
            ordered_edges = list(wire.OrderedEdges)
            if ordered_edges:
                return ordered_edges
        except Exception:
            pass

    return list(wire.Edges)


def _ordered_vectors_from_wire(wire):
    edge_vectors = [_edge_vectors(edge) for edge in _wire_edges(wire)]
    edge_vectors = [vectors for vectors in edge_vectors if len(vectors) >= 2]
    if not edge_vectors:
        return [vertex.Point for vertex in wire.OrderedVertexes]

    vectors = list(edge_vectors.pop(0))

    while edge_vectors:
        current = vectors[-1]
        best_index = 0
        best_reversed = False
        best_distance = None

        for index, candidate in enumerate(edge_vectors):
            start_distance = _vector_distance(current, candidate[0])
            end_distance = _vector_distance(current, candidate[-1])

            if best_distance is None or start_distance < best_distance:
                best_distance = start_distance
                best_index = index
                best_reversed = False
            if end_distance < best_distance:
                best_distance = end_distance
                best_index = index
                best_reversed = True

        next_vectors = edge_vectors.pop(best_index)
        if best_reversed:
            next_vectors = list(reversed(next_vectors))

        if _vector_distance(vectors[-1], next_vectors[0]) < 1e-6:
            vectors.extend(next_vectors[1:])
        else:
            vectors.extend(next_vectors)

    return vectors


def _contour_signature(points):
    return tuple(sorted((round(point[0], 3), round(point[1], 3)) for point in points))


def _average_z_from_wire(wire):
    vertices = getattr(wire, "OrderedVertexes", [])
    if not vertices:
        return 0.0
    return sum(vertex.Point.z for vertex in vertices) / len(vertices)


def _extract_points_from_wire(wire):
    vectors = _ordered_vectors_from_wire(wire)
    if len(vectors) < 2 or _vector_distance(vectors[0], vectors[-1]) > 1e-4:
        raise ValueError("O contorno selecionado não está fechado.")

    return _clean_xy_points(vectors)


def _extract_contours_from_wires(wires):
    contours = []
    seen = set()

    for wire in wires:
        if not wire.isClosed():
            continue
        try:
            points = _extract_points_from_wire(wire)
        except Exception:
            continue
        if abs(_polygon_area_2d(points)) <= 1e-6:
            continue

        signature = _contour_signature(points)
        if signature in seen:
            continue
        seen.add(signature)
        contours.append(points)

    return contours


def _wire_projection_area(wire):
    try:
        points = _extract_points_from_wire(wire)
    except Exception:
        return 0.0
    return abs(_polygon_area_2d(points))


def _outer_wire_from_face(face):
    wires = list(face.Wires)
    if not wires:
        raise ValueError("A face selecionada não contém contornos.")

    return max(wires, key=_wire_projection_area)


def _face_projection_area(face):
    if not _is_plane_face(face):
        return 0.0
    try:
        return _wire_projection_area(_outer_wire_from_face(face))
    except Exception:
        return 0.0


def _best_planar_face_from_shape(shape):
    faces = [face for face in getattr(shape, "Faces", []) if _is_plane_face(face)]
    if not faces:
        return None

    valid_faces = [face for face in faces if _face_projection_area(face) > 1e-6]
    if not valid_faces:
        return None

    return max(valid_faces, key=lambda face: (_face_projection_area(face), getattr(face, "Area", 0.0)))


def _planar_face_contours_from_shape(shape):
    return _planar_geometry_from_faces(getattr(shape, "Faces", []))["contours"]


def _circle_from_edge(edge):
    curve = getattr(edge, "Curve", None)
    radius = float(getattr(curve, "Radius", 0.0) or 0.0)
    center = getattr(curve, "Center", None)
    if radius <= 0.0 or center is None:
        return None
    return {
        "x": float(center.x),
        "y": float(center.y),
        "diameter_mm": radius * 2.0,
    }


def _circle_from_wire(wire):
    edges = list(getattr(wire, "Edges", []) or [])
    if len(edges) != 1:
        return None
    return _circle_from_edge(edges[0])


def _circular_hole_from_record(record):
    points = record["points"]
    circle = record.get("circle")
    if circle is not None:
        center_x = circle["x"]
        center_y = circle["y"]
        diameter = circle["diameter_mm"]
    else:
        xs = [point[0] for point in points]
        ys = [point[1] for point in points]
        center_x = (min(xs) + max(xs)) * 0.5
        center_y = (min(ys) + max(ys)) * 0.5
        width = max(xs) - min(xs)
        height = max(ys) - min(ys)
        mean_diameter = (width + height) * 0.5
        if mean_diameter <= 1e-6:
            return None

        radii = [
            math.hypot(point[0] - center_x, point[1] - center_y)
            for point in points
        ]
        mean_radius = sum(radii) / len(radii)
        radial_error = max(abs(radius - mean_radius) for radius in radii)
        circular_tolerance = max(0.20, mean_radius * 0.05)
        if abs(width - height) > max(0.40, mean_diameter * 0.08):
            return None
        if radial_error > circular_tolerance:
            return None
        diameter = mean_radius * 2.0

    z_values = record["z_values"]
    inferred_depth = max(z_values) - min(z_values) if len(z_values) > 1 else 0.0
    return {
        "x": center_x,
        "y": center_y,
        "diameter_mm": diameter,
        "depth_mm": max(0.0, inferred_depth),
        "points": points,
    }


def _planar_geometry_from_faces(faces):
    candidates_by_signature = {}

    for face in faces:
        if not _is_plane_face(face):
            continue
        valid_wires = []
        for wire in getattr(face, "Wires", []):
            try:
                area = _wire_projection_area(wire)
                if area <= 1e-6:
                    continue
                points = _extract_points_from_wire(wire)
                valid_wires.append((area, wire, points))
            except Exception:
                continue
        if not valid_wires:
            continue

        valid_wires.sort(key=lambda item: item[0], reverse=True)
        for wire_index, (_area, wire, points) in enumerate(valid_wires):
            signature = _contour_signature(points)
            z_value = _average_z_from_wire(wire)
            record = candidates_by_signature.setdefault(
                signature,
                {
                    "points": points,
                    "top_z": z_value,
                    "z_values": [],
                    "seen_as_inner": False,
                    "circle": None,
                },
            )
            record["z_values"].append(z_value)
            record["seen_as_inner"] = record["seen_as_inner"] or wire_index > 0
            wire_circle = _circle_from_wire(wire)
            if wire_circle is not None:
                record["circle"] = wire_circle
            if z_value > record["top_z"]:
                record["top_z"] = z_value
                record["points"] = points

    contours = []
    holes = []
    for record in candidates_by_signature.values():
        if record["seen_as_inner"]:
            hole = _circular_hole_from_record(record)
            if hole is not None:
                holes.append(hole)
            continue
        contours.append(record["points"])

    return {
        "contours": contours,
        "holes": holes,
    }


def _planar_geometry_from_shape(shape):
    return _planar_geometry_from_faces(getattr(shape, "Faces", []))


def _extract_points_from_face(face):
    """Extrai pontos 2D de uma face plana selecionada."""
    if not _is_plane_face(face):
        raise ValueError("A face selecionada não é plana. Selecione uma face plana do modelo 3D.")

    wire = _outer_wire_from_face(face)
    if _wire_projection_area(wire) <= 1e-6:
        raise ValueError(
            "A face selecionada não gera um contorno útil em XY. "
            "Selecione a face superior da chapa ou o objeto inteiro da chapa."
        )

    return _extract_points_from_wire(wire)


def _extract_points_from_sketch(obj):
    """Extrai pontos 2D de um Sketch."""
    shape = obj.Shape
    wires = shape.Wires
    if not wires:
        raise ValueError("O Sketch selecionado não contém contornos.")

    return _extract_points_from_wire(max(wires, key=_wire_projection_area))


def _extract_contours_from_sketch(obj):
    shape = obj.Shape
    wires = getattr(shape, "Wires", [])
    if not wires:
        return []

    return _extract_contours_from_wires(wires)


def _external_edges_from_sketch(obj):
    edges = []
    document = getattr(obj, "Document", None) or FreeCAD.ActiveDocument
    for reference in list(getattr(obj, "ExternalGeometry", []) or []):
        if not isinstance(reference, (tuple, list)) or len(reference) < 2:
            continue
        referenced_object = reference[0]
        if isinstance(referenced_object, str) and document is not None:
            referenced_object = document.getObject(referenced_object)
        if referenced_object is None:
            continue
        sub_names = reference[1]
        if isinstance(sub_names, str):
            sub_names = [sub_names]
        for sub_name in sub_names or []:
            sub_object = None
            try:
                sub_object = referenced_object.getSubObject(str(sub_name))
            except Exception:
                pass
            if sub_object is None:
                try:
                    sub_object = referenced_object.Shape.getElement(str(sub_name))
                except Exception:
                    continue
            if getattr(sub_object, "ShapeType", None) == "Edge":
                edges.append(sub_object)
    return edges


def _extract_geometry_from_sketch(obj):
    wires = list(getattr(obj.Shape, "Wires", []) or [])

    contours = []
    holes = []
    for wire in wires:
        if not wire.isClosed():
            continue
        try:
            points = _extract_points_from_wire(wire)
        except Exception:
            continue
        circle = _circle_from_wire(wire)
        if circle is None:
            contours.append(points)
            continue
        holes.append(
            {
                "x": circle["x"],
                "y": circle["y"],
                "diameter_mm": circle["diameter_mm"],
                "depth_mm": 0.0,
                "points": points,
            }
        )

    external_edges = _external_edges_from_sketch(obj)
    external_contour_edges = []
    for edge in external_edges:
        vectors = _edge_vectors(edge)
        circle = _circle_from_edge(edge)
        is_closed = (
            len(vectors) >= 2
            and _vector_distance(vectors[0], vectors[-1]) <= 1e-4
        )
        if circle is None or not is_closed:
            external_contour_edges.append(edge)
            continue
        holes.append(
            {
                "x": circle["x"],
                "y": circle["y"],
                "diameter_mm": circle["diameter_mm"],
                "depth_mm": 0.0,
                "points": _clean_xy_points(vectors),
            }
        )
    if external_contour_edges:
        try:
            contours.extend(_selected_edge_contours(external_contour_edges))
        except ValueError:
            pass

    return {
        "contours": _dedupe_contours(contours),
        "holes": _dedupe_holes(holes),
    }


def _extract_points_from_object(obj):
    if not hasattr(obj, "Shape"):
        raise ValueError("O objeto selecionado não possui geometria Shape.")

    shape = obj.Shape
    face = _best_planar_face_from_shape(shape)
    if face is not None:
        return _extract_points_from_face(face)

    return _extract_points_from_sketch(obj)


def _extract_contours_from_object(obj):
    if not hasattr(obj, "Shape"):
        raise ValueError("O objeto selecionado não possui geometria Shape.")

    shape = obj.Shape
    contours = _planar_face_contours_from_shape(shape)
    if contours:
        return contours

    contours = _extract_contours_from_sketch(obj)
    if contours:
        return contours

    raise ValueError("Nenhum contorno fechado útil foi encontrado no objeto selecionado.")


def _extract_geometry_from_object(obj):
    if not hasattr(obj, "Shape"):
        raise ValueError("O objeto selecionado não possui geometria Shape.")

    geometry = _planar_geometry_from_shape(obj.Shape)
    if geometry["contours"]:
        return geometry

    sketch_geometry = _extract_geometry_from_sketch(obj)
    if sketch_geometry["contours"] or sketch_geometry["holes"]:
        return sketch_geometry

    raise ValueError("Nenhum contorno fechado útil foi encontrado no objeto selecionado.")


def _get_first_selected_face():
    for selection in FreeCADGui.Selection.getSelectionEx():
        for sub_object in selection.SubObjects:
            if getattr(sub_object, "ShapeType", None) == "Face":
                return sub_object
    return None


def _get_selected_faces():
    faces = []
    for selection in FreeCADGui.Selection.getSelectionEx():
        for sub_object in selection.SubObjects:
            if getattr(sub_object, "ShapeType", None) == "Face":
                faces.append(sub_object)
    return faces


def _get_selected_edges():
    edges = []
    has_edge_subelements = False
    for selection in FreeCADGui.Selection.getSelectionEx():
        for sub_object in selection.SubObjects:
            if getattr(sub_object, "ShapeType", None) != "Edge":
                continue
            has_edge_subelements = True
            edges.append(sub_object)
    return edges, has_edge_subelements


def _selected_edge_contours(edges, tolerance=1e-4):
    remaining = [
        vectors
        for vectors in (_edge_vectors(edge) for edge in edges)
        if len(vectors) >= 2
    ]
    contours = []
    open_chains = []

    while remaining:
        chain = list(remaining.pop(0))
        progress = True
        while remaining and progress:
            progress = False
            for index, candidate in enumerate(remaining):
                if _vector_distance(chain[-1], candidate[0]) <= tolerance:
                    chain.extend(candidate[1:])
                elif _vector_distance(chain[-1], candidate[-1]) <= tolerance:
                    chain.extend(list(reversed(candidate[:-1])))
                elif _vector_distance(chain[0], candidate[-1]) <= tolerance:
                    chain = list(candidate[:-1]) + chain
                elif _vector_distance(chain[0], candidate[0]) <= tolerance:
                    chain = list(reversed(candidate[1:])) + chain
                else:
                    continue
                remaining.pop(index)
                progress = True
                break

        if _vector_distance(chain[0], chain[-1]) <= tolerance:
            points = _clean_xy_points(chain)
            if abs(_polygon_area_2d(points)) > 1e-6:
                contours.append(points)
        else:
            open_chains.append(chain)

    if open_chains:
        raise ValueError(
            "As arestas selecionadas não formam um contorno fechado. "
            "Selecione todas as arestas do espaço que será usinado."
        )
    if not contours:
        raise ValueError(
            "As arestas selecionadas não formam uma área fechada útil."
        )
    return _dedupe_contours(contours)


def _selected_edge_geometry(edges, tolerance=1e-4):
    holes = []
    contour_edges = []
    for edge in edges:
        vectors = _edge_vectors(edge)
        circle = _circle_from_edge(edge)
        is_closed = (
            len(vectors) >= 2
            and _vector_distance(vectors[0], vectors[-1]) <= tolerance
        )
        if circle is None or not is_closed:
            contour_edges.append(edge)
            continue
        points = _clean_xy_points(vectors)
        holes.append(
            {
                "x": circle["x"],
                "y": circle["y"],
                "diameter_mm": circle["diameter_mm"],
                "depth_mm": 0.0,
                "points": points,
            }
        )

    contours = (
        _selected_edge_contours(contour_edges, tolerance=tolerance)
        if contour_edges
        else []
    )
    return {
        "contours": contours,
        "holes": _dedupe_holes(holes),
    }


def _dedupe_contours(contours):
    deduped = []
    seen = set()
    for points in contours:
        signature = _contour_signature(points)
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(points)
    return deduped


def _dedupe_holes(holes):
    deduped = []
    seen = set()
    for hole in holes:
        signature = (
            round(float(hole["x"]), 3),
            round(float(hole["y"]), 3),
            round(float(hole.get("diameter_mm", 0.0)), 3),
        )
        if signature in seen:
            continue
        seen.add(signature)
        deduped.append(hole)
    return deduped


def _panelnest_managed_type(obj):
    return str(getattr(obj, PANELNEST_MANAGED_PROPERTY, "") or "")


def _document_objects(obj):
    document = getattr(obj, "Document", None) or FreeCAD.ActiveDocument
    return list(getattr(document, "Objects", []) or [])


def _panelnest_cam_objects(objects):
    return [
        candidate
        for candidate in objects
        if _panelnest_managed_type(candidate) == PANELNEST_CAM_TYPE
    ]


def _panelnest_cam_for_selection(obj):
    managed_type = _panelnest_managed_type(obj)
    is_cam_group = str(getattr(obj, "Name", "") or "") == "PanelNest_CAM_Chapas"
    if managed_type == PANELNEST_CAM_TYPE:
        return [obj]
    if is_cam_group:
        return _panelnest_cam_objects(list(getattr(obj, "Group", []) or []))

    managed_container_types = {
        "layout_root",
        "layout_group",
        "layout_sheet_container",
        "layout_sheet",
    }
    if managed_type not in managed_container_types:
        return None

    candidates = _panelnest_cam_objects(_document_objects(obj))
    name = str(getattr(obj, "Name", "") or "")

    if managed_type in {"layout_sheet_container", "layout_sheet"}:
        match = re.search(r"(\d{2})_(\d{2})$", name)
        if match:
            expected_name = f"PanelNestCAMChapa{match.group(1)}_{match.group(2)}"
            candidates = [
                candidate
                for candidate in candidates
                if str(getattr(candidate, "Name", "") or "") == expected_name
            ]
    elif managed_type == "layout_group":
        match = re.search(r"(\d{2})$", name)
        if match:
            expected_prefix = f"PanelNestCAMChapa{match.group(1)}_"
            candidates = [
                candidate
                for candidate in candidates
                if str(getattr(candidate, "Name", "") or "").startswith(expected_prefix)
            ]

    if not candidates:
        raise ValueError(
            "O layout selecionado não possui uma geometria CAM correspondente. "
            "Gere novamente o Layout PanelNest e tente outra vez."
        )
    if len(candidates) > 1:
        raise ValueError(
            "O item selecionado contém mais de uma chapa. "
            "Abra 'PanelNest — CAM Chapas' e selecione apenas a 'CAM Chapa' que será usinada."
        )
    return candidates


def resolve_selection_objects(selection):
    """Resolve tree selections to the geometry that should actually be used.

    In particular, a PanelNest layout/container resolves to its managed
    ``CAM Chapa`` object, never to the sheet base.  Ordinary objects are
    returned unchanged and duplicate resolutions are removed.
    """

    resolved = []
    seen = set()

    for obj in selection:
        cam_objects = _panelnest_cam_for_selection(obj)
        for candidate in cam_objects if cam_objects is not None else [obj]:
            name = str(getattr(candidate, "Name", "") or id(candidate))
            if name in seen:
                continue
            seen.add(name)
            resolved.append(candidate)

    return resolved


# Backward-compatible private alias for callers from older WoodCAM copies.
_resolve_selection_objects = resolve_selection_objects


def get_selected_geometry():
    if not FreeCAD.GuiUp:
        raise RuntimeError("Este script deve ser executado dentro do FreeCAD com a interface gráfica ativa.")

    contours = []
    holes = []
    selected_faces = _get_selected_faces()
    if selected_faces:
        face_geometry = _planar_geometry_from_faces(selected_faces)
        contours.extend(face_geometry["contours"])
        holes.extend(face_geometry["holes"])

    if contours:
        return {
            "contours": _dedupe_contours(contours),
            "holes": _dedupe_holes(holes),
        }

    selected_edges, has_edge_subelements = _get_selected_edges()
    if has_edge_subelements:
        selected_edge_geometry = _selected_edge_geometry(selected_edges)
        if (
            not selected_edge_geometry["contours"]
            and not selected_edge_geometry["holes"]
        ):
            raise ValueError(
                "Nenhuma geometria fechada útil foi encontrada nas arestas selecionadas."
            )
        return selected_edge_geometry

    selection = FreeCADGui.Selection.getSelection()
    if not selection:
        raise ValueError("Selecione um Sketch, uma face plana ou objetos 3D no FreeCAD.")

    for obj in resolve_selection_objects(selection):
        geometry = _extract_geometry_from_object(obj)
        contours.extend(geometry["contours"])
        holes.extend(geometry["holes"])

    contours = _dedupe_contours(contours)
    holes = _dedupe_holes(holes)
    if not contours and not holes:
        raise ValueError(
            "Nenhum contorno fechado ou furo circular foi encontrado na seleção."
        )

    return {
        "contours": contours,
        "holes": holes,
    }


def get_selected_contours():
    return get_selected_geometry()["contours"]


def get_selected_holes():
    return get_selected_geometry()["holes"]


def get_selected_wire_points():
    return get_selected_contours()[0]
