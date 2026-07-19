"""Connectivity and containment derived from exact domain geometry.

Topology is a deterministic projection of :class:`VectorDocument`; it never
silently mutates or joins source entities.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Set, Tuple

from .document import VectorDocument
from .entities import CircleEntity, EllipseEntity, PathEntity
from .primitives import DEFAULT_EPSILON, BBox2D, GeometryError, Vec2
from ..geometry.math2d import PointLocation, point_in_polygon, polygon_centroid, signed_area


@dataclass(frozen=True, slots=True)
class NodeReference:
    entity_id: str
    node_id: str
    point: Vec2
    is_path_endpoint: bool


@dataclass(frozen=True, slots=True)
class TopologyNode:
    id: str
    point: Vec2
    references: Tuple[NodeReference, ...]
    degree: int


@dataclass(frozen=True, slots=True)
class TopologyEdge:
    entity_id: str
    span_id: str
    start_node_id: str
    end_node_id: str


@dataclass(frozen=True)
class TopologyGraph:
    nodes_by_id: Mapping[str, TopologyNode]
    edges: Tuple[TopologyEdge, ...]
    components: Tuple[frozenset[str], ...]
    source_revision: int
    tolerance: float
    reference_to_topology: Mapping[Tuple[str, str], str] = field(default_factory=dict)

    def node_for(self, entity_id: str, node_id: str) -> TopologyNode:
        topology_id = self.reference_to_topology[(entity_id, node_id)]
        return self.nodes_by_id[topology_id]

    @property
    def open_nodes(self) -> Tuple[TopologyNode, ...]:
        return tuple(node for node in self.nodes_by_id.values() if node.degree == 1)

    @property
    def branch_nodes(self) -> Tuple[TopologyNode, ...]:
        return tuple(node for node in self.nodes_by_id.values() if node.degree > 2)


class _UnionFind:
    def __init__(self, size: int):
        self.parent = list(range(size))
        self.rank = [0] * size

    def find(self, value: int) -> int:
        while self.parent[value] != value:
            self.parent[value] = self.parent[self.parent[value]]
            value = self.parent[value]
        return value

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root == right_root:
            return
        if self.rank[left_root] < self.rank[right_root]:
            left_root, right_root = right_root, left_root
        self.parent[right_root] = left_root
        if self.rank[left_root] == self.rank[right_root]:
            self.rank[left_root] += 1


def _topology_id(references: Sequence[NodeReference]) -> str:
    key = "|".join(sorted("%s:%s" % (ref.entity_id, ref.node_id) for ref in references))
    return "topology-%s" % hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


def _selected_entity_ids(
    document: VectorDocument,
    entity_ids: Optional[Iterable[str]],
) -> Optional[frozenset[str]]:
    if entity_ids is None:
        return None
    selected = frozenset(entity_ids)
    missing = selected - set(document.entities_by_id)
    if missing:
        raise KeyError("unknown topology entity IDs: %s" % sorted(missing))
    return selected


def build_topology(
    document: VectorDocument,
    tolerance: float = DEFAULT_EPSILON,
    *,
    entity_ids: Optional[Iterable[str]] = None,
) -> TopologyGraph:
    """Build a graph, clustering geometrically coincident path nodes."""

    tolerance = float(tolerance)
    if tolerance < 0.0:
        raise ValueError("topology tolerance cannot be negative")
    selected = _selected_entity_ids(document, entity_ids)
    references: List[NodeReference] = []
    for entity in sorted(document.entities_by_id.values(), key=lambda value: value.id):
        if selected is not None and entity.id not in selected:
            continue
        if not isinstance(entity, PathEntity):
            continue
        nodes = entity.nodes()
        for index, node in enumerate(nodes):
            is_endpoint = not entity.closed and index in (0, len(nodes) - 1)
            references.append(NodeReference(entity.id, node.id, node.point, is_endpoint))

    union_find = _UnionFind(len(references))
    # A simple O(n²) baseline is deliberately correct and dependency-free.  A
    # spatial index can replace only this candidate search once measurements
    # justify it, without changing graph semantics.
    for left in range(len(references)):
        for right in range(left + 1, len(references)):
            if references[left].point.almost_equals(references[right].point, tolerance):
                union_find.union(left, right)

    grouped: Dict[int, List[NodeReference]] = {}
    for index, reference in enumerate(references):
        grouped.setdefault(union_find.find(index), []).append(reference)

    reference_to_topology: Dict[Tuple[str, str], str] = {}
    points_by_topology: Dict[str, Vec2] = {}
    refs_by_topology: Dict[str, Tuple[NodeReference, ...]] = {}
    for members in grouped.values():
        members = sorted(members, key=lambda ref: (ref.entity_id, ref.node_id))
        topology_id = _topology_id(members)
        point = Vec2(
            sum(member.point.x for member in members) / len(members),
            sum(member.point.y for member in members) / len(members),
        )
        points_by_topology[topology_id] = point
        refs_by_topology[topology_id] = tuple(members)
        for member in members:
            reference_to_topology[(member.entity_id, member.node_id)] = topology_id

    edges: List[TopologyEdge] = []
    degree: Dict[str, int] = {node_id: 0 for node_id in refs_by_topology}
    adjacency: Dict[str, Set[str]] = {node_id: set() for node_id in refs_by_topology}
    for entity in sorted(document.entities_by_id.values(), key=lambda value: value.id):
        if selected is not None and entity.id not in selected:
            continue
        if not isinstance(entity, PathEntity):
            continue
        for index, span in enumerate(entity.spans):
            start_ref = entity.node_ids[index]
            end_ref = entity.node_ids[(index + 1) % len(entity.node_ids)]
            start = reference_to_topology[(entity.id, start_ref)]
            end = reference_to_topology[(entity.id, end_ref)]
            edges.append(TopologyEdge(entity.id, span.id, start, end))
            if start == end:
                degree[start] += 2
            else:
                degree[start] += 1
                degree[end] += 1
                adjacency[start].add(end)
                adjacency[end].add(start)

    nodes = {
        topology_id: TopologyNode(
            topology_id,
            points_by_topology[topology_id],
            refs_by_topology[topology_id],
            degree[topology_id],
        )
        for topology_id in sorted(refs_by_topology)
    }
    components = []
    unseen = set(nodes)
    while unseen:
        seed = min(unseen)
        stack = [seed]
        component = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(adjacency[current] - component)
        unseen -= component
        components.append(frozenset(component))
    components.sort(key=lambda component: min(component) if component else "")
    return TopologyGraph(
        nodes_by_id=nodes,
        edges=tuple(edges),
        components=tuple(components),
        source_revision=document.revision,
        tolerance=tolerance,
        reference_to_topology=reference_to_topology,
    )


@dataclass(frozen=True, slots=True)
class EndpointPair:
    first: NodeReference
    second: NodeReference
    distance: float


def nearby_open_endpoint_pairs(
    document: VectorDocument,
    tolerance: float,
    *,
    minimum_distance: float = DEFAULT_EPSILON,
    entity_ids: Optional[Iterable[str]] = None,
) -> Tuple[EndpointPair, ...]:
    tolerance = float(tolerance)
    if tolerance < 0.0 or minimum_distance < 0.0:
        raise ValueError("endpoint tolerances cannot be negative")
    selected = _selected_entity_ids(document, entity_ids)
    endpoints: List[NodeReference] = []
    for entity in document.entities_by_id.values():
        if selected is not None and entity.id not in selected:
            continue
        if isinstance(entity, PathEntity) and not entity.closed:
            nodes = entity.nodes()
            endpoints.append(NodeReference(entity.id, nodes[0].id, nodes[0].point, True))
            endpoints.append(NodeReference(entity.id, nodes[-1].id, nodes[-1].point, True))
    result = []
    for left in range(len(endpoints)):
        for right in range(left + 1, len(endpoints)):
            first, second = endpoints[left], endpoints[right]
            distance = first.point.distance_to(second.point)
            if minimum_distance < distance <= tolerance:
                result.append(EndpointPair(first, second, distance))
    return tuple(sorted(result, key=lambda pair: (pair.distance, pair.first.entity_id, pair.second.entity_id)))


def _closed_points(entity, deflection: float) -> Optional[Tuple[Vec2, ...]]:
    if isinstance(entity, PathEntity) and entity.closed:
        return entity.flatten(deflection, include_closure=False)
    if isinstance(entity, (CircleEntity, EllipseEntity)):
        return entity.flatten(deflection, include_closure=False)
    return None


def _interior_sample(points: Sequence[Vec2]) -> Vec2:
    centroid = polygon_centroid(points)
    if point_in_polygon(centroid, points) is PointLocation.INSIDE:
        return centroid
    average = Vec2(
        sum(point.x for point in points) / len(points),
        sum(point.y for point in points) / len(points),
    )
    if point_in_polygon(average, points) is PointLocation.INSIDE:
        return average
    # Move from each edge midpoint toward the average; this avoids testing a
    # vertex that is necessarily on the boundary.
    for index, point in enumerate(points):
        midpoint = point.lerp(points[(index + 1) % len(points)], 0.5)
        for factor in (0.01, 0.05, 0.2, 0.5):
            candidate = midpoint.lerp(average, factor)
            if point_in_polygon(candidate, points) is PointLocation.INSIDE:
                return candidate
    raise GeometryError("could not find an interior point for a closed contour")


@dataclass(frozen=True)
class ContainmentTree:
    parent_by_id: Mapping[str, Optional[str]]
    children_by_id: Mapping[str, Tuple[str, ...]]
    depth_by_id: Mapping[str, int]
    roots: Tuple[str, ...]
    source_revision: int

    def descendants(self, entity_id: str) -> Tuple[str, ...]:
        result = []
        stack = list(reversed(self.children_by_id.get(entity_id, ())))
        while stack:
            child = stack.pop()
            result.append(child)
            stack.extend(reversed(self.children_by_id.get(child, ())))
        return tuple(result)


def build_containment_tree(
    document: VectorDocument,
    deflection: float = 0.01,
    tolerance: float = DEFAULT_EPSILON,
    *,
    entity_ids: Optional[Iterable[str]] = None,
) -> ContainmentTree:
    selected = _selected_entity_ids(document, entity_ids)
    contours = {}
    for entity in document.entities_by_id.values():
        if selected is not None and entity.id not in selected:
            continue
        points = _closed_points(entity, deflection)
        if points is not None and len(points) >= 3:
            contours[entity.id] = {
                "points": points,
                "bounds": BBox2D.from_points(points),
                "area": abs(signed_area(points)),
                "sample": _interior_sample(points),
            }
    parent: Dict[str, Optional[str]] = {}
    for child_id, child in contours.items():
        candidates = []
        for parent_id, possible in contours.items():
            if child_id == parent_id or possible["area"] <= child["area"] + tolerance:
                continue
            if not possible["bounds"].contains_bbox(child["bounds"], tolerance):
                continue
            location = point_in_polygon(child["sample"], possible["points"], tolerance)
            if location is PointLocation.INSIDE:
                candidates.append((possible["area"], parent_id))
        parent[child_id] = min(candidates)[1] if candidates else None

    children: Dict[str, List[str]] = {entity_id: [] for entity_id in contours}
    for child_id, parent_id in parent.items():
        if parent_id is not None:
            children[parent_id].append(child_id)
    for values in children.values():
        values.sort()

    depth: Dict[str, int] = {}
    def calculate_depth(entity_id: str, visiting: Set[str]) -> int:
        if entity_id in depth:
            return depth[entity_id]
        if entity_id in visiting:
            raise GeometryError("containment cycle detected")
        parent_id = parent[entity_id]
        value = 0 if parent_id is None else calculate_depth(parent_id, visiting | {entity_id}) + 1
        depth[entity_id] = value
        return value
    for entity_id in contours:
        calculate_depth(entity_id, set())
    roots = tuple(sorted(entity_id for entity_id, parent_id in parent.items() if parent_id is None))
    return ContainmentTree(
        parent_by_id=parent,
        children_by_id={key: tuple(value) for key, value in children.items()},
        depth_by_id=depth,
        roots=roots,
        source_revision=document.revision,
    )
