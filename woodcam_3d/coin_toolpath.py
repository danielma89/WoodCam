"""Representação leve de percursos com o scene graph Coin3D do FreeCAD.

OCC/Part é excelente para geometria CAD, mas milhares de arestas independentes
são caras para desenhar e salvar. Percursos são dados de visualização; este
objeto usa um único ``SoLineSet`` e mantém os pontos no FCStd para reabertura.
"""

from __future__ import annotations


def _edge_point_lines(edges):
    """Converte segmentos consecutivos em polilinhas sem duplicar extremos."""
    lines = []
    for edge in edges:
        if (
            isinstance(edge, (tuple, list))
            and len(edge) == 2
            and len(edge[0]) >= 3
            and len(edge[1]) >= 3
        ):
            edge_points = [
                (float(point[0]), float(point[1]), float(point[2]))
                for point in edge
            ]
        else:
            vertices = list(getattr(edge, "Vertexes", []) or [])
            if len(vertices) < 2:
                continue
            edge_points = [
                (
                    float(vertex.Point.x),
                    float(vertex.Point.y),
                    float(vertex.Point.z),
                )
                for vertex in vertices
            ]
        if lines and lines[-1][-1] == edge_points[0]:
            lines[-1].extend(edge_points[1:])
        else:
            lines.append(edge_points)
    return lines


class CoinToolpathOverlay:
    """Prévia completa enviada ao scene graph, sem entrar no documento."""

    def __init__(self, gui_document=None):
        self.gui_document = gui_document
        self.scene = None
        self.root = None

    def update(self, components):
        self.clear()
        import FreeCADGui
        from pivy import coin

        gui_document = self.gui_document or FreeCADGui.ActiveDocument
        if gui_document is None:
            return
        scene = gui_document.ActiveView.getSceneGraph()
        root = coin.SoSeparator()
        pick_style = coin.SoPickStyle()
        pick_style.style = coin.SoPickStyle.UNPICKABLE
        root.addChild(pick_style)
        specs = (
            # O relevo sombreado é a referência principal. Os rápidos e
            # rampas não podem formar uma gaiola opaca em volta dele, e o
            # raster de corte precisa continuar legível sem encobrir a forma.
            ("rapid", (0.42, 0.50, 0.68), 1.0, 0.94),
            ("ramp", (0.95, 0.58, 0.04), 1.0, 0.86),
            ("cut", (0.04, 0.22, 0.48), 1.0, 0.58),
            ("corner", (0.48, 0.10, 0.62), 2.0, 0.70),
        )
        for key, color, width, transparency in specs:
            point_lines = _edge_point_lines(components.get(key, ()))
            if not point_lines:
                continue
            points = [point for line in point_lines for point in line]
            counts = [len(line) for line in point_lines]
            branch = coin.SoSeparator()
            material = coin.SoMaterial()
            material.diffuseColor = color
            material.transparency = transparency
            style = coin.SoDrawStyle()
            style.lineWidth = width
            coordinates = coin.SoCoordinate3()
            coordinates.point.setValues(0, len(points), points)
            line_set = coin.SoLineSet()
            line_set.numVertices.setValues(0, len(counts), counts)
            for node in (material, style, coordinates, line_set):
                branch.addChild(node)
            root.addChild(branch)
        scene.addChild(root)
        self.scene = scene
        self.root = root

    def clear(self):
        if self.scene is not None and self.root is not None:
            try:
                self.scene.removeChild(self.root)
            except Exception:
                pass
        self.scene = None
        self.root = None


class CoinToolpathFeature:
    def __init__(self, obj=None):
        if obj is not None:
            obj.Proxy = self

    def execute(self, _obj):
        return None

    def dumps(self):
        return None

    def loads(self, _state):
        return None


class CoinToolpathViewProvider:
    def __init__(self, view_object=None):
        self.root = None
        self.material = None
        self.style = None
        self.coordinates = None
        self.lines = None
        self.pick_style = None
        if view_object is not None:
            view_object.Proxy = self

    def attach(self, view_object):
        from pivy import coin

        self.root = coin.SoSeparator()
        self.pick_style = coin.SoPickStyle()
        self.pick_style.style = coin.SoPickStyle.UNPICKABLE
        self.material = coin.SoMaterial()
        self.style = coin.SoDrawStyle()
        self.coordinates = coin.SoCoordinate3()
        self.lines = coin.SoLineSet()
        self.root.addChild(self.pick_style)
        self.root.addChild(self.material)
        self.root.addChild(self.style)
        self.root.addChild(self.coordinates)
        self.root.addChild(self.lines)
        view_object.addDisplayMode(self.root, "Percurso")
        self._rebuild(view_object.Object)

    def _rebuild(self, obj):
        if self.coordinates is None or obj is None:
            return
        points = list(getattr(obj, "Points", []) or [])
        counts = [int(value) for value in list(getattr(obj, "LineCounts", []) or [])]
        tuples = [(float(point.x), float(point.y), float(point.z)) for point in points]
        self.coordinates.point.setNum(0)
        self.lines.numVertices.setNum(0)
        if tuples:
            self.coordinates.point.setValues(0, len(tuples), tuples)
        if counts:
            self.lines.numVertices.setValues(0, len(counts), counts)
        self.material.diffuseColor = (
            float(getattr(obj, "ColorRed", 0.2)),
            float(getattr(obj, "ColorGreen", 0.55)),
            float(getattr(obj, "ColorBlue", 1.0)),
        )
        self.material.transparency = max(
            0.0, min(1.0, float(getattr(obj, "Transparency", 0.0)))
        )
        self.style.lineWidth = max(1.0, float(getattr(obj, "LineWidth", 1.0)))

    def set_segment_pairs(self, segments):
        """Atualiza um rastro transitório direto na GPU, sem tocar no FCStd."""
        if self.coordinates is None or self.lines is None:
            return
        points = []
        for start, end in segments:
            points.append((float(start[0]), float(start[1]), float(start[2])))
            points.append((float(end[0]), float(end[1]), float(end[2])))
        self.coordinates.point.setNum(0)
        self.lines.numVertices.setNum(0)
        if points:
            self.coordinates.point.setValues(0, len(points), points)
            self.lines.numVertices.setValues(0, len(segments), [2] * len(segments))

    def updateData(self, obj, prop):
        if prop in {
            "Points", "LineCounts", "ColorRed", "ColorGreen", "ColorBlue",
            "Transparency", "LineWidth",
        }:
            self._rebuild(obj)

    def getDisplayModes(self, _view_object):
        return ["Percurso"]

    def getDefaultDisplayMode(self):
        return "Percurso"

    def setDisplayMode(self, mode):
        return mode

    def onChanged(self, _view_object, _prop):
        return None

    def dumps(self):
        return None

    def loads(self, _state):
        return None


def create_coin_toolpath_feature(
    document,
    name,
    label,
    edges,
    color=(0.2, 0.55, 1.0),
    line_width=1.0,
    transparency=0.0,
):
    """Cria um objeto persistente leve a partir de segmentos Part temporários."""
    import FreeCAD

    obj = document.addObject("App::FeaturePython", str(name))
    obj.Label = str(label)
    CoinToolpathFeature(obj)
    for prop_type, prop_name in (
        ("App::PropertyVectorList", "Points"),
        ("App::PropertyIntegerList", "LineCounts"),
        ("App::PropertyFloat", "ColorRed"),
        ("App::PropertyFloat", "ColorGreen"),
        ("App::PropertyFloat", "ColorBlue"),
        ("App::PropertyFloat", "LineWidth"),
        ("App::PropertyFloat", "Transparency"),
    ):
        obj.addProperty(prop_type, prop_name, "WoodCAM Visual")
    lines = _edge_point_lines(edges)
    points = [FreeCAD.Vector(*point) for line in lines for point in line]
    counts = [len(line) for line in lines]
    obj.ColorRed, obj.ColorGreen, obj.ColorBlue = (float(value) for value in color)
    obj.LineWidth = float(line_width)
    obj.Transparency = float(transparency)
    obj.Points = points
    obj.LineCounts = counts
    for prop_name in (
        "Points", "LineCounts", "ColorRed", "ColorGreen", "ColorBlue",
        "LineWidth", "Transparency",
    ):
        obj.setEditorMode(prop_name, 2)
    view_object = getattr(obj, "ViewObject", None)
    if view_object is not None:
        CoinToolpathViewProvider(view_object)
    return obj


__all__ = [
    "CoinToolpathOverlay",
    "CoinToolpathFeature",
    "CoinToolpathViewProvider",
    "create_coin_toolpath_feature",
]
