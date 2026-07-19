"""Undoable editing of Piece2D production metadata."""

from __future__ import annotations

from dataclasses import replace
import math

from woodcam_editor.domain import Command, DocumentChangeSet


ROTATION_MODES = {
    "zero": (0.0,),
    "zero_ninety": (0.0, 90.0),
    # The current organizer consumes explicit angles.  The metadata flag keeps
    # the user's continuous/free intent for a future arbitrary-angle packer.
    "free": (0.0, 90.0, 180.0, 270.0),
    "locked": (0.0,),
}


def piece_rotation_mode(piece):
    stored = dict(piece.metadata or {}).get("rotation_mode")
    if stored in ROTATION_MODES:
        return stored
    values = tuple(float(value) for value in piece.rotations_allowed)
    if values == (0.0, 90.0):
        return "zero_ninety"
    if values == (0.0, 90.0, 180.0, 270.0):
        return "free"
    return "zero"


class UpdatePieceMetadataCommand(Command):
    label = "Editar metadados da peça"

    def __init__(
        self,
        piece_id,
        *,
        name,
        quantity,
        material,
        thickness,
        grain_direction=None,
        rotation_mode="zero_ninety",
    ):
        super(UpdatePieceMetadataCommand, self).__init__()
        self.piece_id = str(piece_id)
        self.name = str(name).strip()
        self.quantity = int(quantity)
        self.material = str(material).strip()
        self.thickness = float(thickness)
        self.grain_direction = (
            None if grain_direction is None else float(grain_direction)
        )
        self.rotation_mode = str(rotation_mode)
        if not self.piece_id or not self.name:
            raise ValueError("peça e nome são obrigatórios")
        if self.quantity < 1:
            raise ValueError("quantidade deve ser positiva")
        if not math.isfinite(self.thickness) or self.thickness < 0.0:
            raise ValueError("espessura deve ser finita e não negativa")
        if self.grain_direction is not None and not math.isfinite(self.grain_direction):
            raise ValueError("direção do veio deve ser finita")
        if self.rotation_mode not in ROTATION_MODES:
            raise ValueError("modo de rotação desconhecido: %s" % self.rotation_mode)

    def _mutate(self, document):
        try:
            piece = document.pieces_by_id[self.piece_id]
        except KeyError:
            raise KeyError("peça desconhecida: %s" % self.piece_id)
        metadata = dict(piece.metadata or {})
        metadata["rotation_mode"] = self.rotation_mode
        metadata["rotation_locked"] = self.rotation_mode == "locked"
        document.pieces_by_id[self.piece_id] = replace(
            piece,
            name=self.name,
            quantity=self.quantity,
            material=self.material,
            thickness=self.thickness,
            grain_direction=self.grain_direction,
            rotations_allowed=ROTATION_MODES[self.rotation_mode],
            metadata=metadata,
        )
        return DocumentChangeSet(pieces_changed=frozenset((self.piece_id,)))


__all__ = [
    "ROTATION_MODES",
    "UpdatePieceMetadataCommand",
    "piece_rotation_mode",
]

