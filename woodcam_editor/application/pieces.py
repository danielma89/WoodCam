"""Undoable editing of Piece2D production metadata."""

from __future__ import annotations

from dataclasses import replace
import math

from woodcam_editor.domain import Command, DocumentChangeSet


ROTATION_MODES = {
    "zero": (0.0,),
    "zero_ninety": (0.0, 90.0),
    # Persist a compact, backward-compatible value.  The nesting resolver
    # expands the explicit free intent into arbitrary and contour-aligned
    # angles at search time.
    "free": (0.0, 90.0, 180.0, 270.0),
    "locked": (0.0,),
}


def nesting_rotation_angles(piece=None, step_degrees=15, outer_points=()):
    """Resolve the angles a nesting search may use for one persisted piece.

    Recognition-created pieces historically inherit ``(0, 90)`` without an
    explicit rotation decision.  Treating that schema default as an operator
    constraint prevents diagonal rails from ever becoming horizontal.  When
    there is no grain and no explicit mode, use the real arbitrary-angle
    search.  Once the operator records a mode, that intent is authoritative.
    """

    step = float(step_degrees)
    if not math.isfinite(step) or step <= 0.0 or step > 360.0:
        raise ValueError("passo de rotação precisa estar entre 0 e 360 graus")
    metadata = dict(getattr(piece, "metadata", {}) or {})
    mode = metadata.get("rotation_mode")
    if bool(metadata.get("rotation_locked", False)) or mode == "locked":
        return (0.0,)
    if mode == "zero":
        return (0.0,)
    if mode == "zero_ninety":
        return (0.0, 90.0)
    if mode == "free" or (
        mode is None and getattr(piece, "grain_direction", None) is None
    ):
        count = max(1, int(math.ceil(360.0 / step - 1.0e-12)))
        values = [
            angle
            for angle in (index * step for index in range(count))
            if angle < 360.0 - 1.0e-9
        ]

        # Online nesting tools do not rely only on a fixed angular grid: long
        # straight edges are high-value orientations.  Add up to eight unique
        # edge families and their quarter turns.  The packer later measures
        # and prunes them by the real contour footprint, keeping the search
        # bounded even when curves arrive as dense polylines.
        points = tuple(
            (float(x_value), float(y_value))
            for x_value, y_value in (outer_points or ())
        )
        edges = []
        if len(points) >= 2:
            for index, start in enumerate(points):
                end = points[(index + 1) % len(points)]
                delta_x = end[0] - start[0]
                delta_y = end[1] - start[1]
                length_squared = delta_x * delta_x + delta_y * delta_y
                if length_squared > 1.0e-18:
                    edges.append((length_squared, delta_x, delta_y, index))
        families = []
        for _length, delta_x, delta_y, index in sorted(
            edges,
            key=lambda value: (-value[0], value[3]),
        ):
            alignment = (-math.degrees(math.atan2(delta_y, delta_x))) % 90.0
            if any(abs(alignment - existing) <= 1.0e-7 for existing in families):
                continue
            families.append(alignment)
            for quarter_turn in (0.0, 90.0, 180.0, 270.0):
                candidate = (alignment + quarter_turn) % 360.0
                if not any(abs(candidate - existing) <= 1.0e-7 for existing in values):
                    values.append(candidate)
            if len(families) >= 8:
                break
        return tuple(sorted(values))

    # Unknown external modes (for example a profile already baked by
    # PanelNest) and pieces with an explicit grain keep their stored contract.
    values = []
    for raw_angle in getattr(piece, "rotations_allowed", ()) or (0.0,):
        angle = float(raw_angle) % 360.0
        if not any(abs(angle - existing) <= 1.0e-9 for existing in values):
            values.append(angle)
    return tuple(sorted(values)) or (0.0,)


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
    "nesting_rotation_angles",
    "piece_rotation_mode",
]
