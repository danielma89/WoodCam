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
    def __init__(self, entity_id, node_id, position, primary=False):
        super(NodeHandleItem, self).__init__(-4.5, -4.5, 9.0, 9.0)
        self.entity_id = str(entity_id)
        self.node_id = str(node_id)
        self.setPos(_point(position))
        self.setFlag(IGNORE_TRANSFORM, True)
        self.setZValue(110.0)
        pen = QtGui.QPen(QtGui.QColor("#991b1b"), 1.3)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QtGui.QBrush(QtGui.QColor("#fde68a" if primary else "#fecaca")))
        self.setAcceptedMouseButtons(QtCore.Qt.NoButton if hasattr(QtCore.Qt, "NoButton") else QtCore.Qt.MouseButton.NoButton)

    def set_node_selected(self, selected):
        self.setBrush(QtGui.QBrush(QtGui.QColor("#fbbf24" if selected else "#fecaca")))


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
        self.preview_item = QtWidgets.QGraphicsPathItem()
        preview_pen = QtGui.QPen(QtGui.QColor("#d946ef"), 1.8)
        preview_pen.setCosmetic(True)
        preview_pen.setStyle(DASH_LINE)
        self.preview_item.setPen(preview_pen)
        self.preview_item.setBrush(QtGui.QBrush(NO_BRUSH))
        self.preview_item.setZValue(90.0)
        self.scene.addItem(self.preview_item)
        self.preview_item.hide()

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

    def clear_nodes(self):
        for item in self.node_items:
            self.scene.removeItem(item)
        self.node_items = []

    def show_nodes(self, entity_id, nodes, selected_node_ids=()):
        self.clear_nodes()
        selected = set(str(value) for value in selected_node_ids)
        for index, (node_id, position) in enumerate(nodes):
            item = NodeHandleItem(entity_id, node_id, position, primary=index == 0)
            item.set_node_selected(str(node_id) in selected)
            self.scene.addItem(item)
            self.node_items.append(item)

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
