"""Transactional command execution backed by the FreeCAD document history."""

from __future__ import annotations

from typing import Any, Callable, List, Optional

from woodcam_editor.application.document_store import DocumentStoreError


def copy_document_state(target: Any, source: Any) -> None:
    """Replace a mutable VectorDocument in-place, preserving UI references."""
    snapshot = source.clone() if callable(getattr(source, "clone", None)) else source
    for name in (
        "schema_version",
        "document_uuid",
        "revision",
        "units",
        "coordinate_system",
        "work_area",
        "layers_by_id",
        "entities_by_id",
        "pieces_by_id",
        "active_layer_id",
        "metadata",
    ):
        if hasattr(snapshot, name):
            setattr(target, name, getattr(snapshot, name))
    validator = getattr(target, "validate_invariants", None)
    if callable(validator):
        validator()


class FreeCADCommandSession:
    """Execute domain commands as one FreeCAD transaction each.

    GeometryJSON is the undoable host state.  On Undo/Redo it is loaded back
    into the same VectorDocument instance so controllers and views never keep a
    stale object reference.
    """

    def __init__(self, vector_document: Any, store: Any) -> None:
        self.vector_document = vector_document
        self.store = store
        self.document = store.document
        # Some FreeCAD sessions/documents start with transaction recording
        # disabled, which leaves Ctrl+Z grey even when openTransaction is used.
        # The vector editor requires a real host history.
        try:
            if int(getattr(self.document, "UndoMode", 0) or 0) == 0:
                self.document.UndoMode = 1
        except Exception:
            pass
        # A feature can disappear after a tree deletion or host Undo.  The
        # snapshot loaded when this session opened is not a valid fallback in
        # that case: using it would resurrect vectors the user just removed.
        from woodcam_editor.domain.document import VectorDocument

        self._empty_baseline = VectorDocument.create_default(
            getattr(vector_document, "work_area", None)
        )
        self._listeners: List[Callable[[Any], None]] = []

    def subscribe(self, listener: Callable[[Any], None]) -> Callable[[], None]:
        if listener not in self._listeners:
            self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _emit(self, change_set: Any = None) -> None:
        for listener in tuple(self._listeners):
            listener(change_set)

    def execute(self, command: Any) -> Any:
        before = self.vector_document.clone()
        opened = False
        command_applied = False
        try:
            self.document.openTransaction(str(getattr(command, "label", "WoodCAM 2D — Editar vetores")))
            opened = True
            change_set = command.apply(self.vector_document)
            command_applied = True
            geometry_changed = any(
                bool(getattr(change_set, name, ()))
                for name in ("added", "changed", "removed")
            )
            self.store.save(
                self.vector_document,
                transaction_label=str(getattr(command, "label", "WoodCAM 2D — Editar vetores")),
                use_transaction=False,
                # Layer, Piece2D, work-area and document-metadata commands do
                # not alter the OCC projection. Rebuilding the 2,000+ edge
                # compound and recomputing the whole FCStd for a visibility
                # checkbox made a visual toggle take tens of seconds.
                refresh_derived_shape=bool(geometry_changed),
            )
            self.document.commitTransaction()
            opened = False
        except Exception:
            if opened:
                try:
                    self.document.abortTransaction()
                except Exception:
                    pass
            if command_applied:
                try:
                    command.revert(self.vector_document)
                except Exception:
                    # ``before`` remains the final authority even if a custom
                    # command has a defective revert implementation.
                    pass
            copy_document_state(self.vector_document, before)
            raise
        self._emit(change_set)
        return change_set

    def reload(self) -> Any:
        loaded = self.store.load()
        copy_document_state(
            self.vector_document,
            loaded if loaded is not None else self._empty_baseline,
        )
        self._emit(None)
        return self.vector_document

    def undo(self) -> Any:
        undo = getattr(self.document, "undo", None)
        if not callable(undo):
            raise DocumentStoreError("Este documento FreeCAD não oferece Undo.")
        undo()
        return self.reload()

    def redo(self) -> Any:
        redo = getattr(self.document, "redo", None)
        if not callable(redo):
            raise DocumentStoreError("Este documento FreeCAD não oferece Redo.")
        redo()
        return self.reload()


__all__ = ["FreeCADCommandSession", "copy_document_state"]
