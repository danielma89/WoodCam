"""Non-destructive two-point inspection tool."""

from __future__ import annotations

import math

from woodcam_editor.application import EditorMode

from ..compat import LEFT_BUTTON, RIGHT_BUTTON, QtCore, qt_enum
from .base import EditorTool, xy


def measurement_text(first, second):
    """Return CAD-style polar and Cartesian measurement in millimetres."""

    ax, ay = xy(first)
    bx, by = xy(second)
    dx, dy = bx - ax, by - ay
    return "L %.3f mm   ΔX %.3f   ΔY %.3f   ∠ %.2f°" % (
        math.hypot(dx, dy), dx, dy, math.degrees(math.atan2(dy, dx))
    )


class MeasureTool(EditorTool):
    """Click two snapped points; never creates or changes an entity."""

    mode = EditorMode.MEASURE
    instruction = "Medir: clique o primeiro ponto; o segundo confirma a medida."

    def __init__(self, manager):
        super(MeasureTool, self).__init__(manager)
        self.first = None
        self.locked = None

    def activate(self, entity_id=None):
        self.first = None
        self.locked = None
        self.overlays.clear_transient()
        self.manager.set_status(self.instruction)

    def cancel(self):
        super(MeasureTool, self).cancel()
        self.first = None
        self.locked = None

    def _show(self, first, second):
        self.overlays.show_path_preview((first, second))
        self.overlays.show_measure(measurement_text(first, second), second)

    def pointer_press(self, event):
        if event.button == RIGHT_BUTTON:
            self.cancel()
            self.manager.activate(EditorMode.SELECT)
            return
        if event.button != LEFT_BUTTON:
            return
        point, _candidate = self.snapped(event, reference_point=self.first)
        if self.first is None:
            self.locked = None
            self.first = point
            self.overlays.clear_measurement_preview()
            self.overlays.show_snap(_candidate)
            self.manager.set_status("Primeiro ponto medido; aponte o segundo. Esc cancela.")
            return
        self.locked = (self.first, point)
        self.first = None
        self._show(*self.locked)
        self.manager.set_status(
            "%s — clique para iniciar outra medida; Esc cancela." % measurement_text(*self.locked)
        )

    def pointer_move(self, event):
        if self.first is None:
            return
        point, _candidate = self.snapped(event, reference_point=self.first)
        self._show(self.first, point)
        self.manager.set_status("Prévia: %s" % measurement_text(self.first, point))

    def key_press(self, event):
        if event.key() == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            self.cancel()
            self.manager.activate(EditorMode.SELECT)
            event.accept()


__all__ = ["MeasureTool", "measurement_text"]
