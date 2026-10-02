import unittest

from woodcam_editor.adapters.freecad_store import (
    FreeCADDocumentStore,
    _recompute_derived_feature,
    protect_woodcam_document_view,
)


class _ModernFeature:
    Name = "WoodCAM2D_VectorDrawing"
    DocumentUUID = "doc-modern"
    SchemaVersion = 1
    Revision = 42
    Checksum = "checksum-modern"

    @property
    def GeometryJSON(self):
        raise AssertionError("modern stored_info must not read the full GeometryJSON")


class _LegacyFeature:
    Name = "WoodCAM2D_VectorDrawing"
    DocumentUUID = "doc-legacy"
    SchemaVersion = 1
    Revision = 3
    Checksum = ""
    GeometryJSON = '{"schema_version":1}'


class _StoreProbe:
    def __init__(self, feature):
        self.feature = feature

    def _feature(self):
        return self.feature


class FreeCADStoredInfoTests(unittest.TestCase):
    def test_derived_shape_recomputes_only_its_feature(self):
        feature = object()

        class Document:
            def __init__(self):
                self.calls = []

            def recompute(self, objects=None):
                self.calls.append(objects)

        document = Document()
        _recompute_derived_feature(document, feature)
        self.assertEqual(document.calls, [[feature]])

    def test_old_recompute_overload_remains_supported(self):
        class Document:
            def __init__(self):
                self.calls = 0

            def recompute(self):
                self.calls += 1

        document = Document()
        _recompute_derived_feature(document, object())
        self.assertEqual(document.calls, 1)

    def test_modern_fingerprint_never_materializes_geometry_json(self):
        info = FreeCADDocumentStore.stored_info(_StoreProbe(_ModernFeature()))
        self.assertEqual(info.document_uuid, "doc-modern")
        self.assertEqual(info.revision, 42)
        self.assertEqual(info.checksum, "checksum-modern")

    def test_legacy_feature_without_checksum_uses_json_presence_fallback(self):
        info = FreeCADDocumentStore.stored_info(_StoreProbe(_LegacyFeature()))
        self.assertEqual(info.document_uuid, "doc-legacy")
        self.assertEqual(info.revision, 3)
        self.assertEqual(info.checksum, "")

    def test_internal_vector_cache_is_not_selectable_in_freecad_view(self):
        class View:
            ShowInTree = True
            Selectable = True

        class Feature:
            ViewObject = View()

        FreeCADDocumentStore._configure_internal_view(Feature())
        self.assertFalse(Feature.ViewObject.ShowInTree)
        self.assertFalse(Feature.ViewObject.Selectable)

    def test_open_document_hides_payload_without_reading_it(self):
        class Operation:
            PropertiesList = ("SettingsJSON", "MovesCompressedBase64")

            def __init__(self):
                self.modes = {}

            @property
            def MovesCompressedBase64(self):
                raise AssertionError("opening a document must not read moves")

            def getEditorMode(self, name):
                return self.modes.get(name, [])

            def setEditorMode(self, name, mode):
                self.modes[name] = ["Hidden"] if mode == 2 else []

        operation = Operation()

        class Document:
            Objects = (operation,)

            def getObject(self, _name):
                return None

        protect_woodcam_document_view(Document())
        self.assertEqual(operation.getEditorMode("MovesCompressedBase64"), ["Hidden"])


if __name__ == "__main__":
    unittest.main()
