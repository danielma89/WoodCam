"""Leitura somente para orientar o smoke test da macro no modelo mesa."""

import FreeCAD as App


MODEL = "/home/danielma/Modelos/mesa.FCStd"


def edge_count(obj):
    try:
        return len(obj.Shape.Edges)
    except Exception:
        return 0


def main():
    doc = App.openDocument(MODEL)
    for obj in doc.Objects:
        count = edge_count(obj)
        if count or "Sketch" in obj.TypeId or "Estrutura" in obj.Label:
            print(
                "%s | %s | edges=%d | label=%s" % (
                    obj.Name, obj.TypeId, count, obj.Label
                )
            )
            try:
                for index, edge in enumerate(obj.Shape.Edges, start=1):
                    print(
                        "  %02d: (%.1f, %.1f, %.1f) -> (%.1f, %.1f, %.1f)" % (
                            index,
                            edge.Vertexes[0].Point.x,
                            edge.Vertexes[0].Point.y,
                            edge.Vertexes[0].Point.z,
                            edge.Vertexes[-1].Point.x,
                            edge.Vertexes[-1].Point.y,
                            edge.Vertexes[-1].Point.z,
                        )
                    )
            except Exception:
                pass
    params = doc.getObject("Config_Estrutura_Esqueleto_V42")
    if params is not None:
        for name in params.PropertiesList:
            if any(token in name.lower() for token in ("costela", "dente", "largura", "altura", "espac", "parede")):
                print("PARAM %s = %s" % (name, getattr(params, name)))
    App.closeDocument(doc.Name)


main()
