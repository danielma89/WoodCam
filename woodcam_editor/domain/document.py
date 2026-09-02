"""The aggregate root and project-level vector metadata."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
import copy
import math
from typing import Any, Dict, Iterable, Mapping, Optional, Tuple

from .entities import CircleEntity, EllipseEntity, GroupEntity, PathEntity, VectorEntity
from .primitives import Affine2D, BBox2D, InvariantError, Vec2, new_id


SCHEMA_VERSION = 1
UNITS = "mm"
COORDINATE_SYSTEM = "xy_cartesian_y_up"
LAYER_PURPOSES = frozenset(("design", "construction", "reference", "cut", "pocket", "drill"))
NON_CAM_LAYER_PURPOSES = frozenset(("construction", "reference"))
POCKET_REGION_ROLES = frozenset(("pocket_region", "pocket_island"))
REMNANT_CUT_ROLE = "remnant_cut"


def entity_manufacturing_role(entity: Any) -> str:
    """Return an entity's explicit manufacturing meaning, when present."""

    return str(
        (getattr(entity, "metadata", {}) or {}).get("import_role", "") or ""
    ).strip().lower()


def entity_is_pocket_feature(entity: Any) -> bool:
    """Pocket boundaries are machining regions, not through-cut contours."""

    return entity_manufacturing_role(entity) in POCKET_REGION_ROLES


def entity_is_pocket_region(entity: Any) -> bool:
    return entity_manufacturing_role(entity) == "pocket_region"


def entity_is_remnant_cut(entity: Any) -> bool:
    """Return whether an open path is an explicit stock-separation cut."""

    metadata = dict(getattr(entity, "metadata", {}) or {})
    role = str(
        metadata.get("woodcam_role", "")
        or metadata.get("import_role", "")
        or ""
    ).strip().lower()
    return role == REMNANT_CUT_ROLE


def pocket_feature_owner_key(entity: Any) -> Tuple[str, ...]:
    """Stable source scope used to keep a pocket attached to its board."""

    metadata = dict(getattr(entity, "metadata", {}) or {})
    instance = str(
        metadata.get("panelnest_instance_id", "")
        or metadata.get("source_tree_instance_id", "")
        or ""
    )
    component = str(metadata.get("source_shape_component_id", "") or "")
    batch = str(metadata.get("import_batch_id", "") or "")
    return tuple(value for value in (batch, instance, component) if value)


def layer_is_cam_eligible(layer: Any) -> bool:
    """Return whether a layer participates in CAM validation/classification.

    Visibility is a layer concern.  Domain entities deliberately do not own a
    second ``visible`` flag, so callers must not infer production eligibility
    from an ad-hoc entity attribute.
    """

    if layer is None:
        return True
    purpose = str(getattr(layer, "purpose", "design") or "design").strip().lower()
    return bool(getattr(layer, "visible", True)) and purpose not in NON_CAM_LAYER_PURPOSES


def entity_is_cam_eligible(document: Any, entity: Any) -> bool:
    """Resolve CAM eligibility exclusively through ``document.layers_by_id``.

    Generic/imported documents without layer information remain compatible and
    include their entities.  A structurally valid :class:`VectorDocument`
    always has the referenced layer; missing-layer corruption is handled by
    invariant validation rather than silently changing geometry semantics.
    """

    layers = getattr(document, "layers_by_id", None)
    if not isinstance(layers, Mapping) or not layers:
        return True
    layer_id = getattr(entity, "layer_id", None)
    if layer_id is None or layer_id not in layers:
        return True
    return layer_is_cam_eligible(layers[layer_id])


@dataclass(frozen=True, slots=True)
class WorkArea:
    min_x: float
    min_y: float
    max_x: float
    max_y: float
    source: str = "woodcam_trabalho"

    def __post_init__(self) -> None:
        bounds = BBox2D(self.min_x, self.min_y, self.max_x, self.max_y)
        if bounds.width <= 0.0 or bounds.height <= 0.0:
            raise InvariantError("work area must have positive width and height")
        if not self.source:
            raise InvariantError("work area source cannot be empty")

    @property
    def bounds(self) -> BBox2D:
        return BBox2D(self.min_x, self.min_y, self.max_x, self.max_y)

    @property
    def width(self) -> float:
        return self.max_x - self.min_x

    @property
    def height(self) -> float:
        return self.max_y - self.min_y

    def contains(self, point_or_bounds, tolerance: float = 0.0) -> bool:
        if isinstance(point_or_bounds, Vec2):
            return self.bounds.contains_point(point_or_bounds, tolerance)
        if isinstance(point_or_bounds, BBox2D):
            return self.bounds.contains_bbox(point_or_bounds, tolerance)
        raise TypeError("WorkArea.contains expects Vec2 or BBox2D")


@dataclass(frozen=True, slots=True)
class Layer:
    name: str = "Desenho"
    id: str = field(default_factory=lambda: new_id("layer"))
    color: str = "#2563eb"
    visible: bool = True
    locked: bool = False
    order: int = 0
    purpose: str = "design"
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if not self.id or not self.name.strip():
            raise InvariantError("layer requires non-empty id and name")
        if self.purpose not in LAYER_PURPOSES:
            raise InvariantError("unsupported layer purpose: %s" % self.purpose)
        if not isinstance(self.order, int):
            raise InvariantError("layer order must be an integer")


@dataclass(frozen=True, slots=True)
class Piece2D:
    name: str
    outer_path_id: str
    inner_path_ids: Tuple[str, ...] = ()
    id: str = field(default_factory=lambda: new_id("piece"))
    quantity: int = 1
    material: str = ""
    thickness: float = 0.0
    grain_direction: Optional[float] = None
    rotations_allowed: Tuple[float, ...] = (0.0, 90.0)
    placement: Affine2D = field(default_factory=Affine2D.identity)
    metadata: Mapping[str, Any] = field(default_factory=dict)
    stale: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "inner_path_ids", tuple(self.inner_path_ids))
        object.__setattr__(self, "rotations_allowed", tuple(float(value) for value in self.rotations_allowed))
        object.__setattr__(self, "thickness", float(self.thickness))
        object.__setattr__(self, "metadata", dict(self.metadata or {}))
        if not self.id or not self.name.strip() or not self.outer_path_id:
            raise InvariantError("piece requires non-empty id, name and outer path")
        if self.outer_path_id in self.inner_path_ids:
            raise InvariantError("piece outer path cannot also be an inner path")
        if len(set(self.inner_path_ids)) != len(self.inner_path_ids):
            raise InvariantError("piece inner path IDs must be unique")
        if not isinstance(self.quantity, int) or self.quantity < 1:
            raise InvariantError("piece quantity must be a positive integer")
        if not math.isfinite(self.thickness) or self.thickness < 0.0:
            raise InvariantError("piece thickness must be finite and non-negative")
        if self.grain_direction is not None and not math.isfinite(float(self.grain_direction)):
            raise InvariantError("grain direction must be finite")
        if not self.rotations_allowed or not all(math.isfinite(value) for value in self.rotations_allowed):
            raise InvariantError("piece requires finite allowed rotations")

    @property
    def pocket_path_ids(self) -> Tuple[str, ...]:
        """Machining-only pocket boundaries carried rigidly with this piece.

        The optional relation lives in metadata to keep schema-v1 FCStd files
        readable while shallow machining semantics are introduced.  Geometry
        remains in normal VectorDocument entities and is never duplicated.
        """

        values = self.metadata.get("pocket_path_ids", ())
        if not isinstance(values, (tuple, list)):
            return ()
        return tuple(str(value) for value in values if str(value))

    @property
    def marking_path_ids(self) -> Tuple[str, ...]:
        """Open engraving/marking paths carried rigidly with this piece."""

        values = self.metadata.get("marking_path_ids", ())
        if not isinstance(values, (tuple, list)):
            return ()
        return tuple(str(value) for value in values if str(value))


@dataclass
class VectorDocument:
    schema_version: int = SCHEMA_VERSION
    document_uuid: str = field(default_factory=lambda: new_id("document"))
    revision: int = 0
    units: str = UNITS
    coordinate_system: str = COORDINATE_SYSTEM
    work_area: Optional[WorkArea] = None
    layers_by_id: Dict[str, Layer] = field(default_factory=dict)
    entities_by_id: Dict[str, VectorEntity] = field(default_factory=dict)
    pieces_by_id: Dict[str, Piece2D] = field(default_factory=dict)
    active_layer_id: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.layers_by_id = dict(self.layers_by_id)
        self.entities_by_id = dict(self.entities_by_id)
        self.pieces_by_id = dict(self.pieces_by_id)
        self.metadata = dict(self.metadata or {})
        if not self.layers_by_id:
            layer = Layer()
            self.layers_by_id[layer.id] = layer
            self.active_layer_id = layer.id
        elif self.active_layer_id is None:
            self.active_layer_id = min(self.layers_by_id.values(), key=lambda layer: (layer.order, layer.id)).id
        self.validate_invariants()

    @classmethod
    def create_default(cls, work_area: Optional[WorkArea] = None) -> "VectorDocument":
        return cls(work_area=work_area)

    @property
    def active_layer(self) -> Layer:
        return self.layers_by_id[self.active_layer_id]

    def clone(self) -> "VectorDocument":
        return copy.deepcopy(self)

    def get_entity(self, entity_id: str) -> VectorEntity:
        try:
            return self.entities_by_id[entity_id]
        except KeyError:
            raise KeyError("unknown entity ID %s" % entity_id)

    def entities_on_layer(self, layer_id: str) -> Tuple[VectorEntity, ...]:
        if layer_id not in self.layers_by_id:
            raise KeyError("unknown layer ID %s" % layer_id)
        return tuple(entity for entity in self.entities_by_id.values() if entity.layer_id == layer_id)

    def add_layers(self, layers: Iterable[Layer], *, bump_revision: bool = True) -> None:
        layers = tuple(layers)
        ids = [layer.id for layer in layers]
        if len(set(ids)) != len(ids) or any(layer_id in self.layers_by_id for layer_id in ids):
            raise InvariantError("cannot add duplicate layer IDs")
        self.layers_by_id.update((layer.id, layer) for layer in layers)
        if bump_revision and layers:
            self.revision += 1

    def add_entities(self, entities: Iterable[VectorEntity], *, bump_revision: bool = True) -> None:
        entities = tuple(entities)
        ids = [entity.id for entity in entities]
        if len(set(ids)) != len(ids) or any(entity_id in self.entities_by_id for entity_id in ids):
            raise InvariantError("cannot add duplicate entity IDs")
        missing_layers = {entity.layer_id for entity in entities} - set(self.layers_by_id)
        if missing_layers:
            raise InvariantError("entities reference missing layers: %s" % sorted(missing_layers))
        self.entities_by_id.update((entity.id, entity) for entity in entities)
        if bump_revision and entities:
            self.revision += 1

    def replace_entities(self, entities: Iterable[VectorEntity], *, bump_revision: bool = True) -> None:
        entities = tuple(entities)
        ids = [entity.id for entity in entities]
        if len(set(ids)) != len(ids):
            raise InvariantError("cannot replace duplicate entity IDs")
        missing = set(ids) - set(self.entities_by_id)
        if missing:
            raise KeyError("cannot replace unknown entities: %s" % sorted(missing))
        missing_layers = {entity.layer_id for entity in entities} - set(self.layers_by_id)
        if missing_layers:
            raise InvariantError("entities reference missing layers: %s" % sorted(missing_layers))
        self.entities_by_id.update((entity.id, entity) for entity in entities)
        changed_ids = set(ids)
        for piece_id, piece in tuple(self.pieces_by_id.items()):
            references = {
                piece.outer_path_id,
                *piece.inner_path_ids,
                *piece.pocket_path_ids,
                *piece.marking_path_ids,
            }
            if changed_ids.intersection(references) and not piece.stale:
                self.pieces_by_id[piece_id] = replace(piece, stale=True)
        if bump_revision and entities:
            self.revision += 1

    def remove_entities(self, entity_ids: Iterable[str], *, bump_revision: bool = True) -> Tuple[VectorEntity, ...]:
        entity_ids = tuple(dict.fromkeys(entity_ids))
        missing = set(entity_ids) - set(self.entities_by_id)
        if missing:
            raise KeyError("cannot remove unknown entities: %s" % sorted(missing))
        removed = tuple(self.entities_by_id.pop(entity_id) for entity_id in entity_ids)
        removed_ids = set(entity_ids)
        updated_pieces = {}
        for piece_id, piece in self.pieces_by_id.items():
            if piece.outer_path_id in removed_ids:
                continue
            inner = tuple(path_id for path_id in piece.inner_path_ids if path_id not in removed_ids)
            pockets = tuple(
                path_id for path_id in piece.pocket_path_ids
                if path_id not in removed_ids
            )
            markings = tuple(
                path_id for path_id in piece.marking_path_ids
                if path_id not in removed_ids
            )
            if (
                inner != piece.inner_path_ids
                or pockets != piece.pocket_path_ids
                or markings != piece.marking_path_ids
            ):
                metadata = dict(piece.metadata or {})
                metadata["pocket_path_ids"] = list(pockets)
                metadata["marking_path_ids"] = list(markings)
                updated_pieces[piece_id] = replace(
                    piece,
                    inner_path_ids=inner,
                    metadata=metadata,
                    stale=True,
                )
            else:
                updated_pieces[piece_id] = piece
        self.pieces_by_id = updated_pieces
        # Groups with deleted children become invalid and are therefore removed,
        # including parent groups affected by that first cascading removal.
        pending_removed = set(removed_ids)
        while pending_removed:
            newly_removed = set()
            for entity_id, entity in tuple(self.entities_by_id.items()):
                if isinstance(entity, GroupEntity) and pending_removed.intersection(entity.child_ids):
                    self.entities_by_id.pop(entity_id)
                    newly_removed.add(entity_id)
            pending_removed = newly_removed
        if bump_revision and removed:
            self.revision += 1
        return removed

    def add_pieces(self, pieces: Iterable[Piece2D], *, bump_revision: bool = True) -> None:
        pieces = tuple(pieces)
        ids = [piece.id for piece in pieces]
        if len(set(ids)) != len(ids) or any(piece_id in self.pieces_by_id for piece_id in ids):
            raise InvariantError("cannot add duplicate piece IDs")
        for piece in pieces:
            references = {
                piece.outer_path_id,
                *piece.inner_path_ids,
                *piece.pocket_path_ids,
                *piece.marking_path_ids,
            }
            missing = references - set(self.entities_by_id)
            if missing:
                raise InvariantError("piece references missing entities: %s" % sorted(missing))
        self.pieces_by_id.update((piece.id, piece) for piece in pieces)
        if bump_revision and pieces:
            self.revision += 1

    def bounds(self, *, visible_only: bool = False) -> Optional[BBox2D]:
        result = None
        for entity in self.entities_by_id.values():
            if isinstance(entity, GroupEntity) or not hasattr(entity, "bounds"):
                continue
            if visible_only and not self.layers_by_id[entity.layer_id].visible:
                continue
            entity_bounds = entity.bounds()
            result = entity_bounds if result is None else result.union(entity_bounds)
        return result

    def validate_invariants(self) -> None:
        if self.schema_version != SCHEMA_VERSION:
            raise InvariantError("unsupported schema version %s" % self.schema_version)
        if not self.document_uuid:
            raise InvariantError("document UUID cannot be empty")
        if not isinstance(self.revision, int) or self.revision < 0:
            raise InvariantError("document revision must be a non-negative integer")
        if self.units != UNITS or self.coordinate_system != COORDINATE_SYSTEM:
            raise InvariantError("domain supports only mm and Cartesian Y-up coordinates")
        if self.active_layer_id not in self.layers_by_id:
            raise InvariantError("active layer does not exist")
        if set(self.layers_by_id) != {layer.id for layer in self.layers_by_id.values()}:
            raise InvariantError("layer dictionary keys do not match IDs")
        if set(self.entities_by_id) != {entity.id for entity in self.entities_by_id.values()}:
            raise InvariantError("entity dictionary keys do not match IDs")
        if set(self.pieces_by_id) != {piece.id for piece in self.pieces_by_id.values()}:
            raise InvariantError("piece dictionary keys do not match IDs")
        for entity in self.entities_by_id.values():
            if entity.layer_id not in self.layers_by_id:
                raise InvariantError("entity %s references a missing layer" % entity.id)
            if isinstance(entity, GroupEntity):
                missing = set(entity.child_ids) - set(self.entities_by_id)
                if missing:
                    raise InvariantError("group %s references missing children" % entity.id)
        for piece in self.pieces_by_id.values():
            missing = {
                piece.outer_path_id,
                *piece.inner_path_ids,
                *piece.pocket_path_ids,
                *piece.marking_path_ids,
            } - set(self.entities_by_id)
            if missing:
                raise InvariantError("piece %s references missing paths" % piece.id)
        span_ids = []
        node_ids = []
        for entity in self.entities_by_id.values():
            if isinstance(entity, PathEntity):
                span_ids.extend(span.id for span in entity.spans)
                node_ids.extend(entity.node_ids)
            elif isinstance(entity, CircleEntity):
                node_ids.extend((entity.center_node_id, entity.radius_node_id))
            elif isinstance(entity, EllipseEntity):
                node_ids.append(entity.center_node_id)
        if len(span_ids) != len(set(span_ids)):
            raise InvariantError("span IDs must be globally unique")
        if len(node_ids) != len(set(node_ids)):
            raise InvariantError("node IDs must be globally unique")
        groups = {
            entity.id: entity
            for entity in self.entities_by_id.values()
            if isinstance(entity, GroupEntity)
        }
        visiting = set()
        visited = set()
        def visit(group_id: str) -> None:
            if group_id in visited:
                return
            if group_id in visiting:
                raise InvariantError("group hierarchy contains a cycle")
            visiting.add(group_id)
            for child_id in groups[group_id].child_ids:
                if child_id in groups:
                    visit(child_id)
            visiting.remove(group_id)
            visited.add(group_id)
        for group_id in groups:
            visit(group_id)


def validate_document_invariants(document: VectorDocument) -> None:
    """Compatibility entry point used by persistence adapters."""

    document.validate_invariants()
