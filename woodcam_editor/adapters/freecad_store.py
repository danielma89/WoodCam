"""FreeCAD persistence adapter for the model-owned vector document.

``GeometryJSON`` is the source of truth.  ``Shape`` is rebuilt from it and is
only a display/selection cache for users outside the Editor 2D window.
"""

from __future__ import annotations

import json
import math
from contextlib import contextmanager
from typing import Any, Iterable, Optional

from woodcam_editor.application.document_store import (
    DocumentStoreError,
    StoredDocumentCorruptError,
    StoredDocumentInfo,
    VECTOR_DOCUMENT_FEATURE_NAME,
    VectorDocumentStore,
)
from woodcam_tree import ensure_woodcam_tree


PROPERTY_GROUP = "WoodCAM 2D"
PROPERTY_SPECS = (
    ("App::PropertyInteger", "SchemaVersion"),
    ("App::PropertyString", "DocumentUUID"),
    ("App::PropertyString", "GeometryJSON"),
    ("App::PropertyInteger", "Revision"),
    ("App::PropertyString", "Checksum"),
    ("App::PropertyString", "LastMigration"),
    ("App::PropertyString", "SourceMetadataJSON"),
)


def _freecad_modules():
    try:
        import FreeCAD  # type: ignore
        import Part  # type: ignore
    except ImportError as error:  # pragma: no cover - exercised in FreeCADCmd
        raise DocumentStoreError(
            "A persistência FCStd só pode ser usada dentro do FreeCAD."
        ) from error
    return FreeCAD, Part


def _serialization_api():
    from woodcam_editor.domain.serialization import (
        deserialize_document,
        serialize_document,
    )
    try:
        from woodcam_editor.domain.document import validate_document_invariants
    except ImportError:
        def validate_document_invariants(document):
            document.validate_invariants()

    return serialize_document, deserialize_document, validate_document_invariants


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value if value is not None else {},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _attribute(value: Any, *names: str, default: Any = None) -> Any:
    for name in names:
        if hasattr(value, name):
            return getattr(value, name)
    return default


def _xy(value: Any) -> tuple[float, float]:
    if value is None:
        raise ValueError("Ponto vetorial ausente.")
    if hasattr(value, "x") and hasattr(value, "y"):
        return float(value.x), float(value.y)
    return float(value[0]), float(value[1])


def _vector(FreeCAD: Any, point: Any):
    x_value, y_value = _xy(point)
    return FreeCAD.Vector(x_value, y_value, 0.0)


def _arc_midpoint(span: Any) -> Any:
    point_at = getattr(span, "point_at", None)
    if callable(point_at):
        return point_at(0.5)

    start_x, start_y = _xy(span.start)
    end_x, end_y = _xy(span.end)
    center_x, center_y = _xy(span.center)
    start_angle = math.atan2(start_y - center_y, start_x - center_x)
    end_angle = math.atan2(end_y - center_y, end_x - center_x)
    clockwise = bool(getattr(span, "clockwise", False))
    if clockwise:
        sweep = -((start_angle - end_angle) % (math.pi * 2.0))
    else:
        sweep = (end_angle - start_angle) % (math.pi * 2.0)
    mid_angle = start_angle + sweep * 0.5
    radius = math.hypot(start_x - center_x, start_y - center_y)
    return (center_x + math.cos(mid_angle) * radius, center_y + math.sin(mid_angle) * radius)


def _span_edge(span: Any, FreeCAD: Any, Part: Any):
    class_name = type(span).__name__.lower()
    start = _attribute(span, "start")
    end = _attribute(span, "end")
    if start is None or end is None:
        return None

    start_vector = _vector(FreeCAD, start)
    end_vector = _vector(FreeCAD, end)
    if start_vector.distanceToPoint(end_vector) <= 1e-12:
        return None

    if "arc" in class_name or hasattr(span, "center"):
        middle_vector = _vector(FreeCAD, _arc_midpoint(span))
        try:
            return Part.Arc(start_vector, middle_vector, end_vector).toShape()
        except Exception:
            return Part.ArcOfCircle(start_vector, middle_vector, end_vector).toShape()

    if "bezier" in class_name or hasattr(span, "control1") or hasattr(span, "control_1"):
        control_1 = _attribute(span, "control1", "control_1")
        control_2 = _attribute(span, "control2", "control_2")
        if control_1 is None or control_2 is None:
            return None
        curve = Part.BezierCurve()
        curve.setPoles(
            [
                start_vector,
                _vector(FreeCAD, control_1),
                _vector(FreeCAD, control_2),
                end_vector,
            ]
        )
        return curve.toShape()

    return Part.makeLine(start_vector, end_vector)


def _path_shape(entity: Any, FreeCAD: Any, Part: Any):
    edges = [
        edge
        for edge in (
            _span_edge(span, FreeCAD, Part)
            for span in list(_attribute(entity, "spans", default=()) or ())
        )
        if edge is not None
    ]
    if not edges:
        return None
    if bool(getattr(entity, "closed", False)):
        try:
            return Part.Wire(edges)
        except Exception:
            pass
    return Part.makeCompound(edges) if len(edges) > 1 else edges[0]


def _circle_shape(entity: Any, FreeCAD: Any, Part: Any):
    radius = float(getattr(entity, "radius", 0.0) or 0.0)
    if radius <= 0.0:
        return None
    return Part.makeCircle(radius, _vector(FreeCAD, entity.center))


def _ellipse_shape(entity: Any, FreeCAD: Any, Part: Any):
    radius_x = float(_attribute(entity, "radius_x", "rx", default=0.0) or 0.0)
    radius_y = float(_attribute(entity, "radius_y", "ry", default=0.0) or 0.0)
    if radius_x <= 0.0 or radius_y <= 0.0:
        return None

    center = _vector(FreeCAD, entity.center)
    major = max(radius_x, radius_y)
    minor = min(radius_x, radius_y)
    shape = Part.Ellipse(center, major, minor).toShape()
    rotation = float(getattr(entity, "rotation", 0.0) or 0.0)
    # Ellipse's default major axis is X.  Swap axes by adding 90 degrees.
    if radius_y > radius_x:
        rotation += math.pi * 0.5
    if abs(rotation) > 1e-15:
        shape.rotate(center, FreeCAD.Vector(0.0, 0.0, 1.0), math.degrees(rotation))
    return shape


def build_derived_shape(vector_document: Any):
    """Build a read-only OCC cache from domain entities."""

    FreeCAD, Part = _freecad_modules()
    entities = _attribute(vector_document, "entities_by_id", default={}) or {}
    shapes = []
    for entity in entities.values() if hasattr(entities, "values") else entities:
        class_name = type(entity).__name__.lower()
        try:
            if "path" in class_name or hasattr(entity, "spans"):
                shape = _path_shape(entity, FreeCAD, Part)
            elif "ellipse" in class_name or hasattr(entity, "radius_x"):
                shape = _ellipse_shape(entity, FreeCAD, Part)
            elif "circle" in class_name or (
                hasattr(entity, "center") and hasattr(entity, "radius")
            ):
                shape = _circle_shape(entity, FreeCAD, Part)
            else:
                shape = None
            if shape is not None and not shape.isNull():
                shapes.append(shape)
        except Exception:
            # A cache failure for one unsupported entity must not destroy the
            # authoritative JSON or prevent the other entities from showing.
            continue
    if not shapes:
        return Part.Shape()
    return Part.makeCompound(shapes)


class FreeCADDocumentStore(VectorDocumentStore):
    """Persist one ``VectorDocument`` in a FreeCAD document feature."""

    def __init__(self, freecad_document: Any = None):
        FreeCAD, _Part = _freecad_modules()
        self.document = freecad_document or FreeCAD.ActiveDocument
        if self.document is None:
            raise DocumentStoreError("Abra ou crie um documento no FreeCAD.")

    def _feature(self):
        return self.document.getObject(VECTOR_DOCUMENT_FEATURE_NAME)

    def exists(self) -> bool:
        feature = self._feature()
        return bool(feature is not None and str(getattr(feature, "GeometryJSON", "") or "").strip())

    def _ensure_group(self):
        return ensure_woodcam_tree(self.document).parts

    def _ensure_feature(self):
        feature = self._feature()
        if feature is None:
            feature = self.document.addObject("Part::FeaturePython", VECTOR_DOCUMENT_FEATURE_NAME)
        feature.Label = "Documento vetorial (interno)"
        self._ensure_group().addObject(feature)
        view_object = getattr(feature, "ViewObject", None)
        if view_object is not None and hasattr(view_object, "ShowInTree"):
            try:
                view_object.ShowInTree = False
            except Exception:
                pass
        for property_type, property_name in PROPERTY_SPECS:
            if property_name not in list(getattr(feature, "PropertiesList", []) or []):
                feature.addProperty(property_type, property_name, PROPERTY_GROUP)
        return feature

    @contextmanager
    def _transaction(self, label: str, enabled: bool):
        opened = False
        try:
            if enabled:
                self.document.openTransaction(str(label))
                opened = True
            yield
            if opened:
                self.document.commitTransaction()
                opened = False
        except Exception:
            if opened:
                try:
                    self.document.abortTransaction()
                except Exception:
                    pass
            raise

    def load(self) -> Optional[Any]:
        feature = self._feature()
        if feature is None:
            return None
        # Abrir um FCStd antigo já compacta apenas a organização visual. A
        # Shape/GeometryJSON de origem permanece intocada.
        feature.Label = "Documento vetorial (interno)"
        self._ensure_group().addObject(feature)
        view_object = getattr(feature, "ViewObject", None)
        if view_object is not None and hasattr(view_object, "ShowInTree"):
            try:
                view_object.ShowInTree = False
            except Exception:
                pass
        geometry_json = str(getattr(feature, "GeometryJSON", "") or "").strip()
        if not geometry_json:
            return None

        _serialize, deserialize, validate = _serialization_api()
        expected_checksum = str(getattr(feature, "Checksum", "") or "").strip() or None
        try:
            vector_document = deserialize(
                geometry_json,
                expected_checksum=expected_checksum,
            )
            validate(vector_document)
        except Exception as error:
            raise StoredDocumentCorruptError(
                "O documento vetorial salvo falhou na validação. O JSON original "
                "foi preservado e não será sobrescrito."
            ) from error
        return vector_document

    def save(
        self,
        vector_document: Any,
        *,
        transaction_label: str = "WoodCAM 2D — Salvar desenho",
        use_transaction: bool = True,
        source_metadata: Optional[dict] = None,
    ) -> StoredDocumentInfo:
        serialize, _deserialize, validate = _serialization_api()
        validate(vector_document)
        serialized = serialize(vector_document)
        geometry_json = str(serialized.json_text)
        checksum = str(serialized.checksum)
        schema_version = int(getattr(vector_document, "schema_version", 1))
        document_uuid = str(getattr(vector_document, "document_uuid", ""))
        revision = int(getattr(vector_document, "revision", 0))
        metadata = source_metadata
        if metadata is None:
            document_metadata = dict(getattr(vector_document, "metadata", {}) or {})
            metadata = document_metadata.get("source_metadata", {})

        with self._transaction(transaction_label, use_transaction):
            feature = self._ensure_feature()
            feature.SchemaVersion = schema_version
            feature.DocumentUUID = document_uuid
            feature.GeometryJSON = geometry_json
            feature.Revision = revision
            feature.Checksum = checksum
            feature.LastMigration = str(getattr(serialized, "last_migration", "") or "")
            feature.SourceMetadataJSON = _canonical_json(metadata)
            feature.Shape = build_derived_shape(vector_document)
            self.document.recompute()

        return StoredDocumentInfo(
            feature_name=feature.Name,
            document_uuid=document_uuid,
            schema_version=schema_version,
            revision=revision,
            checksum=checksum,
        )

    def refresh_derived_shape(self, vector_document: Any) -> None:
        feature = self._feature()
        if feature is None:
            raise DocumentStoreError("O documento vetorial ainda não foi persistido.")
        feature.Shape = build_derived_shape(vector_document)
        self.document.recompute()

    def stored_info(self) -> Optional[StoredDocumentInfo]:
        feature = self._feature()
        if feature is None or not str(getattr(feature, "GeometryJSON", "") or "").strip():
            return None
        return StoredDocumentInfo(
            feature_name=feature.Name,
            document_uuid=str(getattr(feature, "DocumentUUID", "") or ""),
            schema_version=int(getattr(feature, "SchemaVersion", 0) or 0),
            revision=int(getattr(feature, "Revision", 0) or 0),
            checksum=str(getattr(feature, "Checksum", "") or ""),
        )


def find_vector_feature(freecad_document: Any = None):
    """Return the persisted feature without creating or mutating anything."""

    FreeCAD, _Part = _freecad_modules()
    document = freecad_document or FreeCAD.ActiveDocument
    return document.getObject(VECTOR_DOCUMENT_FEATURE_NAME) if document is not None else None
