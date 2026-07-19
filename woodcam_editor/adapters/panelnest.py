"""Stable, optional bridge between Editor 2D pieces and PanelNest."""

from __future__ import annotations

import hashlib
import importlib
import json
import math
from dataclasses import dataclass, field, fields, is_dataclass, replace
from typing import Any, Mapping, Optional, Sequence

from woodcam_editor.adapters.woodcam_geometry import (
    GeometryAdapterError,
    document_to_woodcam_geometry,
)


@dataclass(frozen=True)
class PanelPart:
    """PanelNest-facing piece contract; internals always travel with the outer."""

    id: str
    profile_points: tuple[tuple[float, float], ...]
    inner_profile_loops: tuple[tuple[tuple[float, float], ...], ...] = ()
    circular_holes: tuple[Mapping[str, Any], ...] = ()
    name: str = ""
    quantity: int = 1
    material: str = ""
    thickness: float = 0.0
    grain_direction: Optional[float] = None
    rotations_allowed: tuple[float, ...] = (0.0, 90.0)
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "profile_points": [list(point) for point in self.profile_points],
            "inner_profile_loops": [
                [list(point) for point in loop]
                for loop in self.inner_profile_loops
            ],
            "circular_holes": [dict(hole) for hole in self.circular_holes],
            "name": self.name,
            "quantity": self.quantity,
            "material": self.material,
            "thickness": self.thickness,
            "grain_direction": self.grain_direction,
            "rotations_allowed": list(self.rotations_allowed),
            "metadata": dict(self.metadata),
        }


class PanelNestBridgeError(RuntimeError):
    """Raised when a safe Editor 2D -> PanelNest exchange cannot be built."""


class PanelNestContractError(PanelNestBridgeError):
    """Raised when a native PanelNest model would lose WoodCAM geometry."""


@dataclass(frozen=True)
class PanelNestContract:
    """Runtime capabilities discovered without making PanelNest mandatory."""

    available: bool
    module_name: str = ""
    native_panel_part: bool = False
    native_fields: tuple[str, ...] = ()
    supports_inner_profile_loops: bool = False
    has_ensure_part_properties: bool = False
    has_collect_parts: bool = False
    has_direct_part_import: bool = False

    def to_dict(self) -> dict:
        return {
            "available": self.available,
            "module_name": self.module_name,
            "native_panel_part": self.native_panel_part,
            "native_fields": list(self.native_fields),
            "supports_inner_profile_loops": self.supports_inner_profile_loops,
            "has_ensure_part_properties": self.has_ensure_part_properties,
            "has_collect_parts": self.has_collect_parts,
            "has_direct_part_import": self.has_direct_part_import,
        }


@dataclass(frozen=True)
class PanelNestExchangeItem:
    """One classified Editor 2D piece materialized for PanelNest."""

    part_id: str
    object_names: tuple[str, ...]
    label: str
    quantity: int
    outer_point_count: int
    inner_profile_count: int
    circular_hole_count: int
    source_offset: tuple[float, float]
    shape_volume_mm3: float
    warnings: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "part_id": self.part_id,
            "object_names": list(self.object_names),
            "label": self.label,
            "quantity": self.quantity,
            "outer_point_count": self.outer_point_count,
            "inner_profile_count": self.inner_profile_count,
            "circular_hole_count": self.circular_hole_count,
            "source_offset": list(self.source_offset),
            "shape_volume_mm3": self.shape_volume_mm3,
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class PanelNestExchangeResult:
    """Structured, UI-friendly outcome of an exchange operation."""

    mode: str
    group_name: str
    document_uuid: str
    source_revision: int
    revision_token: str
    contract: PanelNestContract
    items: tuple[PanelNestExchangeItem, ...]
    recognized_part_count: int = 0
    warnings: tuple[str, ...] = ()

    @property
    def object_names(self) -> tuple[str, ...]:
        return tuple(name for item in self.items for name in item.object_names)

    def to_dict(self) -> dict:
        return {
            "mode": self.mode,
            "group_name": self.group_name,
            "document_uuid": self.document_uuid,
            "source_revision": self.source_revision,
            "revision_token": self.revision_token,
            "contract": self.contract.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "recognized_part_count": self.recognized_part_count,
            "warnings": list(self.warnings),
        }


_AUTO_PANELNEST = object()
EXCHANGE_ROOT_NAME = "WoodCAM2DPanelNestExchange"
EXCHANGE_ROOT_LABEL = "WoodCAM 2D — PanelNest (layout do Editor preservado)"
EXCHANGE_SCHEMA_VERSION = 1
EXCHANGE_PROPERTY_GROUP = "WoodCAM 2D / PanelNest"
EXCHANGE_LAYOUT_MODE = "preserve_editor_xy"


def _load_panelnest_module():
    try:
        return importlib.import_module("panelnest")
    # A sibling workbench must never prevent WoodCAM itself from opening.  A
    # broken/transitional PanelNest installation is treated as unavailable and
    # the lossless FreeCAD exchange remains usable.
    except Exception:
        return None


def _native_field_names(native_type: Any) -> tuple[str, ...]:
    if native_type is None:
        return ()
    if is_dataclass(native_type):
        return tuple(item.name for item in fields(native_type))
    annotations = getattr(native_type, "__annotations__", {}) or {}
    return tuple(str(name) for name in annotations)


def inspect_panelnest_contract(panelnest_module: Any = _AUTO_PANELNEST) -> PanelNestContract:
    """Inspect the installed sibling lazily; ``None`` explicitly means absent."""

    module = _load_panelnest_module() if panelnest_module is _AUTO_PANELNEST else panelnest_module
    if module is None:
        return PanelNestContract(available=False)
    native_type = getattr(module, "PanelPart", None)
    native_fields = _native_field_names(native_type)
    direct_import_names = (
        "import_panel_parts",
        "register_panel_parts",
        "create_parts_from_model",
        "send_panel_parts",
    )
    return PanelNestContract(
        available=True,
        module_name=str(getattr(module, "__name__", type(module).__name__)),
        native_panel_part=callable(native_type),
        native_fields=native_fields,
        supports_inner_profile_loops="inner_profile_loops" in native_fields,
        has_ensure_part_properties=callable(getattr(module, "ensure_part_properties", None)),
        has_collect_parts=callable(getattr(module, "collect_parts", None)),
        has_direct_part_import=any(callable(getattr(module, name, None)) for name in direct_import_names),
    )


def _rotations(value: Any) -> tuple[float, ...]:
    if value is None:
        return (0.0, 90.0)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"free", "livre"}:
            return tuple(float(item) for item in range(0, 360, 15))
        if normalized in {"locked", "bloqueada", "0"}:
            return (0.0,)
        if normalized in {"0/90", "orthogonal"}:
            return (0.0, 90.0)
    try:
        values = tuple(float(item) for item in value)
        return values or (0.0,)
    except TypeError:
        return (float(value),)


def document_to_panel_parts(
    vector_document: Any,
    *,
    piece_ids: Optional[Sequence[str]] = None,
    deflection: float = 0.01,
) -> list[PanelPart]:
    """Convert classified pieces without ever promoting holes to pieces."""

    pieces = getattr(vector_document, "pieces_by_id", {}) or {}
    selected_ids = list(piece_ids) if piece_ids is not None else list(pieces)
    if not selected_ids:
        raise GeometryAdapterError(
            "Crie/classifique as Peças 2D antes de enviá-las ao PanelNest."
        )

    result = []
    for piece_id in selected_ids:
        if piece_id not in pieces:
            raise GeometryAdapterError(f"Peça 2D inexistente: {piece_id}")
        piece = pieces[piece_id]
        geometry = document_to_woodcam_geometry(
            vector_document,
            piece_ids=[piece_id],
            deflection=deflection,
        )
        contours = geometry["contours"]
        if not contours:
            raise GeometryAdapterError(f"A peça {piece_id} não possui contorno externo.")
        metadata = dict(getattr(piece, "metadata", {}) or {})
        result.append(
            PanelPart(
                id=str(piece_id),
                profile_points=tuple(tuple(point) for point in contours[0]),
                inner_profile_loops=tuple(
                    tuple(tuple(point) for point in loop)
                    for loop in contours[1:]
                ),
                circular_holes=tuple(dict(hole) for hole in geometry["holes"]),
                name=str(getattr(piece, "name", "") or ""),
                quantity=max(1, int(getattr(piece, "quantity", 1) or 1)),
                material=str(getattr(piece, "material", "") or ""),
                thickness=float(getattr(piece, "thickness", 0.0) or 0.0),
                grain_direction=getattr(piece, "grain_direction", None),
                rotations_allowed=_rotations(getattr(piece, "rotations_allowed", None)),
                metadata=metadata,
            )
        )
    return result


def _point_xy(value: Any) -> tuple[float, float]:
    if hasattr(value, "x") and hasattr(value, "y"):
        return float(value.x), float(value.y)
    return float(value[0]), float(value[1])


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def _clean_loop(values: Sequence[Any]) -> tuple[tuple[float, float], ...]:
    points: list[tuple[float, float]] = []
    for value in values:
        point = _point_xy(value)
        if not points or _distance(points[-1], point) > 1e-8:
            points.append(point)
    if len(points) > 1 and _distance(points[0], points[-1]) <= 1e-8:
        points.pop()
    return tuple(points)


def _polygon_area(points: Sequence[tuple[float, float]]) -> float:
    return 0.5 * sum(
        first[0] * second[1] - second[0] * first[1]
        for first, second in zip(points, points[1:] + points[:1])
    )


def _hole_center(hole: Mapping[str, Any]) -> tuple[float, float]:
    return (
        float(hole.get("x", hole.get("x_mm", 0.0)) or 0.0),
        float(hole.get("y", hole.get("y_mm", 0.0)) or 0.0),
    )


def _normalized_panel_part(part: PanelPart) -> tuple[PanelPart, tuple[float, float]]:
    outer = _clean_loop(part.profile_points)
    if len(outer) < 3 or abs(_polygon_area(outer)) <= 1e-8:
        raise PanelNestBridgeError(f"A peça {part.id} não possui perfil externo válido.")
    min_x = min(point[0] for point in outer)
    min_y = min(point[1] for point in outer)

    def shifted_loop(loop):
        clean = _clean_loop(loop)
        return tuple((point[0] - min_x, point[1] - min_y) for point in clean)

    normalized_holes = []
    for original in part.circular_holes:
        hole = dict(original)
        center_x, center_y = _hole_center(hole)
        hole["x"] = center_x - min_x
        hole["y"] = center_y - min_y
        hole["x_mm"] = center_x - min_x
        hole["y_mm"] = center_y - min_y
        if hole.get("points"):
            shifted_points = []
            for point in hole["points"]:
                point_x, point_y = _point_xy(point)
                shifted_points.append([point_x - min_x, point_y - min_y])
            hole["points"] = shifted_points
        normalized_holes.append(hole)

    metadata = dict(part.metadata)
    metadata.setdefault("woodcam_source_offset", [min_x, min_y])
    return (
        replace(
            part,
            profile_points=shifted_loop(outer),
            inner_profile_loops=tuple(shifted_loop(loop) for loop in part.inner_profile_loops),
            circular_holes=tuple(normalized_holes),
            metadata=metadata,
        ),
        (min_x, min_y),
    )


def _profile_dimensions(part: PanelPart) -> tuple[float, float]:
    points = _clean_loop(part.profile_points)
    return (
        max(point[0] for point in points) - min(point[0] for point in points),
        max(point[1] for point in points) - min(point[1] for point in points),
    )


def _panelnest_grain(part: PanelPart) -> tuple[str, Optional[str]]:
    if part.grain_direction is None:
        return "Livre", None
    angle = float(part.grain_direction) % 180.0
    if min(abs(angle), abs(angle - 180.0)) <= 1e-6:
        return "Comprimento da chapa", None
    if abs(angle - 90.0) <= 1e-6:
        return "Largura da chapa", None
    return (
        "Livre",
        f"A direção de veio exata ({part.grain_direction:g}°) foi mantida no JSON; "
        "o PanelNest atual só expõe Livre/Comprimento/Largura.",
    )


def _native_holes(part: PanelPart) -> list[dict]:
    holes = []
    for raw_hole in part.circular_holes:
        center_x, center_y = _hole_center(raw_hole)
        holes.append(
            {
                "x_mm": center_x,
                "y_mm": center_y,
                "diameter_mm": float(raw_hole.get("diameter_mm", 0.0) or 0.0),
                "depth_mm": float(raw_hole.get("depth_mm", part.thickness) or part.thickness),
                "face": str(raw_hole.get("face", "top") or "top"),
            }
        )
    return holes


def panel_part_to_native(
    part: PanelPart,
    *,
    object_name: str,
    panelnest_module: Any = _AUTO_PANELNEST,
) -> Any:
    """Build the real sibling model only when doing so cannot drop internals."""

    module = _load_panelnest_module() if panelnest_module is _AUTO_PANELNEST else panelnest_module
    contract = inspect_panelnest_contract(module)
    if not contract.available or not contract.native_panel_part:
        raise PanelNestContractError("O modelo PanelPart do PanelNest não está disponível.")
    normalized, _offset = _normalized_panel_part(part)
    if normalized.inner_profile_loops and not contract.supports_inner_profile_loops:
        raise PanelNestContractError(
            "O PanelPart instalado não aceita recortes internos arbitrários; "
            "use o intercâmbio FreeCAD, que preserva esses recortes no Shape e no JSON."
        )

    length_mm, width_mm = _profile_dimensions(normalized)
    grain, _warning = _panelnest_grain(normalized)
    metadata = dict(normalized.metadata)
    payload = {
        "part_id": normalized.id,
        "object_name": str(object_name),
        "label": normalized.name or normalized.id,
        "length_mm": length_mm,
        "width_mm": width_mm,
        "thickness_mm": normalized.thickness,
        "quantity": normalized.quantity,
        "material": normalized.material,
        "cut_method": str(metadata.get("cut_method", "Auto") or "Auto"),
        "allow_rotation": len(set(normalized.rotations_allowed)) > 1,
        "grain_direction": grain,
        "grain_rotated": bool(metadata.get("grain_rotated", False)),
        "grain_match_group": str(metadata.get("grain_match_group", "") or ""),
        "edge_band_top": bool(metadata.get("edge_band_top", False)),
        "edge_band_bottom": bool(metadata.get("edge_band_bottom", False)),
        "edge_band_left": bool(metadata.get("edge_band_left", False)),
        "edge_band_right": bool(metadata.get("edge_band_right", False)),
        "source_type": "WoodCAM2D",
        "holes": _native_holes(normalized),
        "hardware": list(metadata.get("hardware", []) or []),
        "profile_points": list(normalized.profile_points),
        "inner_profile_loops": [list(loop) for loop in normalized.inner_profile_loops],
    }
    supported_payload = {name: payload[name] for name in contract.native_fields if name in payload}
    return module.PanelPart(**supported_payload)


def _canonical_revision_token(vector_document: Any, parts: Sequence[PanelPart]) -> str:
    payload = {
        "document_uuid": str(getattr(vector_document, "document_uuid", "") or ""),
        "revision": int(getattr(vector_document, "revision", 0) or 0),
        "parts": [part.to_dict() for part in parts],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=list,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _freecad_modules():
    try:
        import FreeCAD as App
        import Part
    except (ImportError, ModuleNotFoundError) as error:
        raise PanelNestBridgeError(
            "O intercâmbio com PanelNest precisa ser executado dentro do FreeCAD."
        ) from error
    return App, Part


def _ensure_property(obj: Any, type_name: str, name: str, description: str = "") -> None:
    if name not in tuple(getattr(obj, "PropertiesList", ()) or ()):
        obj.addProperty(type_name, name, EXCHANGE_PROPERTY_GROUP, description)


def _set_string(obj: Any, name: str, value: Any, description: str = "") -> None:
    _ensure_property(obj, "App::PropertyString", name, description)
    setattr(obj, name, str(value))


def _set_float(obj: Any, name: str, value: Any, description: str = "") -> None:
    _ensure_property(obj, "App::PropertyFloat", name, description)
    setattr(obj, name, float(value))


def _set_integer(obj: Any, name: str, value: Any, description: str = "") -> None:
    _ensure_property(obj, "App::PropertyInteger", name, description)
    setattr(obj, name, int(value))


def _set_bool(obj: Any, name: str, value: Any, description: str = "") -> None:
    _ensure_property(obj, "App::PropertyBool", name, description)
    setattr(obj, name, bool(value))


def _set_enum(
    obj: Any,
    name: str,
    options: Sequence[str],
    value: str,
    description: str = "",
) -> None:
    _ensure_property(obj, "App::PropertyEnumeration", name, description)
    current = value if value in options else options[0]
    setattr(obj, name, list(options))
    setattr(obj, name, current)


def _wire_from_loop(App: Any, Part: Any, values: Sequence[Any], *, label: str):
    points = _clean_loop(values)
    for index, point in enumerate(points, start=1):
        if not all(math.isfinite(coordinate) for coordinate in point):
            raise PanelNestBridgeError(
                f"{label} possui coordenada não finita no ponto {index}."
            )
    if len(points) < 3 or abs(_polygon_area(points)) <= 1e-8:
        raise PanelNestBridgeError(f"{label} não forma um contorno fechado válido.")
    vectors = [App.Vector(point[0], point[1], 0.0) for point in points]
    edges = [
        Part.makeLine(vectors[index], vectors[(index + 1) % len(vectors)])
        for index in range(len(vectors))
    ]
    wire = Part.Wire(edges)
    if hasattr(wire, "isClosed") and not wire.isClosed():
        raise PanelNestBridgeError(f"{label} resultou em um wire aberto.")
    if hasattr(wire, "isValid") and not wire.isValid():
        raise PanelNestBridgeError(
            f"{label} resultou em um wire OCC inválido; verifique segmentos sobrepostos."
        )
    return wire


def _face_from_wire(Part: Any, wire: Any, *, label: str):
    """Return one valid planar region, repairing only unambiguous wire defects.

    OCC can report a closed ``Wire`` as valid while ``Part.Face(wire)`` is
    invalid (typically a repeated vertex, a touching segment or a retraced
    edge).  ``FaceMakerSimple`` splits that topology into actual bounded
    regions.  Accepting exactly one region is a safe normalization; multiple
    regions would silently turn one classified panel into several parts and
    are therefore rejected with an actionable message.
    """

    direct_face = None
    try:
        direct_face = Part.Face(wire)
    except Exception:
        direct_face = None
    if (
        direct_face is not None
        and (not hasattr(direct_face, "isValid") or direct_face.isValid())
        and float(getattr(direct_face, "Area", 0.0) or 0.0) > 1e-8
    ):
        return direct_face

    try:
        normalized = Part.makeFace([wire], "Part::FaceMakerSimple")
    except Exception as error:
        raise PanelNestBridgeError(
            f"{label} não forma uma face OCC válida; verifique cruzamentos "
            f"ou segmentos sobrepostos: {error}"
        ) from error
    faces = tuple(getattr(normalized, "Faces", ()) or ())
    if len(faces) != 1:
        if faces:
            raise PanelNestBridgeError(
                f"{label} se cruza ou se sobrepõe e foi dividido pelo OCC em "
                f"{len(faces)} regiões; corrija esse vetor antes de enviar ao PanelNest."
            )
        raise PanelNestBridgeError(
            f"{label} não delimita nenhuma região preenchível; corrija esse vetor "
            "antes de enviar ao PanelNest."
        )
    face = faces[0]
    if (
        (hasattr(face, "isValid") and not face.isValid())
        or float(getattr(face, "Area", 0.0) or 0.0) <= 1e-8
    ):
        raise PanelNestBridgeError(
            f"{label} continuou inválido após a normalização OCC; verifique "
            "autocruzamentos e trechos duplicados."
        )
    return face


def _shape_is_valid(shape: Any) -> bool:
    if shape is None:
        return False
    try:
        if hasattr(shape, "isNull") and shape.isNull():
            return False
        if hasattr(shape, "isValid") and not shape.isValid():
            return False
    except Exception:
        return False
    return True


def _shape_volume(shape: Any) -> float:
    try:
        return float(getattr(shape, "Volume", 0.0) or 0.0)
    except Exception:
        return 0.0


def _shape_scale(shape: Any, thickness: float) -> float:
    try:
        diagonal = float(shape.BoundBox.DiagonalLength)
    except Exception:
        diagonal = 0.0
    return max(1.0, diagonal, float(thickness))


def _refined_shape_candidates(shape: Any, *, precision: float) -> tuple[Any, ...]:
    candidates = [shape]
    try:
        refined = shape.removeSplitter()
        if refined is not None:
            candidates.append(refined)
    except Exception:
        pass
    try:
        repaired = shape.copy() if hasattr(shape, "copy") else shape
        if hasattr(repaired, "fix") and repaired.fix(
            precision,
            precision,
            max(precision * 100.0, 1e-6),
        ):
            candidates.append(repaired)
            try:
                refined_repair = repaired.removeSplitter()
                if refined_repair is not None:
                    candidates.append(refined_repair)
            except Exception:
                pass
    except Exception:
        pass
    return tuple(candidates)


def _validate_cut_result(
    result: Any,
    cutter_records: Sequence[tuple[str, Any]],
    *,
    precision: float,
    volume_tolerance: float,
) -> tuple[Any, str]:
    failures: list[str] = []
    for candidate in _refined_shape_candidates(result, precision=precision):
        if not _shape_is_valid(candidate):
            failures.append("o resultado OCC permaneceu inválido")
            continue
        volume = _shape_volume(candidate)
        if volume <= volume_tolerance:
            failures.append("os recortes removeram todo o volume da peça")
            continue
        solids = tuple(getattr(candidate, "Solids", ()) or ())
        if len(solids) != 1:
            failures.append(
                f"os recortes dividiram a peça em {len(solids)} sólidos independentes"
            )
            continue
        missing = []
        for label, cutter in cutter_records:
            try:
                remaining = candidate.common(cutter)
                remaining_volume = _shape_volume(remaining)
            except Exception:
                missing.append(label)
                continue
            if remaining_volume > volume_tolerance:
                missing.append(label)
        if missing:
            failures.append(
                "o resultado ainda contém material em " + ", ".join(missing)
            )
            continue
        return candidate, ""
    return None, "; ".join(dict.fromkeys(failures)) or "o OCC não gerou resultado"


def _cut_shape_with_fallbacks(
    outer_shape: Any,
    cutter_records: Sequence[tuple[str, Any]],
    *,
    piece_id: str,
    thickness: float,
):
    if not cutter_records:
        return outer_shape
    scale = _shape_scale(outer_shape, thickness)
    precision = max(1e-7, scale * 1e-10)
    volume_tolerance = max(1e-6, _shape_volume(outer_shape) * 1e-10)
    cutters = tuple(cutter for _label, cutter in cutter_records)
    failures: list[str] = []

    # A single General Fuse operation avoids the invalid intermediate solids
    # produced by sequential cuts when two recuts touch or overlap.
    for fuzzy_tolerance in (0.0, precision * 10.0):
        try:
            if fuzzy_tolerance > 0.0:
                candidate = outer_shape.cut(cutters, fuzzy_tolerance)
            elif len(cutters) == 1:
                candidate = outer_shape.cut(cutters[0])
            else:
                candidate = outer_shape.cut(cutters)
        except Exception as error:
            failures.append(f"falha booleana OCC: {error}")
            continue
        validated, reason = _validate_cut_result(
            candidate,
            cutter_records,
            precision=precision,
            volume_tolerance=volume_tolerance,
        )
        if validated is not None:
            return validated
        failures.append(reason)

    # Compatibility fallback for OCC builds whose multi-tool Boolean is less
    # stable.  Every intermediate is refined, while completeness is checked
    # only against the final solid so overlapping cutters are not discarded.
    try:
        candidate = outer_shape
        for cutter in cutters:
            candidate = candidate.cut(cutter)
            try:
                refined = candidate.removeSplitter()
                if refined is not None:
                    candidate = refined
            except Exception:
                pass
        validated, reason = _validate_cut_result(
            candidate,
            cutter_records,
            precision=precision,
            volume_tolerance=volume_tolerance,
        )
        if validated is not None:
            return validated
        failures.append(reason)
    except Exception as error:
        failures.append(f"fallback sequencial OCC falhou: {error}")

    detail = "; ".join(dict.fromkeys(item for item in failures if item))
    raise PanelNestBridgeError(
        f"Não foi possível gerar um sólido OCC seguro para a peça {piece_id}: "
        f"{detail}. Nenhum Shape dessa peça foi enviado ao PanelNest."
    )


def _shape_for_part(App: Any, Part: Any, part: PanelPart):
    if part.thickness <= 0.0:
        raise PanelNestBridgeError(
            f"Defina uma espessura maior que zero para a peça {part.id} antes de enviá-la."
        )
    outer_label = f"Perfil externo da peça {part.id}"
    outer_wire = _wire_from_loop(App, Part, part.profile_points, label=outer_label)
    try:
        outer_face = _face_from_wire(Part, outer_wire, label=outer_label)
        shape = outer_face.extrude(App.Vector(0.0, 0.0, part.thickness))
    except Exception as error:
        if isinstance(error, PanelNestBridgeError):
            raise
        raise PanelNestBridgeError(
            f"Não foi possível criar o sólido externo da peça {part.id}: {error}"
        ) from error

    if not _shape_is_valid(shape) or _shape_volume(shape) <= 1e-8:
        raise PanelNestBridgeError(
            f"O perfil externo da peça {part.id} não gerou um sólido OCC válido."
        )
    outer_solids = tuple(getattr(shape, "Solids", ()) or ())
    if len(outer_solids) != 1:
        raise PanelNestBridgeError(
            f"O perfil externo da peça {part.id} gerou {len(outer_solids)} sólidos; "
            "classifique cada região como uma peça separada antes de enviar."
        )

    cutter_records: list[tuple[str, Any]] = []

    for index, loop in enumerate(part.inner_profile_loops, start=1):
        cutter_label = f"recorte interno {index} da peça {part.id}"
        inner_wire = _wire_from_loop(
            App,
            Part,
            loop,
            label=cutter_label.capitalize(),
        )
        try:
            inner_face = _face_from_wire(
                Part,
                inner_wire,
                label=cutter_label.capitalize(),
            )
            cutter = inner_face.extrude(App.Vector(0.0, 0.0, part.thickness))
        except Exception as error:
            if isinstance(error, PanelNestBridgeError):
                raise
            raise PanelNestBridgeError(
                f"Falha ao cortar o recorte interno {index} da peça {part.id}: {error}"
            ) from error
        try:
            intersection_volume = _shape_volume(shape.common(cutter))
        except Exception as error:
            raise PanelNestBridgeError(
                f"Não foi possível validar o recorte interno {index} da peça "
                f"{part.id}: {error}"
            ) from error
        if intersection_volume <= max(1e-6, _shape_volume(shape) * 1e-10):
            raise PanelNestBridgeError(
                f"O recorte interno {index} da peça {part.id} não intersecta o perfil externo."
            )
        cutter_records.append((cutter_label, cutter))

    for index, hole in enumerate(part.circular_holes, start=1):
        center_x, center_y = _hole_center(hole)
        diameter = float(hole.get("diameter_mm", 0.0) or 0.0)
        if diameter <= 0.0:
            raise PanelNestBridgeError(
                f"O furo circular {index} da peça {part.id} possui diâmetro inválido."
            )
        requested_depth = float(hole.get("depth_mm", 0.0) or 0.0)
        depth = part.thickness if requested_depth <= 0.0 else min(part.thickness, requested_depth)
        face = str(hole.get("face", "top") or "top").lower()
        start_z = part.thickness - depth if face in {"top", "superior"} else 0.0
        # Slightly overcut through-holes to avoid coplanar boolean remnants.
        if depth >= part.thickness - 1e-8:
            start_z = -0.01
            depth = part.thickness + 0.02
        try:
            cutter = Part.makeCylinder(
                diameter / 2.0,
                depth,
                App.Vector(center_x, center_y, start_z),
                App.Vector(0.0, 0.0, 1.0),
            )
        except Exception as error:
            raise PanelNestBridgeError(
                f"Falha ao cortar o furo circular {index} da peça {part.id}: {error}"
            ) from error
        try:
            intersection_volume = _shape_volume(shape.common(cutter))
        except Exception as error:
            raise PanelNestBridgeError(
                f"Não foi possível validar o furo circular {index} da peça "
                f"{part.id}: {error}"
            ) from error
        if intersection_volume <= max(1e-6, _shape_volume(shape) * 1e-10):
            raise PanelNestBridgeError(
                f"O furo circular {index} da peça {part.id} não intersecta o perfil externo."
            )
        cutter_records.append((f"furo circular {index} da peça {part.id}", cutter))

    return _cut_shape_with_fallbacks(
        shape,
        cutter_records,
        piece_id=part.id,
        thickness=part.thickness,
    )


def _set_exchange_properties(
    feature: Any,
    part: PanelPart,
    *,
    document_uuid: str,
    source_revision: int,
    revision_token: str,
    occurrence: int,
) -> tuple[str, ...]:
    metadata = dict(part.metadata)
    length_mm, width_mm = _profile_dimensions(part)
    grain, grain_warning = _panelnest_grain(part)
    warnings = tuple(item for item in (grain_warning,) if item)

    _set_string(feature, "WoodCAMExchangeType", "panel_part")
    _set_integer(feature, "WoodCAMExchangeSchemaVersion", EXCHANGE_SCHEMA_VERSION)
    _set_string(feature, "WoodCAMSourceDocumentUuid", document_uuid)
    _set_integer(feature, "WoodCAMSourceRevision", source_revision)
    _set_string(feature, "WoodCAMSourceRevisionToken", revision_token)
    _set_string(feature, "WoodCAMSourcePieceId", part.id)
    _set_integer(feature, "WoodCAMSourceOccurrence", occurrence)
    _set_string(
        feature,
        "WoodCAMPanelPartJson",
        json.dumps(part.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=list),
        "Contrato completo: perfil externo, recortes, furos e metadados",
    )
    _set_string(
        feature,
        "WoodCAMInnerProfilesJson",
        json.dumps(part.to_dict()["inner_profile_loops"], ensure_ascii=False, separators=(",", ":")),
    )

    # These names/types are the real contract consumed by PanelNest.collect_parts.
    _set_string(feature, "PanelNestId", f"{part.id}-{occurrence:03d}")
    _set_string(feature, "PanelNestBaseLabel", part.name or part.id)
    _set_string(feature, "PanelNestMaterial", part.material)
    _set_string(
        feature,
        "PanelNestHardwareJson",
        json.dumps(list(metadata.get("hardware", []) or []), ensure_ascii=False, separators=(",", ":")),
    )
    _set_float(feature, "PanelNestLengthMm", length_mm)
    _set_float(feature, "PanelNestWidthMm", width_mm)
    _set_float(feature, "PanelNestThicknessMm", part.thickness)
    _set_integer(feature, "PanelNestQuantity", 1)
    _set_enum(
        feature,
        "PanelNestCutMethod",
        ("Auto", "CNC", "Seccionadora"),
        str(metadata.get("cut_method", "Auto") or "Auto"),
    )
    _set_bool(feature, "PanelNestAllowRotation", len(set(part.rotations_allowed)) > 1)
    _set_enum(
        feature,
        "PanelNestGrainDirection",
        ("Livre", "Comprimento da chapa", "Largura da chapa"),
        grain,
    )
    _set_string(feature, "PanelNestGrainMatchGroup", metadata.get("grain_match_group", "") or "")
    _set_bool(feature, "PanelNestGrainRotated", metadata.get("grain_rotated", False))
    for suffix in ("Top", "Bottom", "Left", "Right"):
        key = "edge_band_" + suffix.lower()
        _set_bool(feature, "PanelNestEdgeBand" + suffix, metadata.get(key, False))
    return warnings


def _apply_source_pose(
    App: Any,
    feature: Any,
    source_offset: tuple[float, float],
) -> None:
    """Restore the final Editor pose after local OCC normalization.

    ``document_to_panel_parts`` already applies the piece's complete affine
    transform, including its current rotation.  ``_normalized_panel_part``
    then subtracts the world XY minimum solely to make a stable local OCC
    solid.  The orientation therefore remains baked into the local profile;
    restoring this translation places the feature exactly where the Editor
    shows it, on XY with Z=0, without invoking any PanelNest layout command.
    """

    offset_x, offset_y = map(float, source_offset)
    feature.Placement = App.Placement(
        App.Vector(offset_x, offset_y, 0.0),
        App.Rotation(),
    )
    _set_float(feature, "WoodCAMSourcePlacementX", offset_x)
    _set_float(feature, "WoodCAMSourcePlacementY", offset_y)
    _set_float(feature, "WoodCAMSourcePlacementZ", 0.0)
    _set_string(feature, "WoodCAMSourcePlane", "XY")
    _set_string(feature, "WoodCAMSourceRotationMode", "baked_in_profile")
    _set_string(feature, "WoodCAMExchangeLayoutMode", EXCHANGE_LAYOUT_MODE)
    _set_bool(feature, "WoodCAMPanelNestAutoNesting", False)


def _remove_previous_exchange(freecad_document: Any) -> None:
    root = freecad_document.getObject(EXCHANGE_ROOT_NAME)
    if root is None or str(getattr(root, "WoodCAMExchangeType", "") or "") != "panelnest_exchange_root":
        return
    children = list(getattr(root, "Group", ()) or ())
    for child in reversed(children):
        if str(getattr(child, "WoodCAMExchangeType", "") or "") == "panel_part":
            freecad_document.removeObject(child.Name)
    freecad_document.removeObject(root.Name)


def _panelnest_recognized_count(module: Any, objects: Sequence[Any]) -> tuple[int, Optional[str]]:
    collector = getattr(module, "collect_parts", None) if module is not None else None
    if not callable(collector):
        return 0, None
    try:
        try:
            recognized = collector(list(objects), include_hidden=True)
        except TypeError:
            recognized = collector(list(objects))
        return len(list(recognized or [])), None
    except Exception as error:
        return 0, f"O PanelNest não conseguiu validar os objetos de intercâmbio: {error}"


def send_document_to_panelnest(
    vector_document: Any,
    *,
    freecad_document: Any = None,
    piece_ids: Optional[Sequence[str]] = None,
    deflection: float = 0.01,
    replace_existing: bool = True,
    panelnest_module: Any = _AUTO_PANELNEST,
) -> PanelNestExchangeResult:
    """Materialize complete pieces in one undoable, optional PanelNest exchange.

    The installed PanelNest has no public consumer for arbitrary inner loops.
    Therefore the authoritative exchange is a group of ``Part::Feature`` solids:
    every recut/hole is physically subtracted from the Shape and also retained
    in JSON.  Current PanelNest commands can select this group as ordinary panel
    parts, while future adapters can consume the lossless JSON contract.
    """

    App, Part = _freecad_modules()
    document = freecad_document if freecad_document is not None else getattr(App, "ActiveDocument", None)
    if document is None:
        raise PanelNestBridgeError("Abra ou crie um documento FreeCAD antes de enviar as peças.")
    module = _load_panelnest_module() if panelnest_module is _AUTO_PANELNEST else panelnest_module
    contract = inspect_panelnest_contract(module)
    raw_parts = document_to_panel_parts(
        vector_document,
        piece_ids=piece_ids,
        deflection=deflection,
    )
    normalized_parts = []
    offsets = {}
    for raw_part in raw_parts:
        normalized, offset = _normalized_panel_part(raw_part)
        normalized_parts.append(normalized)
        offsets[normalized.id] = offset
    document_uuid = str(getattr(vector_document, "document_uuid", "") or "")
    source_revision = int(getattr(vector_document, "revision", 0) or 0)
    revision_token = _canonical_revision_token(vector_document, normalized_parts)
    warnings: list[str] = []
    if not contract.available:
        warnings.append(
            "PanelNest não está instalado: os objetos de intercâmbio foram criados e poderão ser usados quando ele estiver disponível."
        )
    elif not contract.has_direct_part_import:
        warnings.append(
            "O PanelNest instalado não expõe uma função pública para importar PanelPart; "
            "selecione o grupo de intercâmbio criado, que contém peças reconhecíveis pelo fluxo atual."
        )
    if (
        contract.available
        and any(part.inner_profile_loops for part in normalized_parts)
        and not contract.supports_inner_profile_loops
    ):
        warnings.append(
            "O PanelPart atual não possui campo para recortes internos arbitrários; "
            "eles foram preservados fisicamente no Shape e no WoodCAMPanelPartJson."
        )

    # Build and validate every OCC solid before mutating the host document.
    # FreeCAD's abortTransaction can recreate removed objects without all of
    # their dynamic properties, so relying on rollback after a geometry error
    # could destroy the previous complete exchange manifest.
    prepared_shapes = tuple(
        _shape_for_part(App, Part, part)
        for part in normalized_parts
    )

    transaction_open = False
    created_features = []
    try:
        if hasattr(document, "openTransaction"):
            document.openTransaction("WoodCAM 2D: enviar ao PanelNest")
            transaction_open = True
        if replace_existing:
            _remove_previous_exchange(document)
        root = document.addObject("App::DocumentObjectGroup", EXCHANGE_ROOT_NAME)
        root.Label = EXCHANGE_ROOT_LABEL
        _set_string(root, "WoodCAMExchangeType", "panelnest_exchange_root")
        _set_integer(root, "WoodCAMExchangeSchemaVersion", EXCHANGE_SCHEMA_VERSION)
        _set_string(root, "WoodCAMSourceDocumentUuid", document_uuid)
        _set_integer(root, "WoodCAMSourceRevision", source_revision)
        _set_string(root, "WoodCAMSourceRevisionToken", revision_token)
        _set_string(root, "WoodCAMExchangeLayoutMode", EXCHANGE_LAYOUT_MODE)
        _set_bool(root, "WoodCAMPanelNestAutoNesting", False)

        items = []
        for part_index, (part, shape) in enumerate(
            zip(normalized_parts, prepared_shapes),
            start=1,
        ):
            object_names = []
            item_warnings: list[str] = []
            for occurrence in range(1, part.quantity + 1):
                feature = document.addObject(
                    "Part::Feature",
                    f"WoodCAM2DPNPart{part_index:03d}_{occurrence:03d}",
                )
                feature.Label = (
                    f"{part.name or part.id} ({occurrence}/{part.quantity})"
                    if part.quantity > 1
                    else (part.name or part.id)
                )
                feature.Shape = shape.copy() if hasattr(shape, "copy") else shape
                _apply_source_pose(App, feature, offsets[part.id])
                ensure_properties = getattr(module, "ensure_part_properties", None) if module is not None else None
                if callable(ensure_properties):
                    ensure_properties(feature)
                item_warnings.extend(
                    _set_exchange_properties(
                        feature,
                        part,
                        document_uuid=document_uuid,
                        source_revision=source_revision,
                        revision_token=revision_token,
                        occurrence=occurrence,
                    )
                )
                root.addObject(feature)
                created_features.append(feature)
                object_names.append(feature.Name)
            items.append(
                PanelNestExchangeItem(
                    part_id=part.id,
                    object_names=tuple(object_names),
                    label=part.name or part.id,
                    quantity=part.quantity,
                    outer_point_count=len(part.profile_points),
                    inner_profile_count=len(part.inner_profile_loops),
                    circular_hole_count=len(part.circular_holes),
                    source_offset=offsets[part.id],
                    shape_volume_mm3=float(shape.Volume),
                    warnings=tuple(dict.fromkeys(item_warnings)),
                )
            )

        manifest = {
            "schema_version": EXCHANGE_SCHEMA_VERSION,
            "layout_mode": EXCHANGE_LAYOUT_MODE,
            "automatic_nesting": False,
            "document_uuid": document_uuid,
            "source_revision": source_revision,
            "revision_token": revision_token,
            "parts": [part.to_dict() for part in normalized_parts],
            "placements": {
                part.id: {
                    "base": [offsets[part.id][0], offsets[part.id][1], 0.0],
                    "plane": "XY",
                    "rotation_mode": "baked_in_profile",
                }
                for part in normalized_parts
            },
            "object_names": [feature.Name for feature in created_features],
        }
        _set_string(
            root,
            "WoodCAMExchangeManifestJson",
            json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=list),
        )
        document.recompute()
        recognized_count, recognition_warning = _panelnest_recognized_count(module, created_features)
        if recognition_warning:
            warnings.append(recognition_warning)
        if contract.has_collect_parts and recognized_count != len(created_features):
            warnings.append(
                f"O PanelNest reconheceu {recognized_count} de {len(created_features)} objeto(s) de intercâmbio."
            )
        if transaction_open:
            document.commitTransaction()
            transaction_open = False
    except Exception:
        if transaction_open and hasattr(document, "abortTransaction"):
            document.abortTransaction()
        raise

    mode = "freecad_exchange+panelnest_metadata" if contract.has_ensure_part_properties else "freecad_exchange"
    return PanelNestExchangeResult(
        mode=mode,
        group_name=root.Name,
        document_uuid=document_uuid,
        source_revision=source_revision,
        revision_token=revision_token,
        contract=contract,
        items=tuple(items),
        recognized_part_count=recognized_count,
        warnings=tuple(dict.fromkeys(warnings)),
    )


class PanelNestGeometryProvider:
    """Explicit immutable snapshot of geometry resolved from PanelNest selection."""

    def __init__(self, geometry: Mapping[str, Any], description: str = "PanelNest"):
        self._geometry = {
            "contours": [list(contour) for contour in geometry.get("contours", [])],
            "holes": [dict(hole) for hole in geometry.get("holes", [])],
        }
        self._description = str(description)
        canonical = json.dumps(
            self._geometry,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=list,
        )
        self._token = hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    @classmethod
    def from_current_selection(cls):
        # ``geometry_reader`` already resolves PanelNestManagedType and avoids
        # importing the sheet base.  Keep this import lazy so PanelNest remains
        # optional and the pure editor package can be tested without FreeCAD.
        from geometry_reader import get_selected_geometry

        return cls(get_selected_geometry(), "PanelNest — seleção CAM Chapa")

    def get_geometry(self) -> dict:
        return {
            "contours": [list(contour) for contour in self._geometry["contours"]],
            "holes": [dict(hole) for hole in self._geometry["holes"]],
        }

    def describe_source(self) -> str:
        return self._description

    def revision_token(self) -> str:
        return self._token
