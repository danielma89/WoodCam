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
from .entities import CircleEntity, EllipseEntity, GroupEntity, PathEntity, VectorEntity
from .primitives import Affine2D, GeometryError, InvariantError, Vec2, new_id
from .spans import CubicBezierSpan, LineSpan


_DEGENERATE_CLOSURE_TOLERANCE = 1.0e-6


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


class ArrayCopyCommand(Command):
    """Duplicate selected objects into an exact rectangular array.

    Every duplicate receives fresh entity/span/node IDs.  A selected group is
    copied as a new group together with its children, so a furniture panel
    made of outer profile plus holes remains one selectable compound.  The
    original vectors are never transformed or replaced.
    """

    label = "Copiar em matriz"

    def __init__(
        self,
        entity_ids: Iterable[str],
        columns: int,
        rows: int,
        step_x: float,
        step_y: float,
        source_document: Optional[VectorDocument] = None,
        origin_offset: Optional[Vec2] = None,
    ) -> None:
        super().__init__()
        self.entity_ids = tuple(dict.fromkeys(str(value) for value in entity_ids))
        self.columns = int(columns)
        self.rows = int(rows)
        self.step_x = float(step_x)
        self.step_y = float(step_y)
        self.origin_offset = origin_offset if origin_offset is not None else Vec2(0.0, 0.0)
        # A clipboard paste must keep working even if the source is moved or
        # deleted after Ctrl+C.  The optional snapshot is transient command
        # input; VectorDocument remains the sole persistent source of truth.
        self.source_document = (
            source_document.clone() if source_document is not None else None
        )
        self.created_root_ids: Tuple[str, ...] = ()
        self.created_piece_ids: Tuple[str, ...] = ()
        if not self.entity_ids:
            raise ValueError("ArrayCopyCommand requires at least one entity")
        if self.columns < 1 or self.rows < 1:
            raise ValueError("a matriz exige ao menos uma coluna e uma linha")
        if self.columns * self.rows < 2:
            raise ValueError("a matriz precisa criar ao menos uma cópia")

    @staticmethod
    def _clone_leaf(entity: VectorEntity, transform: Affine2D) -> VectorEntity:
        """Transform a drawable entity and renew every editable identity."""

        transformed = entity.transformed(transform)
        metadata = {
            **dict(getattr(transformed, "metadata", {}) or {}),
            "copy_source_id": str(entity.id),
        }
        if isinstance(transformed, PathEntity):
            return replace(
                transformed,
                id=new_id("path"),
                spans=tuple(replace(span, id=new_id("span")) for span in transformed.spans),
                node_ids=tuple(new_id("node") for _ in transformed.node_ids),
                metadata=metadata,
            )
        if isinstance(transformed, CircleEntity):
            return replace(
                transformed,
                id=new_id("circle"),
                center_node_id=new_id("node"),
                radius_node_id=new_id("node"),
                metadata=metadata,
            )
        if isinstance(transformed, EllipseEntity):
            return replace(
                transformed,
                id=new_id("ellipse"),
                center_node_id=new_id("node"),
                metadata=metadata,
            )
        raise GeometryError("tipo de vetor não suportado pela cópia em matriz")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        source_document = self.source_document or document
        missing = set(self.entity_ids) - set(source_document.entities_by_id)
        if missing:
            raise KeyError("cannot copy unknown entities: %s" % ", ".join(sorted(missing)))

        # Do not duplicate a selected child twice when its containing group is
        # also selected by an external script. The UI normally canonicalizes
        # these selections already, but the command is the safety boundary.
        selected = set(self.entity_ids)
        grouped_children = set()
        for entity_id in self.entity_ids:
            entity = source_document.get_entity(entity_id)
            if isinstance(entity, GroupEntity):
                grouped_children.update(entity.child_ids)
        roots = tuple(entity_id for entity_id in self.entity_ids if entity_id not in grouped_children)
        if not roots:
            raise GeometryError("a seleção da matriz não possui objetos raiz")

        created = []
        created_roots = []
        created_pieces = []
        for row in range(self.rows):
            for column in range(self.columns):
                if row == 0 and column == 0:
                    continue
                transform = Affine2D.translation(
                    Vec2(column * self.step_x + self.origin_offset.x,
                         row * self.step_y + self.origin_offset.y)
                )
                copies_by_source = {}

                def clone(entity_id: str) -> str:
                    entity_id = str(entity_id)
                    if entity_id in copies_by_source:
                        return copies_by_source[entity_id].id
                    source = source_document.get_entity(entity_id)
                    if source is None:
                        raise KeyError("group contains unknown entity: %s" % entity_id)
                    if isinstance(source, GroupEntity):
                        child_ids = tuple(clone(child_id) for child_id in source.child_ids)
                        copy = GroupEntity(
                            layer_id=source.layer_id,
                            child_ids=child_ids,
                            metadata={
                                **dict(source.metadata or {}),
                                "copy_source_id": source.id,
                            },
                        )
                    else:
                        copy = self._clone_leaf(source, transform)
                    copies_by_source[entity_id] = copy
                    created.append(copy)
                    return copy.id

                created_roots.extend(clone(entity_id) for entity_id in roots)

                # A recognized Piece2D is a relation over the copied vectors,
                # not duplicate geometry.  Recreate that relation only when
                # the whole physical piece (outer, holes and pockets) was
                # included in this copy.
                for piece in source_document.pieces_by_id.values():
                    references = (
                        piece.outer_path_id,
                        *piece.inner_path_ids,
                        *piece.pocket_path_ids,
                        *piece.marking_path_ids,
                    )
                    if not references or not all(
                        value in copies_by_source for value in references
                    ):
                        continue
                    metadata = dict(piece.metadata or {})
                    metadata["copy_source_piece_id"] = piece.id
                    if piece.pocket_path_ids:
                        metadata["pocket_path_ids"] = [
                            copies_by_source[value].id for value in piece.pocket_path_ids
                        ]
                    if piece.marking_path_ids:
                        metadata["marking_path_ids"] = [
                            copies_by_source[value].id
                            for value in piece.marking_path_ids
                        ]
                    created_pieces.append(
                        replace(
                            piece,
                            id=new_id("piece"),
                            outer_path_id=copies_by_source[piece.outer_path_id].id,
                            inner_path_ids=tuple(
                                copies_by_source[value].id for value in piece.inner_path_ids
                            ),
                            placement=transform @ piece.placement,
                            metadata=metadata,
                            stale=False,
                        )
                    )

        document.add_entities(tuple(created), bump_revision=False)
        document.add_pieces(tuple(created_pieces), bump_revision=False)
        self.created_root_ids = tuple(created_roots)
        self.created_piece_ids = tuple(piece.id for piece in created_pieces)
        return DocumentChangeSet(
            added=frozenset(entity.id for entity in created),
            pieces_changed=frozenset(self.created_piece_ids),
        )


class GroupEntitiesCommand(Command):
    """Persist a non-destructive object group for whole-object editing."""

    label = "Agrupar vetores"

    def __init__(self, entity_ids: Iterable[str], *, group_id: Optional[str] = None, name: str = "Grupo"):
        super().__init__()
        self.entity_ids = tuple(dict.fromkeys(str(value) for value in entity_ids))
        self.group_id = str(group_id or "")
        self.name = str(name or "Grupo")
        if len(self.entity_ids) < 2:
            raise ValueError("GroupEntitiesCommand requires at least two entities")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        missing = set(self.entity_ids) - set(document.entities_by_id)
        if missing:
            raise KeyError("cannot group unknown entities: %s" % ", ".join(sorted(missing)))
        children = tuple(document.get_entity(entity_id) for entity_id in self.entity_ids)
        # A child already represented by an explicitly selected ancestor would
        # make the same vector belong to two visible group branches.  The UI
        # normally canonicalizes this selection, but the command is the
        # safety boundary and must remain correct for scripts as well.
        selected_ids = set(self.entity_ids)
        selected_group_children = set()
        for child in children:
            if isinstance(child, GroupEntity):
                selected_group_children.update(child.child_ids)
        redundant = selected_ids.intersection(selected_group_children)
        if redundant:
            raise GeometryError(
                "cannot group an object together with its selected parent group: %s"
                % ", ".join(sorted(redundant))
            )
        layers = {entity.layer_id for entity in children}
        if len(layers) != 1:
            raise GeometryError("grouped entities must belong to the same layer")
        group_values = {
            "layer_id": children[0].layer_id,
            "child_ids": self.entity_ids,
            "metadata": {"name": self.name},
        }
        if self.group_id:
            group_values["id"] = self.group_id
        group = GroupEntity(**group_values)
        document.add_entities((group,), bump_revision=False)
        self.group_id = group.id
        return DocumentChangeSet(added=frozenset((group.id,)))


class UngroupEntitiesCommand(Command):
    """Remove group relationships while retaining every child vector."""

    label = "Desagrupar vetores"

    def __init__(self, group_ids: Iterable[str]):
        super().__init__()
        self.group_ids = tuple(dict.fromkeys(str(value) for value in group_ids))
        if not self.group_ids:
            raise ValueError("UngroupEntitiesCommand requires one or more group IDs")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        groups = []
        for group_id in self.group_ids:
            entity = document.get_entity(group_id)
            if not isinstance(entity, GroupEntity):
                raise GeometryError("entity %s is not a group" % group_id)
            groups.append(entity)
        document.remove_entities(tuple(group.id for group in groups), bump_revision=False)
        return DocumentChangeSet(removed=frozenset(group.id for group in groups))


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
        cuts = document.metadata.get("organization_remnant_cuts", ()) or ()
        retained_cuts = [
            cut for cut in cuts
            if not isinstance(cut, dict) or str(cut.get("entity_id", "")) not in removed
        ]
        remnant_metadata_changed = len(retained_cuts) != len(cuts)
        if remnant_metadata_changed:
            document.metadata["organization_remnant_cuts"] = retained_cuts
        return DocumentChangeSet(
            removed=frozenset(removed),
            work_area_changed=remnant_metadata_changed,
        )


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


class ReversePathDirectionCommand(Command):
    """Reverse exact path traversal without changing its geometric shape.

    CAM direction is meaningful for climb/conventional cutting and imported
    vectors may arrive clockwise or counter-clockwise.  Reversing must keep
    the entity and node IDs stable, so it is a normal atomic document command
    rather than a scene-level visual trick.
    """

    label = "Inverter direção dos vetores"

    def __init__(self, entity_ids: Iterable[str]):
        super().__init__()
        self.entity_ids = tuple(dict.fromkeys(str(entity_id) for entity_id in entity_ids))
        if not self.entity_ids:
            raise ValueError("ReversePathDirectionCommand requires one or more paths")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        reversed_paths = []
        for entity_id in self.entity_ids:
            entity = document.get_entity(entity_id)
            if not isinstance(entity, PathEntity):
                raise GeometryError("ReversePathDirectionCommand requires PathEntity values")
            reversed_paths.append(entity.reversed())
        document.replace_entities(tuple(reversed_paths), bump_revision=False)
        return DocumentChangeSet(changed=frozenset(self.entity_ids))


class ReplaceTextOutlinesCommand(Command):
    """Replace a text group's generated outlines while preserving its group ID."""

    label = "Editar texto vetorial"

    def __init__(
        self,
        group_id: str,
        old_child_ids: Iterable[str],
        replacement_entities: Iterable[VectorEntity],
    ) -> None:
        super().__init__()
        self.group_id = str(group_id)
        self.old_child_ids = tuple(dict.fromkeys(str(value) for value in old_child_ids))
        self.replacement_entities = tuple(replacement_entities)
        if not self.group_id or not self.old_child_ids:
            raise ValueError("ReplaceTextOutlinesCommand requires an existing text group")
        replacement_group = next(
            (
                entity
                for entity in self.replacement_entities
                if isinstance(entity, GroupEntity) and entity.id == self.group_id
            ),
            None,
        )
        if replacement_group is None:
            raise ValueError("replacement text must retain its group ID")
        self.replacement_group = replacement_group
        self.replacement_children = tuple(
            entity
            for entity in self.replacement_entities
            if entity.id != self.group_id
        )
        if not self.replacement_children:
            raise ValueError("replacement text requires outline paths")
        if set(replacement_group.child_ids) != {
            entity.id for entity in self.replacement_children
        }:
            raise ValueError("replacement text group must reference exactly its new outlines")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        group = document.get_entity(self.group_id)
        if not isinstance(group, GroupEntity):
            raise GeometryError("o grupo de texto original não existe mais")
        if tuple(group.child_ids) != self.old_child_ids:
            raise CommandStateError("o texto mudou; reabra a edição antes de aplicar")
        missing = set(self.old_child_ids) - set(document.entities_by_id)
        if missing:
            raise KeyError("missing text outlines: %s" % ", ".join(sorted(missing)))
        new_ids = {entity.id for entity in self.replacement_children}
        collisions = new_ids.intersection(document.entities_by_id)
        if collisions:
            raise GeometryError("new text outline IDs already exist: %s" % ", ".join(sorted(collisions)))
        # Replace the parent first so deleting old leaves cannot cascade-remove
        # the stable text group.  Invariants are checked only after all three
        # mutations have completed inside this one command.
        document.replace_entities((self.replacement_group,), bump_revision=False)
        document.add_entities(self.replacement_children, bump_revision=False)
        document.remove_entities(self.old_child_ids, bump_revision=False)
        return DocumentChangeSet(
            added=frozenset(new_ids),
            changed=frozenset((self.group_id,)),
            removed=frozenset(self.old_child_ids),
        )


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


class MoveBezierHandleCommand(Command):
    """Move one cubic Bézier control handle without sampling the curve."""

    label = "Mover controle Bézier"

    def __init__(self, entity_id: str, span_id: str, handle: str, new_position: Vec2):
        super().__init__()
        if not entity_id or not span_id:
            raise ValueError("MoveBezierHandleCommand requires entity and span IDs")
        if handle not in ("control1", "control2"):
            raise ValueError("Bézier handle must be control1 or control2")
        if not isinstance(new_position, Vec2):
            raise TypeError("new_position must be Vec2")
        self.entity_id = str(entity_id)
        self.span_id = str(span_id)
        self.handle = str(handle)
        self.new_position = new_position

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        entity = document.get_entity(self.entity_id)
        if not isinstance(entity, PathEntity):
            raise GeometryError("entity %s is not a path" % self.entity_id)
        replacement_spans = []
        found = False
        for span in entity.spans:
            if str(getattr(span, "id", "")) != self.span_id:
                replacement_spans.append(span)
                continue
            if not isinstance(span, CubicBezierSpan):
                raise GeometryError("span %s is not a cubic Bézier" % self.span_id)
            values = {
                "id": span.id,
                "start": span.start,
                "control1": span.control1,
                "control2": span.control2,
                "end": span.end,
            }
            values[self.handle] = self.new_position
            replacement_spans.append(CubicBezierSpan(**values))
            found = True
        if not found:
            raise KeyError("unknown Bézier span %s" % self.span_id)
        document.replace_entities((replace(entity, spans=tuple(replacement_spans)),), bump_revision=False)
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
            referenced = {
                piece.outer_path_id,
                *piece.inner_path_ids,
                *piece.pocket_path_ids,
                *piece.marking_path_ids,
            }
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


class ApplyBooleanPreviewCommand(ApplyModifierPreviewCommand):
    """Apply a closed boolean and retain its multiple rings as one object.

    The domain deliberately represents every exact ring independently (a
    CircleEntity remains a true circle rather than becoming sampled points).
    A boolean difference can therefore produce an outer ring plus detached
    internal rings.  The persistent group is the compound-vector identity:
    clicking, moving or deleting any child operates on the whole result, while
    CAM/piece recognition still sees each exact contour.
    """

    label = "Aplicar booleano vetorial"

    def __init__(self, preview, *, group_id: Optional[str] = None):
        if not str(getattr(preview, "operation", "")).startswith("boolean_"):
            raise ValueError("ApplyBooleanPreviewCommand requires a boolean preview")
        super().__init__(preview)
        self.group_id = str(group_id or "")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        changes = super()._mutate(document)
        result_ids = tuple(str(entity.id) for entity in self.preview.result_entities)
        if len(result_ids) < 2:
            self.group_id = ""
            return changes
        entities = tuple(document.get_entity(entity_id) for entity_id in result_ids)
        layer_ids = {entity.layer_id for entity in entities if entity is not None}
        if len(layer_ids) != 1 or any(entity is None for entity in entities):
            raise InvariantError("boolean result must contain valid entities on one layer")
        group = GroupEntity(
            id=self.group_id or new_id("group"),
            layer_id=next(iter(layer_ids)),
            child_ids=result_ids,
            metadata={
                "name": "Booleano %s" % str(self.preview.operation).replace("boolean_", ""),
                "source_kind": "editor_boolean_compound",
            },
        )
        self.group_id = group.id
        document.add_entities((group,), bump_revision=False)
        return changes.merged(DocumentChangeSet(added=frozenset((group.id,))))


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


def _close_path_at_midpoint(path: PathEntity) -> PathEntity:
    """Close one open path without creating a microscopic bridge span."""

    if path.closed:
        raise GeometryError("path is already closed")
    if len(path.spans) < 2:
        raise GeometryError("a closed path requires at least two spans")
    midpoint = path.start.lerp(path.end, 0.5)
    spans = list(path.spans)
    spans[0] = spans[0].with_start(midpoint)
    spans[-1] = spans[-1].with_end(midpoint)
    metadata = dict(path.metadata)
    metadata["closed_with"] = "midpoint"
    return PathEntity(
        id=path.id,
        layer_id=path.layer_id,
        spans=tuple(spans),
        closed=True,
        node_ids=path.node_ids[:-1],
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


def join_path_entities_with_smooth_curve(
    first: PathEntity,
    second: PathEntity,
    first_endpoint: str = "end",
    second_endpoint: str = "start",
    tolerance: float = 0.2,
) -> PathEntity:
    """Join two explicitly selected open endpoints with an exact Bézier bridge.

    This is the Aspire-style "join with smooth curve" operation.  It never
    moves a deliberate gap: the original paths retain their endpoints and a
    cubic span is inserted with tangents inherited from both selected ends.
    Near-coincident endpoints still use the ordinary midpoint merge, because
    creating a microscopic curve would only harm the topology.
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
    gap = left.end.distance_to(right.start)
    if gap <= float(tolerance):
        return join_path_entities(
            first, second, first_endpoint, second_endpoint, tolerance=float(tolerance)
        )
    try:
        left_tangent = left.spans[-1].tangent_at(1.0)
        right_tangent = right.spans[0].tangent_at(0.0)
    except Exception as error:
        raise GeometryError("não foi possível obter tangentes nas pontas selecionadas") from error
    handle = gap / 3.0
    bridge = CubicBezierSpan(
        left.end,
        left.end + left_tangent * handle,
        right.start - right_tangent * handle,
        right.start,
    )
    metadata = dict(left.metadata)
    joined_from = list(metadata.get("joined_from", []))
    joined_from.extend((first.id, second.id))
    metadata["joined_from"] = list(dict.fromkeys(joined_from))
    metadata["joined_with_smooth_curve_gap_mm"] = float(gap)
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
        if self.mode not in ("move", "line", "smooth"):
            raise ValueError("join mode must be 'move', 'line' or 'smooth'")

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        first = document.get_entity(self.first_path_id)
        second = document.get_entity(self.second_path_id)
        if not isinstance(first, PathEntity) or not isinstance(second, PathEntity):
            raise GeometryError("JoinPathsCommand requires two paths")
        joiner = {
            "move": join_path_entities,
            "line": join_path_entities_with_line,
            "smooth": join_path_entities_with_smooth_curve,
        }[self.mode]
        joined = joiner(
            first, second, self.first_endpoint, self.second_endpoint, self.tolerance
        )
        document.remove_entities((second.id,), bump_revision=False)
        document.replace_entities((joined,), bump_revision=False)
        return DocumentChangeSet(
            changed=frozenset((first.id,)),
            removed=frozenset((second.id,)),
        )


class JoinOpenPathsWithinToleranceCommand(Command):
    """Join all compatible selected open paths within one explicit tolerance.

    This is the batch counterpart to ``JoinPathsCommand`` and mirrors Aspire's
    *Join Open Vectors*.  It deliberately never inserts a bridge or reaches
    across a large gap: each accepted pair is merged at its midpoint only when
    the actual endpoint distance is inside the supplied tolerance.
    """

    label = "Unir vetores abertos"

    def __init__(
        self,
        entity_ids: Iterable[str],
        tolerance: float = 0.2,
        *,
        auto_close: bool = True,
    ):
        super().__init__()
        self.entity_ids = tuple(dict.fromkeys(str(entity_id) for entity_id in entity_ids))
        self.tolerance = float(tolerance)
        self.auto_close = bool(auto_close)
        if len(self.entity_ids) < 2:
            raise ValueError("Selecione pelo menos dois caminhos abertos")
        if self.tolerance < 0.0:
            raise ValueError("join tolerance cannot be negative")
        self.joined_pairs: Tuple[Tuple[str, str, float], ...] = ()
        self.closed_path_ids: Tuple[str, ...] = ()
        self.result_path_ids: Tuple[str, ...] = ()

    @staticmethod
    def _best_candidate(document: VectorDocument, active_ids: Sequence[str], ranks):
        candidates = []
        for first_index, first_id in enumerate(active_ids):
            first = document.get_entity(first_id)
            if not isinstance(first, PathEntity) or first.closed:
                continue
            for second_id in active_ids[first_index + 1:]:
                second = document.get_entity(second_id)
                if not isinstance(second, PathEntity) or second.closed:
                    continue
                if first.layer_id != second.layer_id:
                    continue
                for first_endpoint, first_point in (("start", first.start), ("end", first.end)):
                    for second_endpoint, second_point in (("start", second.start), ("end", second.end)):
                        distance = first_point.distance_to(second_point)
                        candidates.append((
                            distance,
                            min(ranks[first_id], ranks[second_id]),
                            max(ranks[first_id], ranks[second_id]),
                            first_id,
                            second_id,
                            first_endpoint,
                            second_endpoint,
                        ))
        if not candidates:
            return None
        # Stable ordering avoids a hidden geometry choice when several gaps
        # have the same measure.  The selected order breaks ties, then IDs.
        return min(candidates, key=lambda value: value[:5])

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        ranks = {entity_id: index for index, entity_id in enumerate(self.entity_ids)}
        active_ids = [
            entity_id for entity_id in self.entity_ids
            if isinstance(document.get_entity(entity_id), PathEntity)
            and not document.get_entity(entity_id).closed
        ]
        if len(active_ids) < 2:
            raise GeometryError("Selecione pelo menos dois caminhos abertos")
        changed = set()
        removed = set()
        joined = []
        while len(active_ids) >= 2:
            candidate = self._best_candidate(document, active_ids, ranks)
            if candidate is None:
                break
            distance, _first_rank, _second_rank, first_id, second_id, first_endpoint, second_endpoint = candidate
            if distance > self.tolerance:
                break
            # Preserve the earliest selected path ID as the surviving object.
            if ranks[second_id] < ranks[first_id]:
                first_id, second_id = second_id, first_id
                first_endpoint, second_endpoint = second_endpoint, first_endpoint
            first = document.get_entity(first_id)
            second = document.get_entity(second_id)
            joined_path = join_path_entities(
                first, second, first_endpoint, second_endpoint, self.tolerance
            )
            document.remove_entities((second_id,), bump_revision=False)
            document.replace_entities((joined_path,), bump_revision=False)
            active_ids.remove(second_id)
            changed.add(first_id)
            removed.add(second_id)
            joined.append((first_id, second_id, float(distance)))
        if not joined:
            raise GeometryError(
                "Nenhuma ponta selecionada está dentro da tolerância de %.6g mm" % self.tolerance
            )
        closed_path_ids = []
        if self.auto_close:
            for entity_id in active_ids:
                if entity_id not in changed:
                    continue
                path = document.get_entity(entity_id)
                if (
                    isinstance(path, PathEntity)
                    and not path.closed
                    and len(path.spans) >= 2
                    and path.start.distance_to(path.end) <= self.tolerance
                ):
                    closed_candidate = _close_path_at_midpoint(path)
                    if abs(closed_candidate.signed_area()) <= 1.0e-12:
                        continue
                    document.replace_entities(
                        (closed_candidate,),
                        bump_revision=False,
                    )
                    closed_path_ids.append(entity_id)
        self.joined_pairs = tuple(joined)
        self.closed_path_ids = tuple(closed_path_ids)
        self.result_path_ids = tuple(
            entity_id
            for entity_id in active_ids
            if entity_id in changed and document.get_entity(entity_id) is not None
        )
        return DocumentChangeSet(changed=frozenset(changed), removed=frozenset(removed))


class ClosePathCommand(Command):
    label = "Fechar caminho"

    def __init__(self, path_id: str, mode: str = "line"):
        super().__init__()
        mode = str(mode).lower()
        if mode not in ("line", "smooth", "midpoint"):
            raise ValueError("close mode must be 'line', 'smooth' or 'midpoint'")
        self.path_id = path_id
        self.mode = mode

    def _mutate(self, document: VectorDocument) -> DocumentChangeSet:
        path = document.get_entity(self.path_id)
        if not isinstance(path, PathEntity) or path.closed:
            raise GeometryError("ClosePathCommand requires an open path")
        gap = path.end.distance_to(path.start)
        if gap <= _DEGENERATE_CLOSURE_TOLERANCE:
            closed = _close_path_at_midpoint(path)
        elif self.mode == "line":
            closing = LineSpan(path.end, path.start)
            closed = PathEntity(
                id=path.id,
                layer_id=path.layer_id,
                spans=path.spans + (closing,),
                closed=True,
                node_ids=path.node_ids,
                metadata=path.metadata,
            )
        elif self.mode == "smooth":
            # Preserve both original endpoints and attach an exact Bezier
            # bridge tangent to the existing first/last spans.  This is the
            # vector-editor "close with smooth curve" behaviour: no node is
            # teleported and no raster approximation enters the document.
            first_tangent = path.spans[0].tangent_at(0.0)
            last_tangent = path.spans[-1].tangent_at(1.0)
            handle = gap / 3.0
            closing = CubicBezierSpan(
                path.end,
                path.end + last_tangent * handle,
                path.start - first_tangent * handle,
                path.start,
            )
            metadata = dict(path.metadata)
            metadata["closed_with"] = "smooth"
            closed = PathEntity(
                id=path.id,
                layer_id=path.layer_id,
                spans=path.spans + (closing,),
                closed=True,
                node_ids=path.node_ids,
                metadata=metadata,
            )
        else:
            closed = _close_path_at_midpoint(path)
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
