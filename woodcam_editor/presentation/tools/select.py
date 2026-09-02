"""Object selection and whole-object movement."""

from __future__ import annotations

from woodcam_editor.application import EditorMode

from ..compat import (
    ALT_MODIFIER,
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
        self.resize_handle = None
        self.resize_bounds = None
        self.resize_preview = None
        self.hit_was_selected = False

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
        shift = has_modifier(event.modifiers, SHIFT_MODIFIER)
        if self.controller.selection.ids and not shift:
            if self.overlays.hit_test_selection_pivot(self.view, event.screen_pos):
                # The centre control is the explicit, visible target for a
                # transform move.  Empty space inside a contour/bounds never
                # becomes a hidden hit region. Test it before nearby handles:
                # degenerate vectors can place them only a few pixels apart.
                self.hit_id = self.controller.selection.primary_id
                self.marquee = False
                return
            handle = self.overlays.hit_test_selection_handle(
                self.view, event.screen_pos
            )
            if handle is not None:
                self.resize_handle = handle
                self.resize_bounds = self.controller.selection_bounds()
                self.marquee = False
                return
        raw_hit_id = self.adapter.hit_test_body(self.view, event.screen_pos)
        self.hit_id = self.controller.grouped_selection_id_for_hit(raw_hit_id)
        self.hit_was_selected = bool(
            self.hit_id is not None and self.hit_id in self.selection_before
        )
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
        if self.resize_handle is not None and self.resize_bounds is not None:
            bounds = self._resized_bounds(event.scene_pos)
            if bounds is not None:
                self.dragging = True
                self.resize_preview = bounds
                self.overlays.show_selection_transform(bounds)
                self.overlays.show_measure(
                    "%.3f × %.3f mm" % (bounds[2] - bounds[0], bounds[3] - bounds[1]),
                    event.scene_pos,
                )
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
        if has_modifier(event.modifiers, ALT_MODIFIER):
            if abs(delta.x()) >= abs(delta.y()):
                delta.setY(0.0)
            else:
                delta.setX(0.0)
        self.adapter.preview_translation(self.controller.selection.ids, delta)
        bounds = self.controller.selection_bounds()
        if bounds is not None:
            # The scene is only a projection during drag.  Move the transient
            # selection frame by the same delta so it never lags behind the
            # previewed vectors; the document command still happens once on
            # mouse release.
            self.overlays.show_selection_transform(
                bounds.translated(self.controller.vec(delta.x(), delta.y()))
            )
        self.overlays.show_measure(
            "ΔX %.3f mm   ΔY %.3f mm" % (delta.x(), delta.y()),
            event.scene_pos,
        )

    def pointer_release(self, event):
        if self.press_screen is None:
            return
        shift = has_modifier(event.modifiers, SHIFT_MODIFIER)
        if self.resize_handle is not None and self.resize_bounds is not None:
            bounds = self.resize_preview or self._resized_bounds(event.scene_pos)
            self.overlays.clear_transient()
            if bounds is not None:
                self.controller.set_selection_bounds(
                    bounds[0],
                    bounds[1],
                    bounds[2] - bounds[0],
                    bounds[3] - bounds[1],
                    preserve_ratio=shift,
                )
            self._reset()
            if self.controller.selection.ids:
                self.overlays.show_selection_transform(
                    self.controller.selection_bounds()
                )
            return
        if self.dragging and self.marquee:
            crossing = event.screen_pos.x() < self.press_screen.x()
            raw_ids = self.adapter.select_in_screen_rect(
                self.view,
                self.press_screen,
                event.screen_pos,
                crossing=crossing,
            )
            ids = tuple(
                dict.fromkeys(
                    self.controller.grouped_selection_id_for_hit(entity_id)
                    for entity_id in raw_ids
                )
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
            if has_modifier(event.modifiers, ALT_MODIFIER):
                if abs(delta.x()) >= abs(delta.y()):
                    delta.setY(0.0)
                else:
                    delta.setX(0.0)
            self.adapter.clear_preview()
            movement = self.controller.vec(delta.x(), delta.y())
            if has_modifier(event.modifiers, CTRL_MODIFIER):
                self.controller.array_copy_selection(2, 1, movement.x, movement.y)
            else:
                self.controller.move_entities(self.controller.selection.ids, movement)
        elif (
            self.hit_id is not None
            and self.hit_was_selected
            and not shift
            and self.controller.mode == EditorMode.SELECT
        ):
            # A later click on an already selected vector exposes transform
            # handles.  Node editing is deliberately exclusive to N.
            self._reset()
            self.manager.activate(EditorMode.TRANSFORM)
            return
        elif (
            self.hit_id is not None
            and self.controller.mode == EditorMode.TRANSFORM
            and not self.hit_was_selected
        ):
            # Selecting another vector is a fresh SELECT gesture, not a
            # transform of the previous selection.
            self._reset()
            self.manager.activate(EditorMode.SELECT)
            return
        self.overlays.clear_transient()
        self._reset()

    def _resized_bounds(self, scene_pos):
        """Return a normalized preview bounds tuple for one screen handle.

        Handle indices are clockwise from visual top-left.  The bounds are
        kept in model (Y-up) coordinates and never mutate the document during
        pointer movement; commit happens once on release through the
        controller's transform command.
        """
        bounds = self.resize_bounds
        if bounds is None or self.resize_handle is None:
            return None
        x = float(scene_pos.x())
        y = float(scene_pos.y())
        min_x = float(bounds.min_x)
        min_y = float(bounds.min_y)
        max_x = float(bounds.max_x)
        max_y = float(bounds.max_y)
        epsilon = 1.0e-6
        index = int(self.resize_handle)
        if index in (0, 6, 7):
            min_x = min(x, max_x - epsilon)
        if index in (2, 3, 4):
            max_x = max(x, min_x + epsilon)
        # On screen, top is the larger Y value in the model.
        if index in (0, 1, 2):
            max_y = max(y, min_y + epsilon)
        if index in (4, 5, 6):
            min_y = min(y, max_y - epsilon)
        return min_x, min_y, max_x, max_y

    def pointer_double_click(self, event):
        # Deliberately no node-mode shortcut here.  System double-click timing
        # must not compete with the explicit SELECT -> TRANSFORM -> N flow.
        return

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
            elif self.controller.mode == EditorMode.TRANSFORM:
                self.manager.activate(EditorMode.SELECT)
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
