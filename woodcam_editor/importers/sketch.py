"""Snapshot importer for FreeCAD Sketch geometry.

The importer reads solved geometry, applies global placement and creates an
independent set of domain entities.  It never adds constraints, hides the
source, or otherwise mutates the Sketch.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Any, Iterable, Optional

from woodcam_editor.importers.part_shape import (
    ImportIssue,
    ImportLayerDescriptor,
    ImportResult,
    _arc_span,
    _bezier_span,
    _circle_entity,
    _clockwise,
    _domain_api,
    _line_span,
    _path_entity,
    _transform_point,
    _vec2,
)


def _geometry_name(geometry: Any) -> str:
    return "{} {}".format(
        type(geometry).__name__,
        getattr(geometry, "TypeId", ""),
    ).lower()


def _geometry_point(geometry: Any, parameter: float):
    for method_name in ("value", "valueAt"):
        method = getattr(geometry, method_name, None)
        if callable(method):
            return method(parameter)
    raise ValueError("A geometria não fornece avaliação paramétrica.")


def _placement_is_xy(placement: Any, tolerance: float) -> bool:
    if placement is None:
        return True
    rotation = getattr(placement, "Rotation", None)
    if rotation is None:
        return True
    try:
        import FreeCAD  # type: ignore

        normal = rotation.multVec(FreeCAD.Vector(0.0, 0.0, 1.0))
        return abs(abs(float(normal.z)) - 1.0) <= tolerance
    except Exception:
        return True


def _span_endpoints(span: Any):
    return span.start, span.end


def _near(first: Any, second: Any, tolerance: float) -> bool:
    if hasattr(first, "distance_to"):
        return first.distance_to(second) <= tolerance
    return (
        (float(first.x) - float(second.x)) ** 2
        + (float(first.y) - float(second.y)) ** 2
    ) ** 0.5 <= tolerance


def _chain_spans(spans: Iterable[Any], tolerance: float):
    """Build deterministic non-branching paths from solved Sketch segments."""

    remaining = list(spans)
    chains = []
    branch_count = 0
    while remaining:
        chain = [remaining.pop(0)]
        changed = True
        while changed and remaining:
            changed = False
            chain_start = _span_endpoints(chain[0])[0]
            chain_end = _span_endpoints(chain[-1])[1]

            append_candidates = []
            prepend_candidates = []
            for index, candidate in enumerate(remaining):
                start, end = _span_endpoints(candidate)
                if _near(chain_end, start, tolerance):
                    append_candidates.append((index, candidate))
                elif _near(chain_end, end, tolerance):
                    append_candidates.append((index, candidate.reversed()))
                if _near(end, chain_start, tolerance):
                    prepend_candidates.append((index, candidate))
                elif _near(start, chain_start, tolerance):
                    prepend_candidates.append((index, candidate.reversed()))

            if len(append_candidates) == 1:
                index, candidate = append_candidates[0]
                chain.append(candidate)
                remaining.pop(index)
                changed = True
            elif len(append_candidates) > 1:
                branch_count += 1
            elif len(prepend_candidates) == 1:
                index, candidate = prepend_candidates[0]
                chain.insert(0, candidate)
                remaining.pop(index)
                changed = True
            elif len(prepend_candidates) > 1:
                branch_count += 1

        closed = len(chain) > 1 and _near(chain[0].start, chain[-1].end, tolerance)
        chains.append((tuple(chain), closed))
    return chains, branch_count


def _sketch_geometry_to_domain(
    geometry: Any,
    api: dict,
    placement: Any,
    layer_id: str,
):
    name = _geometry_name(geometry)
    if "line" in name and "bspline" not in name:
        start = _vec2(api, _transform_point(geometry.StartPoint, placement))
        end = _vec2(api, _transform_point(geometry.EndPoint, placement))
        return "span", _line_span(api, start, end), None

    if "arcof circle" in name or "arcofcircle" in name or (
        "arc" in name and "circle" in name
    ):
        start_3d = _transform_point(geometry.StartPoint, placement)
        end_3d = _transform_point(geometry.EndPoint, placement)
        center_3d = _transform_point(geometry.Center, placement)
        first = float(getattr(geometry, "FirstParameter", 0.0))
        last = float(getattr(geometry, "LastParameter", 1.0))
        forward_3d = _transform_point(
            _geometry_point(geometry, first + (last - first) * 1.0e-6), placement
        )
        return (
            "span",
            _arc_span(
                api,
                _vec2(api, start_3d),
                _vec2(api, end_3d),
                _vec2(api, center_3d),
                _clockwise(start_3d, forward_3d, center_3d),
            ),
            None,
        )

    if "circle" in name:
        center = _vec2(api, _transform_point(geometry.Center, placement))
        return (
            "entity",
            _circle_entity(api, layer_id, center, float(geometry.Radius)),
            None,
        )

    if "bezier" in name and "bspline" not in name:
        poles = list(geometry.getPoles())
        if len(poles) != 4:
            return None, None, f"Bézier de {len(poles) - 1} grau ainda não suportada."
        points = [_vec2(api, _transform_point(point, placement)) for point in poles]
        return (
            "span",
            _bezier_span(api, points[0], points[1], points[2], points[3]),
            None,
        )

    if "bspline" in name:
        return None, None, "B-spline preservada para conversão futura; não foi importada silenciosamente."

    return None, None, f"Tipo de Sketch não suportado: {type(geometry).__name__}."


def import_sketch(
    sketch: Any,
    *,
    layer_id: str,
    include_construction: bool = False,
    construction_layer_id: Optional[str] = None,
    connection_tolerance: float = 1.0e-9,
    planar_tolerance: float = 1.0e-8,
) -> ImportResult:
    """Import a solved Sketch as an independent snapshot."""

    if "Sketch" not in str(getattr(sketch, "TypeId", "Sketch")):
        raise ValueError("O objeto informado não é um Sketch do FreeCAD.")
    try:
        placement = sketch.getGlobalPlacement()
    except Exception:
        placement = getattr(sketch, "Placement", None)
    if not _placement_is_xy(placement, planar_tolerance):
        raise ValueError(
            "O plano do Sketch não é paralelo ao XY global; a importação 2D "
            "não projetará uma vista lateral silenciosamente."
        )

    api = _domain_api()
    regular_spans = []
    construction_spans = []
    entities = []
    issues = []
    fingerprint_rows = []
    used_source_layers = set()

    geometry_values = list(getattr(sketch, "Geometry", []) or [])
    for index, geometry in enumerate(geometry_values):
        try:
            is_construction = bool(sketch.getConstruction(index))
        except Exception:
            is_construction = False
        fingerprint_rows.append((index, type(geometry).__name__, is_construction))
        if is_construction and not include_construction:
            issues.append(
                ImportIssue(index, type(geometry).__name__, "Geometria de construção ignorada.", "info")
            )
            continue
        target_layer = construction_layer_id or layer_id if is_construction else layer_id
        try:
            kind, value, message = _sketch_geometry_to_domain(
                geometry, api, placement, target_layer
            )
        except Exception as error:
            issues.append(
                ImportIssue(index, type(geometry).__name__, f"Falha ao copiar geometria: {error}")
            )
            continue
        if message:
            issues.append(ImportIssue(index, type(geometry).__name__, message))
            continue
        if kind == "entity":
            source_layer_key = "construction" if is_construction else "geometry"
            entity_metadata = dict(getattr(value, "metadata", {}) or {})
            entity_metadata.update(
                {
                    "source_format": "freecad_sketch_snapshot",
                    "source_layer_key": source_layer_key,
                }
            )
            entities.append(replace(value, metadata=entity_metadata))
            used_source_layers.add(source_layer_key)
        elif is_construction:
            construction_spans.append(value)
        else:
            regular_spans.append(value)

    for spans, target_layer, label, source_layer_key in (
        (regular_spans, layer_id, "desenho", "geometry"),
        (construction_spans, construction_layer_id or layer_id, "construção", "construction"),
    ):
        chains, branch_count = _chain_spans(spans, connection_tolerance)
        for chain, closed in chains:
            entities.append(
                _path_entity(
                    api,
                    target_layer,
                    chain,
                    closed,
                    {
                        "source_format": "freecad_sketch_snapshot",
                        "source_layer_key": source_layer_key,
                    },
                )
            )
            used_source_layers.add(source_layer_key)
        if branch_count:
            issues.append(
                ImportIssue(
                    -1,
                    "Topology",
                    f"Foram encontradas {branch_count} ramificação(ões) em geometria de {label}; "
                    "os caminhos foram mantidos abertos para validação.",
                    "warning",
                )
            )

    fingerprint_text = json.dumps(fingerprint_rows, separators=(",", ":"))
    metadata = {
        "source_kind": "freecad_sketch_snapshot",
        "source_name": str(getattr(sketch, "Name", "") or ""),
        "source_label": str(getattr(sketch, "Label", "") or ""),
        "source_document": str(getattr(getattr(sketch, "Document", None), "FileName", "") or ""),
        "source_fingerprint": hashlib.sha256(fingerprint_text.encode("utf-8")).hexdigest(),
    }
    source_label = metadata["source_label"] or metadata["source_name"] or "Sketch"
    layers = {}
    if "geometry" in used_source_layers:
        layers["geometry"] = ImportLayerDescriptor(
            "geometry",
            source_label,
            purpose="design",
        )
    if "construction" in used_source_layers:
        layers["construction"] = ImportLayerDescriptor(
            "construction",
            f"{source_label} — construção",
            purpose="construction",
        )
    return ImportResult(tuple(entities), tuple(issues), metadata, layers)
