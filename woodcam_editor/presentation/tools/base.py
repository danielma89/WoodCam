"""Common contract and helpers for modal editor tools."""

from __future__ import annotations

import math

from ..compat import SHIFT_MODIFIER, QtCore, QtWidgets


def xy(point):
    x = point.x() if callable(getattr(point, "x", None)) else point.x
    y = point.y() if callable(getattr(point, "y", None)) else point.y
    return float(x), float(y)


def screen_distance(first, second):
    return math.hypot(float(second.x() - first.x()), float(second.y() - first.y()))


def has_modifier(modifiers, modifier):
    try:
        return bool(modifiers & modifier)
    except TypeError:
        return modifiers == modifier


class EditorTool:
    mode = None

    def __init__(self, manager):
        self.manager = manager
        self.view = manager.view
        self.controller = manager.controller
        self.adapter = manager.adapter
        self.overlays = manager.overlays

    @property
    def drag_threshold(self):
        return float(QtWidgets.QApplication.startDragDistance())

    def activate(self, entity_id=None):
        pass

    def deactivate(self):
        self.cancel()

    def cancel(self):
        self.adapter.clear_preview()
        self.overlays.clear_transient()

    def snapped(self, event, excluded_ids=(), reference_point=None):
        disabled = has_modifier(event.modifiers, SHIFT_MODIFIER)
        point = self.controller.vec(event.scene_pos.x(), event.scene_pos.y())
        snapped, candidate = self.controller.snap(
            point,
            self.view.pixels_per_mm(),
            excluded_ids=excluded_ids,
            disabled=disabled,
            reference_point=reference_point,
        )
        self.overlays.show_snap(candidate)
        self.manager.set_snap_status(candidate.label if candidate else "")
        return snapped, candidate

    def pointer_press(self, event):
        pass

    def pointer_move(self, event):
        pass

    def pointer_release(self, event):
        pass

    def pointer_double_click(self, event):
        pass

    def key_press(self, event):
        pass


__all__ = ["EditorTool", "has_modifier", "screen_distance", "xy"]
