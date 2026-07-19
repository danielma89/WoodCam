"""Leitura segura de Sketches 2D antes de organizar ou usinar."""

import Part

from geometry_reader import _extract_points_from_wire, _polygon_area_2d


def _inside(point, contour):
    x, y = point
    inside = False
    for index, current in enumerate(contour):
        previous = contour[index - 1]
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
    return inside


def _endpoint_position(sketch, edge_index, point, tolerance=1e-5):
    """Retorna 1 ou 2 quando point coincide com início/fim da geometria."""
    try:
        geometry = sketch.Geometry[edge_index - 1]
        for position, candidate in ((1, geometry.StartPoint), (2, geometry.EndPoint)):
            distance = ((candidate.x - point.x) ** 2 + (candidate.y - point.y) ** 2) ** 0.5
            if distance <= tolerance:
                return position
    except Exception:
        pass
    return None


def _shape_edge_to_geometry_index(sketch, shape_edge_index):
    """Converte índice de Edge da Shape para índice de Sketch.Geometry."""
    try:
        target_edge = sketch.Shape.Edges[shape_edge_index]
        for geometry_index in range(len(sketch.Geometry)):
            geometry_shape = sketch.getGeometryShape(geometry_index)
            for edge in getattr(geometry_shape, "Edges", []) or []:
                try:
                    if edge.isSame(target_edge):
                        return geometry_index
                except Exception:
                    if edge == target_edge:
                        return geometry_index
    except Exception:
        pass
    return shape_edge_index


def nearest_structure_connections(sketch):
    """Pontos de extremidade isolados e a geometria mais próxima de cada um."""
    connections = []
    for wire in list(getattr(getattr(sketch, "Shape", None), "Wires", []) or []):
        if wire.isClosed() or len(wire.Edges) < 1:
            continue
        shape_edges = list(getattr(sketch.Shape, "Edges", []) or [])
        wire_indices = []
        for edge in wire.Edges:
            for index, shape_edge in enumerate(shape_edges):
                try:
                    matches = edge.isSame(shape_edge)
                except Exception:
                    matches = edge == shape_edge
                if matches:
                    wire_indices.append(index)
                    break
        vertices = list(getattr(wire, "OrderedVertexes", []) or getattr(wire, "Vertexes", []) or [])
        if len(vertices) < 2 or not wire_indices:
            continue
        for endpoint_index, vertex in enumerate((vertices[0], vertices[-1])):
            point = vertex.Point
            source_shape_edge = wire_indices[0 if endpoint_index == 0 else -1]
            source_geometry = _shape_edge_to_geometry_index(sketch, source_shape_edge)
            closest = None
            for target_index, target in enumerate(shape_edges):
                if target_index == source_shape_edge:
                    continue
                try:
                    distance = float(target.distToShape(Part.Vertex(point))[0])
                except Exception:
                    continue
                if closest is None or distance < closest["distance"]:
                    closest = {"target_index": target_index, "distance": distance}
            if closest is not None:
                target_geometry = _shape_edge_to_geometry_index(
                    sketch, closest["target_index"]
                )
                closest.update(
                    source_index=source_geometry,
                    source_position=_endpoint_position(
                        sketch, source_geometry + 1, point
                    ) or (1 if endpoint_index == 0 else 2),
                    target_geometry=target_geometry,
                    target_position=_endpoint_position(
                        sketch, target_geometry + 1, point
                    ),
                )
                connections.append(closest)
    return connections


def diagnose_sketch(sketch, work_area=None):
    """Retorna contornos, vetores abertos e possíveis peças de um Sketch."""
    wires = list(getattr(getattr(sketch, "Shape", None), "Wires", []) or [])
    closed = []
    open_count = 0
    open_edge_indices = []
    open_endpoints = []
    open_gaps = []
    nearest_connections = []
    duplicates = 0
    signatures = set()
    for wire in wires:
        if not wire.isClosed():
            open_count += 1
            wire_edge_indices = []
            vertices = list(getattr(wire, "OrderedVertexes", []) or getattr(wire, "Vertexes", []) or [])
            for edge in wire.Edges:
                for index, shape_edge in enumerate(sketch.Shape.Edges, start=1):
                    try:
                        matches = edge.isSame(shape_edge)
                    except Exception:
                        matches = edge == shape_edge
                    if matches:
                        wire_edge_indices.append(index)
                        break
            if len(vertices) >= 2:
                start = vertices[0].Point
                end = vertices[-1].Point
                open_gaps.append(
                    (
                        (float(start.x), float(start.y), float(start.z)),
                        (float(end.x), float(end.y), float(end.z)),
                    )
                )
                for vertex in (vertices[0], vertices[-1]):
                    point = vertex.Point
                    try:
                        placement = sketch.getGlobalPlacement()
                    except Exception:
                        placement = getattr(sketch, "Placement", None)
                    if placement is not None:
                        try:
                            point = placement.multVec(point)
                        except Exception:
                            pass
                    open_endpoints.append((float(point.x), float(point.y), float(point.z)))
                for endpoint_index, point in enumerate((start, end), start=1):
                    source_edge = wire_edge_indices[0 if endpoint_index == 1 else -1]
                    closest = None
                    for edge_index, candidate in enumerate(sketch.Shape.Edges, start=1):
                        # A própria aresta terminal sempre tem distância zero,
                        # mas outras arestas da mesma cadeia podem ser o alvo
                        # correto (por exemplo, uma ponta que encosta numa
                        # linha vertical do mesmo perfil).
                        if edge_index == source_edge:
                            continue
                        try:
                            distance = float(candidate.distToShape(Part.Vertex(point))[0])
                        except Exception:
                            continue
                        if closest is None or distance < closest["distance"]:
                            closest = {"edge": edge_index, "distance": distance}
                    if closest is not None:
                        closest["endpoint"] = endpoint_index
                        closest["source_edge"] = source_edge
                        closest["source_position"] = _endpoint_position(
                            sketch, source_edge, point
                        )
                        closest["target_position"] = _endpoint_position(
                            sketch, closest["edge"], point
                        )
                        nearest_connections.append(closest)
            for edge in wire.Edges:
                for index, shape_edge in enumerate(sketch.Shape.Edges, start=1):
                    try:
                        if edge.isSame(shape_edge):
                            open_edge_indices.append(index)
                            break
                    except Exception:
                        if edge == shape_edge:
                            open_edge_indices.append(index)
                            break
            continue
        try:
            points = _extract_points_from_wire(wire)
        except Exception:
            open_count += 1
            continue
        if abs(_polygon_area_2d(points)) <= 1e-6:
            continue
        signature = tuple(sorted((round(x, 3), round(y, 3)) for x, y in points))
        if signature in signatures:
            duplicates += 1
            continue
        signatures.add(signature)
        closed.append(points)

    pieces = []
    for contour in closed:
        containers = [other for other in closed if other is not contour and _inside(contour[0], other)]
        if not containers:
            holes = [other for other in closed if other is not contour and _inside(other[0], contour)]
            pieces.append({"contour": contour, "holes": holes})

    outside = []
    if work_area is not None:
        min_x, min_y, max_x, max_y = work_area
        for index, piece in enumerate(pieces, start=1):
            points = piece["contour"]
            if any(x < min_x or x > max_x or y < min_y or y > max_y for x, y in points):
                outside.append(index)

    return {
        "closed_count": len(closed),
        "open_count": open_count,
        "open_edge_indices": sorted(set(open_edge_indices)),
        "open_endpoints": open_endpoints,
        "open_gaps": open_gaps,
        "nearest_connections": nearest_connections,
        "duplicates": duplicates,
        "pieces": pieces,
        "outside": outside,
    }
