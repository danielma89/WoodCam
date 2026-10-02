"""Projection bridge between VectorDocument and QGraphicsScene."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .compat import DASH_LINE, NO_BRUSH, QtCore, QtGui, QtWidgets, qt_enum
from .i18n import translate_text
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
        # Selection is session state.  Keep its rendered counterpart so a
        # Ctrl+A on a large imported layout does not walk every QGraphicsItem
        # just to discover that its pen already has the right colour.
        self._rendered_selection = set()
        self._preview_ids = set()
        self.sheet_area_items = []
        self.preview_sheet_area_items = []
        # Page labels are presentation-only.  The persisted source of truth is
        # still ``organization_sheet_bounds``; a label must never become a
        # drawable vector or affect nesting/CAM selection.
        self.sheet_label_items = []
        self.preview_sheet_label_items = []
        self.remnant_cut_items = []
        self.remnant_label_items = []
        self.remnant_labels_by_entity_id = {}
        self.preview_remnant_cut_items = []
        self.preview_remnant_label_items = []
        self._preview_remnant_values = ()
        self.work_area_item = QtWidgets.QGraphicsRectItem()
        pen = QtGui.QPen(QtGui.QColor("#0ea5e9"), 1.5)
        pen.setCosmetic(True)
        pen.setStyle(DASH_LINE)
        self.work_area_item.setPen(pen)
        # A subtle sheet fill makes the usable material unmistakable without
        # hiding vectors.  Geometry remains above it; the surrounding canvas
        # stays neutral, like a CAD/CAM nesting sheet.
        self._work_area_fill = QtGui.QColor("#bae6fd")
        self._work_area_fill.setAlpha(62)
        self.work_area_item.setBrush(QtGui.QBrush(self._work_area_fill))
        self.work_area_item.setZValue(-10.0)
        self.scene.addItem(self.work_area_item)
        self.set_work_area(getattr(document, "work_area", None))
        self.refresh()
        if selection is not None:
            selection.subscribe(lambda _ids: self.update_selection())

    def set_work_area_fill_visible(self, visible):
        """Toggle only the material-page tint; geometry remains untouched."""

        self.work_area_item.setBrush(
            QtGui.QBrush(self._work_area_fill)
            if visible
            else QtGui.QBrush(NO_BRUSH)
        )

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
        """Synchronize only the visible projection affected by a command.

        ``Piece2D`` and document-metadata commands do not change vector
        geometry.  Rebuilding every ``QGraphicsPathItem`` after those commands
        made a large imported cabinet appear to freeze even though recognising
        pieces itself had already finished.  The document/change-set remains
        the authority: this method merely avoids repainting unrelated scene
        projections.
        """
        work_area_changed = change_set is None or bool(
            getattr(change_set, "work_area_changed", False)
        )
        if work_area_changed:
            self.set_work_area(getattr(self.document, "work_area", None))
        current = {str(entity.id): entity for entity in self.entities()}
        if change_set is None:
            removed_ids = tuple(
                entity_id
                for entity_id in self.items_by_id
                if entity_id not in current
            )
            sync_ids = set(current)
        else:
            # A command names every entity whose persisted geometry changed.
            # Layer edits are handled separately below without rebuilding paths.
            removed_ids = tuple(getattr(change_set, "removed", ()) or ())
            sync_ids = set(getattr(change_set, "added", ()) or ())
            sync_ids.update(getattr(change_set, "changed", ()) or ())
        remnant_entities_changed = any(
            str(entity_id) in self.remnant_labels_by_entity_id
            or str(
                (getattr(current.get(str(entity_id)), "metadata", {}) or {}).get(
                    "woodcam_role", ""
                )
            ).strip().lower() == "remnant_cut"
            for entity_id in set(removed_ids) | sync_ids
        )
        changed_layers = (
            set()
            if change_set is None
            else set(getattr(change_set, "layers_changed", ()) or ())
        )
        for entity_id in tuple(str(value) for value in removed_ids):
            item = self.items_by_id.pop(entity_id, None)
            if item is not None:
                self.scene.removeItem(item)
                self._rendered_selection.discard(entity_id)
        for entity_id in sorted(str(value) for value in sync_ids):
            entity = current.get(entity_id)
            if entity is None:
                continue
            item = self.items_by_id.get(entity_id)
            if item is None:
                item = EntityGraphicsItem(entity)
                self.items_by_id[entity_id] = item
                self.scene.addItem(item)
            else:
                item.update_entity(entity)
            layer = getattr(self.document, "layers_by_id", {}).get(getattr(entity, "layer_id", None))
            # GroupEntity is a persistent relationship, not duplicated scene
            # geometry.  Its child vectors remain the only drawable items.
            is_group = type(entity).__name__ == "GroupEntity"
            item.setVisible(not is_group and bool(getattr(layer, "visible", True)))
            item.editor_locked = bool(getattr(layer, "locked", False))
        if work_area_changed or remnant_entities_changed:
            # Metadata and entity creation may arrive in the same composite
            # command. Re-evaluate after new EntityGraphicsItems exist so a
            # persisted remnant vector is not drawn twice as an overlay line.
            self._sync_remnant_cuts()
        # Layer visibility/locking changes no geometry. Update the existing
        # QGraphicsItems in place; rebuilding every QPainterPath and scanning
        # scene bounds turned a checkbox into a full-scene refresh.
        if changed_layers:
            for entity_id, entity in current.items():
                if str(getattr(entity, "layer_id", "")) not in changed_layers:
                    continue
                item = self.items_by_id.get(entity_id)
                if item is None:
                    continue
                layer = getattr(self.document, "layers_by_id", {}).get(
                    getattr(entity, "layer_id", None)
                )
                is_group = type(entity).__name__ == "GroupEntity"
                item.setVisible(
                    not is_group and bool(getattr(layer, "visible", True))
                )
                item.editor_locked = bool(getattr(layer, "locked", False))
        self.update_selection()
        geometry_bounds_changed = change_set is None or any(
            bool(getattr(change_set, name, ()))
            for name in ("added", "changed", "removed")
        )
        requires_full_scene_bounds = change_set is None or any(
            bool(getattr(change_set, name, ()))
            for name in ("added", "removed")
        )
        if requires_full_scene_bounds:
            self._update_scene_rect(
                preserve_camera=change_set is not None,
                keep_existing=change_set is not None,
            )
        elif geometry_bounds_changed:
            # Dragging one item used to scan every imported vector again on
            # mouse release.  Scene bounds only need to grow for an ordinary
            # move/edit; leaving harmless empty space behind is preferable to
            # a visible pause after every confirmed command.
            self._expand_scene_rect_for(
                getattr(change_set, "changed", ())
            )

    def update_selection(self):
        selected = set(self._expanded_selection_ids(
            self.selection.ids if self.selection is not None else ()
        ))
        selected.intersection_update(self.items_by_id)
        changed = selected.symmetric_difference(self._rendered_selection)
        for entity_id in changed:
            item = self.items_by_id.get(entity_id)
            if item is not None:
                item.set_editor_selected(entity_id in selected)
        self._rendered_selection = selected
        for entity_id, label in self.remnant_labels_by_entity_id.items():
            item = self.items_by_id.get(entity_id)
            label.setVisible(
                entity_id in selected and item is not None and item.isVisible()
            )

    def _expanded_selection_ids(self, entity_ids):
        """Map selected persistent groups to their visible leaf vectors."""

        mapping = getattr(self.document, "entities_by_id", {}) or {}
        result = set()
        visited = set()

        def visit(entity_id):
            entity_id = str(entity_id)
            if entity_id in visited:
                return
            visited.add(entity_id)
            entity = mapping.get(entity_id)
            if type(entity).__name__ == "GroupEntity":
                for child_id in getattr(entity, "child_ids", ()):
                    visit(child_id)
            elif entity_id in self.items_by_id:
                result.add(entity_id)

        for entity_id in entity_ids:
            visit(entity_id)
        return result

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
        self._sync_remnant_cuts()
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

    def _append_sheet_rect(self, bounds, items, color, label_items=None, label=""):
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
        if label_items is not None and label:
            text = QtWidgets.QGraphicsSimpleTextItem(str(label))
            text.setBrush(QtGui.QBrush(QtGui.QColor(color)))
            font = text.font()
            font.setBold(True)
            font.setPointSize(9)
            text.setFont(font)
            # The view deliberately flips Y for the CAD coordinate system.
            # Keep page names upright and screen-readable at every zoom.
            ignore_transform = qt_enum(
                QtWidgets.QGraphicsItem,
                "ItemIgnoresTransformations",
                "GraphicsItemFlag",
            )
            text.setFlag(ignore_transform, True)
            text.setPos(rect.left() + 7.0, rect.top() + 7.0)
            text.setZValue(2.0)
            text.setAcceptedMouseButtons(
                QtCore.Qt.NoButton
                if hasattr(QtCore.Qt, "NoButton")
                else QtCore.Qt.MouseButton.NoButton
            )
            self.scene.addItem(text)
            label_items.append(text)

    def _sync_sheet_areas(self):
        self._clear_rect_items(self.sheet_area_items)
        self._clear_rect_items(self.sheet_label_items)
        values = tuple(
            (getattr(self.document, "metadata", {}) or {}).get(
                "organization_sheet_bounds", ()
            )
            or ()
        )
        # The first page is already represented by work_area_item, but it
        # receives the same visual identity as the virtual pages alongside it.
        for index, bounds in enumerate(values, start=1):
            if index == 1:
                rect = self._work_area_rect(bounds)
                if rect.width() > 0.0 and rect.height() > 0.0:
                    # Keep the first material boundary explicit after an
                    # organization command; metadata refreshes must not make
                    # the table outline appear to disappear.
                    self._append_sheet_rect(
                        bounds,
                        self.sheet_area_items,
                        "#0ea5e9",
                    )
                    self._append_sheet_label(
                        rect,
                        self.sheet_label_items,
                        "#0369a1",
                        translate_text("Chapa %02d  ·  X0 Y0") % 1,
                    )
                continue
            self._append_sheet_rect(
                bounds,
                self.sheet_area_items,
                "#0ea5e9",
                self.sheet_label_items,
                translate_text("Chapa %02d  ·  X0 Y0") % index,
            )

    def show_preview_sheet_bounds(self, bounds_values):
        self.clear_preview_sheet_bounds()
        for index, bounds in enumerate(tuple(bounds_values or ()), start=1):
            if index == 1:
                rect = self._work_area_rect(bounds)
                if rect.width() > 0.0 and rect.height() > 0.0:
                    self._append_sheet_label(
                        rect,
                        self.preview_sheet_label_items,
                        "#d946ef",
                        translate_text("Chapa %02d — prévia  ·  X0 Y0") % 1,
                    )
                continue
            self._append_sheet_rect(
                bounds,
                self.preview_sheet_area_items,
                "#d946ef",
                self.preview_sheet_label_items,
                translate_text("Chapa %02d — prévia  ·  X0 Y0") % index,
            )
        self._update_scene_rect()

    def clear_preview_sheet_bounds(self):
        self._clear_rect_items(self.preview_sheet_area_items)
        self._clear_rect_items(self.preview_sheet_label_items)

    @staticmethod
    def _remnant_cut_values(raw):
        try:
            start = tuple(map(float, raw["start"]))
            end = tuple(map(float, raw["end"]))
            bounds = tuple(map(float, raw["remnant_bounds"]))
            area = float(raw.get("area", 0.0))
            if len(start) != 2 or len(end) != 2 or len(bounds) != 4:
                return None
            if not all(
                math.isfinite(value) for value in start + end + bounds + (area,)
            ):
                return None
            if bounds[2] <= bounds[0] or bounds[3] <= bounds[1] or area < 0.0:
                return None
            return start, end, bounds, area
        except (KeyError, TypeError, ValueError):
            return None

    def _append_remnant_cut(self, raw, line_items, label_items, color, draw_line=True):
        values = self._remnant_cut_values(raw)
        if values is None:
            return
        start, end, bounds, area = values
        rect = self._work_area_rect(bounds)
        label_text = translate_text("Retalho %.0f × %.0f mm · %.2f m²") % (
            rect.width(), rect.height(), area / 1_000_000.0
        )
        if draw_line:
            line = QtWidgets.QGraphicsLineItem(
                start[0], start[1], end[0], end[1]
            )
            pen = QtGui.QPen(QtGui.QColor(color), 2.2)
            pen.setCosmetic(True)
            pen.setStyle(DASH_LINE)
            line.setPen(pen)
            line.setToolTip(label_text)
            line.setAcceptHoverEvents(True)
            line.setZValue(3.0)
            line.setAcceptedMouseButtons(
                QtCore.Qt.NoButton
                if hasattr(QtCore.Qt, "NoButton")
                else QtCore.Qt.MouseButton.NoButton
            )
            self.scene.addItem(line)
            line_items.append(line)

        if isinstance(raw, dict) and not bool(raw.get("show_label", True)):
            return

        label = QtWidgets.QGraphicsSimpleTextItem(label_text)
        label.setBrush(QtGui.QBrush(QtGui.QColor(color)))
        font = label.font()
        font.setBold(True)
        font.setPointSize(9)
        label.setFont(font)
        label.setFlag(
            qt_enum(
                QtWidgets.QGraphicsItem,
                "ItemIgnoresTransformations",
                "GraphicsItemFlag",
            ),
            True,
        )
        label.setPos(rect.left() + 7.0, rect.top() + 7.0)
        label.setZValue(3.0)
        label.setAcceptedMouseButtons(
            QtCore.Qt.NoButton
            if hasattr(QtCore.Qt, "NoButton")
            else QtCore.Qt.MouseButton.NoButton
        )
        self.scene.addItem(label)
        label_items.append(label)
        label.setVisible(False)
        return label

    def _sync_remnant_cuts(self):
        self._clear_rect_items(self.remnant_cut_items)
        self._clear_rect_items(self.remnant_label_items)
        self.remnant_labels_by_entity_id.clear()
        values = (getattr(self.document, "metadata", {}) or {}).get(
            "organization_remnant_cuts", ()
        ) or ()
        for raw in values:
            entity_id = (
                str(raw.get("entity_id", "") or "")
                if isinstance(raw, dict)
                else ""
            )
            # The vector is authoritative. Old metadata must not resurrect a
            # deleted cut or leave its label behind after Undo/Redo/reload.
            item = self.items_by_id.get(entity_id)
            if item is None:
                continue
            entity = (getattr(self.document, "entities_by_id", {}) or {}).get(entity_id)
            if str((getattr(entity, "metadata", {}) or {}).get("woodcam_role", "")).strip().lower() != "remnant_cut":
                continue
            label = self._append_remnant_cut(
                raw,
                self.remnant_cut_items,
                self.remnant_label_items,
                "#ea580c",
                draw_line=False,
            )
            if label is not None:
                self.remnant_labels_by_entity_id[entity_id] = label
                item.setToolTip(label.text())
                item.setAcceptHoverEvents(True)

    def show_preview_remnant_cuts(self, values):
        self.clear_preview_remnant_cuts()
        self._preview_remnant_values = tuple(values or ())
        for raw in self._preview_remnant_values:
            self._append_remnant_cut(
                raw,
                self.preview_remnant_cut_items,
                self.preview_remnant_label_items,
                "#c026d3",
            )

    def clear_preview_remnant_cuts(self):
        self._clear_rect_items(self.preview_remnant_cut_items)
        self._clear_rect_items(self.preview_remnant_label_items)
        self._preview_remnant_values = ()

    def refresh_language(self):
        """Rebuild only presentation text when WoodCAM changes language."""

        preview_values = self._preview_remnant_values
        self._sync_remnant_cuts()
        if preview_values:
            self.show_preview_remnant_cuts(preview_values)
        self.update_selection()

    def _append_sheet_label(self, rect, label_items, color, label):
        """Add an upright, non-interactive label to an existing page rect."""

        text = QtWidgets.QGraphicsSimpleTextItem(str(label))
        text.setBrush(QtGui.QBrush(QtGui.QColor(color)))
        font = text.font()
        font.setBold(True)
        font.setPointSize(9)
        text.setFont(font)
        ignore_transform = qt_enum(
            QtWidgets.QGraphicsItem,
            "ItemIgnoresTransformations",
            "GraphicsItemFlag",
        )
        text.setFlag(ignore_transform, True)
        text.setPos(rect.left() + 7.0, rect.top() + 7.0)
        text.setZValue(2.0)
        text.setAcceptedMouseButtons(
            QtCore.Qt.NoButton
            if hasattr(QtCore.Qt, "NoButton")
            else QtCore.Qt.MouseButton.NoButton
        )
        self.scene.addItem(text)
        label_items.append(text)

    def _set_scene_rect_preserving_camera(self, rect, preserve_camera=False):
        anchors = []
        target = QtCore.QRectF(rect)
        if preserve_camera:
            for view in tuple(self.scene.views() or ()):
                viewport = view.viewport()
                visible = view.mapToScene(viewport.rect()).boundingRect()
                # Activating a scrollbar while a primitive is confirmed can
                # change Qt's alignment from centered to scrollable before
                # ``centerOn`` has enough range to restore the old camera.
                # Keep one viewport of navigation margin around what the
                # operator was looking at; this is presentation state only.
                target = target.united(
                    visible.adjusted(
                        -visible.width(),
                        -visible.height(),
                        visible.width(),
                        visible.height(),
                    )
                )
                anchors.append(
                    (
                        view,
                        view.mapToScene(viewport.rect().center()),
                    )
                )
        self.scene.setSceneRect(target)
        for view, center in anchors:
            view.centerOn(center)

    def _update_scene_rect(self, preserve_camera=False, keep_existing=False):
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
        target = rect.adjusted(-margin, -margin, margin, margin)
        if keep_existing and not self.scene.sceneRect().isNull():
            target = target.united(self.scene.sceneRect())
        self._set_scene_rect_preserving_camera(target, preserve_camera)

    def _expand_scene_rect_for(self, entity_ids):
        current = self.scene.sceneRect()
        changed_bounds = QtCore.QRectF()
        for entity_id in tuple(entity_ids or ()):
            item = self.items_by_id.get(str(entity_id))
            if item is None:
                continue
            bounds = item.mapToScene(item.path()).boundingRect()
            changed_bounds = (
                bounds
                if changed_bounds.isNull()
                else changed_bounds.united(bounds)
            )
        if changed_bounds.isNull():
            return
        if current.isNull():
            self._update_scene_rect()
            return
        if current.contains(changed_bounds):
            return
        expanded = current.united(changed_bounds)
        margin = max(50.0, max(changed_bounds.width(), changed_bounds.height()) * 0.08)
        self._set_scene_rect_preserving_camera(
            expanded.adjusted(-margin, -margin, margin, margin),
            preserve_camera=True,
        )

    def hit_test_body(self, view, screen_pos, radius_px=7.0):
        screen_point = QtCore.QPointF(screen_pos)
        candidates = []
        transform = view.viewportTransform()
        scene_point = view.mapToScene(screen_point.toPoint())
        radius_mm = float(radius_px) / max(1.0e-12, view.pixels_per_mm())
        query = QtCore.QRectF(
            scene_point.x() - radius_mm,
            scene_point.y() - radius_mm,
            radius_mm * 2.0,
            radius_mm * 2.0,
        )
        mode = getattr(QtCore.Qt, "IntersectsItemBoundingRect", None)
        if mode is None:
            mode = QtCore.Qt.ItemSelectionMode.IntersectsItemBoundingRect
        nearby_items = self.scene.items(query, mode)
        for item in nearby_items:
            if not isinstance(item, EntityGraphicsItem):
                continue
            entity_id = item.entity_id
            if not item.isVisible() or getattr(item, "editor_locked", False):
                continue
            scene_path = item.mapToScene(item.path())
            screen_path = transform.map(scene_path)
            is_pocket_region = bool(
                getattr(item, "_is_pocket_region", False)
            )
            if is_pocket_region and screen_path.contains(screen_point):
                # A pocket is an explicit machining area.  Its hatching is the
                # intentional hit surface and wins over an external contour
                # that may share part of the same boundary.
                candidates.append((0, 0.0, -item.zValue(), entity_id))
                continue
            stroker = QtGui.QPainterPathStroker()
            stroker.setWidth(radius_px * 2.0)
            # A closed painter path is filled for ``contains``.  Selection in
            # Aspire is contour based: the inside of a rectangle/circle is
            # background, not a hidden selection surface.
            hit_path = stroker.createStroke(screen_path)
            if hit_path.contains(screen_point):
                center = screen_path.boundingRect().center()
                distance = abs(center.x() - screen_point.x()) + abs(center.y() - screen_point.y())
                candidates.append(
                    (
                        0 if is_pocket_region else 1,
                        distance,
                        -item.zValue(),
                        entity_id,
                    )
                )
        return min(candidates)[3] if candidates else None

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
        # Query Qt's spatial index first.  Modifier tools call this method for
        # every mouse packet; walking every node in a complete nesting made a
        # manual T-bone progressively slower as the drawing grew.
        screen_point = QtCore.QPointF(screen_pos)
        scene_point = view.mapToScene(screen_point.toPoint())
        radius_mm = float(radius_px) / max(1.0e-12, view.pixels_per_mm())
        query = QtCore.QRectF(
            scene_point.x() - radius_mm,
            scene_point.y() - radius_mm,
            radius_mm * 2.0,
            radius_mm * 2.0,
        )
        mode = getattr(QtCore.Qt, "IntersectsItemBoundingRect", None)
        if mode is None:
            mode = QtCore.Qt.ItemSelectionMode.IntersectsItemBoundingRect
        nearby_ids = []
        for item in self.scene.items(query, mode):
            if not isinstance(item, EntityGraphicsItem):
                continue
            if not item.isVisible() or getattr(item, "editor_locked", False):
                continue
            if item.entity_id not in nearby_ids:
                nearby_ids.append(item.entity_id)

        candidates = []
        for entity_id in nearby_ids:
            entity = self.document.get_entity(entity_id)
            if entity is None:
                continue
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
        scene_polygon = view.mapToScene(rect.toAlignedRect())
        mode = getattr(QtCore.Qt, "IntersectsItemBoundingRect", None)
        if mode is None:
            mode = QtCore.Qt.ItemSelectionMode.IntersectsItemBoundingRect
        nearby_items = self.scene.items(scene_polygon.boundingRect(), mode)
        def segment_touches_rect(first, second):
            if rect.contains(first) or rect.contains(second):
                return True
            dx = float(second.x() - first.x())
            dy = float(second.y() - first.y())
            start_x = float(first.x())
            start_y = float(first.y())
            minimum_x, maximum_x = float(rect.left()), float(rect.right())
            minimum_y, maximum_y = float(rect.top()), float(rect.bottom())
            lower, upper = 0.0, 1.0
            for coefficient, offset in (
                (-dx, start_x - minimum_x), (dx, maximum_x - start_x),
                (-dy, start_y - minimum_y), (dy, maximum_y - start_y),
            ):
                if abs(coefficient) <= 1.0e-12:
                    if offset < 0.0:
                        return False
                    continue
                parameter = offset / coefficient
                if coefficient < 0.0:
                    lower = max(lower, parameter)
                else:
                    upper = min(upper, parameter)
                if lower > upper:
                    return False
            return True

        def polygon_matches(polygon):
            points = tuple(polygon)
            if not points:
                return False
            if not crossing:
                return all(rect.contains(point) for point in points)
            return any(
                segment_touches_rect(first, second)
                for first, second in zip(points, points[1:])
            )
        for item in nearby_items:
            if not isinstance(item, EntityGraphicsItem):
                continue
            entity_id = item.entity_id
            if not item.isVisible() or getattr(item, "editor_locked", False):
                continue
            screen_path = transform.map(item.mapToScene(item.path()))
            # A marquee is about real vector geometry, not its bounding box.
            # Flattened screen polygons retain the contour-only contract even
            # for closed paths, whose Qt painter path otherwise has a filled
            # interior and would falsely intersect a small empty marquee.
            matches = bool(screen_path.toSubpathPolygons()) and all(
                polygon_matches(polygon)
                for polygon in screen_path.toSubpathPolygons()
            ) if not crossing else any(
                polygon_matches(polygon)
                for polygon in screen_path.toSubpathPolygons()
            )
            if matches:
                selected.append(entity_id)
        return tuple(selected)

    def preview_translation(self, entity_ids, delta):
        dx = delta.x() if callable(getattr(delta, "x", None)) else delta.x
        dy = delta.y() if callable(getattr(delta, "y", None)) else delta.y
        self.clear_preview()
        for entity_id in self._expanded_selection_ids(entity_ids):
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
