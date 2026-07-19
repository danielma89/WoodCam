"""Small programmatic CAD icons used by the compact editor toolbars."""

from __future__ import annotations

from .compat import QtCore, QtGui, qt_enum


def _canvas(size=24):
    pixmap = QtGui.QPixmap(int(size), int(size))
    pixmap.fill(QtGui.QColor(0, 0, 0, 0))
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    pen = QtGui.QPen(QtGui.QColor("#1f2937"), max(1.4, size / 14.0))
    pen.setCapStyle(qt_enum(QtCore.Qt, "RoundCap", "PenCapStyle"))
    pen.setJoinStyle(qt_enum(QtCore.Qt, "RoundJoin", "PenJoinStyle"))
    painter.setPen(pen)
    painter.setBrush(QtGui.QBrush(QtGui.QColor(0, 0, 0, 0)))
    return pixmap, painter


def _node(painter, x, y, size=2.8):
    painter.save()
    painter.setBrush(QtGui.QBrush(QtGui.QColor("#ffffff")))
    painter.drawRect(QtCore.QRectF(x - size * 0.5, y - size * 0.5, size, size))
    painter.restore()


def tool_icon(name, size=24):
    """Return a scalable-looking icon drawn only with QPainter primitives."""
    pixmap, painter = _canvas(size)
    s = float(size) / 24.0

    def point(x, y):
        return QtCore.QPointF(x * s, y * s)

    if name == "select":
        path = QtGui.QPainterPath(point(5, 3))
        path.lineTo(point(17, 13))
        path.lineTo(point(11.8, 14.1))
        path.lineTo(point(15.4, 20))
        path.lineTo(point(12.4, 21.5))
        path.lineTo(point(8.9, 15.4))
        path.lineTo(point(5, 19))
        path.closeSubpath()
        painter.setBrush(QtGui.QBrush(QtGui.QColor("#dbeafe")))
        painter.drawPath(path)
    elif name == "nodes":
        points = (point(4, 17), point(9, 7), point(16, 11), point(20, 4))
        painter.drawPolyline(QtGui.QPolygonF(points))
        for value in points:
            _node(painter, value.x(), value.y(), 3.4 * s)
    elif name == "line":
        painter.drawLine(point(4, 19), point(20, 5))
        _node(painter, 4 * s, 19 * s, 3.2 * s)
        _node(painter, 20 * s, 5 * s, 3.2 * s)
    elif name == "polyline":
        points = (point(3, 18), point(8, 8), point(14, 15), point(21, 5))
        painter.drawPolyline(QtGui.QPolygonF(points))
        for value in points:
            _node(painter, value.x(), value.y(), 3.0 * s)
    elif name == "rectangle":
        painter.drawRect(QtCore.QRectF(4 * s, 6 * s, 16 * s, 12 * s))
        _node(painter, 4 * s, 6 * s, 2.8 * s)
        _node(painter, 20 * s, 18 * s, 2.8 * s)
    elif name == "circle":
        painter.drawEllipse(QtCore.QRectF(4 * s, 4 * s, 16 * s, 16 * s))
        painter.drawPoint(point(12, 12))
    elif name == "ellipse":
        painter.drawEllipse(QtCore.QRectF(3 * s, 7 * s, 18 * s, 10 * s))
        painter.drawLine(point(12, 12), point(20, 12))
    elif name == "arc":
        painter.drawArc(QtCore.QRectF(3 * s, 4 * s, 18 * s, 17 * s), 20 * 16, 235 * 16)
        _node(painter, 20.4 * s, 8.2 * s, 3.0 * s)
        _node(painter, 5.5 * s, 18.2 * s, 3.0 * s)
    elif name == "polygon":
        points = (
            point(12, 3), point(21, 10), point(17, 20),
            point(7, 20), point(3, 10), point(12, 3),
        )
        painter.drawPolyline(QtGui.QPolygonF(points))
    elif name in ("undo", "redo"):
        painter.drawArc(QtCore.QRectF(5 * s, 5 * s, 14 * s, 14 * s), 35 * 16, 250 * 16)
        if name == "undo":
            painter.drawLine(point(5, 8), point(4, 14))
            painter.drawLine(point(4, 14), point(10, 13))
        else:
            painter.drawLine(point(19, 8), point(20, 14))
            painter.drawLine(point(20, 14), point(14, 13))
    elif name == "delete":
        painter.drawRect(QtCore.QRectF(7 * s, 7 * s, 10 * s, 13 * s))
        painter.drawLine(point(5, 6), point(19, 6))
        painter.drawLine(point(9, 3), point(15, 3))
        painter.drawLine(point(10, 10), point(10, 17))
        painter.drawLine(point(14, 10), point(14, 17))
    elif name == "fit":
        for first, second, third in (
            ((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)),
            ((20, 15), (20, 20), (15, 20)), ((9, 20), (4, 20), (4, 15)),
        ):
            painter.drawPolyline(QtGui.QPolygonF((point(*first), point(*second), point(*third))))

    painter.end()
    icon = QtGui.QIcon()
    icon.addPixmap(pixmap)
    return icon


__all__ = ["tool_icon"]
