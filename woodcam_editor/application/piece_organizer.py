"""Pure 2D piece classification and deterministic MaxRects organization.

The organizer treats an outer loop and every nested loop as one rigid unit.
It intentionally returns a preview plan instead of mutating a document; the
controller turns the accepted plan into one atomic command.  Packing evaluates
bottom-left free rectangles, deterministic input orders and allowed rotations.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from woodcam_editor.domain.document import entity_is_cam_eligible


Point = Tuple[float, float]
Bounds = Tuple[float, float, float, float]


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


@dataclass
class ClassificationResult:
    loops: Dict[str, ClassifiedLoop] = field(default_factory=dict)
    pieces: List[ClassifiedPiece] = field(default_factory=list)
    open_entity_ids: List[str] = field(default_factory=list)
    rejected_entity_ids: List[str] = field(default_factory=list)


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
    for entity_id, entity in _document_entities(document).items():
        if not include_non_cam_layers and not entity_is_cam_eligible(document, entity):
            continue
        points = entity_polyline(entity, deflection)
        if not _is_closed(entity, points, tolerance):
            if points:
                result.open_entity_ids.append(str(entity_id))
            continue
        if len(points) > 1 and _distance(points[0], points[-1]) <= tolerance:
            points = points[:-1]
        area = polygon_area(points)
        if len(points) < 3 or abs(area) <= tolerance * tolerance:
            result.rejected_entity_ids.append(str(entity_id))
            continue
        raw[str(entity_id)] = (entity, points, polygon_bounds(points), area)

    parents: Dict[str, Optional[str]] = {}
    for entity_id, (_entity, points, bounds, area) in raw.items():
        probe = points[0]
        entity_batch = str(
            (getattr(_entity, "metadata", {}) or {}).get("import_batch_id", "")
        )
        containers = []
        for candidate_id, (_other, candidate_points, candidate_bounds, candidate_area) in raw.items():
            if candidate_id == entity_id or abs(candidate_area) <= abs(area):
                continue
            candidate_batch = str(
                (getattr(_other, "metadata", {}) or {}).get("import_batch_id", "")
            )
            if entity_batch and candidate_batch and entity_batch != candidate_batch:
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
    for index, root in enumerate(sorted(roots, key=lambda item: item.entity_id), start=1):
        descendants = [
            loop for loop in result.loops.values()
            if _root_id(loop.entity_id, parents) == root.entity_id and loop.entity_id != root.entity_id
        ]
        immediate_holes = tuple(
            loop.entity_id for loop in descendants
            if loop.parent_id == root.entity_id and loop.depth % 2 == 1
        )
        result.pieces.append(
            ClassifiedPiece(
                piece_id=f"piece-{index:03d}-{root.entity_id}",
                outer_id=root.entity_id,
                inner_ids=immediate_holes,
                descendant_ids=tuple(loop.entity_id for loop in descendants),
                bounds=root.bounds,
                area=abs(root.area),
                outer_points=root.points,
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
        for x_value, y_value in (
            (min_x, min_y),
            (max_x, min_y),
            (max_x, max_y),
            (min_x, max_y),
        ):
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
    return tuple(result)


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
                score = (
                    round(used_height, 12),
                    round(used_width * used_height, 12),
                    round(used_width, 12),
                    round(y_value, 12),
                    round(x_value, 12),
                    round(free_waste, 12),
                    round(option.angle, 12),
                )
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


_SEARCH_ORDER_BUDGETS = {
    "fast": (4, 2),
    "balanced": (12, 6),
    "thorough": (24, 12),
}


def _stable_order_hash(item: _PackInstance, salt: int) -> int:
    """Return a process-independent hash for reproducible search orders."""

    value = 1469598103934665603
    payload = "%d|%s|%d" % (salt, item.piece.piece_id, item.instance)
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
    return tuple(result)


def _organize_pieces_rectangular(
    pieces: Iterable[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float = 10.0,
    quantities: Optional[Mapping[str, int]] = None,
    rotations: Optional[Mapping[str, Sequence[float]]] = None,
    max_orders: int = 4,
) -> OrganizationResult:
    """Return a deterministic compact bottom-left placement preview.

    Each classified piece is a rigid unit: the outer contour and every nested
    contour receive the same placement.  Candidates are generated from the
    bottom/left work-area boundary and the top/right edges of already placed
    boxes.  Every permitted rotation is compared at every candidate.  Several
    deterministic size orders are evaluated and the result that places the
    most material with the lowest used height/area is returned.

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
    for ordered in orders:
        packed, unplaced, used = _pack_bottom_left(
            ordered,
            (min_x, min_y, max_x, max_y),
            spacing,
            rotations,
        )
        used_width = 0.0 if used is None else used[2] - used[0]
        used_height = 0.0 if used is None else used[3] - used[1]
        placed_source_area = sum(box.item.source_area for box in packed)
        score = (
            len(unplaced),
            -round(placed_source_area, 12),
            round(used_height, 12),
            round(used_width * used_height, 12),
            round(used_width, 12),
            _layout_signature(packed),
        )
        layouts.append((score, packed, unplaced, used))

    _score, packed, unplaced, used = min(layouts, key=lambda value: value[0])
    result = OrganizationResult(
        unplaced_piece_ids=[item.piece.piece_id for item in unplaced],
        used_bounds=used,
        strategy="MaxRects",
        evaluated_layouts=len(orders),
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
                entity_ids=(piece.outer_id,) + piece.descendant_ids,
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
    return resolution


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


def _rasterize_polygon_bits(
    points: Sequence[Point],
    width: float,
    height: float,
    resolution: float,
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
    for (x1, y1), (x2, y2) in edges:
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
) -> Tuple[_RasterOrientation, ...]:
    result = []
    for option in _rotation_options(piece, allowed):
        points, option = _rotate_outer_points(piece, option.angle)
        row_bits, rows, cols, area_pixels = _rasterize_polygon_bits(
            points,
            option.width,
            option.height,
            resolution,
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


def _find_raster_position(
    sheet_bits: Sequence[int],
    sheet_rows: int,
    sheet_cols: int,
    mask: _RasterOrientation,
    work_bounds: Bounds,
    spacing: float,
) -> Optional[Tuple[int, int]]:
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
        return None
    first_mask_row = next(
        ((index, bits) for index, bits in enumerate(mask.row_bits) if bits),
        None,
    )
    if first_mask_row is None:
        return None
    first_offset, first_bits = first_mask_row
    for row in range(min_row, max_row + 1):
        quick_row = sheet_bits[row + first_offset]
        for col in range(min_col, max_col + 1):
            if quick_row & (first_bits << col):
                continue
            if _raster_mask_fits(sheet_bits, mask, row, col):
                return row, col
    return None


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
        expanded = source_bits
        for distance in range(1, spacing_pixels + 1):
            expanded |= (source_bits << distance) | (source_bits >> distance)
        expanded = (expanded << col) & clip
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
) -> Tuple[List[_RasterPacked], List[_PackInstance], Optional[Bounds]]:
    width = work_bounds[2] - work_bounds[0]
    height = work_bounds[3] - work_bounds[1]
    sheet_cols = int(math.floor(width / resolution + 1e-12))
    sheet_rows = int(math.floor(height / resolution + 1e-12))
    sheet_bits = [0] * sheet_rows
    spacing_pixels = int(math.ceil(spacing / resolution - 1e-12))
    packed: List[_RasterPacked] = []
    unplaced: List[_PackInstance] = []

    for item in ordered:
        best = None
        for orientation in orientation_by_piece[item.piece.piece_id]:
            position = _find_raster_position(
                sheet_bits,
                sheet_rows,
                sheet_cols,
                orientation,
                work_bounds,
                spacing,
            )
            if position is None:
                continue
            row, col = position
            x_value = work_bounds[0] + col * resolution
            y_value = work_bounds[1] + row * resolution
            bounds = (
                x_value,
                y_value,
                x_value + orientation.exact_width,
                y_value + orientation.exact_height,
            )
            used = _bounds_union([value.bounds for value in packed] + [bounds])
            used_width = used[2] - used[0]
            used_height = used[3] - used[1]
            score = (
                round(used_height, 12),
                round(used_width * used_height, 12),
                round(used_width, 12),
                row,
                col,
                round(orientation.angle, 12),
            )
            if best is None or score < best[0]:
                best = (score, orientation, row, col, bounds)
        if best is None:
            unplaced.append(item)
            continue
        _score, orientation, row, col, bounds = best
        _stamp_raster_mask(
            sheet_bits,
            sheet_rows,
            sheet_cols,
            orientation,
            row,
            col,
            spacing_pixels,
        )
        packed.append(_RasterPacked(item, orientation, row, col, bounds))

    return packed, unplaced, _bounds_union([value.bounds for value in packed])


def _organize_pieces_raster(
    pieces: Sequence[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float,
    quantities: Mapping[str, int],
    rotations: Mapping[str, Sequence[float]],
    max_orders: int = 2,
) -> Optional[OrganizationResult]:
    if not pieces or any(len(piece.outer_points) < 3 for piece in pieces):
        return None
    if sum(len(piece.outer_points) for piece in pieces) > 50000:
        return None
    resolution = _raster_resolution(pieces, work_bounds, spacing)
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
        orientations = _raster_orientations(
            piece,
            rotations.get(piece.piece_id, (0.0, 90.0)),
            resolution,
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
    layouts = []
    for ordered in orders:
        packed, unplaced, used = _pack_raster_order(
            ordered,
            work_bounds,
            spacing,
            orientation_by_piece,
            resolution,
        )
        used_width = 0.0 if used is None else used[2] - used[0]
        used_height = 0.0 if used is None else used[3] - used[1]
        placed_area = sum(value.item.source_area for value in packed)
        score = (
            len(unplaced),
            -round(placed_area, 12),
            round(used_height, 12),
            round(used_width * used_height, 12),
            round(used_width, 12),
            _raster_layout_signature(packed),
        )
        layouts.append((score, packed, unplaced, used))
    if not layouts:
        return None

    _score, packed, unplaced, used = min(layouts, key=lambda value: value[0])
    result = OrganizationResult(
        unplaced_piece_ids=[value.piece.piece_id for value in unplaced],
        used_bounds=used,
        strategy="contorno real",
        evaluated_layouts=len(orders),
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
                entity_ids=(piece.outer_id,) + piece.descendant_ids,
            )
        )
    return result


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
    return (
        len(result.unplaced_piece_ids),
        -len(result.placements),
        round(height, 12),
        round(width * height, 12),
        round(width, 12),
        signature,
    )


def _organize_single_sheet(
    pieces: Iterable[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float = 10.0,
    quantities: Optional[Mapping[str, int]] = None,
    rotations: Optional[Mapping[str, Sequence[float]]] = None,
    search_mode: str = "balanced",
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
    rectangular_orders, raster_orders = _SEARCH_ORDER_BUDGETS[search_mode]
    rectangular = _organize_pieces_rectangular(
        pieces,
        work_bounds,
        spacing,
        quantities,
        rotations,
        rectangular_orders,
    )
    raster = _organize_pieces_raster(
        pieces,
        tuple(map(float, work_bounds)),
        spacing,
        quantities,
        rotations,
        raster_orders,
    )
    if raster is None:
        return rectangular
    evaluated = rectangular.evaluated_layouts + raster.evaluated_layouts
    selected = (
        raster
        if _organization_result_score(raster) <= _organization_result_score(rectangular)
        else rectangular
    )
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


def organize_pieces(
    pieces: Iterable[ClassifiedPiece],
    work_bounds: Bounds,
    spacing: float = 10.0,
    quantities: Optional[Mapping[str, int]] = None,
    rotations: Optional[Mapping[str, Sequence[float]]] = None,
    search_mode: str = "balanced",
) -> OrganizationResult:
    """Organize every fitting piece across deterministic side-by-side sheets.

    One sheet uses the real-contour raster/MaxRects competition.  If pieces
    remain, the same packing is repeated on a new virtual sheet to the right.
    A piece that cannot fit an empty sheet remains unplaced.  Quantity packing
    keeps the historical single-sheet contract because repeated instances do
    not have independent document entities in the Editor.
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

    if any(max(1, int(quantities.get(piece.piece_id, 1))) > 1 for piece in pieces):
        result = _organize_single_sheet(
            pieces, work_bounds, spacing, quantities, rotations, search_mode
        )
        result.sheet_bounds = (work_bounds,) if result.placements else ()
        return finalize(result)

    remaining = pieces
    combined = OrganizationResult()
    sheet_width = work_bounds[2] - work_bounds[0]
    sheet_gap = max(50.0, spacing * 4.0)
    sheet_index = 0
    while remaining:
        page = _organize_single_sheet(
            remaining, work_bounds, spacing, {}, rotations, search_mode
        )
        combined.evaluated_layouts += page.evaluated_layouts
        if page.strategy:
            if not combined.strategy:
                combined.strategy = page.strategy
            elif page.strategy not in combined.strategy.split(" + "):
                combined.strategy += " + " + page.strategy
        if not page.placements:
            combined.unplaced_piece_ids.extend(
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
        combined.sheet_bounds += (translated_sheet,)
        placed_ids = set()
        for placement in page.placements:
            placed_ids.add(placement.piece_id)
            placed_bounds = (
                placement.placed_bounds[0] + offset_x,
                placement.placed_bounds[1],
                placement.placed_bounds[2] + offset_x,
                placement.placed_bounds[3],
            )
            combined.placements.append(
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
            combined.used_bounds = _union_bounds(
                combined.used_bounds, placed_bounds
            )
        remaining = tuple(
            piece for piece in remaining if piece.piece_id not in placed_ids
        )
        sheet_index += 1
    return finalize(combined)


__all__ = [
    "Bounds",
    "ClassificationResult",
    "ClassifiedLoop",
    "ClassifiedPiece",
    "OrganizationResult",
    "PiecePlacement",
    "classify_document_pieces",
    "entity_polyline",
    "organize_pieces",
    "point_in_polygon",
    "polygon_area",
    "polygon_bounds",
]
