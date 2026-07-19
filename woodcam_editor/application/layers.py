"""Undoable layer mutations kept outside the Qt presentation."""

from __future__ import annotations

from dataclasses import replace

from woodcam_editor.domain import Command, DocumentChangeSet, Layer


class UpdateLayerCommand(Command):
    label = "Alterar camada"

    def __init__(self, layer_id, **changes):
        super(UpdateLayerCommand, self).__init__()
        self.layer_id = str(layer_id)
        self.changes = dict(changes)

    def _mutate(self, document):
        if self.layer_id not in document.layers_by_id:
            raise KeyError("camada desconhecida: %s" % self.layer_id)
        document.layers_by_id[self.layer_id] = replace(
            document.layers_by_id[self.layer_id],
            **self.changes
        )
        return DocumentChangeSet(layers_changed=frozenset((self.layer_id,)))


class SetActiveLayerCommand(Command):
    label = "Ativar camada"

    def __init__(self, layer_id):
        super(SetActiveLayerCommand, self).__init__()
        self.layer_id = str(layer_id)

    def _mutate(self, document):
        if self.layer_id not in document.layers_by_id:
            raise KeyError("camada desconhecida: %s" % self.layer_id)
        document.active_layer_id = self.layer_id
        return DocumentChangeSet(layers_changed=frozenset((self.layer_id,)))


class AddLayerCommand(Command):
    label = "Adicionar camada"

    def __init__(self, layer, make_active=True):
        super(AddLayerCommand, self).__init__()
        if not isinstance(layer, Layer):
            raise TypeError("layer deve ser Layer")
        self.layer = layer
        self.make_active = bool(make_active)

    def _mutate(self, document):
        document.add_layers((self.layer,), bump_revision=False)
        if self.make_active:
            document.active_layer_id = self.layer.id
        return DocumentChangeSet(layers_changed=frozenset((self.layer.id,)))


__all__ = ["AddLayerCommand", "SetActiveLayerCommand", "UpdateLayerCommand"]

