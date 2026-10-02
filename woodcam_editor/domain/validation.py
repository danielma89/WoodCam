"""Read-only vector validation.

The validator only reports deterministic issues.  It never edits geometry;
every suggested repair must be previewed and executed as a separate command.
"""

from __future__ import annotations

from dataclasses import dataclass
from collections import Counter
from enum import Enum
import hashlib
import itertools
import math
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .document import (
    VectorDocument,
    WorkArea,
    entity_is_cam_eligible,
    entity_is_piece_marking,
    entity_is_pocket_feature,
    entity_is_remnant_cut,
    pocket_feature_owner_key,
)
from .entities import CircleEntity, EllipseEntity, GroupEntity, PathEntity
from .primitives import DEFAULT_EPSILON, InvariantError, Vec2
from .topology import build_containment_tree, build_topology, nearby_open_endpoint_pairs
from ..geometry.math2d import (
    PointLocation,
    SegmentIntersection,
    point_in_polygon,
    segment_intersections,
)


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    BLOCKER = "blocker"


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    id: str
    severity: Severity
    code: str
    message: str
    entity_ids: Tuple[str, ...] = ()
    span_ids: Tuple[str, ...] = ()
    node_ids: Tuple[str, ...] = ()
    points: Tuple[Vec2, ...] = ()
    suggested_actions: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ValidationReport:
    issues: Tuple[ValidationIssue, ...]
    source_revision: int

    @property
    def blockers(self) -> Tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity is Severity.BLOCKER)

    @property
    def errors(self) -> Tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity in (Severity.ERROR, Severity.BLOCKER))

    @property
    def is_valid_for_cam(self) -> bool:
        return not self.errors

    def by_code(self, code: str) -> Tuple[ValidationIssue, ...]:
        return tuple(issue for issue in self.issues if issue.code == code)


def _issue_id(
    code: str,
    entity_ids: Iterable[str] = (),
    span_ids: Iterable[str] = (),
    points: Iterable[Vec2] = (),
) -> str:
    point_key = ["%.12g,%.12g" % (point.x, point.y) for point in points]
    key = "|".join(
        [code]
        + sorted(entity_ids)
        + sorted(span_ids)
        + sorted(point_key)
    )
    return "issue-%s" % hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def _make_issue(
    severity: Severity,
    code: str,
    message: str,
    *,
    entity_ids: Iterable[str] = (),
    span_ids: Iterable[str] = (),
    node_ids: Iterable[str] = (),
    points: Iterable[Vec2] = (),
    suggested_actions: Iterable[str] = (),
) -> ValidationIssue:
    entity_ids = tuple(entity_ids)
    span_ids = tuple(span_ids)
    node_ids = tuple(node_ids)
    points = tuple(points)
    return ValidationIssue(
        id=_issue_id(code, entity_ids, span_ids, points),
        severity=severity,
        code=code,
        message=message,
        entity_ids=entity_ids,
        span_ids=span_ids,
        node_ids=node_ids,
        points=points,
        suggested_actions=tuple(suggested_actions),
    )


def validate_document_invariants(document: VectorDocument) -> None:
    """Raise :class:`InvariantError` for non-negotiable structural corruption."""

    document.validate_invariants()
    span_ids = []
    node_ids = []
    for entity in document.entities_by_id.values():
        if isinstance(entity, PathEntity):
            span_ids.extend(span.id for span in entity.spans)
            node_ids.extend(entity.node_ids)
        elif isinstance(entity, CircleEntity):
            node_ids.extend((entity.center_node_id, entity.radius_node_id))
        elif isinstance(entity, EllipseEntity):
            node_ids.append(entity.center_node_id)
    if len(span_ids) != len(set(span_ids)):
        raise InvariantError("span IDs must be globally unique")
    if len(node_ids) != len(set(node_ids)):
        raise InvariantError("node IDs must be globally unique")
    # Detect cycles between groups independently of presentation hierarchy.
    groups = {
        entity.id: entity
        for entity in document.entities_by_id.values()
        if isinstance(entity, GroupEntity)
    }
    visiting = set()
    visited = set()
    def visit(group_id: str) -> None:
        if group_id in visited:
            return
        if group_id in visiting:
            raise InvariantError("group hierarchy contains a cycle")
        visiting.add(group_id)
        for child_id in groups[group_id].child_ids:
            if child_id in groups:
                visit(child_id)
        visiting.remove(group_id)
        visited.add(group_id)
    for group_id in groups:
        visit(group_id)


def _quantize(value: float, tolerance: float) -> int:
    scale = max(tolerance, DEFAULT_EPSILON)
    return int(round(value / scale))


def _canonical_point_cycle(points: Sequence[Vec2], tolerance: float, closed: bool):
    values = tuple((_quantize(point.x, tolerance), _quantize(point.y, tolerance)) for point in points)
    if not values:
        return values
    if not closed:
        reverse = tuple(reversed(values))
        return min(values, reverse)
    rotations = []
    for sequence in (values, tuple(reversed(values))):
        rotations.extend(sequence[index:] + sequence[:index] for index in range(len(sequence)))
    return min(rotations)


def _geometry_signature(entity, tolerance: float, deflection: float):
    if isinstance(entity, PathEntity):
        points = entity.flatten(deflection, include_closure=False)
        return ("path", entity.closed, _canonical_point_cycle(points, tolerance, entity.closed))
    if isinstance(entity, CircleEntity):
        return (
            "circle",
            _quantize(entity.center.x, tolerance),
            _quantize(entity.center.y, tolerance),
            _quantize(entity.radius, tolerance),
        )
    if isinstance(entity, EllipseEntity):
        return (
            "ellipse",
            _quantize(entity.center.x, tolerance),
            _quantize(entity.center.y, tolerance),
            _quantize(entity.radius_x, tolerance),
            _quantize(entity.radius_y, tolerance),
            _quantize(entity.rotation, tolerance),
        )
    return None


def exact_duplicate_entity_groups(
    document: VectorDocument,
    *,
    entity_ids: Optional[Iterable[str]] = None,
    geometric_tolerance: float = 1.0e-6,
    deflection: float = 0.01,
    include_non_cam_layers: bool = False,
) -> Tuple[Tuple[str, ...], ...]:
    """Return deterministic groups of geometrically identical entities.

    This is intentionally narrower than generic collinear overlap.  Removing a
    complete exact copy is conservative; deciding which portion of two partly
    overlapping contours survives is a trim/weld operation and must remain an
    explicit operator choice.
    """

    if geometric_tolerance <= 0.0:
        raise ValueError("geometric_tolerance must be positive")
    if deflection <= 0.0:
        raise ValueError("deflection must be positive")
    scope = (
        None
        if entity_ids is None
        else frozenset(str(entity_id) for entity_id in entity_ids)
    )
    grouped: Dict[object, List[str]] = {}
    for entity in sorted(document.entities_by_id.values(), key=lambda item: item.id):
        if scope is not None and entity.id not in scope:
            continue
        if not include_non_cam_layers and not entity_is_cam_eligible(document, entity):
            continue
        signature = _geometry_signature(entity, geometric_tolerance, deflection)
        if signature is not None:
            grouped.setdefault(signature, []).append(entity.id)
    return tuple(
        tuple(ids)
        for ids in grouped.values()
        if len(ids) > 1
    )


def _span_geometry_signature(span, tolerance: float, deflection: float):
    points = tuple(span.flatten(deflection))
    if len(points) < 2:
        return None
    return (
        type(span).__name__,
        _canonical_point_cycle(points, tolerance, False),
    )


def redundant_open_overline_entity_ids(
    document: VectorDocument,
    *,
    entity_ids: Optional[Iterable[str]] = None,
    geometric_tolerance: float = 1.0e-6,
    deflection: float = 0.01,
) -> Tuple[str, ...]:
    """Find open paths exactly redrawn over spans of one closed contour.

    PanelNest DXF exports can contain both the useful closed LWPOLYLINE and
    several LINE copies for every side.  The open copies carry no additional
    boundary and prevent piece classification.  A candidate is removable only
    when every one of its exact span signatures is already present, with the
    required multiplicity, in the same closed path.
    """

    if geometric_tolerance <= 0.0:
        raise ValueError("geometric_tolerance must be positive")
    if deflection <= 0.0:
        raise ValueError("deflection must be positive")
    scope = (
        None
        if entity_ids is None
        else frozenset(str(entity_id) for entity_id in entity_ids)
    )
    closed_span_sets = []
    for entity in document.entities_by_id.values():
        if not isinstance(entity, PathEntity) or not entity.closed:
            continue
        if scope is not None and entity.id not in scope:
            continue
        if not entity_is_cam_eligible(document, entity):
            continue
        signatures = Counter(
            signature
            for signature in (
                _span_geometry_signature(span, geometric_tolerance, deflection)
                for span in entity.spans
            )
            if signature is not None
        )
        if signatures:
            closed_span_sets.append(signatures)

    redundant = []
    for entity in sorted(document.entities_by_id.values(), key=lambda item: item.id):
        if not isinstance(entity, PathEntity) or entity.closed:
            continue
        if scope is not None and entity.id not in scope:
            continue
        if not entity_is_cam_eligible(document, entity):
            continue
        signatures = Counter(
            signature
            for signature in (
                _span_geometry_signature(span, geometric_tolerance, deflection)
                for span in entity.spans
            )
            if signature is not None
        )
        if signatures and any(
            all(available[signature] >= count for signature, count in signatures.items())
            for available in closed_span_sets
        ):
            redundant.append(entity.id)
    return tuple(redundant)


@dataclass(frozen=True, slots=True)
class _FlatSegment:
    entity_id: str
    span_id: str
    ordinal: int
    total: int
    closed: bool
    start: Vec2
    end: Vec2


def _flat_segments(entity, deflection: float) -> Tuple[_FlatSegment, ...]:
    raw = []
    if isinstance(entity, PathEntity):
        ordinal = 0
        for span in entity.spans:
            points = span.flatten(deflection)
            for index in range(len(points) - 1):
                raw.append((span.id, ordinal, points[index], points[index + 1]))
                ordinal += 1
        total = len(raw)
        return tuple(
            _FlatSegment(entity.id, span_id, ordinal, total, entity.closed, start, end)
            for span_id, ordinal, start, end in raw
        )
    if isinstance(entity, (CircleEntity, EllipseEntity)):
        points = entity.flatten(deflection, include_closure=True)
        total = len(points) - 1
        return tuple(
            _FlatSegment(entity.id, entity.id, index, total, True, points[index], points[index + 1])
            for index in range(total)
        )
    return ()


def _segment_intersection_candidates(
    segments: Sequence[_FlatSegment],
    tolerance: float,
):
    """Yield unique broad-phase candidates for segment intersection.

    Importing a furniture skeleton can produce thousands of small segments
    (teeth, holes and repeated construction details).  Testing every segment
    against every other turns a read-only Diagnose click into O(n²) work.  The
    grid below indexes expanded segment bounding boxes; it may return harmless
    false positives, but cannot omit a pair whose segments meet within the
    requested tolerance.  Exact intersection remains the authoritative test.
    """

    if len(segments) < 2:
        return
    min_x = min(min(item.start.x, item.end.x) for item in segments)
    max_x = max(max(item.start.x, item.end.x) for item in segments)
    min_y = min(min(item.start.y, item.end.y) for item in segments)
    max_y = max(max(item.start.y, item.end.y) for item in segments)
    span = max(max_x - min_x, max_y - min_y)
    # About sqrt(n) cells across the dominant dimension keeps ordinary source
    # segments in very few buckets while long members still remain bounded.
    cell_size = max(
        float(tolerance),
        span / max(1.0, math.sqrt(float(len(segments)))),
        DEFAULT_EPSILON,
    )
    buckets: Dict[Tuple[int, int], List[int]] = {}
    emitted = set()
    padding = max(float(tolerance), DEFAULT_EPSILON)
    for index, segment in enumerate(segments):
        left = min(segment.start.x, segment.end.x) - padding
        right = max(segment.start.x, segment.end.x) + padding
        bottom = min(segment.start.y, segment.end.y) - padding
        top = max(segment.start.y, segment.end.y) + padding
        min_col = int(math.floor((left - min_x) / cell_size))
        max_col = int(math.floor((right - min_x) / cell_size))
        min_row = int(math.floor((bottom - min_y) / cell_size))
        max_row = int(math.floor((top - min_y) / cell_size))
        cells = []
        for column in range(min_col, max_col + 1):
            for row in range(min_row, max_row + 1):
                cell = (column, row)
                cells.append(cell)
                for previous in buckets.get(cell, ()):
                    pair = (previous, index)
                    if pair not in emitted:
                        emitted.add(pair)
                        yield pair
        for cell in cells:
            buckets.setdefault(cell, []).append(index)


def _adjacent(left: _FlatSegment, right: _FlatSegment) -> bool:
    if left.entity_id != right.entity_id:
        return False
    difference = abs(left.ordinal - right.ordinal)
    return difference <= 1 or (left.closed and difference == left.total - 1)


def _closed_entity_points(entity, deflection: float) -> Tuple[Vec2, ...]:
    if isinstance(entity, PathEntity):
        return entity.flatten(deflection, include_closure=False) if entity.closed else ()
    if isinstance(entity, (CircleEntity, EllipseEntity)):
        return entity.flatten(deflection, include_closure=False)
    return ()


def _physical_import_scope(entity) -> Optional[Tuple[str, ...]]:
    """Return the explicit physical-board scope carried by an import.

    A direct Assembly import contains many boards whose local flattened
    profiles can legitimately occupy the same coordinates before staging or
    after an operator moves them.  They are not duplicate vectors, touching
    contours or branches of one part.  The importer stamps each leaf with a
    stable physical instance; PanelNest uses an equivalent instance key.
    Unscoped hand-drawn vectors intentionally remain comparable with every
    other vector so normal editor diagnostics retain their existing meaning.
    """

    metadata = dict(getattr(entity, "metadata", {}) or {})
    batch = str(metadata.get("import_batch_id", "") or "")
    instance = str(
        metadata.get("panelnest_instance_id", "")
        or metadata.get("source_tree_instance_id", "")
        or ""
    )
    if instance:
        return ("physical-instance", batch, instance)
    if batch:
        return ("import-batch", batch)
    return None


def _same_physical_scope(first, second) -> bool:
    """Whether two entities may describe the same manufacturing profile."""

    first_scope = _physical_import_scope(first)
    second_scope = _physical_import_scope(second)
    # Manual/unscoped drawing can intentionally intersect an imported part.
    return first_scope is None or second_scope is None or first_scope == second_scope


def entities_have_boundary_only_contact(
    document: VectorDocument,
    first_id: str,
    second_id: str,
    *,
    tolerance: float = 1.0e-6,
    deflection: float = 0.01,
) -> bool:
    """Return true when two independent closed contours only touch boundaries.

    Adjacent nested parts commonly share corners or a complete edge before the
    editor organizer adds machining spacing.  They are safe to classify as
    separate rigid pieces, unlike contours with interior overlap or a proper
    crossing.  This predicate is read-only and never welds their topology.
    """

    if first_id == second_id:
        return False
    first = document.entities_by_id.get(first_id)
    second = document.entities_by_id.get(second_id)
    first_points = _closed_entity_points(first, deflection)
    second_points = _closed_entity_points(second, deflection)
    if len(first_points) < 3 or len(second_points) < 3:
        return False

    intersections = []
    for first_index, first_start in enumerate(first_points):
        first_end = first_points[(first_index + 1) % len(first_points)]
        for second_index, second_start in enumerate(second_points):
            second_end = second_points[(second_index + 1) % len(second_points)]
            intersections.extend(
                segment_intersections(
                    first_start,
                    first_end,
                    second_start,
                    second_end,
                    tolerance,
                )
            )
    if not intersections:
        return False
    parameter_epsilon = min(0.25, tolerance)
    if any(
        intersection.kind == "cross"
        and parameter_epsilon < intersection.parameter_a < 1.0 - parameter_epsilon
        and parameter_epsilon < intersection.parameter_b < 1.0 - parameter_epsilon
        for intersection in intersections
    ):
        return False
    if any(
        point_in_polygon(point, second_points, tolerance) is PointLocation.INSIDE
        for point in first_points
    ):
        return False
    if any(
        point_in_polygon(point, first_points, tolerance) is PointLocation.INSIDE
        for point in second_points
    ):
        return False
    return True


def _deduplicate_points(points: Iterable[Vec2], tolerance: float) -> Tuple[Vec2, ...]:
    result = []
    for point in points:
        if not any(point.almost_equals(existing, tolerance) for existing in result):
            result.append(point)
    return tuple(result)


def _is_intentional_relief_touch(
    document: VectorDocument,
    entity_id: str,
    point: Vec2,
    tolerance: float,
) -> bool:
    entity = document.entities_by_id.get(entity_id)
    if not isinstance(entity, PathEntity):
        return False
    values = entity.metadata.get("intentional_relief_touch_points", ())
    for value in values:
        try:
            allowed = Vec2.from_sequence(value)
        except (TypeError, ValueError):
            continue
        if point.almost_equals(allowed, tolerance):
            return True
    return False


def _pocket_feature_relationship(first, second) -> bool:
    """Return whether two contours belong to one imported shallow feature.

    A pocket open to the stock edge legitimately shares a segment with the
    piece profile.  The explicit source component is required; unrelated
    contours are never excused merely because one happens to be on a pocket
    layer.
    """

    if first is None or second is None:
        return False
    if not (entity_is_pocket_feature(first) or entity_is_pocket_feature(second)):
        return False
    first_owner = pocket_feature_owner_key(first)
    second_owner = pocket_feature_owner_key(second)
    return bool(first_owner and second_owner and first_owner == second_owner)


def validate_document(
    document: VectorDocument,
    *,
    geometric_tolerance: float = 1.0e-6,
    join_tolerance: float = 0.2,
    microspan_length: float = 0.01,
    deflection: float = 0.01,
    include_non_cam_layers: bool = False,
    entity_ids: Optional[Iterable[str]] = None,
) -> ValidationReport:
    """Return a deterministic, read-only report for editor and CAM gating.

    By default geometry on hidden, ``reference`` or ``construction`` layers is
    excluded from CAM issues.  Set ``include_non_cam_layers=True`` only for an
    explicit all-layer diagnostic; structural document invariants are always
    checked regardless of presentation state.
    """

    for value, name in (
        (geometric_tolerance, "geometric_tolerance"),
        (join_tolerance, "join_tolerance"),
        (microspan_length, "microspan_length"),
        (deflection, "deflection"),
    ):
        if value <= 0.0:
            raise ValueError("%s must be positive" % name)
    issues: List[ValidationIssue] = []
    try:
        validate_document_invariants(document)
    except InvariantError as exc:
        issues.append(
            _make_issue(
                Severity.BLOCKER,
                "DOCUMENT_INVARIANT",
                str(exc),
                suggested_actions=("repair_document_or_restore_backup",),
            )
        )
        return ValidationReport(tuple(issues), document.revision)

    requested_scope = (
        None
        if entity_ids is None
        else frozenset(str(entity_id) for entity_id in entity_ids)
    )
    all_cam_entities = tuple(
        entity
        for entity in sorted(document.entities_by_id.values(), key=lambda item: item.id)
        if requested_scope is None or entity.id in requested_scope
        if include_non_cam_layers or entity_is_cam_eligible(document, entity)
        if not entity_is_remnant_cut(entity)
    )
    valid_work_areas = []
    if document.work_area is not None:
        valid_work_areas.append(document.work_area)
    for raw_bounds in tuple(
        (document.metadata or {}).get("organization_sheet_bounds", ()) or ()
    )[1:]:
        try:
            valid_work_areas.append(WorkArea(*map(float, raw_bounds)))
        except (TypeError, ValueError):
            # Metadata is auxiliary projection state; malformed legacy values
            # must not crash a read-only diagnostic.
            continue
    piece_marking_ids = frozenset(
        {
            marking_id
            for piece in document.pieces_by_id.values()
            for marking_id in piece.marking_path_ids
        }
        | {
            entity.id
            for entity in all_cam_entities
            if entity_is_piece_marking(entity)
        }
    )
    redundant_overline_ids = frozenset(
        redundant_open_overline_entity_ids(
            document,
            entity_ids=(
                entity.id
                for entity in all_cam_entities
                if entity.id not in piece_marking_ids
            ),
            geometric_tolerance=geometric_tolerance,
            deflection=deflection,
        )
    )
    if redundant_overline_ids:
        issues.append(
            _make_issue(
                Severity.BLOCKER,
                "REDUNDANT_OPEN_OVERLINES",
                "%d linha(s) aberta(s) já estão cobertas por contornos fechados; "
                "use Reparar → Limpar sobrelinhas/duplicados…"
                % len(redundant_overline_ids),
                entity_ids=tuple(sorted(redundant_overline_ids)),
                suggested_actions=("preview_delete_redundant_overlines",),
            )
        )
    cam_entities = tuple(
        entity
        for entity in all_cam_entities
        if entity.id not in redundant_overline_ids
    )
    cam_entity_ids = frozenset(entity.id for entity in cam_entities)

    for entity in cam_entities:
        if isinstance(entity, PathEntity):
            if not entity.closed and entity.id not in piece_marking_ids:
                nodes = entity.nodes()
                issues.append(
                    _make_issue(
                        Severity.BLOCKER,
                        "OPEN_PATH",
                        "O caminho está aberto.",
                        entity_ids=(entity.id,),
                        node_ids=(nodes[0].id, nodes[-1].id),
                        points=(nodes[0].point, nodes[-1].point),
                        suggested_actions=("join_endpoints", "close_with_line", "close_by_midpoint"),
                    )
                )
            for span in entity.spans:
                length = span.length()
                if length <= geometric_tolerance:
                    issues.append(
                        _make_issue(
                            Severity.BLOCKER,
                            "ZERO_LENGTH_SPAN",
                            "Segmento com comprimento zero ou abaixo da tolerância.",
                            entity_ids=(entity.id,),
                            span_ids=(span.id,),
                            points=(span.start, span.end),
                            suggested_actions=("delete_zero_spans",),
                        )
                    )
                elif length < microspan_length:
                    issues.append(
                        _make_issue(
                            Severity.WARNING,
                            "MICROSPAN",
                            "Segmento muito curto: %.6g mm." % length,
                            entity_ids=(entity.id,),
                            span_ids=(span.id,),
                            points=(span.start, span.end),
                            suggested_actions=("review_microspan",),
                        )
                    )
            if entity.closed and abs(entity.signed_area(deflection)) <= geometric_tolerance ** 2:
                issues.append(
                    _make_issue(
                        Severity.BLOCKER,
                        "ZERO_AREA_PATH",
                        "O caminho fechado possui área nula.",
                        entity_ids=(entity.id,),
                        suggested_actions=("inspect_path",),
                    )
                )

        if valid_work_areas and hasattr(entity, "bounds"):
            bounds = entity.bounds()
            if not any(
                area.contains(bounds, geometric_tolerance)
                for area in valid_work_areas
            ):
                issues.append(
                    _make_issue(
                        Severity.WARNING,
                        "OUTSIDE_WORK_AREA",
                        "A entidade está total ou parcialmente fora da área de Trabalho.",
                        entity_ids=(entity.id,),
                        points=(bounds.center,),
                        suggested_actions=("move_inside_work_area", "resize_work_area"),
                    )
                )

    for duplicate_ids in exact_duplicate_entity_groups(
        document,
        entity_ids=cam_entity_ids,
        geometric_tolerance=geometric_tolerance,
        deflection=deflection,
        include_non_cam_layers=include_non_cam_layers,
    ):
        by_scope: Dict[Optional[Tuple[str, ...]], List[str]] = {}
        for entity_id in duplicate_ids:
            entity = document.entities_by_id.get(entity_id)
            if entity is not None:
                by_scope.setdefault(_physical_import_scope(entity), []).append(entity_id)
        for same_scope_ids in by_scope.values():
            if len(same_scope_ids) < 2:
                continue
            original_id = same_scope_ids[0]
            for duplicate_id in same_scope_ids[1:]:
                original = document.entities_by_id.get(original_id)
                duplicate = document.entities_by_id.get(duplicate_id)
                if _pocket_feature_relationship(original, duplicate):
                    continue
                issues.append(
                    _make_issue(
                        Severity.ERROR,
                        "DUPLICATE_ENTITY",
                        "Geometria duplicada sobreposta.",
                        entity_ids=(original_id, duplicate_id),
                        suggested_actions=("delete_duplicate",),
                    )
                )

    for pair in nearby_open_endpoint_pairs(
        document,
        join_tolerance,
        minimum_distance=geometric_tolerance,
        entity_ids=cam_entity_ids,
    ):
        first = document.entities_by_id.get(pair.first.entity_id)
        second = document.entities_by_id.get(pair.second.entity_id)
        if first is None or second is None or not _same_physical_scope(first, second):
            continue
        issues.append(
            _make_issue(
                Severity.WARNING,
                "NEAR_OPEN_ENDPOINTS",
                "Extremidades abertas estão a %.6g mm." % pair.distance,
                entity_ids=(pair.first.entity_id, pair.second.entity_id),
                node_ids=(pair.first.node_id, pair.second.node_id),
                points=(pair.first.point, pair.second.point),
                suggested_actions=("preview_join_endpoints",),
            )
        )

    topology = build_topology(
        document,
        geometric_tolerance,
        entity_ids=cam_entity_ids,
    )
    boundary_contact_cache: Dict[Tuple[str, str], bool] = {}

    def boundary_only_contact(first_id: str, second_id: str) -> bool:
        pair = tuple(sorted((first_id, second_id)))
        if pair not in boundary_contact_cache:
            boundary_contact_cache[pair] = entities_have_boundary_only_contact(
                document,
                pair[0],
                pair[1],
                tolerance=geometric_tolerance,
                deflection=deflection,
            )
        return boundary_contact_cache[pair]

    def pocket_feature_contact(first_id: str, second_id: str) -> bool:
        return _pocket_feature_relationship(
            document.entities_by_id.get(first_id),
            document.entities_by_id.get(second_id),
        )

    for node in topology.branch_nodes:
        branch_entity_ids = tuple(sorted({ref.entity_id for ref in node.references}))
        if (
            len(branch_entity_ids) == 1
            and _is_intentional_relief_touch(
                document, branch_entity_ids[0], node.point, geometric_tolerance
            )
        ):
            continue
        if len(branch_entity_ids) > 1 and all(
            boundary_only_contact(first_id, second_id)
            or pocket_feature_contact(first_id, second_id)
            for first_id, second_id in itertools.combinations(branch_entity_ids, 2)
        ):
            # These are independent closed pieces touching before organization,
            # not one branched path. The pair-level issue below remains a CAM
            # blocker until spacing is applied.
            continue
        issues.append(
            _make_issue(
                Severity.BLOCKER,
                "BRANCH_NODE",
                "Ramificação topológica com grau %d." % node.degree,
                entity_ids=branch_entity_ids,
                node_ids=tuple(ref.node_id for ref in node.references),
                points=(node.point,),
                suggested_actions=("split_or_trim_branch",),
            )
        )

    segments = []
    for entity in cam_entities:
        segments.extend(_flat_segments(entity, deflection))
    intersections_by_pair: Dict[Tuple[str, str], List[Vec2]] = {}
    spans_by_pair: Dict[Tuple[str, str], set] = {}
    for left_index, right_index in _segment_intersection_candidates(
        segments, geometric_tolerance
    ):
        left = segments[left_index]
        right = segments[right_index]
        if _adjacent(left, right):
            continue
        left_entity = document.entities_by_id.get(left.entity_id)
        right_entity = document.entities_by_id.get(right.entity_id)
        if (
            left_entity is None
            or right_entity is None
            or not _same_physical_scope(left_entity, right_entity)
        ):
            continue
        intersections = segment_intersections(
            left.start,
            left.end,
            right.start,
            right.end,
            geometric_tolerance,
        )
        if not intersections:
            continue
        pair = tuple(sorted((left.entity_id, right.entity_id)))
        if pair[0] != pair[1] and pocket_feature_contact(pair[0], pair[1]):
            continue
        intersections_by_pair.setdefault(pair, []).extend(item.point for item in intersections)
        spans_by_pair.setdefault(pair, set()).update((left.span_id, right.span_id))
    for pair in sorted(intersections_by_pair):
        points = _deduplicate_points(intersections_by_pair[pair], geometric_tolerance)
        self_intersection = pair[0] == pair[1]
        if self_intersection:
            points = tuple(
                point
                for point in points
                if not _is_intentional_relief_touch(
                    document, pair[0], point, geometric_tolerance
                )
            )
            if not points:
                continue
        boundary_contact = (
            not self_intersection
            and boundary_only_contact(pair[0], pair[1])
        )
        issues.append(
            _make_issue(
                Severity.BLOCKER,
                "SELF_INTERSECTION"
                if self_intersection
                else "TOUCHING_CONTOURS"
                if boundary_contact
                else "CONTOUR_INTERSECTION",
                "O contorno possui auto-interseção."
                if self_intersection
                else "Contornos fechados apenas se tocam; organize com espaçamento antes do CAM."
                if boundary_contact
                else "Dois contornos se intersectam ou sobrepõem.",
                entity_ids=(pair[0],) if self_intersection else pair,
                span_ids=tuple(sorted(spans_by_pair[pair])),
                points=points,
                suggested_actions=("classify_and_organize",)
                if boundary_contact
                else ("inspect_intersection", "trim_interactively"),
            )
        )

    # Verify declared pieces against containment. Classification itself remains
    # a separate explicit command/service.
    if document.pieces_by_id:
        tree = build_containment_tree(
            document,
            deflection,
            geometric_tolerance,
            entity_ids=cam_entity_ids,
        )
        for piece in document.pieces_by_id.values():
            if piece.outer_path_id not in cam_entity_ids:
                continue
            for inner_id in piece.inner_path_ids:
                if inner_id not in cam_entity_ids:
                    continue
                ancestor = tree.parent_by_id.get(inner_id)
                is_descendant = False
                while ancestor is not None:
                    if ancestor == piece.outer_path_id:
                        is_descendant = True
                        break
                    ancestor = tree.parent_by_id.get(ancestor)
                if not is_descendant:
                    issues.append(
                        _make_issue(
                            Severity.BLOCKER,
                            "INNER_OUTSIDE_PIECE",
                            "Recorte interno não está contido na peça externa.",
                            entity_ids=(piece.outer_path_id, inner_id),
                            suggested_actions=("reclassify_pieces",),
                        )
                    )

    severity_order = {
        Severity.BLOCKER: 0,
        Severity.ERROR: 1,
        Severity.WARNING: 2,
        Severity.INFO: 3,
    }
    issues.sort(key=lambda issue: (severity_order[issue.severity], issue.code, issue.id))
    return ValidationReport(tuple(issues), document.revision)
