"""Exact closed-vector boolean operations backed by FreeCAD/OCC.

The Editor domain remains the source of truth.  This adapter receives a
selection, builds temporary OCC faces, executes an exact planar boolean and
imports the result back as immutable domain entities.  Nothing in the source
FCStd Shape is touched; callers apply the returned ModifierPreview through the
normal command/Undo route.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from woodcam_editor.geometry.modifiers import ModifierError, ModifierPreview


def _freecad_modules():
    try:
        import FreeCAD  # type: ignore
        import Part  # type: ignore
    except Exception as error:
        raise ModifierError(
            "Booleanos vetoriais exigem o núcleo geométrico do FreeCAD/OCC."
        ) from error
    return FreeCAD, Part


def _vector(FreeCAD, point):
    return FreeCAD.Vector(float(point.x), float(point.y), 0.0)


def _entity_wire(entity, FreeCAD, Part):
    from woodcam_editor.domain import CircleEntity, PathEntity
    from woodcam_editor.domain.spans import ArcSpan, CubicBezierSpan, LineSpan

    if isinstance(entity, CircleEntity):
        edge = Part.makeCircle(
            float(entity.radius), _vector(FreeCAD, entity.center), FreeCAD.Vector(0, 0, 1)
        )
        return Part.Wire([edge])
    if not isinstance(entity, PathEntity) or not entity.closed:
        raise ModifierError("Selecione somente vetores fechados para uma operação booleana.")

    edges = []
    for span in entity.spans:
        if isinstance(span, LineSpan):
            edges.append(Part.makeLine(_vector(FreeCAD, span.start), _vector(FreeCAD, span.end)))
        elif isinstance(span, ArcSpan):
            # OCC's three-point arc retains the original circular geometry;
            # point_at(.5) is valid for both clockwise and major arcs.
            edges.append(
                Part.Arc(
                    _vector(FreeCAD, span.start),
                    _vector(FreeCAD, span.point_at(0.5)),
                    _vector(FreeCAD, span.end),
                ).toShape()
            )
        elif isinstance(span, CubicBezierSpan):
            curve = Part.BezierCurve()
            curve.setPoles(
                [
                    _vector(FreeCAD, span.start),
                    _vector(FreeCAD, span.control1),
                    _vector(FreeCAD, span.control2),
                    _vector(FreeCAD, span.end),
                ]
            )
            edges.append(curve.toShape())
        else:
            raise ModifierError("O tipo de curva selecionado ainda não pode entrar em booleanos.")
    try:
        return Part.Wire(edges)
    except Exception as error:
        raise ModifierError("O vetor fechado não forma um wire OCC válido.") from error


def _entity_face(entity, FreeCAD, Part):
    try:
        return Part.Face(_entity_wire(entity, FreeCAD, Part))
    except Exception as error:
        raise ModifierError("Não foi possível criar uma face a partir do vetor selecionado.") from error


def _require_same_editable_layer(entities):
    if len(entities) < 2:
        raise ModifierError("Selecione pelo menos dois vetores fechados.")
    layers = {str(entity.layer_id) for entity in entities}
    if len(layers) != 1:
        raise ModifierError("Os vetores precisam estar na mesma camada para o booleano.")


def _area(shape) -> float:
    return abs(float(getattr(shape, "Area", 0.0) or 0.0))


def _difference_operands(source, faces):
    """Choose the containing contour as difference base when unambiguous.

    Aspire-style use should not depend on the accidental ordering of Ctrl-click
    selections: an isolated circle wholly inside a panel is a hole regardless
    of whether the user clicked the circle or the panel first.  Overlapping or
    ambiguous profiles deliberately retain the explicit first-selected base.
    """
    candidates = []
    for index, face in enumerate(faces):
        if _area(face) <= 1.0e-8:
            continue
        contains_every_other = True
        for other_index, other in enumerate(faces):
            if other_index == index:
                continue
            other_area = _area(other)
            if other_area <= 1.0e-8:
                contains_every_other = False
                break
            common = face.common(other)
            # A small relative allowance absorbs OCC's numeric noise while
            # still rejecting overlap-only selections as ambiguous.
            if abs(_area(common) - other_area) > max(1.0e-7, other_area * 1.0e-8):
                contains_every_other = False
                break
        if contains_every_other:
            candidates.append(index)
    if len(candidates) == 1:
        base_index = candidates[0]
        order = (base_index,) + tuple(index for index in range(len(source)) if index != base_index)
        return tuple(source[index] for index in order), tuple(faces[index] for index in order), True
    return tuple(source), tuple(faces), False


def preview_boolean(operation: str, entities: Iterable[object]) -> ModifierPreview:
    """Return an exact preview for a closed-vector boolean operation.

    For a difference, a uniquely containing outer contour is selected as the
    material automatically, so an inner hole can be clicked before its panel.
    If the selection is ambiguous, the first selected contour remains the
    material.  A detached cutter is rejected rather than silently discarded.
    """

    source = tuple(entities or ())
    _require_same_editable_layer(source)
    operation = str(operation or "").lower()
    labels = {
        "union": "soldar",
        "difference": "subtrair",
        "intersection": "interseção",
        "overlap": "sobrepor",
    }
    if operation not in labels:
        raise ValueError("Operação booleana desconhecida: %s" % operation)
    FreeCAD, Part = _freecad_modules()
    faces = tuple(_entity_face(entity, FreeCAD, Part) for entity in source)
    inferred_difference_base = False
    if operation == "difference":
        source, faces, inferred_difference_base = _difference_operands(source, faces)

    try:
        if operation == "union":
            result = faces[0].multiFuse(list(faces[1:])).removeSplitter()
        elif operation == "difference":
            result = faces[0]
            for cutter in faces[1:]:
                # A completely detached cutter is almost always a selection
                # error.  Do not pretend it became a hole.
                common = result.common(cutter)
                if common.isNull() or float(getattr(common, "Area", 0.0)) <= 1.0e-8:
                    raise ModifierError(
                        "O vetor a subtrair não está dentro do contorno externo selecionado."
                    )
                result = result.cut(cutter)
            result = result.removeSplitter()
        elif operation == "intersection":
            result = faces[0]
            for other in faces[1:]:
                result = result.common(other)
            result = result.removeSplitter()
        else:
            # Aspire/VCarve's Overlap Vectors is intentionally not the same
            # as an N-way intersection: all earlier selected objects are
            # clipped independently by the *last* selected closed vector.
            # This makes it useful for keeping the portions of several
            # circles/ornaments inside one boundary in a single operation.
            clipper = faces[-1]
            overlap_results = []
            for subject in faces[:-1]:
                clipped = subject.common(clipper).removeSplitter()
                if not clipped.isNull() and _area(clipped) > 1.0e-8:
                    overlap_results.append(clipped)
            if not overlap_results:
                raise ModifierError(
                    "Nenhuma área dos vetores iniciais sobrepõe o último vetor selecionado."
                )
            result = overlap_results
    except ModifierError:
        raise
    except Exception as error:
        raise ModifierError("O booleano OCC falhou para esta seleção.") from error

    if operation != "overlap":
        if result.isNull() or not list(getattr(result, "Wires", []) or []):
            raise ModifierError("O resultado do booleano não contém uma área fechada.")

    from woodcam_editor.importers.part_shape import import_part_shape

    imported_entities = []
    result_shapes = result if operation == "overlap" else (result,)
    for result_shape in result_shapes:
        imported = import_part_shape(result_shape, layer_id=source[0].layer_id)
        if not imported.entities:
            detail = imported.issues[0].message if imported.issues else "geometria vazia"
            raise ModifierError("Não foi possível converter o resultado do booleano: %s" % detail)
        imported_entities.extend(imported.entities)
    metadata = {
        "source_kind": "editor_boolean",
        "boolean_operation": operation,
        "boolean_sources": tuple(entity.id for entity in source),
    }
    result_entities = tuple(
        replace(entity, metadata={**dict(getattr(entity, "metadata", {}) or {}), **metadata})
        for entity in imported_entities
    )
    return ModifierPreview(
        operation="boolean_%s" % operation,
        original_entities=source,
        result_entities=result_entities,
        metadata={
            "label": labels[operation],
            "source_count": len(source),
            "inferred_difference_base": inferred_difference_base,
            "difference_base_id": source[0].id if operation == "difference" else None,
            "overlap_clipper_id": source[-1].id if operation == "overlap" else None,
        },
    )


__all__ = ["preview_boolean"]
