"""FreeCADCmd smoke for persistent, undoable image relief meshes."""

from __future__ import annotations

import tempfile
from pathlib import Path

import FreeCAD
from PIL import Image

from woodcam_relief.freecad_adapter import (
    FreeCADReliefPreview,
    LightweightReliefViewProvider,
    RELIEF_GROUP_NAME,
    RELIEF_TYPE,
    create_persistent_relief,
)
from woodcam_relief.heightmap import ReliefOptions, build_relief_mesh, load_heightmap
from woodcam_3d.freecad_adapter import (
    height_field_from_relief_object,
    relief_source_fingerprint,
)


with tempfile.TemporaryDirectory(prefix="woodcam-relief-smoke-") as directory:
    image_path = Path(directory) / "gradient.png"
    image = Image.new("L", (16, 8))
    image.putdata([int((index % 16) / 15.0 * 255) for index in range(128)])
    image.save(image_path)
    data = load_heightmap(
        image_path,
        ReliefOptions(
            width_mm=160,
            height_mm=80,
            relief_height_mm=8,
            base_thickness_mm=2,
            smoothing_radius_px=0,
            mesh_resolution=24,
        ),
    )
    mesh = build_relief_mesh(data)
    document = FreeCAD.newDocument("WoodCAMReliefSmoke")
    from pivy import coin

    class _FakeView:
        def __init__(self):
            self.scene = coin.SoSeparator()

        def getSceneGraph(self):
            return self.scene

    class _FakeGuiDocument:
        def __init__(self):
            self.ActiveView = _FakeView()

    preview = FreeCADReliefPreview(_FakeGuiDocument())
    preview.update(mesh)
    assert preview._root is not None
    preview.clear()
    assert preview._root is None
    obj = create_persistent_relief(document, data, mesh)
    assert obj.WoodCAMReliefType == RELIEF_TYPE
    assert obj.GenerationMethod == "bitmap"
    assert obj.GeneratorMetadataJSON == "{}"
    assert not hasattr(obj, "Mesh")
    assert obj.VisualFacetCount == mesh.facet_count
    class _FakeViewObject:
        def __init__(self, source):
            self.Object = source
            self.Proxy = None
            self.modes = {}

        def addDisplayMode(self, node, name):
            self.modes[name] = node

    fake_view = _FakeViewObject(obj)
    provider = LightweightReliefViewProvider(fake_view)
    provider.attach(fake_view)
    assert "Relevo" in fake_view.modes
    assert provider.coordinates.point.getNum() > 0, provider.last_error
    assert provider.coordinates.point.getNum() < len(mesh.vertices)
    assert provider.pick_style.style.getValue() == coin.SoPickStyle.BOUNDING_BOX
    assert provider.key_light is not None
    assert float(provider.material.shininess[0]) > 0.3
    diffuse = provider.material.diffuseColor[0].getValue()
    assert max(diffuse) - min(diffuse) < 0.10, diffuse
    assert document.getObject(RELIEF_GROUP_NAME) is not None
    assert len(obj.HeightMapPNGBase64) > 20
    field = height_field_from_relief_object(obj, sampling_mm=2.0)
    assert field is not None
    assert field.source_hash == relief_source_fingerprint(obj)
    assert field.columns <= data.width_px
    assert field.rows <= data.height_px
    assert abs(field.source_min_z - 0.0) < 1e-9
    assert abs(field.source_max_z - 10.0) < 1e-9
    name = obj.Name
    output = str(Path(directory) / "relief.FCStd")
    document.saveAs(output)
    document.undo()
    assert document.getObject(name) is None
    document.redo()
    assert document.getObject(name) is not None
    FreeCAD.closeDocument(document.Name)
    reopened = FreeCAD.openDocument(output)
    stored = reopened.getObject(name)
    assert stored is not None
    assert stored.WoodCAMReliefType == RELIEF_TYPE
    assert stored.GenerationMethod == "bitmap"
    assert stored.GeneratorMetadataJSON == "{}"
    assert not hasattr(stored, "Mesh")
    assert stored.VisualFacetCount == mesh.facet_count
    FreeCAD.closeDocument(reopened.Name)

print("WoodCAM image relief smoke: OK")
