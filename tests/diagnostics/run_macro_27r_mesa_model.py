"""Smoke da 27R no arquivo real Modelos/mesa.FCStd, sem salvar alterações."""

from pathlib import Path
import time

import FreeCAD as App


ROOT = Path(__file__).resolve().parents[2]
MODEL = Path("/home/danielma/Modelos/mesa.FCStd")
MACRO = ROOT / "macros" / "V4_27R_ESTRUTURA_ESTAVEL.FCMacro"


def load_macro_namespace():
    source = MACRO.read_text(encoding="utf-8")
    marker = "\ntry:\n    main()\nexcept Exception as exc:"
    if marker not in source:
        raise RuntimeError("Não foi possível isolar a execução automática da macro.")
    namespace = {"__name__": "macro_27r_mesa_model_smoke"}
    exec(compile(source.split(marker, 1)[0], str(MACRO), "exec"), namespace)
    return namespace


def main():
    doc = App.openDocument(str(MODEL))
    source = doc.getObject("Group")
    assert source is not None, "O grupo de esqueleto 'Group' não foi encontrado."
    assert len(source.Shape.Edges) == 11, "O esqueleto mesa mudou; revise o teste."

    namespace = load_macro_namespace()
    for function_name in ("create_planar_segment", "create_axis_segment"):
        original = namespace[function_name]

        def timed(*args, _original=original, _name=function_name, **kwargs):
            label = getattr(args[2] if _name == "create_planar_segment" else args[2], "get", lambda *_: None)("index")
            started = time.perf_counter()
            print("PERFIL início %s %s" % (_name, label), flush=True)
            if _name == "create_planar_segment":
                descriptor = args[2]
                print(
                    "  eixo: start=%s end=%s x=%s y=%s z=%s" % (
                        descriptor.get("start"), descriptor.get("end"),
                        descriptor.get("axis_x"), descriptor.get("axis_y"),
                        descriptor.get("axis_z"),
                    ),
                    flush=True,
                )
            result = _original(*args, **kwargs)
            print("PERFIL fim %s %s: %.2f s" % (_name, label, time.perf_counter() - started), flush=True)
            return result

        namespace[function_name] = timed
    params = namespace["ensure_parameters"](doc)
    params.ObjetosOrigem = [source]
    namespace["main"]()

    generated = [
        obj for obj in doc.Objects
        if "GeradorEstruturaEsqueleto" in getattr(obj, "PropertiesList", [])
    ]
    planar = [obj for obj in doc.Objects if obj.Name.startswith("Segmento_Seguro_Planar_")]
    spatial = [obj for obj in doc.Objects if obj.Name.startswith("Segmento_Seguro_3D_")]
    assert len(planar) >= 5, "Quadro e travessa central não foram gerados."
    assert len(spatial) >= 6, "As seis pernas do modelo mesa foram ignoradas."
    feature_boxes = [
        obj.Shape.BoundBox for obj in generated
        if getattr(obj, "TypeId", "") == "Part::Feature" and not obj.Shape.isNull()
    ]
    x_span = max(box.XMax for box in feature_boxes) - min(box.XMin for box in feature_boxes)
    y_span = max(box.YMax for box in feature_boxes) - min(box.YMin for box in feature_boxes)
    z_span = max(box.ZMax for box in feature_boxes) - min(box.ZMin for box in feature_boxes)
    assert x_span < 4000.0 and y_span < 4000.0 and z_span < 2000.0, (
        "Há uma peça posicionada fora da mesa: "
        "%.1f x %.1f x %.1f mm" % (x_span, y_span, z_span)
    )
    print(
        "Macro 27R mesa.FCStd: OK "
        "(%d objetos; %d planos; %d barras 3D; envelope %.1f x %.1f x %.1f mm)" % (
            len(generated), len(planar), len(spatial), x_span, y_span, z_span
        )
    )
    # Não salvar: o arquivo em Modelos deve permanecer exatamente como estava.
    App.closeDocument(doc.Name)


main()
