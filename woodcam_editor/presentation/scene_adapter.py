"""Projection bridge between VectorDocument and QGraphicsScene."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .compat import DASH_LINE, NO_BRUSH, QtCore, QtGui, QtWidgets
from .items import EntityGraphicsItem, entity_painter_path


@dataclass(frozen=True)
class SpanHit:
    entity_id: str
    span_id: str
    point: object
    parameter: float
    distance_px: float


@dataclass(frozen=True)
class NodeHit:
    entity_id: str
    node_id: str
    point: object
    distance_px: float
    span_id: str = ""
    endpoint: str = ""


@dataclass(frozen=True)
class GeometryTargetHit:
    entity_id: str
    point: object
    distance_px: float
    span_id: str = ""


class SceneAdapter:
    def __init__(self, scene, document, selection=None):
        self.scene = scene
        self.document = document
        self.selection = selection
        self.items_by_id = {}
        self._preview_ids = set()
        self.sheet_area_items = []
        self.preview_sheet_area_items = []
        self.work_area_item = QtWidgets.QGraphicsRectItem()
        pen = QtGui.QPen(QtGui.QColor("#0ea5e9"), 1.5)
        pen.setCosmetic(True)
        pen.setStyle(DASH_LINE)
        self.work_area_item.setPen(pen)
        self.work_area_item.setBrush(QtGui.QBrush(NO_BRUSH))
        self.work_area_item.setZValue(1.0)
        self.scene.addItem(self.work_area_item)
        self.set_work_area(getattr(document, "work_area", None))
        self.refresh()
        if selection is not None:
            selection.subscribe(lambda _ids: self.update_selection())

    def set_document(self, document):
        self.document = document
        self.set_work_area(getattr(document, "work_area", None))
        self.refresh()

    def entities(self):
        mapping = getattr(self.document, "entities_by_id", {})
        return mapping.values() if hasattr(mapping, "values") else mapping

    def entity(self, entity_id):
        method = getattr(self.document, "get_entity", None)
        if callable(method):
            try:
                return method(entity_id)
            except Exception:
                return None
        return getattr(self.document, "entities_by_id", {}).get(entity_id)

    def refresh(self, change_set=None):
        work_area_changed = change_set is None or bool(
            getattr(change_set, "work_area_changed", False)
        )
        if work_area_changed:
            self.set_work_area(getattr(self.document, "work_area", None))
        current = {str(entity.id): entity for entity in self.entities()}
        for entity_id in tuple(self.items_by_id):
            if entity_id not in current:
                self.scene.removeItem(self.items_by_id.pop(entity_id))
        for entity_id, entity in current.items():
            item = self.items_by_id.get(entity_id)
            if item is None:
                item = EntityGraphicsItem(entity)
                self.items_by_id[entity_id] = item
                self.scene.addItem(item)
            else:
                item.update_entity(entity)
            layer = getattr(self.document, "layers_by_id", {}).get(getattr(entity, "layer_id", None))
            item.setVisible(bool(getattr(layer, "visible", True)))
            item.editor_locked = bool(getattr(layer, "locked", False))
        self.update_selection()
        geometry_bounds_changed = change_set is None or any(
            bool(getattr(change_set, name, ()))
            for name in ("added", "changed", "removed", "layers_changed")
        )
        if geometry_bounds_changed:
            self._update_scene_rect()

    def update_selection(self):
        selected = set(self.selection.ids if self.selection is not None else ())
        for entity_id, item in self.items_by_id.items():
            item.set_editor_selected(entity_id in selected)

    def editable_visible_entity_ids(self):
        return tuple(
            entity_id
            for entity_id, item in self.items_by_id.items()
            if item.isVisible() and not getattr(item, "editor_locked", False)
        )

    def set_work_area(self, work_area):
        rect = self._work_area_rect(work_area)
        self.work_area_item.setRect(rect)
        self.work_area_item.setVisible(rect.width() > 0 and rect.height() > 0)
        self._sync_sheet_areas()
        # Deliberately keep sceneRect/camera unchanged.  Recomputing sceneRect
        # here makes QGraphicsView re-centre or clamp its scroll bars, which
        # leaves the dashed rectangle apparently fixed while entities jump.
        # Geometry refreshes and explicit Fit update sceneRect when intended.

    @staticmethod
    def _work_area_rect(work_area):
        if work_area is None:
            return QtCore.QRectF()
        try:
            return QtCore.QRectF(
                float(work_area.min_x),
                float(work_area.min_y),
                float(work_area.max_x - work_area.min_x),
                float(work_area.max_y - work_area.min_y),
            ).normalized()
        except Exception:
            if isinstance(work_area, (tuple, list)) and len(work_area) == 4:
                min_x, min_y, max_x, max_y = map(float, work_area)
                return QtCore.QRectF(
                    min_x, min_y, max_x - min_x, max_y - min_y
                ).normalized()
            return QtCore.QRectF()

    def _clear_rect_items(self, items):
        for item in items:
            self.scene.removeItem(item)
        items[:] = []

    def _append_sheet_rect(self, bounds, items, color):
        rect = self._work_area_rect(bounds)
        if rect.isNull() or rect.width() <= 0.0 or rect.height() <= 0.0:
            return
        item = QtWidgets.QGraphicsRectItem(rect)
        pen = QtGui.QPen(QtGui.QColor(color), 1.5)
        pen.setCosmetic(True)
        pen.setStyle(DASH_LINE)
        item.setPen(pen)
        item.setBrush(QtGui.QBrush(NO_BRUSH))
        item.setZValue(1.0)
        self.scene.addItem(item)
        items.append(item)

    def _sync_sheet_areas(self):
        self._clear_rect_items(self.sheet_area_items)
        values = tuple(
            (getattr(self.document, "metadata", {}) or {}).get(
                "organization_sheet_bounds", ()
            )
            or ()
        )
        # The first page is already represented by work_area_item.
        for bounds in values[1:]:
            self._append_sheet_rect(bounds, self.sheet_area_items, "#0ea5e9")

    def show_preview_sheet_bounds(self, bounds_values):
        self.clear_preview_sheet_bounds()
        for bounds in tuple(bounds_values or ())[1:]:
            self._append_sheet_rect(
                bounds, self.preview_sheet_area_items, "#d946ef"
            )
        self._update_scene_rect()

    def clear_preview_sheet_bounds(self):
        self._clear_rect_items(self.preview_sheet_area_items)

    def _update_scene_rect(self):
        rect = self.work_area_item.rect() if self.work_area_item.isVisible() else QtCore.QRectF()
        for item in self.sheet_area_items + self.preview_sheet_area_items:
            bounds = item.rect()
            rect = rect.united(bounds) if not rect.isNull() else bounds
        for item in self.items_by_id.values():
            bounds = item.mapToScene(item.path()).boundingRect()
            rect = rect.united(bounds) if not rect.isNull() else bounds
        if rect.isNull() or rect.width() <= 0.0 or rect.height() <= 0.0:
            rect = QtCore.QRectF(-500.0, -500.0, 1000.0, 1000.0)
        margin = max(50.0, max(rect.width(), rect.height()) * 0.08)
        self.scene.setSceneRect(rect.adjusted(-margin, -margin, margin, margin))

    def hit_test_body(self, view, screen_pos, radius_px=7.0):
        screen_point = QtCore.QPointF(screen_pos)
        candidates = []
        transform = view.viewportTransform()
        for entity_id, item in self.items_by_id.items():
            if not item.isVisible() or getattr(item, "editor_locked", False):
                continue
            scene_path = item.mapToScene(item.path())
            screen_path = transform.map(scene_path)
            stroker = QtGui.QPainterPathStroker()
            stroker.setWidth(radius_px * 2.0)
            hit_path = stroker.createStroke(screen_path).united(screen_path)
            if hit_path.contains(screen_point):
                center = screen_path.boundingRect().center()
                distance = abs(center.x() - screen_point.x()) + abs(center.y() - screen_point.y())
                candidates.append((distance, -item.zValue(), entity_id))
        return min(candidates)[2] if candidates else None

    def _entity_is_editable_visible(self, entity):
        layer = getattr(self.document, "layers_by_id", {}).get(getattr(entity, "layer_id", None))
        return bool(layer is None or (layer.visible and not layer.locked))

    def hit_test_span(self, view, screen_pos, radius_px=9.0, excluded_ids=()):
        scene_point = view.mapToScene(screen_pos)
        candidates = []
        excluded = set(str(value) for value in excluded_ids)
        for entity in self.entities():
            if str(entity.id) in excluded:
                continue
            if not self._entity_is_editable_visible(entity):
                continue
            spans = tuple(getattr(entity, "spans", ()) or ())
            for span in spans:
                try:
                    model_point = type(span.start)(scene_point.x(), scene_point.y())
                    nearest = span.nearest_point(model_point)
                    viewport_point = view.mapFromScene(
                        QtCore.QPointF(nearest.point.x, nearest.point.y)
                    )
                    distance = math.hypot(
                        viewport_point.x() - screen_pos.x(),
                        viewport_point.y() - screen_pos.y(),
                    )
                except Exception:
                    continue
                if distance <= radius_px:
                    candidates.append(
                        SpanHit(
                            str(entity.id),
                            str(span.id),
                            nearest.point,
                            float(nearest.parameter),
                            distance,
                        )
                    )
        return min(candidates, key=lambda value: value.distance_px) if candidates else None

    def hit_test_geometry_target(self, view, screen_pos, radius_px=10.0, excluded_ids=()):
        excluded = set(str(value) for value in excluded_ids)
        candidates = []
        span_hit = self.hit_test_span(
            view, screen_pos, radius_px=radius_px, excluded_ids=excluded
        )
        if span_hit is not None:
            candidates.append(
                GeometryTargetHit(
                    span_hit.entity_id,
                    span_hit.point,
                    span_hit.distance_px,
                    span_hit.span_id,
                )
            )
        scene_point = view.mapToScene(screen_pos)
        for entity in self.entities():
            if str(entity.id) in excluded or not self._entity_is_editable_visible(entity):
                continue
            if type(entity).__name__ not in ("CircleEntity", "EllipseEntity"):
                continue
            try:
                from woodcam_editor.geometry.modifiers import project_point_to_geometry

                model_point = type(entity.center)(scene_point.x(), scene_point.y())
                projection = project_point_to_geometry(model_point, entity)
                viewport_point = view.mapFromScene(
                    QtCore.QPointF(projection.point.x, projection.point.y)
                )
                distance = math.hypot(
                    viewport_point.x() - screen_pos.x(),
                    viewport_point.y() - screen_pos.y(),
                )
            except Exception:
                continue
            if distance <= radius_px:
                candidates.append(
                    GeometryTargetHit(
                        str(entity.id), projection.point, distance, ""
                    )
                )
        return min(candidates, key=lambda value: value.distance_px) if candidates else None

    def hit_test_path_node(self, view, screen_pos, radius_px=11.0, closed_only=False):
        candidates = []
        for entity in self.entities():
            if not self._entity_is_editable_visible(entity):
                continue
            if not getattr(entity, "spans", None) or (closed_only and not entity.closed):
                continue
            nodes_method = getattr(entity, "nodes", None)
            if not callable(nodes_method):
                continue
            for node in nodes_method():
                viewport_point = view.mapFromScene(QtCore.QPointF(node.point.x, node.point.y))
                distance = math.hypot(
                    viewport_point.x() - screen_pos.x(),
                    viewport_point.y() - screen_pos.y(),
                )
                if distance <= radius_px:
                    candidates.append(
                        NodeHit(str(entity.id), str(node.id), node.point, distance)
                    )
        return min(candidates, key=lambda value: value.distance_px) if candidates else None

    def hit_test_open_endpoint(self, view, screen_pos, radius_px=12.0):
        candidates = []
        for entity in self.entities():
            if not self._entity_is_editable_visible(entity):
                continue
            spans = tuple(getattr(entity, "spans", ()) or ())
            if not spans or getattr(entity, "closed", False):
                continue
            endpoint_specs = (
                (entity.node_ids[0], spans[0].start, spans[0].id, "start"),
                (entity.node_ids[-1], spans[-1].end, spans[-1].id, "end"),
            )
            for node_id, point, span_id, endpoint in endpoint_specs:
                viewport_point = view.mapFromScene(QtCore.QPointF(point.x, point.y))
                distance = math.hypot(
                    viewport_point.x() - screen_pos.x(),
                    viewport_point.y() - screen_pos.y(),
                )
                if distance <= radius_px:
                    candidates.append(
                        NodeHit(
                            str(entity.id), str(node_id), point, distance,
                            str(span_id), endpoint,
                        )
                    )
        return min(candidates, key=lambda value: value.distance_px) if candidates else None

    def select_in_screen_rect(self, view, first_screen, current_screen, crossing=False):
        rect = QtCore.QRectF(QtCore.QPointF(first_screen), QtCore.QPointF(current_screen)).normalized()
        transform = view.viewportTransform()
        selected = []
        for entity_id, item in self.items_by_id.items():
            if not item.isVisible() or getattr(item, "editor_locked", False):
                continue
            screen_path = transform.map(item.mapToScene(item.path()))
            bounds = screen_path.boundingRect()
            matches = rect.intersects(bounds) if crossing else rect.contains(bounds)
            if matches:
                selected.append(entity_id)
        return tuple(selected)

    def preview_translation(self, entity_ids, delta):
        dx = delta.x() if callable(getattr(delta, "x", None)) else delta.x
        dy = delta.y() if callable(getattr(delta, "y", None)) else delta.y
        self.clear_preview()
        for entity_id in entity_ids:
            item = self.items_by_id.get(str(entity_id))
            if item is not None:
                item.setPos(float(dx), float(dy))
                self._preview_ids.add(str(entity_id))

    def preview_entity(self, entity_id, entity):
        self.clear_preview()
        item = self.items_by_id.get(str(entity_id))
        if item is not None and entity is not None:
            item.setPath(entity_painter_path(entity))
            self._preview_ids.add(str(entity_id))

    def clear_preview(self):
        for entity_id in tuple(self._preview_ids):
            item = self.items_by_id.get(entity_id)
            entity = self.entity(entity_id)
            if item is not None and entity is not None:
                item.update_entity(entity)
        self._preview_ids.clear()


__all__ = ["GeometryTargetHit", "NodeHit", "SceneAdapter", "SpanHit"]
