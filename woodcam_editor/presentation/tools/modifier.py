"""Preview-first interactive trim/extend/offset/fillet tools."""

from __future__ import annotations

from woodcam_editor.application import EditorMode

from ..compat import LEFT_BUTTON, QtCore, qt_enum
from .base import EditorTool


class PreviewModifierTool(EditorTool):
    instruction = "Aponte uma geometria válida."

    def __init__(self, manager):
        super(PreviewModifierTool, self).__init__(manager)
        self.preview = None
        self.last_event = None

    def activate(self, entity_id=None):
        self.preview = None
        self.last_event = None
        self.overlays.clear_transient()
        self.manager.set_status(self.instruction)

    def cancel(self):
        super(PreviewModifierTool, self).cancel()
        self.preview = None
        self.last_event = None

    def pointer_move(self, event):
        self.last_event = event
        self._update_preview(event)

    def pointer_press(self, event):
        if event.button != LEFT_BUTTON:
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
        self.preview = None
        self.overlays.clear_transient()
        self.manager.set_status("Modificação aplicada. Ctrl+Z desfaz em uma etapa.")

    def key_press(self, event):
        if event.key() == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            self.cancel()
            self.manager.activate(EditorMode.SELECT)
            event.accept()

    def recompute_last_hover(self):
        if self.last_event is not None:
            self._update_preview(self.last_event)

    def _update_preview(self, event):
        try:
            preview, text = self.compute_preview(event)
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
        self.manager.set_status("Candidato inválido: %s" % reason)

    def compute_preview(self, event):
        raise NotImplementedError


class TrimTool(PreviewModifierTool):
    mode = EditorMode.TRIM
    instruction = "Trim: aponte o trecho linear entre duas interseções."

    def compute_preview(self, event):
        hit = self.adapter.hit_test_span(self.view, event.screen_pos)
        if hit is None:
            raise ValueError("aponte um segmento linear editável")
        preview = self.controller.preview_trim(hit.entity_id, hit.span_id, hit.point)
        removed = float(preview.metadata.get("removed_length_mm", 0.0))
        return preview, "Trim remove %.3f mm" % removed


class ExtendTool(PreviewModifierTool):
    mode = EditorMode.EXTEND
    instruction = "Extend: aponte a extremidade de um caminho aberto."

    def compute_preview(self, event):
        hit = self.adapter.hit_test_open_endpoint(self.view, event.screen_pos)
        if hit is None:
            raise ValueError("aponte uma ponta aberta editável")
        preview = self.controller.preview_extend(hit.entity_id, hit.span_id, hit.endpoint)
        length = float(preview.metadata.get("extension_length_mm", 0.0))
        return preview, "Estender %.3f mm" % length


class OffsetTool(PreviewModifierTool):
    mode = EditorMode.OFFSET
    instruction = "Offset: aponte um contorno linear fechado."

    def compute_preview(self, event):
        hit = self.adapter.hit_test_span(self.view, event.screen_pos)
        if hit is None:
            raise ValueError("aponte um contorno fechado editável")
        distance = float(self.manager.modifier_parameters["offset_distance"])
        preview = self.controller.preview_offset(hit.entity_id, distance)
        side = "externo" if distance > 0.0 else "interno"
        return preview, "Offset %s %.3f mm" % (side, abs(distance))


class FilletTool(PreviewModifierTool):
    mode = EditorMode.FILLET
    kind = "normal"
    instruction = "Filete: aponte um canto linear fechado."

    def compute_preview(self, event):
        hit = self.adapter.hit_test_path_node(
            self.view, event.screen_pos, closed_only=True
        )
        if hit is None:
            raise ValueError("aponte um nó de contorno fechado")
        params = self.manager.modifier_parameters
        preview = self.controller.preview_fillet(
            hit.entity_id,
            hit.node_id,
            params["fillet_radius"],
            kind=self.kind,
            contour_role=params["contour_role"],
            tbone_side=params["tbone_side"],
        )
        label = {
            "normal": "Filete",
            "dogbone": "Dogbone",
            "tbone": "T-bone",
        }[self.kind]
        warnings = tuple(getattr(preview, "warnings", ()) or ())
        suffix = " — " + warnings[0].message if warnings else ""
        return preview, "%s R %.3f mm%s" % (
            label, float(params["fillet_radius"]), suffix
        )


class DogboneTool(FilletTool):
    mode = EditorMode.DOGBONE
    kind = "dogbone"
    instruction = "Dogbone: aponte canto de 90° de um recorte interno."


class TBoneTool(FilletTool):
    mode = EditorMode.TBONE
    kind = "tbone"
    instruction = "T-bone: aponte canto de 90° de um recorte interno."


class AutomaticReliefTool(EditorTool):
    kind = "dogbone"
    mode = EditorMode.AUTO_DOGBONE

    def __init__(self, manager):
        super(AutomaticReliefTool, self).__init__(manager)
        self.preview = None

    def activate(self, entity_id=None):
        self.preview = None
        self.recompute_last_hover()

    def deactivate(self):
        self.manager.set_preview_available(False)
        super(AutomaticReliefTool, self).deactivate()

    def cancel(self):
        self.preview = None
        self.manager.set_preview_available(False)
        super(AutomaticReliefTool, self).cancel()

    def recompute_last_hover(self):
        params = self.manager.modifier_parameters
        try:
            preview = self.controller.preview_automatic_reliefs(
                params["fillet_radius"],
                kind=self.kind,
                tbone_side=params["tbone_side"],
            )
        except Exception as error:
            self.preview = None
            self.overlays.clear_transient()
            self.manager.set_preview_available(False)
            metadata = getattr(error, "metadata", {}) or {}
            rejected = int(metadata.get("rejected_count", 0))
            suffix = " (%d rejeitado(s))" % rejected if rejected else ""
            self.manager.set_status("Prévia automática indisponível: %s%s" % (error, suffix))
            return
        self.preview = preview
        self.overlays.show_modifier_preview(preview)
        applied = int(preview.metadata.get("applied_count", 0))
        rejected = int(preview.metadata.get("rejected_count", 0))
        self.manager.set_preview_available(True)
        label = "Dogbone" if self.kind == "dogbone" else "T-bone"
        self.manager.set_status(
            "Prévia %s automática: %d aplicado(s), %d rejeitado(s). "
            "Clique/Enter ou use Aplicar prévia automática; Esc cancela."
            % (label, applied, rejected)
        )

    def confirm_preview(self):
        if self.preview is None:
            return False
        preview = self.preview
        try:
            self.controller.apply_modifier_preview(preview)
        except Exception as error:
            self.manager.set_status("Não foi possível aplicar a prévia automática: %s" % error)
            return False
        self.preview = None
        self.overlays.clear_transient()
        self.manager.set_preview_available(False)
        self.manager.set_status("Alívios automáticos aplicados em um único Undo.")
        return True

    def pointer_press(self, event):
        if event.button == LEFT_BUTTON:
            self.confirm_preview()

    def key_press(self, event):
        if event.key() in (
            qt_enum(QtCore.Qt, "Key_Return", "Key"),
            qt_enum(QtCore.Qt, "Key_Enter", "Key"),
        ):
            self.confirm_preview()
            event.accept()
        elif event.key() == qt_enum(QtCore.Qt, "Key_Escape", "Key"):
            self.cancel()
            self.manager.activate(EditorMode.SELECT)
            event.accept()


class AutomaticDogboneTool(AutomaticReliefTool):
    mode = EditorMode.AUTO_DOGBONE
    kind = "dogbone"


class AutomaticTBoneTool(AutomaticReliefTool):
    mode = EditorMode.AUTO_TBONE
    kind = "tbone"


__all__ = [
    "DogboneTool",
    "AutomaticDogboneTool",
    "AutomaticReliefTool",
    "AutomaticTBoneTool",
    "ExtendTool",
    "FilletTool",
    "OffsetTool",
    "PreviewModifierTool",
    "TBoneTool",
    "TrimTool",
]
