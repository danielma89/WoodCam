"""FreeCAD boundary for transient relief previews and persistent mesh objects."""

from __future__ import annotations

import base64
from dataclasses import asdict
from io import BytesIO
import json
import math
from typing import Any

from .heightmap import (
    HeightMapData,
    ReliefMesh,
    ReliefOptions,
    build_relief_mesh,
    heightmap_png,
)


RELIEF_GROUP_NAME = "WoodCAM2D_Reliefs"
RELIEF_TYPE = "woodcam_heightmap_relief_v1"
MAX_VISUAL_RESOLUTION = 384


def _configure_relief_material(material) -> None:
    """Use a neutral studio-clay material so shallow slopes remain readable.

    Keeping the RGB channels close is intentional: a saturated material can be
    mistaken for FreeCAD's orange selection highlight and hides the height
    transitions that the operator is trying to judge.
    """

    material.ambientColor = (0.11, 0.12, 0.13)
    material.diffuseColor = (0.50, 0.53, 0.57)
    material.specularColor = (0.32, 0.34, 0.38)
    material.shininess = 0.34


def _relief_key_light(coin):
    light = coin.SoDirectionalLight()
    light.direction = (-0.38, -0.52, -1.0)
    light.color = (1.0, 0.98, 0.95)
    light.intensity = 0.48
    return light


def _visual_relief_mesh(mesh: ReliefMesh) -> ReliefMesh:
    """Compact the closed CAM preview into a light Coin display mesh.

    ``build_relief_mesh`` keeps a fully tessellated underside because that is
    useful for interchange/export.  The FreeCAD viewport only needs the top,
    the perimeter skirt and two triangles for the flat base.  Reusing that
    representation roughly halves vertices/faces without changing the height
    map consumed by CAM.
    """

    columns, rows = int(mesh.columns), int(mesh.rows)
    top_count = columns * rows
    if columns < 2 or rows < 2 or len(mesh.vertices) < top_count:
        return mesh
    top = list(mesh.vertices[:top_count])
    underside = list(mesh.vertices[top_count:])
    base_z = min((point[2] for point in underside), default=0.0)
    triangles = []
    for row in range(rows - 1):
        for column in range(columns - 1):
            a = row * columns + column
            b = a + 1
            c = a + columns
            d = c + 1
            triangles.extend(((a, c, b), (b, c, d)))

    perimeter = []
    perimeter.extend(range(columns))
    perimeter.extend(row * columns + columns - 1 for row in range(1, rows))
    perimeter.extend(
        (rows - 1) * columns + column
        for column in range(columns - 2, -1, -1)
    )
    perimeter.extend(row * columns for row in range(rows - 2, 0, -1))
    bottom_by_top = {}
    vertices = list(top)
    for top_index in perimeter:
        x, y, _z = top[top_index]
        bottom_by_top[top_index] = len(vertices)
        vertices.append((x, y, base_z))
    for index, current in enumerate(perimeter):
        following = perimeter[(index + 1) % len(perimeter)]
        bottom_current = bottom_by_top[current]
        bottom_following = bottom_by_top[following]
        triangles.extend(
            (
                (current, bottom_current, following),
                (following, bottom_current, bottom_following),
            )
        )

    top_left = 0
    top_right = columns - 1
    bottom_right = (rows - 1) * columns + columns - 1
    bottom_left = (rows - 1) * columns
    a = bottom_by_top[top_left]
    b = bottom_by_top[top_right]
    c = bottom_by_top[bottom_right]
    d = bottom_by_top[bottom_left]
    triangles.extend(((a, b, c), (a, c, d)))
    return ReliefMesh(tuple(vertices), tuple(triangles), columns, rows)


def _normalized_cross(first, second, third):
    ax = second[0] - first[0]
    ay = second[1] - first[1]
    az = second[2] - first[2]
    bx = third[0] - first[0]
    by = third[1] - first[1]
    bz = third[2] - first[2]
    normal = (
        ay * bz - az * by,
        az * bx - ax * bz,
        ax * by - ay * bx,
    )
    length = math.sqrt(sum(value * value for value in normal))
    if length <= 1e-15:
        return (0.0, 0.0, 1.0)
    return tuple(value / length for value in normal)


def _normalized_vector(vector):
    length = math.sqrt(sum(value * value for value in vector))
    if length <= 1e-15:
        return (0.0, 0.0, 1.0)
    return tuple(value / length for value in vector)


def _preview_normals(mesh: ReliefMesh):
    """Return smooth top normals and flat base/side normal indices for Coin."""

    columns, rows = mesh.columns, mesh.rows
    top_count = columns * rows
    normals = []
    for row in range(rows):
        upper = max(0, row - 1)
        lower = min(rows - 1, row + 1)
        for column in range(columns):
            left = max(0, column - 1)
            right = min(columns - 1, column + 1)
            left_point = mesh.vertices[row * columns + left]
            right_point = mesh.vertices[row * columns + right]
            upper_point = mesh.vertices[upper * columns + column]
            lower_point = mesh.vertices[lower * columns + column]
            tangent_x = tuple(
                right_point[index] - left_point[index] for index in range(3)
            )
            tangent_y = tuple(
                upper_point[index] - lower_point[index] for index in range(3)
            )
            normals.append(
                _normalized_vector(
                    (
                        tangent_x[1] * tangent_y[2] - tangent_x[2] * tangent_y[1],
                        tangent_x[2] * tangent_y[0] - tangent_x[0] * tangent_y[2],
                        tangent_x[0] * tangent_y[1] - tangent_x[1] * tangent_y[0],
                    )
                )
            )
    underside_index = len(normals)
    normals.append((0.0, 0.0, -1.0))
    normal_indices = []
    for triangle in mesh.triangles:
        if all(index < top_count for index in triangle):
            indices = triangle
        elif all(index >= top_count for index in triangle):
            indices = (underside_index,) * 3
        else:
            side_index = len(normals)
            normals.append(
                _normalized_cross(
                    mesh.vertices[triangle[0]],
                    mesh.vertices[triangle[1]],
                    mesh.vertices[triangle[2]],
                )
            )
            indices = (side_index,) * 3
        normal_indices.extend((indices[0], indices[1], indices[2], -1))
    return normals, normal_indices


class FreeCADReliefPreview:
    """Coin scene-graph overlay that never creates a document object."""

    def __init__(self, gui_document: Any = None) -> None:
        self.gui_document = gui_document
        self._scene = None
        self._root = None

    def update(self, mesh: ReliefMesh) -> None:
        self.clear()
        try:
            import FreeCADGui
            from pivy import coin

            gui_document = self.gui_document or FreeCADGui.ActiveDocument
            if gui_document is None:
                return
            scene = gui_document.ActiveView.getSceneGraph()
            root = coin.SoSeparator()
            pick_style = coin.SoPickStyle()
            pick_style.style = coin.SoPickStyle.UNPICKABLE
            material = coin.SoMaterial()
            _configure_relief_material(material)
            key_light = _relief_key_light(coin)
            display_mesh = _visual_relief_mesh(mesh)
            coordinates = coin.SoCoordinate3()
            coordinates.point.setValues(
                0, len(display_mesh.vertices), list(display_mesh.vertices)
            )
            normal_values, normal_indices = _preview_normals(display_mesh)
            normal_node = coin.SoNormal()
            normal_node.vector.setValues(0, len(normal_values), normal_values)
            normal_binding = coin.SoNormalBinding()
            normal_binding.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
            faces = coin.SoIndexedFaceSet()
            indices = []
            for triangle in display_mesh.triangles:
                indices.extend((triangle[0], triangle[1], triangle[2], -1))
            faces.coordIndex.setValues(0, len(indices), indices)
            faces.normalIndex.setValues(0, len(normal_indices), normal_indices)
            hints = coin.SoShapeHints()
            hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
            hints.creaseAngle = 1.45
            for node in (
                pick_style, key_light, material, hints, coordinates, normal_node,
                normal_binding, faces
            ):
                root.addChild(node)
            scene.addChild(root)
            self._scene = scene
            self._root = root
        except Exception:
            self._scene = None
            self._root = None

    def clear(self) -> None:
        if self._scene is not None and self._root is not None:
            try:
                self._scene.removeChild(self._root)
            except Exception:
                pass
        self._scene = None
        self._root = None


class LightweightReliefFeature:
    """Dados do relevo sem uma segunda cópia topológica no documento."""

    def __init__(self, obj=None):
        if obj is not None:
            obj.Proxy = self

    def execute(self, _obj):
        return None

    def dumps(self):
        return None

    def loads(self, _state):
        return None


class LightweightReliefViewProvider:
    """Desenha a prévia diretamente em Coin, reconstruída do PNG salvo."""

    def __init__(self, view_object=None):
        self.root = None
        self.material = None
        self.coordinates = None
        self.normals = None
        self.normal_binding = None
        self.faces = None
        self.transform = None
        self.pick_style = None
        self.key_light = None
        self.last_error = ""
        if view_object is not None:
            view_object.Proxy = self

    def attach(self, view_object):
        from pivy import coin

        self.root = coin.SoSeparator()
        self.transform = coin.SoTransform()
        self.pick_style = coin.SoPickStyle()
        self.pick_style.style = coin.SoPickStyle.BOUNDING_BOX
        self.material = coin.SoMaterial()
        self.key_light = _relief_key_light(coin)
        hints = coin.SoShapeHints()
        hints.vertexOrdering = coin.SoShapeHints.COUNTERCLOCKWISE
        hints.creaseAngle = 1.45
        self.coordinates = coin.SoCoordinate3()
        self.normals = coin.SoNormal()
        self.normal_binding = coin.SoNormalBinding()
        self.normal_binding.value = coin.SoNormalBinding.PER_VERTEX_INDEXED
        self.faces = coin.SoIndexedFaceSet()
        for node in (
            self.transform,
            self.pick_style,
            self.key_light,
            self.material,
            hints,
            self.coordinates,
            self.normals,
            self.normal_binding,
            self.faces,
        ):
            self.root.addChild(node)
        view_object.addDisplayMode(self.root, "Relevo")
        self._rebuild(view_object.Object)

    def _heightmap_data(self, obj):
        from PIL import Image

        encoded = str(getattr(obj, "HeightMapPNGBase64", "") or "")
        options = ReliefOptions(
            **json.loads(str(getattr(obj, "ReliefOptionsJSON", "{}") or "{}"))
        )
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            grayscale = image.convert("L")
            width, height = grayscale.size
            pixels = grayscale.tobytes()
        return HeightMapData(
            width,
            height,
            pixels,
            options,
            str(getattr(obj, "SourceImageName", "")),
            str(getattr(obj, "SourceSHA256", "")),
            str(getattr(obj, "GenerationMethod", "bitmap") or "bitmap"),
            str(getattr(obj, "GeneratorMetadataJSON", "{}") or "{}"),
        )

    def _rebuild(self, obj):
        if self.coordinates is None or obj is None:
            return
        try:
            from pivy import coin

            maximum = min(
                MAX_VISUAL_RESOLUTION,
                int(getattr(obj, "VisualResolution", 256) or 256),
            )
            mesh = _visual_relief_mesh(
                build_relief_mesh(self._heightmap_data(obj), max_grid=maximum)
            )
            normal_values, normal_indices = _preview_normals(mesh)
            indices = []
            for triangle in mesh.triangles:
                indices.extend((triangle[0], triangle[1], triangle[2], -1))
            self.coordinates.point.setNum(0)
            self.normals.vector.setNum(0)
            self.faces.coordIndex.setNum(0)
            self.faces.normalIndex.setNum(0)
            self.coordinates.point.setValues(0, len(mesh.vertices), list(mesh.vertices))
            self.normals.vector.setValues(0, len(normal_values), normal_values)
            self.faces.coordIndex.setValues(0, len(indices), indices)
            self.faces.normalIndex.setValues(0, len(normal_indices), normal_indices)
            _configure_relief_material(self.material)
            placement = getattr(obj, "Placement", None)
            base = getattr(placement, "Base", None)
            self.transform.translation = (
                float(getattr(base, "x", 0.0) or 0.0),
                float(getattr(base, "y", 0.0) or 0.0),
                float(getattr(base, "z", 0.0) or 0.0),
            )
            rotation = getattr(placement, "Rotation", None)
            axis = getattr(rotation, "Axis", None)
            self.transform.rotation.setValue(
                coin.SbVec3f(
                    float(getattr(axis, "x", 0.0) or 0.0),
                    float(getattr(axis, "y", 0.0) or 0.0),
                    float(getattr(axis, "z", 1.0) or 1.0),
                ),
                float(getattr(rotation, "Angle", 0.0) or 0.0),
            )
        except Exception as error:
            self.last_error = str(error)
            self.coordinates.point.setNum(0)
            self.faces.coordIndex.setNum(0)

    def updateData(self, obj, prop):
        if prop in {
            "HeightMapPNGBase64",
            "ReliefOptionsJSON",
            "VisualResolution",
            "Placement",
        }:
            self._rebuild(obj)

    def getDisplayModes(self, _view_object):
        return ["Relevo"]

    def getDefaultDisplayMode(self):
        return "Relevo"

    def setDisplayMode(self, mode):
        return mode

    def onChanged(self, _view_object, _prop):
        return None

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def _unique_name(document: Any, base: str) -> str:
    if document.getObject(base) is None:
        return base
    index = 2
    while document.getObject("%s_%02d" % (base, index)) is not None:
        index += 1
    return "%s_%02d" % (base, index)


def _add_property(obj: Any, property_type: str, name: str, group: str, value: Any) -> None:
    if not hasattr(obj, name):
        obj.addProperty(property_type, name, group)
    setattr(obj, name, value)


def create_persistent_relief(
    document: Any,
    data: HeightMapData,
    mesh_data: ReliefMesh,
    *,
    label: str | None = None,
) -> Any:
    """Create one undoable persistent relief and retain its exact height map."""

    if document is None:
        raise RuntimeError("Abra um documento FreeCAD antes de criar o relevo.")
    import FreeCAD

    try:
        if int(getattr(document, "UndoMode", 0) or 0) == 0:
            document.UndoMode = 1
    except Exception:
        pass
    opened = False
    try:
        document.openTransaction("WoodCAM 3D — Criar relevo por imagem")
        opened = True
        group = document.getObject(RELIEF_GROUP_NAME)
        if group is None:
            group = document.addObject("App::DocumentObjectGroup", RELIEF_GROUP_NAME)
            group.Label = "WoodCAM 3D — Relevos"
        name = _unique_name(document, "WoodCAM_ImageRelief")
        obj = document.addObject("App::FeaturePython", name)
        obj.Label = label or "Relevo — %s" % data.source_name
        LightweightReliefFeature(obj)
        _add_property(obj, "App::PropertyPlacement", "Placement", "Base", FreeCAD.Placement())
        group.addObject(obj)
        properties_group = "WoodCAM 3D — Relevo"
        _add_property(obj, "App::PropertyString", "WoodCAMReliefType", properties_group, RELIEF_TYPE)
        _add_property(obj, "App::PropertyString", "SourceImageName", properties_group, data.source_name)
        _add_property(obj, "App::PropertyString", "SourceSHA256", properties_group, data.source_sha256)
        _add_property(obj, "App::PropertyString", "GenerationMethod", properties_group, data.generator)
        _add_property(
            obj,
            "App::PropertyString",
            "GeneratorMetadataJSON",
            properties_group,
            data.generator_metadata_json,
        )
        _add_property(
            obj,
            "App::PropertyString",
            "HeightMapPNGBase64",
            properties_group,
            base64.b64encode(heightmap_png(data)).decode("ascii"),
        )
        _add_property(
            obj,
            "App::PropertyString",
            "ReliefOptionsJSON",
            properties_group,
            json.dumps(asdict(data.options), sort_keys=True, separators=(",", ":")),
        )
        _add_property(obj, "App::PropertyLength", "Width", properties_group, float(data.options.width_mm))
        _add_property(obj, "App::PropertyLength", "Height", properties_group, float(data.options.height_mm))
        _add_property(
            obj,
            "App::PropertyLength",
            "ReliefHeight",
            properties_group,
            float(data.options.relief_height_mm),
        )
        _add_property(
            obj,
            "App::PropertyLength",
            "BaseThickness",
            properties_group,
            float(data.options.base_thickness_mm),
        )
        _add_property(obj, "App::PropertyInteger", "MapWidthPixels", properties_group, int(data.width_px))
        _add_property(obj, "App::PropertyInteger", "MapHeightPixels", properties_group, int(data.height_px))
        _add_property(obj, "App::PropertyInteger", "MeshColumns", properties_group, int(mesh_data.columns))
        _add_property(obj, "App::PropertyInteger", "MeshRows", properties_group, int(mesh_data.rows))
        _add_property(
            obj,
            "App::PropertyInteger",
            "VisualResolution",
            properties_group,
            int(min(MAX_VISUAL_RESOLUTION, max(mesh_data.columns, mesh_data.rows))),
        )
        _add_property(
            obj,
            "App::PropertyInteger",
            "VisualFacetCount",
            properties_group,
            int(mesh_data.facet_count),
        )
        document.recompute()
        document.commitTransaction()
        opened = False
    except Exception:
        if opened:
            try:
                document.abortTransaction()
            except Exception:
                pass
        raise
    view_object = getattr(obj, "ViewObject", None)
    if view_object is not None:
        try:
            LightweightReliefViewProvider(view_object)
            view_object.DisplayMode = "Relevo"
            if hasattr(view_object, "SelectionStyle"):
                view_object.SelectionStyle = "BoundBox"
        except Exception:
            pass
    return obj


__all__ = [
    "FreeCADReliefPreview",
    "LightweightReliefFeature",
    "LightweightReliefViewProvider",
    "RELIEF_GROUP_NAME",
    "RELIEF_TYPE",
    "create_persistent_relief",
]
