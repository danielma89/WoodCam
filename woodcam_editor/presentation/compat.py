"""Single compatibility point for the Qt bindings shipped with FreeCAD."""

from __future__ import annotations

try:
    from PySide6 import QtCore, QtGui, QtWidgets
except ImportError:
    try:
        from PySide2 import QtCore, QtGui, QtWidgets
    except ImportError:
        from PySide import QtCore, QtGui

        QtWidgets = QtGui


Signal = getattr(QtCore, "Signal", getattr(QtCore, "pyqtSignal", None))
Slot = getattr(QtCore, "Slot", getattr(QtCore, "pyqtSlot", None))


def qt_enum(container, name, group=None):
    """Return a Qt5/Qt6 enum without evaluating a missing fallback eagerly."""
    value = getattr(container, name, None)
    if value is not None:
        return value
    if group:
        nested = getattr(container, group, None)
        if nested is not None:
            return getattr(nested, name)
    raise AttributeError("Qt enum %s.%s is unavailable" % (container, name))


LEFT_BUTTON = qt_enum(QtCore.Qt, "LeftButton", "MouseButton")
RIGHT_BUTTON = qt_enum(QtCore.Qt, "RightButton", "MouseButton")
MIDDLE_BUTTON = qt_enum(QtCore.Qt, "MiddleButton", "MouseButton")
NO_BUTTON = qt_enum(QtCore.Qt, "NoButton", "MouseButton")
SHIFT_MODIFIER = qt_enum(QtCore.Qt, "ShiftModifier", "KeyboardModifier")
CTRL_MODIFIER = qt_enum(QtCore.Qt, "ControlModifier", "KeyboardModifier")
ALT_MODIFIER = qt_enum(QtCore.Qt, "AltModifier", "KeyboardModifier")
NO_MODIFIER = qt_enum(QtCore.Qt, "NoModifier", "KeyboardModifier")
STRONG_FOCUS = qt_enum(QtCore.Qt, "StrongFocus", "FocusPolicy")
KEEP_ASPECT_RATIO = qt_enum(QtCore.Qt, "KeepAspectRatio", "AspectRatioMode")
DASH_LINE = qt_enum(QtCore.Qt, "DashLine", "PenStyle")
NO_PEN = qt_enum(QtCore.Qt, "NoPen", "PenStyle")
NO_BRUSH = qt_enum(QtCore.Qt, "NoBrush", "BrushStyle")
CROSS_CURSOR = qt_enum(QtCore.Qt, "CrossCursor", "CursorShape")
ARROW_CURSOR = qt_enum(QtCore.Qt, "ArrowCursor", "CursorShape")
CLOSED_HAND_CURSOR = qt_enum(QtCore.Qt, "ClosedHandCursor", "CursorShape")
OPEN_HAND_CURSOR = qt_enum(QtCore.Qt, "OpenHandCursor", "CursorShape")


def event_pos(event):
    position = getattr(event, "position", None)
    if callable(position):
        return position()
    return QtCore.QPointF(event.pos())


def event_screen_point(event):
    point = event_pos(event)
    return QtCore.QPoint(int(round(point.x())), int(round(point.y())))


def wheel_delta(event):
    angle_delta = getattr(event, "angleDelta", None)
    if callable(angle_delta):
        return angle_delta().y()
    return event.delta()


__all__ = [
    "QtCore",
    "QtGui",
    "QtWidgets",
    "Signal",
    "Slot",
    "qt_enum",
    "event_pos",
    "event_screen_point",
    "wheel_delta",
]

