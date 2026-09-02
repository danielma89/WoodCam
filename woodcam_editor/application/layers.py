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


class UpdateLayersCommand(Command):
    """Apply one metadata/visibility edit to several layers atomically."""

    label = "Alterar camadas"

    def __init__(self, layer_ids, **changes):
        super(UpdateLayersCommand, self).__init__()
        self.layer_ids = tuple(dict.fromkeys(str(value) for value in layer_ids))
        self.changes = dict(changes)
        if not self.layer_ids:
            raise ValueError("selecione ao menos uma camada")

    def _mutate(self, document):
        missing = set(self.layer_ids) - set(document.layers_by_id)
        if missing:
            raise KeyError("camada desconhecida: %s" % sorted(missing)[0])
        for layer_id in self.layer_ids:
            document.layers_by_id[layer_id] = replace(
                document.layers_by_id[layer_id],
                **self.changes
            )
        return DocumentChangeSet(layers_changed=frozenset(self.layer_ids))


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


__all__ = [
    "AddLayerCommand",
    "SetActiveLayerCommand",
    "UpdateLayerCommand",
    "UpdateLayersCommand",
]
