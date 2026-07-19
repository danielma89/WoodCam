"""Explicit two-stage connect and branch-free splice tools."""

from __future__ import annotations

from types import SimpleNamespace

from woodcam_editor.application import EditorMode

from ..compat import LEFT_BUTTON, QtCore, qt_enum
from .base import EditorTool


class TwoStageRepairTool(EditorTool):
    instruction = "Clique a geometria de origem."

    def __init__(self, manager):
        super(TwoStageRepairTool, self).__init__(manager)
        self.source = None
        self.preview = None
        self.last_event = None

    def activate(self, entity_id=None):
        self.source = None
        self.preview = None
        self.last_event = None
        self.overlays.clear_transient()
        self.manager.set_status(self.instruction)

    def cancel(self):
        super(TwoStageRepairTool, self).cancel()
        self.source = None
        self.preview = None
        self.last_event = None

    def pointer_press(self, event):
        if event.button != LEFT_BUTTON:
            return
        if self.source is None:
            source = self.pick_source(event)
            if source is None:
                return
            self.source = source
            self._show_source_marker()
            self.manager.set_status(self.source_selected_message())
            return
        self.last_event = event
        self._update_preview(event)
        if self.preview is None:
            return
        preview = self.preview
        try:
            self.controller.apply_modifier_preview(preview)
        except Exception as error:
            self._invalid("Não foi possível aplicar: %s" % error)
            return
        self.source = None
        self.preview = None
        self.last_event = None
        self.overlays.clear_transient()
        self.manager.set_status("Reparo aplicado em um Undo. Clique para iniciar outro.")

    def pointer_move(self, event):
        if self.source is None:
            return
        self.last_event = event
        self._update_preview(event)

    def key_press(self, event):
        if event.key() == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            self.cancel()
            self.manager.activate(EditorMode.SELECT)
            event.accept()

    def recompute_last_hover(self):
        if self.source is not None and self.last_event is not None:
            self._update_preview(self.last_event)

    def _show_source_marker(self):
        point = getattr(self.source, "point", None)
        if point is not None:
            self.overlays.show_snap(SimpleNamespace(point=point))

    def _update_preview(self, event):
        try:
            preview, text = self.compute_target_preview(event)
        except Exception as error:
            self._invalid(str(error))
            return
        self.preview = preview
        self.overlays.show_modifier_preview(preview)
        self.overlays.show_measure(text, event.scene_pos)
        self.manager.set_status("Prévia: %s — clique para aplicar; Esc cancela." % text)

    def _invalid(self, reason):
        self.preview = None
        self.overlays.clear_transient()
        self._show_source_marker()
        self.manager.set_status("Candidato inválido: %s" % reason)

    def pick_source(self, event):
        raise NotImplementedError

    def source_selected_message(self):
        raise NotImplementedError

    def compute_target_preview(self, event):
        raise NotImplementedError


class ConnectTool(TwoStageRepairTool):
    mode = EditorMode.CONNECT
    instruction = "Conectar: clique explicitamente uma ponta aberta de origem."

    def pick_source(self, event):
        hit = self.adapter.hit_test_open_endpoint(self.view, event.screen_pos)
        if hit is None:
            self.manager.set_status("Candidato inválido: aponte uma ponta de caminho aberto.")
        return hit

    def source_selected_message(self):
        return "Ponta %s selecionada; aponte uma reta, arco ou círculo alvo." % self.source.endpoint

    def compute_target_preview(self, event):
        target = self.adapter.hit_test_geometry_target(
            self.view,
            event.screen_pos,
            excluded_ids=(self.source.entity_id,),
        )
        if target is None:
            raise ValueError("aponte uma geometria alvo editável")
        tolerance = float(self.manager.modifier_parameters["join_tolerance"])
        preview = self.controller.preview_connect_endpoint(
            self.source.entity_id,
            self.source.endpoint,
            target.entity_id,
            target.span_id or None,
            tolerance,
        )
        distance = float(preview.metadata.get("distance_mm", 0.0))
        branch = bool(preview.metadata.get("creates_branch", False))
        warning = " — AVISO: gera ramificação" if branch else " — sem ramificação"
        return preview, "Conectar %.3f mm%s" % (distance, warning)


class JoinEndpointsTool(TwoStageRepairTool):
    """Join two endpoints chosen by the operator, even across entities."""

    mode = EditorMode.JOIN_ENDPOINTS
    instruction = "Unir 2 pontas: clique a primeira ponta de um caminho aberto."

    def pick_source(self, event):
        hit = self.adapter.hit_test_open_endpoint(self.view, event.screen_pos)
        if hit is None:
            self.manager.set_status(
                "Candidato inválido: clique uma ponta aberta; o círculo verde confirma."
            )
        return hit

    def source_selected_message(self):
        return (
            "Primeira ponta marcada; clique uma ponta de OUTRO caminho. "
            "A linha magenta será apenas a prévia."
        )

    def compute_target_preview(self, event):
        target = self.adapter.hit_test_open_endpoint(self.view, event.screen_pos)
        if target is None or target.entity_id == self.source.entity_id:
            raise ValueError("aponte uma ponta aberta de outro caminho")
        tolerance = float(self.manager.modifier_parameters["join_tolerance"])
        preview = self.controller.preview_join_endpoints(
            self.source.entity_id,
            self.source.endpoint,
            target.entity_id,
            target.endpoint,
            tolerance,
        )
        distance = float(preview.metadata.get("distance_mm", 0.0))
        mode = preview.metadata.get("join_mode")
        action = (
            "fundir no mesmo ponto"
            if mode == "merge"
            else "criar uma reta entre as pontas"
        )
        return preview, "Abertura %.3f mm — %s" % (distance, action)


class SpliceTool(TwoStageRepairTool):
    mode = EditorMode.SPLICE
    instruction = "Emendar: clique o corpo de um caminho aberto de origem."

    def pick_source(self, event):
        hit = self.adapter.hit_test_span(self.view, event.screen_pos)
        if hit is None:
            self.manager.set_status("Candidato inválido: aponte um caminho aberto.")
            return None
        entity = self.controller.get_entity(hit.entity_id)
        if entity is None or getattr(entity, "closed", True):
            self.manager.set_status("Candidato inválido: a origem precisa ser um Path aberto.")
            return None
        return hit

    def source_selected_message(self):
        return "Caminho aberto selecionado; aponte um contorno fechado ou círculo alvo."

    def compute_target_preview(self, event):
        target = self.adapter.hit_test_geometry_target(
            self.view,
            event.screen_pos,
            excluded_ids=(self.source.entity_id,),
        )
        if target is None:
            raise ValueError("aponte um contorno fechado ou círculo alvo")
        tolerance = float(self.manager.modifier_parameters["join_tolerance"])
        route = str(self.manager.modifier_parameters["splice_route"])
        preview = self.controller.preview_splice(
            self.source.entity_id,
            target.entity_id,
            route=route,
            tolerance=tolerance,
        )
        length = float(preview.metadata.get("route_length_mm", 0.0))
        route_label = "Curto" if route == "short" else "Longo"
        return preview, "Emenda %s %.3f mm — contorno alvo será consumido" % (
            route_label, length
        )


__all__ = [
    "ConnectTool",
    "JoinEndpointsTool",
    "SpliceTool",
    "TwoStageRepairTool",
]
