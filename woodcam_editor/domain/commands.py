"""Atomic, reversible mutations of :class:`VectorDocument`.

The production FreeCAD adapter wraps these commands in document transactions.
For pure tests and unattached documents :class:`InMemoryCommandHistory` offers
the same execute/undo/redo contract without introducing a second geometry
model.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field, replace
from typing import FrozenSet, Iterable, List, Optional, Sequence, Tuple

from .document import Piece2D, VectorDocument, WorkArea
from .entities import PathEntity, VectorEntity
from .primitives import Affine2D, GeometryError, InvariantError, Vec2
from .spans import LineSpan


class CommandStateError(RuntimeError):
    """Raised when a command is applied/reverted in an invalid order."""


@dataclass(frozen=True, slots=True)
class DocumentChangeSet:
    added: FrozenSet[str] = frozenset()
    changed: FrozenSet[str] = frozenset()
    removed: FrozenSet[str] = frozenset()
    layers_changed: FrozenSet[str] = frozenset()
    pieces_changed: FrozenSet[str] = frozenset()
    work_area_changed: bool = False

    def merged(self, other: "DocumentChangeSet") -> "DocumentChangeSet":
        added = (self.added | other.added) - (self.removed | other.removed)
        removed = (self.removed | other.removed) - (self.added | other.added)
        changed = (self.changed | other.changed) - added - removed
        return DocumentChangeSet(
            added=frozenset(added),
            changed=frozenset(changed),
            removed=frozenset(removed),
            layers_changed=self.layers_changed | other.layers_changed,
            pieces_changed=self.pieces_changed | other.pieces_changed,
            work_area_changed=self.work_area_changed or other.work_area_changed,
        )

    def reversed(self) -> "DocumentChangeSet":
        return DocumentChangeSet(
            added=self.removed,
            changed=self.changed,
            removed=self.added,
            layers_changed=self.layers_changed,
            pieces_changed=self.pieces_changed,
            work_area_changed=self.work_area_changed,
        )


def _restore_document(target: VectorDocument, snapshot: VectorDocument, revision: Optional[int] = None) -> None:
    restored = snapshot.clone()
    target.schema_version = restored.schema_version
    target.document_uuid = restored.document_uuid
    target.revision = restored.revision if revision is None else revision
    target.units = restored.units
    target.coordinate_system = restored.coordinate_system
    target.work_area = restored.work_area
    target.layers_by_id = restored.layers_by_id
    target.entities_by_id = restored.entities_by_id
    target.pieces_by_id = restored.pieces_by_id
    target.active_layer_id = restored.active_layer_id
    target.metadata = restored.metadata


class Command:
    """Base command with snapshot-backed atomicity and deterministic redo."""

    label = "Alterar desenho 2D"

    def __init__(self) -> None:
        self._before: Optional[VectorDocument] = None
        self._after: Optional[VectorDocument] = None
        self._is_applied = False
        self._forward_changes = DocumentChangeSet()

    @property
    def is_applied(self) -> bool:
        return self._is_applied

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        raise NotImplementedError

    def apply(self, document: VectorDocument) -> DocumentChangeSet:
        if self._is_applied:
            raise CommandStateError("command is already applied")
        current_revision = document.revision
        if self._after is not None:
            _restore_document(document, self._after, revision=current_revision + 1)
        else:
            self._before = document.clone()
            try:
                self._forward_changes = self._mutate(document)
                document.validate_invariants()
                document.revision = current_revision + 1
                self._after = document.clone()
            except Exception:
                _restore_document(document, self._before)
                raise
        self._is_applied = True
        return self._forward_changes

    def revert(self, document: VectorDocument) -> DocumentChangeSet:
        if not self._is_applied or self._before is None:
            raise CommandStateError("command is not currently applied")
        current_revision = document.revision
        _restore_document(document, self._before, revision=current_revision + 1)
        document.validate_invariants()
        self._is_applied = False
        return self._forward_changes.reversed()


class AddEntitiesCommand(Command):
    label = "Adicionar vetores"

    def __init__(self, entities: Iterable[VectorEntity]):
        super().__init__()
        self.entities = tuple(entities)
        if not self.entities:
            raise ValueError("AddEntitiesCommand requires at least one entity")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        document.add_entities(self.entities, bump_revision=False)
        return DocumentChangeSet(added=frozenset(entity.id for entity in self.entities))


class DeleteEntitiesCommand(Command):
    label = "Excluir vetores"

    def __init__(self, entity_ids: Iterable[str]):
        super().__init__()
        self.entity_ids = tuple(dict.fromkeys(entity_ids))
        if not self.entity_ids:
            raise ValueError("DeleteEntitiesCommand requires at least one entity ID")

    @property
    def ids(self) -> Tuple[str, ...]:
        return self.entity_ids

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        before_ids = set(document.entities_by_id)
        document.remove_entities(self.entity_ids, bump_revision=False)
        removed = before_ids - set(document.entities_by_id)
        return DocumentChangeSet(removed=frozenset(removed))


class TransformEntitiesCommand(Command):
    label = "Transformar vetores"

    def __init__(self, entity_ids: Iterable[str], transform: Affine2D):
        super().__init__()
        self.entity_ids = tuple(dict.fromkeys(entity_ids))
        self.transform = transform
        if not self.entity_ids:
            raise ValueError("TransformEntitiesCommand requires at least one entity ID")
        if not isinstance(transform, Affine2D):
            raise TypeError("transform must be Affine2D")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        transformed = []
        for entity_id in self.entity_ids:
            entity = document.get_entity(entity_id)
            transformed.append(entity.transformed(self.transform))
        document.replace_entities(transformed, bump_revision=False)
        return DocumentChangeSet(changed=frozenset(self.entity_ids))


class ReplaceEntitiesCommand(Command):
    """Replace complete entities as one exact, undoable property edit.

    Numeric primitive properties such as ellipse radii cannot be represented
    by the similarity-only transform contract without losing their exact
    meaning.  This command keeps that edit atomic while still validating that
    every replacement refers to an existing entity with the same ID.
    """

    label = "Editar propriedades exatas"

    def __init__(self, entities: Iterable[VectorEntity]):
        super().__init__()
        self.entities = tuple(entities)
        ids = tuple(entity.id for entity in self.entities)
        if not ids:
            raise ValueError("ReplaceEntitiesCommand requires at least one entity")
        if len(set(ids)) != len(ids):
            raise ValueError("replacement entity IDs must be unique")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        ids = frozenset(entity.id for entity in self.entities)
        missing = ids - set(document.entities_by_id)
        if missing:
            raise KeyError("unknown replacement entity IDs: %s" % ", ".join(sorted(missing)))
        document.replace_entities(self.entities, bump_revision=False)
        return DocumentChangeSet(changed=ids)


class MoveEntitiesCommand(TransformEntitiesCommand):
    label = "Mover vetores"

    def __init__(self, entity_ids: Iterable[str], delta: Vec2):
        if not isinstance(delta, Vec2):
            raise TypeError("delta must be Vec2")
        self.delta = delta
        super().__init__(entity_ids, Affine2D.translation(delta))


class MoveNodeCommand(Command):
    label = "Mover nó"

    def __init__(self, entity_id: str, node_id: str, new_position: Vec2):
        super().__init__()
        if not entity_id or not node_id:
            raise ValueError("MoveNodeCommand requires entity and node IDs")
        if not isinstance(new_position, Vec2):
            raise TypeError("new_position must be Vec2")
        self.entity_id = entity_id
        self.node_id = node_id
        self.new_position = new_position

    @classmethod
    def by_delta(
        cls,
        document: VectorDocument,
        entity_id: str,
        node_id: str,
        delta: Vec2,
    ) -> "MoveNodeCommand":
        entity = document.get_entity(entity_id)
        return cls(entity_id, node_id, entity.node_position(node_id) + delta)

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        entity = document.get_entity(self.entity_id)
        if not hasattr(entity, "with_node_moved"):
            raise GeometryError("entity %s does not support node editing" % self.entity_id)
        moved = entity.with_node_moved(self.node_id, self.new_position)
        document.replace_entities((moved,), bump_revision=False)
        return DocumentChangeSet(changed=frozenset((self.entity_id,)))


class SetLayerCommand(Command):
    label = "Mover vetores para camada"

    def __init__(self, entity_ids: Iterable[str], layer_id: str):
        super().__init__()
        self.entity_ids = tuple(dict.fromkeys(entity_ids))
        self.layer_id = layer_id
        if not self.entity_ids or not layer_id:
            raise ValueError("SetLayerCommand requires entities and target layer")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        if self.layer_id not in document.layers_by_id:
            raise KeyError("unknown layer ID %s" % self.layer_id)
        replacements = [replace(document.get_entity(entity_id), layer_id=self.layer_id) for entity_id in self.entity_ids]
        document.replace_entities(replacements, bump_revision=False)
        return DocumentChangeSet(changed=frozenset(self.entity_ids))


class SetWorkAreaCommand(Command):
    """Replace or clear the persisted project work area atomically."""

    label = "Alterar área de Trabalho"

    def __init__(self, work_area: Optional[WorkArea]):
        super().__init__()
        if work_area is not None and not isinstance(work_area, WorkArea):
            raise TypeError("work_area must be WorkArea or None")
        self.work_area = work_area

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        # WorkArea is frozen and validates its bounds on construction.  Piece
        # classification remains fresh because no source geometry changed.
        document.work_area = self.work_area
        return DocumentChangeSet(work_area_changed=True)


class SetDocumentMetadataCommand(Command):
    """Persist one document metadata value as an atomic undoable change."""

    label = "Alterar metadados do desenho"

    def __init__(self, key: str, value):
        super().__init__()
        self.key = str(key).strip()
        if not self.key:
            raise ValueError("metadata key cannot be empty")
        self.value = deepcopy(value)

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        if self.value is None:
            document.metadata.pop(self.key, None)
        else:
            document.metadata[self.key] = deepcopy(self.value)
        # Page outlines are projected alongside the work area.
        return DocumentChangeSet(work_area_changed=True)


class ReplacePiecesCommand(Command):
    """Replace the derived Piece2D classification as one undoable operation."""

    label = "Criar peças 2D"

    def __init__(self, pieces: Iterable[Piece2D]):
        super().__init__()
        self.pieces = tuple(replace(piece, stale=False) for piece in pieces)
        if len({piece.id for piece in self.pieces}) != len(self.pieces):
            raise ValueError("Piece2D IDs must be unique")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        old_ids = frozenset(document.pieces_by_id)
        for piece in self.pieces:
            referenced = {piece.outer_path_id, *piece.inner_path_ids}
            missing = referenced - set(document.entities_by_id)
            if missing:
                raise InvariantError(
                    "piece %s references missing entities: %s"
                    % (piece.id, ", ".join(sorted(missing)))
                )
        document.pieces_by_id = {piece.id: piece for piece in self.pieces}
        return DocumentChangeSet(
            pieces_changed=old_ids | frozenset(document.pieces_by_id)
        )


class ApplyModifierPreviewCommand(Command):
    """Apply an already-reviewed pure geometry preview atomically."""

    label = "Aplicar modificação vetorial"
    expected_operation = None

    def __init__(self, preview):
        super().__init__()
        required = ("operation", "original_entities", "result_entities")
        if not all(hasattr(preview, name) for name in required):
            raise TypeError("preview does not implement the modifier preview contract")
        if self.expected_operation is not None and preview.operation != self.expected_operation:
            raise ValueError(
                "%s requires a %s preview, received %s"
                % (type(self).__name__, self.expected_operation, preview.operation)
            )
        self.preview = preview

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        originals = tuple(self.preview.original_entities)
        results = tuple(self.preview.result_entities)
        original_by_id = {entity.id: entity for entity in originals}
        result_by_id = {entity.id: entity for entity in results}
        for entity_id, original in original_by_id.items():
            current = document.get_entity(entity_id)
            if current != original:
                raise CommandStateError(
                    "modifier preview is stale for entity %s; create a new preview"
                    % entity_id
                )
        removed = set(original_by_id) - set(result_by_id)
        changed = set(original_by_id) & set(result_by_id)
        added = set(result_by_id) - set(original_by_id)
        if removed:
            document.remove_entities(sorted(removed), bump_revision=False)
        if changed:
            document.replace_entities(
                tuple(result_by_id[entity_id] for entity_id in sorted(changed)),
                bump_revision=False,
            )
        if added:
            document.add_entities(
                tuple(result_by_id[entity_id] for entity_id in sorted(added)),
                bump_revision=False,
            )
        return DocumentChangeSet(
            added=frozenset(added),
            changed=frozenset(changed),
            removed=frozenset(removed),
        )


class TrimLineSpanCommand(ApplyModifierPreviewCommand):
    label = "Aparar segmento linear"
    expected_operation = "trim_line"


class ExtendLineSpanCommand(ApplyModifierPreviewCommand):
    label = "Estender segmento linear"
    expected_operation = "extend_line"


class OffsetPathCommand(ApplyModifierPreviewCommand):
    label = "Deslocar contorno"
    expected_operation = "offset"


class FilletCornerCommand(ApplyModifierPreviewCommand):
    label = "Aplicar filete de canto"
    expected_operation = "corner_fillet"


class ConnectEndpointToGeometryCommand(ApplyModifierPreviewCommand):
    label = "Conectar ponta à geometria"
    expected_operation = "connect_endpoint_to_geometry"


class SplicePathToContourCommand(ApplyModifierPreviewCommand):
    label = "Emendar caminho ao contorno"
    expected_operation = "splice_open_path_to_contour"


class AutoCornerReliefsCommand(ApplyModifierPreviewCommand):
    label = "Aplicar alívios automáticos"
    expected_operation = "auto_corner_reliefs"


def _endpoint_name(value: str) -> str:
    value = str(value).lower()
    if value not in ("start", "end"):
        raise ValueError("endpoint must be 'start' or 'end'")
    return value


def join_path_entities(
    first: PathEntity,
    second: PathEntity,
    first_endpoint: str = "end",
    second_endpoint: str = "start",
    tolerance: float = 0.2,
) -> PathEntity:
    """Join two open paths in any of the four endpoint orientations."""

    if first.closed or second.closed:
        raise GeometryError("only open paths can be joined")
    if first.id == second.id:
        raise GeometryError("cannot join a path to itself")
    if first.layer_id != second.layer_id:
        raise GeometryError("paths on different layers cannot be joined directly")
    first_endpoint = _endpoint_name(first_endpoint)
    second_endpoint = _endpoint_name(second_endpoint)
    left = first.reversed() if first_endpoint == "start" else first
    right = second.reversed() if second_endpoint == "end" else second
    distance = left.end.distance_to(right.start)
    if distance > tolerance:
        raise GeometryError("open endpoints are %.6g mm apart (tolerance %.6g mm)" % (distance, tolerance))
    midpoint = left.end.lerp(right.start, 0.5)
    left = left.with_node_moved(left.node_ids[-1], midpoint)
    right = right.with_node_moved(right.node_ids[0], midpoint)
    metadata = dict(left.metadata)
    joined_from = list(metadata.get("joined_from", []))
    joined_from.extend((first.id, second.id))
    metadata["joined_from"] = list(dict.fromkeys(joined_from))
    return PathEntity(
        id=first.id,
        layer_id=left.layer_id,
        spans=left.spans + right.spans,
        closed=False,
        node_ids=left.node_ids + right.node_ids[1:],
        metadata=metadata,
    )


def join_path_entities_with_line(
    first: PathEntity,
    second: PathEntity,
    first_endpoint: str = "end",
    second_endpoint: str = "start",
    tolerance: float = 0.2,
) -> PathEntity:
    """Join two explicitly chosen open endpoints without deforming the paths.

    Endpoints already inside ``tolerance`` are merged at their midpoint.  A
    larger intentional gap receives one straight bridge span.  This mirrors
    the vector-editor operation usually called *join with line* and, unlike
    increasing the tolerance, never teleports the original geometry.
    """

    if first.closed or second.closed:
        raise GeometryError("only open paths can be joined")
    if first.id == second.id:
        raise GeometryError("cannot join a path to itself")
    if first.layer_id != second.layer_id:
        raise GeometryError("paths on different layers cannot be joined directly")
    first_endpoint = _endpoint_name(first_endpoint)
    second_endpoint = _endpoint_name(second_endpoint)
    left = first.reversed() if first_endpoint == "start" else first
    right = second.reversed() if second_endpoint == "end" else second
    distance = left.end.distance_to(right.start)
    if distance <= float(tolerance):
        return join_path_entities(
            first,
            second,
            first_endpoint,
            second_endpoint,
            tolerance=float(tolerance),
        )
    bridge = LineSpan(left.end, right.start)
    metadata = dict(left.metadata)
    joined_from = list(metadata.get("joined_from", []))
    joined_from.extend((first.id, second.id))
    metadata["joined_from"] = list(dict.fromkeys(joined_from))
    metadata["joined_with_line_gap_mm"] = float(distance)
    return PathEntity(
        id=first.id,
        layer_id=left.layer_id,
        spans=left.spans + (bridge,) + right.spans,
        closed=False,
        node_ids=left.node_ids + right.node_ids,
        metadata=metadata,
    )


class JoinPathsCommand(Command):
    label = "Unir caminhos abertos"

    def __init__(
        self,
        first_path_id: str,
        second_path_id: str,
        first_endpoint: str = "end",
        second_endpoint: str = "start",
        tolerance: float = 0.2,
        mode: str = "move",
    ):
        super().__init__()
        self.first_path_id = first_path_id
        self.second_path_id = second_path_id
        self.first_endpoint = _endpoint_name(first_endpoint)
        self.second_endpoint = _endpoint_name(second_endpoint)
        self.tolerance = float(tolerance)
        self.mode = str(mode).strip().lower()
        if self.tolerance < 0.0:
            raise ValueError("join tolerance cannot be negative")
        if self.mode not in ("move", "line"):
            raise ValueError("join mode must be 'move' or 'line'")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        first = document.get_entity(self.first_path_id)
        second = document.get_entity(self.second_path_id)
        if not isinstance(first, PathEntity) or not isinstance(second, PathEntity):
            raise GeometryError("JoinPathsCommand requires two paths")
        joiner = (
            join_path_entities_with_line
            if self.mode == "line"
            else join_path_entities
        )
        joined = joiner(
            first, second, self.first_endpoint, self.second_endpoint, self.tolerance
        )
        document.remove_entities((second.id,), bump_revision=False)
        document.replace_entities((joined,), bump_revision=False)
        return DocumentChangeSet(
            changed=frozenset((first.id,)),
            removed=frozenset((second.id,)),
        )


class ClosePathCommand(Command):
    label = "Fechar caminho"

    def __init__(self, path_id: str, mode: str = "line"):
        super().__init__()
        mode = str(mode).lower()
        if mode not in ("line", "midpoint"):
            raise ValueError("close mode must be 'line' or 'midpoint'")
        self.path_id = path_id
        self.mode = mode

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        path = document.get_entity(self.path_id)
        if not isinstance(path, PathEntity) or path.closed:
            raise GeometryError("ClosePathCommand requires an open path")
        if self.mode == "line":
            closing = LineSpan(path.end, path.start)
            closed = PathEntity(
                id=path.id,
                layer_id=path.layer_id,
                spans=path.spans + (closing,),
                closed=True,
                node_ids=path.node_ids,
                metadata=path.metadata,
            )
        else:
            midpoint = path.start.lerp(path.end, 0.5)
            spans = list(path.spans)
            spans[0] = spans[0].with_start(midpoint)
            spans[-1] = spans[-1].with_end(midpoint)
            closed = PathEntity(
                id=path.id,
                layer_id=path.layer_id,
                spans=tuple(spans),
                closed=True,
                node_ids=path.node_ids[:-1],
                metadata=path.metadata,
            )
        document.replace_entities((closed,), bump_revision=False)
        return DocumentChangeSet(changed=frozenset((path.id,)))


class SplitSpanCommand(Command):
    label = "Dividir segmento"

    def __init__(self, path_id: str, span_id: str, parameter: float):
        super().__init__()
        self.path_id = path_id
        self.span_id = span_id
        self.parameter = float(parameter)

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        path = document.get_entity(self.path_id)
        if not isinstance(path, PathEntity):
            raise GeometryError("SplitSpanCommand requires a path")
        span = next((candidate for candidate in path.spans if candidate.id == self.span_id), None)
        if span is None:
            raise KeyError("unknown span ID %s" % self.span_id)
        updated = path.with_span_replaced(span.id, span.split(self.parameter))
        document.replace_entities((updated,), bump_revision=False)
        return DocumentChangeSet(changed=frozenset((path.id,)))


class CompositeCommand(Command):
    label = "Alteração composta"

    def __init__(self, commands: Iterable[Command], label: Optional[str] = None):
        super().__init__()
        self.commands = tuple(commands)
        if not self.commands:
            raise ValueError("CompositeCommand requires at least one command")
        if label:
            self.label = label

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        changes = DocumentChangeSet()
        for command in self.commands:
            changes = changes.merged(command.apply(document))
        return changes


class InMemoryCommandHistory:
    """Undo/Redo history for tests and documents not attached to FreeCAD."""

    def __init__(self, document: VectorDocument, max_depth: int = 500):
        if not isinstance(document, VectorDocument):
            raise TypeError("history requires VectorDocument")
        if max_depth < 1:
            raise ValueError("max_depth must be positive")
        self.document = document
        self.max_depth = int(max_depth)
        self._undo: List[Command] = []
        self._redo: List[Command] = []

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def undo_label(self) -> Optional[str]:
        return self._undo[-1].label if self._undo else None

    @property
    def redo_label(self) -> Optional[str]:
        return self._redo[-1].label if self._redo else None

    def execute(self, command: Command) -> DocumentChangeSet:
        if not isinstance(command, Command):
            raise TypeError("execute expects Command")
        changes = command.apply(self.document)
        self._undo.append(command)
        del self._undo[:-self.max_depth]
        self._redo.clear()
        return changes

    def undo(self) -> DocumentChangeSet:
        if not self._undo:
            raise CommandStateError("nothing to undo")
        command = self._undo.pop()
        changes = command.revert(self.document)
        self._redo.append(command)
        return changes

    def redo(self) -> DocumentChangeSet:
        if not self._redo:
            raise CommandStateError("nothing to redo")
        command = self._redo.pop()
        changes = command.apply(self.document)
        self._undo.append(command)
        return changes

    def clear(self) -> None:
        self._undo.clear()
        self._redo.clear()
