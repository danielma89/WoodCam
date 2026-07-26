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


def _append_arc(path, span):
    """Append a visually smooth circular arc as cubic Bézier sections for Qt.

    The domain deliberately retains ``ArcSpan``.  Drawing it through
    ``flatten(0.05)`` made a small dogbone visibly polygonal at close zoom,
    which looked like an import defect even though its coordinates were exact.
    Qt has cubic paths but no model-space circular-arc primitive, so each
    quarter arc is expressed by its standard high-precision Bézier
    approximation.  The exact ArcSpan remains untouched in the domain.
    This affects only the scene projection, never VectorDocument geometry.
    """

    start_angle = float(span.start_angle)
    sweep = float(span.sweep_angle)
    radius = float(span.radius)
    center_x, center_y = _xy(span.center)
    steps = max(1, int(math.ceil(abs(sweep) / (math.pi * 0.5))))
    delta = sweep / float(steps)
    for index in range(steps):
        angle0 = start_angle + delta * index
        angle1 = angle0 + delta
        coefficient = 4.0 / 3.0 * math.tan((angle1 - angle0) * 0.25)
        point0 = QtCore.QPointF(
            center_x + radius * math.cos(angle0),
            center_y + radius * math.sin(angle0),
        )
        point3 = QtCore.QPointF(
            center_x + radius * math.cos(angle1),
            center_y + radius * math.sin(angle1),
        )
        control1 = QtCore.QPointF(
            point0.x() - radius * math.sin(angle0) * coefficient,
            point0.y() + radius * math.cos(angle0) * coefficient,
        )
        control2 = QtCore.QPointF(
            point3.x() + radius * math.sin(angle1) * coefficient,
            point3.y() - radius * math.cos(angle1) * coefficient,
        )
        path.cubicTo(control1, control2, point3)


def entity_painter_path(entity):
    path = QtGui.QPainterPath()
    spans = tuple(getattr(entity, "spans", ()) or ())
    if spans:
        path.moveTo(_point(spans[0].start))
        for span in spans:
            name = type(span).__name__.lower()
            if "line" in name:
                path.lineTo(_point(span.end))
            elif "arc" in name and hasattr(span, "center"):
                _append_arc(path, span)
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
        # A muted rose keeps vectors distinct from the slate/gray CAM path
        # without using the orange reserved for CAM previews and warnings.
        self._base_color = QtGui.QColor("#b4536a")
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
        # Aspire keeps selected vectors dark and uses bounds/handles as the
        # selection cue. Orange is reserved for CAM previews.
        color = QtGui.QColor("#111827") if self._selected else self._base_color
        pen = QtGui.QPen(color, 1.8 if self._selected else 1.35)
        pen.setCosmetic(True)
        self.setPen(pen)
        self.setBrush(QtGui.QBrush(QtCore.Qt.NoBrush if hasattr(QtCore.Qt, "NoBrush") else QtCore.Qt.BrushStyle.NoBrush))


__all__ = ["EntityGraphicsItem", "entity_painter_path"]
