"""Adaptador fino entre objetos FreeCAD e o núcleo puro ``woodcam_3d``."""

from __future__ import annotations

import base64
import hashlib
from io import BytesIO
import json

from .surface import MeshData, height_field_from_grayscale


RELIEF_TYPE = "woodcam_heightmap_relief_v1"


def _xyz(point):
    return float(point.x), float(point.y), float(point.z)


def _placed_vertices(obj, points):
    placement = getattr(obj, "Placement", None)
    if placement is None or not hasattr(placement, "multVec"):
        return [_xyz(point) for point in points]
    return [_xyz(placement.multVec(point)) for point in points]


def mesh_data_from_object(obj, linear_deflection=0.08):
    """Extrai ``Mesh::Feature`` ou tessela um ``Shape`` sem modificar a fonte."""
    if obj is None:
        raise ValueError("Selecione um relevo, STL, malha ou sólido 3D.")
    mesh = getattr(obj, "Mesh", None)
    if mesh is not None and int(getattr(mesh, "CountFacets", 0) or 0) > 0:
        points, facets = mesh.Topology
        vertices = _placed_vertices(obj, points)
        triangles = []
        for facet in facets:
            indexes = list(facet)
            if len(indexes) == 3:
                triangles.append(tuple(int(index) for index in indexes))
            elif len(indexes) > 3:
                triangles.extend(
                    (int(indexes[0]), int(indexes[index]), int(indexes[index + 1]))
                    for index in range(1, len(indexes) - 1)
                )
        return MeshData.create(vertices, triangles, getattr(obj, "Name", ""))

    shape = getattr(obj, "Shape", None)
    if shape is not None and not bool(getattr(shape, "isNull", lambda: True)()):
        points, facets = shape.tessellate(float(linear_deflection))
        # ``TopoShape.tessellate`` já devolve os pontos no sistema global da
        # Shape, incluindo o Placement do Part::Feature. Aplicar novamente o
        # Placement aqui duplicava X/Y (por exemplo, uma peça em X=240 virava
        # percurso em X=480), separando visualmente modelo e usinagem.
        # ``Mesh.Topology``, ao contrário, continua local ao Mesh::Feature e
        # por isso usa ``_placed_vertices`` no ramo acima.
        vertices = [_xyz(point) for point in points]
        return MeshData.create(vertices, facets, getattr(obj, "Name", ""))
    raise ValueError(
        "O objeto selecionado não contém uma malha ou Shape 3D utilizável. "
        "Importe o STL no FreeCAD ou selecione o relevo criado por imagem."
    )


def _identity_rotation(placement):
    rotation = getattr(placement, "Rotation", None)
    if rotation is None:
        return True
    try:
        return abs(float(rotation.Angle)) <= 1e-10
    except Exception:
        return False


def relief_source_fingerprint(obj):
    """Fingerprint barato do relevo, sem enumerar sua malha visual."""
    if str(getattr(obj, "WoodCAMReliefType", "")) != RELIEF_TYPE:
        return None
    placement = getattr(obj, "Placement", None)
    base = getattr(placement, "Base", None)
    payload = "|".join(
        (
            str(getattr(obj, "HeightMapPNGBase64", "")),
            str(getattr(obj, "ReliefOptionsJSON", "")),
            str(getattr(obj, "Width", "")),
            str(getattr(obj, "Height", "")),
            str(getattr(obj, "ReliefHeight", "")),
            str(getattr(obj, "BaseThickness", "")),
            "%.9f,%.9f,%.9f" % (
                float(getattr(base, "x", 0.0) or 0.0),
                float(getattr(base, "y", 0.0) or 0.0),
                float(getattr(base, "z", 0.0) or 0.0),
            ),
            str(getattr(getattr(placement, "Rotation", None), "Angle", 0.0)),
        )
    )
    return hashlib.sha256(payload.encode("utf-8", "replace")).hexdigest()


def height_field_from_relief_object(obj, sampling_mm=0.5, max_samples=300_000):
    """Lê o PNG preservado pelo WoodCAM; retorna ``None`` para STL/Shape."""
    fingerprint = relief_source_fingerprint(obj)
    if fingerprint is None:
        return None
    placement = getattr(obj, "Placement", None)
    if placement is not None and not _identity_rotation(placement):
        # Um relevo rotacionado deixa de ser um mapa XY direto. Nesse caso o
        # adaptador genérico de malha continua sendo a opção correta.
        return None
    encoded = str(getattr(obj, "HeightMapPNGBase64", "") or "")
    if not encoded:
        return None
    try:
        from PIL import Image

        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            grayscale = image.convert("L")
            image_width, image_height = grayscale.size
            pixels = grayscale.tobytes()
        options = json.loads(str(getattr(obj, "ReliefOptionsJSON", "{}") or "{}"))
    except Exception as error:
        raise ValueError("Não foi possível ler o mapa de altura preservado: %s" % error)

    base = getattr(placement, "Base", None)
    offset_x = float(getattr(base, "x", 0.0) or 0.0)
    offset_y = float(getattr(base, "y", 0.0) or 0.0)
    offset_z = float(getattr(base, "z", 0.0) or 0.0)
    return height_field_from_grayscale(
        pixels,
        image_width,
        image_height,
        origin_x=float(options.get("origin_x_mm", 0.0)) + offset_x,
        origin_y=float(options.get("origin_y_mm", 0.0)) + offset_y,
        width_mm=float(getattr(obj, "Width")),
        height_mm=float(getattr(obj, "Height")),
        base_z=offset_z,
        base_thickness=float(getattr(obj, "BaseThickness")),
        relief_height=float(getattr(obj, "ReliefHeight")),
        sampling_mm=sampling_mm,
        max_samples=max_samples,
        source_hash=fingerprint,
    )


def selected_surface_object(selection_ex):
    candidates = []
    for record in selection_ex or []:
        obj = getattr(record, "Object", None)
        if obj is None:
            continue
        if str(getattr(obj, "WoodCAMReliefType", "")) == RELIEF_TYPE:
            candidates.append((4, obj))
            continue
        mesh = getattr(obj, "Mesh", None)
        shape = getattr(obj, "Shape", None)
        if mesh is not None and int(getattr(mesh, "CountFacets", 0) or 0) > 0:
            candidates.append((3, obj))
        elif shape is not None and getattr(shape, "Faces", None):
            z_span = float(getattr(getattr(shape, "BoundBox", None), "ZLength", 0.0) or 0.0)
            if z_span > 1e-7:
                candidates.append((2, obj))
    if not candidates:
        raise ValueError(
            "Selecione na árvore ou na vista um relevo, STL, malha ou sólido 3D."
        )
    best_rank = max(rank for rank, _obj in candidates)
    candidates = [obj for rank, obj in candidates if rank == best_rank]
    if len(candidates) > 1:
        raise ValueError("Selecione somente um modelo 3D por operação.")
    return candidates[0]


__all__ = [
    "height_field_from_relief_object",
    "mesh_data_from_object",
    "relief_source_fingerprint",
    "selected_surface_object",
]
