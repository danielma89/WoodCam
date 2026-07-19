"""DXF importer with a deterministic ASCII backend and isolated FreeCAD fallback."""

from __future__ import annotations

import hashlib
import colorsys
import math
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable, Mapping

from woodcam_editor.importers.part_shape import (
    ImportIssue,
    ImportLayerDescriptor,
    ImportResult,
    _arc_span,
    _bezier_span,
    _circle_entity,
    _domain_api,
    _ellipse_entity,
    _line_span,
    _path_entity,
    import_part_shape,
)
from woodcam_editor.importers.sketch import _chain_spans


INSUNITS_TO_MM = {
    0: 1.0,
    1: 25.4,
    2: 304.8,
    4: 1.0,
    5: 10.0,
    6: 1000.0,
    7: 1_000_000.0,
    8: 0.0000254,
    9: 0.0254,
    10: 914.4,
    11: 1.0e-7,
    12: 1.0e-6,
    13: 1.0e-3,
    14: 100.0,
}


def _pairs(path: Path):
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    if len(lines) % 2:
        raise ValueError("DXF ASCII possui linha de código sem valor correspondente.")
    result = []
    for index in range(0, len(lines), 2):
        try:
            code = int(lines[index].strip())
        except ValueError as error:
            raise ValueError(f"Código DXF inválido na linha {index + 1}.") from error
        result.append((code, lines[index + 1].strip()))
    return result


def _unit_scale(pairs) -> tuple[float, int]:
    for index, (code, value) in enumerate(pairs):
        if code == 9 and value.upper() == "$INSUNITS":
            for next_code, next_value in pairs[index + 1 : index + 5]:
                if next_code in (70, 280):
                    unit = int(float(next_value))
                    return INSUNITS_TO_MM.get(unit, 1.0), unit
    return 1.0, 0


def _entity_records(pairs):
    in_entities = False
    records = []
    current = None
    for index, (code, value) in enumerate(pairs):
        if code == 0 and value.upper() == "SECTION":
            in_entities = index + 1 < len(pairs) and pairs[index + 1] == (2, "ENTITIES")
            continue
        if in_entities and code == 0 and value.upper() == "ENDSEC":
            if current:
                records.append(current)
            break
        if not in_entities:
            continue
        if code == 0:
            if current:
                records.append(current)
            current = [value.upper(), []]
        elif current is not None:
            current[1].append((code, value))
    return records


def _layer_records(pairs):
    """Read AcDb LAYER table rows without depending on ezdxf/FreeCAD."""

    in_tables = False
    in_layer_table = False
    records = []
    current = None
    index = 0
    while index < len(pairs):
        code, value = pairs[index]
        upper = value.upper()
        if code == 0 and upper == "SECTION":
            in_tables = index + 1 < len(pairs) and pairs[index + 1] == (2, "TABLES")
            index += 1
        elif in_tables and code == 0 and upper == "ENDSEC":
            if current:
                records.append(current)
            break
        elif in_tables and code == 0 and upper == "TABLE":
            in_layer_table = index + 1 < len(pairs) and pairs[index + 1] == (2, "LAYER")
            index += 1
        elif in_layer_table and code == 0 and upper == "ENDTAB":
            if current:
                records.append(current)
            current = None
            in_layer_table = False
        elif in_layer_table and code == 0 and upper == "LAYER":
            if current:
                records.append(current)
            current = []
        elif in_layer_table and current is not None:
            current.append((code, value))
        index += 1
    return records


def _first(values, code: int, default=None):
    return next((value for item_code, value in values if item_code == code), default)


def _all(values, code: int):
    return [value for item_code, value in values if item_code == code]


def _float(values, code: int, default=0.0) -> float:
    return float(_first(values, code, default))


def _int(values, code: int, default=0) -> int:
    return int(float(_first(values, code, default)))


def _aci_color(index: int) -> str | None:
    """Return an sRGB approximation for every AutoCAD Color Index value."""

    index = abs(int(index))
    basic = {
        1: "#ff0000",
        2: "#ffff00",
        3: "#00ff00",
        4: "#00ffff",
        5: "#0000ff",
        6: "#ff00ff",
        # ACI 7 is adaptive black/white; black is visible on WoodCAM's canvas.
        7: "#000000",
        8: "#808080",
        9: "#c0c0c0",
    }
    if index in basic:
        return basic[index]
    if 10 <= index <= 249:
        hue_slot, shade = divmod(index - 10, 10)
        hue = (hue_slot * 15.0) / 360.0
        saturation = 1.0 if shade % 2 == 0 else 0.5
        values = (1.0, 1.0, 0.65, 0.65, 0.5, 0.5, 0.3, 0.3, 0.15, 0.15)
        red, green, blue = colorsys.hsv_to_rgb(hue, saturation, values[shade])
        return "#{:02x}{:02x}{:02x}".format(
            round(red * 255), round(green * 255), round(blue * 255)
        )
    if 250 <= index <= 255:
        gray = (51, 80, 105, 130, 190, 255)[index - 250]
        return "#{0:02x}{0:02x}{0:02x}".format(gray)
    return None


def _layer_color(values) -> tuple[str | None, dict]:
    true_color = _first(values, 420)
    aci = _int(values, 62, 7)
    metadata = {"dxf_aci_color": aci}
    if true_color is not None:
        integer = int(float(true_color)) & 0xFFFFFF
        metadata["dxf_true_color"] = integer
        return f"#{integer:06x}", metadata
    return _aci_color(aci), metadata


def _dxf_layer_descriptors(pairs) -> dict[str, ImportLayerDescriptor]:
    result = {}
    for values in _layer_records(pairs):
        name = str(_first(values, 2, "0") or "0")
        color, color_metadata = _layer_color(values)
        flags = _int(values, 70, 0)
        metadata = {
            **color_metadata,
            "dxf_flags": flags,
            "dxf_linetype": str(_first(values, 6, "CONTINUOUS") or "CONTINUOUS"),
            "visible": _int(values, 62, 7) >= 0,
        }
        result[name] = ImportLayerDescriptor(
            source_key=name,
            name=name,
            color=color,
            purpose="design",
            metadata=metadata,
        )
    return result


def _entity_metadata(source_layer: str, entity_type: str = "") -> dict:
    metadata = {
        "source_format": "dxf",
        "source_layer_key": source_layer,
        "dxf_layer": source_layer,
    }
    if entity_type:
        metadata["dxf_entity"] = entity_type
    return metadata


def _vec(api: Mapping[str, Any], x_value: float, y_value: float, scale: float):
    return api["Vec2"](float(x_value) * scale, float(y_value) * scale)


def _bulge_span(api, start, end, bulge):
    if abs(bulge) <= 1e-14:
        return _line_span(api, start, end)
    chord = end - start
    length = chord.length()
    if length <= 1e-12:
        raise ValueError("Vértice DXF com bulge forma segmento zero.")
    midpoint = (start + end) * 0.5
    offset = length * (1.0 - bulge * bulge) / (4.0 * bulge)
    center = midpoint + chord.perpendicular_left().normalized() * offset
    return _arc_span(api, start, end, center, bulge < 0.0)


def _lwpolyline(values, api, layer_id, scale, metadata):
    vertices = []
    current = None
    for code, value in values:
        if code == 10:
            if current is not None:
                vertices.append(current)
            current = {"x": float(value), "y": 0.0, "bulge": 0.0}
        elif current is not None and code == 20:
            current["y"] = float(value)
        elif current is not None and code == 42:
            current["bulge"] = float(value)
    if current is not None:
        vertices.append(current)
    closed = bool(_int(values, 70, 0) & 1)
    points = [_vec(api, item["x"], item["y"], scale) for item in vertices]
    spans = []
    count = len(points) if closed else max(0, len(points) - 1)
    for index in range(count):
        spans.append(
            _bulge_span(
                api,
                points[index],
                points[(index + 1) % len(points)],
                vertices[index]["bulge"],
            )
        )
    return _path_entity(api, layer_id, spans, closed, metadata) if spans else None


def _polyline_record(records, start_index, api, layer_id, scale, metadata):
    header_values = records[start_index][1]
    closed = bool(_int(header_values, 70, 0) & 1)
    vertices = []
    index = start_index + 1
    while index < len(records) and records[index][0] == "VERTEX":
        values = records[index][1]
        vertices.append(
            {
                "point": _vec(api, _float(values, 10), _float(values, 20), scale),
                "bulge": _float(values, 42, 0.0),
            }
        )
        index += 1
    if index < len(records) and records[index][0] == "SEQEND":
        index += 1
    spans = []
    count = len(vertices) if closed else max(0, len(vertices) - 1)
    for vertex_index in range(count):
        spans.append(
            _bulge_span(
                api,
                vertices[vertex_index]["point"],
                vertices[(vertex_index + 1) % len(vertices)]["point"],
                vertices[vertex_index]["bulge"],
            )
        )
    entity = _path_entity(api, layer_id, spans, closed, metadata) if spans else None
    return entity, index


def import_dxf_ascii(path: str | Path, *, layer_id: str) -> ImportResult:
    source_path = Path(path)
    pairs = _pairs(source_path)
    scale, unit_code = _unit_scale(pairs)
    api = _domain_api()
    records = _entity_records(pairs)
    layers = _dxf_layer_descriptors(pairs)
    entities = []
    issues = []
    loose_spans = {}
    index = 0
    while index < len(records):
        entity_type, values = records[index]
        source_layer = str(_first(values, 8, "0"))
        layers.setdefault(
            source_layer,
            ImportLayerDescriptor(source_layer, source_layer),
        )
        metadata = _entity_metadata(source_layer, entity_type)
        try:
            if entity_type == "LINE":
                span = _line_span(
                    api,
                    _vec(api, _float(values, 10), _float(values, 20), scale),
                    _vec(api, _float(values, 11), _float(values, 21), scale),
                )
                loose_spans.setdefault(source_layer, []).append(span)
            elif entity_type == "CIRCLE":
                entities.append(
                    _circle_entity(
                        api,
                        layer_id,
                        _vec(api, _float(values, 10), _float(values, 20), scale),
                        _float(values, 40) * scale,
                        metadata,
                    )
                )
            elif entity_type == "ARC":
                center = _vec(api, _float(values, 10), _float(values, 20), scale)
                radius = _float(values, 40) * scale
                start_angle = math.radians(_float(values, 50))
                end_angle = math.radians(_float(values, 51))
                start = center + api["Vec2"](math.cos(start_angle), math.sin(start_angle)) * radius
                end = center + api["Vec2"](math.cos(end_angle), math.sin(end_angle)) * radius
                loose_spans.setdefault(source_layer, []).append(
                    _arc_span(api, start, end, center, False)
                )
            elif entity_type == "LWPOLYLINE":
                entity = _lwpolyline(values, api, layer_id, scale, metadata)
                if entity:
                    entities.append(entity)
            elif entity_type == "POLYLINE":
                entity, next_index = _polyline_record(
                    records, index, api, layer_id, scale, metadata
                )
                if entity:
                    entities.append(entity)
                index = next_index
                continue
            elif entity_type == "SPLINE":
                degree = _int(values, 71, 3)
                xs = [float(item) * scale for item in _all(values, 10)]
                ys = [float(item) * scale for item in _all(values, 20)]
                points = [api["Vec2"](x_value, y_value) for x_value, y_value in zip(xs, ys)]
                if degree == 3 and len(points) == 4:
                    loose_spans.setdefault(source_layer, []).append(
                        _bezier_span(api, points[0], points[1], points[2], points[3])
                    )
                else:
                    issues.append(
                        ImportIssue(index, entity_type, f"SPLINE grau {degree} com {len(points)} polos não suportada.")
                    )
            elif entity_type == "ELLIPSE":
                center = _vec(api, _float(values, 10), _float(values, 20), scale)
                major_x = _float(values, 11) * scale
                major_y = _float(values, 21) * scale
                major = math.hypot(major_x, major_y)
                ratio = _float(values, 40, 1.0)
                start_parameter = _float(values, 41, 0.0)
                end_parameter = _float(values, 42, math.tau)
                if abs((end_parameter - start_parameter) - math.tau) > 1e-8:
                    issues.append(ImportIssue(index, entity_type, "Arco elíptico DXF ainda não suportado."))
                else:
                    entities.append(
                        _ellipse_entity(
                            api,
                            layer_id,
                            center,
                            major,
                            major * ratio,
                            math.atan2(major_y, major_x),
                            metadata,
                        )
                    )
            elif entity_type not in {"VERTEX", "SEQEND"}:
                issues.append(ImportIssue(index, entity_type, f"Entidade DXF não suportada: {entity_type}."))
        except Exception as error:
            issues.append(ImportIssue(index, entity_type, f"Falha ao importar {entity_type}: {error}"))
        index += 1

    for source_layer, spans in loose_spans.items():
        chains, branches = _chain_spans(spans, 1e-9)
        for chain, closed in chains:
            entities.append(
                _path_entity(
                    api,
                    layer_id,
                    chain,
                    closed,
                    _entity_metadata(source_layer),
                )
            )
        if branches:
            issues.append(
                ImportIssue(-1, "Topology", f"{branches} ramificação(ões) DXF mantidas como caminhos separados.")
            )
    if unit_code == 0:
        issues.append(ImportIssue(-1, "Units", "DXF sem $INSUNITS; assumido milímetro.", "info"))
    file_bytes = source_path.read_bytes()
    return ImportResult(
        tuple(entities),
        tuple(issues),
        {
            "source_kind": "dxf",
            "source_path": str(source_path),
            "source_fingerprint": hashlib.sha256(file_bytes).hexdigest(),
            "dxf_insunits": unit_code,
            "unit_scale_to_mm": scale,
            "backend": "native_ascii",
        },
        layers,
    )


def import_dxf_with_freecad(path: str | Path, *, layer_id: str) -> ImportResult:
    """Import into an isolated temporary document, then close it in ``finally``."""

    try:
        import FreeCAD  # type: ignore
        import importDXF  # type: ignore
    except ImportError as error:
        raise RuntimeError("O backend DXF do FreeCAD não está disponível.") from error
    document_name = "WoodCAMDXFImport_" + uuid.uuid4().hex
    document = FreeCAD.newDocument(document_name)
    entities = []
    issues = []
    layers = {}
    try:
        importDXF.insert(str(Path(path)), document.Name)
        document.recompute()
        for object_index, obj in enumerate(list(document.Objects)):
            shape = getattr(obj, "Shape", None)
            if shape is None or bool(getattr(shape, "isNull", lambda: True)()):
                continue
            result = import_part_shape(obj, layer_id=layer_id)
            source_layer = str(
                getattr(obj, "Layer", "")
                or next(
                    (
                        getattr(parent, "Label", getattr(parent, "Name", ""))
                        for parent in list(getattr(obj, "InList", ()) or ())
                        if str(getattr(parent, "TypeId", "") or "").startswith("App::DocumentObjectGroup")
                    ),
                    "",
                )
                or getattr(obj, "Label", getattr(obj, "Name", "0"))
                or "0"
            )
            color = None
            view_object = getattr(obj, "ViewObject", None)
            rgb = getattr(view_object, "LineColor", None) if view_object is not None else None
            if rgb and len(rgb) >= 3:
                color = "#{:02x}{:02x}{:02x}".format(
                    round(float(rgb[0]) * 255),
                    round(float(rgb[1]) * 255),
                    round(float(rgb[2]) * 255),
                )
            layers.setdefault(
                source_layer,
                ImportLayerDescriptor(source_layer, source_layer, color=color),
            )
            for entity in result.entities:
                metadata = dict(getattr(entity, "metadata", {}) or {})
                metadata.update(_entity_metadata(source_layer))
                entities.append(replace(entity, metadata=metadata))
            issues.extend(result.issues)
        if not entities:
            raise ValueError("O importador DXF do FreeCAD não produziu geometria útil.")
    finally:
        FreeCAD.closeDocument(document.Name)
    file_bytes = Path(path).read_bytes()
    return ImportResult(
        tuple(entities),
        tuple(issues),
        {
            "source_kind": "dxf",
            "source_path": str(path),
            "source_fingerprint": hashlib.sha256(file_bytes).hexdigest(),
            "backend": "freecad_temp_document",
        },
        layers,
    )


def import_dxf(
    path: str | Path,
    *,
    layer_id: str,
    backend: str = "native",
) -> ImportResult:
    if backend == "native":
        return import_dxf_ascii(path, layer_id=layer_id)
    if backend == "freecad":
        return import_dxf_with_freecad(path, layer_id=layer_id)
    raise ValueError("backend DXF deve ser 'native' ou 'freecad'.")
