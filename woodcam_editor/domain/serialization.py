"""Canonical JSON v1 serialization and checksum support."""

from __future__ import annotations

from dataclasses import dataclass
import copy
import hashlib
import json
from typing import Any, Dict, Iterable, Mapping

from .document import (
    COORDINATE_SYSTEM,
    SCHEMA_VERSION,
    UNITS,
    Layer,
    Piece2D,
    VectorDocument,
    WorkArea,
)
from .entities import CircleEntity, EllipseEntity, GroupEntity, PathEntity
from .primitives import Affine2D, GeometryError, InvariantError, Vec2
from .spans import ArcSpan, CubicBezierSpan, LineSpan


class SerializationError(ValueError):
    """Raised for malformed, unsupported or corrupt vector JSON."""


@dataclass(frozen=True, slots=True)
class SerializedDocument:
    json_text: str
    checksum: str
    schema_version: int
    document_uuid: str
    revision: int


def _point_to_data(point: Vec2):
    return [point.x, point.y]


def _point_from_data(value: Any, label: str) -> Vec2:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise SerializationError("%s must contain [x, y]" % label)
    try:
        return Vec2(value[0], value[1])
    except (TypeError, ValueError) as exc:
        raise SerializationError("invalid %s: %s" % (label, exc))


def _transform_to_data(transform: Affine2D) -> list:
    return [transform.a, transform.b, transform.c, transform.d, transform.tx, transform.ty]


def _transform_from_data(value: Any, label: str = "transform") -> Affine2D:
    if not isinstance(value, (list, tuple)) or len(value) != 6:
        raise SerializationError("%s must contain [a, b, c, d, tx, ty]" % label)
    try:
        return Affine2D(*value)
    except (TypeError, ValueError) as exc:
        raise SerializationError("invalid %s: %s" % (label, exc))


def _span_to_dict(span) -> Dict[str, Any]:
    if isinstance(span, LineSpan):
        return {
            "id": span.id,
            "type": "line",
            "start": _point_to_data(span.start),
            "end": _point_to_data(span.end),
        }
    if isinstance(span, ArcSpan):
        return {
            "id": span.id,
            "type": "arc",
            "start": _point_to_data(span.start),
            "end": _point_to_data(span.end),
            "center": _point_to_data(span.center),
            "clockwise": span.clockwise,
        }
    if isinstance(span, CubicBezierSpan):
        return {
            "id": span.id,
            "type": "cubic_bezier",
            "start": _point_to_data(span.start),
            "control1": _point_to_data(span.control1),
            "control2": _point_to_data(span.control2),
            "end": _point_to_data(span.end),
        }
    raise SerializationError("unsupported span type %r" % type(span))


def _span_from_dict(data: Mapping[str, Any], label: str):
    if not isinstance(data, Mapping):
        raise SerializationError("%s must be an object" % label)
    span_type = data.get("type")
    span_id = _required_string(data, "id", label)
    try:
        if span_type == "line":
            return LineSpan(
                _point_from_data(data.get("start"), label + ".start"),
                _point_from_data(data.get("end"), label + ".end"),
                id=span_id,
            )
        if span_type == "arc":
            return ArcSpan(
                _point_from_data(data.get("start"), label + ".start"),
                _point_from_data(data.get("end"), label + ".end"),
                _point_from_data(data.get("center"), label + ".center"),
                bool(data.get("clockwise", False)),
                id=span_id,
            )
        if span_type == "cubic_bezier":
            return CubicBezierSpan(
                _point_from_data(data.get("start"), label + ".start"),
                _point_from_data(data.get("control1"), label + ".control1"),
                _point_from_data(data.get("control2"), label + ".control2"),
                _point_from_data(data.get("end"), label + ".end"),
                id=span_id,
            )
    except (TypeError, ValueError, GeometryError) as exc:
        raise SerializationError("invalid %s: %s" % (label, exc))
    raise SerializationError("unsupported span type %r at %s" % (span_type, label))


def _entity_to_dict(entity) -> Dict[str, Any]:
    common = {
        "id": entity.id,
        "type": entity.type,
        "layer_id": entity.layer_id,
        "metadata": copy.deepcopy(dict(entity.metadata)),
    }
    if isinstance(entity, PathEntity):
        common.update(
            {
                "closed": entity.closed,
                "node_ids": list(entity.node_ids),
                "spans": [_span_to_dict(span) for span in entity.spans],
            }
        )
    elif isinstance(entity, CircleEntity):
        common.update(
            {
                "center": _point_to_data(entity.center),
                "radius": entity.radius,
                "center_node_id": entity.center_node_id,
                "radius_node_id": entity.radius_node_id,
            }
        )
    elif isinstance(entity, EllipseEntity):
        common.update(
            {
                "center": _point_to_data(entity.center),
                "radius_x": entity.radius_x,
                "radius_y": entity.radius_y,
                "rotation": entity.rotation,
                "center_node_id": entity.center_node_id,
            }
        )
    elif isinstance(entity, GroupEntity):
        common.update(
            {
                "child_ids": list(entity.child_ids),
                "transform": _transform_to_data(entity.transform),
            }
        )
    else:
        raise SerializationError("unsupported entity type %r" % type(entity))
    return common


def _entity_from_dict(data: Mapping[str, Any], index: int):
    label = "entities[%d]" % index
    if not isinstance(data, Mapping):
        raise SerializationError("%s must be an object" % label)
    entity_type = data.get("type")
    common = {
        "id": _required_string(data, "id", label),
        "layer_id": _required_string(data, "layer_id", label),
        "metadata": _mapping(data.get("metadata", {}), label + ".metadata"),
    }
    try:
        if entity_type == "path":
            spans_data = data.get("spans")
            if not isinstance(spans_data, list) or not spans_data:
                raise SerializationError("%s.spans must be a non-empty list" % label)
            return PathEntity(
                spans=tuple(
                    _span_from_dict(span_data, "%s.spans[%d]" % (label, span_index))
                    for span_index, span_data in enumerate(spans_data)
                ),
                closed=bool(data.get("closed", False)),
                node_ids=tuple(_string_list(data.get("node_ids", []), label + ".node_ids")),
                **common,
            )
        if entity_type == "circle":
            return CircleEntity(
                center=_point_from_data(data.get("center"), label + ".center"),
                radius=data.get("radius"),
                center_node_id=_required_string(data, "center_node_id", label),
                radius_node_id=_required_string(data, "radius_node_id", label),
                **common,
            )
        if entity_type == "ellipse":
            return EllipseEntity(
                center=_point_from_data(data.get("center"), label + ".center"),
                radius_x=data.get("radius_x"),
                radius_y=data.get("radius_y"),
                rotation=data.get("rotation", 0.0),
                center_node_id=_required_string(data, "center_node_id", label),
                **common,
            )
        if entity_type == "group":
            return GroupEntity(
                child_ids=tuple(_string_list(data.get("child_ids"), label + ".child_ids")),
                transform=_transform_from_data(data.get("transform", [1, 0, 0, 1, 0, 0]), label + ".transform"),
                **common,
            )
    except SerializationError:
        raise
    except (TypeError, ValueError, GeometryError, InvariantError) as exc:
        raise SerializationError("invalid %s: %s" % (label, exc))
    raise SerializationError("unsupported entity type %r at %s" % (entity_type, label))


def _required_string(data: Mapping[str, Any], key: str, label: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value:
        raise SerializationError("%s.%s must be a non-empty string" % (label, key))
    return value


def _mapping(value: Any, label: str) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SerializationError("%s must be an object" % label)
    return copy.deepcopy(dict(value))


def _string_list(value: Any, label: str) -> list:
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise SerializationError("%s must be a list of non-empty strings" % label)
    return list(value)


def document_to_dict(document: VectorDocument) -> Dict[str, Any]:
    document.validate_invariants()
    unknown = document.metadata.get("_unknown_top_level", {})
    result = copy.deepcopy(unknown) if isinstance(unknown, Mapping) else {}
    metadata = copy.deepcopy(document.metadata)
    metadata.pop("_unknown_top_level", None)
    result.update(
        {
            "schema_version": document.schema_version,
            "document_uuid": document.document_uuid,
            "revision": document.revision,
            "units": document.units,
            "coordinate_system": document.coordinate_system,
            "work_area": None
            if document.work_area is None
            else {
                "min_x": document.work_area.min_x,
                "min_y": document.work_area.min_y,
                "max_x": document.work_area.max_x,
                "max_y": document.work_area.max_y,
                "source": document.work_area.source,
            },
            "layers": [
                {
                    "id": layer.id,
                    "name": layer.name,
                    "color": layer.color,
                    "visible": layer.visible,
                    "locked": layer.locked,
                    "order": layer.order,
                    "purpose": layer.purpose,
                    "metadata": copy.deepcopy(dict(layer.metadata)),
                }
                for layer in sorted(document.layers_by_id.values(), key=lambda item: (item.order, item.id))
            ],
            "entities": [
                _entity_to_dict(entity)
                for entity in sorted(document.entities_by_id.values(), key=lambda item: item.id)
            ],
            "pieces": [
                {
                    "id": piece.id,
                    "name": piece.name,
                    "outer_path_id": piece.outer_path_id,
                    "inner_path_ids": list(piece.inner_path_ids),
                    "quantity": piece.quantity,
                    "material": piece.material,
                    "thickness": piece.thickness,
                    "grain_direction": piece.grain_direction,
                    "rotations_allowed": list(piece.rotations_allowed),
                    "placement": _transform_to_data(piece.placement),
                    "metadata": copy.deepcopy(dict(piece.metadata)),
                    "stale": piece.stale,
                }
                for piece in sorted(document.pieces_by_id.values(), key=lambda item: item.id)
            ],
            "active_layer_id": document.active_layer_id,
            "metadata": metadata,
        }
    )
    # Fail early on metadata containing NaN, objects, etc.
    canonical_json(result)
    return result


def document_from_dict(data: Mapping[str, Any]) -> VectorDocument:
    if not isinstance(data, Mapping):
        raise SerializationError("document root must be an object")
    data = migrate_to_current(dict(data))
    known = {
        "schema_version",
        "document_uuid",
        "revision",
        "units",
        "coordinate_system",
        "work_area",
        "layers",
        "entities",
        "pieces",
        "active_layer_id",
        "metadata",
    }
    metadata = _mapping(data.get("metadata", {}), "metadata")
    unknown = {key: copy.deepcopy(value) for key, value in data.items() if key not in known}
    if unknown:
        metadata["_unknown_top_level"] = unknown

    layers_data = data.get("layers")
    if not isinstance(layers_data, list) or not layers_data:
        raise SerializationError("layers must be a non-empty list")
    layers = []
    for index, item in enumerate(layers_data):
        label = "layers[%d]" % index
        if not isinstance(item, Mapping):
            raise SerializationError("%s must be an object" % label)
        try:
            layers.append(
                Layer(
                    id=_required_string(item, "id", label),
                    name=_required_string(item, "name", label),
                    color=str(item.get("color", "#2563eb")),
                    visible=bool(item.get("visible", True)),
                    locked=bool(item.get("locked", False)),
                    order=int(item.get("order", index)),
                    purpose=str(item.get("purpose", "design")),
                    metadata=_mapping(item.get("metadata", {}), label + ".metadata"),
                )
            )
        except (TypeError, ValueError, InvariantError) as exc:
            raise SerializationError("invalid %s: %s" % (label, exc))

    work_area_data = data.get("work_area")
    work_area = None
    if work_area_data is not None:
        if not isinstance(work_area_data, Mapping):
            raise SerializationError("work_area must be an object or null")
        try:
            work_area = WorkArea(
                work_area_data.get("min_x"),
                work_area_data.get("min_y"),
                work_area_data.get("max_x"),
                work_area_data.get("max_y"),
                str(work_area_data.get("source", "woodcam_trabalho")),
            )
        except (TypeError, ValueError, InvariantError) as exc:
            raise SerializationError("invalid work_area: %s" % exc)

    entities_data = data.get("entities", [])
    pieces_data = data.get("pieces", [])
    if not isinstance(entities_data, list) or not isinstance(pieces_data, list):
        raise SerializationError("entities and pieces must be lists")
    entities = [_entity_from_dict(item, index) for index, item in enumerate(entities_data)]
    pieces = []
    for index, item in enumerate(pieces_data):
        label = "pieces[%d]" % index
        if not isinstance(item, Mapping):
            raise SerializationError("%s must be an object" % label)
        try:
            pieces.append(
                Piece2D(
                    id=_required_string(item, "id", label),
                    name=_required_string(item, "name", label),
                    outer_path_id=_required_string(item, "outer_path_id", label),
                    inner_path_ids=tuple(_string_list(item.get("inner_path_ids", []), label + ".inner_path_ids")),
                    quantity=int(item.get("quantity", 1)),
                    material=str(item.get("material", "")),
                    thickness=float(item.get("thickness", 0.0)),
                    grain_direction=item.get("grain_direction"),
                    rotations_allowed=tuple(item.get("rotations_allowed", (0.0, 90.0))),
                    placement=_transform_from_data(item.get("placement", [1, 0, 0, 1, 0, 0]), label + ".placement"),
                    metadata=_mapping(item.get("metadata", {}), label + ".metadata"),
                    stale=bool(item.get("stale", False)),
                )
            )
        except SerializationError:
            raise
        except (TypeError, ValueError, InvariantError) as exc:
            raise SerializationError("invalid %s: %s" % (label, exc))

    try:
        return VectorDocument(
            schema_version=int(data.get("schema_version")),
            document_uuid=_required_string(data, "document_uuid", "document"),
            revision=int(data.get("revision", 0)),
            units=str(data.get("units", UNITS)),
            coordinate_system=str(data.get("coordinate_system", COORDINATE_SYSTEM)),
            work_area=work_area,
            layers_by_id={layer.id: layer for layer in layers},
            entities_by_id={entity.id: entity for entity in entities},
            pieces_by_id={piece.id: piece for piece in pieces},
            active_layer_id=_required_string(data, "active_layer_id", "document"),
            metadata=metadata,
        )
    except (TypeError, ValueError, InvariantError) as exc:
        raise SerializationError("invalid document: %s" % exc)


def canonical_json(data: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            data,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SerializationError("data is not valid canonical JSON: %s" % exc)


def document_to_json(document: VectorDocument, *, pretty: bool = False) -> str:
    data = document_to_dict(document)
    if pretty:
        try:
            return json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise SerializationError("document is not JSON serialisable: %s" % exc)
    return canonical_json(data)


def document_from_json(text: str) -> VectorDocument:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SerializationError("invalid JSON: %s" % exc)
    return document_from_dict(data)


def checksum_for_data(data: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


def document_checksum(document: VectorDocument) -> str:
    return checksum_for_data(document_to_dict(document))


def geometry_checksum(document: VectorDocument) -> str:
    """Checksum of vector entities, excluding derived/document metadata.

    CAM operations depend on the entities that produced their toolpaths. The
    Piece2D classification cache and document revision can change while those
    entities remain identical, so they must not make an operation stale.
    """
    entities = [
        _entity_to_dict(entity)
        for entity in sorted(document.entities_by_id.values(), key=lambda item: item.id)
    ]
    return checksum_for_data({"entities": entities})


def serialize_document(document: VectorDocument, *, pretty: bool = False) -> SerializedDocument:
    data = document_to_dict(document)
    checksum = checksum_for_data(data)
    json_text = (
        json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False)
        if pretty
        else canonical_json(data)
    )
    return SerializedDocument(
        json_text=json_text,
        checksum=checksum,
        schema_version=document.schema_version,
        document_uuid=document.document_uuid,
        revision=document.revision,
    )


def deserialize_document(text: str, expected_checksum: str | None = None) -> VectorDocument:
    try:
        data = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise SerializationError("invalid JSON: %s" % exc)
    if not isinstance(data, Mapping):
        raise SerializationError("document root must be an object")
    actual_checksum = checksum_for_data(data)
    if expected_checksum is not None and actual_checksum.lower() != expected_checksum.lower():
        raise SerializationError(
            "checksum mismatch: expected %s, got %s" % (expected_checksum, actual_checksum)
        )
    return document_from_dict(data)


def migrate_to_current(data: Dict[str, Any]) -> Dict[str, Any]:
    try:
        version = int(data.get("schema_version"))
    except (TypeError, ValueError):
        raise SerializationError("schema_version is required and must be an integer")
    if version > SCHEMA_VERSION:
        raise SerializationError(
            "document schema v%d is newer than supported v%d" % (version, SCHEMA_VERSION)
        )
    if version < 1:
        raise SerializationError("unsupported document schema v%d" % version)
    # Explicit migration steps are appended here when schema v2 is introduced.
    migrated = copy.deepcopy(data)
    if version != SCHEMA_VERSION:
        raise SerializationError("no migration path from v%d to v%d" % (version, SCHEMA_VERSION))
    return migrated
