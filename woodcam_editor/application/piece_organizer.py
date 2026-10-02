"""Pure 2D piece classification and deterministic MaxRects organization.

The organizer treats an outer loop and every nested loop as one rigid unit.
It intentionally returns a preview plan instead of mutating a document; the
controller turns the accepted plan into one atomic command.  Packing evaluates
bottom-left free rectangles, deterministic input orders and allowed rotations.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import math
from functools import lru_cache
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from woodcam_editor.domain.document import (
    entity_is_cam_eligible,
    entity_is_pocket_feature,
    entity_is_remnant_cut,
    pocket_feature_owner_key,
)
from woodcam_editor.domain.primitives import Affine2D, Vec2
from woodcam_editor.geometry.polygon_offset import common_line_offset_closed_polygon
from .common_line import (
    CommonLineContour,
    CommonLineIssue,
    CommonLineIssueCode,
    plan_common_line_cut,
)


Point = Tuple[float, float]
Bounds = Tuple[float, float, float, float]
DRILL_DIAMETER_MAX_MM = 12.0


class OrganizationSearchCancelled(RuntimeError):
    """Internal/public stop signal for a progressive nesting search."""


def _check_search_cancelled(stop_requested: Optional[Callable[[], bool]]) -> None:
    if stop_requested is not None and bool(stop_requested()):
        raise OrganizationSearchCancelled("busca de organização interrompida")


def circle_is_drill(entity: Any, max_diameter_mm: float = DRILL_DIAMETER_MAX_MM) -> bool:
    """Classify a circular contour conservatively for CAM layers."""
    radius = getattr(entity, "radius", None)
    if radius is None:
        return False
    try:
        return float(radius) * 2.0 <= float(max_diameter_mm) + 1e-9
    except (TypeError, ValueError):
        return False


def polygon_area(points: Sequence[Point]) -> float:
    if len(points) < 3:
        return 0.0
    return 0.5 * sum(
        float(point[0]) * float(points[(index + 1) % len(points)][1])
        - float(points[(index + 1) % len(points)][0]) * float(point[1])
        for index, point in enumerate(points)
    )


def polygon_bounds(points: Sequence[Point]) -> Bounds:
    xs = [float(point[0]) for point in points]
    ys = [float(point[1]) for point in points]
    if not xs:
        raise ValueError("Contorno vazio não possui limites.")
    return min(xs), min(ys), max(xs), max(ys)


def point_in_polygon(point: Point, contour: Sequence[Point]) -> bool:
    x, y = float(point[0]), float(point[1])
    inside = False
    for index, current in enumerate(contour):
        previous = contour[index - 1]
        x1, y1 = float(previous[0]), float(previous[1])
        x2, y2 = float(current[0]), float(current[1])
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
    return inside


def _point_on_polygon_boundary(
    point: Point,
    contour: Sequence[Point],
    tolerance: float,
) -> bool:
    px, py = map(float, point)
    limit = max(abs(float(tolerance)), 1.0e-9)
    for index, start in enumerate(contour):
        end = contour[(index + 1) % len(contour)]
        dx = float(end[0]) - float(start[0])
        dy = float(end[1]) - float(start[1])
        length_squared = dx * dx + dy * dy
        if length_squared <= 1.0e-18:
            continue
        ratio = (
            (px - float(start[0])) * dx
            + (py - float(start[1])) * dy
        ) / length_squared
        ratio = max(0.0, min(1.0, ratio))
        nearest = (
            float(start[0]) + dx * ratio,
            float(start[1]) + dy * ratio,
        )
        if _distance(point, nearest) <= limit:
            return True
    return False


def _point_inside_or_on_boundary(
    point: Point,
    contour: Sequence[Point],
    tolerance: float,
) -> bool:
    return point_in_polygon(point, contour) or _point_on_polygon_boundary(
        point,
        contour,
        tolerance,
    )


def _open_path_probes(points: Sequence[Point]) -> Tuple[Point, ...]:
    """Sample every span so a concave piece cannot own a crossing shortcut."""

    probes = list(points)
    for start, end in zip(points, points[1:]):
        for ratio in (0.25, 0.5, 0.75):
            probes.append(
                (
                    float(start[0]) + (float(end[0]) - float(start[0])) * ratio,
                    float(start[1]) + (float(end[1]) - float(start[1])) * ratio,
                )
            )
    return tuple(probes)


def _as_xy(point: Any) -> Point:
    if hasattr(point, "x") and hasattr(point, "y"):
        x_value = point.x() if callable(point.x) else point.x
        y_value = point.y() if callable(point.y) else point.y
        return float(x_value), float(y_value)
    return float(point[0]), float(point[1])


def entity_polyline(entity: Any, deflection: float = 0.05) -> List[Point]:
    """Obtain a closed polyline without coupling to one domain implementation."""
    for method_name in ("flatten", "to_polyline", "discretize"):
        method = getattr(entity, method_name, None)
        if callable(method):
            try:
                value = method(deflection)
            except TypeError:
                value = method()
            if value:
                return [_as_xy(point) for point in value]
    points = getattr(entity, "points", None)
    if points:
        return [_as_xy(point) for point in points]
    spans = list(getattr(entity, "spans", []) or [])
    if spans:
        result: List[Point] = []
        for span in spans:
            flatten = getattr(span, "flatten", None)
            if callable(flatten):
                try:
                    values = flatten(deflection)
                except TypeError:
                    values = flatten()
            else:
                values = [getattr(span, "start"), getattr(span, "end")]
            converted = [_as_xy(point) for point in values]
            if result and converted and _distance(result[-1], converted[0]) <= 1e-9:
                result.extend(converted[1:])
            else:
                result.extend(converted)
        return result
    center = getattr(entity, "center", None)
    radius = getattr(entity, "radius", None)
    if center is not None and radius is not None:
        cx, cy = _as_xy(center)
        radius = float(radius)
        segments = max(24, int(math.ceil(math.tau * radius / max(deflection, 0.01))))
        return [
            (cx + radius * math.cos(math.tau * index / segments),
             cy + radius * math.sin(math.tau * index / segments))
            for index in range(segments)
        ]
    return []


def _distance(a: Point, b: Point) -> float:
    return math.hypot(float(a[0]) - float(b[0]), float(a[1]) - float(b[1]))


def _is_closed(entity: Any, points: Sequence[Point], tolerance: float) -> bool:
    closed = getattr(entity, "closed", None)
    if closed is not None:
        return bool(closed)
    name = entity.__class__.__name__.lower()
    if "circle" in name or "ellipse" in name:
        return True
    return len(points) >= 3 and _distance(points[0], points[-1]) <= tolerance


def _document_entities(document: Any) -> Mapping[str, Any]:
    for attr in ("entities_by_id", "entities"):
        value = getattr(document, attr, None)
        if isinstance(value, Mapping):
            return value
        if value is not None:
            return {
                str(getattr(entity, "id", index)): entity
                for index, entity in enumerate(value)
            }
    return {}


@dataclass(frozen=True)
class ClassifiedLoop:
    entity_id: str
    points: Tuple[Point, ...]
    bounds: Bounds
    area: float
    parent_id: Optional[str]
    depth: int


@dataclass(frozen=True)
class ClassifiedPiece:
    piece_id: str
    outer_id: str
    inner_ids: Tuple[str, ...]
    descendant_ids: Tuple[str, ...]
    bounds: Bounds
    area: float
    outer_points: Tuple[Point, ...] = ()
    feature_ids: Tuple[str, ...] = ()
    marking_ids: Tuple[str, ...] = ()


@dataclass
class ClassificationResult:
    loops: Dict[str, ClassifiedLoop] = field(default_factory=dict)
    pieces: List[ClassifiedPiece] = field(default_factory=list)
    open_entity_ids: List[str] = field(default_factory=list)
    marking_entity_ids: List[str] = field(default_factory=list)
    rejected_entity_ids: List[str] = field(default_factory=list)


def filter_pieces_for_selection(
    pieces: Iterable[ClassifiedPiece],
    selected_entity_ids: Iterable[str],
) -> Tuple[ClassifiedPiece, ...]:
    """Return the rigid pieces owned by an explicit vector selection.

    Selecting any vector that belongs to a piece selects the whole machining
    unit for organization: external contour, descendants, pocket regions and
    open markings.  An empty selection intentionally preserves the historical
    "organize all" command used by existing files and toolbar workflows.
    """

    pieces = tuple(pieces)
    selected = {
        str(entity_id)
        for entity_id in selected_entity_ids
        if entity_id is not None
    }
    if not selected:
        return pieces
    return tuple(
        piece
        for piece in pieces
        if selected.intersection(
            (
                piece.outer_id,
                *piece.descendant_ids,
                *piece.feature_ids,
                *piece.marking_ids,
            )
        )
    )


def classify_document_pieces(
    document: Any,
    tolerance: float = 1e-6,
    deflection: float = 0.05,
    *,
    include_non_cam_layers: bool = False,
) -> ClassificationResult:
    """Classify closed production contours into rigid pieces.

    Layer visibility and purpose are authoritative.  An entity-level
    ``visible`` attribute is intentionally ignored.  Hidden, reference and
    construction layers participate only when ``include_non_cam_layers`` is
    explicitly enabled for diagnostics.
    """

    result = ClassificationResult()
    raw: Dict[str, Tuple[Any, List[Point], Bounds, float]] = {}
    pocket_features: Dict[str, Tuple[Any, List[Point], Bounds, float]] = {}
    open_paths: Dict[str, Tuple[Any, List[Point], Bounds]] = {}
    for entity_id, entity in _document_entities(document).items():
        if entity_is_remnant_cut(entity):
            continue
        if not include_non_cam_layers and not entity_is_cam_eligible(document, entity):
            continue
        points = entity_polyline(entity, deflection)
        if not _is_closed(entity, points, tolerance):
            if points:
                open_paths[str(entity_id)] = (
                    entity,
                    points,
                    polygon_bounds(points),
                )
            continue
        if len(points) > 1 and _distance(points[0], points[-1]) <= tolerance:
            points = points[:-1]
        area = polygon_area(points)
        if len(points) < 3 or abs(area) <= tolerance * tolerance:
            result.rejected_entity_ids.append(str(entity_id))
            continue
        if entity_is_pocket_feature(entity):
            pocket_features[str(entity_id)] = (
                entity,
                points,
                polygon_bounds(points),
                area,
            )
            continue
        raw[str(entity_id)] = (entity, points, polygon_bounds(points), area)

    # Most large imports are already separated into physical panel instances.
    # Keep containment strictly inside that instance (or import batch) before
    # doing the exact polygon test.  The old loop still visited every other
    # contour merely to reject it by metadata, making "Reconhecer peças e
    # furos" quadratic on a cabinet with hundreds of dogbones and drills.
    # Unscoped hand-drawn geometry remains visible to every scope so the
    # existing mixed manual/import workflow keeps its behaviour.
    scopes: Dict[str, Optional[Tuple[str, ...]]] = {}
    scoped_entity_ids: Dict[Tuple[str, ...], List[str]] = {}
    unscoped_entity_ids: List[str] = []
    for entity_id, (entity, _points, _bounds, _area) in raw.items():
        metadata = dict(getattr(entity, "metadata", {}) or {})
        batch = str(metadata.get("import_batch_id", "") or "")
        # PanelNest already supplies an instance ID.  Direct Assembly-tree
        # imports now carry the same physical-piece information under a
        # neutral key, so a cabinet of separate boards is not treated as one
        # giant containment search.
        instance = str(
            metadata.get("panelnest_instance_id", "")
            or metadata.get("source_tree_instance_id", "")
            or ""
        )
        if instance:
            scope = ("physical-instance", batch, instance)
        elif batch:
            scope = ("import-batch", batch)
        else:
            scope = None
        scopes[entity_id] = scope
        if scope is None:
            unscoped_entity_ids.append(entity_id)
        else:
            scoped_entity_ids.setdefault(scope, []).append(entity_id)

    parents: Dict[str, Optional[str]] = {}
    for entity_id, (_entity, points, bounds, area) in raw.items():
        probe = points[0]
        entity_batch = str(
            (getattr(_entity, "metadata", {}) or {}).get("import_batch_id", "")
        )
        entity_instance = str(
            (getattr(_entity, "metadata", {}) or {}).get("panelnest_instance_id", "")
            or (getattr(_entity, "metadata", {}) or {}).get("source_tree_instance_id", "")
            or ""
        )
        containers = []
        scope = scopes[entity_id]
        candidate_ids = (
            tuple(raw)
            if scope is None
            else tuple(scoped_entity_ids.get(scope, ())) + tuple(unscoped_entity_ids)
        )
        for candidate_id in candidate_ids:
            _other, candidate_points, candidate_bounds, candidate_area = raw[candidate_id]
            if candidate_id == entity_id or abs(candidate_area) <= abs(area):
                continue
            candidate_batch = str(
                (getattr(_other, "metadata", {}) or {}).get("import_batch_id", "")
            )
            candidate_instance = str(
                (getattr(_other, "metadata", {}) or {}).get("panelnest_instance_id", "")
                or (getattr(_other, "metadata", {}) or {}).get("source_tree_instance_id", "")
                or ""
            )
            if entity_batch and candidate_batch and entity_batch != candidate_batch:
                continue
            # PanelNest imports label every outer profile, inner recut and
            # circular hole with its physical panel instance.  Comparing
            # containment across all other panels is meaningless and becomes
            # quadratic on a full cabinet.
            if entity_instance and candidate_instance and entity_instance != candidate_instance:
                continue
            if not _bounds_contains(candidate_bounds, bounds, tolerance):
                continue
            if point_in_polygon(probe, candidate_points):
                containers.append((abs(candidate_area), candidate_id))
        parents[entity_id] = min(containers)[1] if containers else None

    def depth_of(entity_id: str) -> int:
        seen = set()
        depth = 0
        current = parents.get(entity_id)
        while current is not None:
            if current in seen:
                raise ValueError("Ciclo inválido na árvore de contenção.")
            seen.add(current)
            depth += 1
            current = parents.get(current)
        return depth

    for entity_id, (_entity, points, bounds, area) in raw.items():
        result.loops[entity_id] = ClassifiedLoop(
            entity_id=entity_id,
            points=tuple(points),
            bounds=bounds,
            area=area,
            parent_id=parents[entity_id],
            depth=depth_of(entity_id),
        )

    roots = [loop for loop in result.loops.values() if loop.parent_id is None]
    markings_by_root: Dict[str, List[str]] = {root.entity_id: [] for root in roots}
    voids_by_root: Dict[str, List[ClassifiedLoop]] = {
        root.entity_id: [] for root in roots
    }
    for loop in result.loops.values():
        if loop.depth % 2 == 1:
            root_id = _root_id(loop.entity_id, parents)
            if root_id in voids_by_root:
                voids_by_root[root_id].append(loop)
    for entity_id, (entity, points, bounds) in open_paths.items():
        metadata = dict(getattr(entity, "metadata", {}) or {})
        open_batch = str(metadata.get("import_batch_id", "") or "")
        open_instance = str(
            metadata.get("panelnest_instance_id", "")
            or metadata.get("source_tree_instance_id", "")
            or ""
        )
        probes = _open_path_probes(points)
        candidates = []
        for root in roots:
            root_entity = raw[root.entity_id][0]
            root_metadata = dict(getattr(root_entity, "metadata", {}) or {})
            root_batch = str(root_metadata.get("import_batch_id", "") or "")
            root_instance = str(
                root_metadata.get("panelnest_instance_id", "")
                or root_metadata.get("source_tree_instance_id", "")
                or ""
            )
            if open_batch and root_batch and open_batch != root_batch:
                continue
            if open_instance and root_instance and open_instance != root_instance:
                continue
            if not _bounds_contains(root.bounds, bounds, tolerance):
                continue
            if not all(
                _point_inside_or_on_boundary(probe, root.points, tolerance)
                for probe in probes
            ):
                continue
            if any(
                _point_inside_or_on_boundary(probe, void.points, tolerance)
                for void in voids_by_root[root.entity_id]
                for probe in probes
            ):
                continue
            candidates.append((abs(root.area), root.entity_id))
        # Ownership must be unambiguous.  A stroke lying on a shared edge or
        # inside overlapping external contours may be a cut, not a marking;
        # keep blocking it instead of silently attaching it to an arbitrary
        # piece.
        if len(candidates) == 1:
            owner_id = candidates[0][1]
            markings_by_root[owner_id].append(entity_id)
            result.marking_entity_ids.append(entity_id)
        else:
            result.open_entity_ids.append(entity_id)

    for index, root in enumerate(sorted(roots, key=lambda item: item.entity_id), start=1):
        descendants = [
            loop for loop in result.loops.values()
            if _root_id(loop.entity_id, parents) == root.entity_id and loop.entity_id != root.entity_id
        ]
        immediate_holes = tuple(
            loop.entity_id for loop in descendants
            if loop.parent_id == root.entity_id and loop.depth % 2 == 1
        )
        root_entity = raw[root.entity_id][0]
        root_owner = pocket_feature_owner_key(root_entity)
        owned_features = []
        for feature_id, (feature, points, bounds, _area) in pocket_features.items():
            feature_owner = pocket_feature_owner_key(feature)
            same_source_component = bool(
                root_owner and feature_owner and root_owner == feature_owner
            )
            center = (
                (bounds[0] + bounds[2]) * 0.5,
                (bounds[1] + bounds[3]) * 0.5,
            )
            geometrically_owned = bool(
                _bounds_contains(root.bounds, bounds, tolerance)
                and point_in_polygon(center, root.points)
            )
            if same_source_component or geometrically_owned:
                owned_features.append(feature_id)
        result.pieces.append(
            ClassifiedPiece(
                piece_id=f"piece-{index:03d}-{root.entity_id}",
                outer_id=root.entity_id,
                inner_ids=immediate_holes,
                descendant_ids=tuple(loop.entity_id for loop in descendants),
                bounds=root.bounds,
                area=abs(root.area),
                outer_points=root.points,
                feature_ids=tuple(sorted(owned_features)),
                marking_ids=tuple(sorted(markings_by_root[root.entity_id])),
            )
        )
    return result


def _root_id(entity_id: str, parents: Mapping[str, Optional[str]]) -> str:
    current = entity_id
    seen = set()
    while parents.get(current) is not None:
        if current in seen:
            break
        seen.add(current)
        current = str(parents[current])
    return current


def _bounds_contains(outer: Bounds, inner: Bounds, tolerance: float) -> bool:
    return (
        outer[0] <= inner[0] + tolerance
        and outer[1] <= inner[1] + tolerance
        and outer[2] >= inner[2] - tolerance
        and outer[3] >= inner[3] - tolerance
    )


@dataclass(frozen=True)
class PiecePlacement:
    piece_id: str
    instance: int
    dx: float
    dy: float
    rotation_degrees: float
    placed_bounds: Bounds
    entity_ids: Tuple[str, ...]
    sheet_index: int = 0


def organization_placement_transform(
    piece: ClassifiedPiece,
    placement: PiecePlacement,
) -> Affine2D:
    """Return the exact rigid transform represented by one placement.

    ``dx``/``dy`` are already calculated from the rotated *real contour* by
    the packer.  Recomputing them from the old axis-aligned bounding-box
    corners makes the UI project a different pose from the one validated by
    the organizer, especially for long diagonal rails.
    """

    min_x, min_y, max_x, max_y = map(float, piece.bounds)
    center = Vec2((min_x + max_x) * 0.5, (min_y + max_y) * 0.5)
    rotation = Affine2D.rotation(
        math.radians(float(placement.rotation_degrees or 0.0)),
        center,
    )
    return Affine2D.translation(
        Vec2(float(placement.dx), float(placement.dy))
    ) @ rotation


@dataclass
class OrganizationResult:
    placements: List[PiecePlacement] = field(default_factory=list)
    unplaced_piece_ids: List[str] = field(default_factory=list)
    used_bounds: Optional[Bounds] = None
    sheet_bounds: Tuple[Bounds, ...] = ()
    strategy: str = ""
    evaluated_layouts: int = 0
    placed_area: float = 0.0
    sheet_area: float = 0.0
    utilization_percent: float = 0.0
    scrap_fragmentation_score: float = 0.0


def organization_with_stationary_pieces(pieces, result):
    """Include unmoved sheet occupants for clearance and remnant validation."""
    placed_ids = {value.piece_id for value in result.placements}
    stationary = [
        PiecePlacement(
            piece_id=piece.piece_id, instance=0, dx=0.0, dy=0.0,
            rotation_degrees=0.0, placed_bounds=piece.bounds,
            entity_ids=(piece.outer_id,) + tuple(piece.descendant_ids)
            + tuple(piece.feature_ids) + tuple(piece.marking_ids),
        )
        for piece in pieces if piece.piece_id not in placed_ids
    ]
    return replace(result, placements=list(result.placements) + stationary)


@dataclass(frozen=True)
class RectangularRemnantCut:
    """One safe guillotine line that isolates a rectangular reusable remnant."""

    sheet_index: int
    start: Point
    end: Point
    remnant_bounds: Bounds
    area: float
    remnant_id: str = ""
    show_label: bool = True


def suggest_rectangular_remnant_cuts(
    result: OrganizationResult,
    *,
    minimum_short_side: float = 100.0,
    clearance: float = 2.0,
) -> Tuple[RectangularRemnantCut, ...]:
    """Decompose safe edge waste into storeable rectangular remnants.

    Each iteration isolates the largest safe edge strip and repeats inside the
    occupied remainder.  Thus an L-shaped leftover around a bottom-left nest
    becomes two useful rectangles without any suggested line crossing a part.
    """

    minimum_short_side = max(0.0, float(minimum_short_side))
    clearance = max(0.0, float(clearance))
    suggestions = []
    tolerance = 1.0e-7

    for sheet_index, sheet in enumerate(tuple(result.sheet_bounds or ())):
        sx0, sy0, sx1, sy1 = map(float, sheet)
        placements = tuple(
            placement
            for placement in result.placements
            if int(placement.sheet_index) == sheet_index
        )
        if not placements or sx1 <= sx0 or sy1 <= sy0:
            continue

        obstacles = []
        x_coordinates = {sx0, sx1}
        y_coordinates = {sy0, sy1}
        for placement in placements:
            px0, py0, px1, py1 = map(float, placement.placed_bounds)
            obstacle = (
                max(sx0, px0 - clearance),
                max(sy0, py0 - clearance),
                min(sx1, px1 + clearance),
                min(sy1, py1 + clearance),
            )
            if obstacle[2] <= obstacle[0] or obstacle[3] <= obstacle[1]:
                continue
            obstacles.append(obstacle)
            x_coordinates.update((obstacle[0], obstacle[2]))
            y_coordinates.update((obstacle[1], obstacle[3]))

        xs = sorted(x_coordinates)
        ys = sorted(y_coordinates)
        column_count = len(xs) - 1
        row_count = len(ys) - 1
        if column_count <= 0 or row_count <= 0:
            continue
        occupied = [
            [False for _column in range(column_count)]
            for _row in range(row_count)
        ]
        for row in range(row_count):
            centre_y = (ys[row] + ys[row + 1]) * 0.5
            for column in range(column_count):
                centre_x = (xs[column] + xs[column + 1]) * 0.5
                occupied[row][column] = any(
                    ox0 - tolerance <= centre_x <= ox1 + tolerance
                    and oy0 - tolerance <= centre_y <= oy1 + tolerance
                    for ox0, oy0, ox1, oy1 in obstacles
                )

        existing_cut_segments = []

        def segment_is_covered(start, end):
            horizontal = abs(start[1] - end[1]) <= tolerance
            for old_start, old_end in existing_cut_segments:
                old_horizontal = abs(old_start[1] - old_end[1]) <= tolerance
                if horizontal != old_horizontal:
                    continue
                if horizontal:
                    if abs(start[1] - old_start[1]) > tolerance:
                        continue
                    new_min, new_max = sorted((start[0], end[0]))
                    old_min, old_max = sorted((old_start[0], old_end[0]))
                else:
                    if abs(start[0] - old_start[0]) > tolerance:
                        continue
                    new_min, new_max = sorted((start[1], end[1]))
                    old_min, old_max = sorted((old_start[1], old_end[1]))
                if old_min <= new_min + tolerance and old_max >= new_max - tolerance:
                    return True
            return False

        def required_cuts(bounds):
            rx0, ry0, rx1, ry1 = bounds
            sides = (
                ((rx0, ry0), (rx1, ry0), abs(ry0 - sy0) <= tolerance),
                ((rx1, ry0), (rx1, ry1), abs(rx1 - sx1) <= tolerance),
                ((rx0, ry1), (rx1, ry1), abs(ry1 - sy1) <= tolerance),
                ((rx0, ry0), (rx0, ry1), abs(rx0 - sx0) <= tolerance),
            )
            return tuple(
                (start, end)
                for start, end, is_sheet_edge in sides
                if not is_sheet_edge and not segment_is_covered(start, end)
            )

        def largest_empty_rectangle():
            heights = [0.0] * column_count
            best = None
            for row in range(row_count):
                row_height = ys[row + 1] - ys[row]
                for column in range(column_count):
                    heights[column] = (
                        0.0
                        if occupied[row][column]
                        else heights[column] + row_height
                    )
                stack = []
                for column in range(column_count + 1):
                    height = heights[column] if column < column_count else 0.0
                    start_column = column
                    while stack and stack[-1][1] > height + tolerance:
                        left_column, previous_height = stack.pop()
                        start_column = left_column
                        width = xs[column] - xs[left_column]
                        if (
                            width <= tolerance
                            or previous_height <= tolerance
                            or min(width, previous_height) + tolerance
                            < minimum_short_side
                        ):
                            continue
                        bounds = (
                            xs[left_column],
                            ys[row + 1] - previous_height,
                            xs[column],
                            ys[row + 1],
                        )
                        cuts = required_cuts(bounds)
                        if not cuts:
                            continue
                        area = width * previous_height
                        cut_length = sum(
                            math.hypot(
                                end[0] - start[0], end[1] - start[1]
                            )
                            for start, end in cuts
                        )
                        # A slightly smaller rectangle isolated by one clean
                        # guillotine cut is normally more useful than a
                        # marginally larger pocket requiring two or three
                        # extra cuts. Area remains dominant for material
                        # differences larger than that practical penalty.
                        practical_area = area / (
                            1.0 + 0.30 * max(0, len(cuts) - 1)
                        )
                        score = (
                            -practical_area,
                            -area,
                            len(cuts),
                            cut_length,
                            -bounds[3],
                            bounds[0],
                        )
                        candidate = (score, bounds, cuts, area)
                        if best is None or score < best[0]:
                            best = candidate
                    if not stack or stack[-1][1] < height - tolerance:
                        stack.append((start_column, height))
            return best

        for remnant_index in range(8):
            candidate = largest_empty_rectangle()
            if candidate is None:
                break
            _score, bounds, cuts, area = candidate
            rx0, ry0, rx1, ry1 = bounds
            remnant_id = "sheet-%d-remnant-%d" % (
                sheet_index,
                remnant_index + 1,
            )
            for cut_index, (start, end) in enumerate(cuts):
                suggestions.append(
                    RectangularRemnantCut(
                        sheet_index=sheet_index,
                        start=tuple(map(float, start)),
                        end=tuple(map(float, end)),
                        remnant_bounds=tuple(map(float, bounds)),
                        area=float(area),
                        remnant_id=remnant_id,
                        show_label=cut_index == 0,
                    )
                )
                existing_cut_segments.append((start, end))
            # Reserve the accepted rectangle before looking for another one,
            # so overlapping maximal rectangles decompose an L instead of
            # reporting the same material twice.
            for row in range(row_count):
                if ys[row] < ry0 - tolerance or ys[row + 1] > ry1 + tolerance:
                    continue
                for column in range(column_count):
                    if xs[column] < rx0 - tolerance or xs[column + 1] > rx1 + tolerance:
                        continue
                    occupied[row][column] = True

    return tuple(suggestions)


@dataclass(frozen=True)
class _PackInstance:
    piece: ClassifiedPiece
    instance: int
    source_width: float
    source_height: float

    @property
    def source_area(self) -> float:
        return self.source_width * self.source_height

    @property
    def identity(self) -> Tuple[str, int]:
        return self.piece.piece_id, self.instance


@dataclass(frozen=True)
class _RotationOption:
    angle: float
    width: float
    height: float
    rotated_min_x: float
    rotated_min_y: float


@dataclass(frozen=True)
class _PackedBox:
    item: _PackInstance
    option: _RotationOption
    bounds: Bounds


def _rotation_options(
    piece: ClassifiedPiece,
    allowed: Sequence[float],
) -> Tuple[_RotationOption, ...]:
    min_x, min_y, max_x, max_y = map(float, piece.bounds)
    if (
        not all(math.isfinite(value) for value in (min_x, min_y, max_x, max_y))
        or max_x <= min_x
        or max_y <= min_y
    ):
        return ()
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    source_points = tuple(
        (float(x_value), float(y_value))
        for x_value, y_value in piece.outer_points
    )
    if len(source_points) < 3:
        source_points = (
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        )
    normalized = []
    for raw_angle in allowed or (0.0,):
        angle = float(raw_angle) % 360.0
        if not math.isfinite(angle):
            raise ValueError("As rotações permitidas precisam ser finitas.")
        if abs(angle - 360.0) <= 1e-9 or abs(angle) <= 1e-9:
            angle = 0.0
        if not any(abs(angle - existing) <= 1e-9 for existing in normalized):
            normalized.append(angle)
    normalized.sort()

    result = []
    for angle in normalized:
        radians = math.radians(angle)
        cosine, sine = math.cos(radians), math.sin(radians)
        rotated = []
        for x_value, y_value in source_points:
            offset_x, offset_y = x_value - center_x, y_value - center_y
            rotated.append(
                (
                    center_x + cosine * offset_x - sine * offset_y,
                    center_y + sine * offset_x + cosine * offset_y,
                )
            )
        rotated_min_x = min(point[0] for point in rotated)
        rotated_min_y = min(point[1] for point in rotated)
        rotated_max_x = max(point[0] for point in rotated)
        rotated_max_y = max(point[1] for point in rotated)
        result.append(
            _RotationOption(
                angle=angle,
                width=rotated_max_x - rotated_min_x,
                height=rotated_max_y - rotated_min_y,
                rotated_min_x=rotated_min_x,
                rotated_min_y=rotated_min_y,
            )
        )
    if len(result) <= 8 or len(piece.outer_points) < 3:
        return tuple(result)

    # A blind 15-degree sweep multiplies every raster state by 24 even for a
    # rectangle. Keep the four orthogonal poses plus the orientations whose
    # *real contour* has the smallest bounding footprint. Long diagonal rails
    # therefore retain the angle that makes them horizontal, while the first
    # progressive preview remains fast enough to be useful.
    selected = []
    for cardinal in (0.0, 90.0, 180.0, 270.0):
        option = min(
            result,
            key=lambda value: (
                min(
                    abs(value.angle - cardinal),
                    360.0 - abs(value.angle - cardinal),
                ),
                value.angle,
            ),
        )
        if option not in selected:
            selected.append(option)
    for option in sorted(
        result,
        key=lambda value: (
            round(value.width * value.height, 12),
            round(min(value.width, value.height), 12),
            round(max(value.width, value.height), 12),
            round(value.angle, 12),
        ),
    ):
        if option not in selected:
            selected.append(option)
        if len(selected) >= 8:
            break
    return tuple(sorted(selected, key=lambda value: value.angle))


def _bounds_union(values: Sequence[Bounds]) -> Optional[Bounds]:
    if not values:
        return None
    return (
        min(value[0] for value in values),
        min(value[1] for value in values),
        max(value[2] for value in values),
        max(value[3] for value in values),
    )


def _rectangles_intersect(left: Bounds, right: Bounds) -> bool:
    tolerance = 1e-9
    return not (
        left[2] <= right[0] + tolerance
        or right[2] <= left[0] + tolerance
        or left[3] <= right[1] + tolerance
        or right[3] <= left[1] + tolerance
    )


def _prune_free_rectangles(rectangles: Iterable[Bounds]) -> List[Bounds]:
    tolerance = 1e-9
    valid = sorted(
        {
            tuple(float(value) for value in rectangle)
            for rectangle in rectangles
            if rectangle[2] - rectangle[0] > tolerance
            and rectangle[3] - rectangle[1] > tolerance
        },
        key=lambda value: (value[1], value[0], value[3], value[2]),
    )
    result = []
    for index, rectangle in enumerate(valid):
        contained = False
        for other_index, other in enumerate(valid):
            if index == other_index:
                continue
            if (
                other[0] <= rectangle[0] + tolerance
                and other[1] <= rectangle[1] + tolerance
                and other[2] >= rectangle[2] - tolerance
                and other[3] >= rectangle[3] - tolerance
            ):
                contained = True
                break
        if not contained:
            result.append(rectangle)
    return result


def _subtract_occupied_from_free(
    free_rectangles: Sequence[Bounds],
    occupied: Bounds,
) -> List[Bounds]:
    split = []
    for free in free_rectangles:
        if not _rectangles_intersect(free, occupied):
            split.append(free)
            continue
        if occupied[0] > free[0] + 1e-9:
            split.append((free[0], free[1], occupied[0], free[3]))
        if occupied[2] < free[2] - 1e-9:
            split.append((occupied[2], free[1], free[2], free[3]))
        if occupied[1] > free[1] + 1e-9:
            split.append((free[0], free[1], free[2], occupied[1]))
        if occupied[3] < free[3] - 1e-9:
            split.append((free[0], occupied[3], free[2], free[3]))
    return _prune_free_rectangles(split)


def _layout_signature(boxes: Sequence[_PackedBox]) -> Tuple:
    return tuple(
        sorted(
            (
                box.item.piece.piece_id,
                box.item.instance,
                round(box.bounds[0], 9),
                round(box.bounds[1], 9),
                round(box.option.angle, 9),
            )
            for box in boxes
        )
    )


def _pack_bottom_left(
    ordered: Sequence[_PackInstance],
    work_bounds: Bounds,
    spacing: float,
    rotations: Mapping[str, Sequence[float]],
    stop_requested: Optional[Callable[[], bool]] = None,
    heuristic: str = "bottom_left",
) -> Tuple[List[_PackedBox], List[_PackInstance], Optional[Bounds]]:
    min_x, min_y, max_x, max_y = work_bounds
    usable_min_x = min_x + spacing
    usable_min_y = min_y + spacing
    packed: List[_PackedBox] = []
    unplaced: List[_PackInstance] = []
    # A packed footprint includes its required top/right spacing.  Starting
    # the free rectangle after the left/bottom spacing yields exactly one
    # spacing margin on all four work-area borders.
    free_rectangles: List[Bounds] = []
    if usable_min_x < max_x - 1e-9 and usable_min_y < max_y - 1e-9:
        free_rectangles.append((usable_min_x, usable_min_y, max_x, max_y))

    for item in ordered:
        _check_search_cancelled(stop_requested)
        options = _rotation_options(
            item.piece,
            rotations.get(item.piece.piece_id, (0.0, 90.0)),
        )
        best = None
        for option in options:
            footprint_width = option.width + spacing
            footprint_height = option.height + spacing
            for free in free_rectangles:
                if (
                    footprint_width > free[2] - free[0] + 1e-9
                    or footprint_height > free[3] - free[1] + 1e-9
                ):
                    continue
                x_value, y_value = free[0], free[1]
                candidate = (
                    x_value,
                    y_value,
                    x_value + option.width,
                    y_value + option.height,
                )
                used = _bounds_union(
                    [existing.bounds for existing in packed] + [candidate]
                )
                used_width = used[2] - used[0]
                used_height = used[3] - used[1]
                free_waste = (
                    (free[2] - free[0]) * (free[3] - free[1])
                    - footprint_width * footprint_height
                )
                leftover_width = free[2] - free[0] - footprint_width
                leftover_height = free[3] - free[1] - footprint_height
                short_side = min(leftover_width, leftover_height)
                long_side = max(leftover_width, leftover_height)
                global_score = (
                    round(used_height, 12),
                    round(used_width * used_height, 12),
                    round(used_width, 12),
                )
                tail = (
                    round(y_value, 12),
                    round(x_value, 12),
                    round(option.angle, 12),
                )
                if heuristic == "best_short_side":
                    score = (
                        round(short_side, 12),
                        round(long_side, 12),
                        round(free_waste, 12),
                    ) + global_score + tail
                elif heuristic == "best_area":
                    score = (
                        round(free_waste, 12),
                        round(short_side, 12),
                        round(long_side, 12),
                    ) + global_score + tail
                elif heuristic == "global_area":
                    score = (
                        round(used_width * used_height, 12),
                        round(used_height, 12),
                        round(used_width, 12),
                        round(free_waste, 12),
                    ) + tail
                else:
                    score = global_score + (
                        round(free_waste, 12),
                    ) + tail
                if best is None or score < best[0]:
                    best = (score, option, candidate)
        if best is None:
            unplaced.append(item)
            continue
        _score, option, candidate = best
        packed.append(_PackedBox(item, option, candidate))
        occupied = (
            candidate[0],
            candidate[1],
            candidate[2] + spacing,
            candidate[3] + spacing,
        )
        free_rectangles = _subtract_occupied_from_free(
            free_rectangles,
            occupied,
        )

    return packed, unplaced, _bounds_union([box.bounds for box in packed])


def _bounds_have_spacing(first: Bounds, second: Bounds, spacing: float) -> bool:
    """Return whether two axis-aligned footprints keep the requested gap."""

    tolerance = 1.0e-9
    return (
        first[2] + spacing <= second[0] + tolerance
        or second[2] + spacing <= first[0] + tolerance
        or first[3] + spacing <= second[1] + tolerance
        or second[3] + spacing <= first[1] + tolerance
    )


def _pack_corner_points(
    ordered: Sequence[_PackInstance],
    work_bounds: Bounds,
    spacing: float,
    rotations: Mapping[str, Sequence[float]],
    stop_requested: Optional[Callable[[], bool]] = None,
) -> Tuple[List[_PackedBox], List[_PackInstance], Optional[Bounds]]:
    """Pack on the Cartesian frontier of already placed footprints.

    MaxRects represents free space by split rectangles.  That is fast, but a
    split can hide a valid ``right edge × top edge`` corner assembled from two
    different neighbours.  This complementary bottom-left generator tests
    those frontier intersections directly.  It remains deterministic and
    works only with preview footprints; the exact contour gate still decides
    whether the resulting layout may be applied.
    """

    min_x, min_y, max_x, max_y = map(float, work_bounds)
    usable_min_x = min_x + spacing
    usable_min_y = min_y + spacing
    usable_max_x = max_x - spacing
    usable_max_y = max_y - spacing
    packed: List[_PackedBox] = []
    unplaced: List[_PackInstance] = []

    for item_index, item in enumerate(ordered):
        if item_index % 4 == 0:
            _check_search_cancelled(stop_requested)
        x_candidates = {usable_min_x}
        y_candidates = {usable_min_y}
        for existing in packed:
            x_candidates.add(existing.bounds[2] + spacing)
            y_candidates.add(existing.bounds[3] + spacing)
        best = None
        for option in _rotation_options(
            item.piece,
            rotations.get(item.piece.piece_id, (0.0, 90.0)),
        ):
            for y_value in sorted(y_candidates):
                if y_value + option.height > usable_max_y + 1.0e-9:
                    continue
                for x_value in sorted(x_candidates):
                    if x_value + option.width > usable_max_x + 1.0e-9:
                        continue
                    candidate = (
                        x_value,
                        y_value,
                        x_value + option.width,
                        y_value + option.height,
                    )
                    if any(
                        not _bounds_have_spacing(candidate, value.bounds, spacing)
                        for value in packed
                    ):
                        continue
                    used = _bounds_union(
                        [value.bounds for value in packed] + [candidate]
                    )
                    used_width = used[2] - used[0]
                    used_height = used[3] - used[1]
                    score = (
                        round(used_height, 12),
                        round(used_width * used_height, 12),
                        round(used_width, 12),
                        round(y_value, 12),
                        round(x_value, 12),
                        round(option.angle, 12),
                    )
                    if best is None or score < best[0]:
                        best = (score, option, candidate)
        if best is None:
            unplaced.append(item)
            continue
        _score, option, candidate = best
        packed.append(_PackedBox(item, option, candidate))

    return packed, unplaced, _bounds_union([box.bounds for box in packed])


_SEARCH_ORDER_BUDGETS = {
    # rectangular orders, raster orders, beam width, positions/orientation
    "fast": (4, 2, 1, 1),
    "balanced": (12, 4, 2, 2),
    "thorough": (24, 6, 4, 3),
}


@lru_cache(maxsize=4096)
def _stable_order_geometry_payload(item: _PackInstance) -> str:
    """Cache the translation-independent part of exploratory order hashes."""

    min_x, min_y = float(item.piece.bounds[0]), float(item.piece.bounds[1])
    normalized_points = tuple(
        sorted(
            (
                round(float(x_value) - min_x, 6),
                round(float(y_value) - min_y, 6),
            )
            for x_value, y_value in item.piece.outer_points
        )
    )
    # Base exploratory permutations on normalized geometry, not UUIDs. DXF
    # imports receive fresh entity IDs, and letting those random identifiers
    # choose the layout made the same cabinet nest differently after a clean
    # import. Identical shapes intentionally tie; their final ID/instance tie
    # break is geometrically irrelevant and keeps the signature stable.
    return "%.6f|%.6f|%.6f|%r|%d" % (
        item.source_width,
        item.source_height,
        float(item.piece.area),
        normalized_points,
        item.instance,
    )


def _stable_order_hash(item: _PackInstance, salt: int) -> int:
    """Return a process-independent hash for reproducible search orders."""

    value = 1469598103934665603
    payload = "%d|%s" % (salt, _stable_order_geometry_payload(item))
    for byte in payload.encode("utf-8"):
        value ^= byte
        value = (value * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return value


def _candidate_orders(
    instances: Sequence[_PackInstance],
    max_orders: int = 4,
) -> Tuple[Tuple[_PackInstance, ...], ...]:
    key_suffix = lambda item: (item.piece.piece_id, item.instance)
    sort_keys = (
        lambda item: (-item.source_area, -max(item.source_width, item.source_height), *key_suffix(item)),
        lambda item: (-max(item.source_width, item.source_height), -item.source_area, *key_suffix(item)),
        lambda item: (-item.source_height, -item.source_width, -item.source_area, *key_suffix(item)),
        lambda item: (-item.source_width, -item.source_height, -item.source_area, *key_suffix(item)),
    )
    result = []
    identities = set()
    for sort_key in sort_keys:
        ordered = tuple(sorted(instances, key=sort_key))
        identity = tuple(item.identity for item in ordered)
        if identity not in identities:
            identities.add(identity)
            result.append(ordered)
    # The first four candidates preserve the established heuristics. Extra
    # candidates explore stable permutations, avoiding random/time-based
    # results that would change between PCs or make Undo previews irrepeatable.
    salt = 1
    max_orders = max(1, int(max_orders))
    while len(result) < max_orders and salt <= max_orders * 4:
        ordered = tuple(
            sorted(
                instances,
                key=lambda item: (
                    _stable_order_hash(item, salt),
                    item.piece.piece_id,
                    item.instance,
                ),
            )
        )
        identity = tuple(item.identity for item in ordered)
        if identity not in identities:
            identities.add(identity)
            result.append(ordered)
        salt += 1
    return tuple(result[:max_orders])


def _organize_pieces_rectangular(
    pieces: Iterable[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float = 10.0,
    quantities: Optional[Mapping[str, int]] = None,
    rotations: Optional[Mapping[str, Sequence[float]]] = None,
    max_orders: int = 4,
    stop_requested: Optional[Callable[[], bool]] = None,
    prefer_remnants: bool = False,
) -> OrganizationResult:
    """Return a deterministic compact bottom-left placement preview.

    Each classified piece is a rigid unit: the outer contour and every nested
    contour receive the same placement.  Candidates are generated from the
    bottom/left work-area boundary and the top/right edges of already placed
    boxes.  Every permitted rotation is compared at every candidate.  Several
    deterministic size orders are evaluated and the result that places the
    most material with the lowest used height/area is returned. Material area
    precedes raw piece count: otherwise a first sheet full of many small parts
    can strand large parts on one extra sheet each during progressive packing.

    This function is pure.  It does not mutate geometry or the document; the
    caller remains responsible for turning an accepted preview into one Undo.
    """
    min_x, min_y, max_x, max_y = map(float, work_bounds)
    if not all(math.isfinite(value) for value in (min_x, min_y, max_x, max_y)):
        raise ValueError("A área de Trabalho precisa ter limites finitos.")
    if max_x <= min_x or max_y <= min_y:
        raise ValueError("A área de Trabalho precisa ter largura e altura positivas.")
    spacing = max(0.0, float(spacing))
    if not math.isfinite(spacing):
        raise ValueError("O espaçamento precisa ser finito.")
    quantities = quantities or {}
    rotations = rotations or {}
    instances: List[_PackInstance] = []
    for piece in pieces:
        quantity = max(1, int(quantities.get(piece.piece_id, 1)))
        width = float(piece.bounds[2]) - float(piece.bounds[0])
        height = float(piece.bounds[3]) - float(piece.bounds[1])
        if not math.isfinite(width) or not math.isfinite(height) or width <= 0.0 or height <= 0.0:
            # Invalid/degenerate classified pieces are conservatively reported
            # as unplaced by the normal packing result.
            width = 0.0
            height = 0.0
        for instance in range(1, quantity + 1):
            instances.append(_PackInstance(piece, instance, width, height))

    if not instances:
        return OrganizationResult()

    layouts = []
    orders = _candidate_orders(instances, max_orders=max_orders)
    heuristics = (
        "bottom_left",
        "best_short_side",
        "best_area",
        "global_area",
    )
    trials = []
    # Preserve the four established size-order previews first. Deeper budgets
    # then compare standard MaxRects placement rules before spending the rest
    # on deterministic order permutations. This adds global layout diversity
    # without randomness or a second geometry model.
    established_count = min(4, len(orders), max(1, int(max_orders)))
    for ordered in orders[:established_count]:
        trials.append((ordered, "bottom_left"))
    for heuristic in heuristics[1:]:
        for ordered in orders[:established_count]:
            if len(trials) >= max_orders:
                break
            trials.append((ordered, heuristic))
    next_order = established_count
    while len(trials) < max_orders and next_order < len(orders):
        trials.append(
            (
                orders[next_order],
                heuristics[(next_order - established_count) % len(heuristics)],
            )
        )
        next_order += 1

    for ordered, heuristic in trials:
        _check_search_cancelled(stop_requested)
        packed, unplaced, used = _pack_bottom_left(
            ordered,
            (min_x, min_y, max_x, max_y),
            spacing,
            rotations,
            stop_requested,
            heuristic,
        )
        used_width = 0.0 if used is None else used[2] - used[0]
        used_height = 0.0 if used is None else used[3] - used[1]
        placed_source_area = sum(box.item.source_area for box in packed)
        scrap_fragmentation = _layout_scrap_fragmentation_score(
            (
                (box.item.piece, box.option.angle, box.bounds)
                for box in packed
            ),
            used,
        )
        score = (
            -round(placed_source_area, 12),
            len(unplaced),
            round(used_height, 12),
            round(used_width * used_height, 12),
            round(used_width, 12),
            round(scrap_fragmentation, 12),
            _layout_signature(packed),
        )
        layouts.append((score, packed, unplaced, used, "MaxRects"))

    # The frontier method is intentionally evaluated for every order, not
    # only as a fallback. On the production cabinet set it exposes a compact
    # row assembled from corners belonging to different MaxRects splits.
    for ordered in orders:
        _check_search_cancelled(stop_requested)
        packed, unplaced, used = _pack_corner_points(
            ordered,
            (min_x, min_y, max_x, max_y),
            spacing,
            rotations,
            stop_requested,
        )
        used_width = 0.0 if used is None else used[2] - used[0]
        used_height = 0.0 if used is None else used[3] - used[1]
        placed_source_area = sum(box.item.source_area for box in packed)
        scrap_fragmentation = _layout_scrap_fragmentation_score(
            (
                (box.item.piece, box.option.angle, box.bounds)
                for box in packed
            ),
            used,
        )
        score = (
            -round(placed_source_area, 12),
            len(unplaced),
            round(used_height, 12),
            round(used_width * used_height, 12),
            round(used_width, 12),
            round(scrap_fragmentation, 12),
            _layout_signature(packed),
        )
        layouts.append((score, packed, unplaced, used, "Pontos de fronteira"))

    def layout_score(value):
        if not prefer_remnants:
            return value[0]
        _score, boxes, unplaced_items, _used, _strategy = value
        preview = OrganizationResult(
            placements=[PiecePlacement(box.item.piece.piece_id, box.item.instance,
                                       0.0, 0.0, box.option.angle, box.bounds, ())
                        for box in boxes],
            unplaced_piece_ids=[item.piece.piece_id for item in unplaced_items],
            sheet_bounds=(tuple(map(float, work_bounds)),),
        )
        return organization_remnant_score(preview, clearance=max(1.0, spacing * 0.5)) + (_score,)

    _score, packed, unplaced, used, strategy = min(layouts, key=layout_score)
    result = OrganizationResult(
        unplaced_piece_ids=[item.piece.piece_id for item in unplaced],
        used_bounds=used,
        strategy=strategy,
        evaluated_layouts=len(layouts),
        scrap_fragmentation_score=_layout_scrap_fragmentation_score(
            (
                (box.item.piece, box.option.angle, box.bounds)
                for box in packed
            ),
            used,
        ),
    )
    for box in packed:
        piece = box.item.piece
        translation_x = box.bounds[0] - box.option.rotated_min_x
        translation_y = box.bounds[1] - box.option.rotated_min_y
        result.placements.append(
            PiecePlacement(
                piece_id=piece.piece_id,
                instance=box.item.instance,
                dx=translation_x,
                dy=translation_y,
                rotation_degrees=box.option.angle,
                placed_bounds=box.bounds,
                entity_ids=(
                    (piece.outer_id,)
                    + piece.descendant_ids
                    + piece.feature_ids
                    + piece.marking_ids
                ),
            )
        )
    return result


@dataclass(frozen=True)
class _RasterOrientation:
    angle: float
    row_bits: Tuple[int, ...]
    rows: int
    cols: int
    area_pixels: int
    exact_width: float
    exact_height: float
    rotated_min_x: float
    rotated_min_y: float
    resolution: float


@dataclass(frozen=True)
class _RasterPacked:
    item: _PackInstance
    orientation: _RasterOrientation
    row: int
    col: int
    bounds: Bounds


@dataclass(frozen=True)
class _RasterBeamState:
    sheet_bits: Tuple[int, ...]
    packed: Tuple[_RasterPacked, ...] = ()
    unplaced: Tuple[_PackInstance, ...] = ()
    contact_pixels: int = 0


def _raster_resolution(
    pieces: Sequence[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float,
) -> float:
    width = float(work_bounds[2]) - float(work_bounds[0])
    height = float(work_bounds[3]) - float(work_bounds[1])
    target_long_axis = 700.0 if len(pieces) > 30 else 900.0
    resolution = max(0.5, max(width, height) / target_long_axis)
    if spacing > 0.0:
        resolution = min(resolution, max(0.5, spacing * 0.5))
    if len(pieces) <= 30:
        # A grade orientada apenas pelo maior lado ficou grosseira demais em
        # chapas baixas/compridas. No caso real de suportes de 80 mm em uma
        # faixa 1780 x 410, células de quase 2 mm aceitavam falsos encaixes no
        # raster; a validação vetorial então descartava a solução inteira e o
        # usuário recebia o fallback retangular, sem diagonal com diagonal.
        #
        # Use 1 mm quando a área da grade comportar isso. Em chapas grandes o
        # piso calculado pelo orçamento mantém a busca abaixo do limite já
        # aplicado por ``_organize_pieces_raster``. Isso melhora precisão sem
        # espalhar tolerâncias nem enfraquecer a validação vetorial final.
        maximum_precision_cells = 1350000.0
        cell_limited_resolution = math.sqrt(
            max(0.0, width * height) / maximum_precision_cells
        )
        precision_resolution = max(1.0, cell_limited_resolution)
        resolution = min(resolution, precision_resolution)
    # Small clearance must not silently disable contour packing on a full
    # production sheet. Respect the raster cell budget by coarsening the
    # candidate grid; exact vector clearance remains the acceptance gate.
    return max(resolution, math.sqrt(max(0.0, width * height) / 1350000.0))


def _bounded_raster_resolution(pieces, work_bounds, spacing, rotations):
    """Fit both the sheet and all allowed masks before allocating bitmaps."""
    resolution = _raster_resolution(pieces, work_bounds, spacing)
    options = tuple(
        option for piece in pieces
        for option in _rotation_options(piece, rotations.get(piece.piece_id, (0.0, 90.0)))
    )
    while True:
        cells = sum(math.ceil(option.width / resolution) * math.ceil(option.height / resolution)
                    for option in options)
        if cells <= 12000000:
            return resolution
        resolution *= math.sqrt(cells / 10000000.0)


def _rotate_outer_points(
    piece: ClassifiedPiece,
    angle: float,
) -> Tuple[Tuple[Point, ...], _RotationOption]:
    option = next(
        value
        for value in _rotation_options(piece, (angle,))
        if abs(value.angle - (float(angle) % 360.0)) <= 1e-9
        or (value.angle == 0.0 and abs(float(angle) % 360.0) <= 1e-9)
    )
    min_x, min_y, max_x, max_y = map(float, piece.bounds)
    center_x = (min_x + max_x) * 0.5
    center_y = (min_y + max_y) * 0.5
    radians = math.radians(option.angle)
    cosine, sine = math.cos(radians), math.sin(radians)
    points = []
    for x_value, y_value in piece.outer_points:
        offset_x = float(x_value) - center_x
        offset_y = float(y_value) - center_y
        rotated_x = center_x + cosine * offset_x - sine * offset_y
        rotated_y = center_y + sine * offset_x + cosine * offset_y
        points.append(
            (
                rotated_x - option.rotated_min_x,
                rotated_y - option.rotated_min_y,
            )
        )
    if len(points) > 1 and _distance(points[0], points[-1]) <= 1e-9:
        points.pop()
    return tuple(points), option


def _layout_scrap_fragmentation_score(records, used_bounds) -> float:
    """Prefer consolidated offcut near the layout boundary on exact ties.

    Sheet count and occupied envelope remain the primary objectives.  When
    those are identical, minimizing the material's polar area moment about the
    envelope centre moves concavities toward the outside instead of trapping
    several small scraps between pieces.  The calculation uses exact contour
    vertices; raster cells remain only candidate generators.
    """

    if used_bounds is None:
        return 0.0
    center_x = (float(used_bounds[0]) + float(used_bounds[2])) * 0.5
    center_y = (float(used_bounds[1]) + float(used_bounds[3])) * 0.5
    total = 0.0
    for piece, angle, placed_bounds in records:
        points, _option = _rotate_outer_points(piece, angle)
        if len(points) < 3:
            continue
        translated = tuple(
            (
                float(placed_bounds[0]) + float(x_value) - center_x,
                float(placed_bounds[1]) + float(y_value) - center_y,
            )
            for x_value, y_value in points
        )
        signed_moment = 0.0
        for index, (x_start, y_start) in enumerate(translated):
            x_end, y_end = translated[(index + 1) % len(translated)]
            cross = x_start * y_end - x_end * y_start
            signed_moment += cross * (
                x_start * x_start
                + x_start * x_end
                + x_end * x_end
                + y_start * y_start
                + y_start * y_end
                + y_end * y_end
            )
        total += abs(signed_moment) / 12.0
    return total


def _rasterize_polygon_bits(
    points: Sequence[Point],
    width: float,
    height: float,
    resolution: float,
    stop_requested: Optional[Callable[[], bool]] = None,
) -> Tuple[Tuple[int, ...], int, int, int]:
    """Rasterize a real outer contour into integer row bitmasks.

    Scanline filling handles concavity; sampled boundary cells are also marked
    so thin/diagonal edges cannot disappear between pixel centres.
    """

    if len(points) < 3 or width <= 0.0 or height <= 0.0:
        return (), 0, 0, 0
    cols = max(1, int(math.ceil(width / resolution - 1e-12)))
    rows = max(1, int(math.ceil(height / resolution - 1e-12)))
    row_bits = [0] * rows
    edges = tuple(
        (points[index], points[(index + 1) % len(points)])
        for index in range(len(points))
    )

    for row in range(rows):
        if row % 32 == 0:
            _check_search_cancelled(stop_requested)
        y_value = (row + 0.5) * resolution
        intersections = []
        for (x1, y1), (x2, y2) in edges:
            if (y1 > y_value) == (y2 > y_value):
                continue
            intersections.append(
                x1 + (x2 - x1) * (y_value - y1) / (y2 - y1)
            )
        intersections.sort()
        for index in range(0, len(intersections) - 1, 2):
            left, right = intersections[index], intersections[index + 1]
            first = max(0, int(math.ceil(left / resolution - 0.5 - 1e-12)))
            last = min(cols - 1, int(math.floor(right / resolution - 0.5 + 1e-12)))
            if last >= first:
                row_bits[row] |= ((1 << (last - first + 1)) - 1) << first

    # Mark the contour itself conservatively.  Sampling at <= half a pixel
    # guarantees that a boundary crossing cannot skip a complete grid cell.
    for edge_index, ((x1, y1), (x2, y2)) in enumerate(edges):
        if edge_index % 16 == 0:
            _check_search_cancelled(stop_requested)
        length = math.hypot(x2 - x1, y2 - y1)
        steps = max(1, int(math.ceil(length / max(resolution * 0.45, 1e-9))))
        for step in range(steps + 1):
            factor = step / steps
            x_value = x1 + (x2 - x1) * factor
            y_value = y1 + (y2 - y1) * factor
            col = min(cols - 1, max(0, int(math.floor(x_value / resolution))))
            row = min(rows - 1, max(0, int(math.floor(y_value / resolution))))
            row_bits[row] |= 1 << col

    area_pixels = sum(value.bit_count() for value in row_bits)
    return tuple(row_bits), rows, cols, area_pixels


def _raster_orientations(
    piece: ClassifiedPiece,
    allowed: Sequence[float],
    resolution: float,
    stop_requested: Optional[Callable[[], bool]] = None,
) -> Tuple[_RasterOrientation, ...]:
    result = []
    for option in _rotation_options(piece, allowed):
        _check_search_cancelled(stop_requested)
        points, option = _rotate_outer_points(piece, option.angle)
        row_bits, rows, cols, area_pixels = _rasterize_polygon_bits(
            points,
            option.width,
            option.height,
            resolution,
            stop_requested,
        )
        if area_pixels:
            result.append(
                _RasterOrientation(
                    angle=option.angle,
                    row_bits=row_bits,
                    rows=rows,
                    cols=cols,
                    area_pixels=area_pixels,
                    exact_width=option.width,
                    exact_height=option.height,
                    rotated_min_x=option.rotated_min_x,
                    rotated_min_y=option.rotated_min_y,
                    resolution=resolution,
                )
            )
    return tuple(result)


def _raster_mask_fits(
    sheet_bits: Sequence[int],
    mask: _RasterOrientation,
    row: int,
    col: int,
) -> bool:
    for mask_row, bits in enumerate(mask.row_bits):
        if bits and sheet_bits[row + mask_row] & (bits << col):
            return False
    return True


def _raster_contact_pixels(
    sheet_bits: Sequence[int],
    mask: _RasterOrientation,
    row: int,
    col: int,
) -> int:
    """Count edge contact without allowing any overlap."""

    contact = 0
    for mask_row, source_bits in enumerate(mask.row_bits):
        if not source_bits:
            continue
        shifted = source_bits << col
        sheet_row = row + mask_row
        contact += ((shifted << 1) & sheet_bits[sheet_row]).bit_count()
        contact += ((shifted >> 1) & sheet_bits[sheet_row]).bit_count()
        if sheet_row > 0:
            contact += (shifted & sheet_bits[sheet_row - 1]).bit_count()
        if sheet_row + 1 < len(sheet_bits):
            contact += (shifted & sheet_bits[sheet_row + 1]).bit_count()
    return contact


def _raster_candidate_rows(
    sheet_bits: Sequence[int],
    min_row: int,
    max_row: int,
    mask_rows: int,
) -> Tuple[int, ...]:
    rows = {min_row, max_row}
    previous = 0
    for row, bits in enumerate(sheet_bits):
        if bits != previous:
            rows.update((row - mask_rows, row - mask_rows + 1, row, row + 1))
        previous = bits
    return tuple(sorted(row for row in rows if min_row <= row <= max_row))


@lru_cache(maxsize=32768)
def _bit_runs(bits: int) -> Tuple[Tuple[int, int], ...]:
    """Return half-open runs of set bits without scanning an entire sheet row."""

    value = int(bits)
    runs = []
    while value:
        start = (value & -value).bit_length() - 1
        shifted = value >> start
        length = ((shifted ^ (shifted + 1)).bit_length() - 1)
        end = start + length
        runs.append((start, end))
        value &= ~(((1 << length) - 1) << start)
    return tuple(runs)


@lru_cache(maxsize=32768)
def _raster_forbidden_cols(source_bits, occupied_bits, min_col, max_col):
    """Translations blocked by a pair of rows, reusable across frontier levels."""
    forbidden = 0
    for source_start, source_end in _bit_runs(source_bits):
        for occupied_start, occupied_end in _bit_runs(occupied_bits):
            first = max(min_col, occupied_start - source_end + 1)
            last = min(max_col, occupied_end - source_start - 1)
            if first <= last:
                forbidden |= ((1 << (last - first + 1)) - 1) << (first - min_col)
    return forbidden


def _raster_candidate_cols(
    sheet_bits: Sequence[int],
    mask: _RasterOrientation,
    row: int,
    min_col: int,
    max_col: int,
) -> Tuple[int, ...]:
    """Return collision-free horizontal frontier contacts for this row.

    Two half-open runs collide at a contiguous interval of translations.
    Union those forbidden intervals as integer bits instead of generating
    thousands of blocked contacts and replaying the whole mask at each one.
    Endpoints of the remaining free runs are the feasible frontier contacts.
    """

    if max_col < min_col:
        return ()
    available = (1 << (max_col - min_col + 1)) - 1
    for mask_row, source_bits in enumerate(mask.row_bits):
        if not source_bits:
            continue
        occupied_bits = sheet_bits[row + mask_row]
        if not occupied_bits:
            continue
        available &= ~_raster_forbidden_cols(source_bits, occupied_bits, min_col, max_col)
        if not available:
            return ()
    candidates = set()
    for start, end in _bit_runs(available):
        candidates.add(min_col + start)
        candidates.add(min_col + end - 1)
    return tuple(sorted(candidates))


def _find_raster_positions(
    sheet_bits: Sequence[int],
    sheet_rows: int,
    sheet_cols: int,
    mask: _RasterOrientation,
    work_bounds: Bounds,
    spacing: float,
    limit: int = 1,
    used_bounds: Optional[Bounds] = None,
    stop_requested: Optional[Callable[[], bool]] = None,
) -> Tuple[Tuple[int, int, int], ...]:
    resolution = mask.resolution
    min_col = max(0, int(math.ceil(spacing / resolution - 1e-12)))
    min_row = max(0, int(math.ceil(spacing / resolution - 1e-12)))
    max_col = min(
        sheet_cols - mask.cols,
        int(
            math.floor(
                (work_bounds[2] - spacing - work_bounds[0] - mask.exact_width)
                / resolution
                + 1e-12
            )
        ),
    )
    max_row = min(
        sheet_rows - mask.rows,
        int(
            math.floor(
                (work_bounds[3] - spacing - work_bounds[1] - mask.exact_height)
                / resolution
                + 1e-12
            )
        ),
    )
    if max_col < min_col or max_row < min_row:
        return ()
    first_mask_row = next(
        ((index, bits) for index, bits in enumerate(mask.row_bits) if bits),
        None,
    )
    if first_mask_row is None:
        return ()
    first_offset, first_bits = first_mask_row
    candidates = []
    rows = _raster_candidate_rows(sheet_bits, min_row, max_row, mask.rows)
    feasible_row_levels = 0
    max_row_levels = min(3, max(1, int(limit)))
    for row in rows:
        _check_search_cancelled(stop_requested)
        candidates_before_row = len(candidates)
        quick_row = sheet_bits[row + first_offset]
        columns = _raster_candidate_cols(
            sheet_bits,
            mask,
            row,
            min_col,
            max_col,
        )
        for col_index, col in enumerate(columns):
            if col_index % 128 == 0:
                _check_search_cancelled(stop_requested)
            if quick_row & (first_bits << col):
                continue
            if _raster_mask_fits(sheet_bits, mask, row, col):
                x_value = work_bounds[0] + col * resolution
                y_value = work_bounds[1] + row * resolution
                bounds = (
                    x_value,
                    y_value,
                    x_value + mask.exact_width,
                    y_value + mask.exact_height,
                )
                used = bounds if used_bounds is None else _union_bounds(used_bounds, bounds)
                width = used[2] - used[0]
                height = used[3] - used[1]
                contact = _raster_contact_pixels(sheet_bits, mask, row, col)
                score = (
                    round(height, 12),
                    round(width * height, 12),
                    round(width, 12),
                    -contact,
                    row,
                    col,
                )
                candidates.append((score, row, col, contact))
        if len(candidates) > candidates_before_row:
            feasible_row_levels += 1
            # Height is the primary compactness criterion. Once enough
            # feasible frontier levels have been collected, every later row
            # is strictly worse in height and cannot enter this call's small
            # candidate set. This pruning is essential on fine rasters.
            if feasible_row_levels >= max_row_levels:
                break
    candidates.sort(key=lambda value: value[0])
    return tuple(
        (row, col, contact)
        for _score, row, col, contact in candidates[:max(1, int(limit))]
    )


def _find_raster_position(
    sheet_bits: Sequence[int],
    sheet_rows: int,
    sheet_cols: int,
    mask: _RasterOrientation,
    work_bounds: Bounds,
    spacing: float,
) -> Optional[Tuple[int, int]]:
    positions = _find_raster_positions(
        sheet_bits,
        sheet_rows,
        sheet_cols,
        mask,
        work_bounds,
        spacing,
    )
    return None if not positions else positions[0][:2]


def _stamp_raster_mask(
    sheet_bits: List[int],
    sheet_rows: int,
    sheet_cols: int,
    mask: _RasterOrientation,
    row: int,
    col: int,
    spacing_pixels: int,
) -> None:
    clip = (1 << sheet_cols) - 1
    for mask_row, source_bits in enumerate(mask.row_bits):
        if not source_bits:
            continue
        # Translate before dilation: shifting a local row right discards the
        # negative bits of its left clearance halo. In sheet coordinates those
        # bits are real space and must block neighbours approaching from left.
        shifted = source_bits << col
        expanded = shifted
        for distance in range(1, spacing_pixels + 1):
            expanded |= (shifted << distance) | (shifted >> distance)
        expanded &= clip
        sheet_row = row + mask_row
        for destination in range(
            max(0, sheet_row - spacing_pixels),
            min(sheet_rows - 1, sheet_row + spacing_pixels) + 1,
        ):
            sheet_bits[destination] |= expanded


def _raster_layout_signature(values: Sequence[_RasterPacked]) -> Tuple:
    return tuple(
        sorted(
            (
                value.item.piece.piece_id,
                value.item.instance,
                round(value.bounds[0], 9),
                round(value.bounds[1], 9),
                round(value.orientation.angle, 9),
            )
            for value in values
        )
    )


def _pack_raster_order(
    ordered: Sequence[_PackInstance],
    work_bounds: Bounds,
    spacing: float,
    orientation_by_piece: Mapping[str, Tuple[_RasterOrientation, ...]],
    resolution: float,
    beam_width: int = 1,
    candidate_limit: int = 1,
    piece_choice_limit: int = 1,
    stop_requested: Optional[Callable[[], bool]] = None,
) -> Tuple[List[_RasterPacked], List[_PackInstance], Optional[Bounds]]:
    width = work_bounds[2] - work_bounds[0]
    height = work_bounds[3] - work_bounds[1]
    sheet_cols = int(math.floor(width / resolution + 1e-12))
    sheet_rows = int(math.floor(height / resolution + 1e-12))
    spacing_pixels = int(math.ceil(spacing / resolution - 1e-12))
    beam_width = max(1, int(beam_width))
    candidate_limit = max(1, int(candidate_limit))
    piece_choice_limit = max(1, int(piece_choice_limit))
    states = (_RasterBeamState(tuple(0 for _ in range(sheet_rows))),)
    order_rank = {item.identity: index for index, item in enumerate(ordered)}
    for _depth in range(len(ordered)):
        _check_search_cancelled(stop_requested)
        expanded_states = []
        for state in states:
            _check_search_cancelled(stop_requested)
            handled = {
                value.item.identity for value in state.packed
            } | {
                value.identity for value in state.unplaced
            }
            remaining = tuple(
                item for item in ordered if item.identity not in handled
            )
            if not remaining:
                expanded_states.append(state)
                continue
            used_before = _bounds_union([value.bounds for value in state.packed])
            for item in remaining[:piece_choice_limit]:
                state_candidates = []
                for orientation in orientation_by_piece[item.piece.piece_id]:
                    _check_search_cancelled(stop_requested)
                    for row, col, contact in _find_raster_positions(
                        state.sheet_bits,
                        sheet_rows,
                        sheet_cols,
                        orientation,
                        work_bounds,
                        spacing,
                        limit=candidate_limit,
                        used_bounds=used_before,
                        stop_requested=stop_requested,
                    ):
                        x_value = work_bounds[0] + col * resolution
                        y_value = work_bounds[1] + row * resolution
                        bounds = (
                            x_value,
                            y_value,
                            x_value + orientation.exact_width,
                            y_value + orientation.exact_height,
                        )
                        used = (
                            bounds
                            if used_before is None
                            else _union_bounds(used_before, bounds)
                        )
                        used_width = used[2] - used[0]
                        used_height = used[3] - used[1]
                        state_candidates.append(
                            (
                                (
                                    round(used_height, 12),
                                    round(used_width * used_height, 12),
                                    round(used_width, 12),
                                    -contact,
                                    order_rank[item.identity],
                                    row,
                                    col,
                                    round(orientation.angle, 12),
                                ),
                                orientation,
                                row,
                                col,
                                bounds,
                                contact,
                            )
                        )
                if not state_candidates:
                    expanded_states.append(
                        _RasterBeamState(
                            state.sheet_bits,
                            state.packed,
                            state.unplaced + (item,),
                            state.contact_pixels,
                        )
                    )
                    continue
                state_candidates.sort(key=lambda value: value[0])
                for (
                    _score,
                    orientation,
                    row,
                    col,
                    bounds,
                    contact,
                ) in state_candidates[: max(candidate_limit, beam_width)]:
                    next_bits = list(state.sheet_bits)
                    _stamp_raster_mask(
                        next_bits,
                        sheet_rows,
                        sheet_cols,
                        orientation,
                        row,
                        col,
                        spacing_pixels,
                    )
                    expanded_states.append(
                        _RasterBeamState(
                            tuple(next_bits),
                            state.packed + (
                                _RasterPacked(item, orientation, row, col, bounds),
                            ),
                            state.unplaced,
                            state.contact_pixels + contact,
                        )
                    )

        def state_score(state):
            used = _bounds_union([value.bounds for value in state.packed])
            used_width = 0.0 if used is None else used[2] - used[0]
            used_height = 0.0 if used is None else used[3] - used[1]
            return (
                len(state.unplaced),
                -len(state.packed),
                round(used_height, 12),
                round(used_width * used_height, 12),
                round(used_width, 12),
                -state.contact_pixels,
                _raster_layout_signature(state.packed),
                tuple(value.identity for value in state.unplaced),
            )

        unique = {}
        for state in expanded_states:
            handled_identity = tuple(
                sorted(
                    [value.item.identity for value in state.packed]
                    + [value.identity for value in state.unplaced]
                )
            )
            key = (
                state.sheet_bits,
                handled_identity,
                tuple(value.identity for value in state.unplaced),
            )
            previous = unique.get(key)
            if previous is None or state_score(state) < state_score(previous):
                unique[key] = state
        states = tuple(sorted(unique.values(), key=state_score)[:beam_width])

    best = min(states, key=state_score)
    packed = list(best.packed)
    unplaced = list(best.unplaced)
    return packed, unplaced, _bounds_union([value.bounds for value in packed])


def _organize_pieces_raster(
    pieces: Sequence[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float,
    quantities: Mapping[str, int],
    rotations: Mapping[str, Sequence[float]],
    max_orders: int = 2,
    beam_width: int = 1,
    candidate_limit: int = 1,
    stop_requested: Optional[Callable[[], bool]] = None,
    toolpath_offset: float = 0.0,
    prefer_remnants: bool = False,
) -> Optional[OrganizationResult]:
    if not pieces or any(len(piece.outer_points) < 3 for piece in pieces):
        return None
    if sum(len(piece.outer_points) for piece in pieces) > 50000:
        return None
    resolution = _bounded_raster_resolution(pieces, work_bounds, spacing, rotations)
    sheet_cols = int(math.floor((work_bounds[2] - work_bounds[0]) / resolution))
    sheet_rows = int(math.floor((work_bounds[3] - work_bounds[1]) / resolution))
    if sheet_cols <= 0 or sheet_rows <= 0 or sheet_cols * sheet_rows > 1500000:
        return None

    instances = []
    for piece in pieces:
        width = piece.bounds[2] - piece.bounds[0]
        height = piece.bounds[3] - piece.bounds[1]
        quantity = max(1, int(quantities.get(piece.piece_id, 1)))
        for instance in range(1, quantity + 1):
            instances.append(_PackInstance(piece, instance, width, height))
    if len(instances) > 200:
        return None

    orientation_by_piece = {}
    estimated_mask_cells = 0
    for piece in pieces:
        _check_search_cancelled(stop_requested)
        orientations = _raster_orientations(
            piece,
            rotations.get(piece.piece_id, (0.0, 90.0)),
            resolution,
            stop_requested,
        )
        if not orientations:
            return None
        orientation_by_piece[piece.piece_id] = orientations
        estimated_mask_cells += sum(value.rows * value.cols for value in orientations)
    if estimated_mask_cells > 12000000:
        return None

    orders = _candidate_orders(instances, max_orders=max_orders)
    if len(instances) > 30:
        orders = orders[:max(2, min(int(max_orders), 4))]
        beam_width = min(int(beam_width), 4)
        candidate_limit = min(int(candidate_limit), 3)
    if len(instances) > 80:
        beam_width = 1
        candidate_limit = 1
    layouts = []
    for ordered in orders:
        _check_search_cancelled(stop_requested)
        search_settings = []
        for current_beam, current_limit in (
            (beam_width, candidate_limit),
            (min(beam_width, 4), min(candidate_limit, 3)),
            (min(beam_width, 2), min(candidate_limit, 2)),
            (1, 1),
        ):
            setting = (
                max(1, int(current_beam)),
                max(1, int(current_limit)),
            )
            if setting not in search_settings:
                search_settings.append(setting)
        # Every deeper profile keeps the shallower adaptive searches in the
        # competition. A wider beam can prune the state that a width-2 search
        # would have retained because their intermediate scores are different;
        # "profundo" must therefore never discard a proven balanced layout.
        for current_beam, current_limit in search_settings:
            _check_search_cancelled(stop_requested)
            packed, unplaced, used = _pack_raster_order(
                ordered,
                work_bounds,
                spacing,
                orientation_by_piece,
                resolution,
                beam_width=current_beam,
                candidate_limit=current_limit,
                piece_choice_limit=current_limit,
                stop_requested=stop_requested,
            )
            used_width = 0.0 if used is None else used[2] - used[0]
            used_height = 0.0 if used is None else used[3] - used[1]
            placed_area = sum(value.item.source_area for value in packed)
            scrap_fragmentation = _layout_scrap_fragmentation_score(
                (
                    (value.item.piece, value.orientation.angle, value.bounds)
                    for value in packed
                ),
                used,
            )
            score = (
                -round(placed_area, 12),
                len(unplaced),
                round(used_height, 12),
                round(used_width * used_height, 12),
                round(used_width, 12),
                round(scrap_fragmentation, 12),
                _raster_layout_signature(packed),
            )
            layouts.append((score, packed, unplaced, used))
    if not layouts:
        return None

    def result_from_layout(packed, unplaced, used):
        result = OrganizationResult(
            unplaced_piece_ids=[value.piece.piece_id for value in unplaced],
            used_bounds=used,
            strategy="contorno real",
            evaluated_layouts=len(layouts),
            scrap_fragmentation_score=_layout_scrap_fragmentation_score(
                (
                    (value.item.piece, value.orientation.angle, value.bounds)
                    for value in packed
                ),
                used,
            ),
        )
        for value in packed:
            piece = value.item.piece
            result.placements.append(
                PiecePlacement(
                    piece_id=piece.piece_id,
                    instance=value.item.instance,
                    dx=value.bounds[0] - value.orientation.rotated_min_x,
                    dy=value.bounds[1] - value.orientation.rotated_min_y,
                    rotation_degrees=value.orientation.angle,
                    placed_bounds=value.bounds,
                    entity_ids=(
                        (piece.outer_id,)
                        + piece.descendant_ids
                        + piece.feature_ids
                        + piece.marking_ids
                    ),
                )
            )
        return result

    def layout_score(value):
        if not prefer_remnants:
            return value[0]
        old_score, boxes, unplaced_items, _used = value
        preview = OrganizationResult(
            placements=[PiecePlacement(box.item.piece.piece_id, box.item.instance,
                                       0.0, 0.0, box.orientation.angle, box.bounds, ())
                        for box in boxes],
            unplaced_piece_ids=[item.piece.piece_id for item in unplaced_items],
            sheet_bounds=(tuple(map(float, work_bounds)),),
        )
        return organization_remnant_score(preview, clearance=max(1.0, spacing * 0.5)) + (old_score,)

    ordered_layouts = sorted(layouts, key=layout_score)
    best_invalid = None
    for _score, packed, unplaced, used in ordered_layouts:
        _check_search_cancelled(stop_requested)
        result = result_from_layout(packed, unplaced, used)
        issues = validate_organization_result_geometry(
            pieces,
            result,
            minimum_clearance=spacing,
            toolpath_offset=toolpath_offset,
        )
        if not issues:
            return result
        if best_invalid is None:
            best_invalid = result
    # Preserve the existing outer fallback contract. The caller compares this
    # candidate with MaxRects and will reject it through the same exact gate;
    # returning the best invalid layout keeps the diagnostic cause available.
    return best_invalid


def _organization_result_score(result: OrganizationResult) -> Tuple:
    if result.used_bounds is None:
        width = height = 0.0
    else:
        width = result.used_bounds[2] - result.used_bounds[0]
        height = result.used_bounds[3] - result.used_bounds[1]
    signature = tuple(
        sorted(
            (
                value.piece_id,
                value.instance,
                round(value.placed_bounds[0], 9),
                round(value.placed_bounds[1], 9),
                round(value.rotation_degrees, 9),
            )
            for value in result.placements
        )
    )
    sheet_count = len(result.sheet_bounds)
    if not sheet_count and result.placements:
        # Internal single-sheet candidates are scored before ``organize_pieces``
        # attaches their sheet bounds.  Derive the same value from placement
        # indices so this key remains valid for both internal and progressive
        # full-layout comparisons.
        sheet_count = 1 + max(
            max(0, int(value.sheet_index))
            for value in result.placements
        )
    return (
        len(result.unplaced_piece_ids),
        -len(result.placements),
        sheet_count,
        round(height, 12),
        round(width * height, 12),
        round(width, 12),
        round(float(result.scrap_fragmentation_score), 12),
        signature,
    )


def organization_result_score(result: OrganizationResult) -> Tuple:
    """Public deterministic comparison key used by progressive UI searches."""

    return _organization_result_score(result)


def organization_remnant_score(result, *, minimum_short_side=100.0, clearance=1.0):
    """Rank complete previews by coverage, sheets and reusable, cuttable waste.

    Use the same conservative rectangles as the offcut preview. Bounding boxes
    intentionally exclude pockets between diagonal contours that cannot yet
    be separated by the existing rectangular-remnant tool.
    """
    base = _organization_result_score(result)
    remnants = {}
    for cut in suggest_rectangular_remnant_cuts(
        result, minimum_short_side=minimum_short_side, clearance=clearance,
    ):
        key = (cut.sheet_index, cut.remnant_bounds)
        area, count = remnants.get(key, (cut.area, 0))
        remnants[key] = area, count + 1
    practical_areas = [area / (1.0 + 0.30 * max(0, count - 1))
                       for area, count in remnants.values()]
    return base[:3] + (
        -round(max(practical_areas, default=0.0), 6),
        -round(sum(area for area, _count in remnants.values()), 6),
    ) + base[3:]


def orthogonal_nesting_rotations(rotations):
    """Explore aligned poses without adding any rotation forbidden by the user."""
    return {
        key: tuple(angle for angle in angles
                   if abs(float(angle) / 90.0 - round(float(angle) / 90.0)) < 1e-9)
        or tuple(angles)
        for key, angles in rotations.items()
    }


def _point_segment_clearance(point, start, end):
    vector_x = end.x - start.x
    vector_y = end.y - start.y
    length_squared = vector_x * vector_x + vector_y * vector_y
    if length_squared <= 1.0e-24:
        return point.distance_to(start), start
    parameter = (
        (point.x - start.x) * vector_x
        + (point.y - start.y) * vector_y
    ) / length_squared
    parameter = max(0.0, min(1.0, parameter))
    projected = Vec2(
        start.x + vector_x * parameter,
        start.y + vector_y * parameter,
    )
    return point.distance_to(projected), projected


def _contour_clearance(first, second):
    """Return exact boundary distance and the nearest point pair."""

    best = (float("inf"), first.points[0], second.points[0])
    second_segments = tuple(
        (start, end, min(start.x, end.x), min(start.y, end.y),
         max(start.x, end.x), max(start.y, end.y))
        for start, end in zip(second.points, second.points[1:] + second.points[:1])
    )
    for first_index, first_start in enumerate(first.points):
        first_end = first.points[(first_index + 1) % len(first.points)]
        min_x, max_x = sorted((first_start.x, first_end.x))
        min_y, max_y = sorted((first_start.y, first_end.y))
        for second_start, second_end, sx0, sy0, sx1, sy1 in second_segments:
            # Bounding-box distance is a lower bound on segment distance.
            # Skip only pairs that cannot improve the exact minimum already
            # measured; this changes cost, never the clearance tolerance.
            dx = max(0.0, min_x - sx1, sx0 - max_x)
            dy = max(0.0, min_y - sy1, sy0 - max_y)
            if dx * dx + dy * dy >= best[0] * best[0]:
                continue
            candidates = []
            distance, projected = _point_segment_clearance(
                first_start, second_start, second_end
            )
            candidates.append((distance, first_start, projected))
            distance, projected = _point_segment_clearance(
                first_end, second_start, second_end
            )
            candidates.append((distance, first_end, projected))
            distance, projected = _point_segment_clearance(
                second_start, first_start, first_end
            )
            candidates.append((distance, projected, second_start))
            distance, projected = _point_segment_clearance(
                second_end, first_start, first_end
            )
            candidates.append((distance, projected, second_end))
            candidate = min(candidates, key=lambda value: value[0])
            if candidate[0] < best[0]:
                best = candidate
                if best[0] <= 1.0e-12:
                    return best
    return best


def _bounds_clearance(first, second):
    delta_x = max(first[0] - second[2], second[0] - first[2], 0.0)
    delta_y = max(first[1] - second[3], second[1] - first[3], 0.0)
    return math.hypot(delta_x, delta_y)


def validate_organization_result_geometry(
    pieces: Iterable[ClassifiedPiece],
    result: OrganizationResult,
    tolerance: float = 1.0e-6,
    minimum_clearance: float = 0.0,
    toolpath_offset: float = 0.0,
) -> Tuple[CommonLineIssue, ...]:
    """Prove topology and requested clearance on placed real outer contours.

    Raster/MaxRects remain candidate generators. This exact vector pass is the
    final safety gate before a candidate can reach the UI, and deliberately
    accepts ordinary common boundaries and T/4-way point junctions when the
    requested clearance is zero.
    """

    piece_by_id = {piece.piece_id: piece for piece in pieces}
    contours_by_sheet = {}
    for placement in result.placements:
        piece = piece_by_id.get(placement.piece_id)
        if piece is None or len(piece.outer_points) < 3:
            continue
        transform = organization_placement_transform(piece, placement)
        points = tuple(
            transform.apply_to_point(Vec2(float(x_value), float(y_value)))
            for x_value, y_value in piece.outer_points
        )
        contour_id = "%s#%d" % (placement.piece_id, placement.instance)
        contours_by_sheet.setdefault(int(placement.sheet_index), []).append(
            CommonLineContour(contour_id, points)
        )
    issues = []
    minimum_clearance = max(0.0, float(minimum_clearance))
    toolpath_offset = max(0.0, float(toolpath_offset))
    for contours in contours_by_sheet.values():
        if len(contours) < 2:
            continue
        plan = plan_common_line_cut(contours, tolerance=float(tolerance))
        issues.extend(plan.issues)
        if plan.issues or minimum_clearance <= tolerance:
            continue
        contour_bounds = [
            (
                min(point.x for point in contour.points),
                min(point.y for point in contour.points),
                max(point.x for point in contour.points),
                max(point.y for point in contour.points),
            )
            for contour in contours
        ]
        for first_index, first in enumerate(contours):
            for second_index in range(first_index + 1, len(contours)):
                second = contours[second_index]
                if (
                    _bounds_clearance(
                        contour_bounds[first_index],
                        contour_bounds[second_index],
                    )
                    >= minimum_clearance - tolerance
                ):
                    continue
                clearance, first_point, second_point = _contour_clearance(
                    first, second
                )
                if clearance >= minimum_clearance - tolerance:
                    continue
                location = Vec2(
                    (first_point.x + second_point.x) * 0.5,
                    (first_point.y + second_point.y) * 0.5,
                )
                issues.append(
                    CommonLineIssue(
                        CommonLineIssueCode.INSUFFICIENT_CLEARANCE,
                        "As peças %s e %s ficaram com folga de %.3f mm; "
                        "a folga mínima é %.3f mm."
                        % (
                            first.contour_id,
                            second.contour_id,
                            clearance,
                            minimum_clearance,
                        ),
                        (first.contour_id, second.contour_id),
                        (location,),
                    )
                )
        if not issues and toolpath_offset > tolerance:
            compensated = []
            for contour in contours:
                try:
                    points = common_line_offset_closed_polygon(
                        ((point.x, point.y) for point in contour.points),
                        toolpath_offset,
                    )
                except ValueError as error:
                    issues.append(
                        CommonLineIssue(
                            CommonLineIssueCode.INVALID_CONTOUR,
                            "Não foi possível compensar %s: %s"
                            % (contour.contour_id, error),
                            (contour.contour_id,),
                        )
                    )
                    continue
                compensated.append(CommonLineContour(contour.contour_id, points))
            if len(compensated) == len(contours):
                issues.extend(
                    plan_common_line_cut(
                        compensated,
                        tolerance=float(tolerance),
                    ).issues
                )
    return tuple(issues)


def _copy_organization_without_placement(
    result: OrganizationResult,
    remove_index: int,
    piece_by_id: Mapping[str, ClassifiedPiece],
) -> OrganizationResult:
    placements = [
        placement
        for index, placement in enumerate(result.placements)
        if index != int(remove_index)
    ]
    removed = result.placements[int(remove_index)]
    used = _bounds_union([placement.placed_bounds for placement in placements])
    return OrganizationResult(
        placements=placements,
        unplaced_piece_ids=list(result.unplaced_piece_ids) + [removed.piece_id],
        used_bounds=used,
        sheet_bounds=tuple(result.sheet_bounds),
        strategy=result.strategy,
        evaluated_layouts=result.evaluated_layouts,
        placed_area=sum(
            max(0.0, float(piece_by_id[placement.piece_id].area))
            for placement in placements
            if placement.piece_id in piece_by_id
        ),
        sheet_area=result.sheet_area,
        utilization_percent=result.utilization_percent,
        scrap_fragmentation_score=_layout_scrap_fragmentation_score(
            (
                (
                    piece_by_id[placement.piece_id],
                    placement.rotation_degrees,
                    placement.placed_bounds,
                )
                for placement in placements
                if placement.piece_id in piece_by_id
            ),
            used,
        ),
    )


def _repair_invalid_organization_candidate(
    pieces: Sequence[ClassifiedPiece],
    result: OrganizationResult,
    minimum_clearance: float,
    toolpath_offset: float,
) -> Optional[OrganizationResult]:
    """Turn an unsafe full candidate into a safe partial sheet.

    The outer organizer already carries unplaced pieces to the next physical
    sheet.  Pruning only a conflicting placement is therefore preferable to
    aborting the whole job after an otherwise useful page was found.
    """

    piece_by_id = {piece.piece_id: piece for piece in pieces}
    working = result
    while working.placements:
        issues = validate_organization_result_geometry(
            pieces,
            working,
            minimum_clearance=minimum_clearance,
            toolpath_offset=toolpath_offset,
        )
        if not issues:
            return working
        placement_index = {
            "%s#%d" % (placement.piece_id, placement.instance): index
            for index, placement in enumerate(working.placements)
        }
        implicated = sorted(
            {
                placement_index[contour_id]
                for issue in issues
                for contour_id in issue.contour_ids
                if contour_id in placement_index
            }
        )
        if not implicated:
            return None
        alternatives = []
        for index in implicated:
            candidate = _copy_organization_without_placement(
                working,
                index,
                piece_by_id,
            )
            candidate_issues = validate_organization_result_geometry(
                pieces,
                candidate,
                minimum_clearance=minimum_clearance,
                toolpath_offset=toolpath_offset,
            )
            alternatives.append(
                (
                    len(candidate_issues),
                    _organization_result_score(candidate),
                    candidate,
                )
            )
        working = min(alternatives, key=lambda value: value[:2])[2]
    return working


def _repack_with_safe_clearance(
    pieces: Sequence[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float,
    quantities: Mapping[str, int],
    rotations: Mapping[str, Sequence[float]],
    max_orders: int,
    stop_requested: Optional[Callable[[], bool]],
    toolpath_offset: float,
) -> Optional[OrganizationResult]:
    """Try a slightly wider one-sheet repack before spilling a piece.

    Exact cutter compensation can reject an otherwise legal minimum-gap pose,
    especially around acute corners.  The previous recovery immediately
    removed one conflicting placement, which made an obviously roomy job use a
    second sheet.  Re-running deterministic MaxRects with progressively wider
    clearance gives every piece a new pose on the same sheet first.
    """

    expected_instances = sum(
        max(1, int(quantities.get(piece.piece_id, 1)))
        for piece in pieces
    )
    base_step = max(0.25, min(2.0, max(0.0, float(toolpath_offset)) * 0.25))
    best = None
    for factor in (1.0, 2.0, 4.0, 8.0, 16.0):
        _check_search_cancelled(stop_requested)
        trial_spacing = float(spacing) + base_step * factor
        candidate = _organize_pieces_rectangular(
            pieces,
            work_bounds,
            trial_spacing,
            quantities,
            rotations,
            min(max(1, int(max_orders)), 8),
            stop_requested,
        )
        issues = validate_organization_result_geometry(
            pieces,
            candidate,
            minimum_clearance=spacing,
            toolpath_offset=toolpath_offset,
        )
        if issues:
            continue
        candidate.strategy = "MaxRects — folga vetorial segura"
        if best is None or _organization_result_score(
            candidate
        ) < _organization_result_score(best):
            best = candidate
        if len(candidate.placements) >= expected_instances:
            return candidate
    return best


def _organize_single_sheet(
    pieces: Iterable[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float = 10.0,
    quantities: Optional[Mapping[str, int]] = None,
    rotations: Optional[Mapping[str, Sequence[float]]] = None,
    search_mode: str = "balanced",
    search_budget: Optional[Sequence[int]] = None,
    stop_requested: Optional[Callable[[], bool]] = None,
    toolpath_offset: float = 0.0,
    prefer_remnants: bool = False,
) -> OrganizationResult:
    """Return the best pure preview for one sheet.

    Classified contours use independent integer bitmasks, allowing concave
    pieces to interlock while preserving their outer+descendant rigid unit.
    MaxRects remains the deterministic fallback for missing contour data,
    excessive raster cost or any case where rectangular packing scores better.
    """

    pieces = tuple(pieces)
    spacing = max(0.0, float(spacing))
    quantities = quantities or {}
    rotations = rotations or {}
    if search_mode not in _SEARCH_ORDER_BUDGETS:
        raise ValueError("O perfil de busca do nesting é inválido.")
    budget = (
        _SEARCH_ORDER_BUDGETS[search_mode]
        if search_budget is None
        else tuple(int(value) for value in search_budget)
    )
    if (
        len(budget) != 4
        or budget[0] < 1
        or budget[1] < 0
        or budget[2] < 1
        or budget[3] < 1
    ):
        raise ValueError("O orçamento de busca do nesting é inválido.")
    (
        rectangular_orders,
        raster_orders,
        raster_beam_width,
        raster_candidate_limit,
    ) = budget
    _check_search_cancelled(stop_requested)
    rectangular = _organize_pieces_rectangular(
        pieces,
        work_bounds,
        spacing,
        quantities,
        rotations,
        rectangular_orders,
        stop_requested,
        prefer_remnants,
    )
    raster = None
    if raster_orders > 0:
        raster = _organize_pieces_raster(
            pieces,
            tuple(map(float, work_bounds)),
            spacing,
            quantities,
            rotations,
            raster_orders,
            raster_beam_width,
            raster_candidate_limit,
            stop_requested,
            toolpath_offset,
            prefer_remnants,
        )
    if raster is None:
        issues = validate_organization_result_geometry(
            pieces,
            rectangular,
            minimum_clearance=spacing,
            toolpath_offset=toolpath_offset,
        )
        if issues:
            repacked = _repack_with_safe_clearance(
                pieces,
                work_bounds,
                spacing,
                quantities,
                rotations,
                rectangular_orders,
                stop_requested,
                toolpath_offset,
            )
            if repacked is not None and repacked.placements:
                repacked.evaluated_layouts += rectangular.evaluated_layouts
                return repacked
            repaired = _repair_invalid_organization_candidate(
                pieces,
                rectangular,
                spacing,
                toolpath_offset,
            )
            if repaired is not None and repaired.placements:
                return repaired
            raise RuntimeError(
                "O nesting retangular produziu geometria inválida: %s"
                % issues[0].message
            )
        return rectangular
    evaluated = rectangular.evaluated_layouts + raster.evaluated_layouts
    def candidate_score(candidate):
        if not prefer_remnants:
            return _organization_result_score(candidate)
        return organization_remnant_score(
            replace(candidate, sheet_bounds=(tuple(map(float, work_bounds)),)),
            clearance=max(1.0, spacing * 0.5),
        )
    selected, fallback = (
        (raster, rectangular)
        if candidate_score(raster) <= candidate_score(rectangular)
        else (rectangular, raster)
    )
    selected_issues = validate_organization_result_geometry(
        pieces,
        selected,
        minimum_clearance=spacing,
        toolpath_offset=toolpath_offset,
    )
    if selected_issues:
        fallback_issues = validate_organization_result_geometry(
            pieces,
            fallback,
            minimum_clearance=spacing,
            toolpath_offset=toolpath_offset,
        )
        if fallback_issues:
            repacked = _repack_with_safe_clearance(
                pieces,
                work_bounds,
                spacing,
                quantities,
                rotations,
                rectangular_orders,
                stop_requested,
                toolpath_offset,
            )
            if repacked is not None:
                repacked.evaluated_layouts += evaluated
            repaired = [
                candidate
                for candidate in (
                    repacked,
                    _repair_invalid_organization_candidate(
                        pieces,
                        selected,
                        spacing,
                        toolpath_offset,
                    ),
                    _repair_invalid_organization_candidate(
                        pieces,
                        fallback,
                        spacing,
                        toolpath_offset,
                    ),
                )
                if candidate is not None and candidate.placements
            ]
            if not repaired:
                raise RuntimeError(
                    "O nesting foi bloqueado pela validação vetorial final: %s"
                    % selected_issues[0].message
                )
            selected = min(repaired, key=candidate_score)
        else:
            selected = fallback
    selected.evaluated_layouts = evaluated
    return selected


def _union_bounds(first: Optional[Bounds], second: Bounds) -> Bounds:
    if first is None:
        return second
    return (
        min(first[0], second[0]),
        min(first[1], second[1]),
        max(first[2], second[2]),
        max(first[3], second[3]),
    )


def _preserve_existing_layout_candidate(
    pieces: Sequence[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float,
    rotations: Mapping[str, Sequence[float]],
    toolpath_offset: float,
    stop_requested: Optional[Callable[[], bool]] = None,
) -> Optional[OrganizationResult]:
    """Keep an already valid arrangement and split it at natural gaps.

    Operators commonly prepare repeated rails and panels in meaningful blocks
    before asking the organizer to create sheets.  The packing generators used
    to discard that work even when the same number of sheets was unavoidable.
    This candidate preserves every relative XY position and the current
    orientation inside each block.  It only translates whole
    guillotine-separated blocks to side-by-side virtual sheets.

    A block is accepted only after the same exact contour/clearance gate used by
    generated nests.  Overlapping, too-tight or genuinely scattered layouts
    therefore remain the responsibility of the regular organizer.
    """

    pieces = tuple(pieces)
    if len(pieces) < 2:
        return None
    # Guillotine partitioning is a quality candidate, not the safety/packing
    # fallback.  Keep the first progressive preview responsive on very large
    # production batches; their normal deterministic packer remains unchanged.
    if len(pieces) > 64:
        return None
    min_x, min_y, max_x, max_y = map(float, work_bounds)
    usable_width = max_x - min_x - 2.0 * spacing
    usable_height = max_y - min_y - 2.0 * spacing
    if usable_width <= 1.0e-9 or usable_height <= 1.0e-9:
        return None
    piece_by_id = {piece.piece_id: piece for piece in pieces}
    all_ids = tuple(sorted(piece_by_id))
    tolerance = 1.0e-7

    def zero_rotation_allowed(piece_id):
        allowed = rotations.get(piece_id, (0.0, 90.0))
        return any(
            abs(float(angle) % 360.0) <= 1.0e-9
            for angle in allowed
        )

    if not all(zero_rotation_allowed(piece_id) for piece_id in all_ids):
        return None

    @lru_cache(maxsize=None)
    def group_bounds(group_ids):
        return _bounds_union(
            [piece_by_id[piece_id].bounds for piece_id in group_ids]
        )

    @lru_cache(maxsize=None)
    def group_can_stay(group_ids):
        _check_search_cancelled(stop_requested)
        bounds = group_bounds(group_ids)
        if bounds is None:
            return False
        width = bounds[2] - bounds[0]
        height = bounds[3] - bounds[1]
        if (
            width > usable_width + tolerance
            or height > usable_height + tolerance
        ):
            return False
        shift_x = min_x + spacing - bounds[0]
        shift_y = min_y + spacing - bounds[1]
        placements = []
        for piece_id in group_ids:
            piece = piece_by_id[piece_id]
            placed_bounds = (
                piece.bounds[0] + shift_x,
                piece.bounds[1] + shift_y,
                piece.bounds[2] + shift_x,
                piece.bounds[3] + shift_y,
            )
            placements.append(
                PiecePlacement(
                    piece_id=piece_id,
                    instance=1,
                    dx=shift_x,
                    dy=shift_y,
                    rotation_degrees=0.0,
                    placed_bounds=placed_bounds,
                    entity_ids=(
                        (piece.outer_id,)
                        + piece.descendant_ids
                        + piece.feature_ids
                        + piece.marking_ids
                    ),
                )
            )
        probe = OrganizationResult(placements=placements)
        issues = validate_organization_result_geometry(
            (piece_by_id[piece_id] for piece_id in group_ids),
            probe,
            minimum_clearance=spacing,
            toolpath_offset=toolpath_offset,
        )
        if issues:
            return False
        # The exact gate has no contours to compare for legacy/custom pieces
        # without ``outer_points``.  Preserve them only when their conservative
        # bounding boxes also prove the requested spacing.
        grouped_pieces = [piece_by_id[piece_id] for piece_id in group_ids]
        return all(
            _bounds_have_spacing(first.bounds, second.bounds, spacing)
            for index, first in enumerate(grouped_pieces)
            for second in grouped_pieces[index + 1 :]
            if len(first.outer_points) < 3 or len(second.outer_points) < 3
        )

    def partition_score(groups):
        areas = []
        for group in groups:
            bounds = group_bounds(group)
            areas.append(
                max(0.0, bounds[2] - bounds[0])
                * max(0.0, bounds[3] - bounds[1])
            )
        return (
            len(groups),
            round(sum(areas), 9),
            round(max(areas) if areas else 0.0, 9),
            groups,
        )

    @lru_cache(maxsize=None)
    def partition(group_ids):
        _check_search_cancelled(stop_requested)
        group_ids = tuple(sorted(group_ids))
        if group_can_stay(group_ids):
            return (group_ids,)
        candidates = []
        for axis in (0, 1):
            ordered = sorted(
                group_ids,
                key=lambda piece_id: (
                    (
                        piece_by_id[piece_id].bounds[axis]
                        + piece_by_id[piece_id].bounds[axis + 2]
                    )
                    * 0.5,
                    piece_id,
                ),
            )
            for index in range(1, len(ordered)):
                left = tuple(sorted(ordered[:index]))
                right = tuple(sorted(ordered[index:]))
                left_edge = max(
                    piece_by_id[value].bounds[axis + 2]
                    for value in left
                )
                right_edge = min(
                    piece_by_id[value].bounds[axis]
                    for value in right
                )
                if left_edge > right_edge + tolerance:
                    continue
                left_groups = partition(left)
                right_groups = partition(right)
                if left_groups is None or right_groups is None:
                    continue
                candidates.append(left_groups + right_groups)
        if not candidates:
            return None
        return min(candidates, key=partition_score)

    groups = partition(all_ids)
    if not groups:
        return None
    groups = tuple(
        sorted(
            groups,
            key=lambda group: (
                group_bounds(group)[0],
                group_bounds(group)[1],
                group,
            ),
        )
    )
    sheet_width = max_x - min_x
    sheet_gap = max(50.0, spacing * 4.0)
    result = OrganizationResult(
        strategy="arranjo atual preservado",
        evaluated_layouts=1,
    )
    fragmentation = 0.0
    for sheet_index, group_ids in enumerate(groups):
        _check_search_cancelled(stop_requested)
        source_bounds = group_bounds(group_ids)
        offset_x = sheet_index * (sheet_width + sheet_gap)
        shift_x = min_x + spacing + offset_x - source_bounds[0]
        shift_y = min_y + spacing - source_bounds[1]
        sheet_bounds = (
            min_x + offset_x,
            min_y,
            max_x + offset_x,
            max_y,
        )
        result.sheet_bounds += (sheet_bounds,)
        page_bounds = None
        page_layout = []
        for piece_id in group_ids:
            piece = piece_by_id[piece_id]
            placed_bounds = (
                piece.bounds[0] + shift_x,
                piece.bounds[1] + shift_y,
                piece.bounds[2] + shift_x,
                piece.bounds[3] + shift_y,
            )
            placement = PiecePlacement(
                piece_id=piece_id,
                instance=1,
                dx=shift_x,
                dy=shift_y,
                rotation_degrees=0.0,
                placed_bounds=placed_bounds,
                entity_ids=(
                    (piece.outer_id,)
                    + piece.descendant_ids
                    + piece.feature_ids
                    + piece.marking_ids
                ),
                sheet_index=sheet_index,
            )
            result.placements.append(placement)
            result.used_bounds = _union_bounds(result.used_bounds, placed_bounds)
            page_bounds = _union_bounds(page_bounds, placed_bounds)
            page_layout.append((piece, 0.0, placed_bounds))
        fragmentation += _layout_scrap_fragmentation_score(
            page_layout,
            page_bounds,
        )
    result.scrap_fragmentation_score = fragmentation
    issues = validate_organization_result_geometry(
        pieces,
        result,
        minimum_clearance=spacing,
        toolpath_offset=toolpath_offset,
    )
    return None if issues else result


def organization_sheet_index(bounds, sheet_bounds):
    """Assign overflowing geometry to its largest sheet overlap, then nearest.

    This is an organization scope, not CAM containment: a part crossing a
    sheet edge must still be movable as a whole. Ties keep the earlier sheet.
    """
    x0, y0, x1, y1 = bounds
    def score(item):
        index, (sx0, sy0, sx1, sy1) = item
        overlap = max(0., min(x1, sx1) - max(x0, sx0)) * max(0., min(y1, sy1) - max(y0, sy0))
        distance = max(sx0 - x1, x0 - sx1, 0.) ** 2 + max(sy0 - y1, y0 - sy1, 0.) ** 2
        return (-overlap, distance, index)
    return min(enumerate(sheet_bounds), key=score)[0]


def organize_pieces_in_sheet_pool(pieces, sheet_bounds, *, new_sheet_bounds, **options):
    """Pack the active sheet, then supplied empty sheets, then new sheets.

    Destination bounds are snapshots. No occupied sheet is a candidate; the
    caller supplies the first new sheet beyond the existing document layout.
    Result sheet indexes are local to the returned destination bounds.
    """
    remaining = tuple(pieces)
    available = list(tuple(map(float, bounds)) for bounds in sheet_bounds)
    if not available:
        raise ValueError("Defina a chapa ativa para organizar.")
    next_new = tuple(map(float, new_sheet_bounds))
    result = OrganizationResult()
    gap = max(50., float(options.get("spacing", 10.)) * 4.)
    while remaining:
        _check_search_cancelled(options.get("stop_requested"))
        creating = not available
        bounds = available.pop(0) if available else next_new
        page = organize_pieces(remaining, bounds, single_sheet=True, **options)
        result.evaluated_layouts += page.evaluated_layouts
        if page.placements or not result.sheet_bounds:
            index = len(result.sheet_bounds)
            result.sheet_bounds += (bounds,)
            result.placements.extend(replace(p, sheet_index=index) for p in page.placements)
            if page.used_bounds is not None:
                result.used_bounds = _union_bounds(result.used_bounds, page.used_bounds)
            result.placed_area += page.placed_area
            result.sheet_area += (bounds[2] - bounds[0]) * (bounds[3] - bounds[1])
            result.scrap_fragmentation_score += page.scrap_fragmentation_score
        if page.strategy and page.strategy not in result.strategy.split(" + "):
            result.strategy = " + ".join(filter(None, (result.strategy, page.strategy)))
        placed = {p.piece_id for p in page.placements}
        remaining = tuple(p for p in remaining if p.piece_id not in placed)
        if creating:
            if not placed:
                break  # No piece fits a fresh sheet; never allocate endlessly.
            step = bounds[2] - bounds[0] + gap
            next_new = (bounds[0] + step, bounds[1], bounds[2] + step, bounds[3])
    result.unplaced_piece_ids = [p.piece_id for p in remaining]
    result.utilization_percent = 100. * result.placed_area / result.sheet_area if result.sheet_area else 0.
    return result


def organize_pieces(
    pieces: Iterable[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float = 10.0,
    quantities: Optional[Mapping[str, int]] = None,
    rotations: Optional[Mapping[str, Sequence[float]]] = None,
    search_mode: str = "balanced",
    search_budget: Optional[Sequence[int]] = None,
    stop_requested: Optional[Callable[[], bool]] = None,
    toolpath_offset: float = 0.0,
    single_sheet: bool = False,
    prefer_remnants: bool = False,
) -> OrganizationResult:
    """Organize every fitting piece across deterministic side-by-side sheets.

    One sheet uses the real-contour raster/MaxRects competition.  If pieces
    remain, the same packing is repeated on a new virtual sheet to the right.
    A piece that cannot fit an empty sheet remains unplaced.  Quantity packing
    keeps the historical single-sheet contract because repeated instances do
    not have independent document entities in the Editor.
    ``single_sheet`` keeps all destinations inside the supplied active sheet;
    excess pieces are reported without allocating another sheet.
    """

    pieces = tuple(pieces)
    work_bounds = tuple(map(float, work_bounds))
    spacing = max(0.0, float(spacing))
    quantities = quantities or {}
    rotations = rotations or {}
    if search_mode not in _SEARCH_ORDER_BUDGETS:
        raise ValueError("O perfil de busca do nesting é inválido.")
    piece_by_id = {piece.piece_id: piece for piece in pieces}

    def finalize(result):
        result.placed_area = sum(
            max(0.0, float(piece_by_id[item.piece_id].area))
            for item in result.placements
            if item.piece_id in piece_by_id
        )
        result.sheet_area = sum(
            max(0.0, bounds[2] - bounds[0]) * max(0.0, bounds[3] - bounds[1])
            for bounds in result.sheet_bounds
        )
        result.utilization_percent = (
            0.0
            if result.sheet_area <= 0.0
            else min(100.0, result.placed_area / result.sheet_area * 100.0)
        )
        return result

    if (single_sheet and len(pieces) != 1) or any(
        max(1, int(quantities.get(piece.piece_id, 1))) > 1 for piece in pieces
    ):
        result = _organize_single_sheet(
            pieces,
            work_bounds,
            spacing,
            quantities,
            rotations,
            search_mode,
            search_budget,
            stop_requested,
            toolpath_offset,
            prefer_remnants,
        )
        result.sheet_bounds = (work_bounds,) if result.placements else ()
        return finalize(result)

    if len(pieces) == 1:
        # There is no packing decision to optimize for a single physical
        # piece. Anchor its validated orientation at the sheet datum so repeat
        # searches cannot leave it at a raster/frontier candidate that looks
        # arbitrary to the operator.
        result = _organize_single_sheet(
            pieces,
            work_bounds,
            spacing,
            {},
            rotations,
            search_mode,
            search_budget,
            stop_requested,
            toolpath_offset,
            prefer_remnants,
        )
        if result.placements:
            placement = result.placements[0]
            shift_x = float(work_bounds[0]) - float(placement.placed_bounds[0])
            shift_y = float(work_bounds[1]) - float(placement.placed_bounds[1])
            placed_bounds = (
                float(placement.placed_bounds[0]) + shift_x,
                float(placement.placed_bounds[1]) + shift_y,
                float(placement.placed_bounds[2]) + shift_x,
                float(placement.placed_bounds[3]) + shift_y,
            )
            result.placements = [
                replace(
                    placement,
                    dx=float(placement.dx) + shift_x,
                    dy=float(placement.dy) + shift_y,
                    placed_bounds=placed_bounds,
                )
            ]
            result.used_bounds = placed_bounds
        result.sheet_bounds = (work_bounds,) if result.placements else ()
        return finalize(result)

    preserved_layout = _preserve_existing_layout_candidate(
        pieces,
        work_bounds,
        spacing,
        rotations,
        toolpath_offset,
        stop_requested,
    )

    sheet_width = work_bounds[2] - work_bounds[0]
    sheet_gap = max(50.0, spacing * 4.0)

    def pack_across_sheets(current_rotations, strategy_prefix=""):
        remaining = pieces
        result = OrganizationResult()
        sheet_index = 0
        while remaining:
            _check_search_cancelled(stop_requested)
            page = _organize_single_sheet(
                remaining,
                work_bounds,
                spacing,
                {},
                current_rotations,
                search_mode,
                search_budget,
                stop_requested,
                toolpath_offset,
                prefer_remnants,
            )
            result.evaluated_layouts += page.evaluated_layouts
            result.scrap_fragmentation_score += page.scrap_fragmentation_score
            if page.strategy:
                if not result.strategy:
                    result.strategy = page.strategy
                elif page.strategy not in result.strategy.split(" + "):
                    result.strategy += " + " + page.strategy
            if not page.placements:
                result.unplaced_piece_ids.extend(
                    piece.piece_id for piece in remaining
                )
                break
            offset_x = sheet_index * (sheet_width + sheet_gap)
            translated_sheet = (
                work_bounds[0] + offset_x,
                work_bounds[1],
                work_bounds[2] + offset_x,
                work_bounds[3],
            )
            result.sheet_bounds += (translated_sheet,)
            placed_ids = set()
            for placement in page.placements:
                placed_ids.add(placement.piece_id)
                placed_bounds = (
                    placement.placed_bounds[0] + offset_x,
                    placement.placed_bounds[1],
                    placement.placed_bounds[2] + offset_x,
                    placement.placed_bounds[3],
                )
                result.placements.append(
                    PiecePlacement(
                        piece_id=placement.piece_id,
                        instance=placement.instance,
                        dx=placement.dx + offset_x,
                        dy=placement.dy,
                        rotation_degrees=placement.rotation_degrees,
                        placed_bounds=placed_bounds,
                        entity_ids=placement.entity_ids,
                        sheet_index=sheet_index,
                    )
                )
                result.used_bounds = _union_bounds(
                    result.used_bounds, placed_bounds
                )
            remaining = tuple(
                piece for piece in remaining if piece.piece_id not in placed_ids
            )
            sheet_index += 1
        if strategy_prefix:
            result.strategy = (
                strategy_prefix
                if not result.strategy
                else strategy_prefix + " — " + result.strategy
            )
        return finalize(result)

    combined = pack_across_sheets(rotations)
    orthogonal_rotations = {}
    has_non_orthogonal_angle = False
    for piece in pieces:
        allowed = tuple(rotations.get(piece.piece_id, (0.0, 90.0)))
        orthogonal = []
        for raw_angle in allowed:
            angle = float(raw_angle) % 360.0
            distance = min(
                abs(angle - cardinal)
                for cardinal in (0.0, 90.0, 180.0, 270.0, 360.0)
            )
            if distance <= 1.0e-7:
                if not any(abs(angle - value) <= 1.0e-7 for value in orthogonal):
                    orthogonal.append(angle)
            else:
                has_non_orthogonal_angle = True
        if not orthogonal:
            orthogonal_rotations = None
            break
        orthogonal_rotations[piece.piece_id] = tuple(orthogonal)
    if has_non_orthogonal_angle and orthogonal_rotations is not None:
        orthogonal_layout = pack_across_sheets(
            orthogonal_rotations,
            "ângulos ortogonais",
        )
        evaluated_layouts = (
            combined.evaluated_layouts + orthogonal_layout.evaluated_layouts
        )
        combined = min(
            (combined, orthogonal_layout),
            key=_organization_result_score,
        )
        combined.evaluated_layouts = evaluated_layouts
    if preserved_layout is not None:
        preserved_layout = finalize(preserved_layout)
        preserved_layout.evaluated_layouts += combined.evaluated_layouts
        preserved_unplaced = len(preserved_layout.unplaced_piece_ids)
        combined_unplaced = len(combined.unplaced_piece_ids)
        preserved_sheets = len(preserved_layout.sheet_bounds)
        combined_sheets = len(combined.sheet_bounds)
        improves_coverage = preserved_unplaced < combined_unplaced
        same_coverage = preserved_unplaced == combined_unplaced
        reduces_sheet_count = same_coverage and preserved_sheets < combined_sheets
        preserves_equivalent_overflow = (
            same_coverage
            and preserved_sheets > 1
            and preserved_sheets == combined_sheets
        )
        if (
            len(preserved_layout.placements) >= len(combined.placements)
            and (
                improves_coverage
                or reduces_sheet_count
                or preserves_equivalent_overflow
            )
        ):
            return preserved_layout
    return combined


__all__ = [
    "Bounds",
    "DRILL_DIAMETER_MAX_MM",
    "ClassificationResult",
    "ClassifiedLoop",
    "ClassifiedPiece",
    "OrganizationResult",
    "OrganizationSearchCancelled",
    "RectangularRemnantCut",
    "PiecePlacement",
    "circle_is_drill",
    "classify_document_pieces",
    "entity_polyline",
    "filter_pieces_for_selection",
    "organize_pieces",
    "organization_placement_transform",
    "organization_result_score",
    "suggest_rectangular_remnant_cuts",
    "validate_organization_result_geometry",
    "point_in_polygon",
    "polygon_area",
    "polygon_bounds",
]
