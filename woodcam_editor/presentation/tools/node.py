"""Node editing with an explicit press/drag/release contract."""

from __future__ import annotations

from woodcam_editor.application import EditorMode

from ..compat import LEFT_BUTTON, QtCore, qt_enum
from .base import EditorTool, screen_distance, xy


class NodeTool(EditorTool):
    mode = EditorMode.NODE_EDIT

    def __init__(self, manager):
        super(NodeTool, self).__init__(manager)
        self.entity_id = None
        self.node_id = None
        self.handle_kind = "node"
        self.original = None
        self.press_screen = None
        self.press_scene = None
        self.dragging = False
        self.preview_position = None

    def activate(self, entity_id=None):
        self.entity_id = entity_id or self.controller.selection.primary_id
        if self.entity_id is None:
            self.manager.activate(EditorMode.SELECT)
            return
        self.controller.selection.select_only(self.entity_id)
        self._show_nodes()

    def deactivate(self):
        self.cancel()
        self.overlays.clear_nodes()

    def cancel(self):
        super(NodeTool, self).cancel()
        self.node_id = None
        self.handle_kind = "node"
        self.original = None
        self.press_screen = None
        self.press_scene = None
        self.dragging = False
        self.preview_position = None
        if self.entity_id:
            self._show_nodes()

    def _show_nodes(self):
        if self.entity_id is None:
            self.overlays.clear_nodes()
            return
        self.overlays.show_nodes(
            self.entity_id,
            self.controller.editable_handle_positions(self.entity_id),
            self.controller.selected_node_ids,
        )
        self.overlays.show_bezier_guides(self.controller.get_entity(self.entity_id))

    def pointer_press(self, event):
        if event.button != LEFT_BUTTON:
            return
        handle = self.overlays.hit_test_node(self.view, event.screen_pos)
        if handle is None:
            entity_id = self.adapter.hit_test_body(self.view, event.screen_pos)
            if entity_id is not None and entity_id != self.entity_id:
                self.manager.activate(EditorMode.SELECT)
                self.controller.selection.select_only(entity_id)
            return
        self.node_id = handle.node_id
        self.handle_kind = getattr(handle, "kind", "node")
        self.controller.selected_node_ids = (self.node_id,)
        if self.handle_kind == "bezier_control":
            self.original = self.controller.bezier_handle_position(self.entity_id, self.node_id)
        else:
            self.original = self.controller.get_entity(self.entity_id).node_position(self.node_id)
        self.press_screen = event.screen_pos
        self.press_scene = event.scene_pos
        self.dragging = False
        self.preview_position = self.original
        self._show_nodes()

    def pointer_move(self, event):
        if self.node_id is None or self.press_screen is None:
            return
        if not self.dragging and screen_distance(self.press_screen, event.screen_pos) < self.drag_threshold:
            return
        self.dragging = True
        ox, oy = xy(self.original)
        delta = event.scene_pos - self.press_scene
        unsnapped = self.controller.vec(ox + delta.x(), oy + delta.y())
        snapped, candidate = self.controller.snap(
            unsnapped,
            self.view.pixels_per_mm(),
            excluded_ids=(self.entity_id,),
            disabled=bool(event.modifiers & QtCore.Qt.ShiftModifier),
        )
        self.preview_position = snapped
        self.overlays.show_snap(candidate)
        preview = (
            self.controller.preview_bezier_handle(self.entity_id, self.node_id, snapped)
            if self.handle_kind == "bezier_control"
            else self.controller.preview_node(self.entity_id, self.node_id, snapped)
        )
        self.adapter.preview_entity(self.entity_id, preview)
        if preview is not None:
            self.overlays.show_nodes(
                self.entity_id,
                self.controller.editable_handle_positions_for_entity(preview),
                (self.node_id,),
            )
            self.overlays.show_bezier_guides(preview)
        nx, ny = xy(snapped)
        self.overlays.show_measure(
            "X %.3f   Y %.3f   ΔX %.3f   ΔY %.3f mm" % (nx, ny, nx - ox, ny - oy),
            event.scene_pos,
        )

    def pointer_release(self, event):
        if self.node_id is None:
            return
        entity_id = self.entity_id
        node_id = self.node_id
        handle_kind = self.handle_kind
        position = self.preview_position
        was_dragging = self.dragging
        self.adapter.clear_preview()
        self.overlays.clear_transient()
        self.node_id = None
        self.handle_kind = "node"
        self.press_screen = None
        self.press_scene = None
        self.dragging = False
        if was_dragging:
            if handle_kind == "bezier_control":
                self.controller.move_bezier_handle(entity_id, node_id, position)
            else:
                self.controller.move_node(entity_id, node_id, position)
        self._show_nodes()

    def key_press(self, event):
        key = event.key()
        if key in (qt_enum(QtCore.Qt, "Key_Escape", "Key"), qt_enum(QtCore.Qt, "Key_N", "Key")):
            if self.node_id is not None:
                self.cancel()
            else:
                self.manager.activate(EditorMode.SELECT)
            event.accept()


__all__ = ["NodeTool"]
