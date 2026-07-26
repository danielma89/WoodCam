"""Screen-tolerance snapping for the vector editor.

The engine consumes domain entities through their public geometry API.  It is
deliberately independent from Qt: the caller supplies the current number of
screen pixels per millimetre.  Therefore a 10 px capture radius feels the same
at 25% and at 800% zoom.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math
from typing import Any, Iterable, Iterator, List, Optional, Sequence, Set, Tuple


class SnapKind(str, Enum):
    ENDPOINT = "endpoint"
    INTERSECTION = "intersection"
    CENTER = "center"
    MIDPOINT = "midpoint"
    QUADRANT = "quadrant"
    ON_GEOMETRY = "on_geometry"
    GRID = "grid"
    HORIZONTAL = "horizontal"
    VERTICAL = "vertical"
    ANGLE = "angle"
    PERPENDICULAR = "perpendicular"
    TANGENT = "tangent"
    WORK_AREA = "work_area"


_LABELS = {
    SnapKind.ENDPOINT: "Fim",
    SnapKind.INTERSECTION: "Interseção",
    SnapKind.CENTER: "Centro",
    SnapKind.MIDPOINT: "Meio",
    SnapKind.QUADRANT: "Quadrante",
    SnapKind.ON_GEOMETRY: "Na geometria",
    SnapKind.GRID: "Grade",
    SnapKind.HORIZONTAL: "Horizontal",
    SnapKind.VERTICAL: "Vertical",
    SnapKind.ANGLE: "Ângulo",
    SnapKind.PERPENDICULAR: "Perpendicular",
    SnapKind.TANGENT: "Tangente",
    SnapKind.WORK_AREA: "Área de Trabalho",
}

_PRIORITY = {
    SnapKind.ENDPOINT: 10,
    SnapKind.INTERSECTION: 20,
    SnapKind.CENTER: 30,
    SnapKind.QUADRANT: 31,
    SnapKind.MIDPOINT: 32,
    SnapKind.HORIZONTAL: 40,
    SnapKind.VERTICAL: 41,
    SnapKind.ANGLE: 42,
    SnapKind.PERPENDICULAR: 43,
    SnapKind.TANGENT: 44,
    SnapKind.ON_GEOMETRY: 50,
    SnapKind.WORK_AREA: 55,
    SnapKind.GRID: 60,
}


@dataclass(frozen=True)
class SnapCandidate:
    point: Any
    kind: SnapKind
    entity_id: Optional[str] = None
    span_id: Optional[str] = None
    parameter: Optional[float] = None
    distance_px: float = 0.0
    priority: int = 100
    label: str = ""


@dataclass
class SnapSettings:
    radius_px: float = 10.0
    endpoint: bool = True
    intersection: bool = True
    midpoint: bool = True
    center: bool = True
    quadrant: bool = True
    on_geometry: bool = True
    grid: bool = True
    grid_spacing_mm: float = 10.0
    # Smart snaps are measured from the active drawing/measurement anchor.
    # They are screen-radius candidates just like endpoint/grid snaps, never
    # hidden constraints in the document.
    horizontal: bool = True
    vertical: bool = True
    angle: bool = True
    angle_increment_degrees: float = 15.0
    perpendicular: bool = True
    tangent: bool = True


def _xy(point: Any) -> Tuple[float, float]:
    if point is None:
        raise TypeError("point is None")
    x = point.x() if callable(getattr(point, "x", None)) else point.x
    y = point.y() if callable(getattr(point, "y", None)) else point.y
    return float(x), float(y)


def _make_like(reference: Any, x: float, y: float) -> Any:
    try:
        return type(reference)(float(x), float(y))
    except Exception:
        try:
            from woodcam_editor.domain import Vec2

            return Vec2(float(x), float(y))
        except Exception:
            return (float(x), float(y))


def _document_entities(document: Any) -> Iterable[Any]:
    mapping = getattr(document, "entities_by_id", None)
    if mapping is None:
        mapping = getattr(document, "entities", {})
    return mapping.values() if hasattr(mapping, "values") else mapping


def _entity_spans(entity: Any) -> Tuple[Any, ...]:
    return tuple(getattr(entity, "spans", ()) or ())


def _entity_id(entity: Any) -> Optional[str]:
    value = getattr(entity, "id", None)
    return None if value is None else str(value)


def _span_id(span: Any) -> Optional[str]:
    value = getattr(span, "id", None)
    return None if value is None else str(value)


class SnapEngine:
    """Ranks geometric snap candidates using a screen-space radius."""

    def __init__(self, settings: Optional[SnapSettings] = None) -> None:
        self.settings = settings or SnapSettings()

    def find(
        self,
        point: Any,
        document: Any,
        pixels_per_mm: float,
        excluded_entity_ids: Iterable[str] = (),
        disabled: bool = False,
        reference_point: Any = None,
    ) -> Optional[SnapCandidate]:
        if disabled or pixels_per_mm <= 0.0:
            return None
        excluded = {str(value) for value in excluded_entity_ids}
        candidates: List[SnapCandidate] = []
        entities = tuple(
            entity
            for entity in _document_entities(document)
            if self._entity_is_editable_visible(document, entity)
        )
        for entity in entities:
            if _entity_id(entity) in excluded:
                continue
            candidates.extend(self._entity_candidates(point, entity, pixels_per_mm))
        if self.settings.intersection:
            candidates.extend(
                self._intersection_candidates(point, entities, pixels_per_mm)
            )
        if self.settings.grid and self.settings.grid_spacing_mm > 1e-12:
            x, y = _xy(point)
            spacing = self.settings.grid_spacing_mm
            candidates.append(
                self._candidate(
                    point,
                    _make_like(point, round(x / spacing) * spacing, round(y / spacing) * spacing),
                    SnapKind.GRID,
                    pixels_per_mm,
                )
            )
        if reference_point is not None:
            candidates.extend(
                self._smart_candidates(
                    point,
                    reference_point,
                    pixels_per_mm,
                    entities,
                    excluded,
                )
            )
        candidates = [
            candidate
            for candidate in candidates
            if candidate.distance_px <= self.settings.radius_px
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda value: (value.priority, value.distance_px))

    def _smart_candidates(
        self,
        point: Any,
        reference_point: Any,
        pixels_per_mm: float,
        entities: Sequence[Any],
        excluded: Set[str],
    ) -> List[SnapCandidate]:
        """Generate temporary Ortho/angle candidates from one known anchor.

        The candidates have no entity IDs and live only under the cursor.  A
        drawing tool passes its first/last confirmed point, so a loose mouse
        movement cannot change a vector or create an implicit constraint.
        ``distance_px`` remains the sole capture gate, which makes this feel
        identical at every zoom level.
        """

        px, py = _xy(point)
        rx, ry = _xy(reference_point)
        candidates: List[SnapCandidate] = []
        if self.settings.horizontal:
            candidates.append(
                self._candidate(
                    point,
                    _make_like(point, px, ry),
                    SnapKind.HORIZONTAL,
                    pixels_per_mm,
                )
            )
        if self.settings.vertical:
            candidates.append(
                self._candidate(
                    point,
                    _make_like(point, rx, py),
                    SnapKind.VERTICAL,
                    pixels_per_mm,
                )
            )
        increment = float(self.settings.angle_increment_degrees or 0.0)
        if self.settings.angle and 1.0e-9 < increment < 180.0:
            dx, dy = px - rx, py - ry
            length = math.hypot(dx, dy)
            if length > 1.0e-12:
                step = math.radians(increment)
                raw_angle = math.atan2(dy, dx)
                snapped_angle = round(raw_angle / step) * step
                # Horizontal/vertical already have their own stronger,
                # clearer labels.  Do not create duplicate candidates there.
                quarter_turn = math.pi * 0.5
                distance_to_axis = abs((snapped_angle + quarter_turn * 0.5) % quarter_turn - quarter_turn * 0.5)
                if distance_to_axis > 1.0e-8:
                    target = _make_like(
                        point,
                        rx + length * math.cos(snapped_angle),
                        ry + length * math.sin(snapped_angle),
                    )
                    candidate = self._candidate(
                        point, target, SnapKind.ANGLE, pixels_per_mm
                    )
                    # Include the actual snapped increment in the status
                    # instead of a vague generic "angle" label.
                    candidate = SnapCandidate(
                        point=candidate.point,
                        kind=candidate.kind,
                        entity_id=candidate.entity_id,
                        span_id=candidate.span_id,
                        parameter=candidate.parameter,
                        distance_px=candidate.distance_px,
                        priority=candidate.priority,
                        label="Ângulo %.0f°" % math.degrees(snapped_angle),
                    )
                    candidates.append(candidate)
        candidates.extend(
            self._reference_geometry_candidates(
                point, reference_point, pixels_per_mm, entities, excluded
            )
        )
        return candidates

    def _reference_geometry_candidates(
        self,
        point: Any,
        reference_point: Any,
        pixels_per_mm: float,
        entities: Sequence[Any],
        excluded: Set[str],
    ) -> List[SnapCandidate]:
        """Return exact perpendicular/tangent candidates from the anchor.

        This deliberately starts with the two unambiguous primitives useful
        for cabinetry: a perpendicular foot to a finite line, and radial or
        tangent points on an exact circle.  Ellipse and arbitrary Bézier
        tangency need a numerical solver and are left out rather than guessed.
        """

        candidates: List[SnapCandidate] = []
        rx, ry = _xy(reference_point)
        for entity in entities:
            entity_id = _entity_id(entity)
            if entity_id in excluded:
                continue
            for span in _entity_spans(entity):
                if type(span).__name__ != "LineSpan" or not self.settings.perpendicular:
                    if type(span).__name__ != "ArcSpan":
                        continue
                    candidates.extend(
                        self._arc_reference_candidates(
                            point,
                            reference_point,
                            span,
                            pixels_per_mm,
                            entity_id,
                        )
                    )
                    continue
                sx, sy = _xy(span.start)
                ex, ey = _xy(span.end)
                dx, dy = ex - sx, ey - sy
                length_sq = dx * dx + dy * dy
                if length_sq <= 1.0e-12:
                    continue
                parameter = ((rx - sx) * dx + (ry - sy) * dy) / length_sq
                # The source is a finite vector, never an infinite imaginary
                # extension.  This avoids a surprising snap beyond its end.
                if parameter < -1.0e-12 or parameter > 1.0 + 1.0e-12:
                    continue
                target = _make_like(
                    reference_point,
                    sx + dx * parameter,
                    sy + dy * parameter,
                )
                candidates.append(
                    self._candidate(
                        point,
                        target,
                        SnapKind.PERPENDICULAR,
                        pixels_per_mm,
                        entity_id,
                        _span_id(span),
                        parameter,
                    )
                )

            center = getattr(entity, "center", None)
            radius = getattr(entity, "radius", None)
            if center is None or radius is None:
                continue
            cx, cy = _xy(center)
            radius = float(radius)
            vx, vy = rx - cx, ry - cy
            distance = math.hypot(vx, vy)
            if radius <= 1.0e-12 or distance <= 1.0e-12:
                continue
            ux, uy = vx / distance, vy / distance
            if self.settings.perpendicular:
                candidates.append(
                    self._candidate(
                        point,
                        _make_like(reference_point, cx + radius * ux, cy + radius * uy),
                        SnapKind.PERPENDICULAR,
                        pixels_per_mm,
                        entity_id,
                    )
                )
            if self.settings.tangent and distance > radius + 1.0e-12:
                base = math.atan2(vy, vx)
                offset = math.acos(radius / distance)
                for angle in (base + offset, base - offset):
                    candidates.append(
                        self._candidate(
                            point,
                            _make_like(
                                reference_point,
                                cx + radius * math.cos(angle),
                                cy + radius * math.sin(angle),
                            ),
                            SnapKind.TANGENT,
                            pixels_per_mm,
                            entity_id,
                        )
                    )
        return candidates

    def _arc_reference_candidates(
        self,
        point: Any,
        reference_point: Any,
        span: Any,
        pixels_per_mm: float,
        entity_id: Optional[str],
    ) -> List[SnapCandidate]:
        """Reference candidates restricted to the actual ArcSpan sweep."""

        center = getattr(span, "center", None)
        radius = getattr(span, "radius", None)
        if center is None or radius is None:
            return []
        cx, cy = _xy(center)
        rx, ry = _xy(reference_point)
        radius = float(radius)
        vx, vy = rx - cx, ry - cy
        distance = math.hypot(vx, vy)
        if radius <= 1.0e-12 or distance <= 1.0e-12:
            return []
        result: List[SnapCandidate] = []

        def add_if_on_arc(target, kind):
            nearest, parameter = self._nearest(span, target)
            if nearest is None:
                return
            tx, ty = _xy(target)
            nx, ny = _xy(nearest)
            if math.hypot(tx - nx, ty - ny) > 1.0e-7:
                return
            result.append(
                self._candidate(
                    point,
                    target,
                    kind,
                    pixels_per_mm,
                    entity_id,
                    _span_id(span),
                    parameter,
                )
            )

        ux, uy = vx / distance, vy / distance
        if self.settings.perpendicular:
            add_if_on_arc(
                _make_like(reference_point, cx + radius * ux, cy + radius * uy),
                SnapKind.PERPENDICULAR,
            )
        if self.settings.tangent and distance > radius + 1.0e-12:
            base = math.atan2(vy, vx)
            offset = math.acos(radius / distance)
            for angle in (base + offset, base - offset):
                add_if_on_arc(
                    _make_like(
                        reference_point,
                        cx + radius * math.cos(angle),
                        cy + radius * math.sin(angle),
                    ),
                    SnapKind.TANGENT,
                )
        return result

    @staticmethod
    def _entity_is_editable_visible(document: Any, entity: Any) -> bool:
        layer = getattr(document, "layers_by_id", {}).get(getattr(entity, "layer_id", None))
        return bool(layer is None or (layer.visible and not layer.locked))

    def _intersection_candidates(
        self,
        point: Any,
        entities: Sequence[Any],
        pixels_per_mm: float,
    ) -> List[SnapCandidate]:
        """Line-line intersections in the local screen-radius neighbourhood."""
        from woodcam_editor.geometry.math2d import segment_intersections

        px, py = _xy(point)
        world_radius = self.settings.radius_px / max(1e-12, pixels_per_mm)
        nearby = []
        for entity in entities:
            for span in _entity_spans(entity):
                if type(span).__name__ != "LineSpan":
                    continue
                sx, sy = _xy(span.start)
                ex, ey = _xy(span.end)
                if (
                    max(sx, ex) < px - world_radius
                    or min(sx, ex) > px + world_radius
                    or max(sy, ey) < py - world_radius
                    or min(sy, ey) > py + world_radius
                ):
                    continue
                nearby.append((entity, span))
        result = []
        seen = set()
        for left_index, (left_entity, left_span) in enumerate(nearby):
            for right_entity, right_span in nearby[left_index + 1 :]:
                if left_span.id == right_span.id:
                    continue
                for intersection in segment_intersections(
                    left_span.start,
                    left_span.end,
                    right_span.start,
                    right_span.end,
                    1.0e-9,
                ):
                    if intersection.kind == "overlap":
                        continue
                    key = (round(intersection.point.x, 10), round(intersection.point.y, 10))
                    if key in seen:
                        continue
                    seen.add(key)
                    result.append(
                        self._candidate(
                            point,
                            intersection.point,
                            SnapKind.INTERSECTION,
                            pixels_per_mm,
                            _entity_id(left_entity),
                            _span_id(left_span),
                            getattr(intersection, "parameter_a", None),
                        )
                    )
        return result

    def snapped_point(self, point: Any, *args: Any, **kwargs: Any) -> Tuple[Any, Optional[SnapCandidate]]:
        candidate = self.find(point, *args, **kwargs)
        return (candidate.point, candidate) if candidate is not None else (point, None)

    def _candidate(
        self,
        origin: Any,
        target: Any,
        kind: SnapKind,
        pixels_per_mm: float,
        entity_id: Optional[str] = None,
        span_id: Optional[str] = None,
        parameter: Optional[float] = None,
    ) -> SnapCandidate:
        ox, oy = _xy(origin)
        tx, ty = _xy(target)
        return SnapCandidate(
            point=target,
            kind=kind,
            entity_id=entity_id,
            span_id=span_id,
            parameter=parameter,
            distance_px=math.hypot(tx - ox, ty - oy) * pixels_per_mm,
            priority=_PRIORITY[kind],
            label=_LABELS[kind],
        )

    def _entity_candidates(self, point: Any, entity: Any, pixels_per_mm: float) -> List[SnapCandidate]:
        candidates: List[SnapCandidate] = []
        entity_id = _entity_id(entity)
        spans = _entity_spans(entity)
        if spans:
            for index, span in enumerate(spans):
                span_id = _span_id(span)
                start = getattr(span, "start", None)
                end = getattr(span, "end", None)
                if self.settings.endpoint:
                    if start is not None and index == 0:
                        candidates.append(self._candidate(point, start, SnapKind.ENDPOINT, pixels_per_mm, entity_id, span_id, 0.0))
                    if end is not None:
                        candidates.append(self._candidate(point, end, SnapKind.ENDPOINT, pixels_per_mm, entity_id, span_id, 1.0))
                if self.settings.midpoint:
                    midpoint = self._point_at(span, 0.5)
                    if midpoint is not None:
                        candidates.append(self._candidate(point, midpoint, SnapKind.MIDPOINT, pixels_per_mm, entity_id, span_id, 0.5))
                if self.settings.center and getattr(span, "center", None) is not None:
                    candidates.append(self._candidate(point, span.center, SnapKind.CENTER, pixels_per_mm, entity_id, span_id))
                if self.settings.on_geometry:
                    nearest, parameter = self._nearest(span, point)
                    if nearest is not None:
                        candidates.append(self._candidate(point, nearest, SnapKind.ON_GEOMETRY, pixels_per_mm, entity_id, span_id, parameter))
            return candidates

        center = getattr(entity, "center", None)
        radius = getattr(entity, "radius", None)
        if center is not None and radius is not None:
            cx, cy = _xy(center)
            radius = float(radius)
            if self.settings.center:
                candidates.append(self._candidate(point, center, SnapKind.CENTER, pixels_per_mm, entity_id))
            if self.settings.quadrant:
                for dx, dy in ((radius, 0.0), (0.0, radius), (-radius, 0.0), (0.0, -radius)):
                    candidates.append(self._candidate(point, _make_like(center, cx + dx, cy + dy), SnapKind.QUADRANT, pixels_per_mm, entity_id))
            if self.settings.on_geometry:
                px, py = _xy(point)
                length = math.hypot(px - cx, py - cy)
                if length > 1e-12:
                    target = _make_like(center, cx + (px - cx) * radius / length, cy + (py - cy) * radius / length)
                    candidates.append(self._candidate(point, target, SnapKind.ON_GEOMETRY, pixels_per_mm, entity_id))
        return candidates

    @staticmethod
    def _point_at(span: Any, parameter: float) -> Any:
        method = getattr(span, "point_at", None)
        if callable(method):
            try:
                return method(parameter)
            except Exception:
                pass
        start = getattr(span, "start", None)
        end = getattr(span, "end", None)
        if start is None or end is None:
            return None
        sx, sy = _xy(start)
        ex, ey = _xy(end)
        return _make_like(start, sx + (ex - sx) * parameter, sy + (ey - sy) * parameter)

    @staticmethod
    def _nearest(span: Any, point: Any) -> Tuple[Any, Optional[float]]:
        method = getattr(span, "nearest_point", None)
        if not callable(method):
            return None, None
        try:
            result = method(point)
        except Exception:
            return None, None
        if result is None:
            return None, None
        if hasattr(result, "point"):
            return result.point, getattr(result, "parameter", getattr(result, "t", None))
        if isinstance(result, tuple) and result:
            if len(result) > 1 and isinstance(result[1], (int, float)):
                return result[0], float(result[1])
            return result[0], None
        return result, None


__all__ = ["SnapCandidate", "SnapEngine", "SnapKind", "SnapSettings"]
