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
    cursorLeft = Signal()
    viewChanged = Signal()
    cancelRequested = Signal()

    @staticmethod
    def _frame_interval_for_refresh_rate(refresh_rate):
        """Return a safe Qt timer cadence for the active display.

        Pointer coalescing is presentation-only, but a fixed 8 ms interval
        visibly beats against a 165 Hz display (125 updates spread over 165
        presentation slots).  Follow the real screen cadence while bounding
        the timer between 60-ish and 250 Hz for older Qt/FreeCAD builds.
        """

        try:
            refresh_rate = float(refresh_rate)
        except (TypeError, ValueError):
            refresh_rate = 60.0
        if not math.isfinite(refresh_rate) or refresh_rate < 30.0:
            refresh_rate = 60.0
        return max(4, min(16, int(round(1000.0 / refresh_rate))))

    def _screen_refresh_rate(self):
        screen = None
        screen_getter = getattr(self, "screen", None)
        if callable(screen_getter):
            try:
                screen = screen_getter()
            except Exception:
                screen = None
        if screen is None:
            application = QtWidgets.QApplication.instance()
            if application is not None:
                try:
                    screen = application.primaryScreen()
                except Exception:
                    screen = None
        if screen is None:
            return 60.0
        try:
            return float(screen.refreshRate())
        except Exception:
            return 60.0

    def _update_frame_cadence(self):
        interval = self._frame_interval_for_refresh_rate(
            self._screen_refresh_rate()
        )
        for timer in (
            self._cursor_display_timer,
            self._pointer_move_timer,
            self._pan_timer,
        ):
            timer.setInterval(interval)
        self._frame_interval_ms = interval

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
        self.setCacheMode(
            qt_enum(
                QtWidgets.QGraphicsView,
                "CacheBackground",
                "CacheModeFlag",
            )
        )
        self._zoom = 1.0
        self._min_zoom = 0.02
        self._max_zoom = 50.0
        self._panning = False
        self._space_down = False
        self._last_pan_pos = None
        self._pending_pan_delta = QtCore.QPoint(0, 0)
        self._right_canceling = False
        self._grid_spacing_mm = 10.0
        self._grid_visible = True
        self._grid_origin = QtCore.QPointF(0.0, 0.0)
        # Coordinate labels and ruler markers are presentation-only. Gaming
        # mice can deliver hundreds or thousands of move events per second;
        # repainting two rulers synchronously for every packet made even an
        # empty document feel delayed under FreeCAD/Wayland. Coordinate
        # feedback is rendered at frame cadence; construction previews may
        # opt into the same cadence while their confirming click stays exact.
        self._pending_cursor_display = None
        self._last_pointer_viewport_pos = None
        self._cursor_display_timer = QtCore.QTimer(self)
        self._cursor_display_timer.setSingleShot(True)
        self._cursor_display_timer.setInterval(16)
        try:
            self._cursor_display_timer.setTimerType(
                qt_enum(QtCore.Qt, "PreciseTimer", "TimerType")
            )
        except Exception:
            pass
        self._cursor_display_timer.timeout.connect(self._cursor_display_frame)
        self._pending_pointer_move = None
        self._coalesce_pointer_moves = False
        self._pointer_move_timer = QtCore.QTimer(self)
        self._pointer_move_timer.setSingleShot(True)
        self._pointer_move_timer.setInterval(16)
        self._pan_timer = QtCore.QTimer(self)
        self._pan_timer.setSingleShot(True)
        self._pan_timer.setInterval(16)
        for timer in (self._pointer_move_timer, self._pan_timer):
            try:
                timer.setTimerType(qt_enum(QtCore.Qt, "PreciseTimer", "TimerType"))
            except Exception:
                pass
        self._pointer_move_timer.timeout.connect(self._pointer_move_frame)
        self._pan_timer.timeout.connect(self._pan_frame)
        self._frame_interval_ms = 16
        self._update_frame_cadence()
        self.resetTransform()
        self.scale(1.0, -1.0)

    def showEvent(self, event):  # noqa: N802 - Qt virtual name
        super(VectorGraphicsView, self).showEvent(event)
        # QWidget.screen() is authoritative only after the editor belongs to
        # its real top-level window (embedded or detached).
        self._update_frame_cadence()

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

    def last_pointer_viewport_position(self):
        """Last mouse position on the canvas, including before a menu opens."""
        return (None if self._last_pointer_viewport_pos is None
                else QtCore.QPoint(self._last_pointer_viewport_pos))

    def set_grid_spacing(self, spacing_mm):
        spacing = float(spacing_mm)
        if not math.isfinite(spacing) or spacing <= 0.0:
            raise ValueError("grid spacing must be positive and finite")
        self._grid_spacing_mm = spacing
        self.resetCachedContent()
        self.viewport().update()

    def set_grid_visible(self, visible):
        self._grid_visible = bool(visible)
        self.resetCachedContent()
        self.viewport().update()

    def set_grid_origin(self, x, y):
        self._grid_origin = QtCore.QPointF(float(x), float(y))
        self.resetCachedContent()
        self.viewport().update()

    def leaveEvent(self, event):  # noqa: N802 - Qt virtual name
        self._cursor_display_timer.stop()
        self._pointer_move_timer.stop()
        self._pending_cursor_display = None
        self._pending_pointer_move = None
        self.cursorLeft.emit()
        super(VectorGraphicsView, self).leaveEvent(event)

    def _queue_cursor_display(self, point):
        point = QtCore.QPointF(point)
        if self._cursor_display_timer.isActive():
            self._pending_cursor_display = point
            return
        self.cursorMoved.emit(point)
        self._cursor_display_timer.start()

    def _flush_cursor_display(self):
        point = self._pending_cursor_display
        self._pending_cursor_display = None
        if point is not None:
            self.cursorMoved.emit(point)

    def _cursor_display_frame(self):
        if self._pending_cursor_display is None:
            return
        self._flush_cursor_display()
        self._cursor_display_timer.start()

    def _queue_pointer_move(self, pointer_event):
        if not self._coalesce_pointer_moves:
            self._pointer_move_timer.stop()
            self._flush_pointer_move()
            self.pointerMoved.emit(pointer_event)
            return
        if self._pointer_move_timer.isActive():
            self._pending_pointer_move = pointer_event
            return
        self.pointerMoved.emit(pointer_event)
        self._pointer_move_timer.start()

    def _flush_pointer_move(self):
        pointer_event = self._pending_pointer_move
        self._pending_pointer_move = None
        if pointer_event is not None:
            self.pointerMoved.emit(pointer_event)

    def _pointer_move_frame(self):
        if self._pending_pointer_move is None:
            return
        self._flush_pointer_move()
        self._pointer_move_timer.start()

    def set_pointer_move_coalescing(self, enabled):
        enabled = bool(enabled)
        if enabled == self._coalesce_pointer_moves:
            return
        self._pointer_move_timer.stop()
        self._flush_pointer_move()
        self._coalesce_pointer_moves = enabled

    def _queue_pan_delta(self, delta):
        self._pending_pan_delta += delta
        if self._pan_timer.isActive():
            return
        self._flush_pan()
        self._pan_timer.start()

    def _flush_pan(self):
        delta = QtCore.QPoint(self._pending_pan_delta)
        self._pending_pan_delta = QtCore.QPoint(0, 0)
        if delta.isNull():
            return
        self._ensure_free_pan_extent()
        horizontal = self.horizontalScrollBar()
        vertical = self.verticalScrollBar()
        horizontal.setValue(horizontal.value() - delta.x())
        vertical.setValue(vertical.value() - delta.y())
        if self._last_pan_pos is not None:
            self._queue_cursor_display(self.mapToScene(self._last_pan_pos))

    def _pan_frame(self):
        if self._pending_pan_delta.isNull():
            return
        self._flush_pan()
        self._pan_timer.start()

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
        self.viewChanged.emit()

    def reset_camera(self):
        self.resetTransform()
        self.scale(1.0, -1.0)
        self._zoom = 1.0
        self.viewChanged.emit()

    def _pointer_event(self, event, screen=None, scene_pos=None):
        screen = event_screen_point(event) if screen is None else screen
        scene_pos = self.mapToScene(screen) if scene_pos is None else scene_pos
        return CanvasPointerEvent(
            screen_pos=screen,
            scene_pos=scene_pos,
            button=event.button(),
            buttons=event.buttons(),
            modifiers=event.modifiers(),
            original=event,
        )

    def _ensure_free_pan_extent(self):
        """Grow only the camera canvas so scroll bars never clamp CAD pan.

        ``QGraphicsView`` derives its scroll range from ``sceneRect``.  The
        document adapter intentionally keeps that rectangle close to the real
        geometry, which is useful for Fit but used to turn Fit into a camera
        boundary.  Extending the scene rectangle around the current viewport
        changes no entity, work area or command history; it merely provides
        room to keep dragging.  Explicit Fit rebuilds the tight geometry rect
        before centering it again.
        """

        scene = self.scene()
        if scene is None or self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return
        visible = self.mapToScene(self.viewport().rect()).boundingRect().normalized()
        if visible.width() <= 1e-9 or visible.height() <= 1e-9:
            return
        current = scene.sceneRect()
        # Keep a full viewport of guard space before growing again. The old
        # test compared two equally moving padded rectangles, so a one-pixel
        # pan extended sceneRect and recentred the view again on every event.
        guard = visible.adjusted(
            -visible.width(),
            -visible.height(),
            visible.width(),
            visible.height(),
        )
        if not current.isNull() and current.contains(guard):
            return
        padding_x = max(500.0, visible.width() * 8.0)
        padding_y = max(500.0, visible.height() * 8.0)
        navigation_rect = visible.adjusted(
            -padding_x,
            -padding_y,
            padding_x,
            padding_y,
        )
        center = self.mapToScene(self.viewport().rect().center())
        scene.setSceneRect(
            navigation_rect if current.isNull() else current.united(navigation_rect)
        )
        # Extending scroll ranges must not itself look like a pan step.
        self.centerOn(center)

    def mousePressEvent(self, event):
        self._last_pointer_viewport_pos = event_screen_point(event)
        # A queued visual preview must reach the exact last hover point before
        # a click consumes it. The click itself is still dispatched directly.
        self._pointer_move_timer.stop()
        self._flush_pointer_move()
        # Do not let the FreeCAD main window steal editor shortcuts after a
        # canvas click.  We intentionally accept the pointer event below, so
        # QGraphicsView's default focus hand-off would not otherwise run.
        self.setFocus()
        # FreeCAD actions use application-level shortcuts.  While the user is
        # actively clicking the 2D canvas, explicitly own the keyboard so
        # Ctrl+A/Delete cannot be redirected to the document tree.
        try:
            self.grabKeyboard()
        except Exception:
            pass
        if event.button() == RIGHT_BUTTON:
            self._right_canceling = True
            self.cancelRequested.emit()
            event.accept()
            return
        if event.button() == MIDDLE_BUTTON or (self._space_down and event.button() == LEFT_BUTTON):
            self._panning = True
            self._last_pan_pos = event_screen_point(event)
            self._pending_pan_delta = QtCore.QPoint(0, 0)
            self._ensure_free_pan_extent()
            self.setCursor(CLOSED_HAND_CURSOR)
            event.accept()
            return
        self.pointerPressed.emit(self._pointer_event(event))
        event.accept()

    def focusOutEvent(self, event):
        try:
            self.releaseKeyboard()
        except Exception:
            pass
        super(VectorGraphicsView, self).focusOutEvent(event)

    def mouseMoveEvent(self, event):
        screen = event_screen_point(event)
        self._last_pointer_viewport_pos = screen
        scene_pos = self.mapToScene(screen)
        if self._panning and self._last_pan_pos is not None:
            delta = screen - self._last_pan_pos
            self._last_pan_pos = screen
            self._queue_pan_delta(delta)
            event.accept()
            return
        self._queue_cursor_display(scene_pos)
        self._queue_pointer_move(
            self._pointer_event(event, screen=screen, scene_pos=scene_pos)
        )
        event.accept()

    def mouseReleaseEvent(self, event):
        self._last_pointer_viewport_pos = event_screen_point(event)
        if event.button() == RIGHT_BUTTON and self._right_canceling:
            self._right_canceling = False
            event.accept()
            return
        if self._panning:
            self._pan_timer.stop()
            self._flush_pan()
            self._panning = False
            self._last_pan_pos = None
            self.setCursor(OPEN_HAND_CURSOR if self._space_down else ARROW_CURSOR)
            event.accept()
            return
        self._pointer_move_timer.stop()
        self._flush_pointer_move()
        self.pointerReleased.emit(self._pointer_event(event))
        event.accept()

    def mouseDoubleClickEvent(self, event):
        self._last_pointer_viewport_pos = event_screen_point(event)
        if event.button() == RIGHT_BUTTON:
            self._right_canceling = False
            self.cancelRequested.emit()
            event.accept()
            return
        self._pointer_move_timer.stop()
        self._flush_pointer_move()
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
        self.viewChanged.emit()
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
        # Grid lines are axis-aligned cosmetic strokes. Antialiasing them
        # wastes fill-rate during pan and makes the fine grid less crisp.
        painter.setRenderHint(QtGui.QPainter.Antialiasing, False)
        painter.fillRect(rect, QtGui.QColor("#ffffff"))
        if not self._grid_visible:
            painter.restore()
            return
        pixels = self.pixels_per_mm()
        minor = self._grid_spacing_mm
        while minor * pixels < 8.0:
            minor *= 5.0
        major = minor * 5.0
        origin_x = self._grid_origin.x()
        origin_y = self._grid_origin.y()
        left = origin_x + math.floor((rect.left() - origin_x) / minor) * minor
        right = origin_x + math.ceil((rect.right() - origin_x) / minor) * minor
        bottom = origin_y + math.floor((rect.top() - origin_y) / minor) * minor
        top = origin_y + math.ceil((rect.bottom() - origin_y) / minor) * minor
        minor_pen = QtGui.QPen(QtGui.QColor("#eef2f7"), 0.0)
        major_pen = QtGui.QPen(QtGui.QColor("#dbe4ef"), 0.0)
        value = left
        count = 0
        while value <= right and count < 5000:
            ratio = (value - origin_x) / major
            painter.setPen(major_pen if abs(ratio - round(ratio)) < 1e-7 else minor_pen)
            painter.drawLine(QtCore.QPointF(value, bottom), QtCore.QPointF(value, top))
            value += minor
            count += 1
        value = bottom
        count = 0
        while value <= top and count < 5000:
            ratio = (value - origin_y) / major
            painter.setPen(major_pen if abs(ratio - round(ratio)) < 1e-7 else minor_pen)
            painter.drawLine(QtCore.QPointF(left, value), QtCore.QPointF(right, value))
            value += minor
            count += 1
        axis_pen = QtGui.QPen(QtGui.QColor("#9fb0c4"), 0.0)
        painter.setPen(axis_pen)
        painter.drawLine(
            QtCore.QPointF(origin_x, bottom), QtCore.QPointF(origin_x, top)
        )
        painter.drawLine(
            QtCore.QPointF(left, origin_y), QtCore.QPointF(right, origin_y)
        )
        painter.restore()


class RulerWidget(QtWidgets.QWidget):
    """Lightweight millimetre ruler synchronized with the editor camera."""

    def __init__(self, view, horizontal=True, parent=None):
        super(RulerWidget, self).__init__(parent)
        self.view = view
        self.horizontal = bool(horizontal)
        self._cursor_scene_position = None
        self._coordinate_origin = QtCore.QPointF(0.0, 0.0)
        self.setAttribute(QtCore.Qt.WA_TransparentForMouseEvents, True)
        if self.horizontal:
            self.setFixedHeight(22)
        else:
            # Match the top ruler thickness.  Rotated labels do not require a
            # 38 px gutter; that old width merely separated the number from
            # its tick and consumed useful drawing space.
            self.setFixedWidth(22)
        view.viewChanged.connect(self.update)
        view.cursorMoved.connect(self.set_cursor_scene_position)
        view.cursorLeft.connect(self.clear_cursor_position)
        if self.horizontal:
            view.horizontalScrollBar().valueChanged.connect(
                lambda _value: self.update()
            )
        else:
            view.verticalScrollBar().valueChanged.connect(
                lambda _value: self.update()
            )

    @property
    def cursor_scene_position(self):
        return self._cursor_scene_position

    def _marker_coordinate(self, point):
        if point is None:
            return None
        mapped = self.view.mapFromScene(point)
        return float(mapped.x() if self.horizontal else mapped.y())

    def _marker_rect(self, coordinate):
        if coordinate is None:
            return QtCore.QRect()
        if self.horizontal:
            return QtCore.QRect(int(round(coordinate)) - 6, 11, 13, 11)
        return QtCore.QRect(11, int(round(coordinate)) - 6, 11, 13)

    def set_cursor_scene_position(self, point):
        old_coordinate = self._marker_coordinate(self._cursor_scene_position)
        self._cursor_scene_position = QtCore.QPointF(point)
        new_coordinate = self._marker_coordinate(self._cursor_scene_position)
        dirty = self._marker_rect(old_coordinate).united(
            self._marker_rect(new_coordinate)
        )
        if not dirty.isNull():
            self.update(dirty.adjusted(-1, -1, 1, 1))

    def clear_cursor_position(self):
        coordinate = self._marker_coordinate(self._cursor_scene_position)
        self._cursor_scene_position = None
        dirty = self._marker_rect(coordinate)
        if not dirty.isNull():
            self.update(dirty.adjusted(-1, -1, 1, 1))

    def set_coordinate_origin(self, x, y):
        self._coordinate_origin = QtCore.QPointF(float(x), float(y))
        self.update()

    @staticmethod
    def _step(pixels_per_mm):
        target_pixels = 70.0
        if pixels_per_mm <= 1.0e-9:
            return 10.0
        raw = target_pixels / pixels_per_mm
        power = 10.0 ** math.floor(math.log10(max(raw, 1.0e-12)))
        for multiplier in (1.0, 2.0, 5.0, 10.0):
            step = power * multiplier
            if step * pixels_per_mm >= target_pixels:
                return step
        return power * 10.0

    def paintEvent(self, event):  # noqa: N802 - Qt virtual name
        painter = QtGui.QPainter(self)
        clip = event.rect()
        painter.fillRect(clip, QtGui.QColor("#f1f5f9"))
        painter.setPen(QtGui.QPen(QtGui.QColor("#94a3b8"), 1.0))
        if self.horizontal:
            painter.drawLine(
                clip.left(), self.height() - 1, clip.right(), self.height() - 1
            )
        else:
            painter.drawLine(
                self.width() - 1, clip.top(), self.width() - 1, clip.bottom()
            )
        pixels = self.view.pixels_per_mm()
        step = self._step(pixels)
        if self.horizontal:
            start = self.view.mapToScene(QtCore.QPoint(clip.left(), 0)).x()
            end = self.view.mapToScene(QtCore.QPoint(clip.right(), 0)).x()
            origin = self._coordinate_origin.x()
            first = origin + math.floor((start - origin) / step) * step
            value = first
            while value <= end + step and value < start + 100000 * step:
                x = self.view.mapFromScene(QtCore.QPointF(value, 0.0)).x()
                if 0 <= x <= self.width():
                    painter.drawLine(x, self.height() - 8, x, self.height() - 1)
                    local_value = value - origin
                    if abs(local_value) < step * 1.0e-9:
                        local_value = 0.0
                    text = "%.0f" % local_value
                    metrics = painter.fontMetrics()
                    # The tick is the datum.  Center the label on it instead
                    # of adding a fixed offset (which drifts at every zoom).
                    painter.drawText(
                        int(x - metrics.horizontalAdvance(text) / 2),
                        16,
                        text,
                    )
                value += step
        else:
            top = self.view.mapToScene(QtCore.QPoint(0, clip.top())).y()
            bottom = self.view.mapToScene(QtCore.QPoint(0, clip.bottom())).y()
            low, high = min(top, bottom), max(top, bottom)
            origin = self._coordinate_origin.y()
            first = origin + math.floor((low - origin) / step) * step
            value = first
            while value <= high + step and value < low + 100000 * step:
                y = self.view.mapFromScene(QtCore.QPointF(0.0, value)).y()
                if 0 <= y <= self.height():
                    painter.drawLine(self.width() - 7, y, self.width() - 1, y)
                    local_value = value - origin
                    if abs(local_value) < step * 1.0e-9:
                        local_value = 0.0
                    text = "%.0f" % local_value
                    metrics = painter.fontMetrics()
                    painter.save()
                    # Rotate around the tick itself, so the baseline and the
                    # tick stay aligned instead of appearing one label-height
                    # above/below one another.
                    # Keep the rotated label immediately beside the inner
                    # tick.  Both rulers now use the same 22 px thickness.
                    painter.translate(7, y)
                    painter.rotate(-90)
                    painter.drawText(
                        int(-metrics.horizontalAdvance(text) / 2),
                        int((metrics.ascent() - metrics.descent()) / 2),
                        text,
                    )
                    painter.restore()
                value += step
        marker = self._marker_coordinate(self._cursor_scene_position)
        if marker is not None:
            painter.setPen(QtGui.QPen(QtGui.QColor("#0369a1"), 1.0))
            painter.setBrush(QtGui.QBrush(QtGui.QColor("#0ea5e9")))
            if self.horizontal and -6.0 <= marker <= self.width() + 6.0:
                painter.drawPolygon(
                    QtGui.QPolygonF(
                        (
                            QtCore.QPointF(marker, self.height() - 1),
                            QtCore.QPointF(marker - 4.5, self.height() - 8),
                            QtCore.QPointF(marker + 4.5, self.height() - 8),
                        )
                    )
                )
            elif not self.horizontal and -6.0 <= marker <= self.height() + 6.0:
                painter.drawPolygon(
                    QtGui.QPolygonF(
                        (
                            QtCore.QPointF(self.width() - 1, marker),
                            QtCore.QPointF(self.width() - 8, marker - 4.5),
                            QtCore.QPointF(self.width() - 8, marker + 4.5),
                        )
                    )
                )
        painter.end()


__all__ = ["CanvasPointerEvent", "RulerWidget", "VectorGraphicsView"]
