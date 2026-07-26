"""Basic exact-vector creation tools."""

from __future__ import annotations

import math

from woodcam_editor.application import EditorMode

from ..compat import LEFT_BUTTON, RIGHT_BUTTON, QtCore, qt_enum
from .base import EditorTool, xy


def distance(first, second):
    ax, ay = xy(first)
    bx, by = xy(second)
    return math.hypot(bx - ax, by - ay)


class TwoPointTool(EditorTool):
    label = ""

    def __init__(self, manager):
        super(TwoPointTool, self).__init__(manager)
        self.first = None

    def activate(self, entity_id=None):
        self.first = None
        self.overlays.clear_transient()

    def cancel(self):
        super(TwoPointTool, self).cancel()
        self.first = None

    def pointer_press(self, event):
        if event.button == RIGHT_BUTTON:
            self.cancel()
            return
        if event.button != LEFT_BUTTON:
            return
        point, _candidate = self.snapped(event, reference_point=self.first)
        if self.first is None:
            self.first = point
        else:
            self.commit(self.first, point)
            self.first = None
            self.overlays.clear_transient()

    def pointer_move(self, event):
        if self.first is None:
            return
        point, _candidate = self.snapped(event, reference_point=self.first)
        self.preview(self.first, point)

    def key_press(self, event):
        if event.key() == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            if self.first is not None:
                self.cancel()
            else:
                self.manager.activate(EditorMode.SELECT)
            event.accept()

    def preview(self, first, current):
        self.overlays.show_path_preview((first, current))
        ax, ay = xy(first)
        bx, by = xy(current)
        self.overlays.show_measure(
            "L %.3f mm   ΔX %.3f   ΔY %.3f   ∠ %.2f°" % (
                distance(first, current),
                bx - ax,
                by - ay,
                math.degrees(math.atan2(by - ay, bx - ax)),
            ),
            current,
        )

    def commit(self, first, second):
        raise NotImplementedError


class LineTool(TwoPointTool):
    mode = EditorMode.DRAW_LINE

    def commit(self, first, second):
        self.controller.add_line(first, second)


class RectangleTool(TwoPointTool):
    mode = EditorMode.DRAW_RECTANGLE

    def preview(self, first, current):
        ax, ay = xy(first)
        bx, by = xy(current)
        points = (
            self.controller.vec(ax, ay), self.controller.vec(bx, ay),
            self.controller.vec(bx, by), self.controller.vec(ax, by),
        )
        self.overlays.show_path_preview(points, closed=True)
        self.overlays.show_measure("%.3f × %.3f mm" % (abs(bx - ax), abs(by - ay)), current)

    def commit(self, first, second):
        self.controller.add_rectangle(first, second)


class CircleTool(TwoPointTool):
    mode = EditorMode.DRAW_CIRCLE

    def preview(self, first, current):
        radius = distance(first, current)
        self.overlays.show_circle_preview(first, radius)
        self.overlays.show_measure("R %.3f mm   Ø %.3f mm" % (radius, radius * 2.0), current)

    def commit(self, first, second):
        self.controller.add_circle(first, distance(first, second))


class EllipseTool(TwoPointTool):
    mode = EditorMode.DRAW_ELLIPSE

    def preview(self, first, current):
        cx, cy = xy(first)
        px, py = xy(current)
        radius_x = abs(px - cx)
        radius_y = abs(py - cy)
        self.overlays.show_ellipse_preview(first, radius_x, radius_y)
        self.overlays.show_measure(
            "L %.3f mm   A %.3f mm" % (radius_x * 2.0, radius_y * 2.0),
            current,
        )

    def commit(self, first, second):
        cx, cy = xy(first)
        px, py = xy(second)
        self.controller.add_ellipse(first, abs(px - cx), abs(py - cy))


class PolygonTool(TwoPointTool):
    mode = EditorMode.DRAW_POLYGON

    def __init__(self, manager, sides=3):
        super(PolygonTool, self).__init__(manager)
        self.sides = max(3, int(sides))

    def set_sides(self, sides):
        self.sides = max(3, int(sides))

    def preview(self, first, current):
        radius = distance(first, current)
        cx, cy = xy(first)
        px, py = xy(current)
        rotation = math.atan2(py - cy, px - cx)
        points = tuple(
            self.controller.vec(
                cx + radius * math.cos(rotation + index * math.pi * 2.0 / self.sides),
                cy + radius * math.sin(rotation + index * math.pi * 2.0 / self.sides),
            )
            for index in range(self.sides)
        )
        self.overlays.show_path_preview(points, closed=True)
        self.overlays.show_measure("%d lados   R %.3f mm" % (self.sides, radius), current)

    def commit(self, first, second):
        cx, cy = xy(first)
        px, py = xy(second)
        self.controller.add_polygon(first, distance(first, second), self.sides, math.atan2(py - cy, px - cx))


class StarTool(TwoPointTool):
    """Aspire-style star: center click followed by an outer tip/radius."""

    mode = EditorMode.DRAW_STAR

    def __init__(self, manager, points=5, inner_ratio=0.45):
        super(StarTool, self).__init__(manager)
        self.points = max(3, int(points))
        self.inner_ratio = min(0.95, max(0.05, float(inner_ratio)))

    def set_points(self, points):
        self.points = max(3, int(points))

    def set_inner_ratio(self, ratio):
        self.inner_ratio = min(0.95, max(0.05, float(ratio)))

    def _vertices(self, first, current):
        radius = distance(first, current)
        cx, cy = xy(first)
        px, py = xy(current)
        rotation = math.atan2(py - cy, px - cx)
        values = []
        for index in range(self.points * 2):
            current_radius = radius if index % 2 == 0 else radius * self.inner_ratio
            angle = rotation + index * math.pi / self.points
            values.append(self.controller.vec(
                cx + current_radius * math.cos(angle),
                cy + current_radius * math.sin(angle),
            ))
        return tuple(values)

    def preview(self, first, current):
        radius = distance(first, current)
        self.overlays.show_path_preview(self._vertices(first, current), closed=True)
        self.overlays.show_measure(
            "%d pontas   R %.3f mm   interno %.0f%%" % (
                self.points, radius, self.inner_ratio * 100.0,
            ),
            current,
        )

    def commit(self, first, second):
        cx, cy = xy(first)
        px, py = xy(second)
        self.controller.add_star(
            first,
            distance(first, second),
            self.points,
            self.inner_ratio,
            math.atan2(py - cy, px - cx),
        )


class PolylineTool(EditorTool):
    mode = EditorMode.DRAW_POLYLINE

    def __init__(self, manager):
        super(PolylineTool, self).__init__(manager)
        self.points = []
        self.hover = None

    def activate(self, entity_id=None):
        self.points = []
        self.hover = None
        self.overlays.clear_transient()

    def cancel(self):
        super(PolylineTool, self).cancel()
        self.points = []
        self.hover = None

    def pointer_press(self, event):
        if event.button == RIGHT_BUTTON:
            self.finish(False)
            return
        if event.button != LEFT_BUTTON:
            return
        point, _candidate = self.snapped(
            event,
            reference_point=self.points[-1] if self.points else None,
        )
        if self.points and distance(self.points[-1], point) <= 1e-9:
            return
        self.points.append(point)
        if len(self.points) > 2 and distance(self.points[0], point) * self.view.pixels_per_mm() <= 10.0:
            self.points[-1] = self.points[0]
            self.finish(True)

    def pointer_move(self, event):
        if not self.points:
            return
        self.hover, _candidate = self.snapped(
            event,
            reference_point=self.points[-1] if self.points else None,
        )
        values = tuple(self.points) + (self.hover,)
        self.overlays.show_path_preview(values)
        self.overlays.show_measure("Trecho %.3f mm   pontos %d" % (distance(self.points[-1], self.hover), len(self.points)), self.hover)

    def pointer_double_click(self, event):
        self.finish(False)

    def finish(self, closed=False):
        values = list(self.points)
        if closed and values and distance(values[0], values[-1]) <= 1e-9:
            values.pop()
        if len(values) >= (3 if closed else 2):
            self.controller.add_polyline(values, closed=closed)
        self.points = []
        self.hover = None
        self.overlays.clear_transient()

    def key_press(self, event):
        key = event.key()
        if key == qt_enum(QtCore.Qt, "Key_Backspace", "Key") and self.points:
            self.points.pop()
            event.accept()
        elif key in (qt_enum(QtCore.Qt, "Key_Return", "Key"), qt_enum(QtCore.Qt, "Key_Enter", "Key"), qt_enum(QtCore.Qt, "Key_Space", "Key")):
            self.finish(False)
            event.accept()
        elif key == qt_enum(QtCore.Qt, "Key_Tab", "Key"):
            self.finish(True)
            event.accept()
        elif key == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            if self.points:
                self.cancel()
            else:
                self.manager.activate(EditorMode.SELECT)
            event.accept()


class ArcTool(EditorTool):
    mode = EditorMode.DRAW_ARC

    def __init__(self, manager):
        super(ArcTool, self).__init__(manager)
        self.points = []

    def activate(self, entity_id=None):
        self.points = []
        self.overlays.clear_transient()

    def cancel(self):
        super(ArcTool, self).cancel()
        self.points = []

    def pointer_press(self, event):
        if event.button == RIGHT_BUTTON:
            self.cancel()
            return
        if event.button != LEFT_BUTTON:
            return
        point, _candidate = self.snapped(event)
        self.points.append(point)
        if len(self.points) == 3:
            self.controller.add_arc_three_points(*self.points)
            self.points = []
            self.overlays.clear_transient()

    def pointer_move(self, event):
        if not self.points:
            return
        point, _candidate = self.snapped(event)
        values = tuple(self.points) + (point,)
        self.overlays.show_path_preview(values)
        self.overlays.show_measure("Arco: ponto %d/3" % (len(self.points) + 1), point)

    def key_press(self, event):
        if event.key() == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            if self.points:
                self.cancel()
            else:
                self.manager.activate(EditorMode.SELECT)
            event.accept()


class BezierTool(EditorTool):
    """Four-click exact cubic Bézier tool: start, handle 1, handle 2, end."""

    mode = EditorMode.DRAW_BEZIER

    def __init__(self, manager):
        super(BezierTool, self).__init__(manager)
        self.points = []

    def activate(self, entity_id=None):
        self.points = []
        self.overlays.clear_transient()

    def cancel(self):
        super(BezierTool, self).cancel()
        self.points = []

    def pointer_press(self, event):
        if event.button == RIGHT_BUTTON:
            self.cancel()
            return
        if event.button != LEFT_BUTTON:
            return
        reference = self.points[-1] if self.points else None
        point, _candidate = self.snapped(event, reference_point=reference)
        self.points.append(point)
        if len(self.points) == 4:
            # The order deliberately follows the visible control polygon:
            # início → controle 1 → controle 2 → fim.
            self.controller.add_bezier(*self.points)
            self.points = []
            self.overlays.clear_transient()

    def pointer_move(self, event):
        if not self.points:
            return
        reference = self.points[-1]
        point, _candidate = self.snapped(event, reference_point=reference)
        if len(self.points) == 1:
            # Until the first handle is selected, use the hover point both as
            # control and endpoint. This makes the first curve preview useful.
            start = self.points[0]
            control1 = point
            control2 = point
            end = point
        elif len(self.points) == 2:
            start, control1 = self.points
            control2 = point
            end = point
        else:
            start, control1, control2 = self.points
            end = point
        self.overlays.show_bezier_preview(start, control1, control2, end)
        self.overlays.show_measure("Bézier: ponto %d/4" % (len(self.points) + 1), point)

    def key_press(self, event):
        if event.key() == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            if self.points:
                self.cancel()
            else:
                self.manager.activate(EditorMode.SELECT)
            event.accept()


__all__ = ["ArcTool", "BezierTool", "CircleTool", "EllipseTool", "LineTool", "PolygonTool", "StarTool", "PolylineTool", "RectangleTool"]
