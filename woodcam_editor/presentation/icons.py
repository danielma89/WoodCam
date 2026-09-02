"""Small programmatic CAD icons used by the compact editor toolbars."""

from __future__ import annotations

import math
from pathlib import Path

from .compat import QtCore, QtGui, qt_enum


_TABLER_ICON_DIR = (
    Path(__file__).resolve().parents[2] / "resources" / "icons" / "tabler"
)

# Professional, visually consistent SVGs replace the growing collection of
# one-off QPainter glyphs.  Logical WoodCAM names stay stable, so actions,
# translations and tests do not depend on third-party filenames.  The painter
# implementation below remains the fallback for installations whose Qt build
# has no SVG image plugin.
_TABLER_ICON_NAMES = {
    "select": "pointer",
    "nodes": "vector",
    "line": "line",
    "polyline": "vector-spline",
    "rectangle": "rectangle",
    "circle": "circle",
    "ellipse": "oval",
    "arc": "vector-bezier-arc",
    "bezier": "vector-bezier",
    "polygon": "polygon",
    "star": "star",
    "text": "typography",
    "measure": "ruler-measure",
    "recognize_parts": "box-model-2",
    "nest": "layout-grid",
    "undo": "arrow-back-up",
    "redo": "arrow-forward-up",
    "delete": "trash",
    "save": "device-floppy",
    "fit": "maximize",
    "edit_tools": "pencil",
    "repair_tools": "tools",
    "copy": "copy",
    "paste": "clipboard",
    "group": "stack",
    "ungroup": "unlink",
    "weld": "circles-relation",
    "subtract": "layers-difference",
    "intersection": "layers-intersect",
    "overlap": "layers-selected",
    "reverse": "arrows-exchange",
    "text_edit": "text-resize",
    "fit_curves": "vector-bezier-circle",
    "contour": "border-outer",
    "offset": "border-style-2",
    "diagnose": "zoom-exclamation",
    "cleanup": "eraser",
    "repair_close": "route-square",
    "join_paths": "arrows-join",
    "close_line": "join-straight",
    "close_smooth": "join-round",
    "close_midpoint": "arrows-join-2",
    "join_line": "link",
    "join_smooth": "link-plus",
    "project": "arrow-down-to-arc",
    "splice": "git-merge",
    "trim": "scissors",
    "extend": "arrows-diagonal",
    "model_3d": "scan-cube",
    "finish_model_3d": "sphere",
    "machining_boundary": "border-outer",
    "rough_strategy": "layers-subtract",
    "finish_strategy": "route",
    "operation_name": "tag",
    "ramp_3d": "arrow-ramp-right-2",
}


def _tabler_icon(name):
    asset_name = _TABLER_ICON_NAMES.get(str(name))
    if not asset_name:
        return None
    path = _TABLER_ICON_DIR / (asset_name + ".svg")
    if not path.is_file():
        return None
    icon = QtGui.QIcon(str(path))
    return None if icon.isNull() else icon


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
    """Return a professional SVG icon, with a pure-QPainter fallback."""
    asset_icon = _tabler_icon(name)
    if asset_icon is not None:
        return asset_icon
    pixmap, painter = _canvas(size)
    s = float(size) / 24.0

    def point(x, y):
        return QtCore.QPointF(x * s, y * s)

    def stroke(color, width=None):
        pen = QtGui.QPen(
            QtGui.QColor(color),
            float(width if width is not None else max(1.4, size / 14.0)),
        )
        pen.setCapStyle(qt_enum(QtCore.Qt, "RoundCap", "PenCapStyle"))
        pen.setJoinStyle(qt_enum(QtCore.Qt, "RoundJoin", "PenJoinStyle"))
        painter.setPen(pen)

    def fill(color):
        painter.setBrush(QtGui.QBrush(QtGui.QColor(color)))

    if name == "select":
        stroke("#1d4ed8")
        path = QtGui.QPainterPath(point(5, 3))
        path.lineTo(point(17, 13))
        path.lineTo(point(11.8, 14.1))
        path.lineTo(point(15.4, 20))
        path.lineTo(point(12.4, 21.5))
        path.lineTo(point(8.9, 15.4))
        path.lineTo(point(5, 19))
        path.closeSubpath()
        fill("#bfdbfe")
        painter.drawPath(path)
    elif name == "nodes":
        stroke("#7c3aed")
        points = (point(4, 17), point(9, 7), point(16, 11), point(20, 4))
        painter.drawPolyline(QtGui.QPolygonF(points))
        for value in points:
            _node(painter, value.x(), value.y(), 3.4 * s)
    elif name == "line":
        stroke("#0284c7")
        painter.drawLine(point(4, 19), point(20, 5))
        _node(painter, 4 * s, 19 * s, 3.2 * s)
        _node(painter, 20 * s, 5 * s, 3.2 * s)
    elif name == "polyline":
        stroke("#0f766e")
        points = (point(3, 18), point(8, 8), point(14, 15), point(21, 5))
        painter.drawPolyline(QtGui.QPolygonF(points))
        for value in points:
            _node(painter, value.x(), value.y(), 3.0 * s)
    elif name == "rectangle":
        stroke("#2563eb")
        fill("#dbeafe")
        painter.drawRect(QtCore.QRectF(4 * s, 6 * s, 16 * s, 12 * s))
        _node(painter, 4 * s, 6 * s, 2.8 * s)
        _node(painter, 20 * s, 18 * s, 2.8 * s)
    elif name == "circle":
        stroke("#0891b2")
        fill("#cffafe")
        painter.drawEllipse(QtCore.QRectF(4 * s, 4 * s, 16 * s, 16 * s))
        painter.drawPoint(point(12, 12))
    elif name == "ellipse":
        stroke("#0d9488")
        fill("#ccfbf1")
        painter.drawEllipse(QtCore.QRectF(3 * s, 7 * s, 18 * s, 10 * s))
        painter.drawLine(point(12, 12), point(20, 12))
    elif name == "arc":
        stroke("#ea580c")
        painter.drawArc(QtCore.QRectF(3 * s, 4 * s, 18 * s, 17 * s), 20 * 16, 235 * 16)
        _node(painter, 20.4 * s, 8.2 * s, 3.0 * s)
        _node(painter, 5.5 * s, 18.2 * s, 3.0 * s)
    elif name == "bezier":
        stroke("#7c3aed")
        path = QtGui.QPainterPath(point(3, 18))
        path.cubicTo(point(7, 2), point(17, 22), point(21, 6))
        painter.drawPath(path)
        helper = QtGui.QPen(QtGui.QColor("#94a3b8"), max(1.0, size / 22.0))
        helper.setStyle(qt_enum(QtCore.Qt, "DashLine", "PenStyle"))
        painter.save()
        painter.setPen(helper)
        painter.drawLine(point(3, 18), point(7, 2))
        painter.drawLine(point(21, 6), point(17, 22))
        painter.restore()
        for value in (point(3, 18), point(7, 2), point(17, 22), point(21, 6)):
            _node(painter, value.x(), value.y(), 2.8 * s)
    elif name == "polygon":
        stroke("#ca8a04")
        fill("#fef9c3")
        points = (
            point(12, 3), point(21, 10), point(17, 20),
            point(7, 20), point(3, 10), point(12, 3),
        )
        painter.drawPolygon(QtGui.QPolygonF(points))
    elif name == "star":
        stroke("#d97706")
        fill("#fef3c7")
        points = []
        for index in range(11):
            radius = 9.0 if index % 2 == 0 else 4.0
            angle = -math.pi / 2.0 + index * math.pi / 5.0
            points.append(point(12 + radius * math.cos(angle), 12 + radius * math.sin(angle)))
        painter.drawPolygon(QtGui.QPolygonF(points))
    elif name == "text":
        stroke("#7c3aed")
        painter.setPen(QtGui.QColor("#7c3aed"))
        text_font = QtGui.QFont()
        text_font.setBold(True)
        text_font.setPixelSize(max(11, int(16 * s)))
        painter.setFont(text_font)
        painter.drawText(QtCore.QRectF(3 * s, 3 * s, 18 * s, 18 * s), "T")
    elif name == "measure":
        stroke("#059669")
        painter.drawLine(point(4, 19), point(20, 5))
        for offset in (0, 4, 8, 12, 16):
            x = 4.0 + offset
            y = 19.0 - offset
            tick = 2.4 if offset in (0, 16) else 1.7
            painter.drawLine(point(x - tick, y - tick), point(x + tick, y + tick))
        stroke("#047857", max(1.0, size / 20.0))
        painter.drawLine(point(3, 20), point(7, 20))
        painter.drawLine(point(17, 4), point(21, 4))
    elif name == "recognize_parts":
        stroke("#0369a1")
        fill("#e0f2fe")
        painter.drawRoundedRect(QtCore.QRectF(3 * s, 4 * s, 17 * s, 15 * s), 2 * s, 2 * s)
        fill("#ffffff")
        painter.drawEllipse(QtCore.QRectF(6 * s, 8 * s, 3.5 * s, 3.5 * s))
        painter.drawEllipse(QtCore.QRectF(13 * s, 8 * s, 3.5 * s, 3.5 * s))
        stroke("#16a34a", max(1.5, size / 13.0))
        painter.drawLine(point(13.5, 17.2), point(16.2, 20))
        painter.drawLine(point(16.2, 20), point(21.5, 14.2))
    elif name == "nest":
        stroke("#b45309")
        fill("#fef3c7")
        painter.drawRect(QtCore.QRectF(3 * s, 4 * s, 8 * s, 6 * s))
        painter.drawRect(QtCore.QRectF(13 * s, 4 * s, 8 * s, 9 * s))
        painter.drawRect(QtCore.QRectF(3 * s, 12 * s, 10 * s, 8 * s))
        stroke("#2563eb", max(1.3, size / 16.0))
        painter.drawLine(point(15, 18), point(20, 18))
        painter.drawLine(point(20, 18), point(18, 16))
        painter.drawLine(point(20, 18), point(18, 20))
    elif name in ("undo", "redo"):
        stroke("#2563eb")
        painter.drawArc(QtCore.QRectF(5 * s, 5 * s, 14 * s, 14 * s), 35 * 16, 250 * 16)
        if name == "undo":
            painter.drawLine(point(5, 8), point(4, 14))
            painter.drawLine(point(4, 14), point(10, 13))
        else:
            painter.drawLine(point(19, 8), point(20, 14))
            painter.drawLine(point(20, 14), point(14, 13))
    elif name == "delete":
        stroke("#dc2626")
        fill("#fee2e2")
        painter.drawRect(QtCore.QRectF(7 * s, 7 * s, 10 * s, 13 * s))
        painter.drawLine(point(5, 6), point(19, 6))
        painter.drawLine(point(9, 3), point(15, 3))
        painter.drawLine(point(10, 10), point(10, 17))
        painter.drawLine(point(14, 10), point(14, 17))
    elif name == "fit":
        stroke("#0284c7")
        for first, second, third in (
            ((4, 9), (4, 4), (9, 4)), ((15, 4), (20, 4), (20, 9)),
            ((20, 15), (20, 20), (15, 20)), ((9, 20), (4, 20), (4, 15)),
        ):
            painter.drawPolyline(QtGui.QPolygonF((point(*first), point(*second), point(*third))))
    elif name in ("edit_tools", "repair_tools"):
        # Layered tool silhouettes remain legible at the 22 px rail size and
        # distinguish the two expandable flyouts without relying on text.
        if name == "edit_tools":
            stroke("#2563eb", 2.2 * s)
            painter.drawLine(point(5, 19), point(17.5, 6.5))
            fill("#dbeafe")
            painter.drawPolygon(
                QtGui.QPolygonF((point(16, 5), point(20, 4), point(19, 8), point(7, 20), point(4, 20), point(4, 17)))
            )
            stroke("#7c3aed", 1.5 * s)
            painter.drawRect(QtCore.QRectF(4 * s, 4 * s, 8 * s, 7 * s))
            _node(painter, 4 * s, 4 * s, 2.5 * s)
            _node(painter, 12 * s, 11 * s, 2.5 * s)
        else:
            stroke("#0f766e", 2.0 * s)
            painter.drawLine(point(5, 19), point(19, 5))
            painter.drawLine(point(4, 14), point(10, 20))
            painter.drawLine(point(14, 4), point(20, 10))
            stroke("#f59e0b", 1.8 * s)
            painter.drawArc(QtCore.QRectF(3 * s, 3 * s, 12 * s, 12 * s), 200 * 16, 220 * 16)
            fill("#fef3c7")
            painter.drawEllipse(QtCore.QRectF(4 * s, 4 * s, 4 * s, 4 * s))
    elif name in ("copy", "paste"):
        stroke("#2563eb", 1.6 * s)
        fill("#eff6ff")
        if name == "copy":
            painter.drawRoundedRect(QtCore.QRectF(7 * s, 4 * s, 12 * s, 14 * s), 1.5 * s, 1.5 * s)
            fill("#bfdbfe")
            painter.drawRoundedRect(QtCore.QRectF(4 * s, 7 * s, 12 * s, 14 * s), 1.5 * s, 1.5 * s)
        else:
            stroke("#92400e", 1.6 * s)
            fill("#fffbeb")
            painter.drawRoundedRect(QtCore.QRectF(5 * s, 6 * s, 14 * s, 15 * s), 2 * s, 2 * s)
            fill("#fbbf24")
            painter.drawRoundedRect(QtCore.QRectF(8 * s, 3 * s, 8 * s, 5 * s), 1.5 * s, 1.5 * s)
            stroke("#2563eb", 1.4 * s)
            painter.drawLine(point(8, 12), point(16, 12))
            painter.drawLine(point(8, 16), point(14, 16))
    elif name in ("group", "ungroup"):
        stroke("#7c3aed", 1.5 * s)
        fill("#ede9fe")
        painter.drawRect(QtCore.QRectF(4 * s, 5 * s, 7 * s, 7 * s))
        painter.drawEllipse(QtCore.QRectF(13 * s, 12 * s, 7 * s, 7 * s))
        helper = QtGui.QPen(QtGui.QColor("#64748b"), 1.2 * s)
        helper.setStyle(qt_enum(QtCore.Qt, "DashLine", "PenStyle"))
        painter.setPen(helper)
        margin = 2 if name == "group" else 1
        painter.drawRect(QtCore.QRectF(margin * s, margin * s, (24 - 2 * margin) * s, (24 - 2 * margin) * s))
        if name == "ungroup":
            stroke("#dc2626", 1.7 * s)
            painter.drawLine(point(4, 20), point(20, 4))
    elif name in ("weld", "subtract", "intersection", "overlap"):
        stroke("#475569", 1.4 * s)
        first_color = "#93c5fd"
        second_color = "#86efac"
        if name == "subtract":
            second_color = "#fecaca"
        elif name == "intersection":
            first_color = second_color = "#c4b5fd"
        fill(first_color)
        painter.drawEllipse(QtCore.QRectF(3 * s, 6 * s, 12 * s, 12 * s))
        fill(second_color)
        painter.drawEllipse(QtCore.QRectF(9 * s, 6 * s, 12 * s, 12 * s))
        if name == "subtract":
            stroke("#dc2626", 2.0 * s)
            painter.drawLine(point(12, 12), point(18, 12))
        elif name == "intersection":
            stroke("#6d28d9", 2.0 * s)
            painter.drawLine(point(10, 8), point(14, 16))
        elif name == "overlap":
            stroke("#f59e0b", 1.8 * s)
            painter.drawLine(point(4, 20), point(20, 4))
        else:
            stroke("#16a34a", 1.8 * s)
            painter.drawLine(point(8, 12), point(16, 12))
    elif name == "reverse":
        stroke("#2563eb", 1.8 * s)
        painter.drawArc(QtCore.QRectF(3 * s, 5 * s, 18 * s, 14 * s), 15 * 16, 145 * 16)
        painter.drawArc(QtCore.QRectF(3 * s, 5 * s, 18 * s, 14 * s), 195 * 16, 145 * 16)
        fill("#2563eb")
        painter.drawPolygon(QtGui.QPolygonF((point(4, 7), point(10, 6), point(7, 11))))
        painter.drawPolygon(QtGui.QPolygonF((point(20, 17), point(14, 18), point(17, 13))))
    elif name == "text_edit":
        stroke("#7c3aed", 1.5 * s)
        painter.setPen(QtGui.QColor("#7c3aed"))
        font = QtGui.QFont()
        font.setBold(True)
        font.setPixelSize(max(10, int(14 * s)))
        painter.setFont(font)
        painter.drawText(QtCore.QRectF(2 * s, 2 * s, 15 * s, 17 * s), "T")
        stroke("#2563eb", 2.0 * s)
        painter.drawLine(point(11, 20), point(20, 11))
        fill("#fbbf24")
        painter.drawPolygon(QtGui.QPolygonF((point(10, 21), point(12, 17), point(14, 19))))
    elif name in ("fit_curves", "contour", "offset"):
        stroke("#0891b2", 1.7 * s)
        painter.drawArc(QtCore.QRectF(4 * s, 5 * s, 15 * s, 14 * s), 15 * 16, 250 * 16)
        if name in ("contour", "offset"):
            stroke("#f59e0b", 1.4 * s)
            painter.drawArc(QtCore.QRectF(7 * s, 8 * s, 10 * s, 9 * s), 15 * 16, 250 * 16)
        else:
            for value in (point(5, 15), point(10, 7), point(18, 12)):
                _node(painter, value.x(), value.y(), 2.8 * s)
    elif name in ("diagnose", "cleanup"):
        if name == "diagnose":
            stroke("#2563eb", 1.8 * s)
            fill("#dbeafe")
            painter.drawEllipse(QtCore.QRectF(4 * s, 4 * s, 12 * s, 12 * s))
            painter.drawLine(point(14, 14), point(21, 21))
            stroke("#dc2626", 1.8 * s)
            painter.drawLine(point(10, 7), point(10, 11))
            painter.drawPoint(point(10, 14))
        else:
            stroke("#0f766e", 1.7 * s)
            fill("#ccfbf1")
            painter.drawPolygon(QtGui.QPolygonF((point(6, 4), point(19, 17), point(15, 21), point(3, 9))))
            stroke("#ffffff", 1.4 * s)
            painter.drawLine(point(7, 9), point(15, 17))
            painter.drawLine(point(5, 13), point(9, 17))
    elif name in ("repair_close", "join_paths", "close_line", "close_smooth", "close_midpoint", "join_line", "join_smooth"):
        smooth = name in ("close_smooth", "join_smooth")
        stroke("#0f766e", 1.8 * s)
        if smooth:
            path = QtGui.QPainterPath(point(3, 17))
            path.cubicTo(point(8, 3), point(15, 21), point(21, 7))
            painter.drawPath(path)
        else:
            painter.drawLine(point(3, 17), point(9, 8))
            painter.drawLine(point(15, 16), point(21, 7))
        stroke("#f59e0b", 1.9 * s)
        painter.drawLine(point(9, 8), point(15, 16))
        for value in (point(9, 8), point(15, 16)):
            fill("#ffffff")
            painter.drawEllipse(QtCore.QRectF((value.x() - 1.8 * s), (value.y() - 1.8 * s), 3.6 * s, 3.6 * s))
        if name == "close_midpoint":
            fill("#f59e0b")
            painter.drawEllipse(QtCore.QRectF(10.5 * s, 10.5 * s, 3 * s, 3 * s))
    elif name in ("project", "splice"):
        stroke("#2563eb", 1.8 * s)
        painter.drawLine(point(3, 18), point(21, 18))
        stroke("#f59e0b", 1.8 * s)
        painter.drawLine(point(10, 4), point(10, 14))
        painter.drawLine(point(7, 11), point(10, 14))
        painter.drawLine(point(13, 11), point(10, 14))
        if name == "splice":
            stroke("#7c3aed", 1.5 * s)
            painter.drawArc(QtCore.QRectF(7 * s, 14 * s, 7 * s, 7 * s), 0, 180 * 16)
    elif name == "trim":
        stroke("#475569", 1.7 * s)
        painter.drawLine(point(4, 5), point(20, 19))
        painter.drawLine(point(4, 19), point(20, 5))
        stroke("#dc2626", 2.2 * s)
        painter.drawLine(point(9, 9), point(15, 15))
    elif name == "extend":
        stroke("#0f766e", 1.8 * s)
        painter.drawLine(point(3, 17), point(13, 7))
        helper = QtGui.QPen(QtGui.QColor("#0f766e"), 1.5 * s)
        helper.setStyle(qt_enum(QtCore.Qt, "DashLine", "PenStyle"))
        painter.setPen(helper)
        painter.drawLine(point(13, 7), point(21, 3))
        stroke("#f59e0b", 1.6 * s)
        painter.drawLine(point(16, 4), point(21, 3))
        painter.drawLine(point(21, 3), point(19, 8))

    painter.end()
    icon = QtGui.QIcon()
    icon.addPixmap(pixmap)
    return icon


__all__ = ["tool_icon"]
