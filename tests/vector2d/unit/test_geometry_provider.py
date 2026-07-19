import unittest

from woodcam_editor.application.geometry_provider import (
    CallableGeometryProvider,
    GeometryProviderRouter,
    normalize_geometry,
)


class GeometryProviderTest(unittest.TestCase):
    def test_router_never_switches_source_implicitly(self):
        fallback = CallableGeometryProvider(
            lambda: {"contours": [[(0, 0), (1, 0), (1, 1)]], "holes": []},
            "Seleção FreeCAD",
            "freecad",
        )
        editor = CallableGeometryProvider(
            lambda: {"contours": [[(0, 0), (2, 0), (2, 2)]], "holes": []},
            "Editor 2D",
            "editor",
            lambda: 8,
        )
        router = GeometryProviderRouter(fallback)
        router.register(editor)
        self.assertEqual(router.describe_source(), "Seleção FreeCAD")
        router.activate("editor")
        self.assertEqual(router.describe_source(), "Editor 2D")
        self.assertEqual(router.revision_token(), "8")
        router.activate(None)
        self.assertEqual(router.describe_source(), "Seleção FreeCAD")

    def test_normalize_geometry_returns_new_lists(self):
        source = {"contours": [1], "holes": [2]}
        result = normalize_geometry(source)
        result["contours"].append(3)
        self.assertEqual(source["contours"], [1])


if __name__ == "__main__":
    unittest.main()
