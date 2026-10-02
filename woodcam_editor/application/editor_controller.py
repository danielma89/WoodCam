"""Modal application controller for the WoodCAM 2D editor."""

from __future__ import annotations

from copy import deepcopy
from enum import Enum
import inspect
import math
import uuid
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .selection import SelectionModel
from .snapping import SnapEngine, SnapSettings


class CommandExecutionCancelled(RuntimeError):
    """Benign cancellation when the editor session/document changed."""


class EditorMode(str, Enum):
    SELECT = "select"
    TRANSFORM = "transform"
    MEASURE = "measure"
    NODE_EDIT = "node_edit"
    DRAW_LINE = "draw_line"
    DRAW_POLYLINE = "draw_polyline"
    DRAW_RECTANGLE = "draw_rectangle"
    DRAW_CIRCLE = "draw_circle"
    DRAW_ELLIPSE = "draw_ellipse"
    DRAW_ARC = "draw_arc"
    DRAW_BEZIER = "draw_bezier"
    DRAW_POLYGON = "draw_polygon"
    DRAW_STAR = "draw_star"
    TRIM = "trim"
    EXTEND = "extend"
    OFFSET = "offset"
    FILLET = "fillet"
    DOGBONE = "dogbone"
    TBONE = "tbone"
    JOIN_ENDPOINTS = "join_endpoints"
    JOIN_ENDPOINTS_SMOOTH = "join_endpoints_smooth"
    CONNECT = "connect"
    SPLICE = "splice"
    AUTO_DOGBONE = "auto_dogbone"
    AUTO_TBONE = "auto_tbone"


def _uuid(prefix: str) -> str:
    return "%s-%s" % (prefix, uuid.uuid4())


def _xy(value: Any) -> Tuple[float, float]:
    x = value.x() if callable(getattr(value, "x", None)) else value.x
    y = value.y() if callable(getattr(value, "y", None)) else value.y
    return float(x), float(y)


def _construct(cls: Any, **values: Any) -> Any:
    """Construct a domain dataclass while tolerating additive API evolution."""
    try:
        signature = inspect.signature(cls)
        accepted = {
            name: value
            for name, value in values.items()
            if name in signature.parameters
        }
        return cls(**accepted)
    except (TypeError, ValueError):
        return cls(**values)


class EditorController:
    """Coordinates session state and atomic domain commands.

    The controller owns no Qt objects.  A FreeCAD adapter may inject
    ``execute_command``, ``undo`` and ``redo`` callbacks.  Pure tests use the
    domain's ``InMemoryCommandHistory``.
    """

    PASTE_CASCADE_MM = 10.0

    def __init__(
        self,
        document: Any,
        history: Any = None,
        execute_command: Optional[Callable[[Any], Any]] = None,
        undo: Optional[Callable[[], Any]] = None,
        redo: Optional[Callable[[], Any]] = None,
        snap_settings: Optional[SnapSettings] = None,
    ) -> None:
        self.document = document
        self.selection = SelectionModel()
        self.snap_engine = SnapEngine(snap_settings)
        self.mode = EditorMode.SELECT
        self.node_entity_id: Optional[str] = None
        self.selected_node_ids: Tuple[str, ...] = ()
        self._listeners: List[Callable[[Any], None]] = []
        self._mode_listeners: List[Callable[[EditorMode], None]] = []
        self._execute_callback = execute_command
        self._undo_callback = undo
        self._redo_callback = redo
        if history is None and execute_command is None:
            try:
                from woodcam_editor.domain import InMemoryCommandHistory

                history = InMemoryCommandHistory(document)
            except Exception:
                history = None
        self.history = history
        self._clipboard_document = None
        self._clipboard_root_ids: Tuple[str, ...] = ()
        self._paste_generation = 0
        self._group_parent_cache_key = None
        self._group_parent_cache: Dict[str, str] = {}
        self._selection_bounds_cache_key = None
        self._selection_bounds_cache = None

    def subscribe_document(self, listener: Callable[[Any], None]) -> Callable[[], None]:
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener) if listener in self._listeners else None

    def subscribe_mode(self, listener: Callable[[EditorMode], None]) -> Callable[[], None]:
        self._mode_listeners.append(listener)
        return lambda: self._mode_listeners.remove(listener) if listener in self._mode_listeners else None

    def set_mode(self, mode: Any, entity_id: Optional[str] = None) -> None:
        new_mode = mode if isinstance(mode, EditorMode) else EditorMode(mode)
        if new_mode == EditorMode.NODE_EDIT:
            entity_id = entity_id or self.selection.primary_id
            entity = self.get_entity(entity_id) if entity_id else None
            supports_nodes = bool(
                entity is not None
                and (
                    getattr(entity, "spans", None)
                    or callable(getattr(entity, "nodes", None))
                )
            )
            if not supports_nodes:
                new_mode = EditorMode.SELECT
                entity_id = None
        self.mode = new_mode
        self.node_entity_id = entity_id if new_mode == EditorMode.NODE_EDIT else None
        self.selected_node_ids = ()
        for listener in tuple(self._mode_listeners):
            listener(self.mode)

    def get_entity(self, entity_id: Optional[str]) -> Any:
        if entity_id is None:
            return None
        method = getattr(self.document, "get_entity", None)
        if callable(method):
            try:
                return method(entity_id)
            except (KeyError, LookupError):
                return None
        mapping = getattr(self.document, "entities_by_id", {})
        return mapping.get(entity_id)

    def entity_ids(self) -> Tuple[str, ...]:
        mapping = getattr(self.document, "entities_by_id", {})
        return tuple(str(value) for value in mapping.keys())

    def execute(self, command: Any) -> Any:
        if self._execute_callback is not None:
            change_set = self._execute_callback(command)
        elif self.history is not None:
            executor = getattr(self.history, "execute", None)
            if not callable(executor):
                raise RuntimeError("O histórico não oferece execute(command)")
            change_set = executor(command)
        else:
            change_set = command.apply(self.document)
        self.selection.prune(self.entity_ids())
        self._notify(change_set)
        return change_set

    def undo(self) -> Any:
        callback = self._undo_callback or getattr(self.history, "undo", None)
        if not callable(callback):
            return None
        result = callback()
        self.selection.prune(self.entity_ids())
        # FreeCADCommandSession reloads the same mutable VectorDocument in
        # place.  Treat that as a full document refresh instead of passing the
        # document itself as though it were an incremental change set.
        self._notify(None if result is self.document else result)
        return result

    def redo(self) -> Any:
        callback = self._redo_callback or getattr(self.history, "redo", None)
        if not callable(callback):
            return None
        result = callback()
        self.selection.prune(self.entity_ids())
        self._notify(None if result is self.document else result)
        return result

    def document_reloaded(self) -> None:
        """Publish an externally reloaded document through the normal bus.

        FreeCAD's global Undo/Redo changes ``GeometryJSON`` outside this
        controller.  The adapter copies the restored state into the same
        document instance and then calls this method so scene and every panel
        observe one authoritative notification path.
        """
        self.selection.prune(self.entity_ids())
        self._notify(None)

    def _notify(self, change_set: Any = None) -> None:
        for listener in tuple(self._listeners):
            listener(change_set)

    def delete_selected(self) -> bool:
        selected_ids = tuple(self.selection.ids)
        ids = self._editable_entity_ids(
            tuple(selected_ids) + self.expand_group_children(selected_ids)
        )
        if not ids:
            return False
        from woodcam_editor.domain import DeleteEntitiesCommand

        command = self._command(DeleteEntitiesCommand, entity_ids=ids, ids=ids)
        self.execute(command)
        self.selection.clear()
        self.set_mode(EditorMode.SELECT)
        return True

    def move_entities(self, entity_ids: Iterable[str], delta: Any) -> bool:
        ids = self._editable_entity_ids(self.expand_group_children(entity_ids))
        dx, dy = _xy(delta)
        if not ids or math.hypot(dx, dy) <= 1e-12:
            return False
        from woodcam_editor.domain import MoveEntitiesCommand

        command = self._command(
            MoveEntitiesCommand,
            entity_ids=ids,
            ids=ids,
            delta=self.vec(dx, dy),
            dx=dx,
            dy=dy,
        )
        self.execute(command)
        return True

    def array_copy_selection(
        self,
        columns: int,
        rows: int,
        step_x: float,
        step_y: float,
    ) -> bool:
        """Create an Aspire-style rectangular copy array from the selection."""

        roots = tuple(self.selection.ids)
        self._editable_entity_ids(self.expand_group_children(roots))
        from woodcam_editor.domain import ArrayCopyCommand

        command = ArrayCopyCommand(roots, columns, rows, step_x, step_y)
        self.execute(command)
        self.selection.replace(command.created_root_ids)
        return True

    def copy_selection(self) -> int:
        """Snapshot selected whole objects into the editor-local clipboard."""

        roots = list(self.canonical_group_selection(self.selection.ids))
        selected_leaves = set(self.expand_group_children(roots))
        # A Piece2D selected by its external contour must carry every owned
        # hole and pocket even in documents that predate persistent groups.
        for piece in self.document.pieces_by_id.values():
            if piece.outer_path_id not in selected_leaves:
                continue
            for entity_id in (
                piece.outer_path_id,
                *piece.inner_path_ids,
                *piece.pocket_path_ids,
                *piece.marking_path_ids,
            ):
                grouped = self.grouped_selection_id_for_hit(entity_id)
                copy_root = grouped if grouped is not None else entity_id
                if copy_root not in roots:
                    roots.append(copy_root)
        roots = tuple(roots)
        editable = self._editable_entity_ids(self.expand_group_children(roots))
        if not roots or not editable:
            return 0
        # Snapshot only the selected object graph. Cloning a complete cabinet
        # made Ctrl+C duplicate thousands of unrelated vectors and metadata,
        # even though paste consumes only these roots and their Piece2D links.
        from woodcam_editor.domain import GroupEntity, VectorDocument

        included_ids = set()

        def include(entity_id):
            entity_id = str(entity_id)
            if entity_id in included_ids:
                return
            entity = self.get_entity(entity_id)
            if entity is None:
                return
            included_ids.add(entity_id)
            if isinstance(entity, GroupEntity):
                for child_id in entity.child_ids:
                    include(child_id)

        for root_id in roots:
            include(root_id)
        included_pieces = {
            piece_id: deepcopy(piece)
            for piece_id, piece in self.document.pieces_by_id.items()
            if all(
                reference in included_ids
                for reference in (
                    piece.outer_path_id,
                    *piece.inner_path_ids,
                    *piece.pocket_path_ids,
                    *piece.marking_path_ids,
                )
            )
        }
        included_entities = {
            entity_id: deepcopy(self.document.entities_by_id[entity_id])
            for entity_id in included_ids
        }
        included_layer_ids = {
            entity.layer_id for entity in included_entities.values()
        }
        included_layers = {
            layer_id: deepcopy(self.document.layers_by_id[layer_id])
            for layer_id in included_layer_ids
        }
        active_layer_id = (
            self.document.active_layer_id
            if self.document.active_layer_id in included_layers
            else min(included_layers)
        )
        self._clipboard_document = VectorDocument(
            schema_version=self.document.schema_version,
            document_uuid=self.document.document_uuid,
            revision=self.document.revision,
            units=self.document.units,
            coordinate_system=self.document.coordinate_system,
            work_area=deepcopy(self.document.work_area),
            layers_by_id=included_layers,
            entities_by_id=included_entities,
            pieces_by_id=included_pieces,
            active_layer_id=active_layer_id,
            metadata={},
        )
        self._clipboard_root_ids = tuple(roots)
        self._paste_generation = 0
        return len(roots)

    def paste_copied(self, target=None) -> int:
        """Paste the snapshot as one command, centred on ``target`` if given."""

        if self._clipboard_document is None or not self._clipboard_root_ids:
            return 0
        from woodcam_editor.domain import ArrayCopyCommand, Vec2

        generation = self._paste_generation + 1
        if target is None:
            step_x = self.PASTE_CASCADE_MM * generation
            step_y = 0.0
        else:
            target_x, target_y = _xy(target)
            if not (math.isfinite(target_x) and math.isfinite(target_y)):
                raise ValueError("A posição de colagem precisa ser finita.")
            bounds = [entity.bounds() for entity in
                      self._clipboard_document.entities_by_id.values()
                      if callable(getattr(entity, "bounds", None))]
            if not bounds:
                return 0
            centre_x = (min(box.min_x for box in bounds) +
                        max(box.max_x for box in bounds)) * 0.5
            centre_y = (min(box.min_y for box in bounds) +
                        max(box.max_y for box in bounds)) * 0.5
            step_x = target_x - centre_x
            step_y = target_y - centre_y
        command = ArrayCopyCommand(
            self._clipboard_root_ids,
            2,
            1,
            0.0,
            0.0,
            source_document=self._clipboard_document,
            origin_offset=Vec2(step_x, step_y),
        )
        self.execute(command)
        self._paste_generation = generation
        self.selection.replace(command.created_root_ids)
        self.set_mode(EditorMode.SELECT)
        return len(command.created_root_ids)

    def transform_entities(self, entity_ids: Iterable[str], transform: Any) -> bool:
        ids = self._editable_entity_ids(self.expand_group_children(entity_ids))
        if not ids:
            return False
        from woodcam_editor.domain import TransformEntitiesCommand

        self.execute(TransformEntitiesCommand(ids, transform))
        return True

    def replace_entities_exact(self, entities: Iterable[Any]) -> bool:
        """Commit complete primitive property replacements as one Undo step."""
        replacements = tuple(entities)
        if not replacements:
            return False
        ids = self._editable_entity_ids(entity.id for entity in replacements)
        if len(ids) != len(replacements):
            raise KeyError("one or more replacement entities no longer exist")
        from woodcam_editor.domain import ReplaceEntitiesCommand

        self.execute(ReplaceEntitiesCommand(replacements))
        return True

    def _editable_entity_ids(self, entity_ids: Iterable[str]) -> Tuple[str, ...]:
        ids = tuple(dict.fromkeys(str(value) for value in entity_ids))
        locked = []
        for entity_id in ids:
            entity = self.get_entity(entity_id)
            if entity is None:
                continue
            layer = getattr(self.document, "layers_by_id", {}).get(entity.layer_id)
            if layer is not None and layer.locked:
                locked.append(layer.name)
        if locked:
            raise PermissionError(
                "A seleção contém vetor(es) em camada bloqueada: %s"
                % ", ".join(sorted(set(locked)))
            )
        return tuple(entity_id for entity_id in ids if self.get_entity(entity_id) is not None)

    def expand_group_children(self, entity_ids: Iterable[str]) -> Tuple[str, ...]:
        """Resolve selected GroupEntity IDs to their drawable leaf vectors."""

        from woodcam_editor.domain import GroupEntity

        result = []
        seen = set()

        def visit(entity_id):
            entity_id = str(entity_id)
            if entity_id in seen:
                return
            seen.add(entity_id)
            entity = self.get_entity(entity_id)
            if isinstance(entity, GroupEntity):
                for child_id in entity.child_ids:
                    visit(child_id)
                return
            if entity is not None:
                result.append(entity_id)

        for entity_id in entity_ids:
            visit(entity_id)
        return tuple(result)

    def grouped_selection_id_for_hit(self, entity_id: Optional[str]) -> Optional[str]:
        """Return the outermost group containing a clicked child, if any."""

        if entity_id is None:
            return None
        from woodcam_editor.domain import GroupEntity

        hit_entity = self.get_entity(str(entity_id))
        hit_role = str(
            (getattr(hit_entity, "metadata", {}) or {}).get("import_role", "")
            or ""
        )
        if hit_role == "pocket_region":
            # The hatch is a first-class machining selection even when the
            # imported board also owns a group for rigid movement.
            return str(entity_id)

        cache_key = (
            id(self.document),
            int(getattr(self.document, "revision", 0)),
            len(self.document.entities_by_id),
        )
        if cache_key != self._group_parent_cache_key:
            parent_by_child = {}
            for candidate in self.document.entities_by_id.values():
                if isinstance(candidate, GroupEntity):
                    for child_id in candidate.child_ids:
                        parent_by_child[str(child_id)] = candidate.id
            self._group_parent_cache_key = cache_key
            self._group_parent_cache = parent_by_child
        parent_by_child = self._group_parent_cache
        current = str(entity_id)
        visited = set()
        while current in parent_by_child and current not in visited:
            visited.add(current)
            current = str(parent_by_child[current])
        return current

    def canonical_group_selection(self, entity_ids: Iterable[str]) -> Tuple[str, ...]:
        """Replace selected children with their outermost persistent group.

        This is used by Ctrl+A and grouping commands.  It ensures a global
        selection sees an existing group as one object, exactly as a user
        expects after clicking any member of that group.
        """

        result = []
        for entity_id in entity_ids:
            resolved = self.grouped_selection_id_for_hit(str(entity_id))
            if resolved is not None and resolved not in result:
                result.append(resolved)
        return tuple(result)

    def reverse_path_directions(
        self, entity_ids: Optional[Iterable[str]] = None
    ) -> Tuple[str, ...]:
        """Reverse selected drawable paths, expanding a selected piece group.

        Circles and ellipses have no independent traversal direction in the
        editor domain, so they are intentionally left untouched.  This lets an
        imported board group keep its holes while its outer/inner PathEntity
        contours are reversed together in one Undo step.
        """

        from woodcam_editor.domain import PathEntity, ReversePathDirectionCommand

        selected = self.selection.ids if entity_ids is None else tuple(entity_ids)
        leaf_ids = self.expand_group_children(selected)
        path_ids = tuple(
            entity_id
            for entity_id in leaf_ids
            if isinstance(self.get_entity(entity_id), PathEntity)
        )
        if not path_ids:
            return ()
        self._editable_entity_ids(path_ids)
        self.execute(ReversePathDirectionCommand(path_ids))
        return path_ids

    def rotate_selection(self, angle_degrees: float) -> bool:
        bounds = self.selection_bounds()
        if bounds is None or abs(float(angle_degrees)) <= 1e-12:
            return False
        from woodcam_editor.domain import Affine2D

        return self.transform_entities(
            self.selection.ids,
            Affine2D.rotation(math.radians(float(angle_degrees)), bounds.center),
        )

    def mirror_selection(self, axis: str) -> bool:
        bounds = self.selection_bounds()
        if bounds is None:
            return False
        from woodcam_editor.domain import Affine2D

        axis = str(axis).lower()
        if axis in ("horizontal", "h", "x"):
            # Espelho sobre uma linha horizontal: inverte Y.
            transform = Affine2D.mirror_y(bounds.center.y)
        elif axis in ("vertical", "v", "y"):
            # Espelho sobre uma linha vertical: inverte X.
            transform = Affine2D.mirror_x(bounds.center.x)
        else:
            raise ValueError("eixo deve ser 'horizontal' ou 'vertical'")
        return self.transform_entities(self.selection.ids, transform)

    def align_selection(self, alignment: str) -> bool:
        ids = self._editable_entity_ids(self.expand_group_children(self.selection.ids))
        if not ids:
            return False
        bounds_by_id = {entity_id: self.get_entity(entity_id).bounds() for entity_id in ids}
        # With one vector, alignment is relative to the persisted work area,
        # matching the CAD/CAM convention.  With two or more vectors, retain
        # the original selection-to-selection behavior.
        selection_bounds = None
        if len(ids) == 1 and self.document.work_area is not None:
            work_area = self.document.work_area
            selection_bounds = type(next(iter(bounds_by_id.values())))(
                work_area.min_x,
                work_area.min_y,
                work_area.max_x,
                work_area.max_y,
            )
        else:
            for bounds in bounds_by_id.values():
                selection_bounds = bounds if selection_bounds is None else selection_bounds.union(bounds)
        alignment = str(alignment).lower()
        commands = []
        from woodcam_editor.domain import CompositeCommand, MoveEntitiesCommand

        for entity_id, bounds in bounds_by_id.items():
            if alignment == "left":
                dx, dy = selection_bounds.min_x - bounds.min_x, 0.0
            elif alignment in ("center", "center_x"):
                dx, dy = selection_bounds.center.x - bounds.center.x, 0.0
            elif alignment == "right":
                dx, dy = selection_bounds.max_x - bounds.max_x, 0.0
            elif alignment in ("bottom", "low"):
                dx, dy = 0.0, selection_bounds.min_y - bounds.min_y
            elif alignment in ("middle", "center_y"):
                dx, dy = 0.0, selection_bounds.center.y - bounds.center.y
            elif alignment in ("top", "high"):
                dx, dy = 0.0, selection_bounds.max_y - bounds.max_y
            else:
                raise ValueError("alinhamento desconhecido: %s" % alignment)
            if math.hypot(dx, dy) > 1e-12:
                commands.append(MoveEntitiesCommand((entity_id,), self.vec(dx, dy)))
        if not commands:
            return False
        self.execute(CompositeCommand(commands, label="Alinhar vetores"))
        return True

    def distribute_selection(self, axis: str) -> bool:
        ids = self._editable_entity_ids(self.expand_group_children(self.selection.ids))
        if len(ids) < 3:
            return False
        bounds_by_id = {entity_id: self.get_entity(entity_id).bounds() for entity_id in ids}
        axis = str(axis).lower()
        horizontal = axis in ("horizontal", "h", "x")
        if not horizontal and axis not in ("vertical", "v", "y"):
            raise ValueError("eixo deve ser 'horizontal' ou 'vertical'")
        if horizontal:
            ordered = sorted(ids, key=lambda value: (bounds_by_id[value].center.x, value))
            total_extent = bounds_by_id[ordered[-1]].max_x - bounds_by_id[ordered[0]].min_x
            occupied = sum(bounds_by_id[value].width for value in ordered)
        else:
            ordered = sorted(ids, key=lambda value: (bounds_by_id[value].center.y, value))
            total_extent = bounds_by_id[ordered[-1]].max_y - bounds_by_id[ordered[0]].min_y
            occupied = sum(bounds_by_id[value].height for value in ordered)
        gap = (total_extent - occupied) / float(len(ordered) - 1)
        cursor = (
            bounds_by_id[ordered[0]].min_x
            if horizontal else bounds_by_id[ordered[0]].min_y
        )
        from woodcam_editor.domain import CompositeCommand, MoveEntitiesCommand

        commands = []
        for entity_id in ordered:
            bounds = bounds_by_id[entity_id]
            if horizontal:
                delta = self.vec(cursor - bounds.min_x, 0.0)
                cursor += bounds.width + gap
            else:
                delta = self.vec(0.0, cursor - bounds.min_y)
                cursor += bounds.height + gap
            if math.hypot(delta.x, delta.y) > 1e-12:
                commands.append(MoveEntitiesCommand((entity_id,), delta))
        if not commands:
            return False
        self.execute(CompositeCommand(commands, label="Distribuir vetores"))
        return True

    def selection_bounds(self) -> Any:
        cache_key = (
            id(self.document),
            int(getattr(self.document, "revision", 0)),
            tuple(self.selection.ids),
        )
        if cache_key == self._selection_bounds_cache_key:
            return self._selection_bounds_cache
        result = None
        for entity_id in self.expand_group_children(self.selection.ids):
            entity = self.get_entity(entity_id)
            bounds_method = getattr(entity, "bounds", None) if entity else None
            if not callable(bounds_method):
                continue
            bounds = bounds_method()
            result = bounds if result is None else result.union(bounds)
        self._selection_bounds_cache_key = cache_key
        self._selection_bounds_cache = result
        return result

    def set_selection_bounds(self, min_x: float, min_y: float, width: float, height: float, preserve_ratio: bool = False) -> bool:
        bounds = self.selection_bounds()
        if bounds is None:
            return False
        self._editable_entity_ids(self.selection.ids)
        from woodcam_editor.domain import Affine2D

        width = max(0.0, float(width))
        height = max(0.0, float(height))
        scale_x = width / bounds.width if bounds.width > 1e-12 and width > 1e-12 else 1.0
        scale_y = height / bounds.height if bounds.height > 1e-12 and height > 1e-12 else 1.0
        if preserve_ratio:
            candidates = []
            if bounds.width > 1e-12 and width > 1e-12:
                candidates.append(scale_x)
            if bounds.height > 1e-12 and height > 1e-12:
                candidates.append(scale_y)
            uniform = candidates[0] if candidates else 1.0
            scale_x = scale_y = uniform
        transform = (
            Affine2D.translation(float(min_x), float(min_y))
            @ Affine2D.scaling(scale_x, scale_y)
            @ Affine2D.translation(-bounds.min_x, -bounds.min_y)
        )
        return self.transform_entities(self.selection.ids, transform)

    def scale_selection_percent(self, percent: float) -> bool:
        """Scale the complete selection uniformly around its visual centre.

        The percentage control is deliberately a controller use-case instead
        of presentation-side geometry.  Groups and classified pieces therefore
        keep using ``transform_entities`` and produce one atomic Undo exactly
        like the existing resize handles.
        """

        bounds = self.selection_bounds()
        if bounds is None:
            return False
        self._editable_entity_ids(self.selection.ids)
        factor = float(percent) / 100.0
        if not math.isfinite(factor) or factor <= 0.0:
            raise ValueError("a escala deve ser maior que zero")
        if abs(factor - 1.0) <= 1.0e-12:
            return False
        from woodcam_editor.domain import Affine2D

        centre = bounds.center
        transform = (
            Affine2D.translation(centre.x, centre.y)
            @ Affine2D.scaling(factor, factor)
            @ Affine2D.translation(-centre.x, -centre.y)
        )
        return self.transform_entities(self.selection.ids, transform)

    def move_node(self, entity_id: str, node_id: str, new_position: Any) -> bool:
        entity = self.get_entity(entity_id)
        if entity is None:
            return False
        old = entity.node_position(node_id)
        ox, oy = _xy(old)
        nx, ny = _xy(new_position)
        if math.hypot(nx - ox, ny - oy) <= 1e-12:
            return False
        from woodcam_editor.domain import MoveNodeCommand

        command = self._command(
            MoveNodeCommand,
            entity_id=str(entity_id),
            node_id=str(node_id),
            new_position=self.vec(nx, ny),
            position=self.vec(nx, ny),
            before=old,
            after=self.vec(nx, ny),
        )
        self.execute(command)
        return True

    def preview_node(self, entity_id: str, node_id: str, position: Any) -> Any:
        entity = self.get_entity(entity_id)
        if entity is None:
            return None
        method = getattr(entity, "with_node_moved", None)
        return method(node_id, position) if callable(method) else None

    @staticmethod
    def bezier_handle_id(span_id: str, handle: str) -> str:
        if handle not in ("control1", "control2"):
            raise ValueError("Bézier handle must be control1 or control2")
        return "bezier:%s:%s" % (str(span_id), handle)

    @staticmethod
    def parse_bezier_handle_id(handle_id: str) -> Optional[Tuple[str, str]]:
        prefix, separator, remainder = str(handle_id).partition(":")
        if prefix != "bezier" or not separator:
            return None
        span_id, separator, handle = remainder.rpartition(":")
        if not separator or not span_id or handle not in ("control1", "control2"):
            return None
        return span_id, handle

    def bezier_handle_position(self, entity_id: str, handle_id: str) -> Any:
        parsed = self.parse_bezier_handle_id(handle_id)
        if parsed is None:
            raise ValueError("invalid Bézier handle ID")
        span_id, handle = parsed
        entity = self.get_entity(entity_id)
        for span in tuple(getattr(entity, "spans", ()) or ()):
            if str(getattr(span, "id", "")) == span_id and hasattr(span, handle):
                return getattr(span, handle)
        raise KeyError("unknown Bézier handle %s" % handle_id)

    def preview_bezier_handle(self, entity_id: str, handle_id: str, position: Any) -> Any:
        parsed = self.parse_bezier_handle_id(handle_id)
        if parsed is None:
            return None
        span_id, handle = parsed
        entity = self.get_entity(entity_id)
        if entity is None:
            return None
        from dataclasses import replace
        from woodcam_editor.domain import CubicBezierSpan

        replacement_spans = []
        found = False
        point = self.vec(*_xy(position))
        for span in tuple(getattr(entity, "spans", ()) or ()):
            if str(getattr(span, "id", "")) != span_id:
                replacement_spans.append(span)
                continue
            if not isinstance(span, CubicBezierSpan):
                return None
            replacement_spans.append(replace(span, **{handle: point}))
            found = True
        return replace(entity, spans=tuple(replacement_spans)) if found else None

    def move_bezier_handle(self, entity_id: str, handle_id: str, new_position: Any) -> bool:
        parsed = self.parse_bezier_handle_id(handle_id)
        if parsed is None:
            return False
        old = self.bezier_handle_position(entity_id, handle_id)
        nx, ny = _xy(new_position)
        if math.hypot(nx - old.x, ny - old.y) <= 1e-12:
            return False
        from woodcam_editor.domain import MoveBezierHandleCommand

        span_id, handle = parsed
        self.execute(MoveBezierHandleCommand(entity_id, span_id, handle, self.vec(nx, ny)))
        return True

    def node_positions(self, entity_id: str) -> Tuple[Tuple[str, Any], ...]:
        entity = self.get_entity(entity_id)
        return self.node_positions_for_entity(entity)

    def editable_handle_positions(self, entity_id: str) -> Tuple[Tuple[str, Any, str], ...]:
        entity = self.get_entity(entity_id)
        return self.editable_handle_positions_for_entity(entity)

    @classmethod
    def editable_handle_positions_for_entity(cls, entity: Any) -> Tuple[Tuple[str, Any, str], ...]:
        result = [(node_id, point, "node") for node_id, point in cls.node_positions_for_entity(entity)]
        from woodcam_editor.domain import CubicBezierSpan

        for span in tuple(getattr(entity, "spans", ()) or ()):
            if isinstance(span, CubicBezierSpan):
                result.append((cls.bezier_handle_id(span.id, "control1"), span.control1, "bezier_control"))
                result.append((cls.bezier_handle_id(span.id, "control2"), span.control2, "bezier_control"))
        return tuple(result)

    @staticmethod
    def node_positions_for_entity(entity: Any) -> Tuple[Tuple[str, Any], ...]:
        nodes_method = getattr(entity, "nodes", None) if entity else None
        if callable(nodes_method):
            nodes = tuple(nodes_method())
            if nodes:
                return tuple((str(node.id), node.point) for node in nodes)
        node_ids = tuple(getattr(entity, "node_ids", ()) or ()) if entity else ()
        method = getattr(entity, "node_position", None) if entity else None
        if callable(method) and node_ids:
            return tuple((str(node_id), method(node_id)) for node_id in node_ids)
        spans = tuple(getattr(entity, "spans", ()) or ()) if entity else ()
        if not spans:
            return ()
        result: List[Tuple[str, Any]] = []
        result.append(("node-0", spans[0].start))
        result.extend(("node-%d" % (index + 1), span.end) for index, span in enumerate(spans))
        return tuple(result)

    def add_line(self, start: Any, end: Any) -> Optional[str]:
        return self.add_polyline((start, end), closed=False)

    def add_polyline(
        self,
        points: Sequence[Any],
        closed: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        if len(points) < (3 if closed else 2):
            return None
        entity = self._path_entity(points, closed, metadata=metadata)
        self._add_entities((entity,))
        self.selection.select_only(str(entity.id))
        return str(entity.id)

    def add_rectangle(self, first: Any, opposite: Any) -> Optional[str]:
        x1, y1 = _xy(first)
        x2, y2 = _xy(opposite)
        if abs(x2 - x1) <= 1e-12 or abs(y2 - y1) <= 1e-12:
            return None
        return self.add_polyline(
            (self.vec(x1, y1), self.vec(x2, y1), self.vec(x2, y2), self.vec(x1, y2)),
            closed=True,
        )

    def add_circle(self, center: Any, radius: float) -> Optional[str]:
        if radius <= 1e-12:
            return None
        from woodcam_editor.domain import CircleEntity

        entity = _construct(
            CircleEntity,
            id=_uuid("circle"),
            layer_id=self._active_layer_id(),
            center=self.vec(*_xy(center)),
            radius=float(radius),
            metadata={},
        )
        self._add_entities((entity,))
        self.selection.select_only(str(entity.id))
        return str(entity.id)

    def add_ellipse(self, center: Any, radius_x: float, radius_y: float, rotation: float = 0.0) -> Optional[str]:
        radius_x = float(radius_x)
        radius_y = float(radius_y)
        if radius_x <= 1e-12 or radius_y <= 1e-12:
            return None
        from woodcam_editor.domain import EllipseEntity

        entity = _construct(
            EllipseEntity,
            id=_uuid("ellipse"),
            layer_id=self._active_layer_id(),
            center=self.vec(*_xy(center)),
            radius_x=radius_x,
            radius_y=radius_y,
            rotation=float(rotation),
            metadata={},
        )
        self._add_entities((entity,))
        self.selection.select_only(str(entity.id))
        return str(entity.id)

    def add_polygon(self, center: Any, radius: float, sides: int = 3, rotation: float = 0.0) -> Optional[str]:
        sides = max(3, int(sides))
        if radius <= 1e-12:
            return None
        cx, cy = _xy(center)
        points = tuple(
            self.vec(
                cx + radius * math.cos(rotation + index * 2.0 * math.pi / sides),
                cy + radius * math.sin(rotation + index * 2.0 * math.pi / sides),
            )
            for index in range(sides)
        )
        return self.add_polyline(
            points,
            closed=True,
            metadata={"primitive": "polygon", "polygon_sides": sides},
        )

    def add_star(
        self,
        center: Any,
        outer_radius: float,
        points: int = 5,
        inner_ratio: float = 0.45,
        rotation: float = 0.0,
    ) -> Optional[str]:
        """Create one closed path with alternating outer and inner star tips.

        A star is stored as ordinary exact line spans, rather than as a Qt-only
        drawing primitive, so it keeps the same node editing, CAM and Undo
        behavior as every other closed vector.
        """
        outer_radius = float(outer_radius)
        points = max(3, int(points))
        inner_ratio = min(0.95, max(0.05, float(inner_ratio)))
        if outer_radius <= 1e-12:
            return None
        cx, cy = _xy(center)
        vertices = []
        step = math.pi / float(points)
        for index in range(points * 2):
            radius = outer_radius if index % 2 == 0 else outer_radius * inner_ratio
            angle = float(rotation) + index * step
            vertices.append(self.vec(cx + radius * math.cos(angle), cy + radius * math.sin(angle)))
        return self.add_polyline(
            tuple(vertices),
            closed=True,
            metadata={
                "primitive": "star",
                "star_points": points,
                "star_inner_ratio": inner_ratio,
            },
        )

    def add_arc_three_points(self, start: Any, through: Any, end: Any) -> Optional[str]:
        center = self._circumcenter(start, through, end)
        if center is None:
            return self.add_polyline((start, through, end), closed=False)
        sx, sy = _xy(start)
        tx, ty = _xy(through)
        cx, cy = _xy(center)
        clockwise = ((sx - cx) * (ty - cy) - (sy - cy) * (tx - cx)) < 0.0
        from woodcam_editor.domain import ArcSpan, PathEntity

        span = _construct(
            ArcSpan,
            id=_uuid("span"),
            start=self.vec(*_xy(start)),
            end=self.vec(*_xy(end)),
            center=center,
            clockwise=clockwise,
        )
        entity = _construct(
            PathEntity,
            id=_uuid("path"),
            layer_id=self._active_layer_id(),
            spans=(span,),
            closed=False,
            node_ids=(_uuid("node"), _uuid("node")),
            metadata={},
        )
        self._add_entities((entity,))
        self.selection.select_only(str(entity.id))
        return str(entity.id)

    def add_bezier(self, start: Any, control1: Any, control2: Any, end: Any) -> Optional[str]:
        """Add one exact cubic Bézier as one atomic document command.

        The control handles intentionally remain geometry owned by the span;
        they are not presentation-only handles and no sampled polyline is
        inserted into ``VectorDocument``.
        """
        start = self.vec(*_xy(start))
        control1 = self.vec(*_xy(control1))
        control2 = self.vec(*_xy(control2))
        end = self.vec(*_xy(end))
        if (
            start.almost_equals(end, 1.0e-12)
            and start.almost_equals(control1, 1.0e-12)
            and start.almost_equals(control2, 1.0e-12)
        ):
            return None
        from woodcam_editor.domain import CubicBezierSpan, PathEntity

        span = _construct(
            CubicBezierSpan,
            id=_uuid("span"),
            start=start,
            control1=control1,
            control2=control2,
            end=end,
        )
        entity = _construct(
            PathEntity,
            id=_uuid("path"),
            layer_id=self._active_layer_id(),
            spans=(span,),
            closed=False,
            node_ids=(_uuid("node"), _uuid("node")),
            metadata={},
        )
        self._add_entities((entity,))
        self.selection.select_only(str(entity.id))
        return str(entity.id)

    def _path_entity(
        self,
        points: Sequence[Any],
        closed: bool,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Any:
        from woodcam_editor.domain import LineSpan, PathEntity

        values = tuple(self.vec(*_xy(point)) for point in points)
        pairs = list(zip(values, values[1:]))
        if closed:
            pairs.append((values[-1], values[0]))
        spans = tuple(
            _construct(LineSpan, id=_uuid("span"), start=start, end=end)
            for start, end in pairs
        )
        node_count = len(values)
        return _construct(
            PathEntity,
            id=_uuid("path"),
            layer_id=self._active_layer_id(),
            spans=spans,
            closed=bool(closed),
            node_ids=tuple(_uuid("node") for _ in range(node_count)),
            metadata=dict(metadata or {}),
        )

    def _add_entities(self, entities: Iterable[Any]) -> None:
        from woodcam_editor.domain import AddEntitiesCommand

        entities = tuple(entities)
        self.execute(self._command(AddEntitiesCommand, entities=entities))

    @staticmethod
    def _command(command_class: Any, **values: Any) -> Any:
        try:
            signature = inspect.signature(command_class)
            accepted = {key: value for key, value in values.items() if key in signature.parameters}
            return command_class(**accepted)
        except (TypeError, ValueError):
            return command_class(**values)

    def _active_layer_id(self) -> str:
        value = getattr(self.document, "active_layer_id", None)
        if value:
            return str(value)
        layers = getattr(self.document, "layers_by_id", {})
        if layers:
            return str(next(iter(layers.keys())))
        return "layer-default"

    def add_layer(self, name: str = "Nova camada") -> str:
        from woodcam_editor.domain import Layer
        from .layers import AddLayerCommand

        existing = {layer.name for layer in self.document.layers_by_id.values()}
        base = (name or "Nova camada").strip()
        candidate = base
        index = 2
        while candidate in existing:
            candidate = "%s %d" % (base, index)
            index += 1
        layer = Layer(name=candidate, order=len(self.document.layers_by_id))
        self.execute(AddLayerCommand(layer, make_active=True))
        return layer.id

    def set_active_layer(self, layer_id: str) -> None:
        from .layers import SetActiveLayerCommand

        self.execute(SetActiveLayerCommand(layer_id))

    def update_layer(self, layer_id: str, **changes: Any) -> None:
        from .layers import UpdateLayerCommand

        self.execute(UpdateLayerCommand(layer_id, **changes))

    def update_layers(self, layer_ids: Iterable[str], **changes: Any) -> None:
        from .layers import UpdateLayersCommand

        self.execute(UpdateLayersCommand(layer_ids, **changes))

    def move_selection_to_layer(self, layer_id: str) -> bool:
        if not self.selection.ids:
            return False
        from woodcam_editor.domain import SetLayerCommand

        self.execute(SetLayerCommand(self.selection.ids, layer_id))
        return True

    def _visible_path_entities(self, excluded_ids: Iterable[str] = ()) -> Tuple[Any, ...]:
        excluded = set(str(value) for value in excluded_ids)
        result = []
        for entity in getattr(self.document, "entities_by_id", {}).values():
            if str(entity.id) in excluded or not getattr(entity, "spans", None):
                continue
            layer = getattr(self.document, "layers_by_id", {}).get(entity.layer_id)
            if layer is not None and layer.visible:
                result.append(entity)
        return tuple(result)

    def preview_trim(self, entity_id: str, span_id: str, click_point: Any) -> Any:
        self._editable_entity_ids((entity_id,))
        from woodcam_editor.geometry.modifiers import preview_trim_at_point

        path = self.get_entity(entity_id)
        cutters = self._visible_path_entities((entity_id,))
        return preview_trim_at_point(path, span_id, self.vec(*_xy(click_point)), cutters)

    def preview_extend(self, entity_id: str, span_id: str, endpoint: str) -> Any:
        self._editable_entity_ids((entity_id,))
        from woodcam_editor.geometry.modifiers import preview_extend_line_span

        path = self.get_entity(entity_id)
        boundaries = self._visible_path_entities((entity_id,))
        return preview_extend_line_span(path, span_id, endpoint, boundaries)

    def preview_offset(self, entity_id: str, distance: float) -> Any:
        self._editable_entity_ids((entity_id,))
        from woodcam_editor.geometry.modifiers import preview_offset_closed_path

        return preview_offset_closed_path(self.get_entity(entity_id), float(distance))

    def preview_fillet(
        self,
        entity_id: str,
        node_id: str,
        radius: float,
        kind: str = "normal",
        contour_role: str = "auto",
        tbone_side: str = "auto",
    ) -> Any:
        self._editable_entity_ids((entity_id,))
        from woodcam_editor.geometry.modifiers import preview_corner_fillet

        resolved_role = str(contour_role or "auto").lower()
        if kind in ("dogbone", "tbone") and resolved_role == "auto":
            resolved_role = self.contour_role_for_entity(entity_id)
            if resolved_role is None:
                raise ValueError(
                    "não foi possível identificar se o contorno é externo ou interno; "
                    "classifique a peça ou escolha o papel do contorno no painel"
                )
        return preview_corner_fillet(
            self.get_entity(entity_id),
            node_id,
            float(radius),
            kind=kind,
            contour_role=resolved_role,
            tbone_side=tbone_side,
        )

    def contour_role_for_entity(self, entity_id: str) -> Optional[str]:
        """Infer ``outer``/``inner`` from Piece2D or contour containment."""

        entity_id = str(entity_id)
        for piece in self.document.pieces_by_id.values():
            if entity_id == piece.outer_path_id:
                return "outer"
            if entity_id in piece.inner_path_ids:
                return "inner"
        try:
            from woodcam_editor.domain import build_containment_tree

            tree = build_containment_tree(self.document)
            if entity_id in tree.depth_by_id:
                return "outer" if tree.depth_by_id[entity_id] % 2 == 0 else "inner"
        except Exception:
            pass
        return None

    def preview_join_endpoints(
        self,
        first_entity_id: str,
        first_endpoint: str,
        second_entity_id: str,
        second_endpoint: str,
        tolerance: float = 0.2,
        mode: str = "line",
    ) -> Any:
        """Pure preview for an explicit point-to-point join.

        Near-coincident endpoints are merged; a deliberate larger gap is
        bridged by one line so neither source contour is teleported.
        """

        self._editable_entity_ids((first_entity_id, second_entity_id))
        from woodcam_editor.domain import (
            join_path_entities_with_line,
            join_path_entities_with_smooth_curve,
        )
        from woodcam_editor.geometry.modifiers import ModifierPreview

        first = self.get_entity(first_entity_id)
        second = self.get_entity(second_entity_id)
        mode = str(mode or "line").lower()
        if mode not in ("line", "smooth"):
            raise ValueError("modo de união deve ser 'line' ou 'smooth'")
        joiner = (
            join_path_entities_with_smooth_curve
            if mode == "smooth"
            else join_path_entities_with_line
        )
        joined = joiner(
            first,
            second,
            first_endpoint,
            second_endpoint,
            tolerance=float(tolerance),
        )
        first_point = first.start if first_endpoint == "start" else first.end
        second_point = second.start if second_endpoint == "start" else second.end
        distance = first_point.distance_to(second_point)
        return ModifierPreview(
            "join_paths_with_line",
            (first, second),
            (joined,),
            {
                "distance_mm": float(distance),
                "join_mode": "merge" if distance <= float(tolerance) else mode,
                "first_endpoint": first_endpoint,
                "second_endpoint": second_endpoint,
            },
            (first_point, second_point),
        )

    def apply_modifier_preview(self, preview: Any) -> bool:
        if preview is None:
            return False
        self._editable_entity_ids(entity.id for entity in preview.original_entities)
        from woodcam_editor.domain import (
            ApplyBooleanPreviewCommand,
            ApplyModifierPreviewCommand,
            ConnectEndpointToGeometryCommand,
            SplicePathToContourCommand,
        )

        command_class = {
            "connect_endpoint_to_geometry": ConnectEndpointToGeometryCommand,
            "splice_open_path_to_contour": SplicePathToContourCommand,
        }.get(preview.operation, ApplyModifierPreviewCommand)
        if str(preview.operation).startswith("boolean_"):
            command_class = ApplyBooleanPreviewCommand
        if preview.operation == "auto_corner_reliefs":
            from woodcam_editor.domain import AutoCornerReliefsCommand

            command_class = AutoCornerReliefsCommand
        command = command_class(preview)
        self.execute(command)
        result_ids = tuple(entity.id for entity in preview.result_entities)
        group_id = str(getattr(command, "group_id", "") or "")
        self.selection.replace((group_id,) if group_id else result_ids)
        return True

    def preview_connect_endpoint(
        self,
        source_entity_id: str,
        endpoint: str,
        target_entity_id: str,
        target_span_id: Optional[str] = None,
        tolerance: float = 0.2,
    ) -> Any:
        self._editable_entity_ids((source_entity_id, target_entity_id))
        from woodcam_editor.geometry.modifiers import (
            preview_connect_endpoint_to_geometry,
        )

        return preview_connect_endpoint_to_geometry(
            self.get_entity(source_entity_id),
            endpoint,
            self.get_entity(target_entity_id),
            target_span_id=target_span_id or None,
            tolerance=float(tolerance),
        )

    def preview_splice(
        self,
        source_entity_id: str,
        target_entity_id: str,
        route: str = "short",
        tolerance: float = 0.2,
    ) -> Any:
        self._editable_entity_ids((source_entity_id, target_entity_id))
        from woodcam_editor.geometry.modifiers import (
            preview_splice_open_path_to_contour,
        )

        return preview_splice_open_path_to_contour(
            self.get_entity(source_entity_id),
            self.get_entity(target_entity_id),
            route=str(route),
            tolerance=float(tolerance),
        )

    def automatic_relief_scope(self) -> Tuple[Tuple[Any, ...], Dict[str, str]]:
        """Resolve the complete classified job, or selected loose paths.

        A stale selection must not silently turn the command labelled as a
        *total automatic preview* into a partial operation.  When Piece2D
        relationships exist they are the authoritative job scope.  Selection
        remains the fallback for loose, not-yet-classified vector work.
        """
        selected_ids = tuple(self.selection.ids)
        candidate_ids_list = []
        for piece in self.document.pieces_by_id.values():
            candidate_ids_list.append(piece.outer_path_id)
            candidate_ids_list.extend(piece.inner_path_ids)
        if candidate_ids_list:
            candidate_ids = tuple(dict.fromkeys(candidate_ids_list))
        else:
            candidate_ids = self.expand_group_children(selected_ids)
        paths = tuple(
            self.get_entity(entity_id)
            for entity_id in candidate_ids
            if self.get_entity(entity_id) is not None
            and getattr(self.get_entity(entity_id), "spans", None)
        )
        if not paths:
            raise ValueError(
                "selecione contornos Path ou classifique peças antes do alívio automático"
            )
        self._editable_entity_ids(path.id for path in paths)
        roles: Dict[str, str] = {}
        for piece in self.document.pieces_by_id.values():
            roles[piece.outer_path_id] = "outer"
            for inner_id in piece.inner_path_ids:
                roles[inner_id] = "inner"
        try:
            from woodcam_editor.domain import build_containment_tree

            tree = build_containment_tree(self.document)
            for path in paths:
                if path.id not in roles and path.id in tree.depth_by_id:
                    roles[path.id] = (
                        "outer" if tree.depth_by_id[path.id] % 2 == 0 else "inner"
                    )
        except Exception:
            # Explicit Piece2D mappings remain authoritative if containment is
            # unavailable because another invalid contour exists in the file.
            pass
        return paths, {path.id: roles[path.id] for path in paths if path.id in roles}

    def preview_automatic_reliefs(
        self,
        radius: float,
        kind: str = "dogbone",
        tbone_side: str = "auto",
    ) -> Any:
        paths, roles = self.automatic_relief_scope()
        from woodcam_editor.geometry.modifiers import preview_auto_corner_reliefs

        return preview_auto_corner_reliefs(
            paths,
            float(radius),
            kind=kind,
            tbone_side=tbone_side,
            contour_roles=roles,
        )

    def update_piece_metadata(self, piece_id: str, **values: Any) -> bool:
        from .pieces import UpdatePieceMetadataCommand

        self.execute(UpdatePieceMetadataCommand(piece_id, **values))
        return True

    def select_piece(self, piece_id: str) -> Tuple[str, ...]:
        try:
            piece = self.document.pieces_by_id[str(piece_id)]
        except KeyError:
            self.selection.clear()
            return ()
        entity_ids = tuple(
            entity_id
            for entity_id in (
                piece.outer_path_id,
                *piece.inner_path_ids,
                *piece.pocket_path_ids,
                *piece.marking_path_ids,
            )
            if entity_id in self.document.entities_by_id
        )
        self.selection.replace(entity_ids)
        return entity_ids

    @staticmethod
    def vec(x: float, y: float) -> Any:
        from woodcam_editor.domain import Vec2

        return Vec2(float(x), float(y))

    def snap(
        self,
        point: Any,
        pixels_per_mm: float,
        excluded_ids: Iterable[str] = (),
        disabled: bool = False,
        reference_point: Any = None,
    ):
        return self.snap_engine.snapped_point(
            point,
            self.document,
            pixels_per_mm,
            excluded_entity_ids=excluded_ids,
            disabled=disabled,
            reference_point=reference_point,
        )

    @staticmethod
    def _circumcenter(a: Any, b: Any, c: Any) -> Any:
        ax, ay = _xy(a)
        bx, by = _xy(b)
        cx, cy = _xy(c)
        denominator = 2.0 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
        if abs(denominator) <= 1e-12:
            return None
        ux = (
            (ax * ax + ay * ay) * (by - cy)
            + (bx * bx + by * by) * (cy - ay)
            + (cx * cx + cy * cy) * (ay - by)
        ) / denominator
        uy = (
            (ax * ax + ay * ay) * (cx - bx)
            + (bx * bx + by * by) * (ax - cx)
            + (cx * cx + cy * cy) * (bx - ax)
        ) / denominator
        try:
            from woodcam_editor.domain import Vec2

            return Vec2(ux, uy)
        except Exception:
            return (ux, uy)


__all__ = ["CommandExecutionCancelled", "EditorController", "EditorMode"]
