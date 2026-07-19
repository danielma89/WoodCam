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
        candidates = [
            candidate
            for candidate in candidates
            if candidate.distance_px <= self.settings.radius_px
        ]
        if not candidates:
            return None
        return min(candidates, key=lambda value: (value.priority, value.distance_px))

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
