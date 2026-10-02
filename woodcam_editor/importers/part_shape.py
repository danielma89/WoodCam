"""Exact-copy importer from FreeCAD Part/OCC shapes into domain entities."""

from __future__ import annotations

import inspect
import math
from dataclasses import dataclass, field, replace
from typing import Any, Iterable, Mapping, Optional


@dataclass(frozen=True)
class ImportIssue:
    index: int
    geometry_type: str
    message: str
    severity: str = "warning"


@dataclass(frozen=True)
class ImportLayerDescriptor:
    """Source-layer metadata captured before document-specific IDs exist."""

    source_key: str
    name: str
    color: Optional[str] = None
    purpose: str = "design"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        source_key = str(self.source_key or "").strip()
        name = str(self.name or source_key or "Desenho").strip()
        if not source_key:
            raise ValueError("ImportLayerDescriptor exige uma chave de camada fonte.")
        object.__setattr__(self, "source_key", source_key)
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "color", str(self.color) if self.color else None)
        object.__setattr__(self, "purpose", str(self.purpose or "design"))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))

    def to_dict(self) -> dict:
        return {
            "source_key": self.source_key,
            "name": self.name,
            "color": self.color,
            "purpose": self.purpose,
            "metadata": dict(self.metadata),
        }


def _import_layer_descriptor(source_key: str, value: Any) -> ImportLayerDescriptor:
    if isinstance(value, ImportLayerDescriptor):
        return value if value.source_key == source_key else replace(value, source_key=source_key)
    if isinstance(value, Mapping):
        return ImportLayerDescriptor(
            source_key=source_key,
            name=str(value.get("name", source_key) or source_key),
            color=value.get("color"),
            purpose=str(value.get("purpose", "design") or "design"),
            metadata=value.get("metadata", {}) or {},
        )
    return ImportLayerDescriptor(source_key=source_key, name=str(value or source_key))


@dataclass(frozen=True)
class ImportResult:
    entities: tuple[Any, ...]
    issues: tuple[ImportIssue, ...] = ()
    source_metadata: Mapping[str, Any] = field(default_factory=dict)
    layers: Mapping[str, ImportLayerDescriptor] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "entities", tuple(self.entities or ()))
        object.__setattr__(self, "issues", tuple(self.issues or ()))
        object.__setattr__(self, "source_metadata", dict(self.source_metadata or {}))
        object.__setattr__(
            self,
            "layers",
            {
                str(source_key): _import_layer_descriptor(str(source_key), descriptor)
                for source_key, descriptor in dict(self.layers or {}).items()
            },
        )

    @property
    def source_layers(self) -> Mapping[str, ImportLayerDescriptor]:
        """Explicit alias for callers that prefer the longer, unambiguous name."""

        return self.layers


@dataclass(frozen=True)
class _ShapeSource:
    """A read-only effective Shape plus the originating tree label.

    ``Part.getShape`` is essential for an ``App::Link``: its visible
    placement belongs to the instance, while ``obj.Shape`` can refer to the
    unplaced linked object.  The adapter lets the normal Shape importer keep
    its single-source contract without ever altering the source document.
    """

    name: str
    label: str
    shape: Any

    @property
    def Name(self):  # FreeCAD-style spelling used by import_part_shape.
        return self.name

    @property
    def Label(self):
        return self.label

    @property
    def Shape(self):
        return self.shape


def _domain_api():
    from woodcam_editor.domain.primitives import Vec2, new_id
    from woodcam_editor.domain.spans import ArcSpan, CubicBezierSpan, LineSpan
    from woodcam_editor.domain.entities import (
        CircleEntity,
        EllipseEntity,
        PathEntity,
    )

    return {
        "Vec2": Vec2,
        "new_id": new_id,
        "LineSpan": LineSpan,
        "ArcSpan": ArcSpan,
        "CubicBezierSpan": CubicBezierSpan,
        "PathEntity": PathEntity,
        "CircleEntity": CircleEntity,
        "EllipseEntity": EllipseEntity,
    }


def _construct(cls: type, values: Mapping[str, Any]):
    """Construct domain dataclasses while tolerating harmless API additions."""

    parameters = inspect.signature(cls).parameters
    kwargs = {name: values[name] for name in parameters if name in values}
    return cls(**kwargs)


def _new_id(api: Mapping[str, Any], prefix: str) -> str:
    return str(api["new_id"](prefix))


def _vec2(api: Mapping[str, Any], value: Any):
    if hasattr(value, "x") and hasattr(value, "y"):
        return api["Vec2"](float(value.x), float(value.y))
    return api["Vec2"](float(value[0]), float(value[1]))


def _point3(value: Any) -> tuple[float, float, float]:
    if hasattr(value, "x") and hasattr(value, "y"):
        return (float(value.x), float(value.y), float(getattr(value, "z", 0.0)))
    return (
        float(value[0]),
        float(value[1]),
        float(value[2]) if len(value) > 2 else 0.0,
    )


@dataclass(frozen=True)
class _PlaneProjection:
    """Orthonormal local XY plane used to flatten one planar Part face.

    The Editor domain is deliberately 2D.  A furniture assembly, however,
    often contains panels standing on their edge.  Projecting the selected
    broad face into a local plane avoids accidentally importing the MDF
    thickness faces as the part profile.
    """

    origin: tuple[float, float, float]
    axis_x: tuple[float, float, float]
    axis_y: tuple[float, float, float]

    def project(self, value: Any) -> tuple[float, float, float]:
        point = _point3(value)
        delta = tuple(point[index] - self.origin[index] for index in range(3))
        return (
            sum(delta[index] * self.axis_x[index] for index in range(3)),
            sum(delta[index] * self.axis_y[index] for index in range(3)),
            0.0,
        )


def _dot(left: tuple[float, float, float], right: tuple[float, float, float]) -> float:
    return sum(left[index] * right[index] for index in range(3))


def _cross(left: tuple[float, float, float], right: tuple[float, float, float]):
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _normalized(value: tuple[float, float, float]) -> tuple[float, float, float]:
    length = math.sqrt(_dot(value, value))
    if length <= 1.0e-12:
        raise ValueError("O plano da face não possui normal válida.")
    return tuple(component / length for component in value)


def _face_normal(face: Any) -> Optional[tuple[float, float, float]]:
    try:
        return _normalized(_point3(face.normalAt(0.0, 0.0)))
    except Exception:
        return None


def _is_planar_face(face: Any) -> bool:
    """Reject curved side faces when selecting a panel's broad face."""

    checker = getattr(face, "isPlanar", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception:
            pass
    surface = getattr(face, "Surface", None)
    if surface is None:
        # Lightweight importer fakes used by integrations often expose only
        # normalAt/Area.  Retain the historical permissive fallback there.
        return True
    description = "%s %s" % (
        type(surface).__name__,
        getattr(surface, "TypeId", ""),
    )
    return "plane" in description.lower()


def _horizontal_face_height(face: Any, normal: tuple[float, float, float]) -> float:
    """Prefer the same physical side of horizontal solids for temporary unions."""

    if abs(abs(normal[2]) - 1.0) > 1.0e-8:
        return 0.0
    try:
        return float(face.CenterOfMass.z)
    except Exception:
        return 0.0


def _canonical_normal_key(normal: tuple[float, float, float]) -> tuple[float, float, float]:
    """Stable orientation tie-breaker for the two broad sides of a panel.

    OCC is free to list either side of a box first.  When neither side has
    more machining detail, choosing a canonical normal makes every solid in
    the *same source part* pick the same physical side.  That matters for a
    compound used as one dogbone-shaped part: a central plate and the circular
    pads must be coplanar before they can be fused in the temporary import.

    More wires/edges still win before this key, so a genuinely detailed face
    (holes or pockets) remains authoritative.
    """

    for component in normal:
        if abs(component) > 1.0e-8:
            sign = 1.0 if component > 0.0 else -1.0
            return tuple(round(component * sign, 12) for component in normal)
    return (0.0, 0.0, 0.0)


def _normal_side_key(normal: tuple[float, float, float]) -> int:
    """Choose a repeatable side when two broad faces are otherwise equal."""

    for component in normal:
        if abs(component) > 1.0e-8:
            return 1 if component > 0.0 else 0
    return 0


def _plane_projection(face: Any, normal: tuple[float, float, float]) -> _PlaneProjection:
    vertices = list(getattr(face, "Vertexes", []) or [])
    if vertices:
        origin = _point3(vertices[0].Point)
    else:
        origin = _point3(getattr(face, "CenterOfMass"))
    if abs(normal[2]) >= 1.0 - 1.0e-8:
        # Preserve the model X direction for horizontal boards. The underside
        # then mirrors Y instead of unexpectedly swapping length and width.
        return _PlaneProjection(
            origin,
            (1.0, 0.0, 0.0),
            (0.0, 1.0 if normal[2] > 0.0 else -1.0, 0.0),
        )
    reference = min(
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)),
        key=lambda axis: abs(_dot(axis, normal)),
    )
    axis_x = _normalized(_cross(reference, normal))
    axis_y = _normalized(_cross(normal, axis_x))
    return _PlaneProjection(origin, axis_x, axis_y)


def _face_plane_level(face: Any, normal: tuple[float, float, float]) -> float:
    """Signed position of a planar face along the manufacturing normal."""

    try:
        point = _point3(face.CenterOfMass)
    except Exception:
        vertices = list(getattr(face, "Vertexes", []) or [])
        if not vertices:
            raise ValueError("A face não possui posição mensurável.")
        point = _point3(vertices[0].Point)
    return _dot(point, normal)


def _same_shape(first: Any, second: Any) -> bool:
    try:
        return bool(first.isSame(second))
    except Exception:
        return first is second


def _pocket_floor_faces(
    solid: Any,
    reference_face: Any,
    reference_normal: tuple[float, float, float],
    tolerance: float = 1.0e-7,
) -> tuple[tuple[Any, float, float, float], ...]:
    """Find planar floors below a panel surface without altering the solid.

    A through-cut has no parallel bottom face and therefore produces no
    pocket.  A blind recess, including one open to the stock edge, exposes a
    face with the same outward normal below the selected manufacturing plane.
    """

    try:
        reference_level = _face_plane_level(reference_face, reference_normal)
    except Exception:
        return ()
    result = []
    for face in list(getattr(solid, "Faces", []) or []):
        if _same_shape(face, reference_face) or not _is_planar_face(face):
            continue
        normal = _face_normal(face)
        if normal is None or _dot(reference_normal, normal) < 1.0 - 1.0e-6:
            continue
        try:
            bottom_level = _face_plane_level(face, reference_normal)
            depth = reference_level - bottom_level
            area = float(getattr(face, "Area", 0.0) or 0.0)
        except Exception:
            continue
        if depth <= tolerance or area <= tolerance:
            continue
        result.append((face, depth, reference_level, bottom_level))
    return tuple(
        sorted(
            result,
            key=lambda value: (
                round(float(value[1]), 9),
                -round(float(getattr(value[0], "Area", 0.0) or 0.0), 9),
            ),
        )
    )


def _manufacturing_surface_face(
    solid: Any,
    reference_face: Any,
    reference_normal: tuple[float, float, float],
    tolerance: float = 1.0e-7,
) -> Any:
    """Build a temporary stock silhouette on the selected manufacturing plane.

    The most detailed broad face is correct for finding machining features,
    but its outer wire follows any recess that opens onto an edge. Used
    directly as ``cut_external``, that makes a shallow groove look like stock
    removed through the full thickness. The largest parallel planar face is
    the intact stock side (or either equivalent side for a through-cut), so its
    exact outer wire is the safe cutting silhouette. Pocket floors are still
    imported separately as machining regions. All candidates are OCC copies;
    the source solid and FCStd document remain untouched.
    """

    try:
        reference_level = _face_plane_level(reference_face, reference_normal)
    except Exception:
        return reference_face
    projected = []
    for face in list(getattr(solid, "Faces", []) or []):
        if not _is_planar_face(face):
            continue
        normal = _face_normal(face)
        if normal is None or abs(_dot(reference_normal, normal)) < 1.0 - 1.0e-6:
            continue
        try:
            if float(getattr(face, "Area", 0.0) or 0.0) <= tolerance:
                continue
            level = _face_plane_level(face, reference_normal)
            copied = face.copy()
            distance = reference_level - level
            if abs(distance) > tolerance:
                import FreeCAD  # type: ignore

                copied.translate(
                    FreeCAD.Vector(
                        reference_normal[0] * distance,
                        reference_normal[1] * distance,
                        reference_normal[2] * distance,
                    )
                )
            # On equal-area front/back faces prefer the side whose OCC
            # orientation agrees with the selected manufacturing normal.
            # Mixing opposite-oriented copies in a later dogbone fusion can
            # leave a shell split into several coincident faces.
            projected.append((copied, _dot(reference_normal, normal)))
        except Exception:
            continue
    if not projected:
        return reference_face
    return max(
        projected,
        key=lambda value: (
            float(getattr(value[0], "Area", 0.0) or 0.0),
            float(value[1]),
        ),
    )[0]


def _select_panel_face(
    solid: Any,
    preferred_normal: tuple[float, float, float] | None = None,
):
    """Choose the useful broad face of a panel, never one thickness side.

    First keep only faces close to the largest planar face.  This discards
    the four narrow MDF sides even on long narrow strips.  Between the two
    broad faces, prefer the one with more wires/edges and then its actual
    face area.  This deliberately follows the proven Assembly→DXF macro:
    details win first; if both sides carry the same details, the broadest face
    is the stable tiebreaker.
    """

    candidates = []
    for index, face in enumerate(list(getattr(solid, "Faces", []) or [])):
        if not _is_planar_face(face):
            continue
        normal = _face_normal(face)
        if normal is None:
            continue
        area = max(0.0, float(getattr(face, "Area", 0.0)))
        if area <= 1.0e-9:
            continue
        candidates.append((index, face, normal, area))
    if not candidates:
        return None
    # A chapa tem duas faces grandes e paralelas; as outras quatro são as
    # faces da espessura.  Escolher apenas por "mais wires" podia escolher
    # uma lateral longa quando ela recebia muitos encaixes.  A macro de
    # fabricação resolve isto começando pela maior face e só comparando faces
    # paralelas a ela.  Mantemos a mesma regra aqui, antes de pontuar recortes.
    largest = max(candidates, key=lambda candidate: candidate[3])
    largest_area = largest[3]
    largest_normal = largest[2]
    broad = [
        candidate
        for candidate in candidates
        if candidate[3] >= largest_area * 0.45
        and abs(_dot(largest_normal, candidate[2])) >= 1.0 - 1.0e-4
    ]
    return max(
        broad,
        key=lambda candidate: (
            len(list(getattr(candidate[1], "Wires", []) or [])),
            len(list(getattr(candidate[1], "Edges", []) or [])),
            _dot(candidate[2], preferred_normal) if preferred_normal else 0.0,
            # For coplanar top/bottom alternatives, retain the upper face.
            # It is where OCC keeps the fused manufacturing outline for
            # compound panel features such as dogbones.  Area remains the
            # macro-compatible deterministic tiebreaker after that.
            _horizontal_face_height(candidate[1], candidate[2]),
            _normal_side_key(candidate[2]),
            _canonical_normal_key(candidate[2]),
            candidate[3],
        ),
    )


def _faces_are_coplanar(
    first: Any,
    second: Any,
    tolerance: float = 1.0e-7,
) -> bool:
    """Return whether two planar faces occupy the same mathematical plane."""

    first_normal = _face_normal(first)
    second_normal = _face_normal(second)
    if first_normal is None or second_normal is None:
        return False
    if abs(_dot(first_normal, second_normal)) < 1.0 - 1.0e-6:
        return False
    try:
        first_point = _point3(first.CenterOfMass)
        second_point = _point3(second.CenterOfMass)
    except Exception:
        return False
    delta = tuple(second_point[index] - first_point[index] for index in range(3))
    return abs(_dot(delta, first_normal)) <= tolerance


def _faces_have_positive_overlap(first: Any, second: Any, tolerance: float = 1.0e-7) -> bool:
    """Return whether coplanar faces have a *partial* material overlap.

    The earlier XY bounding-box guard was correct only for panels that already
    lay flat.  A standing panel has virtually zero XY footprint, so it made a
    plate-plus-circular-pads compound impossible to recognize.  OCC ``common``
    is evaluated in the native face plane and works for every orientation.
    """

    try:
        if not _faces_are_coplanar(first, second, tolerance):
            return False
        common = first.common(second)
        if bool(getattr(common, "isNull", lambda: True)()):
            return False
        common_area = float(getattr(common, "Area", 0.0))
        first_area = max(0.0, float(getattr(first, "Area", 0.0)))
        second_area = max(0.0, float(getattr(second, "Area", 0.0)))
        if common_area <= tolerance:
            return False
        # Do not silently merge an entire smaller face into a larger one or
        # collapse duplicate/coincident solids.  In an Assembly those are
        # commonly independent boards/instances.  A dogbone extension has a
        # real partial intersection: each round pad overlaps the plate but
        # also extends beyond it.
        return common_area < min(first_area, second_area) - tolerance
    except Exception:
        return False


def _fuse_overlapping_xy_faces(
    source_shapes: Iterable[
        tuple[Any, Optional[_PlaneProjection], tuple[tuple[Any, float, float, float], ...]]
    ]
):
    """Fuse overlapping coplanar source faces into one import outline.

    A dogbone-style cutout is often authored as a square plus four circular
    pads.  Its source object is a compound of solids, so importing every
    broad face independently turns one intended contour into a square and
    four circles.  The fusion happens on a temporary OCC copy; the source
    object, its Shape and the FreeCAD document are never modified.

    Faces that merely touch are intentionally not fused.  Furniture panels
    may share an edge in an assembly but remain distinct CNC pieces.
    """

    # The historical name remains for compatibility with callers/tests.  The
    # operation is intentionally orientation-independent: every candidate
    # comes from one source Shape, so fusing is allowed only for faces that
    # share the same physical plane *and* positive material area.
    candidates = tuple(source_shapes)
    groups = []
    for face, projection, pocket_faces in candidates:
        matching = [
            index
            for index, group in enumerate(groups)
            if any(_faces_have_positive_overlap(face, member[0]) for member in group)
        ]
        if not matching:
            groups.append([(face, projection, pocket_faces)])
            continue
        combined = [(face, projection, pocket_faces)]
        for index in reversed(matching):
            combined.extend(groups.pop(index))
        groups.append(combined)

    fused = []
    for group in groups:
        if len(group) == 1:
            fused.append(group[0])
            continue
        try:
            # Fusing the cluster in one OCC operation is important: repeated
            # pair fuses can preserve splitter faces and reintroduce the
            # square-plus-circles representation during wire import.
            combined_shape = group[0][0].multiFuse(
                [member[0] for member in group[1:]]
            ).removeSplitter()
            if bool(getattr(combined_shape, "isNull", lambda: True)()):
                raise ValueError("união OCC retornou forma nula")
            fused_faces = list(getattr(combined_shape, "Faces", []) or [])
            if len(fused_faces) == 1:
                # A generic one-face Shell has no ``OuterWire`` attribute.
                # Unwrap it so the exact fused dogbone is classified as the
                # component's external contour rather than an internal cut.
                combined_shape = fused_faces[0]
            # Each temporary fusion remains in the plane established by the
            # first face.  Reusing that projection avoids a per-solid local
            # origin/axis from separating members that are now one profile.
            fused.append(
                (
                    combined_shape,
                    group[0][1],
                    tuple(
                        pocket
                        for member in group
                        for pocket in tuple(member[2] or ())
                    ),
                )
            )
        except Exception:
            # A failed boolean must not make an import lose geometry.
            fused.extend(group)
    return tuple(fused)



def _transform_point(
    value: Any,
    placement: Any = None,
    projection: Optional[_PlaneProjection] = None,
) -> tuple[float, float, float]:
    if placement is None:
        result = _point3(value)
    else:
        try:
            import FreeCAD  # type: ignore

            source = value if hasattr(value, "x") else FreeCAD.Vector(*_point3(value))
            result = _point3(placement.multVec(source))
        except Exception:
            result = _point3(value)
    return projection.project(result) if projection is not None else result


def _span_values(api: Mapping[str, Any], start: Any, end: Any) -> dict:
    return {
        "id": _new_id(api, "span"),
        "span_id": _new_id(api, "span"),
        "start_node_id": _new_id(api, "node"),
        "end_node_id": _new_id(api, "node"),
        "start": start,
        "end": end,
        "metadata": {},
    }


def _line_span(api: Mapping[str, Any], start: Any, end: Any):
    return _construct(api["LineSpan"], _span_values(api, start, end))


def _arc_span(api: Mapping[str, Any], start: Any, end: Any, center: Any, clockwise: bool):
    values = _span_values(api, start, end)
    values.update(center=center, clockwise=bool(clockwise))
    return _construct(api["ArcSpan"], values)


def _bezier_span(
    api: Mapping[str, Any],
    start: Any,
    control_1: Any,
    control_2: Any,
    end: Any,
):
    values = _span_values(api, start, end)
    values.update(
        control1=control_1,
        control2=control_2,
        control_1=control_1,
        control_2=control_2,
    )
    return _construct(api["CubicBezierSpan"], values)


def _link_node_ids(spans: Iterable[Any], closed: bool) -> tuple[Any, ...]:
    """Share logical endpoint IDs when the domain exposes node ID fields."""

    linked = list(spans)
    for index in range(1, len(linked)):
        previous_id = getattr(linked[index - 1], "end_node_id", None)
        if previous_id is not None and hasattr(linked[index], "start_node_id"):
            try:
                linked[index] = replace(linked[index], start_node_id=previous_id)
            except (TypeError, ValueError):
                pass
    if closed and len(linked) > 1:
        first_id = getattr(linked[0], "start_node_id", None)
        if first_id is not None and hasattr(linked[-1], "end_node_id"):
            try:
                linked[-1] = replace(linked[-1], end_node_id=first_id)
            except (TypeError, ValueError):
                pass
    return tuple(linked)


def _orient_connected_spans(
    spans: Iterable[Any],
    closed: bool,
    tolerance: float = 1.0e-9,
) -> tuple[Any, ...]:
    """Orient an OCC wire copy without changing any geometric point.

    ``Wire.OrderedEdges`` guarantees traversal order, but individual edge
    orientations are not guaranteed to agree with that traversal.  Try both
    directions of every possible first span and accept only a chain whose
    existing endpoints already coincide.  No gap is healed here.
    """

    source = tuple(spans)
    if len(source) < 2:
        return source
    if tolerance <= 0.0:
        raise ValueError("tolerance must be positive")

    def connected(left: Any, right: Any) -> bool:
        return bool(left.end.almost_equals(right.start, tolerance))

    for first_index in range(len(source)):
        for reverse_first in (False, True):
            first = source[first_index].reversed() if reverse_first else source[first_index]
            chain = [first]
            remaining = [
                (index, span)
                for index, span in enumerate(source)
                if index != first_index
            ]
            while remaining:
                match = None
                for position, (source_index, span) in enumerate(remaining):
                    if connected(chain[-1], span):
                        match = (position, source_index, span)
                        break
                    reversed_span = span.reversed()
                    if connected(chain[-1], reversed_span):
                        match = (position, source_index, reversed_span)
                        break
                if match is None:
                    break
                position, _source_index, oriented = match
                remaining.pop(position)
                chain.append(oriented)
            if remaining:
                continue
            if closed and not chain[-1].end.almost_equals(chain[0].start, tolerance):
                continue
            if not closed and chain[-1].end.almost_equals(chain[0].start, tolerance):
                continue
            return tuple(chain)
    raise ValueError(
        "As arestas do wire não formam uma cadeia contínua sem corrigir geometria."
    )


def _path_entity(
    api: Mapping[str, Any],
    layer_id: str,
    spans: Iterable[Any],
    closed: bool,
    metadata: Optional[Mapping[str, Any]] = None,
):
    spans = _orient_connected_spans(spans, closed)
    spans = _link_node_ids(spans, closed)
    values = {
        "id": _new_id(api, "path"),
        "entity_id": _new_id(api, "path"),
        "layer_id": layer_id,
        "spans": spans,
        "closed": bool(closed),
        "metadata": dict(metadata or {}),
    }
    return _construct(api["PathEntity"], values)


def _circle_entity(
    api: Mapping[str, Any],
    layer_id: str,
    center: Any,
    radius: float,
    metadata: Optional[Mapping[str, Any]] = None,
):
    values = {
        "id": _new_id(api, "circle"),
        "entity_id": _new_id(api, "circle"),
        "layer_id": layer_id,
        "center": center,
        "radius": float(radius),
        "metadata": dict(metadata or {}),
    }
    return _construct(api["CircleEntity"], values)


def _ellipse_entity(
    api: Mapping[str, Any],
    layer_id: str,
    center: Any,
    radius_x: float,
    radius_y: float,
    rotation: float,
    metadata: Optional[Mapping[str, Any]] = None,
):
    values = {
        "id": _new_id(api, "ellipse"),
        "entity_id": _new_id(api, "ellipse"),
        "layer_id": layer_id,
        "center": center,
        "radius_x": float(radius_x),
        "radius_y": float(radius_y),
        "rotation": float(rotation),
        "metadata": dict(metadata or {}),
    }
    return _construct(api["EllipseEntity"], values)


def _edge_endpoints(
    edge: Any,
    placement: Any = None,
    projection: Optional[_PlaneProjection] = None,
):
    vertices = list(getattr(edge, "Vertexes", []) or [])
    if len(vertices) < 2:
        return None
    return (
        _transform_point(vertices[0].Point, placement, projection),
        _transform_point(vertices[-1].Point, placement, projection),
    )


def _edge_midpoint(
    edge: Any,
    placement: Any = None,
    projection: Optional[_PlaneProjection] = None,
):
    first = float(getattr(edge, "FirstParameter", 0.0))
    last = float(getattr(edge, "LastParameter", 1.0))
    parameter = (first + last) * 0.5
    try:
        value = edge.valueAt(parameter)
    except Exception:
        value = edge.Curve.value(parameter)
    return _transform_point(value, placement, projection)


def _clockwise(start: Any, forward_sample: Any, center: Any) -> bool:
    """Determine parametric direction, including arcs larger than 180 degrees."""

    start_x, start_y, _ = start
    sample_x, sample_y, _ = forward_sample
    center_x, center_y, _ = center
    return (
        (start_x - center_x) * (sample_y - center_y)
        - (start_y - center_y) * (sample_x - center_x)
    ) < 0.0


def _curve_name(curve: Any) -> str:
    return "{} {}".format(
        type(curve).__name__,
        getattr(curve, "TypeId", ""),
    ).lower()


def _edge_to_span(
    edge: Any,
    api: Mapping[str, Any],
    placement: Any,
    projection: Optional[_PlaneProjection] = None,
):
    endpoints = _edge_endpoints(edge, placement, projection)
    if endpoints is None:
        return None, "A aresta não possui duas extremidades."
    start_3d, end_3d = endpoints
    start = _vec2(api, start_3d)
    end = _vec2(api, end_3d)
    curve = getattr(edge, "Curve", None)
    curve_name = _curve_name(curve)

    if "line" in curve_name:
        # OCC can expose a degenerate seam/vertex as a linear edge.  It may
        # already be zero length in 3D or collapse only after projection to
        # the panel plane.  Such an edge carries no machinable geometry and
        # must become an import issue, not abort the complete board snapshot.
        if start.almost_equals(end, 1.0e-9):
            return None, "Aresta linear degenerada (comprimento zero) ignorada."
        return _line_span(api, start, end), None

    if "circle" in curve_name:
        center_3d = _transform_point(curve.Center, placement, projection)
        first_parameter = float(getattr(edge, "FirstParameter", 0.0))
        last_parameter = float(getattr(edge, "LastParameter", 1.0))
        forward_parameter = first_parameter + (last_parameter - first_parameter) * 1.0e-6
        try:
            forward_value = edge.valueAt(forward_parameter)
        except Exception:
            forward_value = curve.value(forward_parameter)
        forward_3d = _transform_point(forward_value, placement, projection)
        return (
            _arc_span(
                api,
                start,
                end,
                _vec2(api, center_3d),
                _clockwise(start_3d, forward_3d, center_3d),
            ),
            None,
        )

    if "bezier" in curve_name and "bspline" not in curve_name:
        poles = list(curve.getPoles())
        if len(poles) != 4:
            return None, f"Bézier de {len(poles) - 1} grau ainda não suportada."
        transformed = [
            _vec2(api, _transform_point(point, placement, projection))
            for point in poles
        ]
        if transformed[0].distance_to(start) > transformed[-1].distance_to(start):
            transformed.reverse()
        return _bezier_span(api, start, transformed[1], transformed[2], end), None

    return None, f"Curva OCC não suportada: {type(curve).__name__}."


def _full_curve_entity(
    edge: Any,
    api: Mapping[str, Any],
    layer_id: str,
    placement: Any,
    projection: Optional[_PlaneProjection] = None,
    metadata: Optional[Mapping[str, Any]] = None,
):
    curve = getattr(edge, "Curve", None)
    name = _curve_name(curve)
    if "circle" in name:
        center = _vec2(api, _transform_point(curve.Center, placement, projection))
        return _circle_entity(
            api, layer_id, center, float(curve.Radius), metadata=metadata
        ), None
    if "ellipse" in name:
        center = _vec2(api, _transform_point(curve.Center, placement, projection))
        x_axis = getattr(curve, "XAxis", None)
        rotation = math.atan2(float(x_axis.y), float(x_axis.x)) if x_axis is not None else 0.0
        return (
            _ellipse_entity(
                api,
                layer_id,
                center,
                float(curve.MajorRadius),
                float(curve.MinorRadius),
                rotation,
                metadata=metadata,
            ),
            None,
        )
    return None, f"Curva fechada não suportada: {type(curve).__name__}."


def _shape_has_solids(shape: Any) -> bool:
    try:
        return shape is not None and not shape.isNull() and bool(list(shape.Solids or ()))
    except Exception:
        return False


def _effective_tree_shape(source: Any) -> Any:
    """Read an object's displayed shape, resolving App::Link when possible."""

    try:
        import Part  # type: ignore

        try:
            return Part.getShape(
                source,
                "",
                needSubElement=False,
                refine=False,
            )
        except TypeError:
            return Part.getShape(source)
    except Exception:
        return getattr(source, "Shape", None)


def _tree_children(source: Any) -> tuple[Any, ...]:
    """Return structural children without following a Link target twice."""

    if source is None:
        return ()
    type_id = str(getattr(source, "TypeId", "") or "")
    if type_id.startswith("App::Link") or type_id == "PartDesign::Body":
        return ()
    candidates = []
    for property_name in ("Group", "ElementList", "Elements"):
        try:
            candidates.extend(
                item for item in (getattr(source, property_name, None) or ())
                if hasattr(item, "Name")
            )
        except Exception:
            pass
    if not candidates:
        try:
            candidates.extend(
                item for item in (getattr(source, "OutList", None) or ())
                if hasattr(item, "Name")
            )
        except Exception:
            pass
    unique = []
    seen = set()
    for item in candidates:
        key = (
            str(getattr(getattr(item, "Document", None), "Name", "")),
            str(getattr(item, "Name", id(item))),
        )
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return tuple(unique)


def _collect_tree_shape_sources(sources: Iterable[Any]) -> tuple[_ShapeSource, ...]:
    """Mirror the Assembly→DXF macro's component traversal.

    The direct Editor import used to read ``Shape`` from the selected
    container.  A furniture generator often exposes a consolidated shape
    there, which loses the 98 logical boards that still exist below it in the
    tree.  Walk containers first, treat Links and Bodies as physical leaves,
    and fall back to the container only if it did not yield a leaf.
    """

    result = []
    visited = set()

    def visit(source: Any, depth: int = 0) -> None:
        if source is None or depth > 50:
            return
        key = (
            str(getattr(getattr(source, "Document", None), "Name", "")),
            str(getattr(source, "Name", id(source))),
        )
        if key in visited:
            return
        visited.add(key)

        children = _tree_children(source)
        before = len(result)
        for child in children:
            visit(child, depth + 1)
        if children and len(result) > before:
            return

        shape = _effective_tree_shape(source)
        if not _shape_has_solids(shape):
            return
        result.append(
            _ShapeSource(
                name=str(getattr(source, "Name", "") or "part_shape"),
                label=str(
                    getattr(source, "Label", "")
                    or getattr(source, "Name", "")
                    or "Peça importada"
                ),
                shape=shape,
            )
        )

    for source in sources or ():
        visit(source)
    return tuple(result)


def _stage_tree_imports(results: Iterable[ImportResult]) -> tuple[Any, ...]:
    """Park independent physical boards in a compact, non-nesting grid.

    One Assembly tree leaf is not necessarily one board.  Parametric furniture
    generators commonly expose a single ``PartDesign::Feature`` whose Shape is
    a compound containing several solids.  Those solids may even have the same
    XY footprint at different Z heights.  ``import_part_shape`` stamps every
    post-fusion face component and ``_mark_tree_result_instance`` turns that
    component into a physical-instance scope.  Stage each scope independently
    so containment never mistakes an upper board for a hole in a lower one.

    GroupEntity remains a logical selection wrapper around already transformed
    children; applying the translation to the group as well would move the
    geometry twice.  This routine therefore transforms leaves only, just like
    the previous one-result-per-board implementation.
    """

    from woodcam_editor.domain.entities import GroupEntity
    from woodcam_editor.domain.primitives import Affine2D, Vec2

    blocks = []
    total_area = 0.0
    widest = 25.0
    for result_index, result in enumerate(results):
        # Dict insertion order preserves the OCC/component order and keeps the
        # staging deterministic across save/reopen and repeated imports.
        scoped_entities = {}
        for entity in tuple(result.entities):
            metadata = dict(getattr(entity, "metadata", {}) or {})
            instance_id = str(metadata.get("source_tree_instance_id", "") or "")
            scope = instance_id or "result:%06d" % int(result_index)
            scoped_entities.setdefault(scope, []).append(entity)

        for entities_value in scoped_entities.values():
            entities = tuple(entities_value)
            leaves = tuple(
                entity for entity in entities if not isinstance(entity, GroupEntity)
            )
            if not leaves:
                continue
            min_x = min(entity.bounds().min_x for entity in leaves)
            min_y = min(entity.bounds().min_y for entity in leaves)
            max_x = max(entity.bounds().max_x for entity in leaves)
            max_y = max(entity.bounds().max_y for entity in leaves)
            width = max(0.0, max_x - min_x)
            height = max(0.0, max_y - min_y)
            blocks.append((entities, min_x, min_y, width, height))
            total_area += (width + 25.0) * (height + 25.0)
            widest = max(widest, width + 25.0)
    if not blocks:
        return ()

    target_row_width = max(widest, math.sqrt(total_area) * 1.25)
    placed = []
    x_value = 0.0
    y_value = 0.0
    row_height = 0.0
    for entities, min_x, min_y, width, height in blocks:
        if x_value > 0.0 and x_value + width > target_row_width:
            x_value = 0.0
            y_value += row_height + 25.0
            row_height = 0.0
        transform = Affine2D.translation(Vec2(x_value - min_x, y_value - min_y))
        placed.extend(
            entity if isinstance(entity, GroupEntity) else entity.transformed(transform)
            for entity in entities
        )
        x_value += width + 25.0
        row_height = max(row_height, height)
    return tuple(placed)


def _mark_tree_result_instance(
    result: ImportResult,
    source: _ShapeSource,
    index: int,
) -> ImportResult:
    """Attach a stable physical-board scope to one imported tree leaf."""

    base_instance_id = "%03d:%s" % (int(index), source.name)
    entities = []
    for entity in result.entities:
        metadata = dict(getattr(entity, "metadata", {}) or {})
        component_id = str(metadata.get("source_shape_component_id", "") or "")
        instance_id = (
            base_instance_id + ":" + component_id
            if component_id
            else base_instance_id
        )
        metadata.update(
            {
                "source_tree_instance_id": instance_id,
                "source_tree_label": source.label,
            }
        )
        entities.append(replace(entity, metadata=metadata))
    return ImportResult(entities, result.issues, result.source_metadata, result.layers)


def paired_panel_face_normals(sources: Iterable[Any]) -> dict[str, tuple[float, float, float]]:
    """Face normals pointing away from an aligned, opposite assembly panel.

    Only an unambiguous pair of congruent, parallel boards is eligible.  A
    lone panel or a board with face-specific pockets keeps the importer's
    existing detailed-face choice.  This prevents two opposing through-drilled
    boards from both being flattened from the same physical side.
    """

    records = []
    for source in sources or ():
        shape = getattr(source, "shape", None) or getattr(source, "Shape", None)
        solids = list(getattr(shape, "Solids", ()) or ())
        if len(solids) != 1:
            continue
        selected = _select_panel_face(solids[0])
        if selected is None:
            continue
        normal = selected[2]
        axis = max(range(3), key=lambda index: abs(normal[index]))
        if abs(normal[axis]) < 1.0 - 1.0e-5:
            continue
        bounds = getattr(shape, "BoundBox", None)
        if bounds is None:
            continue
        minima = (float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin))
        maxima = (float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax))
        center = tuple((lo + hi) / 2.0 for lo, hi in zip(minima, maxima))
        dimensions = tuple(hi - lo for lo, hi in zip(minima, maxima))
        name = str(getattr(source, "name", "") or getattr(source, "Name", ""))
        if name and dimensions[axis] > 1.0e-6:
            records.append((name, axis, normal, center, dimensions))

    matches = {}
    for index, first in enumerate(records):
        _name, axis, _normal, center, dimensions = first
        candidates = []
        for other_index, second in enumerate(records):
            if index == other_index or axis != second[1]:
                continue
            if any(abs(a - b) > 0.05 for a, b in zip(dimensions, second[4])):
                continue
            if any(
                abs(center[coordinate] - second[3][coordinate]) > 0.05
                for coordinate in range(3) if coordinate != axis
            ):
                continue
            separation = abs(center[axis] - second[3][axis])
            if separation <= dimensions[axis] + 0.05:
                continue
            candidates.append((separation, other_index))
        if candidates:
            candidates.sort()
            if len(candidates) == 1 or candidates[1][0] - candidates[0][0] > 0.05:
                matches[index] = candidates[0][1]

    result = {}
    for index, partner_index in matches.items():
        if matches.get(partner_index) != index:
            continue
        name, axis, normal, center, _dimensions = records[index]
        direction = 1.0 if center[axis] > records[partner_index][3][axis] else -1.0
        result[name] = tuple(
            direction if coordinate == axis else 0.0 for coordinate in range(3)
        )
    return result


def import_freecad_tree(
    sources: Iterable[Any],
    *,
    layer_id: str,
    flatten_solids: bool = False,
    compound_groups: bool = False,
) -> ImportResult:
    """Import actual Assembly tree components instead of a merged container.

    This is the direct, no-PanelNest equivalent of the Assembly→DXF macro's
    extraction stage.  It is a read-only snapshot and deliberately does not
    run nesting or change any Shape/FCStd object.
    """

    tree_sources = _collect_tree_shape_sources(sources)
    face_preferences = paired_panel_face_normals(tree_sources)
    imported = tuple(
        _mark_tree_result_instance(
            import_part_shape(
            source,
            layer_id=layer_id,
            flatten_solids=flatten_solids,
            compound_groups=compound_groups,
            preferred_face_normal=face_preferences.get(source.name),
            ),
            source,
            index,
        )
        for index, source in enumerate(tree_sources, start=1)
    )
    issues = tuple(issue for result in imported for issue in result.issues)
    layers = {}
    for result in imported:
        layers.update(result.layers)
    return ImportResult(
        _stage_tree_imports(imported),
        issues,
        {
            "source_kind": "freecad_tree",
            "source_count": len(tree_sources),
        },
        layers,
    )


def import_part_shape(
    source: Any,
    *,
    layer_id: str,
    placement: Any = None,
    flatten_solids: bool = False,
    compound_groups: bool = False,
    preferred_face_normal: tuple[float, float, float] | None = None,
    _projection: Optional[_PlaneProjection] = None,
    _stage_projection: bool = True,
) -> ImportResult:
    """Copy an OCC Shape without modifying/hiding the source object.

    ``placement`` is explicit to avoid accidentally applying an object's
    placement twice.  FreeCAD ``Shape`` coordinates are normally already in
    the object's shape placement.

    ``compound_groups`` preserves the object identity of each source face.
    A face can legitimately contain one outer profile plus several exact
    circular holes/inner wires.  The Editor still stores those wires as
    independent vectors for CAM, but exposes them as one selectable compound
    (the same semantic role as ``Part.makeCompound(face.Wires)`` in the
    Assembly→DXF macro).  It is opt-in because low-level boolean callers
    already create their own compound group.

    ``_projection`` and ``_stage_projection`` are internal continuations of
    the manufacturing plane.
    A blind-pocket floor belongs to the same upright board as its broad face;
    importing that floor in global XY collapses it into a line and moves
    circular recesses away from the panel.  Staging the recursively projected
    floor on its own also destroys its position relative to the stock.  Pocket
    extraction therefore passes the immutable projection through and defers
    staging until the complete board is assembled.
    """

    api = _domain_api()
    shape = getattr(source, "Shape", source)
    if shape is None or bool(getattr(shape, "isNull", lambda: True)()):
        raise ValueError("O objeto selecionado não possui Shape importável.")

    # For a solid, copy only its useful broad planar face.  A PanelNest layout
    # is already flat on XY and must be preferred whenever it exists.  Generic
    # imports keep that safe default: upright solids are skipped with a clear
    # issue instead of guessing a cabinet face.  The explicit experimental
    # ``flatten_solids`` path remains available to a caller that truly has no
    # PanelNest layout to use.
    source_shapes = [(shape, _projection, ())]
    source_issues = []
    solids = list(getattr(shape, "Solids", []) or [])
    if solids:
        solid_faces = []
        for solid_index, solid in enumerate(solids):
            selected = _select_panel_face(solid, preferred_face_normal)
            if selected is None:
                continue
            _index, face, normal, _area = selected
            projection = None
            needs_projection = (
                abs(abs(normal[2]) - 1.0) > 1.0e-8
                or (preferred_face_normal is not None and normal[2] < -1.0 + 1.0e-8)
            )
            if needs_projection:
                if not flatten_solids:
                    source_issues.append(
                        ImportIssue(
                            solid_index,
                            "Solid",
                            "Peça em pé ignorada: use Arquivo → Importar peças planas "
                            "pelo PanelNest para ler o perfil sem executar nesting.",
                        )
                    )
                    continue
                projection = _plane_projection(face, normal)
            solid_faces.append(
                (
                    _manufacturing_surface_face(solid, face, normal),
                    projection,
                    _pocket_floor_faces(solid, face, normal),
                )
            )
        if solid_faces:
            source_shapes = _fuse_overlapping_xy_faces(solid_faces)
        else:
            source_shapes = []

    entities = []
    issues = list(source_issues)
    flattened_groups = []
    # The child IDs survive staging transforms below.  Build the logical
    # compound only after those transforms, keeping it in VectorDocument
    # instead of storing geometric state in the scene.
    compound_child_groups = []
    for component_index, (
        source_shape,
        projection,
        pocket_faces,
    ) in enumerate(source_shapes, start=1):
        # This identity is assigned *after* the conservative coplanar overlap
        # fusion.  A square plus partially overlapping circular pads (dogbone)
        # is therefore one component, while coincident boards in different Z
        # planes remain separate physical pieces.
        component_id = "component-%03d" % int(component_index)
        outer_wire = getattr(source_shape, "OuterWire", None)

        def wire_is_outer(candidate):
            if candidate is None or outer_wire is None:
                return False
            try:
                return bool(candidate.isSame(outer_wire))
            except Exception:
                return candidate is outer_wire

        records = []
        local_covered_edges = []
        for wire in list(getattr(source_shape, "Wires", []) or []):
            wire_edges = list(
                getattr(wire, "OrderedEdges", [])
                or getattr(wire, "Edges", [])
                or []
            )
            if wire_edges:
                records.append((wire, wire_edges))
                local_covered_edges.extend(wire_edges)
        for edge in list(getattr(source_shape, "Edges", []) or []):
            is_covered = False
            for covered in local_covered_edges:
                try:
                    is_covered = bool(edge.isSame(covered))
                except Exception:
                    is_covered = edge == covered
                if is_covered:
                    break
            if not is_covered:
                records.append((None, [edge]))

        source_entities = []
        for wire_index, (wire, wire_edges) in enumerate(records):
            # A broad OCC face already knows the manufacturing role of each
            # wire.  Preserve that fact as metadata; the UI later remaps it
            # to document-local layers without changing the source Shape.
            role = "design"
            if wire is not None:
                if wire_is_outer(wire):
                    role = "cut_external"
                elif len(wire_edges) == 1 and bool(
                    getattr(wire_edges[0], "isClosed", lambda: False)()
                ):
                    curve = getattr(wire_edges[0], "Curve", None)
                    radius = getattr(curve, "Radius", None)
                    # A small circular edge is a drilled hole; a larger one
                    # is an internal profile and must not be hidden in Furos.
                    try:
                        role = "drill" if float(radius) * 2.0 <= 12.0 + 1e-9 else "cut_internal"
                    except (TypeError, ValueError):
                        role = "drill"
                else:
                    role = "cut_internal"
            elif len(wire_edges) == 1 and bool(
                getattr(wire_edges[0], "isClosed", lambda: False)()
            ):
                # A standalone circular edge in a compound is still a drill
                # even though OCC did not expose it through a Wire object.
                curve_name = _curve_name(getattr(wire_edges[0], "Curve", None))
                if "circle" in curve_name:
                    curve = getattr(wire_edges[0], "Curve", None)
                    radius = getattr(curve, "Radius", None)
                    try:
                        role = "drill" if float(radius) * 2.0 <= 12.0 + 1e-9 else "cut_internal"
                    except (TypeError, ValueError):
                        role = "drill"
            role_metadata = {
                "import_role": role,
                "source_shape_component_id": component_id,
            }
            if len(wire_edges) == 1 and bool(getattr(wire_edges[0], "isClosed", lambda: False)()):
                entity, message = _full_curve_entity(
                    wire_edges[0],
                    api,
                    layer_id,
                    placement,
                    projection,
                    metadata=role_metadata,
                )
                if entity is not None:
                    source_entities.append(entity)
                else:
                    issues.append(
                        ImportIssue(wire_index, type(wire_edges[0].Curve).__name__, message or "Não suportada")
                    )
                continue

            spans = []
            for edge_index, edge in enumerate(wire_edges):
                span, message = _edge_to_span(edge, api, placement, projection)
                if span is not None:
                    spans.append(span)
                else:
                    issues.append(
                        ImportIssue(
                            edge_index,
                            type(getattr(edge, "Curve", edge)).__name__,
                            message or "Aresta não suportada.",
                        )
                    )
            if spans:
                source_entities.append(
                    _path_entity(
                        api,
                        layer_id,
                        spans,
                        bool(wire is not None and wire.isClosed()),
                        metadata=role_metadata,
                    )
                )

        # A blind recess is represented by its planar bottom face, not by the
        # stock outline.  Import that face through the same exact OCC converter
        # and mark the resulting vectors as machining regions.  Closed pockets
        # often repeat the same XY boundary as an inner wire on the top face;
        # keep only the semantic pocket copy so diagnostics never see a false
        # duplicate.  An edge-open recess has no such top inner wire and is the
        # case that motivated this extraction.
        if pocket_faces:
            from woodcam_editor.domain.validation import _geometry_signature

            for pocket_index, (
                pocket_face,
                pocket_depth,
                reference_level,
                bottom_level,
            ) in enumerate(pocket_faces, start=1):
                pocket_result = import_part_shape(
                    pocket_face,
                    layer_id=layer_id,
                    placement=placement,
                    flatten_solids=False,
                    compound_groups=False,
                    _projection=projection,
                    _stage_projection=False,
                )
                pocket_leaves = tuple(
                    entity
                    for entity in pocket_result.entities
                    if type(entity).__name__ != "GroupEntity"
                )
                outer_candidates = tuple(
                    entity
                    for entity in pocket_leaves
                    if str(
                        (getattr(entity, "metadata", {}) or {}).get(
                            "import_role", ""
                        )
                        or ""
                    )
                    == "cut_external"
                )
                if not outer_candidates:
                    issues.append(
                        ImportIssue(
                            pocket_index - 1,
                            "PocketFace",
                            "O fundo de um rebaixo foi detectado, mas não formou uma região fechada.",
                        )
                    )
                    continue
                region = max(
                    outer_candidates,
                    key=lambda entity: abs(
                        float(getattr(entity, "signed_area", lambda *_: 0.0)(0.05))
                    )
                    if hasattr(entity, "signed_area")
                    else float(entity.bounds().width * entity.bounds().height),
                )
                region_signature = _geometry_signature(region, 1.0e-6, 0.01)
                matching_indices = [
                    index
                    for index, entity in enumerate(source_entities)
                    if str(
                        (getattr(entity, "metadata", {}) or {}).get(
                            "import_role", ""
                        )
                        or ""
                    )
                    in {"cut_internal", "drill"}
                    and _geometry_signature(entity, 1.0e-6, 0.01)
                    == region_signature
                ]
                is_small_blind_drill = bool(
                    type(region).__name__ == "CircleEntity"
                    and float(getattr(region, "radius", 0.0) or 0.0) * 2.0
                    <= 12.0 + 1.0e-9
                )
                common_metadata = {
                    "source_shape_component_id": component_id,
                    "source_pocket_index": int(pocket_index),
                    "pocket_depth_mm": float(pocket_depth),
                    "source_reference_level_mm": float(reference_level),
                    "source_bottom_level_mm": float(bottom_level),
                }
                if is_small_blind_drill:
                    if matching_indices:
                        match_index = matching_indices[0]
                        existing = source_entities[match_index]
                        metadata = dict(getattr(existing, "metadata", {}) or {})
                        metadata.update(common_metadata)
                        metadata["source_depth_mm"] = float(pocket_depth)
                        source_entities[match_index] = replace(
                            existing,
                            metadata=metadata,
                        )
                    else:
                        metadata = dict(getattr(region, "metadata", {}) or {})
                        metadata.update(common_metadata)
                        metadata.update(
                            {
                                "import_role": "drill",
                                "source_depth_mm": float(pocket_depth),
                            }
                        )
                        source_entities.append(replace(region, metadata=metadata))
                    continue

                for index in reversed(matching_indices):
                    source_entities.pop(index)
                region_metadata = dict(getattr(region, "metadata", {}) or {})
                region_metadata.update(common_metadata)
                region_metadata["import_role"] = "pocket_region"
                region = replace(region, metadata=region_metadata)
                source_entities.append(region)
                for island in pocket_leaves:
                    if island.id == region.id:
                        continue
                    island_metadata = dict(getattr(island, "metadata", {}) or {})
                    island_metadata.update(common_metadata)
                    island_metadata.update(
                        {
                            "import_role": "pocket_island",
                            "pocket_region_id": str(region.id),
                        }
                    )
                    source_entities.append(
                        replace(island, metadata=island_metadata)
                    )
        if projection is None:
            entities.extend(source_entities)
            if compound_groups and len(source_entities) > 1:
                compound_child_groups.append(
                    (component_id, tuple(str(entity.id) for entity in source_entities))
                )
        elif source_entities and _stage_projection:
            flattened_groups.append(source_entities)
            if compound_groups and len(source_entities) > 1:
                compound_child_groups.append(
                    (component_id, tuple(str(entity.id) for entity in source_entities))
                )
        elif source_entities:
            entities.extend(source_entities)

    # A local plane has no useful shared XY position with the assembly.  Put
    # every flattened solid in a compact staging grid so that piece recognition
    # never mistakes one panel for a hole of another before the organizer runs.
    #
    # This is deliberately *not* nesting: no rotation is chosen, no material
    # sheet is consumed and no placement is persisted as a machining layout.
    # It is simply the same safe presentation used by the Assembly→DXF macro:
    # each broad face is flattened independently and parked near the others,
    # instead of producing one enormous horizontal strip of vectors.
    if flattened_groups:
        from woodcam_editor.domain.primitives import Affine2D, Vec2

        raw_max_x = max((entity.bounds().max_x for entity in entities), default=-25.0)
        gap = 25.0
        group_bounds = []
        total_area = 0.0
        widest = gap
        for group in flattened_groups:
            group_min_x = min(entity.bounds().min_x for entity in group)
            group_min_y = min(entity.bounds().min_y for entity in group)
            group_max_x = max(entity.bounds().max_x for entity in group)
            group_max_y = max(entity.bounds().max_y for entity in group)
            width = max(0.0, group_max_x - group_min_x)
            height = max(0.0, group_max_y - group_min_y)
            group_bounds.append((group, group_min_x, group_min_y, width, height))
            total_area += (width + gap) * (height + gap)
            widest = max(widest, width + gap)
        # A square-root target keeps a cabinet import visually compact while
        # remaining deterministic and very cheap for hundreds of panels.
        target_row_width = max(widest, math.sqrt(total_area) * 1.25)
        staging_x = raw_max_x + gap
        staging_y = 0.0
        row_height = 0.0
        for group, group_min_x, group_min_y, width, height in group_bounds:
            if staging_x > raw_max_x + gap and staging_x + width > raw_max_x + gap + target_row_width:
                staging_x = raw_max_x + gap
                staging_y += row_height + gap
                row_height = 0.0
            translation = Affine2D.translation(
                Vec2(staging_x - group_min_x, staging_y - group_min_y)
            )
            entities.extend(entity.transformed(translation) for entity in group)
            staging_x += width + gap
            row_height = max(row_height, height)

    if compound_groups and compound_child_groups:
        from woodcam_editor.domain.entities import GroupEntity

        entities.extend(
            GroupEntity(
                layer_id=layer_id,
                child_ids=child_ids,
                metadata={
                    "name": "Peça importada",
                    "source_kind": "part_face_compound",
                    "source_shape_component_id": component_id,
                },
            )
            for component_id, child_ids in compound_child_groups
        )

    metadata = {
        "source_kind": "part_shape",
        "source_name": str(getattr(source, "Name", "") or ""),
        "source_label": str(getattr(source, "Label", "") or ""),
        "source_shape_component_count": len(source_shapes),
    }
    source_layer_key = metadata["source_name"] or "part_shape:default"
    source_layer_name = metadata["source_label"] or metadata["source_name"] or "Forma importada"
    role_layers = {
        "cut_external": ImportLayerDescriptor(
            source_key=source_layer_key + ":cut_external",
            name="Corte externo",
            color="#f97316",
            purpose="cut",
        ),
        "cut_internal": ImportLayerDescriptor(
            source_key=source_layer_key + ":cut_internal",
            name="Corte interno",
            color="#f59e0b",
            purpose="pocket",
        ),
        "drill": ImportLayerDescriptor(
            source_key=source_layer_key + ":drill",
            name="Furos",
            color="#2563eb",
            purpose="drill",
        ),
        "pocket_region": ImportLayerDescriptor(
            source_key=source_layer_key + ":pocket_region",
            name="Rebaixos importados",
            color="#0f766e",
            purpose="pocket",
        ),
        "pocket_island": ImportLayerDescriptor(
            source_key=source_layer_key + ":pocket_island",
            name="Ilhas de rebaixo",
            color="#0d9488",
            purpose="pocket",
        ),
    }
    marked_entities = []
    for entity in entities:
        entity_metadata = dict(getattr(entity, "metadata", {}) or {})
        role = str(entity_metadata.get("import_role", "design") or "design")
        entity_layer_key = (
            source_layer_key + ":" + role if role in role_layers else source_layer_key
        )
        entity_metadata.update(
            {
                "source_format": "part_shape",
                "source_layer_key": entity_layer_key,
            }
        )
        marked_entities.append(replace(entity, metadata=entity_metadata))
    used_roles = {
        str((getattr(entity, "metadata", {}) or {}).get("import_role", "") or "")
        for entity in marked_entities
    }
    layers = {
        source_layer_key: ImportLayerDescriptor(
            source_key=source_layer_key,
            name=source_layer_name,
            purpose="design",
        ),
    }
    layers.update(
        {
            key: descriptor
            for key, descriptor in role_layers.items()
            if key.rsplit(":", 1)[-1] in used_roles
        }
    )
    return ImportResult(tuple(marked_entities), tuple(issues), metadata, layers)
