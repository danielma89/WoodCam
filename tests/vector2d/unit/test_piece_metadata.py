"""Reversible Piece2D metadata editing and whole-piece selection."""

from __future__ import annotations

import unittest

from woodcam_editor.application import EditorController
from woodcam_editor.domain import (
    PathEntity,
    Piece2D,
    Vec2,
    VectorDocument,
    document_from_json,
    document_to_json,
)


def square(document, size, offset=0.0):
    return PathEntity.from_points(
        document.active_layer_id,
        (
            Vec2(offset, offset), Vec2(offset + size, offset),
            Vec2(offset + size, offset + size), Vec2(offset, offset + size),
        ),
        closed=True,
    )


class PieceMetadataTests(unittest.TestCase):
    def setUp(self):
        self.document = VectorDocument.create_default()
        self.outer = square(self.document, 100.0)
        self.inner = square(self.document, 20.0, 30.0)
        self.document.add_entities((self.outer, self.inner))
        self.piece = Piece2D(
            "Peça 1", self.outer.id, (self.inner.id,), id="piece-1"
        )
        self.document.add_pieces((self.piece,))
        self.controller = EditorController(self.document)

    def test_metadata_command_round_trip_undo_redo_and_json(self):
        self.controller.update_piece_metadata(
            self.piece.id,
            name="Lateral esquerda",
            quantity=4,
            material="MDF Carvalho",
            thickness=18.0,
            grain_direction=90.0,
            rotation_mode="zero_ninety",
        )
        updated = self.document.pieces_by_id[self.piece.id]
        self.assertEqual(updated.name, "Lateral esquerda")
        self.assertEqual(updated.quantity, 4)
        self.assertEqual(updated.material, "MDF Carvalho")
        self.assertEqual(updated.thickness, 18.0)
        self.assertEqual(updated.grain_direction, 90.0)
        self.assertEqual(updated.rotations_allowed, (0.0, 90.0))
        self.assertEqual(updated.metadata["rotation_mode"], "zero_ninety")
        restored = document_from_json(document_to_json(self.document))
        self.assertEqual(restored.pieces_by_id[self.piece.id], updated)
        self.controller.undo()
        self.assertEqual(self.document.pieces_by_id[self.piece.id], self.piece)
        self.controller.redo()
        self.assertEqual(self.document.pieces_by_id[self.piece.id], updated)

    def test_rotation_free_and_locked_preserve_explicit_intent(self):
        for mode, allowed in (
            ("free", (0.0, 90.0, 180.0, 270.0)),
            ("locked", (0.0,)),
        ):
            self.controller.update_piece_metadata(
                self.piece.id,
                name="Peça 1",
                quantity=1,
                material="",
                thickness=0.0,
                grain_direction=None,
                rotation_mode=mode,
            )
            piece = self.document.pieces_by_id[self.piece.id]
            self.assertEqual(piece.rotations_allowed, allowed)
            self.assertEqual(piece.metadata["rotation_mode"], mode)

    def test_select_piece_keeps_every_inner_with_outer(self):
        selected = self.controller.select_piece(self.piece.id)
        self.assertEqual(selected, (self.outer.id, self.inner.id))
        self.assertEqual(self.controller.selection.ids, selected)
        self.assertEqual(len(self.document.pieces_by_id), 1)


if __name__ == "__main__":
    unittest.main()

