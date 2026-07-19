"""Stateless visual projection of domain entities."""

from __future__ import annotations

import math

from .compat import QtCore, QtGui, QtWidgets


def _xy(point):
    x = point.x() if callable(getattr(point, "x", None)) else point.x
    y = point.y() if callable(getattr(point, "y", None)) else point.y
    return float(x), float(y)


def _point(point):
    x, y = _xy(point)
    return QtCore.QPointF(x, y)


def _flatten(span, count=48):
    method = getattr(span, "flatten", None)
    if callable(method):
        try:
            values = tuple(method(0.05))
            if len(values) >= 2:
                return values
        except Exception:
            pass
    point_at = getattr(span, "point_at", None)
    if callable(point_at):
        return tuple(point_at(index / float(count)) for index in range(count + 1))
    return (getattr(span, "start"), getattr(span, "end"))


def entity_painter_path(entity):
    path = QtGui.QPainterPath()
    spans = tuple(getattr(entity, "spans", ()) or ())
    if spans:
        path.moveTo(_point(spans[0].start))
        for span in spans:
            name = type(span).__name__.lower()
            if "line" in name:
                path.lineTo(_point(span.end))
            elif "bezier" in name and hasattr(span, "control1"):
                path.cubicTo(_point(span.control1), _point(span.control2), _point(span.end))
            else:
                values = _flatten(span)
                for value in values[1:]:
                    path.lineTo(_point(value))
        if getattr(entity, "closed", False):
            path.closeSubpath()
        return path

    center = getattr(entity, "center", None)
    radius = getattr(entity, "radius", None)
    if center is not None and radius is not None:
        cx, cy = _xy(center)
        radius = float(radius)
        path.addEllipse(QtCore.QRectF(cx - radius, cy - radius, 2.0 * radius, 2.0 * radius))
        return path
    radius_x = getattr(entity, "radius_x", None)
    radius_y = getattr(entity, "radius_y", None)
    if center is not None and radius_x is not None and radius_y is not None:
        cx, cy = _xy(center)
        rotation = float(getattr(entity, "rotation", 0.0))
        first = None
        for index in range(97):
            angle = index * math.pi * 2.0 / 96.0
            local_x = float(radius_x) * math.cos(angle)
            local_y = float(radius_y) * math.sin(angle)
            point = QtCore.QPointF(
                cx + local_x * math.cos(rotation) - local_y * math.sin(rotation),
                cy + local_x * math.sin(rotation) + local_y * math.cos(rotation),
            )
            if first is None:
                path.moveTo(point)
                first = point
            else:
                path.lineTo(point)
        path.closeSubpath()
        return path
    return path


class EntityGraphicsItem(QtWidgets.QGraphicsPathItem):
    """A projection keyed by entity id; it never mutates the domain."""

    def __init__(self, entity, parent=None):
        super(EntityGraphicsItem, self).__init__(parent)
        self.entity_id = str(entity.id)
        self._selected = False
        self._base_color = QtGui.QColor("#2563eb")
        self.setAcceptedMouseButtons(QtCore.Qt.NoButton if hasattr(QtCore.Qt, "NoButton") else QtCore.Qt.MouseButton.NoButton)
        self.setZValue(10.0)
        self.update_entity(entity)

    def update_entity(self, entity):
        self.entity_id = str(entity.id)
        self.setPath(entity_painter_path(entity))
        self.setPos(0.0, 0.0)
        self._apply_style()

    def set_editor_selected(self, selected):
        selected = bool(selected)
        if selected != self._selected:
            self._selected = selected
            self._apply_style()

    def _apply_style(self):
        color = QtGui.QColor("#f97316") if self._selected else self._base_color
        pen = QtGui.QPen(color, 2.2 if self._selected else 1.35)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush if hasattr(QtCore.Qt, "NoBrush") else QtCore.Qt.BrushStyle.NoBrush))


__all__ = ["EntityGraphicsItem", "entity_painter_path"]
