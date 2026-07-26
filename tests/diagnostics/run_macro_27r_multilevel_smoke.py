"""FreeCAD smoke: multi-level table skeleton with braces and legs."""

from pathlib import Path

import FreeCAD as App
import Part


ROOT = Path(__file__).resolve().parents[2]
MACRO = ROOT / "macros" / "V4_27R_ESTRUTURA_ESTAVEL.FCMacro"


def load_macro_namespace():
    source = MACRO.read_text(encoding="utf-8")
    marker = "\ntry:\n    main()\nexcept Exception as exc:"
    if marker not in source:
        raise RuntimeError("Não foi possível isolar a execução automática da macro.")
    namespace = {"__name__": "macro_27r_multilevel_smoke"}
    exec(compile(source.split(marker, 1)[0], str(MACRO), "exec"), namespace)
    return namespace


def line(a, b):
    return Part.makeLine(App.Vector(*a), App.Vector(*b))


def rectangular_frame(z_value, x0=0.0, y0=0.0, x1=1200.0, y1=700.0):
    corners = ((x0, y0, z_value), (x1, y0, z_value), (x1, y1, z_value), (x0, y1, z_value))
    return [line(corners[index], corners[(index + 1) % 4]) for index in range(4)]


def main():
    doc = App.newDocument("Macro27RMultilevelSmoke")
    source = doc.addObject("PartDesign::Feature", "EsqueletoMesaTresNiveis")
    edges = []
    # The top frame carries the planar table interpretation and its internal
    # cross-member.  The lower frames/legs exercise the 3D network reader.
    edges.extend(rectangular_frame(720.0))
    edges.append(line((120.0, 350.0, 720.0), (1080.0, 350.0, 720.0)))
    edges.extend(rectangular_frame(360.0, 80.0, 60.0, 1120.0, 640.0))
    edges.extend(rectangular_frame(40.0, 150.0, 110.0, 1050.0, 590.0))
    for top, bottom in (
        ((0.0, 0.0, 720.0), (150.0, 110.0, 40.0)),
        ((1200.0, 0.0, 720.0), (1050.0, 110.0, 40.0)),
        ((1200.0, 700.0, 720.0), (1050.0, 590.0, 40.0)),
        ((0.0, 700.0, 720.0), (150.0, 590.0, 40.0)),
    ):
        edges.append(line(top, bottom))
    source.Shape = Part.makeCompound(edges)
    doc.recompute()

    namespace = load_macro_namespace()
    params = namespace["ensure_parameters"](doc)
    params.ObjetosOrigem = [source]
    namespace["main"]()

    root = doc.getObject(namespace["ROOT_NAME"])
    assert root is not None, "A macro não criou a estrutura multi-nível."
    generated = [
        obj for obj in doc.Objects
        if "GeradorEstruturaEsqueleto" in getattr(obj, "PropertiesList", [])
    ]
    assert len(generated) > 10, "A estrutura multi-nível não gerou componentes suficientes."
    planar_segments = [
        obj for obj in doc.Objects
        if obj.Name.startswith("Segmento_Seguro_Planar_")
    ]
    spatial_segments = [
        obj for obj in doc.Objects
        if obj.Name.startswith("Segmento_Seguro_3D_")
    ]
    assert len(planar_segments) >= 4, "O quadro principal não foi criado."
    assert len(spatial_segments) >= 9, "As barras dos níveis/pernas foram ignoradas."
    print(
        "Macro 27R multi-level smoke: OK "
        "(%d objetos; %d planos; %d barras 3D)" % (
            len(generated), len(planar_segments), len(spatial_segments)
        )
    )


main()
