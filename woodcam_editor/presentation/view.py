"""Y-up graphics view with CAD-style zoom, pan and normalized events."""

from __future__ import annotations

from dataclasses import dataclass
import math

from .compat import (
    ARROW_CURSOR,
    CLOSED_HAND_CURSOR,
    LEFT_BUTTON,
    MIDDLE_BUTTON,
    OPEN_HAND_CURSOR,
    RIGHT_BUTTON,
    Signal,
    STRONG_FOCUS,
    QtCore,
    QtGui,
    QtWidgets,
    event_pos,
    event_screen_point,
    qt_enum,
    wheel_delta,
)


@dataclass(frozen=True)
class CanvasPointerEvent:
    screen_pos: object
    scene_pos: object
    button: object
    buttons: object
    modifiers: object
    original: object


class VectorGraphicsView(QtWidgets.QGraphicsView):
    pointerPressed = Signal(object)
    pointerMoved = Signal(object)
    pointerReleased = Signal(object)
    pointerDoubleClicked = Signal(object)
    keyPressed = Signal(object)
    keyReleased = Signal(object)
    cursorMoved = Signal(object)
    cancelRequested = Signal()

    def __init__(self, scene=None, parent=None):
        super(VectorGraphicsView, self).__init__(scene, parent)
        self.setRenderHint(QtGui.QPainter.Antialiasing, True)
        self.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        self.setFocusPolicy(STRONG_FOCUS)
        self.setMouseTracking(True)
        self.setDragMode(QtWidgets.QGraphicsView.NoDrag)
        self.setTransformationAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setResizeAnchor(QtWidgets.QGraphicsView.NoAnchor)
        self.setCursor(ARROW_CURSOR)
        self._zoom = 1.0
        self._min_zoom = 0.02
        self._max_zoom = 50.0
        self._panning = False
        self._space_down = False
        self._last_pan_pos = None
        self._right_canceling = False
        self._grid_spacing_mm = 10.0
        self._grid_visible = True
        self.resetTransform()
        self.scale(1.0, -1.0)

    @property
    def zoom_factor(self):
        return self._zoom

    def pixels_per_mm(self):
        transform = self.viewportTransform()
        origin = transform.map(QtCore.QPointF(0.0, 0.0))
        unit = transform.map(QtCore.QPointF(1.0, 0.0))
        return max(1e-12, math.hypot(unit.x() - origin.x(), unit.y() - origin.y()))

    @property
    def grid_spacing_mm(self):
        return self._grid_spacing_mm

    @property
    def grid_visible(self):
        return self._grid_visible

    def set_grid_spacing(self, spacing_mm):
        spacing = float(spacing_mm)
        if not math.isfinite(spacing) or spacing <= 0.0:
            raise ValueError("grid spacing must be positive and finite")
        self._grid_spacing_mm = spacing
        self.viewport().update()

    def set_grid_visible(self, visible):
        self._grid_visible = bool(visible)
        self.viewport().update()

    def model_pos(self, viewport_pos):
        if isinstance(viewport_pos, QtCore.QPointF):
            viewport_pos = QtCore.QPoint(int(viewport_pos.x()), int(viewport_pos.y()))
        return self.mapToScene(viewport_pos)

    def fit_model_rect(self, rect, margin=0.08):
        rect = QtCore.QRectF(rect).normalized()
        if rect.width() <= 1e-9 or rect.height() <= 1e-9:
            return
        width = max(1.0, float(self.viewport().width()))
        height = max(1.0, float(self.viewport().height()))
        scale = min(width / rect.width(), height / rect.height()) * max(0.1, 1.0 - margin)
        scale = min(self._max_zoom, max(self._min_zoom, scale))
        self.resetTransform()
        self.scale(scale, -scale)
        self._zoom = scale
        self.centerOn(rect.center())

    def reset_camera(self):
        self.resetTransform()
        self.scale(1.0, -1.0)
        self._zoom = 1.0

    def _pointer_event(self, event):
        screen = event_screen_point(event)
        return CanvasPointerEvent(
            screen_pos=screen,
            scene_pos=self.mapToScene(screen),
            button=event.button(),
            buttons=event.buttons(),
            modifiers=event.modifiers(),
            original=event,
        )

    def mousePressEvent(self, event):
        if event.button() == RIGHT_BUTTON:
            self._right_canceling = True
            self.cancelRequested.emit()
            event.accept()
            return
        if event.button() == MIDDLE_BUTTON or (self._space_down and event.button() == LEFT_BUTTON):
            self._panning = True
            self._last_pan_pos = event_screen_point(event)
            self.setCursor(CLOSED_HAND_CURSOR)
            event.accept()
            return
        self.pointerPressed.emit(self._pointer_event(event))
        event.accept()

    def mouseMoveEvent(self, event):
        screen = event_screen_point(event)
        scene_pos = self.mapToScene(screen)
        self.cursorMoved.emit(scene_pos)
        if self._panning and self._last_pan_pos is not None:
            delta = screen - self._last_pan_pos
            self._last_pan_pos = screen
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        self.pointerMoved.emit(self._pointer_event(event))
        event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == RIGHT_BUTTON and self._right_canceling:
            self._right_canceling = False
            event.accept()
            return
        if self._panning:
            self._panning = False
            self._last_pan_pos = None
            self.setCursor(OPEN_HAND_CURSOR if self._space_down else ARROW_CURSOR)
            event.accept()
            return
        self.pointerReleased.emit(self._pointer_event(event))
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == RIGHT_BUTTON:
            self._right_canceling = False
            self.cancelRequested.emit()
            event.accept()
            return
        self.pointerDoubleClicked.emit(self._pointer_event(event))
        event.accept()

    def contextMenuEvent(self, event):
        event.accept()

    def wheelEvent(self, event):
        delta = wheel_delta(event)
        if delta == 0:
            event.ignore()
            return
        factor = 1.15 if delta > 0 else 1.0 / 1.15
        target = min(self._max_zoom, max(self._min_zoom, self._zoom * factor))
        factor = target / self._zoom
        if abs(factor - 1.0) <= 1e-12:
            return
        screen = event_screen_point(event)
        before = self.mapToScene(screen)
        self.scale(factor, factor)
        self._zoom = target
        after = self.mapToScene(screen)
        shift = after - before
        self.translate(shift.x(), shift.y())
        event.accept()

    def keyPressEvent(self, event):
        if event.key() == qt_enum(QtCore.Qt, "Key_Space", "Key") and not event.isAutoRepeat():
            self._space_down = True
            if not self._panning:
                self.setCursor(OPEN_HAND_CURSOR)
        self.keyPressed.emit(event)
        if not event.isAccepted():
            super(VectorGraphicsView, self).keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.key() == qt_enum(QtCore.Qt, "Key_Space", "Key") and not event.isAutoRepeat():
            self._space_down = False
            if not self._panning:
                self.setCursor(ARROW_CURSOR)
        self.keyReleased.emit(event)
        if not event.isAccepted():
            super(VectorGraphicsView, self).keyReleaseEvent(event)

    def drawBackground(self, painter, rect):
        painter.save()
        painter.fillRect(rect, QtGui.QColor("#fbfdff"))
        if not self._grid_visible:
            painter.restore()
            return
        pixels = self.pixels_per_mm()
        minor = self._grid_spacing_mm
        while minor * pixels < 8.0:
            minor *= 5.0
        major = minor * 5.0
        left = math.floor(rect.left() / minor) * minor
        right = math.ceil(rect.right() / minor) * minor
        bottom = math.floor(rect.top() / minor) * minor
        top = math.ceil(rect.bottom() / minor) * minor
        minor_pen = QtGui.QPen(QtGui.QColor("#eef2f7"), 0.0)
        major_pen = QtGui.QPen(QtGui.QColor("#dbe4ef"), 0.0)
        value = left
        count = 0
        while value <= right and count < 5000:
            ratio = value / major
            painter.setPen(major_pen if abs(ratio - round(ratio)) < 1e-7 else minor_pen)
            painter.drawLine(QtCore.QPointF(value, bottom), QtCore.QPointF(value, top))
            value += minor
            count += 1
        value = bottom
        count = 0
        while value <= top and count < 5000:
            ratio = value / major
            painter.setPen(major_pen if abs(ratio - round(ratio)) < 1e-7 else minor_pen)
            painter.drawLine(QtCore.QPointF(left, value), QtCore.QPointF(right, value))
            value += minor
            count += 1
        axis_pen = QtGui.QPen(QtGui.QColor("#9fb0c4"), 0.0)
        painter.setPen(axis_pen)
        painter.drawLine(QtCore.QPointF(0.0, bottom), QtCore.QPointF(0.0, top))
        painter.drawLine(QtCore.QPointF(left, 0.0), QtCore.QPointF(right, 0.0))
        painter.restore()


__all__ = ["CanvasPointerEvent", "VectorGraphicsView"]
