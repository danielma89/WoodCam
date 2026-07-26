"""Transient nodes, measurements, snap markers and gesture previews."""

from __future__ import annotations

import math

from .compat import DASH_LINE, NO_BRUSH, QtCore, QtGui, QtWidgets, qt_enum
from .items import entity_painter_path


def _xy(point):
    x = point.x() if callable(getattr(point, "x", None)) else point.x
    y = point.y() if callable(getattr(point, "y", None)) else point.y
    return float(x), float(y)


def _point(point):
    x, y = _xy(point)
    return QtCore.QPointF(x, y)


IGNORE_TRANSFORM = qt_enum(QtWidgets.QGraphicsItem, "ItemIgnoresTransformations", "GraphicsItemFlag")


class NodeHandleItem(QtWidgets.QGraphicsEllipseItem):
    def __init__(self, entity_id, node_id, position, primary=False, kind="node"):
        super(NodeHandleItem, self).__init__(-4.5, -4.5, 9.0, 9.0)
        self.entity_id = str(entity_id)
        self.node_id = str(node_id)
        self.kind = str(kind)
        self.setPos(_point(position))
        self.setFlag(IGNORE_TRANSFORM, True)
        self.setZValue(110.0)
        pen = QtGui.QPen(QtGui.QColor("#1d4ed8" if self.kind == "bezier_control" else "#991b1b"), 1.3)
        pen.setCosmetic(True)
        self.setPen(pen)
        fill = "#bfdbfe" if self.kind == "bezier_control" else ("#fde68a" if primary else "#fecaca")
        self.setBrush(QtGui.QBrush(QtGui.QColor(fill)))
        self.setAcceptedMouseButtons(QtCore.Qt.NoButton if hasattr(QtCore.Qt, "NoButton") else QtCore.Qt.MouseButton.NoButton)

    def set_node_selected(self, selected):
        if self.kind == "bezier_control":
            color = "#60a5fa" if selected else "#bfdbfe"
        else:
            color = "#fbbf24" if selected else "#fecaca"
        self.setBrush(QtGui.QBrush(QtGui.QColor(color)))


class MeasureBubbleItem(QtWidgets.QGraphicsItem):
    def __init__(self):
        super(MeasureBubbleItem, self).__init__()
        self._text = ""
        self._font = QtGui.QFont()
        self._font.setPointSize(9)
        self.setFlag(IGNORE_TRANSFORM, True)
        self.setZValue(140.0)
        self.hide()

    def set_text(self, text):
        self.prepareGeometryChange()
        self._text = str(text)
        self.update()

    def boundingRect(self):
        metrics = QtGui.QFontMetricsF(self._font)
        bounds = metrics.boundingRect(self._text)
        return QtCore.QRectF(12.0, -bounds.height() - 22.0, bounds.width() + 16.0, bounds.height() + 10.0)

    def paint(self, painter, option, widget=None):
        rect = self.boundingRect()
        painter.setPen(QtGui.QPen(QtGui.QColor("#1d4ed8"), 1.0))
        painter.setBrush(QtGui.QBrush(QtGui.QColor(255, 255, 255, 238)))
        painter.drawRoundedRect(rect, 4.0, 4.0)
        painter.setFont(self._font)
        painter.setPen(QtGui.QPen(QtGui.QColor("#0f172a")))
        painter.drawText(rect.adjusted(8.0, 4.0, -8.0, -4.0), QtCore.Qt.AlignLeft if hasattr(QtCore.Qt, "AlignLeft") else QtCore.Qt.AlignmentFlag.AlignLeft, self._text)


class OverlayLayer:
    def __init__(self, scene):
        self.scene = scene
        self.node_items = []
        # Aspire-like selection decoration. These are transient projections;
        # the handles never capture the mouse and do not mutate the document.
        self.selection_frame_item = QtWidgets.QGraphicsRectItem()
        selection_pen = QtGui.QPen(QtGui.QColor("#111827"), 1.2)
        selection_pen.setCosmetic(True)
        selection_pen.setStyle(DASH_LINE)
        self.selection_frame_item.setPen(selection_pen)
        self.selection_frame_item.setBrush(QtGui.QBrush(NO_BRUSH))
        self.selection_frame_item.setZValue(104.0)
        self.selection_frame_item.setAcceptedMouseButtons(
            QtCore.Qt.NoButton
            if hasattr(QtCore.Qt, "NoButton")
            else QtCore.Qt.MouseButton.NoButton
        )
        self.scene.addItem(self.selection_frame_item)
        self.selection_frame_item.hide()
        self.selection_handle_items = []
        self.selection_pivot_item = QtWidgets.QGraphicsEllipseItem(-8.0, -8.0, 16.0, 16.0)
        self.selection_pivot_item.setFlag(IGNORE_TRANSFORM, True)
        pivot_pen = QtGui.QPen(QtGui.QColor("#a21caf"), 1.2)
        pivot_pen.setCosmetic(True)
        pivot_pen.setStyle(DASH_LINE)
        self.selection_pivot_item.setPen(pivot_pen)
        self.selection_pivot_item.setBrush(QtGui.QBrush(NO_BRUSH))
        self.selection_pivot_item.setZValue(105.0)
        self.selection_pivot_item.setAcceptedMouseButtons(
            QtCore.Qt.NoButton
            if hasattr(QtCore.Qt, "NoButton")
            else QtCore.Qt.MouseButton.NoButton
        )
        self.scene.addItem(self.selection_pivot_item)
        self.selection_pivot_item.hide()
        # CAM plan-view paths are a view-only layer.  They intentionally do
        # not create entities or participate in hit-testing: the same
        # persisted moves that feed G-code are simply projected over the 2D
        # vectors, like Aspire's toolpath preview.
        self.toolpath_items = {}
        toolpath_specs = (
            # Aspire-like plan-view contrast: rapid travel is magenta and
            # dashed, while material-cutting moves stay light slate so the
            # rose vectors remain legible underneath.
            ("rapid", "#d946ef", 1.0, DASH_LINE),
            ("ramp", "#94a3b8", 1.2, None),
            ("cut", "#64748b", 1.15, None),
            ("corner", "#475569", 1.35, None),
        )
        for key, color, width, style in toolpath_specs:
            item = QtWidgets.QGraphicsPathItem()
            pen = QtGui.QPen(QtGui.QColor(color), width)
            pen.setCosmetic(True)
            if style is not None:
                pen.setStyle(style)
            item.setPen(pen)
            item.setBrush(QtGui.QBrush(NO_BRUSH))
            item.setZValue(75.0)
            item.setAcceptedMouseButtons(
                QtCore.Qt.NoButton
                if hasattr(QtCore.Qt, "NoButton")
                else QtCore.Qt.MouseButton.NoButton
            )
            self.scene.addItem(item)
            item.hide()
            self.toolpath_items[key] = item

        self.toolpath_entry_item = QtWidgets.QGraphicsPathItem()
        entry_pen = QtGui.QPen(QtGui.QColor("#d946ef"), 1.3)
        entry_pen.setCosmetic(True)
        self.toolpath_entry_item.setPen(entry_pen)
        self.toolpath_entry_item.setBrush(QtGui.QBrush(QtGui.QColor("#d1fae5")))
        self.toolpath_entry_item.setZValue(76.0)
        self.toolpath_entry_item.setAcceptedMouseButtons(
            QtCore.Qt.NoButton
            if hasattr(QtCore.Qt, "NoButton")
            else QtCore.Qt.MouseButton.NoButton
        )
        self.scene.addItem(self.toolpath_entry_item)
        self.toolpath_entry_item.hide()

        self.toolpath_direction_item = QtWidgets.QGraphicsPathItem()
        direction_pen = QtGui.QPen(QtGui.QColor("#334155"), 1.0)
        direction_pen.setCosmetic(True)
        self.toolpath_direction_item.setPen(direction_pen)
        self.toolpath_direction_item.setBrush(QtGui.QBrush(QtGui.QColor("#334155")))
        self.toolpath_direction_item.setZValue(77.0)
        self.toolpath_direction_item.setAcceptedMouseButtons(
            QtCore.Qt.NoButton
            if hasattr(QtCore.Qt, "NoButton")
            else QtCore.Qt.MouseButton.NoButton
        )
        self.scene.addItem(self.toolpath_direction_item)
        self.toolpath_direction_item.hide()

        self.preview_item = QtWidgets.QGraphicsPathItem()
        preview_pen = QtGui.QPen(QtGui.QColor("#d946ef"), 1.8)
        preview_pen.setCosmetic(True)
        preview_pen.setStyle(DASH_LINE)
        self.preview_item.setPen(preview_pen)
        self.preview_item.setBrush(QtGui.QBrush(NO_BRUSH))
        self.preview_item.setZValue(90.0)
        self.scene.addItem(self.preview_item)
        self.preview_item.hide()

        self.handle_guides_item = QtWidgets.QGraphicsPathItem()
        guide_pen = QtGui.QPen(QtGui.QColor("#60a5fa"), 1.0)
        guide_pen.setCosmetic(True)
        guide_pen.setStyle(DASH_LINE)
        self.handle_guides_item.setPen(guide_pen)
        self.handle_guides_item.setZValue(89.0)
        self.scene.addItem(self.handle_guides_item)
        self.handle_guides_item.hide()

        self.marquee_item = QtWidgets.QGraphicsRectItem()
        marquee_pen = QtGui.QPen(QtGui.QColor("#2563eb"), 1.0)
        marquee_pen.setCosmetic(True)
        marquee_pen.setStyle(DASH_LINE)
        self.marquee_item.setPen(marquee_pen)
        self.marquee_item.setBrush(QtGui.QBrush(QtGui.QColor(37, 99, 235, 24)))
        self.marquee_item.setZValue(95.0)
        self.scene.addItem(self.marquee_item)
        self.marquee_item.hide()

        self.snap_item = QtWidgets.QGraphicsPathItem()
        self.snap_item.setFlag(IGNORE_TRANSFORM, True)
        snap_pen = QtGui.QPen(QtGui.QColor("#059669"), 1.8)
        snap_pen.setCosmetic(True)
        self.snap_item.setPen(snap_pen)
        self.snap_item.setZValue(130.0)
        snap_path = QtGui.QPainterPath()
        snap_path.moveTo(-7.0, 0.0)
        snap_path.lineTo(7.0, 0.0)
        snap_path.moveTo(0.0, -7.0)
        snap_path.lineTo(0.0, 7.0)
        self.snap_item.setPath(snap_path)
        self.scene.addItem(self.snap_item)
        self.snap_item.hide()

        self.measure_item = MeasureBubbleItem()
        self.scene.addItem(self.measure_item)

    def clear_transient(self):
        self.preview_item.hide()
        self.preview_item.setPath(QtGui.QPainterPath())
        self.marquee_item.hide()
        self.snap_item.hide()
        self.measure_item.hide()

    def clear_selection_transform(self):
        self.selection_frame_item.hide()
        self.selection_pivot_item.hide()
        for item in self.selection_handle_items:
            self.scene.removeItem(item)
        self.selection_handle_items = []

    def show_selection_transform(self, bounds):
        """Show non-interactive Aspire-style bounds, handles and pivot."""
        if bounds is None:
            self.clear_selection_transform()
            return
        def bound(name, index):
            value = getattr(bounds, name, None)
            return float(value() if callable(value) else value) if value is not None else float(bounds[index])
        min_x, min_y = bound("min_x", 0), bound("min_y", 1)
        max_x, max_y = bound("max_x", 2), bound("max_y", 3)
        rect = QtCore.QRectF(min_x, min_y, max_x - min_x, max_y - min_y).normalized()
        if rect.width() <= 1e-9 or rect.height() <= 1e-9:
            self.clear_selection_transform()
            return
        self.selection_frame_item.setRect(rect)
        self.selection_frame_item.show()
        self.selection_pivot_item.setPos(rect.center())
        self.selection_pivot_item.show()
        for item in self.selection_handle_items:
            self.scene.removeItem(item)
        self.selection_handle_items = []
        # The document uses Y-up coordinates while QGraphicsView paints with
        # the Y axis inverted.  Therefore the visual top edge is the model's
        # ``bottom`` edge.  Keep the handle order clockwise on screen so the
        # SelectTool can apply the expected opposite-corner resize semantics.
        points = (
            (rect.left(), rect.bottom()), (rect.center().x(), rect.bottom()),
            (rect.right(), rect.bottom()), (rect.right(), rect.center().y()),
            (rect.right(), rect.top()), (rect.center().x(), rect.top()),
            (rect.left(), rect.top()), (rect.left(), rect.center().y()),
        )
        no_button = QtCore.Qt.NoButton if hasattr(QtCore.Qt, "NoButton") else QtCore.Qt.MouseButton.NoButton
        for x_value, y_value in points:
            handle = QtWidgets.QGraphicsRectItem(-4.0, -4.0, 8.0, 8.0)
            handle.setFlag(IGNORE_TRANSFORM, True)
            handle.setPos(float(x_value), float(y_value))
            handle_pen = QtGui.QPen(QtGui.QColor("#111827"), 1.0)
            handle_pen.setCosmetic(True)
            handle.setPen(handle_pen)
            handle.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
            handle.setZValue(106.0)
            handle.setAcceptedMouseButtons(no_button)
            self.scene.addItem(handle)
            self.selection_handle_items.append(handle)
        center = QtWidgets.QGraphicsRectItem(-3.0, -3.0, 6.0, 6.0)
        center.setFlag(IGNORE_TRANSFORM, True)
        center.setPos(rect.center())
        center.setPen(QtGui.QPen(QtGui.QColor("#111827"), 1.0))
        center.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
        center.setZValue(107.0)
        center.setAcceptedMouseButtons(no_button)
        self.scene.addItem(center)
        self.selection_handle_items.append(center)

    def hit_test_selection_handle(self, view, screen_pos, radius_px=10.0):
        """Return the screen-space handle index under ``screen_pos``.

        Selection decorations are intentionally non-interactive graphics
        items.  Hit testing is owned by the modal SelectTool, just like node
        editing, so a resize remains an explicit command with Undo/Redo.
        The last item is the center pivot and is not a resize handle.
        """
        best = None
        for index, item in enumerate(self.selection_handle_items[:-1]):
            point = view.mapFromScene(item.scenePos())
            distance = math.hypot(
                float(point.x() - screen_pos.x()),
                float(point.y() - screen_pos.y()),
            )
            if distance <= float(radius_px) and (best is None or distance < best[0]):
                best = (distance, index)
        return best[1] if best is not None else None

    def clear_measurement_preview(self):
        """Clear the previous measurement while preserving the snap marker."""
        self.preview_item.hide()
        self.preview_item.setPath(QtGui.QPainterPath())
        self.measure_item.hide()

    def clear_toolpath_preview(self):
        """Hide the plan-view CAM overlay without touching vectors or CAM."""
        for item in self.toolpath_items.values():
            item.hide()
            item.setPath(QtGui.QPainterPath())
        self.toolpath_entry_item.hide()
        self.toolpath_entry_item.setPath(QtGui.QPainterPath())
        self.toolpath_direction_item.hide()
        self.toolpath_direction_item.setPath(QtGui.QPainterPath())

    @staticmethod
    def _toolpath_path(segments):
        path = QtGui.QPainterPath()
        for segment in tuple(segments or ()):
            if not isinstance(segment, (tuple, list)) or len(segment) != 2:
                continue
            try:
                start, end = segment
                path.moveTo(float(start[0]), float(start[1]))
                path.lineTo(float(end[0]), float(end[1]))
            except (IndexError, TypeError, ValueError):
                continue
        return path

    def show_toolpath_preview(self, components):
        """Render exact XY move components over the editor's vector scene.

        ``components`` is the host CAM decomposition of the exact movement
        list.  Z remains in the authoritative G-code list; a plan view uses
        only XY, so vertical plunges correctly have no visible length here.
        """
        components = dict(components or {})
        any_segment = False
        for key, item in self.toolpath_items.items():
            path = self._toolpath_path(components.get(key, ()))
            item.setPath(path)
            item.setVisible(not path.isEmpty())
            any_segment = any_segment or not path.isEmpty()

        entry_path = QtGui.QPainterPath()
        for value in tuple(components.get("entry_points", ()) or ()):
            try:
                x_value, y_value = float(value[0]), float(value[1])
            except (IndexError, TypeError, ValueError):
                continue
            radius = 1.8
            entry_path.addEllipse(
                QtCore.QRectF(
                    x_value - radius,
                    y_value - radius,
                    radius * 2.0,
                    radius * 2.0,
                )
            )
        self.toolpath_entry_item.setPath(entry_path)
        self.toolpath_entry_item.setVisible(not entry_path.isEmpty())
        direction_path = QtGui.QPainterPath()
        cut_segments = tuple(components.get("cut", ()) or ())
        stride = max(1, int(math.ceil(len(cut_segments) / 24.0)))
        for start, end in cut_segments[::stride]:
            try:
                x0, y0 = float(start[0]), float(start[1])
                x1, y1 = float(end[0]), float(end[1])
            except (IndexError, TypeError, ValueError):
                continue
            dx, dy = x1 - x0, y1 - y0
            length = math.hypot(dx, dy)
            if length <= 1e-9:
                continue
            ux, uy = dx / length, dy / length
            size = min(5.0, max(1.6, length * 0.08))
            bx, by = x1 - ux * size, y1 - uy * size
            px, py = -uy * size * 0.42, ux * size * 0.42
            direction_path.moveTo(QtCore.QPointF(x1, y1))
            direction_path.lineTo(QtCore.QPointF(bx + px, by + py))
            direction_path.lineTo(QtCore.QPointF(bx - px, by - py))
            direction_path.closeSubpath()
        self.toolpath_direction_item.setPath(direction_path)
        self.toolpath_direction_item.setVisible(not direction_path.isEmpty())
        return bool(any_segment)

    def clear_nodes(self):
        for item in self.node_items:
            self.scene.removeItem(item)
        self.node_items = []
        self.handle_guides_item.hide()
        self.handle_guides_item.setPath(QtGui.QPainterPath())

    def show_nodes(self, entity_id, nodes, selected_node_ids=()):
        self.clear_nodes()
        selected = set(str(value) for value in selected_node_ids)
        for index, value in enumerate(nodes):
            node_id, position = value[:2]
            kind = value[2] if len(value) > 2 else "node"
            item = NodeHandleItem(entity_id, node_id, position, primary=index == 0, kind=kind)
            item.set_node_selected(str(node_id) in selected)
            self.scene.addItem(item)
            self.node_items.append(item)

    def show_bezier_guides(self, entity):
        path = QtGui.QPainterPath()
        for span in tuple(getattr(entity, "spans", ()) or ()):
            if not hasattr(span, "control1") or not hasattr(span, "control2"):
                continue
            path.moveTo(_point(span.start))
            path.lineTo(_point(span.control1))
            path.moveTo(_point(span.end))
            path.lineTo(_point(span.control2))
        self.handle_guides_item.setPath(path)
        self.handle_guides_item.setVisible(not path.isEmpty())

    def hit_test_node(self, view, screen_pos, radius_px=8.0):
        best = None
        for item in self.node_items:
            point = view.mapFromScene(item.scenePos())
            distance = math.hypot(point.x() - screen_pos.x(), point.y() - screen_pos.y())
            if distance <= radius_px and (best is None or distance < best[0]):
                best = (distance, item)
        return best[1] if best else None

    def show_entity_preview(self, entity):
        self.preview_item.setPath(entity_painter_path(entity))
        self.preview_item.setPos(0.0, 0.0)
        self.preview_item.show()

    def show_modifier_preview(self, preview):
        path = QtGui.QPainterPath()
        for entity in tuple(getattr(preview, "result_entities", ()) or ()):
            path.addPath(entity_painter_path(entity))
        for point in tuple(getattr(preview, "construction_points", ()) or ()):
            x, y = _xy(point)
            size = 2.0
            path.addEllipse(QtCore.QRectF(x - size, y - size, size * 2.0, size * 2.0))
        self.preview_item.setPath(path)
        self.preview_item.setPos(0.0, 0.0)
        self.preview_item.show()

    def show_issue(self, entities=(), points=()):
        """Destaca entidades/pontos sem alterar a cena persistente."""
        path = QtGui.QPainterPath()
        for entity in tuple(entities or ()):
            path.addPath(entity_painter_path(entity))
        for point in tuple(points or ()):
            x, y = _xy(point)
            size = 3.0
            path.moveTo(x - size, y)
            path.lineTo(x + size, y)
            path.moveTo(x, y - size)
            path.lineTo(x, y + size)
            path.addEllipse(
                QtCore.QRectF(x - size, y - size, size * 2.0, size * 2.0)
            )
        self.preview_item.setPath(path)
        self.preview_item.setPos(0.0, 0.0)
        self.preview_item.setVisible(not path.isEmpty())

    def show_path_preview(self, points, closed=False):
        points = tuple(points)
        path = QtGui.QPainterPath()
        if points:
            path.moveTo(_point(points[0]))
            for value in points[1:]:
                path.lineTo(_point(value))
            if closed and len(points) > 2:
                path.closeSubpath()
        self.preview_item.setPath(path)
        self.preview_item.show()

    def show_bezier_preview(self, start, control1, control2, end):
        """Render a cubic preview without creating a persistent entity."""
        path = QtGui.QPainterPath(_point(start))
        path.cubicTo(_point(control1), _point(control2), _point(end))
        self.preview_item.setPath(path)
        self.preview_item.show()

    def show_circle_preview(self, center, radius):
        cx, cy = _xy(center)
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QRectF(cx - radius, cy - radius, radius * 2.0, radius * 2.0))
        self.preview_item.setPath(path)
        self.preview_item.show()

    def show_ellipse_preview(self, center, radius_x, radius_y):
        cx, cy = _xy(center)
        path = QtGui.QPainterPath()
        path.addEllipse(QtCore.QRectF(cx - radius_x, cy - radius_y, radius_x * 2.0, radius_y * 2.0))
        self.preview_item.setPath(path)
        self.preview_item.show()

    def show_marquee(self, first, current, crossing=False):
        rect = QtCore.QRectF(first, current).normalized()
        self.marquee_item.setRect(rect)
        brush = QtGui.QColor(249, 115, 22, 28) if crossing else QtGui.QColor(37, 99, 235, 24)
        self.marquee_item.setBrush(QtGui.QBrush(brush))
        self.marquee_item.show()

    def show_snap(self, candidate):
        if candidate is None:
            self.snap_item.hide()
            return
        self.snap_item.setPos(_point(candidate.point))
        self.snap_item.show()

    def show_measure(self, text, position):
        self.measure_item.set_text(text)
        point = _point(position)
        self.measure_item.setPos(point)
        self.measure_item.show()


__all__ = ["MeasureBubbleItem", "NodeHandleItem", "OverlayLayer"]
