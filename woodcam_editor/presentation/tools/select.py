"""Object selection and whole-object movement."""

from __future__ import annotations

from woodcam_editor.application import EditorMode

from ..compat import (
    CTRL_MODIFIER,
    LEFT_BUTTON,
    SHIFT_MODIFIER,
    QtCore,
    qt_enum,
)
from .base import EditorTool, has_modifier, screen_distance


class SelectTool(EditorTool):
    mode = EditorMode.SELECT

    def __init__(self, manager):
        super(SelectTool, self).__init__(manager)
        self._reset()

    def _reset(self):
        self.press_screen = None
        self.press_scene = None
        self.hit_id = None
        self.dragging = False
        self.marquee = False
        self.selection_before = ()

    def activate(self, entity_id=None):
        self.overlays.clear_nodes()
        self._reset()

    def cancel(self):
        super(SelectTool, self).cancel()
        self._reset()

    def pointer_press(self, event):
        if event.button != LEFT_BUTTON:
            return
        self.press_screen = event.screen_pos
        self.press_scene = event.scene_pos
        self.selection_before = self.controller.selection.ids
        self.hit_id = self.adapter.hit_test_body(self.view, event.screen_pos)
        shift = has_modifier(event.modifiers, SHIFT_MODIFIER)
        if self.hit_id is not None:
            if shift:
                self.controller.selection.toggle(self.hit_id)
            elif self.hit_id not in self.controller.selection:
                self.controller.selection.select_only(self.hit_id)
            self.marquee = False
        else:
            self.marquee = True
            if not shift:
                self.controller.selection.clear()

    def pointer_move(self, event):
        if self.press_screen is None:
            return
        distance = screen_distance(self.press_screen, event.screen_pos)
        if distance < self.drag_threshold and not self.dragging:
            return
        self.dragging = True
        if self.marquee:
            crossing = event.screen_pos.x() < self.press_screen.x()
            self.overlays.show_marquee(self.press_scene, event.scene_pos, crossing=crossing)
            return
        if self.hit_id is None or self.hit_id not in self.controller.selection:
            return
        delta = event.scene_pos - self.press_scene
        self.adapter.preview_translation(self.controller.selection.ids, delta)
        self.overlays.show_measure(
            "ΔX %.3f mm   ΔY %.3f mm" % (delta.x(), delta.y()),
            event.scene_pos,
        )

    def pointer_release(self, event):
        if self.press_screen is None:
            return
        shift = has_modifier(event.modifiers, SHIFT_MODIFIER)
        if self.dragging and self.marquee:
            crossing = event.screen_pos.x() < self.press_screen.x()
            ids = self.adapter.select_in_screen_rect(
                self.view,
                self.press_screen,
                event.screen_pos,
                crossing=crossing,
            )
            if shift:
                result = list(self.selection_before)
                for entity_id in ids:
                    if entity_id in result:
                        result.remove(entity_id)
                    else:
                        result.append(entity_id)
                self.controller.selection.replace(result)
            else:
                self.controller.selection.replace(ids)
        elif self.dragging and self.hit_id is not None:
            delta = event.scene_pos - self.press_scene
            self.adapter.clear_preview()
            self.controller.move_entities(
                self.controller.selection.ids,
                self.controller.vec(delta.x(), delta.y()),
            )
        self.overlays.clear_transient()
        self._reset()

    def pointer_double_click(self, event):
        entity_id = self.adapter.hit_test_body(self.view, event.screen_pos)
        if entity_id is None:
            return
        self.controller.selection.select_only(entity_id)
        self.manager.activate(EditorMode.NODE_EDIT, entity_id)

    def key_press(self, event):
        key = event.key()
        modifiers = event.modifiers()
        if key in (qt_enum(QtCore.Qt, "Key_Delete", "Key"), qt_enum(QtCore.Qt, "Key_Backspace", "Key")):
            self.controller.delete_selected()
            event.accept()
            return
        if key == qt_enum(QtCore.Qt, "Key_A", "Key") and has_modifier(modifiers, CTRL_MODIFIER):
            self.controller.selection.replace(self.adapter.editable_visible_entity_ids())
            event.accept()
            return
        if key == qt_enum(QtCore.Qt, "Key_N", "Key") and self.controller.selection.primary_id:
            self.manager.activate(EditorMode.NODE_EDIT, self.controller.selection.primary_id)
            event.accept()
            return
        if key == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            if self.press_screen is not None:
                self.cancel()
            else:
                self.controller.selection.clear()
            event.accept()
            return
        step = 0.1 if has_modifier(modifiers, CTRL_MODIFIER) else 1.0
        directions = {
            qt_enum(QtCore.Qt, "Key_Left", "Key"): (-step, 0.0),
            qt_enum(QtCore.Qt, "Key_Right", "Key"): (step, 0.0),
            qt_enum(QtCore.Qt, "Key_Up", "Key"): (0.0, step),
            qt_enum(QtCore.Qt, "Key_Down", "Key"): (0.0, -step),
        }
        if key in directions and self.controller.selection.ids:
            dx, dy = directions[key]
            self.controller.move_entities(self.controller.selection.ids, self.controller.vec(dx, dy))
            event.accept()


__all__ = ["SelectTool"]
