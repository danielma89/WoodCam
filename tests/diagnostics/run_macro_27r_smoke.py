"""Headless FreeCAD smoke for the stable skeleton macro.

It recreates the minimum reported case: one closed rectangular table frame
and one coplanar internal cross member in the same Sketch.
"""

from pathlib import Path

import FreeCAD as App
import Part
import Sketcher


ROOT = Path(__file__).resolve().parents[2]
MACRO = ROOT / "macros" / "V4_27R_ESTRUTURA_ESTAVEL.FCMacro"


def load_macro_namespace():
    source = MACRO.read_text(encoding="utf-8")
    marker = "\ntry:\n    main()\nexcept Exception as exc:"
    if marker not in source:
        raise RuntimeError("Não foi possível isolar a execução automática da macro.")
    namespace = {"__name__": "macro_27r_smoke"}
    exec(compile(source.split(marker, 1)[0], str(MACRO), "exec"), namespace)
    return namespace


def main():
    doc = App.newDocument("Macro27RSmoke")
    sketch = doc.addObject("Sketcher::SketchObject", "MesaComTravessa")
    points = (
        App.Vector(0.0, 0.0, 0.0),
        App.Vector(1200.0, 0.0, 0.0),
        App.Vector(1200.0, 700.0, 0.0),
        App.Vector(0.0, 700.0, 0.0),
    )
    for index in range(4):
        sketch.addGeometry(Part.LineSegment(points[index], points[(index + 1) % 4]), False)
    sketch.addGeometry(
        Part.LineSegment(App.Vector(100.0, 350.0, 0.0), App.Vector(1100.0, 350.0, 0.0)),
        False,
    )
    doc.recompute()

    namespace = load_macro_namespace()
    params = namespace["ensure_parameters"](doc)
    params.ObjetosOrigem = [sketch]
    params.SketchOrigem = sketch
    namespace["main"]()

    root = doc.getObject(namespace["ROOT_NAME"])
    assert root is not None, "A macro não criou a estrutura."
    generated = [
        obj for obj in doc.Objects
        if "GeradorEstruturaEsqueleto" in getattr(obj, "PropertiesList", [])
    ]
    assert len(generated) > 5, "A macro criou geometria insuficiente."
    print("Macro 27R smoke: OK (%d objetos gerados)" % len(generated))


# FreeCADCmd executes a script under its own module name, not ``__main__``.
main()
