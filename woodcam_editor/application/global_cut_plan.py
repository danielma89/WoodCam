"""Pure, deterministic planning for sheet-wide profile cutting.

The module is intentionally independent from Qt, FreeCAD and the G-code
writer.  It turns already compensated cutter centre-lines into unique
physical segments, schedules depth passes, models retaining tabs and, when
requested, plans a separate plunge-first tab-release phase.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import Enum
import hashlib
import itertools
import math
from typing import Iterable, Mapping, Sequence, Tuple

from woodcam_editor.domain.primitives import Vec2
from woodcam_editor.geometry.math2d import (
    PointLocation,
    point_in_polygon,
    segment_intersections,
)

from .common_line import (
    MAX_PHYSICAL_COINCIDENCE_TOLERANCE,
    CommonLineContour,
    CommonLineCutPath,
    CommonLinePlanningError,
    build_common_line_cut_paths,
    plan_common_line_cut,
)


class CutDepthStrategy(str, Enum):
    PER_PIECE = "per_piece"
    PIECE_BIDIRECTIONAL = "piece_bidirectional"
    PIECE_UNIDIRECTIONAL = "piece_unidirectional"
    GLOBAL_BY_DEPTH = "global_by_depth"
    HYBRID_STABILITY = "hybrid_stability"
    HYBRID_PIECE_BIDIRECTIONAL = "hybrid_piece_bidirectional"


class PhysicalSegmentKind(str, Enum):
    INTERNAL = "internal"
    EXTERNAL = "external"
    SHARED = "shared"


class CutPhase(str, Enum):
    SCREW_PILOT = "screw_pilot"
    INTERNAL = "internal"
    SHARED = "shared"
    EXTERNAL = "external"
    TAB_RELEASE = "tab_release"


class TabRetentionKind(str, Enum):
    STOCK = "stock_tab"
    SHARED = "shared_tab"
    WASTE = "waste_tab"


class LooseWasteFixationMode(str, Enum):
    DISABLED = "disabled"
    TABS = "tabs"
    SCREWS = "screws"


class TabReleaseMode(str, Enum):
    KEEP_TABS = "keep_tabs"
    AUTOMATIC_RELEASE = "automatic_release"


class GlobalCutPlanError(ValueError):
    """A plan is geometrically or mechanically unsafe."""


@dataclass(frozen=True, slots=True)
class RetentionRules:
    """Configurable mechanical heuristics; no thresholds live in algorithms."""

    small_piece_tool_factor: float = 6.0
    small_piece_tab_factor: float = 4.0
    elongated_aspect_ratio: float = 2.5
    minimum_tabs_small: int = 4
    minimum_tabs_regular: int = 4
    minimum_tabs_elongated: int = 4
    minimum_tab_spacing_factor: float = 1.25
    minimum_balanced_spacing_ratio: float = 0.30
    corner_clearance_tab_factor: float = 1.5
    corner_clearance_tool_factor: float = 2.0
    minimum_corner_clearance_mm: float = 8.0
    minimum_stock_tabs_per_component: int = 2
    maximum_free_perimeter_ratio: float = 0.60
    maximum_unsupported_perimeter_mm: float = 300.0
    strong_tab_height_ratio: float = 0.75
    strong_tab_maximum_unsupported_perimeter_mm: float = 500.0
    minimum_loose_waste_tabs: int = 2
    waste_detection_resolution_mm: float = 8.0
    waste_detection_max_cells: int = 80000
    geometric_tolerance: float = 0.02

    def for_physical_tab_height(
        self,
        tab_height: float,
        material_thickness: float | None,
    ) -> "RetentionRules":
        """Return the density rules appropriate to the bridge's real strength."""

        if material_thickness is None:
            return self
        material = abs(float(material_thickness))
        height = max(0.0, float(tab_height or 0.0))
        if (
            material <= self.geometric_tolerance
            or height / material < float(self.strong_tab_height_ratio)
        ):
            return self
        return replace(
            self,
            maximum_unsupported_perimeter_mm=(
                self.strong_tab_maximum_unsupported_perimeter_mm
            ),
        )

    def required_tab_count(
        self,
        metrics: "PieceMetrics",
        requested: int,
        tool_diameter: float,
        tab_width: float,
    ) -> int:
        small_limit = max(
            abs(float(tool_diameter)) * self.small_piece_tool_factor,
            abs(float(tab_width)) * self.small_piece_tab_factor,
        )
        if metrics.maximum_dimension <= small_limit:
            mechanical = self.minimum_tabs_small
        elif metrics.aspect_ratio >= self.elongated_aspect_ratio:
            mechanical = self.minimum_tabs_elongated
        else:
            mechanical = self.minimum_tabs_regular
        perimeter_count = int(
            math.ceil(
                metrics.perimeter
                / max(float(self.maximum_unsupported_perimeter_mm), 1.0e-9)
            )
        )
        return max(max(0, int(requested)), mechanical, perimeter_count)


@dataclass(frozen=True, slots=True)
class PieceMetrics:
    owner_id: str
    width: float
    height: float
    perimeter: float
    centre: Vec2

    @property
    def maximum_dimension(self) -> float:
        return max(self.width, self.height)

    @property
    def minimum_dimension(self) -> float:
        return min(self.width, self.height)

    @property
    def aspect_ratio(self) -> float:
        short = max(self.minimum_dimension, 1.0e-12)
        return self.maximum_dimension / short


@dataclass(frozen=True, slots=True)
class PhysicalCutSegment:
    segment_id: str
    start: Vec2
    end: Vec2
    kind: PhysicalSegmentKind
    owner_ids: Tuple[str, ...]
    retains_tab: bool = False
    waste_id: str | None = None
    tab_height_override: float | None = None

    @property
    def length(self) -> float:
        return self.start.distance_to(self.end)


@dataclass(frozen=True, slots=True)
class GlobalCutTrail:
    trail_id: str
    points: Tuple[Vec2, ...]
    segment_ids: Tuple[str, ...]
    edge_tabs: Tuple[bool, ...]
    kind: PhysicalSegmentKind
    owner_ids: Tuple[str, ...]
    reversible: bool = False

    def __post_init__(self):
        edge_count = len(self.points) - 1
        if edge_count <= 0 or len(self.segment_ids) != edge_count:
            raise GlobalCutPlanError("trilha global sem correspondência entre arestas")
        if len(self.edge_tabs) != edge_count:
            raise GlobalCutPlanError("estado de tabs não corresponde às arestas")

    @property
    def closed(self) -> bool:
        return self.points[0].almost_equals(self.points[-1], 1.0e-9)

    def oriented(
        self,
        *,
        reverse: bool = False,
        start_edge_index: int = 0,
    ) -> "GlobalCutTrail":
        """Return the same physical edges in a routed traversal order."""

        edges = [
            (
                self.points[index],
                self.points[index + 1],
                self.segment_ids[index],
                self.edge_tabs[index],
            )
            for index in range(len(self.segment_ids))
        ]
        if reverse:
            edges = [
                (end, start, segment_id, is_tab)
                for start, end, segment_id, is_tab in reversed(edges)
            ]
        if self.closed:
            offset = int(start_edge_index) % len(edges)
            edges = edges[offset:] + edges[:offset]
        elif start_edge_index:
            raise GlobalCutPlanError("uma trilha aberta só pode começar numa extremidade")
        points = [edges[0][0]]
        points.extend(edge[1] for edge in edges)
        return GlobalCutTrail(
            self.trail_id,
            tuple(points),
            tuple(edge[2] for edge in edges),
            tuple(bool(edge[3]) for edge in edges),
            self.kind,
            self.owner_ids,
            self.reversible,
        )


@dataclass(frozen=True, slots=True)
class TrailRoute:
    trail_id: str
    reverse: bool
    start_edge_index: int
    start: Vec2
    end: Vec2


@dataclass(frozen=True, slots=True)
class RetentionTab:
    tab_id: str
    segment_id: str
    kind: TabRetentionKind
    owner_ids: Tuple[str, ...]
    start: Vec2
    end: Vec2
    width: float
    thickness: float
    waste_id: str | None = None
    releasable: bool = True

    @property
    def centre(self) -> Vec2:
        return self.start.lerp(self.end, 0.5)


@dataclass(frozen=True, slots=True)
class CutOperation:
    operation_id: str
    trail_id: str
    segment_ids: Tuple[str, ...]
    owner_ids: Tuple[str, ...]
    depth: float
    phase: CutPhase
    dependencies: Tuple[str, ...] = ()
    final_pass: bool = False
    reverse_trail: bool = False
    start_edge_index: int = 0
    routing_mode: str = "defined"
    executing_owner_id: str | None = None


@dataclass(frozen=True, slots=True)
class SegmentDepthCoverage:
    """One physical segment executed once at one depth by one scheduled piece."""

    segment_id: str
    depth: float
    executing_owner_id: str
    trail_id: str


@dataclass(frozen=True, slots=True)
class CutRouteMetrics:
    """Comparable XY metrics for one scheduled depth pass."""

    depth: float
    routing_mode: str
    cut_distance: float
    rapid_distance: float
    retract_count: int
    plunge_count: int
    piece_switch_count: int
    trail_count: int
    fragmented_piece_count: int
    duplicate_physical_segment_count: int
    sum_of_independent_piece_perimeters: float
    shared_cut_savings: float
    shared_segments_reused: int
    long_rapid_count: int


@dataclass(frozen=True, slots=True)
class TabReleaseOperation:
    operation_id: str
    tab_id: str
    owner_id: str
    plunge_point: Vec2
    sweep_end: Vec2
    sweep_length: float
    target_depth: float
    dependencies: Tuple[str, ...] = ()
    last_for_piece: bool = False


@dataclass(frozen=True, slots=True)
class ScrewAnchor:
    anchor_id: str
    waste_id: str
    point: Vec2
    hole_diameter: float
    pilot_depth: float
    keepout_radius: float
    head_height: float


@dataclass(frozen=True, slots=True)
class WasteRegion:
    waste_id: str
    owner_id: str
    points: Tuple[Vec2, ...]
    area: float
    fixation: str
    fallback_reason: str = ""


@dataclass(frozen=True, slots=True)
class StabilityReport:
    owner_id: str
    required_tabs: int
    actual_tabs: int
    connected_to_stock: bool
    maximum_free_span: float
    valid: bool
    reasons: Tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RetentionGraph:
    """Undirected STOCK/piece graph whose edges are physical tabs."""

    tabs: Tuple[RetentionTab, ...]
    stock_node: str = "STOCK"

    @property
    def piece_ids(self) -> Tuple[str, ...]:
        return tuple(
            sorted(
                {
                    owner
                    for tab in self.tabs
                    if tab.kind != TabRetentionKind.WASTE
                    for owner in tab.owner_ids
                }
            )
        )

    def neighbours(
        self,
        node: str,
        *,
        removed_tabs: Iterable[str] = (),
        removed_pieces: Iterable[str] = (),
    ) -> Tuple[str, ...]:
        ignored_tabs = set(removed_tabs)
        ignored_pieces = set(removed_pieces)
        result = set()
        for tab in self.tabs:
            if tab.kind == TabRetentionKind.WASTE:
                continue
            if tab.tab_id in ignored_tabs:
                continue
            if tab.kind == TabRetentionKind.STOCK:
                endpoints = (tab.owner_ids[0], self.stock_node)
            else:
                if len(tab.owner_ids) != 2:
                    continue
                endpoints = tab.owner_ids
            if node == endpoints[0] and endpoints[1] not in ignored_pieces:
                result.add(endpoints[1])
            elif node == endpoints[1] and endpoints[0] not in ignored_pieces:
                result.add(endpoints[0])
        return tuple(sorted(result))

    def connected_to_stock(
        self,
        owner_id: str,
        *,
        removed_tabs: Iterable[str] = (),
        removed_pieces: Iterable[str] = (),
    ) -> bool:
        removed_pieces = set(removed_pieces)
        if owner_id in removed_pieces:
            return False
        queue = [str(owner_id)]
        visited = set()
        while queue:
            current = queue.pop(0)
            if current == self.stock_node:
                return True
            if current in visited:
                continue
            visited.add(current)
            queue.extend(
                neighbour
                for neighbour in self.neighbours(
                    current,
                    removed_tabs=removed_tabs,
                    removed_pieces=removed_pieces,
                )
                if neighbour not in visited
            )
        return False

    def removal_preserves_other_pieces(
        self,
        owner_id: str,
        remaining_pieces: Iterable[str],
        removed_tabs: Iterable[str] = (),
    ) -> bool:
        incident = {
            tab.tab_id
            for tab in self.tabs
            if owner_id in tab.owner_ids
        }
        ignored_tabs = set(removed_tabs) | incident
        ignored_pieces = {owner_id}
        return all(
            self.connected_to_stock(
                other,
                removed_tabs=ignored_tabs,
                removed_pieces=ignored_pieces,
            )
            for other in remaining_pieces
            if other != owner_id
        )

    def critical_piece_ids(
        self,
        remaining_pieces: Iterable[str] | None = None,
        removed_tabs: Iterable[str] = (),
    ) -> Tuple[str, ...]:
        remaining = tuple(
            sorted(self.piece_ids if remaining_pieces is None else remaining_pieces)
        )
        return tuple(
            owner
            for owner in remaining
            if not self.removal_preserves_other_pieces(
                owner,
                remaining,
                removed_tabs,
            )
        )


@dataclass(frozen=True, slots=True)
class GlobalCutPlan:
    strategy: CutDepthStrategy
    release_mode: TabReleaseMode
    depths: Tuple[float, ...]
    segments: Tuple[PhysicalCutSegment, ...]
    trails: Tuple[GlobalCutTrail, ...]
    tabs: Tuple[RetentionTab, ...]
    operations: Tuple[CutOperation, ...]
    tab_release_operations: Tuple[TabReleaseOperation, ...]
    piece_metrics: Tuple[PieceMetrics, ...]
    stability_reports: Tuple[StabilityReport, ...]
    waste_regions: Tuple[WasteRegion, ...] = ()
    screw_anchors: Tuple[ScrewAnchor, ...] = ()
    segment_depth_coverage: Tuple[SegmentDepthCoverage, ...] = ()
    route_metrics: Tuple[CutRouteMetrics, ...] = ()
    hybrid_intermediate_mode: str = "per_piece_common_line"

    @property
    def retention_graph(self) -> RetentionGraph:
        return RetentionGraph(self.tabs)

    def metrics_for_depth(self, depth: float) -> CutRouteMetrics | None:
        target = round(abs(float(depth)), 9)
        return next(
            (
                metric
                for metric in self.route_metrics
                if round(metric.depth, 9) == target
            ),
            None,
        )

    def covered_segment_ids_for_piece(
        self,
        owner_id: str,
        depth: float,
    ) -> Tuple[str, ...]:
        owned = {
            segment.segment_id
            for segment in self.segments
            if owner_id in segment.owner_ids
        }
        covered = {
            item.segment_id
            for item in self.segment_depth_coverage
            if round(item.depth, 9) == round(abs(float(depth)), 9)
        }
        return tuple(sorted(owned & covered))

    def validate(self) -> "GlobalCutPlan":
        if not self.depths:
            raise GlobalCutPlanError("o plano global não possui profundidades")
        segment_by_id = {segment.segment_id: segment for segment in self.segments}
        if len(segment_by_id) != len(self.segments):
            raise GlobalCutPlanError("há IDs de segmentos físicos duplicados")
        for segment in self.segments:
            if segment.length <= 1.0e-9:
                raise GlobalCutPlanError("segmento degenerado: %s" % segment.segment_id)
            if segment.kind == PhysicalSegmentKind.SHARED and len(segment.owner_ids) != 2:
                raise GlobalCutPlanError(
                    "SharedEdge %s precisa ter exatamente dois owners" % segment.segment_id
                )
            if segment.kind != PhysicalSegmentKind.SHARED and len(segment.owner_ids) != 1:
                raise GlobalCutPlanError(
                    "segmento %s precisa de exatamente um owner" % segment.segment_id
                )
            if len(segment.owner_ids) > 2:
                raise GlobalCutPlanError(
                    "segmento %s possui mais de dois owners" % segment.segment_id
                )

        trail_by_id = {trail.trail_id: trail for trail in self.trails}
        operation_ids = {operation.operation_id for operation in self.operations}
        if len(operation_ids) != len(self.operations):
            raise GlobalCutPlanError("há IDs de operações duplicados")
        seen = set()
        order = {}
        for index, operation in enumerate(self.operations):
            order[operation.operation_id] = index
            trail = trail_by_id.get(operation.trail_id)
            if trail is None:
                raise GlobalCutPlanError("operação referencia trilha inexistente")
            bidirectional_reverse = (
                self.strategy in {
                    CutDepthStrategy.PIECE_BIDIRECTIONAL,
                    CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL,
                }
                and operation.routing_mode == "piece_bidirectional"
                and trail.reversible
            )
            if operation.reverse_trail and not (
                bidirectional_reverse
                or (
                    not trail.closed
                    and (
                        trail.kind == PhysicalSegmentKind.SHARED
                        or trail.reversible
                    )
                )
            ):
                raise GlobalCutPlanError(
                    "a operação %s inverte uma trilha cuja direção é mecânica"
                    % operation.operation_id
                )
            prepared = trail.oriented(
                reverse=operation.reverse_trail,
                start_edge_index=operation.start_edge_index,
            )
            if prepared.segment_ids != operation.segment_ids:
                raise GlobalCutPlanError(
                    "a travessia de %s não preserva os segmentos físicos"
                    % operation.operation_id
                )
            for segment_id in operation.segment_ids:
                if segment_id not in segment_by_id:
                    raise GlobalCutPlanError("operação referencia segmento inexistente")
                key = (segment_id, round(abs(float(operation.depth)), 9))
                if key in seen:
                    raise GlobalCutPlanError(
                        "corte duplicado para %s em Z -%.4f" % key
                    )
                seen.add(key)
            for dependency in operation.dependencies:
                if dependency not in order:
                    raise GlobalCutPlanError(
                        "dependência %s não precede %s"
                        % (dependency, operation.operation_id)
                    )

        if self.strategy in {
            CutDepthStrategy.PIECE_BIDIRECTIONAL,
            CutDepthStrategy.PIECE_UNIDIRECTIONAL,
            CutDepthStrategy.HYBRID_STABILITY,
            CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL,
        }:
            previous = None
            stability_started = False
            phase_rank = {
                CutPhase.INTERNAL: 0,
                CutPhase.SHARED: 1,
                CutPhase.EXTERNAL: 2,
            }
            previous_stability_depth = None
            previous_phase_rank = -1
            for operation in self.operations:
                if operation.routing_mode not in {
                    "fast",
                    "per_piece_common_line",
                    "piece_bidirectional",
                    "piece_unidirectional",
                    "final_sheet_pass",
                    "stability",
                }:
                    raise GlobalCutPlanError(
                        "operação híbrida sem classificação FAST/STABILITY"
                    )
                if previous is not None and previous not in operation.dependencies:
                    raise GlobalCutPlanError(
                        "a cadeia híbrida foi quebrada antes de %s"
                        % operation.operation_id
                    )
                previous = operation.operation_id
                if operation.routing_mode == "final_sheet_pass":
                    if self.strategy != CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL:
                        raise GlobalCutPlanError(
                            "a volta final separada pertence somente ao modo ida/volta"
                        )
                    stability_started = True
                elif operation.routing_mode == "stability":
                    stability_started = True
                    depth_key = round(operation.depth, 9)
                    if depth_key != previous_stability_depth:
                        previous_stability_depth = depth_key
                        previous_phase_rank = -1
                    current_rank = phase_rank[operation.phase]
                    if current_rank < previous_phase_rank:
                        raise GlobalCutPlanError(
                            "STABILITY ROUTE violou INTERNAL → SHARED → EXTERNAL"
                        )
                    previous_phase_rank = current_rank
                elif stability_started:
                    raise GlobalCutPlanError(
                        "FAST ROUTE não pode ocorrer depois da camada passante"
                    )
                elif (
                    self.strategy in {
                        CutDepthStrategy.PIECE_BIDIRECTIONAL,
                        CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL,
                    }
                    and operation.routing_mode != "piece_bidirectional"
                ):
                    raise GlobalCutPlanError(
                        "a camada por peça não usa a visita bidirecional"
                    )
                elif (
                    self.strategy == CutDepthStrategy.PIECE_UNIDIRECTIONAL
                    and operation.routing_mode != "piece_unidirectional"
                ):
                    raise GlobalCutPlanError("a visita por peça não usa sentido único")
                elif (
                    self.strategy == CutDepthStrategy.HYBRID_STABILITY
                    and operation.routing_mode != self.hybrid_intermediate_mode
                ):
                    raise GlobalCutPlanError(
                        "a camada intermediária não usa o scheduler híbrido configurado"
                    )

        required = {
            (segment.segment_id, round(depth, 9))
            for segment in self.segments
            for depth in self.depths
        }
        if seen != required:
            missing = sorted(required - seen)
            raise GlobalCutPlanError(
                "faltam passes de profundidade para segmentos: %s" % (missing[:3],)
            )
        if self.segment_depth_coverage:
            coverage_keys = []
            for coverage in self.segment_depth_coverage:
                segment = segment_by_id.get(coverage.segment_id)
                if segment is None:
                    raise GlobalCutPlanError(
                        "cobertura referencia segmento físico inexistente"
                    )
                if coverage.executing_owner_id not in segment.owner_ids:
                    raise GlobalCutPlanError(
                        "a peça %s não pode executar %s"
                        % (coverage.executing_owner_id, coverage.segment_id)
                    )
                coverage_keys.append(
                    (coverage.segment_id, round(abs(coverage.depth), 9))
                )
            if len(set(coverage_keys)) != len(coverage_keys):
                raise GlobalCutPlanError(
                    "a cobertura física repete segmento DONE na mesma profundidade"
                )
            if set(coverage_keys) != required:
                raise GlobalCutPlanError(
                    "a cobertura física não contém todos os segmentos por profundidade"
                )
            owner_ids = sorted(
                {owner for segment in self.segments for owner in segment.owner_ids}
            )
            for depth in self.depths:
                covered_at_depth = {
                    item.segment_id
                    for item in self.segment_depth_coverage
                    if round(abs(item.depth), 9) == round(depth, 9)
                }
                for owner in owner_ids:
                    expected_owner = {
                        segment.segment_id
                        for segment in self.segments
                        if owner in segment.owner_ids
                    }
                    if not expected_owner.issubset(covered_at_depth):
                        raise GlobalCutPlanError(
                            "a peça %s não ficou fisicamente coberta em Z -%.4f"
                            % (owner, depth)
                        )
        if any(
            metric.duplicate_physical_segment_count
            for metric in self.route_metrics
        ):
            raise GlobalCutPlanError(
                "as métricas detectaram segmento físico duplicado"
            )
        for report in self.stability_reports:
            if not report.valid:
                raise GlobalCutPlanError(
                    "retenção inválida para %s: %s"
                    % (report.owner_id, "; ".join(report.reasons))
                )
        waste_ids = {region.waste_id for region in self.waste_regions}
        if len(waste_ids) != len(self.waste_regions):
            raise GlobalCutPlanError("há IDs de restos soltos duplicados")
        anchor_ids = {anchor.anchor_id for anchor in self.screw_anchors}
        if len(anchor_ids) != len(self.screw_anchors):
            raise GlobalCutPlanError("há IDs de parafusos duplicados")
        for anchor in self.screw_anchors:
            if anchor.waste_id not in waste_ids:
                raise GlobalCutPlanError("parafuso referencia resto inexistente")
            if anchor.hole_diameter <= 0.0 or anchor.pilot_depth <= 0.0:
                raise GlobalCutPlanError("dimensões inválidas no furo piloto")
            if anchor.keepout_radius <= 0.0 or anchor.head_height < 0.0:
                raise GlobalCutPlanError("região proibida de parafuso inválida")
        tab_ids = {tab.tab_id for tab in self.tabs}
        if len(tab_ids) != len(self.tabs):
            raise GlobalCutPlanError("há IDs de tabs duplicados")
        for tab in self.tabs:
            segment = segment_by_id.get(tab.segment_id)
            if segment is None or not segment.retains_tab:
                raise GlobalCutPlanError(
                    "tab %s não corresponde a um segmento retido" % tab.tab_id
                )
            if tab.width <= 1.0e-9 or tab.thickness < 0.0:
                raise GlobalCutPlanError("dimensão inválida na tab %s" % tab.tab_id)
            if tab.kind == TabRetentionKind.STOCK and len(tab.owner_ids) != 1:
                raise GlobalCutPlanError("StockTab precisa de uma peça e STOCK")
            if tab.kind == TabRetentionKind.SHARED and len(tab.owner_ids) != 2:
                raise GlobalCutPlanError("SharedTab precisa de duas peças")
            if tab.kind == TabRetentionKind.WASTE and not tab.waste_id:
                raise GlobalCutPlanError("WasteTab precisa identificar o resto retido")
        for region in self.waste_regions:
            if str(region.fixation) != "tabs":
                continue
            waste_tab_count = sum(
                tab.kind == TabRetentionKind.WASTE
                and tab.waste_id == region.waste_id
                for tab in self.tabs
            )
            if waste_tab_count < 2:
                raise GlobalCutPlanError(
                    "o resto %s precisa de pelo menos duas WasteTabs bem separadas"
                    % region.waste_id
                )
        if self.release_mode == TabReleaseMode.KEEP_TABS and self.tab_release_operations:
            raise GlobalCutPlanError("keep_tabs não pode possuir TabRelease")
        release_ids = {operation.operation_id for operation in self.tab_release_operations}
        known = set(operation_ids)
        for operation in self.tab_release_operations:
            for dependency in operation.dependencies:
                if dependency not in known:
                    raise GlobalCutPlanError("dependência inválida na liberação de tabs")
            known.add(operation.operation_id)
        if len(release_ids) != len(self.tab_release_operations):
            raise GlobalCutPlanError("há operações TabRelease duplicadas")
        if self.release_mode == TabReleaseMode.AUTOMATIC_RELEASE:
            released_tabs = [operation.tab_id for operation in self.tab_release_operations]
            releasable_tab_ids = {
                tab.tab_id for tab in self.tabs if tab.releasable
            }
            if (
                set(released_tabs) != releasable_tab_ids
                or len(released_tabs) != len(releasable_tab_ids)
            ):
                raise GlobalCutPlanError(
                    "automatic_release precisa liberar cada tab física exatamente uma vez"
                )
            if self.tab_release_operations:
                first_dependencies = set(
                    self.tab_release_operations[0].dependencies
                )
                if not operation_ids.issubset(first_dependencies):
                    raise GlobalCutPlanError(
                        "TabRelease começou antes de terminar o corte principal"
                    )
            if any(
                abs(operation.target_depth - self.depths[-1]) > 1.0e-9
                for operation in self.tab_release_operations
            ):
                raise GlobalCutPlanError(
                    "TabRelease não alcança a profundidade final"
                )
        return self


@dataclass(frozen=True, slots=True)
class OwnedContour:
    contour_id: str
    owner_id: str
    points: Tuple[Vec2, ...]

    def __init__(self, contour_id: str, owner_id: str, points: Iterable[Vec2 | Sequence[float]]):
        object.__setattr__(self, "contour_id", str(contour_id))
        object.__setattr__(self, "owner_id", str(owner_id))
        object.__setattr__(
            self,
            "points",
            tuple(point if isinstance(point, Vec2) else Vec2.from_sequence(point) for point in points),
        )


def _normalise_enum(value, enum_type, label):
    try:
        return value if isinstance(value, enum_type) else enum_type(str(value))
    except ValueError:
        raise GlobalCutPlanError("%s inválido: %s" % (label, value))


def _manual_tab_positions_by_contour(contours, positions):
    """Keep explicit sheet XY markers attached to one owner contour."""
    contours = tuple(contours)
    buckets = [[] for _contour in contours]
    for raw_position in tuple(positions or ()):
        try:
            if isinstance(raw_position, dict):
                point = Vec2(
                    float(raw_position["x"]),
                    float(raw_position["y"]),
                )
            elif isinstance(raw_position, Vec2):
                point = raw_position
            else:
                point = Vec2.from_sequence(raw_position)
        except (KeyError, TypeError, ValueError):
            continue
        candidates = []
        for contour_index, contour in enumerate(contours):
            best_distance = None
            points = tuple(contour.points)
            for start, end in zip(points, points[1:] + points[:1]):
                vector = end - start
                length_squared = vector.dot(vector)
                if length_squared <= 1.0e-18:
                    continue
                parameter = max(
                    0.0,
                    min(1.0, (point - start).dot(vector) / length_squared),
                )
                distance = point.distance_to(start + vector * parameter)
                if best_distance is None or distance < best_distance:
                    best_distance = distance
            if best_distance is not None:
                candidates.append((best_distance, contour_index))
        if candidates:
            _distance, contour_index = min(candidates)
            buckets[contour_index].append(raw_position)
    return tuple(tuple(bucket) for bucket in buckets)


def _canonical_key(start: Vec2, end: Vec2, tolerance: float):
    # The UI tolerance recognises candidate borders; it is not a snapping
    # grid for the physical toolpath.  A 0.2 mm grid can place two consecutive
    # 0.156 mm Dogbone chords in different buckets and split a continuous arc
    # into fake trails.  Topology follows the same capped physical coincidence
    # rule used by the common-line planner.
    scale = max(
        min(
            abs(float(tolerance)),
            MAX_PHYSICAL_COINCIDENCE_TOLERANCE,
        ),
        1.0e-9,
    )
    first = (int(round(start.x / scale)), int(round(start.y / scale)))
    second = (int(round(end.x / scale)), int(round(end.y / scale)))
    return (first, second) if first <= second else (second, first)


def _segment_id(kind, owners, start, end, tolerance):
    key = (kind.value, tuple(sorted(owners)), _canonical_key(start, end, tolerance))
    digest = hashlib.sha1(repr(key).encode("utf-8")).hexdigest()[:16]
    return "%s-%s" % (kind.value, digest)


def _piece_metrics(contours: Sequence[CommonLineContour]) -> Tuple[PieceMetrics, ...]:
    result = []
    for contour in contours:
        points = contour.points
        xs = [point.x for point in points]
        ys = [point.y for point in points]
        perimeter = sum(
            points[index].distance_to(points[(index + 1) % len(points)])
            for index in range(len(points))
        )
        result.append(
            PieceMetrics(
                contour.contour_id,
                max(xs) - min(xs),
                max(ys) - min(ys),
                perimeter,
                Vec2((min(xs) + max(xs)) * 0.5, (min(ys) + max(ys)) * 0.5),
            )
        )
    return tuple(result)


def _polygon_area(points: Sequence[Vec2]) -> float:
    return abs(
        sum(
            points[index].cross(points[(index + 1) % len(points)])
            for index in range(len(points))
        )
        * 0.5
    )


def _point_segment_distance(point: Vec2, start: Vec2, end: Vec2) -> float:
    vector = end - start
    length_squared = vector.dot(vector)
    if length_squared <= 1.0e-18:
        return point.distance_to(start)
    parameter = max(0.0, min(1.0, (point - start).dot(vector) / length_squared))
    return point.distance_to(start + vector * parameter)


def _safe_screw_point(
    points: Sequence[Vec2],
    required_clearance: float,
    future_rings: Sequence[Sequence[Vec2]] = (),
) -> tuple[Vec2 | None, float]:
    """Find a deterministic maximum-clearance point inside an exact polygon."""

    if len(points) < 3:
        return None, 0.0
    min_x = min(point.x for point in points)
    max_x = max(point.x for point in points)
    min_y = min(point.y for point in points)
    max_y = max(point.y for point in points)
    span = max(max_x - min_x, max_y - min_y)
    step = max(0.5, min(max(required_clearance / 3.0, 0.5), span / 24.0 or 0.5))
    count_x = max(1, min(160, int(math.ceil((max_x - min_x) / step))))
    count_y = max(1, min(160, int(math.ceil((max_y - min_y) / step))))
    candidates = []
    centroid = Vec2(
        sum(point.x for point in points) / len(points),
        sum(point.y for point in points) / len(points),
    )
    candidates.append(centroid)
    for y_index in range(count_y + 1):
        y = min_y + (max_y - min_y) * y_index / max(count_y, 1)
        for x_index in range(count_x + 1):
            x = min_x + (max_x - min_x) * x_index / max(count_x, 1)
            candidates.append(Vec2(x, y))
    edges = tuple(
        (start, end)
        for ring in (tuple(points),) + tuple(tuple(ring) for ring in future_rings)
        for start, end in zip(ring, ring[1:] + ring[:1])
    )
    accepted = []
    for candidate in candidates:
        if point_in_polygon(candidate, points, 1.0e-7) != PointLocation.INSIDE:
            continue
        clearance = min(
            _point_segment_distance(candidate, start, end)
            for start, end in edges
        )
        if clearance + 1.0e-7 >= required_clearance:
            accepted.append((clearance, candidate))
    if not accepted:
        return None, 0.0
    clearance, point = max(
        accepted,
        key=lambda item: (
            round(item[0], 9),
            -round(item[1].y, 9),
            -round(item[1].x, 9),
        ),
    )
    return point, clearance


def _split_path_for_tab(path: CommonLineCutPath, tab_width: float) -> Tuple[CommonLineCutPath, ...]:
    start, end = path.points
    length = start.distance_to(end)
    if length + 1.0e-9 < tab_width or tab_width <= 1.0e-9:
        return (path,)
    half_parameter = min(0.5, tab_width * 0.5 / length)
    vector = end - start
    low = 0.5 - half_parameter
    high = 0.5 + half_parameter
    values = []
    if low > 1.0e-9:
        values.append(CommonLineCutPath((start, start + vector * low), path.owner_ids, path.shared, False))
    values.append(
        CommonLineCutPath(
            (start + vector * low, start + vector * high),
            path.owner_ids,
            path.shared,
            True,
        )
    )
    if high < 1.0 - 1.0e-9:
        values.append(CommonLineCutPath((start + vector * high, end), path.owner_ids, path.shared, False))
    return tuple(values)


def _split_path_for_tabs(
    path: CommonLineCutPath,
    tab_width: float,
    centre_parameters: Sequence[float],
) -> Tuple[CommonLineCutPath, ...]:
    """Split one shared interval around every selected retention centre.

    A long common edge is one atomic physical segment, but it may need several
    bridges to satisfy the mechanical free-span rule.  Keeping this split in
    one place guarantees that the resulting cut and tab intervals remain
    edge-disjoint.
    """

    start, end = path.points
    length = start.distance_to(end)
    if length <= 1.0e-12 or tab_width <= 1.0e-12 or not centre_parameters:
        return (path,)
    half = min(0.5, tab_width * 0.5 / length)
    ranges = sorted(
        (
            max(0.0, min(1.0, float(parameter)) - half),
            min(1.0, max(0.0, float(parameter)) + half),
        )
        for parameter in centre_parameters
    )
    merged = []
    for low, high in ranges:
        if high - low <= 1.0e-12:
            continue
        if merged and low <= merged[-1][1] + 1.0e-12:
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    cuts = sorted({0.0, 1.0, *(value for pair in merged for value in pair)})
    vector = end - start
    result = []
    for low, high in zip(cuts, cuts[1:]):
        if high - low <= 1.0e-12:
            continue
        middle = (low + high) * 0.5
        is_tab = any(
            range_low - 1.0e-12 <= middle <= range_high + 1.0e-12
            for range_low, range_high in merged
        )
        result.append(
            CommonLineCutPath(
                (start + vector * low, start + vector * high),
                path.owner_ids,
                path.shared,
                is_tab,
            )
        )
    return tuple(result)


@dataclass(frozen=True, slots=True)
class _GlobalTabCandidate:
    """One physical bridge candidate shared by one or two useful pieces."""

    path_index: int
    parameter: float
    point: Vec2
    owner_ids: Tuple[str, ...]
    shared: bool
    strict_corner_clearance: bool
    corner_clearance: float


def _point_at_perimeter_position(
    contour: Sequence[Vec2],
    position: float,
) -> Vec2:
    perimeter = sum(
        start.distance_to(end)
        for start, end in zip(contour, contour[1:] + contour[:1])
    )
    if perimeter <= 1.0e-12:
        return contour[0]
    target = float(position) % perimeter
    progress = 0.0
    for start, end in zip(contour, contour[1:] + contour[:1]):
        length = start.distance_to(end)
        if target <= progress + length + 1.0e-9:
            parameter = max(0.0, min(1.0, (target - progress) / max(length, 1.0e-12)))
            return start + (end - start) * parameter
        progress += length
    return contour[0]


def _circular_distance(first: float, second: float, perimeter: float) -> float:
    delta = abs(float(first) - float(second))
    return min(delta, max(0.0, float(perimeter) - delta))


def _balanced_global_tab_paths(
    paths: Sequence[CommonLineCutPath],
    required_by_owner: Mapping[str, int],
    tab_width: float,
    owner_contours: Mapping[str, Sequence[Vec2]],
    rules: RetentionRules,
    tool_diameter: float,
) -> Tuple[CommonLineCutPath, ...]:
    """Select StockTabs and SharedTabs in one balanced physical pass.

    The previous two-stage selector filled exclusive boundaries first and
    only later supplemented shared lines.  In dense nests that could give a
    middle part twice its required retention and concentrate bridges on the
    sheet perimeter.  Here every owner receives evenly spaced perimeter
    targets and one physical shared candidate may satisfy one target of each
    adjacent part.
    """

    paths = tuple(paths)
    width = abs(float(tab_width))
    if width <= rules.geometric_tolerance:
        return paths
    contours = {
        str(owner): tuple(points)
        for owner, points in owner_contours.items()
        if points
    }
    perimeters = {
        owner: sum(
            start.distance_to(end)
            for start, end in zip(contour, contour[1:] + contour[:1])
        )
        for owner, contour in contours.items()
    }
    target_positions = {
        owner: tuple(
            (index + 0.5) * perimeters[owner] / max(1, int(required))
            for index in range(max(0, int(required)))
        )
        for owner, required in required_by_owner.items()
        if owner in perimeters and perimeters[owner] > rules.geometric_tolerance
    }
    target_points = {
        owner: tuple(
            _point_at_perimeter_position(contours[owner], position)
            for position in positions
        )
        for owner, positions in target_positions.items()
    }

    half = width * 0.5
    desired_corner_clearance = max(
        float(rules.minimum_corner_clearance_mm),
        width * float(rules.corner_clearance_tab_factor),
        abs(float(tool_diameter)) * float(rules.corner_clearance_tool_factor),
    )
    candidates = []
    for path_index, path in enumerate(paths):
        start, end = path.points
        length = start.distance_to(end)
        if length + rules.geometric_tolerance < width:
            continue
        maximum_margin = max(half, length * 0.5)
        strict_margin = min(half + desired_corner_clearance, maximum_margin)
        adaptive_low = half
        adaptive_high = length - half
        strict_low = strict_margin
        strict_high = length - strict_margin
        parameters = {0.5}
        candidate_step = max(
            width * 2.0,
            min(float(rules.maximum_unsupported_perimeter_mm) * 0.20, 60.0),
        )
        sample_count = max(
            1,
            min(
                48,
                int(math.ceil(max(0.0, adaptive_high - adaptive_low) / candidate_step))
                + 1,
            ),
        )
        for sample_index in range(sample_count):
            local = (
                (adaptive_low + adaptive_high) * 0.5
                if sample_count == 1
                else adaptive_low
                + (adaptive_high - adaptive_low)
                * sample_index
                / (sample_count - 1)
            )
            parameters.add(local / max(length, 1.0e-12))
        vector = end - start
        length_squared = max(length * length, 1.0e-12)
        for owner in path.owner_ids:
            for point in target_points.get(owner, ()):
                projected = max(
                    adaptive_low,
                    min(
                        adaptive_high,
                        (point - start).dot(vector) / length_squared * length,
                    ),
                )
                parameters.add(projected / max(length, 1.0e-12))
        for parameter in sorted(parameters):
            local = max(adaptive_low, min(adaptive_high, parameter * length))
            point = start + vector * (local / max(length, 1.0e-12))
            strict = strict_high + 1.0e-9 >= strict_low and (
                strict_low - 1.0e-9 <= local <= strict_high + 1.0e-9
            )
            candidates.append(
                _GlobalTabCandidate(
                    path_index,
                    local / max(length, 1.0e-12),
                    point,
                    tuple(path.owner_ids),
                    bool(path.shared),
                    strict,
                    min(local - half, length - half - local),
                )
            )

    # Different owner targets can project to the same physical point.  Keep a
    # single deterministic candidate for that interval.
    unique = {}
    for candidate in candidates:
        key = (candidate.path_index, round(candidate.parameter, 9))
        previous = unique.get(key)
        if previous is None or (
            candidate.strict_corner_clearance,
            candidate.corner_clearance,
        ) > (
            previous.strict_corner_clearance,
            previous.corner_clearance,
        ):
            unique[key] = candidate
    candidates = tuple(
        unique[key] for key in sorted(unique, key=lambda value: (value[0], value[1]))
    )
    if not candidates:
        raise GlobalCutPlanError("não há trechos retos que comportem tabs")

    positions_by_candidate = tuple(
        {
            owner: _perimeter_position(candidate.point, contours[owner])
            for owner in candidate.owner_ids
            if owner in contours
        }
        for candidate in candidates
    )
    minimum_spacing = max(
        width * float(rules.minimum_tab_spacing_factor),
        rules.geometric_tolerance,
    )

    def compatible(candidate_index, selected_indexes, spacing_ratio):
        candidate = candidates[candidate_index]
        for selected_index in selected_indexes:
            shared_owners = set(candidate.owner_ids) & set(
                candidates[selected_index].owner_ids
            )
            if not shared_owners:
                continue
            if (
                candidate.point.distance_to(candidates[selected_index].point)
                + rules.geometric_tolerance
                < minimum_spacing
            ):
                return False
            for owner in shared_owners:
                owner_target_spacing = perimeters[owner] / max(
                    1, len(target_positions.get(owner, ()))
                )
                required_owner_spacing = max(
                    minimum_spacing,
                    owner_target_spacing * float(spacing_ratio),
                )
                if (
                    _circular_distance(
                        positions_by_candidate[candidate_index][owner],
                        positions_by_candidate[selected_index][owner],
                        perimeters[owner],
                    )
                    + rules.geometric_tolerance
                    < required_owner_spacing
                ):
                    return False
        return True

    def select(pool, spacing_ratio):
        unfilled = {
            (owner, index)
            for owner, positions in target_positions.items()
            for index in range(len(positions))
        }
        selected = []
        available = set(pool)
        while unfilled:
            best = None
            for candidate_index in sorted(available):
                if not compatible(candidate_index, selected, spacing_ratio):
                    continue
                covers = []
                normalised_distance = 0.0
                for owner in candidates[candidate_index].owner_ids:
                    owner_targets = [
                        (index, position)
                        for index, position in enumerate(target_positions.get(owner, ()))
                        if (owner, index) in unfilled
                    ]
                    if not owner_targets:
                        continue
                    perimeter_position = positions_by_candidate[candidate_index][owner]
                    target_index, target_position = min(
                        owner_targets,
                        key=lambda item: (
                            _circular_distance(
                                perimeter_position,
                                item[1],
                                perimeters[owner],
                            ),
                            item[0],
                        ),
                    )
                    distance_to_target = _circular_distance(
                        perimeter_position,
                        target_position,
                        perimeters[owner],
                    )
                    spacing = perimeters[owner] / max(
                        1, len(target_positions[owner])
                    )
                    # A candidate on the opposite side of the piece must not
                    # consume this target merely because every target closer
                    # to it is already filled.  Half a target interval is the
                    # natural Voronoi boundary of the balanced pattern.
                    if (
                        distance_to_target
                        > spacing * 0.5 + rules.geometric_tolerance
                    ):
                        continue
                    normalised_distance += distance_to_target / max(spacing, 1.0e-9)
                    covers.append((owner, target_index))
                if not covers:
                    continue
                # A SharedTab is only economical when it really satisfies a
                # pending target on both neighbours.  Once one neighbour is
                # already complete, preferring that shared point creates an
                # incidental extra tab on the finished piece; across a dense
                # nest this was the source of rows with 10--13 bridges where
                # only four or five were mechanically requested.  Prefer an
                # owner-only candidate for a single remaining target and keep
                # the shared candidate as fallback when the geometry offers
                # no exclusive interval.
                incidental_owners = len(candidate.owner_ids) - len(
                    {owner for owner, _target_index in covers}
                )
                separation = min(
                    (
                        candidates[candidate_index].point.distance_to(
                            candidates[other].point
                        )
                        for other in selected
                        if set(candidates[candidate_index].owner_ids)
                        & set(candidates[other].owner_ids)
                    ),
                    default=float(rules.maximum_unsupported_perimeter_mm),
                )
                score = (
                    len(covers),
                    -incidental_owners,
                    -normalised_distance,
                    int(candidates[candidate_index].shared),
                    min(separation, float(rules.maximum_unsupported_perimeter_mm)),
                    candidates[candidate_index].corner_clearance,
                    -candidate_index,
                )
                if best is None or score > best[0]:
                    best = (score, candidate_index, tuple(covers))
            if best is None:
                return None
            _score, candidate_index, covers = best
            selected.append(candidate_index)
            available.remove(candidate_index)
            unfilled.difference_update(covers)
        return selected

    strict_pool = [
        index
        for index, candidate in enumerate(candidates)
        if candidate.strict_corner_clearance
    ]
    selected = None
    active_spacing_ratio = 0.0
    requested_spacing_ratio = max(
        0.0, float(rules.minimum_balanced_spacing_ratio)
    )
    spacing_ratios = tuple(
        dict.fromkeys(
            (
                requested_spacing_ratio,
                requested_spacing_ratio * 0.5,
                0.0,
            )
        )
    )
    for pool in (strict_pool, range(len(candidates))):
        for spacing_ratio in spacing_ratios:
            selected = select(pool, spacing_ratio)
            if selected is not None:
                active_spacing_ratio = spacing_ratio
                break
        if selected is not None:
            break
    if selected is None:
        raise GlobalCutPlanError(
            "não foi possível distribuir tabs opostas sem sobreposição"
        )

    def owner_gap(owner, indexes):
        positions = sorted(
            positions_by_candidate[index][owner]
            for index in indexes
            if owner in positions_by_candidate[index]
        )
        if len(positions) < 2:
            return perimeters[owner]
        gaps = [second - first for first, second in zip(positions, positions[1:])]
        gaps.append(perimeters[owner] - positions[-1] + positions[0])
        return max(gaps)

    # Projection onto real straight intervals can move an ideal target.  Add
    # only the physical candidate that most reduces an excessive residual
    # span, rather than restarting the old owner-first supplementation.
    while True:
        unsafe = {
            owner
            for owner in required_by_owner
            if owner_gap(owner, selected)
            > float(rules.maximum_unsupported_perimeter_mm) + 1.0e-7
        }
        if not unsafe:
            break
        best = None
        for candidate_index, candidate in enumerate(candidates):
            if candidate_index in selected or not compatible(
                candidate_index, selected, active_spacing_ratio
            ):
                continue
            helped = [owner for owner in candidate.owner_ids if owner in unsafe]
            if not helped:
                continue
            gain = sum(
                max(
                    0.0,
                    owner_gap(owner, selected)
                    - owner_gap(owner, selected + [candidate_index]),
                )
                for owner in helped
            )
            score = (
                sum(
                    owner_gap(owner, selected + [candidate_index])
                    <= float(rules.maximum_unsupported_perimeter_mm) + 1.0e-7
                    for owner in helped
                ),
                gain,
                -sum(owner not in unsafe for owner in candidate.owner_ids),
                int(candidate.shared),
                candidate.corner_clearance,
                -candidate_index,
            )
            if best is None or score > best[0]:
                best = (score, candidate_index)
        if best is None or best[0][1] <= 1.0e-9:
            relaxed_ratio = next(
                (
                    ratio
                    for ratio in spacing_ratios
                    if ratio < active_spacing_ratio - 1.0e-12
                ),
                None,
            )
            if relaxed_ratio is not None:
                active_spacing_ratio = relaxed_ratio
                continue
            raise GlobalCutPlanError(
                "não há regiões de tab capazes de limitar o trecho livre de: %s"
                % ", ".join(sorted(unsafe))
            )
        selected.append(best[1])

    # A shared network must not float as one large island.  Keep two physical
    # StockTabs, well separated, in every connected component whenever the
    # geometry offers them.
    owners = set(required_by_owner)
    adjacency = {owner: set() for owner in owners}
    for index in selected:
        candidate = candidates[index]
        if candidate.shared and len(candidate.owner_ids) == 2:
            first, second = candidate.owner_ids
            adjacency.setdefault(first, set()).add(second)
            adjacency.setdefault(second, set()).add(first)
    components = []
    remaining = set(owners)
    while remaining:
        root = min(remaining)
        queue = [root]
        component = set()
        while queue:
            current = queue.pop(0)
            if current in component:
                continue
            component.add(current)
            queue.extend(sorted(adjacency.get(current, ())))
        remaining.difference_update(component)
        components.append(component)
    for component in components:
        anchors = [
            index
            for index in selected
            if not candidates[index].shared
            and candidates[index].owner_ids[0] in component
        ]
        wanted = min(
            int(rules.minimum_stock_tabs_per_component),
            sum(
                not candidate.shared and candidate.owner_ids[0] in component
                for candidate in candidates
            ),
        )
        while len(anchors) < wanted:
            options = [
                index
                for index, candidate in enumerate(candidates)
                if index not in selected
                and not candidate.shared
                and candidate.owner_ids[0] in component
                and compatible(index, selected, active_spacing_ratio)
            ]
            if not options:
                break
            chosen = max(
                options,
                key=lambda index: (
                    min(
                        (
                            candidates[index].point.distance_to(candidates[anchor].point)
                            for anchor in anchors
                        ),
                        default=float(rules.maximum_unsupported_perimeter_mm),
                    ),
                    int(candidates[index].strict_corner_clearance),
                    candidates[index].corner_clearance,
                    -index,
                ),
            )
            selected.append(chosen)
            anchors.append(chosen)

    # Shared targets are selected globally, so a bridge required by one part
    # may incidentally retain an already-complete neighbour.  After the sheet
    # is connected to STOCK, remove only those shared bridges that are
    # mechanically redundant for *both* owners.  Counts, maximum free span
    # and connectivity remain hard constraints; exclusive STOCK anchors are
    # deliberately preserved here.
    def all_owners_connected_to_stock(indexes):
        connected = {
            candidates[index].owner_ids[0]
            for index in indexes
            if not candidates[index].shared
        }
        adjacency = {owner: set() for owner in owners}
        for index in indexes:
            candidate = candidates[index]
            if candidate.shared and len(candidate.owner_ids) == 2:
                first, second = candidate.owner_ids
                adjacency.setdefault(first, set()).add(second)
                adjacency.setdefault(second, set()).add(first)
        queue = list(connected)
        while queue:
            current = queue.pop(0)
            for neighbour in adjacency.get(current, ()):
                if neighbour in connected:
                    continue
                connected.add(neighbour)
                queue.append(neighbour)
        return owners.issubset(connected)

    def owner_count(owner, indexes):
        return sum(owner in candidates[index].owner_ids for index in indexes)

    while True:
        removable = []
        for candidate_index in selected:
            candidate = candidates[candidate_index]
            if not candidate.shared:
                continue
            remaining_indexes = [
                index for index in selected if index != candidate_index
            ]
            if any(
                owner_count(owner, remaining_indexes)
                < int(required_by_owner[owner])
                or owner_gap(owner, remaining_indexes)
                > float(rules.maximum_unsupported_perimeter_mm) + 1.0e-7
                for owner in candidate.owner_ids
            ):
                continue
            if not all_owners_connected_to_stock(remaining_indexes):
                continue
            nearest = min(
                (
                    _circular_distance(
                        positions_by_candidate[candidate_index][owner],
                        positions_by_candidate[other_index][owner],
                        perimeters[owner],
                    )
                    for owner in candidate.owner_ids
                    for other_index in remaining_indexes
                    if owner in candidates[other_index].owner_ids
                ),
                default=float(rules.maximum_unsupported_perimeter_mm),
            )
            excess = sum(
                max(
                    0,
                    owner_count(owner, selected)
                    - int(required_by_owner[owner]),
                )
                for owner in candidate.owner_ids
            )
            removable.append(
                (
                    (excess, -nearest, -candidate_index),
                    candidate_index,
                )
            )
        if not removable:
            break
        _score, removed_index = max(removable)
        selected.remove(removed_index)

    selected_by_path = {}
    for candidate_index in selected:
        candidate = candidates[candidate_index]
        selected_by_path.setdefault(candidate.path_index, []).append(
            candidate.parameter
        )
    result = []
    for path_index, path in enumerate(paths):
        result.extend(
            _split_path_for_tabs(
                path,
                width,
                selected_by_path.get(path_index, ()),
            )
        )
    return tuple(result)


def _supplement_shared_tabs(
    paths: Sequence[CommonLineCutPath],
    required_by_owner: Mapping[str, int],
    tab_width: float,
    owner_contours: Mapping[str, Sequence[Vec2]] | None = None,
    maximum_free_span: float = 300.0,
    minimum_spacing: float = 0.0,
) -> Tuple[CommonLineCutPath, ...]:
    owner_contours = owner_contours or {}

    def path_midpoint(path):
        return Vec2(
            (path.points[0].x + path.points[1].x) * 0.5,
            (path.points[0].y + path.points[1].y) * 0.5,
        )

    minimum_spacing = max(float(minimum_spacing), float(tab_width), 1.0e-9)

    # The equal-arclength fallback can place owner-only tabs on two edges very
    # close to the same corner.  Keep a maximally separated subset and turn
    # the rejected intervals back into normal cut intervals before adding
    # SharedTabs.  This avoids asking the operator to diagnose an internal
    # fallback choice.
    accepted_tab_indexes = set()
    for owner in sorted(required_by_owner):
        owner_indexes = [
            index
            for index, path in enumerate(paths)
            if path.tab and path.owner_ids == (owner,)
        ]
        if not owner_indexes:
            continue
        contour = tuple(owner_contours.get(owner, ()))
        owner_indexes.sort(
            key=lambda index: (
                _perimeter_position(path_midpoint(paths[index]), contour),
                index,
            )
        )
        chosen = [owner_indexes.pop(0)]
        while owner_indexes:
            compatible = [
                index
                for index in owner_indexes
                if all(
                    path_midpoint(paths[index]).distance_to(
                        path_midpoint(paths[existing])
                    )
                    >= minimum_spacing - 1.0e-9
                    for existing in chosen
                )
            ]
            if not compatible:
                break
            next_index = max(
                compatible,
                key=lambda index: (
                    min(
                        path_midpoint(paths[index]).distance_to(
                            path_midpoint(paths[existing])
                        )
                        for existing in chosen
                    ),
                    -index,
                ),
            )
            chosen.append(next_index)
            owner_indexes.remove(next_index)
        accepted_tab_indexes.update(chosen)

    normalised_paths = []
    for index, path in enumerate(paths):
        if (
            path.tab
            and len(path.owner_ids) == 1
            and index not in accepted_tab_indexes
        ):
            normalised_paths.append(
                CommonLineCutPath(path.points, path.owner_ids, path.shared, False)
            )
        else:
            normalised_paths.append(path)
    paths = tuple(normalised_paths)

    centres = {owner: [] for owner in required_by_owner}
    for path in paths:
        if path.tab:
            for owner in path.owner_ids:
                centres.setdefault(owner, []).append(path_midpoint(path))

    def owner_gap(owner, proposed):
        contour = tuple(owner_contours.get(owner, ()))
        if not contour or len(proposed) < 2:
            return float("inf")
        perimeter = sum(
            start.distance_to(end)
            for start, end in zip(contour, contour[1:] + contour[:1])
        )
        positions = sorted(_perimeter_position(point, contour) for point in proposed)
        gaps = [second - first for first, second in zip(positions, positions[1:])]
        gaps.append(perimeter - positions[-1] + positions[0])
        return max(gaps)

    def unsafe(owner):
        proposed = centres.get(owner, ())
        return (
            len(proposed) < int(required_by_owner.get(owner, 0))
            or owner_gap(owner, proposed) > maximum_free_span + 1.0e-7
        )

    # One common-line atom may be hundreds of millimetres long.  Treating its
    # midpoint as the only possible SharedTab made dense layouts impossible
    # even though the physical edge had ample room for several bridges.
    candidate_step = max(minimum_spacing, float(maximum_free_span) / 3.0)
    candidates = []
    for path_index, path in enumerate(paths):
        if not path.shared or path.tab:
            continue
        length = path.points[0].distance_to(path.points[1])
        if length < tab_width - 1.0e-9:
            continue
        half = tab_width * 0.5
        spare = max(0.0, length - tab_width)
        corner_margin = min(tab_width * 0.5, spare * 0.5)
        low = half + corner_margin
        high = length - half - corner_margin
        if high < low + 1.0e-9:
            positions = (length * 0.5,)
        else:
            count = max(1, int(math.ceil((high - low) / candidate_step)) + 1)
            positions = tuple(
                (low + high) * 0.5
                if count == 1
                else low + (high - low) * index / (count - 1)
                for index in range(count)
            )
        vector = path.points[1] - path.points[0]
        for position in positions:
            parameter = position / length
            candidates.append(
                (
                    path_index,
                    parameter,
                    path.points[0] + vector * parameter,
                    path.owner_ids,
                )
            )

    available = set(range(len(candidates)))
    selected = set()
    while any(unsafe(owner) for owner in required_by_owner):
        best = None
        for candidate_index in sorted(available):
            (
                path_index,
                _parameter,
                point,
                path_owner_ids,
            ) = candidates[candidate_index]
            owners = tuple(
                owner for owner in path_owner_ids if owner in required_by_owner
            )
            if not owners or not any(unsafe(owner) for owner in owners):
                continue
            if any(
                point.distance_to(existing) < minimum_spacing - 1.0e-9
                for owner in owners
                for existing in centres.get(owner, ())
            ):
                continue
            helped = 0
            count_gain = 0
            gap_gain = 0.0
            for owner in owners:
                before = centres.get(owner, ())
                before_unsafe = unsafe(owner)
                after = tuple(before) + (point,)
                after_unsafe = (
                    len(after) < int(required_by_owner.get(owner, 0))
                    or owner_gap(owner, after) > maximum_free_span + 1.0e-7
                )
                helped += int(before_unsafe and not after_unsafe)
                count_gain += int(
                    len(before) < int(required_by_owner.get(owner, 0))
                )
                before_gap = owner_gap(owner, before)
                after_gap = owner_gap(owner, after)
                if math.isfinite(before_gap) and math.isfinite(after_gap):
                    gap_gain += max(0.0, before_gap - after_gap)
            separation = min(
                (
                    point.distance_to(existing)
                    for owner in owners
                    for existing in centres.get(owner, ())
                ),
                default=float(maximum_free_span),
            )
            candidate = (
                helped,
                count_gain,
                gap_gain,
                separation,
                -path_index,
                -candidate_index,
                candidate_index,
            )
            if best is None or candidate > best:
                best = candidate
        # Reducing the largest unsupported arc can require two placements:
        # the first divides a non-critical part of the same long arc and only
        # the second lowers its maximum.  Do not stop merely because one
        # candidate has no immediate scalar gap gain.
        if best is None:
            break
        index = best[-1]
        available.remove(index)
        selected.add(index)
        _path_index, _parameter, point, owner_ids = candidates[index]
        for owner in owner_ids:
            if owner in required_by_owner:
                centres.setdefault(owner, []).append(point)

    selected_by_path = {}
    for candidate_index in selected:
        path_index, parameter, _point, _owners = candidates[candidate_index]
        selected_by_path.setdefault(path_index, []).append(parameter)
    result = []
    for index, path in enumerate(paths):
        parameters = selected_by_path.get(index, ())
        if not parameters:
            result.append(path)
            continue
        split = _split_path_for_tabs(path, tab_width, parameters)
        result.extend(split)

    invalid = [owner for owner in sorted(required_by_owner) if unsafe(owner)]
    if invalid:
        details = ", ".join(invalid)
        raise GlobalCutPlanError(
            "não há regiões StockTab/SharedTab suficientes ou bem distribuídas "
            "para: %s" % details
        )
    return tuple(result)


def _paths_to_segments(
    paths: Sequence[CommonLineCutPath],
    kind: PhysicalSegmentKind,
    tolerance: float,
    waste_id: str | None = None,
    tab_height_override: float | None = None,
) -> Tuple[PhysicalCutSegment, ...]:
    result = []
    used_ids = set()
    for path in paths:
        segment_kind = PhysicalSegmentKind.SHARED if path.shared else kind
        segment_id = _segment_id(
            segment_kind,
            path.owner_ids,
            path.points[0],
            path.points[1],
            tolerance,
        )
        if segment_id in used_ids:
            raise GlobalCutPlanError("segmento físico duplicado antes do agendamento")
        used_ids.add(segment_id)
        result.append(
            PhysicalCutSegment(
                segment_id,
                path.points[0],
                path.points[1],
                segment_kind,
                tuple(path.owner_ids),
                bool(path.tab),
                waste_id,
                tab_height_override if path.tab else None,
            )
        )
    return tuple(result)


def _split_trails_for_safe_entry(
    segments: Sequence[PhysicalCutSegment],
    tolerance: float,
    tool_diameter: float,
) -> Tuple[PhysicalCutSegment, ...]:
    """Create straight, corner-clear nodes usable by every routed trail.

    ``GlobalCutTrail`` can start only at a physical segment boundary. Source
    SVG paths often start inside a flattened Dogbone arc, so retaining the
    source boundary as the first trail node made the cutter plunge in the
    relief. Split each sufficiently long non-tab edge at its midpoint instead.
    The new physical atoms preserve exactly the same geometry and total
    coverage while giving the router safe candidates around the whole profile.
    This happens before shared/exclusive ownership separates one piece
    perimeter into open fragments, so reconstructed owner trails retain the
    safe nodes too.
    """

    segments = tuple(segments)
    if not segments:
        return ()
    minimum_clearance = max(abs(float(tool_diameter)) * 2.0, 12.0)
    split_ids = {
        segment.segment_id
        for segment in segments
        if not segment.retains_tab
        and segment.kind != PhysicalSegmentKind.SHARED
        and segment.length >= minimum_clearance * 2.0 - 1.0e-9
    }
    if not split_ids:
        return segments

    result = []
    used_ids = {
        segment.segment_id
        for segment in segments
        if segment.segment_id not in split_ids
    }
    for segment in segments:
        if segment.segment_id not in split_ids:
            result.append(segment)
            continue
        midpoint = segment.start.lerp(segment.end, 0.5)
        for start, end in ((segment.start, midpoint), (midpoint, segment.end)):
            segment_id = _segment_id(
                segment.kind,
                segment.owner_ids,
                start,
                end,
                tolerance,
            )
            if segment_id in used_ids:
                raise GlobalCutPlanError(
                    "segmento físico duplicado ao criar entrada segura"
                )
            used_ids.add(segment_id)
            result.append(
                PhysicalCutSegment(
                    segment_id,
                    start,
                    end,
                    segment.kind,
                    segment.owner_ids,
                    segment.retains_tab,
                    segment.waste_id,
                    segment.tab_height_override,
                )
            )
    return tuple(result)


def _rings_touch_or_cross(first, second, tolerance):
    for first_index, first_start in enumerate(first):
        first_end = first[(first_index + 1) % len(first)]
        for second_index, second_start in enumerate(second):
            second_end = second[(second_index + 1) % len(second)]
            if segment_intersections(
                first_start,
                first_end,
                second_start,
                second_end,
                tolerance,
            ):
                return True
    return False


def _axis_aligned_stock_bounds(points, tolerance):
    """Return rectangular stock bounds, or ``None`` for unsupported stock."""

    points = tuple(points or ())
    if len(points) < 4:
        return None
    min_x = min(point.x for point in points)
    max_x = max(point.x for point in points)
    min_y = min(point.y for point in points)
    max_y = max(point.y for point in points)
    if max_x - min_x <= tolerance or max_y - min_y <= tolerance:
        return None
    for point in points:
        on_vertical = abs(point.x - min_x) <= tolerance or abs(point.x - max_x) <= tolerance
        on_horizontal = abs(point.y - min_y) <= tolerance or abs(point.y - max_y) <= tolerance
        if not (on_vertical or on_horizontal):
            return None
    return min_x, min_y, max_x, max_y


def _point_inside_any(point, contours, tolerance):
    return any(
        point_in_polygon(point, contour.points, tolerance)
        != PointLocation.OUTSIDE
        for contour in contours
    )


def _enclosed_waste_components(
    stock_boundary,
    outer_contours,
    rules,
    tolerance,
):
    """Conservatively find free-space components disconnected from stock edge.

    Raster cells are used only for connectivity.  A cell is considered blocked
    only when its centre and four inset corners are all inside useful parts;
    diagonal connectivity is allowed.  Both choices intentionally over-connect
    free space, so a narrow passage may hide a real loose rest but cannot create
    a dangerous false enclosed rest.  Machining positions remain vector-exact.
    """

    bounds = _axis_aligned_stock_bounds(stock_boundary, tolerance)
    if bounds is None or not outer_contours:
        return ()
    min_x, min_y, max_x, max_y = bounds
    requested = max(
        float(getattr(rules, "waste_detection_resolution_mm", 5.0)),
        tolerance * 4.0,
    )
    width = max_x - min_x
    height = max_y - min_y
    nx = max(1, int(math.ceil(width / requested)))
    ny = max(1, int(math.ceil(height / requested)))
    max_cells = max(1, int(getattr(rules, "waste_detection_max_cells", 250000)))
    if nx * ny > max_cells:
        scale = math.sqrt(float(nx * ny) / max_cells)
        nx = max(1, int(math.ceil(nx / scale)))
        ny = max(1, int(math.ceil(ny / scale)))
    cell_width = width / nx
    cell_height = height / ny

    cell_contours = {}
    for contour in outer_contours:
        contour_min_x = min(point.x for point in contour.points)
        contour_max_x = max(point.x for point in contour.points)
        contour_min_y = min(point.y for point in contour.points)
        contour_max_y = max(point.y for point in contour.points)
        first_x = max(0, int(math.floor((contour_min_x - min_x) / cell_width)))
        last_x = min(nx - 1, int(math.floor((contour_max_x - min_x) / cell_width)))
        first_y = max(0, int(math.floor((contour_min_y - min_y) / cell_height)))
        last_y = min(ny - 1, int(math.floor((contour_max_y - min_y) / cell_height)))
        for iy in range(first_y, last_y + 1):
            for ix in range(first_x, last_x + 1):
                cell_contours.setdefault((ix, iy), []).append(contour)

    def cell_samples(ix, iy):
        x0 = min_x + ix * cell_width
        y0 = min_y + iy * cell_height
        x1 = x0 + cell_width
        y1 = y0 + cell_height
        inset_x = min(cell_width * 0.12, tolerance * 2.0)
        inset_y = min(cell_height * 0.12, tolerance * 2.0)
        return (
            Vec2((x0 + x1) * 0.5, (y0 + y1) * 0.5),
            Vec2(x0 + inset_x, y0 + inset_y),
            Vec2(x1 - inset_x, y0 + inset_y),
            Vec2(x1 - inset_x, y1 - inset_y),
            Vec2(x0 + inset_x, y1 - inset_y),
        )

    free = set()
    centres = {}
    for iy in range(ny):
        for ix in range(nx):
            samples = cell_samples(ix, iy)
            candidates = cell_contours.get((ix, iy), ())
            if any(
                not _point_inside_any(sample, candidates, tolerance)
                for sample in samples
            ):
                free.add((ix, iy))
                centres[(ix, iy)] = samples[0]

    components = []
    remaining = set(free)
    neighbours = tuple(
        (dx, dy)
        for dx in (-1, 0, 1)
        for dy in (-1, 0, 1)
        if dx or dy
    )
    while remaining:
        seed = min(remaining, key=lambda value: (value[1], value[0]))
        stack = [seed]
        remaining.remove(seed)
        component = []
        touches_stock_edge = False
        while stack:
            cell = stack.pop()
            component.append(cell)
            ix, iy = cell
            touches_stock_edge = touches_stock_edge or (
                ix == 0 or iy == 0 or ix == nx - 1 or iy == ny - 1
            )
            for dx, dy in neighbours:
                neighbour = (ix + dx, iy + dy)
                if neighbour in remaining:
                    remaining.remove(neighbour)
                    stack.append(neighbour)
        if not touches_stock_edge:
            components.append(
                {
                    "cells": tuple(component),
                    "cell_set": frozenset(component),
                    "centres": tuple(centres[cell] for cell in component),
                    "cell_width": cell_width,
                    "cell_height": cell_height,
                    "bounds": bounds,
                    "shape": (nx, ny),
                }
            )
    return tuple(components)


def _component_for_point(point, components):
    for index, component in enumerate(components):
        min_x, min_y, max_x, max_y = component["bounds"]
        nx, ny = component["shape"]
        if not (min_x <= point.x <= max_x and min_y <= point.y <= max_y):
            continue
        ix = min(nx - 1, max(0, int((point.x - min_x) / component["cell_width"])))
        iy = min(ny - 1, max(0, int((point.y - min_y) / component["cell_height"])))
        if (ix, iy) in component["cell_set"]:
            return index
    return None


def _segment_waste_component(segment, components, outer_contours, tolerance):
    if segment.kind != PhysicalSegmentKind.EXTERNAL or not components:
        return None
    midpoint = segment.start.lerp(segment.end, 0.5)
    vector = segment.end - segment.start
    length = segment.length
    if length <= tolerance:
        return None
    normal = Vec2(-vector.y / length, vector.x / length)
    probe_distance = max(
        tolerance * 4.0,
        min(component["cell_width"] for component in components) * 0.60,
    )
    for sign in (-1.0, 1.0):
        probe = midpoint + normal * (probe_distance * sign)
        if _point_inside_any(probe, outer_contours, tolerance):
            continue
        component_index = _component_for_point(probe, components)
        if component_index is not None:
            return component_index
    return None


def _split_waste_tab_segment(
    segment,
    tab_width,
    waste_id,
    tab_height,
    tolerance,
    centre_parameter=0.5,
):
    width = min(max(0.0, float(tab_width)), segment.length)
    if width <= tolerance or segment.length + tolerance < width:
        return None
    direction = (segment.end - segment.start) * (1.0 / segment.length)
    half_width = width * 0.5
    centre_distance = max(
        half_width,
        min(
            segment.length - half_width,
            segment.length * max(0.0, min(1.0, float(centre_parameter))),
        ),
    )
    tab_start = segment.start + direction * (centre_distance - half_width)
    tab_end = tab_start + direction * width
    intervals = []
    if segment.start.distance_to(tab_start) > tolerance:
        intervals.append((segment.start, tab_start, False))
    intervals.append((tab_start, tab_end, True))
    if segment.end.distance_to(tab_end) > tolerance:
        intervals.append((tab_end, segment.end, False))
    result = []
    for start, end, retained in intervals:
        result.append(
            PhysicalCutSegment(
                _segment_id(
                    segment.kind,
                    segment.owner_ids,
                    start,
                    end,
                    tolerance,
                ),
                start,
                end,
                segment.kind,
                segment.owner_ids,
                retained,
                waste_id if retained else None,
                tab_height if retained else None,
            )
        )
    return tuple(result)


def _balanced_waste_tab_placements(
    segments,
    candidates,
    required_count,
    tab_width,
    tool_diameter,
    rules,
    tolerance,
):
    """Choose mechanically useful waste bridges instead of list-order edges.

    Full-width tabs with real corner clearance form the preferred tier.  The
    pair is selected jointly, so a short segment encountered first cannot
    force a tiny bridge when two long/opposed boundaries are available.
    Candidate positions near both usable ends are considered as well as the
    midpoint; this spreads two tabs along elongated scraps.
    """

    requested_width = max(0.0, float(tab_width))
    required_count = max(0, int(required_count))
    if required_count <= 0:
        return ()
    corner_clearance = max(
        float(rules.minimum_corner_clearance_mm),
        requested_width * float(rules.corner_clearance_tab_factor),
        abs(float(tool_diameter)) * float(rules.corner_clearance_tool_factor),
    )

    data = {}
    for index in sorted(set(candidates)):
        segment = segments[index]
        length = float(segment.length)
        actual_width = min(requested_width, length)
        if actual_width <= tolerance:
            continue
        if length + tolerance >= requested_width + 2.0 * corner_clearance:
            tier = 2
        elif length + tolerance >= requested_width:
            tier = 1
        else:
            tier = 0
        available_margin = max(0.0, (length - actual_width) * 0.5)
        applied_clearance = min(corner_clearance, available_margin)
        first_distance = actual_width * 0.5 + applied_clearance
        last_distance = length - actual_width * 0.5 - applied_clearance
        distances = {length * 0.5, first_distance, last_distance}
        parameters = tuple(
            sorted(
                max(0.0, min(1.0, distance / length))
                for distance in distances
            )
        )
        direction = (segment.end - segment.start) * (1.0 / length)
        data[index] = {
            "tier": tier,
            "length": length,
            "width": actual_width,
            "direction": direction,
            "placements": tuple(
                (parameter, segment.start.lerp(segment.end, parameter))
                for parameter in parameters
            ),
        }
    if len(data) < required_count:
        return ()

    if required_count == 1:
        index = max(
            data,
            key=lambda candidate: (
                data[candidate]["tier"],
                data[candidate]["length"],
                -candidate,
            ),
        )
        return ((index, 0.5),)

    best_pair = None
    for first_index, second_index in itertools.combinations(sorted(data), 2):
        first = data[first_index]
        second = data[second_index]
        parallelism = abs(first["direction"].dot(second["direction"]))
        for first_parameter, first_point in first["placements"]:
            for second_parameter, second_point in second["placements"]:
                separation = first_point.distance_to(second_point)
                key = (
                    min(first["tier"], second["tier"]),
                    first["tier"] + second["tier"],
                    min(first["length"], second["length"]),
                    first["length"] + second["length"],
                    parallelism,
                    separation,
                    -first_index,
                    -second_index,
                    -first_parameter,
                    -second_parameter,
                )
                if best_pair is None or key > best_pair[0]:
                    best_pair = (
                        key,
                        (
                            (first_index, first_parameter, first_point),
                            (second_index, second_parameter, second_point),
                        ),
                    )
    if best_pair is None:
        return ()

    selected = list(best_pair[1])
    while len(selected) < required_count:
        used = {item[0] for item in selected}
        best_addition = None
        for index in sorted(data):
            if index in used:
                continue
            item = data[index]
            for parameter, point in item["placements"]:
                separation = min(
                    point.distance_to(existing_point)
                    for _existing_index, _existing_parameter, existing_point in selected
                )
                key = (
                    item["tier"],
                    item["length"],
                    separation,
                    -index,
                    -parameter,
                )
                if best_addition is None or key > best_addition[0]:
                    best_addition = (key, (index, parameter, point))
        if best_addition is None:
            return ()
        selected.append(best_addition[1])
    return tuple((index, parameter) for index, parameter, _point in selected)


def _fix_enclosed_waste_components(
    segments,
    components,
    outer_contours,
    internal_contours,
    mode,
    rules,
    tolerance,
    tab_width,
    material_thickness,
    tool_diameter,
    tool_type,
    pilot_diameter,
    pilot_depth,
    head_diameter,
    safety_margin,
    head_height,
    first_waste_index,
    first_anchor_index,
):
    """Return split segments plus exact anchors for nesting-created wastes."""

    if not components:
        return tuple(segments), (), ()
    segment_components = {
        index: _segment_waste_component(
            segment, components, outer_contours, tolerance
        )
        for index, segment in enumerate(segments)
    }
    rings = tuple(contour.points for contour in outer_contours) + tuple(
        contour.points for contour in internal_contours
    )
    replacements = {}
    regions = []
    anchors = []
    for component_index, component in enumerate(components):
        waste_id = "waste-nesting-%04d" % (first_waste_index + component_index)
        fixation = "disabled"
        fallback_reason = ""
        use_tabs = mode == LooseWasteFixationMode.TABS
        if mode == LooseWasteFixationMode.SCREWS:
            if material_thickness is None or float(material_thickness) <= 0.0:
                raise GlobalCutPlanError(
                    "fixação de restos por parafuso exige espessura do material"
                )
            if not _tool_supports_vertical_plunge(tool_type):
                raise GlobalCutPlanError(
                    "a ferramenta atual não admite os furos piloto dos parafusos"
                )
            requested_pilot = abs(float(pilot_diameter))
            if requested_pilot > 1.0e-9 and requested_pilot < abs(float(tool_diameter)) - tolerance:
                raise GlobalCutPlanError(
                    "o diâmetro piloto configurado é menor que a fresa atual"
                )
            clearance_needed = (
                max(0.0, float(head_diameter)) * 0.5
                + max(0.0, float(tool_diameter)) * 0.5
                + max(0.0, float(safety_margin))
            )
            best = None
            for point in component["centres"]:
                if _point_inside_any(point, outer_contours, tolerance):
                    continue
                clearance = min(
                    (
                        _point_segment_distance(point, start, end)
                        for ring in rings
                        for start, end in zip(ring, ring[1:] + ring[:1])
                    ),
                    default=0.0,
                )
                candidate = (clearance, -point.y, -point.x, point)
                if best is None or candidate[:3] > best[:3]:
                    best = candidate
            if best is not None and best[0] + tolerance >= clearance_needed:
                point = best[3]
                anchors.append(
                    ScrewAnchor(
                        "screw-%04d" % (first_anchor_index + len(anchors)),
                        waste_id,
                        point,
                        max(abs(float(tool_diameter)), requested_pilot),
                        (
                            abs(float(pilot_depth))
                            if abs(float(pilot_depth)) > 1.0e-9
                            else abs(float(material_thickness)) + 2.0
                        ),
                        max(0.0, float(head_diameter)) * 0.5
                        + max(0.0, float(safety_margin)),
                        max(0.0, float(head_height)),
                    )
                )
                fixation = "screw"
            else:
                use_tabs = True
                fallback_reason = "sem posição vetorial segura para o parafuso"
        if use_tabs:
            candidates = [
                index
                for index, detected in segment_components.items()
                if detected == component_index
                and index not in replacements
                and not segments[index].retains_tab
                # Waste retention is allowed to shorten the horizontal bridge
                # to the available straight edge.  Requiring the full piece-
                # tab width made a harmless small offcut block the entire job.
                # Height remains the full material thickness.
                and segments[index].length > tolerance
            ]
            selected = _balanced_waste_tab_placements(
                segments,
                candidates,
                rules.minimum_loose_waste_tabs,
                tab_width,
                tool_diameter,
                rules,
                tolerance,
            )
            if len(selected) < rules.minimum_loose_waste_tabs:
                raise GlobalCutPlanError(
                    "o resto %s não possui duas bordas vetoriais distintas para tabs"
                    % waste_id
                )
            for index, centre_parameter in selected:
                replacement = _split_waste_tab_segment(
                    segments[index],
                    tab_width,
                    waste_id,
                    abs(float(material_thickness or 0.0)),
                    tolerance,
                    centre_parameter=centre_parameter,
                )
                if not replacement:
                    raise GlobalCutPlanError(
                        "não foi possível criar tab segura no resto %s" % waste_id
                    )
                replacements[index] = replacement
            fixation = "tabs"
        region_points = tuple(
            sorted(component["centres"], key=lambda point: (point.x, point.y))
        )
        regions.append(
            WasteRegion(
                waste_id,
                "",
                region_points,
                len(component["cells"])
                * component["cell_width"]
                * component["cell_height"],
                fixation,
                fallback_reason,
            )
        )
    final_segments = []
    for index, segment in enumerate(segments):
        final_segments.extend(replacements.get(index, (segment,)))
    return tuple(final_segments), tuple(regions), tuple(anchors)


def _join_segments(
    segments: Sequence[PhysicalCutSegment],
    tolerance: float,
    prefix: str,
) -> Tuple[GlobalCutTrail, ...]:
    if not segments:
        return ()
    adjacency = {}
    for index, segment in enumerate(segments):
        for point in (segment.start, segment.end):
            adjacency.setdefault(_canonical_key(point, point, tolerance)[0], []).append(index)
    unused = set(range(len(segments)))
    trails = []
    while unused:
        degrees = {
            node: sum(index in unused for index in indexes)
            for node, indexes in adjacency.items()
        }
        odd = sorted(node for node, degree in degrees.items() if degree % 2)
        current = odd[0] if odd else min(node for node, degree in degrees.items() if degree)
        points = []
        ids = []
        tabs = []
        owners = set()
        trail_kind = None
        while True:
            candidates = sorted(index for index in adjacency.get(current, ()) if index in unused)
            if not candidates:
                break
            index = candidates[0]
            segment = segments[index]
            start_key = _canonical_key(segment.start, segment.start, tolerance)[0]
            if current == start_key:
                start, end = segment.start, segment.end
            else:
                start, end = segment.end, segment.start
            if not points:
                points.append(start)
            points.append(end)
            ids.append(segment.segment_id)
            tabs.append(segment.retains_tab)
            owners.update(segment.owner_ids)
            trail_kind = segment.kind
            unused.remove(index)
            current = _canonical_key(end, end, tolerance)[0]
        trails.append(
            GlobalCutTrail(
                "%s-%04d" % (prefix, len(trails) + 1),
                tuple(points),
                tuple(ids),
                tuple(tabs),
                trail_kind,
                tuple(sorted(owners)),
            )
        )
    return tuple(trails)


def _build_trails(segments: Sequence[PhysicalCutSegment], tolerance: float):
    trails = []
    internal_ids = sorted({owner for segment in segments if segment.kind == PhysicalSegmentKind.INTERNAL for owner in segment.owner_ids})
    for owner in internal_ids:
        selected = tuple(segment for segment in segments if segment.kind == PhysicalSegmentKind.INTERNAL and segment.owner_ids == (owner,))
        trails.extend(_join_segments(selected, tolerance, "internal-%s" % owner))
    shared = tuple(segment for segment in segments if segment.kind == PhysicalSegmentKind.SHARED)
    trails.extend(_join_segments(shared, tolerance, "shared"))
    external_ids = sorted({owner for segment in segments if segment.kind == PhysicalSegmentKind.EXTERNAL for owner in segment.owner_ids})
    for owner in external_ids:
        selected = tuple(segment for segment in segments if segment.kind == PhysicalSegmentKind.EXTERNAL and segment.owner_ids == (owner,))
        trails.extend(_join_segments(selected, tolerance, "external-%s" % owner))
    return tuple(trails)


def _tabs_from_segments(segments, thickness):
    result = []
    for segment in segments:
        if not segment.retains_tab:
            continue
        kind = (
            TabRetentionKind.WASTE
            if segment.waste_id
            else (
                TabRetentionKind.SHARED
                if segment.kind == PhysicalSegmentKind.SHARED
                else TabRetentionKind.STOCK
            )
        )
        result.append(
            RetentionTab(
                "tab-%04d" % (len(result) + 1),
                segment.segment_id,
                kind,
                segment.owner_ids,
                segment.start,
                segment.end,
                segment.length,
                max(
                    0.0,
                    float(
                        segment.tab_height_override
                        if segment.tab_height_override is not None
                        else thickness
                    ),
                ),
                segment.waste_id,
                not bool(segment.waste_id),
            )
        )
    return tuple(result)


def _perimeter_position(point: Vec2, contour: Sequence[Vec2]) -> float:
    progress = 0.0
    best = None
    for start, end in zip(contour, contour[1:] + contour[:1]):
        vector = end - start
        length = start.distance_to(end)
        if length <= 1.0e-12:
            continue
        parameter = max(0.0, min(1.0, (point - start).dot(vector) / (length * length)))
        projected = start + vector * parameter
        candidate = (point.distance_to(projected), progress + parameter * length)
        if best is None or candidate < best:
            best = candidate
        progress += length
    return 0.0 if best is None else best[1]


def _maximum_free_span(
    metrics: PieceMetrics,
    tab_centres: Sequence[Vec2],
    contour: Sequence[Vec2] = (),
) -> float:
    if not tab_centres:
        return metrics.perimeter
    if contour:
        positions = sorted(_perimeter_position(point, tuple(contour)) for point in tab_centres)
        if len(positions) == 1:
            return metrics.perimeter
        gaps = [second - first for first, second in zip(positions, positions[1:])]
        gaps.append(metrics.perimeter - positions[-1] + positions[0])
        return max(gaps)
    return metrics.perimeter


def _stability_reports(
    metrics,
    tabs,
    rules,
    requested,
    tool_diameter,
    tab_width,
    outer_contours=(),
):
    graph = RetentionGraph(tuple(tabs))
    contour_by_owner = {
        contour.contour_id: tuple(contour.points) for contour in outer_contours
    }
    reports = []
    for piece in metrics:
        incident = tuple(
            tab
            for tab in tabs
            if tab.kind != TabRetentionKind.WASTE
            and piece.owner_id in tab.owner_ids
        )
        required = rules.required_tab_count(piece, requested, tool_diameter, tab_width)
        connected = graph.connected_to_stock(piece.owner_id)
        free_span = _maximum_free_span(
            piece,
            [tab.centre for tab in incident],
            contour_by_owner.get(piece.owner_id, ()),
        )
        reasons = []
        if len(incident) < required:
            reasons.append("%d tabs; mínimo mecânico %d" % (len(incident), required))
        if not connected:
            reasons.append("sem ligação direta ou indireta ao stock")
        if (
            len(incident) > 1
            and free_span
            > min(
                piece.perimeter * rules.maximum_free_perimeter_ratio,
                rules.maximum_unsupported_perimeter_mm,
            )
        ):
            reasons.append("tabs concentradas; trecho livre excessivo")
        if len(incident) > 1:
            minimum_spacing = min(
                first.centre.distance_to(second.centre)
                for index, first in enumerate(incident)
                for second in incident[index + 1:]
            )
            required_spacing = (
                abs(float(tab_width)) * rules.minimum_tab_spacing_factor
            )
            if minimum_spacing + rules.geometric_tolerance < required_spacing:
                reasons.append(
                    "tabs sobrepostas ou próximas demais (%.2f mm; mínimo %.2f mm)"
                    % (minimum_spacing, required_spacing)
                )
        reports.append(
            StabilityReport(
                piece.owner_id,
                required,
                len(incident),
                connected,
                free_span,
                not reasons,
                tuple(reasons),
            )
        )
    return tuple(reports)


def _operation_phase(trail):
    return {
        PhysicalSegmentKind.INTERNAL: CutPhase.INTERNAL,
        PhysicalSegmentKind.SHARED: CutPhase.SHARED,
        PhysicalSegmentKind.EXTERNAL: CutPhase.EXTERNAL,
    }[trail.kind]


def _route_variants(
    trail: GlobalCutTrail,
    *,
    geometric_vertices_only: bool = False,
) -> Tuple[TrailRoute, ...]:
    """Enumerate direction/start variants that preserve physical geometry."""

    variants = []
    if trail.closed:
        edge_count = len(trail.segment_ids)
        safe_offsets = []
        for index in range(edge_count):
            previous = trail.points[index - 1]
            current = trail.points[index]
            following = trail.points[index + 1]
            incoming = current - previous
            outgoing = following - current
            scale = max(incoming.length() * outgoing.length(), 1.0e-12)
            cross_ratio = abs(incoming.cross(outgoing)) / scale
            dot_ratio = incoming.dot(outgoing) / scale
            if (
                cross_ratio <= 1.0e-7
                and dot_ratio >= 1.0 - 1.0e-7
                and min(incoming.length(), outgoing.length()) >= 12.0 - 1.0e-9
            ):
                safe_offsets.append(index)
        if safe_offsets:
            # Midpoints inserted by ``_split_trails_for_safe_entry``
            # are collinear and have real clearance on both sides. Never
            # trade them for a nearer Dogbone chord or geometric corner.
            offsets = safe_offsets
        elif geometric_vertices_only:
            # A tiny all-curved contour may have no straight entry at all.
            # Keep it machinable, but do not pretend one of its corners is a
            # preferred/safe geometric entry.
            offsets = list(range(edge_count))
        elif edge_count <= 48:
            offsets = list(range(edge_count))
        else:
            offsets = sorted(
                {
                    int(round(index * (edge_count - 1) / 47.0))
                    for index in range(48)
                }
            )
        if len(offsets) > 48:
            offsets = [
                offsets[
                    int(round(index * (len(offsets) - 1) / 47.0))
                ]
                for index in range(48)
            ]
        # Closed profile direction is preserved because climb/conventional
        # milling can make reversal mechanically significant.  Rotating the
        # entry edge is geometry-neutral and still removes long rapids.
        for offset in offsets:
            prepared = trail.oriented(start_edge_index=offset)
            variants.append(
                TrailRoute(
                    trail.trail_id,
                    False,
                    offset,
                    prepared.points[0],
                    prepared.points[-1],
                )
            )
        if trail.reversible:
            for offset in offsets:
                # ``oriented`` reverses the edge list before applying its
                # rotation. The same numeric offset therefore names a
                # different physical vertex after reversal. Map the original
                # safe vertex into reversed-edge coordinates; otherwise a
                # midpoint chosen on a straight can silently become the SVG's
                # first Dogbone chord when the bidirectional router reverses.
                reverse_offset = (-offset) % edge_count
                prepared = trail.oriented(
                    reverse=True,
                    start_edge_index=reverse_offset,
                )
                variants.append(
                    TrailRoute(
                        trail.trail_id,
                        True,
                        reverse_offset,
                        prepared.points[0],
                        prepared.points[-1],
                    )
                )
    else:
        direct = trail.oriented()
        variants.append(
            TrailRoute(
                trail.trail_id,
                False,
                0,
                direct.points[0],
                direct.points[-1],
            )
        )
        # Only a physical shared line is direction-neutral.  Reversing an
        # owner profile could silently change climb/conventional cutting.
        if trail.kind == PhysicalSegmentKind.SHARED or trail.reversible:
            reverse = trail.oriented(reverse=True)
            variants.append(
                TrailRoute(
                    trail.trail_id,
                    True,
                    0,
                    reverse.points[0],
                    reverse.points[-1],
                )
            )
    return tuple(variants)


def _best_variants_for_order(trails, start: Vec2, end_targets=()):
    """Dynamic-program endpoint orientation for a fixed, safety-safe order.

    ``end_targets`` does not reorder across a phase/retention barrier.  It only
    prevents a locally optimal group from finishing at the worst possible end
    for the next equally valid group.
    """

    if not trails:
        return (), 0.0
    variants_by_index = [_route_variants(trail) for trail in trails]
    costs = [start.distance_to(variant.start) for variant in variants_by_index[0]]
    parents = []
    for trail_index in range(1, len(trails)):
        previous_variants = variants_by_index[trail_index - 1]
        variants = variants_by_index[trail_index]
        next_costs = []
        next_parents = []
        for variant in variants:
            choices = [
                (
                    costs[previous_index]
                    + previous.end.distance_to(variant.start),
                    previous_index,
                )
                for previous_index, previous in enumerate(previous_variants)
            ]
            cost, parent = min(choices, key=lambda item: (item[0], item[1]))
            next_costs.append(cost)
            next_parents.append(parent)
        parents.append(next_parents)
        costs = next_costs
    targets = tuple(end_targets or ())
    def terminal_cost(index):
        return (
            min(
                variants_by_index[-1][index].end.distance_to(target)
                for target in targets
            )
            if targets
            else 0.0
        )

    final_variant = min(
        range(len(costs)),
        key=lambda index: (
            costs[index] + terminal_cost(index),
            costs[index],
            index,
        ),
    )
    selected_indexes = [final_variant]
    for parent_row in reversed(parents):
        selected_indexes.append(parent_row[selected_indexes[-1]])
    selected_indexes.reverse()
    selected = tuple(
        variants_by_index[index][variant_index]
        for index, variant_index in enumerate(selected_indexes)
    )
    return selected, costs[final_variant] + terminal_cost(final_variant)


def _nearest_neighbour_order(trails, start: Vec2):
    remaining = list(trails)
    ordered = []
    current = start
    while remaining:
        candidates = []
        for trail in remaining:
            variant = min(
                _route_variants(trail),
                key=lambda value: (
                    current.distance_to(value.start),
                    value.reverse,
                    value.start_edge_index,
                ),
            )
            candidates.append(
                (
                    current.distance_to(variant.start),
                    trail.trail_id,
                    trail,
                    variant,
                )
            )
        _distance, _trail_id, trail, variant = min(
            candidates,
            key=lambda item: (item[0], item[1]),
        )
        ordered.append(trail)
        current = variant.end
        remaining.remove(trail)
    return ordered


def optimize_fast_trails(
    trails: Sequence[GlobalCutTrail],
    start_xy: Vec2 | Sequence[float] = Vec2(0.0, 0.0),
    *,
    end_targets: Sequence[Vec2] = (),
) -> Tuple[TrailRoute, ...]:
    """Nearest-end routing plus bounded 2-opt for a non-through depth pass."""

    start = start_xy if isinstance(start_xy, Vec2) else Vec2.from_sequence(start_xy)
    order = _nearest_neighbour_order(tuple(trails), start)
    selected, best_cost = _best_variants_for_order(order, start, end_targets)
    # Bounded 2-opt removes the common long diagonal without making planning
    # quadratic over the whole sheet for very large jobs.
    maximum_span = 12
    for _pass in range(2):
        improvement = None
        for first in range(max(0, len(order) - 1)):
            for last in range(first + 1, min(len(order), first + maximum_span)):
                candidate_order = (
                    order[:first]
                    + list(reversed(order[first:last + 1]))
                    + order[last + 1:]
                )
                candidate, candidate_cost = _best_variants_for_order(
                    candidate_order,
                    start,
                    end_targets,
                )
                if candidate_cost + 1.0e-7 < best_cost:
                    key = (
                        candidate_cost,
                        tuple(route.trail_id for route in candidate),
                    )
                    if improvement is None or key < improvement[0]:
                        improvement = (key, candidate_order, candidate, candidate_cost)
        if improvement is None:
            break
        _key, order, selected, best_cost = improvement
    return tuple(selected)


def _fixed_order_routes(trails, start: Vec2):
    """Choose entry/direction without changing a safety-defined order."""

    result = []
    current = start
    for trail in trails:
        variant = min(
            _route_variants(trail),
            key=lambda value: (
                current.distance_to(value.start),
                value.reverse,
                value.start_edge_index,
            ),
        )
        result.append(variant)
        current = variant.end
    return tuple(result)


def _nearest_routes(trails, start: Vec2, end_targets=()):
    """Route one safety-equivalent bucket without crossing its barriers."""

    if not trails:
        return ()
    return optimize_fast_trails(
        tuple(trails),
        start,
        end_targets=tuple(end_targets or ()),
    )


def _point_key(point: Vec2, tolerance: float):
    return _canonical_key(point, point, tolerance)[0]


def _trace_component(
    component: Sequence[PhysicalCutSegment],
    tolerance: float,
):
    """Return one technology-oriented path/cycle for a degree-two component."""

    adjacency = {}
    for index, segment in enumerate(component):
        adjacency.setdefault(_point_key(segment.start, tolerance), []).append(index)
        adjacency.setdefault(_point_key(segment.end, tolerance), []).append(index)
    if any(len(indexes) > 2 for indexes in adjacency.values()):
        raise GlobalCutPlanError(
            "os segmentos restantes de uma peça formam uma ramificação ambígua"
        )
    endpoints = sorted(node for node, indexes in adjacency.items() if len(indexes) == 1)
    if len(endpoints) not in {0, 2}:
        raise GlobalCutPlanError(
            "os segmentos restantes não formam trail aberto nem loop fechado"
        )

    def trace(start_node, first_edge=None):
        used = set()
        current = start_node
        points = []
        segment_ids = []
        edge_tabs = []
        reversed_mechanical = 0
        while len(used) < len(component):
            candidates = sorted(
                index
                for index in adjacency.get(current, ())
                if index not in used
            )
            if first_edge is not None and not used:
                candidates = [first_edge] if first_edge in candidates else []
            if not candidates:
                return None
            index = candidates[0]
            segment = component[index]
            start_key = _point_key(segment.start, tolerance)
            if current == start_key:
                start, end = segment.start, segment.end
                reversed_edge = False
            else:
                start, end = segment.end, segment.start
                reversed_edge = True
            if not points:
                points.append(start)
            points.append(end)
            segment_ids.append(segment.segment_id)
            edge_tabs.append(segment.retains_tab)
            if reversed_edge and segment.kind != PhysicalSegmentKind.SHARED:
                reversed_mechanical += 1
            used.add(index)
            current = _point_key(end, tolerance)
        return (
            reversed_mechanical,
            tuple(points),
            tuple(segment_ids),
            tuple(edge_tabs),
        )

    candidates = []
    if endpoints:
        for endpoint in endpoints:
            candidate = trace(endpoint)
            if candidate is not None:
                candidates.append(candidate)
    else:
        start_node = min(adjacency)
        for first_edge in sorted(adjacency[start_node]):
            candidate = trace(start_node, first_edge)
            if candidate is not None:
                candidates.append(candidate)
    if not candidates:
        raise GlobalCutPlanError("não foi possível reconstruir o trail físico restante")
    selected = min(
        candidates,
        key=lambda value: (
            value[0],
            value[2],
            tuple((point.x, point.y) for point in value[1]),
        ),
    )
    if selected[0]:
        raise GlobalCutPlanError(
            "o trail restante exigiria inverter a direção tecnológica do perfil"
        )
    return selected[1:]


def _remaining_piece_trails(
    segments: Sequence[PhysicalCutSegment],
    owner_id: str,
    done_segment_ids: Iterable[str],
    tolerance: float,
    prefix: str,
    allow_bidirectional: bool = False,
) -> Tuple[GlobalCutTrail, ...]:
    """Build edge-disjoint open/closed trails from one owner's pending atoms."""

    done = set(done_segment_ids)
    pending = tuple(
        segment
        for segment in segments
        if owner_id in segment.owner_ids and segment.segment_id not in done
    )
    if not pending:
        return ()
    by_node = {}
    for index, segment in enumerate(pending):
        by_node.setdefault(_point_key(segment.start, tolerance), []).append(index)
        by_node.setdefault(_point_key(segment.end, tolerance), []).append(index)
    unused = set(range(len(pending)))
    components = []
    while unused:
        seed = min(unused)
        component_indexes = set()
        queue = [seed]
        while queue:
            index = queue.pop()
            if index in component_indexes:
                continue
            component_indexes.add(index)
            segment = pending[index]
            for point in (segment.start, segment.end):
                queue.extend(
                    neighbour
                    for neighbour in by_node[_point_key(point, tolerance)]
                    if neighbour not in component_indexes
                )
        unused.difference_update(component_indexes)
        components.append(tuple(pending[index] for index in sorted(component_indexes)))

    trails = []
    for component in components:
        points, segment_ids, edge_tabs = _trace_component(component, tolerance)
        kinds = {segment.kind for segment in component}
        if kinds == {PhysicalSegmentKind.INTERNAL}:
            kind = PhysicalSegmentKind.INTERNAL
        elif kinds == {PhysicalSegmentKind.SHARED}:
            kind = PhysicalSegmentKind.SHARED
        else:
            # A piece perimeter may contain both exclusive and still-pending
            # shared atoms.  It is one physical trail; the non-through phase
            # intentionally has no phase barrier.
            kind = PhysicalSegmentKind.EXTERNAL
        owners = tuple(
            sorted({owner for segment in component for owner in segment.owner_ids})
        )
        trails.append(
            GlobalCutTrail(
                "%s-%04d" % (prefix, len(trails) + 1),
                points,
                segment_ids,
                edge_tabs,
                kind,
                owners,
                bool(allow_bidirectional)
                or (
                    not points[0].almost_equals(points[-1], tolerance)
                    and kinds == {PhysicalSegmentKind.SHARED}
                ),
            )
        )
    return tuple(
        sorted(
            trails,
            key=lambda trail: (
                _operation_phase(trail) != CutPhase.INTERNAL,
                trail.trail_id,
            ),
        )
    )


def _route_piece_trails(
    trails: Sequence[GlobalCutTrail],
    start: Vec2,
    *,
    geometric_entries_only: bool = False,
):
    """Keep internals first, then route the remaining trails locally."""

    result = []
    current = start
    for internal_phase in (True, False):
        remaining = [
            trail
            for trail in trails
            if (_operation_phase(trail) == CutPhase.INTERNAL) == internal_phase
        ]
        while remaining:
            options = []
            for trail in remaining:
                variant = min(
                    _route_variants(
                        trail,
                        geometric_vertices_only=geometric_entries_only,
                    ),
                    key=lambda value: (
                        current.distance_to(value.start),
                        value.reverse,
                        value.start_edge_index,
                    ),
                )
                options.append(
                    (
                        current.distance_to(variant.start),
                        trail.trail_id,
                        trail,
                        variant,
                    )
                )
            _distance, _trail_id, selected, route = min(options)
            result.append((selected, route))
            current = route.end
            remaining.remove(selected)
    return tuple(result)


def _schedule_per_piece_common_line_depth(
    segments: Sequence[PhysicalCutSegment],
    depth: float,
    depth_index: int,
    start: Vec2,
    tolerance: float,
    *,
    prefer_nearest: bool = False,
):
    """Schedule owners locally while DONE remains physical and depth-scoped."""

    owners = sorted({owner for segment in segments for owner in segment.owner_ids})
    owned_ids = {
        owner: {
            segment.segment_id
            for segment in segments
            if owner in segment.owner_ids
        }
        for owner in owners
    }
    remaining_owners = set(owners)
    done = set()
    current = start
    scheduled = []
    added_trails = []
    coverage = []

    def build_candidate(owner, covered, position):
        prefix = "hybrid-d%03d-%s" % (depth_index + 1, owner)
        trails = _remaining_piece_trails(
            segments,
            owner,
            covered,
            tolerance,
            prefix,
        )
        if not trails:
            return None
        routed = _route_piece_trails(
            trails,
            position,
            geometric_entries_only=True,
        )
        outer_count = sum(
            _operation_phase(trail) != CutPhase.INTERNAL
            for trail in trails
        )
        route_current = position
        rapid_distance = 0.0
        for _trail, route in routed:
            rapid_distance += route_current.distance_to(route.start)
            route_current = route.end
        pending_shared = tuple(
            segment
            for segment in segments
            if segment.segment_id not in covered
            and owner in segment.owner_ids
            and segment.kind == PhysicalSegmentKind.SHARED
        )
        emitted_ids = {
            segment_id
            for trail, route in routed
            for segment_id in trail.oriented(
                reverse=route.reverse,
                start_edge_index=route.start_edge_index,
            ).segment_ids
        }
        return {
            "owner": owner,
            "routed": routed,
            "fragmentation": max(0, outer_count - 1),
            "trail_count": len(trails),
            "entry_distance": position.distance_to(routed[0][1].start),
            "rapid_distance": rapid_distance,
            "shared_count": len(pending_shared),
            "shared_length": sum(segment.length for segment in pending_shared),
            "end": route_current,
            "done": frozenset(set(covered) | emitted_ids),
        }

    def choose_with_lookahead(available, covered, position, horizon=3):
        active = tuple(
            owner
            for owner in sorted(available)
            if not owned_ids[owner].issubset(covered)
        )
        if not active or horizon <= 0:
            return ((), 0.0, 0, 0, 0.0, ()), None
        best = None
        candidates = [
            candidate
            for owner in active
            for candidate in (build_candidate(owner, covered, position),)
            if candidate is not None
        ]
        candidates.sort(
            key=lambda candidate: (
                candidate["fragmentation"],
                round(candidate["rapid_distance"], 9),
                candidate["trail_count"],
                -candidate["shared_count"],
                -round(candidate["shared_length"], 9),
                candidate["owner"],
            )
        )
        # Three-step lookahead over every owner grows cubically at each piece.
        # Eight best immediate candidates retain local alternatives while
        # keeping large cabinet layouts interactive and deterministic.
        for candidate in candidates[:8]:
            owner = candidate["owner"]
            future_available = set(active)
            future_available.remove(owner)
            future_score, _future_candidate = choose_with_lookahead(
                future_available,
                candidate["done"],
                candidate["end"],
                horizon - 1,
            )
            score = (
                (candidate["fragmentation"],) + future_score[0],
                round(candidate["rapid_distance"] + future_score[1], 9),
                candidate["trail_count"] + future_score[2],
                -candidate["shared_count"] + future_score[3],
                round(-candidate["shared_length"] + future_score[4], 9),
                (owner,) + future_score[5],
            )
            if best is None or score < best[0]:
                best = (score, candidate)
        if best is None:
            raise GlobalCutPlanError(
                "o scheduler por peça não encontrou segmentos pendentes"
            )
        return best

    while remaining_owners:
        completed_by_another_owner = {
            owner
            for owner in remaining_owners
            if owned_ids[owner].issubset(done)
        }
        remaining_owners.difference_update(completed_by_another_owner)
        if not remaining_owners:
            break
        if prefer_nearest:
            candidates = [
                candidate
                for owner in sorted(remaining_owners)
                for candidate in (
                    build_candidate(owner, frozenset(done), current),
                )
                if candidate is not None
            ]
            if not candidates:
                raise GlobalCutPlanError(
                    "a passada final não encontrou segmentos pendentes"
                )
            selected = min(
                candidates,
                key=lambda candidate: (
                    round(candidate["entry_distance"], 9),
                    round(candidate["rapid_distance"], 9),
                    candidate["fragmentation"],
                    candidate["trail_count"],
                    -candidate["shared_count"],
                    -round(candidate["shared_length"], 9),
                    candidate["owner"],
                ),
            )
        else:
            _score, selected = choose_with_lookahead(
                remaining_owners,
                frozenset(done),
                current,
                min(3, len(remaining_owners)),
            )
        owner = selected["owner"]
        routed = selected["routed"]
        for trail, route in routed:
            prepared = trail.oriented(
                reverse=route.reverse,
                start_edge_index=route.start_edge_index,
            )
            repeated = set(prepared.segment_ids) & done
            if repeated:
                raise GlobalCutPlanError(
                    "segmento DONE reapareceu no trail da peça %s" % owner
                )
            scheduled.append((trail, route, owner))
            added_trails.append(trail)
            for segment_id in prepared.segment_ids:
                done.add(segment_id)
                coverage.append(
                    SegmentDepthCoverage(
                        segment_id,
                        abs(float(depth)),
                        owner,
                        trail.trail_id,
                    )
                )
            current = route.end
        remaining_owners.remove(owner)

    expected = {segment.segment_id for segment in segments}
    if done != expected:
        raise GlobalCutPlanError(
            "a passada por peça terminou com gaps físicos não planejados"
        )
    return tuple(scheduled), tuple(added_trails), tuple(coverage), current


def _schedule_piece_bidirectional_depths(
    segments: Sequence[PhysicalCutSegment],
    depths: Sequence[float],
    start: Vec2,
    tolerance: float,
    *,
    single_direction: bool = False,
):
    """Cut every piece's pending network at all non-through Z levels.

    The physical DONE set remains independent for every depth, but a selected
    trail is kept as one machining visit: depth 1 goes to the far endpoint,
    depth 2 returns on the same remaining trail, and so on.  Shared atoms
    executed by an earlier owner are absent from every later owner's trail at
    every paired depth. With single_direction, each depth repeats the initial
    traversal; the CAM adapter retracts and returns between open passes.
    """

    depths = tuple(abs(float(depth)) for depth in depths)
    if not depths:
        return (), (), (), start
    owners = sorted({owner for segment in segments for owner in segment.owner_ids})
    owned_ids = {
        owner: {
            segment.segment_id
            for segment in segments
            if owner in segment.owner_ids
        }
        for owner in owners
    }
    done_by_depth = {round(depth, 9): set() for depth in depths}
    remaining_owners = set(owners)
    current = start
    scheduled = []
    added_trails = []
    coverage = []

    def fully_done(owner):
        return all(
            owned_ids[owner].issubset(done_by_depth[round(depth, 9)])
            for depth in depths
        )

    def candidate(owner, position):
        # Because every selected trail is emitted at every intermediate depth,
        # all DONE sets are structurally identical here.  Keep the assertion
        # explicit so a future partial-depth feature cannot silently break it.
        snapshots = [
            frozenset(done_by_depth[round(depth, 9)])
            for depth in depths
        ]
        if len(set(snapshots)) != 1:
            raise GlobalCutPlanError(
                "a cobertura bidirecional intermediária divergiu entre profundidades"
            )
        prefix = "hybrid-bidir-%s" % owner
        trails = _remaining_piece_trails(
            segments,
            owner,
            snapshots[0],
            tolerance,
            prefix,
            allow_bidirectional=not single_direction,
        )
        if not trails:
            return None
        routed = _route_piece_trails(trails, position)
        outer_count = sum(
            _operation_phase(trail) != CutPhase.INTERNAL for trail in trails
        )
        route_position = position
        travel = 0.0
        for trail, route in routed:
            travel += route_position.distance_to(route.start)
            # An even number of passes ends where the trail visit began; an
            # odd number ends at its far endpoint.
            route_position = (
                route.end if single_direction or len(depths) % 2 else route.start
            )
        pending_shared = tuple(
            segment
            for segment in segments
            if segment.segment_id not in snapshots[0]
            and owner in segment.owner_ids
            and segment.kind == PhysicalSegmentKind.SHARED
        )
        return {
            "owner": owner,
            "routed": routed,
            "fragmentation": max(0, outer_count - 1),
            "trail_count": len(trails),
            "entry_distance": position.distance_to(routed[0][1].start),
            "travel": travel,
            "shared_count": len(pending_shared),
            "shared_length": sum(segment.length for segment in pending_shared),
            "end": route_position,
        }

    while remaining_owners:
        completed = {owner for owner in remaining_owners if fully_done(owner)}
        remaining_owners.difference_update(completed)
        if not remaining_owners:
            break
        choices = [
            item
            for owner in sorted(remaining_owners)
            for item in (candidate(owner, current),)
            if item is not None
        ]
        if not choices:
            raise GlobalCutPlanError(
                "o scheduler bidirecional não encontrou segmentos pendentes"
            )
        # Non-through visits are a locality route, not a retention route.
        # Retention, tabs and skeleton stability belong to the through layer.
        # Fragmentation remains a tie-breaker, never a reason to jump over a
        # nearby piece to reach a compact one on the other side of the sheet.
        selected = min(
            choices,
            key=lambda item: (
                round(item["entry_distance"], 9),
                round(item["travel"], 9),
                item["fragmentation"],
                item["trail_count"],
                -item["shared_count"],
                -round(item["shared_length"], 9),
                item["owner"],
            ),
        )
        owner = selected["owner"]
        for trail, initial_route in selected["routed"]:
            added_trails.append(trail)
            for depth_index, depth in enumerate(depths):
                if trail.closed:
                    # A closed profile already ends at its own entry point.
                    # Keep its configured climb/conventional direction on
                    # every depth; reversing it would buy no travel saving.
                    reverse = bool(initial_route.reverse)
                    edge_count = len(trail.segment_ids)
                    start_edge_index = initial_route.start_edge_index % edge_count
                else:
                    # An open common-line remainder ends at the opposite node.
                    # Alternate only these paths so the next depth starts
                    # without retracting and traversing the sheet again.
                    reverse = bool(initial_route.reverse) ^ bool(
                        depth_index % 2 and not single_direction
                    )
                    start_edge_index = 0
                prepared = trail.oriented(
                    reverse=reverse,
                    start_edge_index=start_edge_index,
                )
                done = done_by_depth[round(depth, 9)]
                repeated = set(prepared.segment_ids) & done
                if repeated:
                    raise GlobalCutPlanError(
                        "segmento DONE reapareceu na visita bidirecional de %s"
                        % owner
                    )
                route = TrailRoute(
                    trail.trail_id,
                    reverse,
                    start_edge_index,
                    prepared.points[0],
                    prepared.points[-1],
                )
                scheduled.append((trail, depth, route, owner))
                for segment_id in prepared.segment_ids:
                    done.add(segment_id)
                    coverage.append(
                        SegmentDepthCoverage(
                            segment_id,
                            depth,
                            owner,
                            trail.trail_id,
                        )
                    )
                current = route.end
        remaining_owners.remove(owner)

    expected = {segment.segment_id for segment in segments}
    for depth in depths:
        if done_by_depth[round(depth, 9)] != expected:
            raise GlobalCutPlanError(
                "a visita bidirecional terminou com gaps físicos em Z -%.4f"
                % depth
            )
    return tuple(scheduled), tuple(added_trails), tuple(coverage), current


def _route_metrics(
    operations: Sequence[CutOperation],
    trails: Sequence[GlobalCutTrail],
    segments: Sequence[PhysicalCutSegment],
    piece_metrics: Sequence[PieceMetrics],
    start_xy: Vec2 | Sequence[float],
) -> Tuple[CutRouteMetrics, ...]:
    trail_by_id = {trail.trail_id: trail for trail in trails}
    segment_by_id = {segment.segment_id: segment for segment in segments}
    start = start_xy if isinstance(start_xy, Vec2) else Vec2.from_sequence(start_xy)
    independent_perimeters = sum(
        segment.length * len(segment.owner_ids)
        for segment in segments
    )
    all_points = [
        point
        for segment in segments
        for point in (segment.start, segment.end)
    ]
    sheet_diagonal = math.hypot(
        max((point.x for point in all_points), default=0.0)
        - min((point.x for point in all_points), default=0.0),
        max((point.y for point in all_points), default=0.0)
        - min((point.y for point in all_points), default=0.0),
    )
    long_threshold = max(1.0, sheet_diagonal * 0.75)
    result = []
    current = start
    previous_depth = None
    for depth in sorted({operation.depth for operation in operations}):
        selected = [operation for operation in operations if operation.depth == depth]
        if previous_depth is None or round(depth, 9) != round(previous_depth, 9):
            # Position continuity deliberately crosses depth boundaries.
            previous_depth = depth
        rapid_distance = 0.0
        cut_distance = 0.0
        long_rapid_count = 0
        owners = []
        owner_outer_trails = {}
        segment_hits = {}
        shared_reused = set()
        for operation in selected:
            trail = trail_by_id[operation.trail_id].oriented(
                reverse=operation.reverse_trail,
                start_edge_index=operation.start_edge_index,
            )
            rapid = current.distance_to(trail.points[0])
            rapid_distance += rapid
            if rapid > long_threshold:
                long_rapid_count += 1
            cut_distance += sum(
                segment_by_id[segment_id].length
                for segment_id in operation.segment_ids
            )
            for segment_id in operation.segment_ids:
                segment_hits[segment_id] = segment_hits.get(segment_id, 0) + 1
                segment = segment_by_id[segment_id]
                if segment.kind == PhysicalSegmentKind.SHARED:
                    if any(
                        owner != operation.executing_owner_id
                        for owner in segment.owner_ids
                    ):
                        shared_reused.add(segment_id)
            owner = operation.executing_owner_id or operation.owner_ids[0]
            owners.append(owner)
            if operation.phase != CutPhase.INTERNAL:
                owner_outer_trails[owner] = owner_outer_trails.get(owner, 0) + 1
            current = trail.points[-1]
        piece_switch_count = sum(
            first != second for first, second in zip(owners, owners[1:])
        )
        result.append(
            CutRouteMetrics(
                depth,
                selected[0].routing_mode if selected else "defined",
                cut_distance,
                rapid_distance,
                len(selected),
                len(selected),
                piece_switch_count,
                len(selected),
                sum(count > 1 for count in owner_outer_trails.values()),
                sum(count - 1 for count in segment_hits.values() if count > 1),
                independent_perimeters,
                max(0.0, independent_perimeters - cut_distance),
                len(shared_reused),
                long_rapid_count,
            )
        )
    return tuple(result)


def _schedule_operations(
    trails,
    depths,
    strategy,
    piece_metrics=(),
    start_xy=(0.0, 0.0),
    stability_depth=None,
    retention_graph=None,
    segments=(),
    tolerance=0.02,
    hybrid_intermediate_mode="fast",
):
    by_phase = {
        phase: [trail for trail in trails if _operation_phase(trail) == phase]
        for phase in (CutPhase.INTERNAL, CutPhase.SHARED, CutPhase.EXTERNAL)
    }
    sequence = []
    additional_trails = []
    coverage = []
    if strategy in {
        CutDepthStrategy.PIECE_BIDIRECTIONAL,
        CutDepthStrategy.PIECE_UNIDIRECTIONAL,
    }:
        single_direction = strategy == CutDepthStrategy.PIECE_UNIDIRECTIONAL
        current = (
            start_xy if isinstance(start_xy, Vec2) else Vec2.from_sequence(start_xy)
        )
        scheduled, created, depth_coverage, current = (
            _schedule_piece_bidirectional_depths(
                segments,
                depths,
                current,
                tolerance,
                single_direction=single_direction,
            )
        )
        additional_trails.extend(created)
        coverage.extend(depth_coverage)
        sequence.extend(
            (
                trail,
                depth,
                _operation_phase(trail),
                route,
                "piece_unidirectional" if single_direction else "piece_bidirectional",
                owner,
            )
            for trail, depth, route, owner in scheduled
        )
    elif strategy in {
        CutDepthStrategy.HYBRID_STABILITY,
        CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL,
    }:
        risk_by_owner = {
            metric.owner_id: (
                metric.aspect_ratio,
                metric.maximum_dimension,
                metric.perimeter,
            )
            for metric in piece_metrics
        }
        critical_owners = set(
            retention_graph.critical_piece_ids()
            if retention_graph is not None
            else ()
        )
        current = (
            start_xy if isinstance(start_xy, Vec2) else Vec2.from_sequence(start_xy)
        )
        stability_threshold = (
            depths[-1]
            if stability_depth is None
            else min(depths[-1], abs(float(stability_depth)))
        )
        fast_depths = tuple(
            depth for depth in depths if depth < stability_threshold - 1.0e-9
        )
        stability_depths = tuple(
            depth for depth in depths if depth >= stability_threshold - 1.0e-9
        )
        if not stability_depths:
            fast_depths = tuple(depths[:-1])
            stability_depths = (depths[-1],)
        if strategy == CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL:
            # Contrato do modo otimizado: conclui todas as passadas anteriores
            # à última em visitas ida/volta e só então percorre uma última
            # camada separada sobre a chapa inteira. A profundidade nominal da
            # chapa não divide essa última volta em grupos difíceis de seguir.
            fast_depths = tuple(depths[:-1])
            stability_depths = (depths[-1],)
            scheduled, created, depth_coverage, current = (
                _schedule_piece_bidirectional_depths(
                    segments,
                    fast_depths,
                    current,
                    tolerance,
                )
            )
            additional_trails.extend(created)
            coverage.extend(depth_coverage)
            sequence.extend(
                (
                    trail,
                    depth,
                    _operation_phase(trail),
                    route,
                    "piece_bidirectional",
                    owner,
                )
                for trail, depth, route, owner in scheduled
            )

            final_scheduled, final_created, final_coverage, current = (
                _schedule_per_piece_common_line_depth(
                    segments,
                    depths[-1],
                    len(depths) - 1,
                    current,
                    tolerance,
                    prefer_nearest=True,
                )
            )
            additional_trails.extend(final_created)
            coverage.extend(final_coverage)
            sequence.extend(
                (
                    trail,
                    depths[-1],
                    _operation_phase(trail),
                    route,
                    "final_sheet_pass",
                    owner,
                )
                for trail, route, owner in final_scheduled
            )
        for depth_index, depth in enumerate(
            ()
            if strategy == CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL
            else fast_depths
        ):
            if hybrid_intermediate_mode == "fast":
                routed = optimize_fast_trails(trails, current)
                trail_by_id = {trail.trail_id: trail for trail in trails}
                for route in routed:
                    trail = trail_by_id[route.trail_id]
                    sequence.append(
                        (
                            trail,
                            depth,
                            _operation_phase(trail),
                            route,
                            "fast",
                            trail.owner_ids[0],
                        )
                    )
                    if segments:
                        for segment_id in trail.segment_ids:
                            segment = next(
                                segment
                                for segment in segments
                                if segment.segment_id == segment_id
                            )
                            coverage.append(
                                SegmentDepthCoverage(
                                    segment_id,
                                    depth,
                                    segment.owner_ids[0],
                                    trail.trail_id,
                                )
                            )
                if routed:
                    current = routed[-1].end
            elif hybrid_intermediate_mode == "per_piece_common_line":
                scheduled, created, depth_coverage, current = (
                    _schedule_per_piece_common_line_depth(
                        segments,
                        depth,
                        depth_index,
                        current,
                        tolerance,
                    )
                )
                additional_trails.extend(created)
                coverage.extend(depth_coverage)
                sequence.extend(
                    (
                        trail,
                        depth,
                        _operation_phase(trail),
                        route,
                        "per_piece_common_line",
                        owner,
                    )
                    for trail, route, owner in scheduled
                )
            else:
                raise GlobalCutPlanError(
                    "modo intermediário híbrido inválido: %s"
                    % hybrid_intermediate_mode
                )

        final_route_groups = []
        for phase in (CutPhase.INTERNAL, CutPhase.SHARED, CutPhase.EXTERNAL):
            phase_trails = list(by_phase[phase])
            if phase != CutPhase.EXTERNAL:
                if phase_trails:
                    final_route_groups.append(tuple(phase_trails))
            else:
                # Low-risk pieces finish first; long/narrow pieces keep all of
                # their bridges and surrounding skeleton longer.
                def safety_key(trail):
                    return (
                        any(owner in critical_owners for owner in trail.owner_ids),
                        max(
                            (
                                risk_by_owner.get(owner, (0.0, 0.0, 0.0))
                                for owner in trail.owner_ids
                            ),
                            default=(0.0, 0.0, 0.0),
                        ),
                    )

                phase_trails.sort(key=lambda trail: (safety_key(trail), trail.trail_id))
                for safety, grouped in itertools.groupby(phase_trails, key=safety_key):
                    del safety
                    final_route_groups.append(tuple(grouped))
        trail_by_id = {trail.trail_id: trail for trail in trails}
        for depth in (
            ()
            if strategy == CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL
            else stability_depths
        ):
            final_routes = []
            for group_index, safe_group in enumerate(final_route_groups):
                next_group = (
                    final_route_groups[group_index + 1]
                    if group_index + 1 < len(final_route_groups)
                    else ()
                )
                next_starts = tuple(
                    variant.start
                    for trail in next_group
                    for variant in _route_variants(trail)
                )
                group_routes = _nearest_routes(
                    safe_group,
                    current,
                    end_targets=next_starts,
                )
                final_routes.extend(group_routes)
                if group_routes:
                    current = group_routes[-1].end
            for route in final_routes:
                trail = trail_by_id[route.trail_id]
                sequence.append(
                    (
                        trail,
                        depth,
                        _operation_phase(trail),
                        route,
                        "stability",
                        trail.owner_ids[0],
                    )
                )
                if segments:
                    for segment_id in trail.segment_ids:
                        segment = next(
                            segment
                            for segment in segments
                            if segment.segment_id == segment_id
                        )
                        coverage.append(
                            SegmentDepthCoverage(
                                segment_id,
                                depth,
                                segment.owner_ids[0],
                                trail.trail_id,
                            )
                        )
    elif strategy == CutDepthStrategy.GLOBAL_BY_DEPTH:
        for depth in depths:
            for phase in (CutPhase.INTERNAL, CutPhase.SHARED, CutPhase.EXTERNAL):
                sequence.extend(
                    (trail, depth, phase, None, "defined")
                    for trail in by_phase[phase]
                )
    else:
        owners = sorted({owner for trail in trails for owner in trail.owner_ids})
        scheduled = set()
        current = (
            start_xy if isinstance(start_xy, Vec2) else Vec2.from_sequence(start_xy)
        )
        for owner in owners:
            for depth in depths:
                for phase in (CutPhase.INTERNAL, CutPhase.SHARED, CutPhase.EXTERNAL):
                    pending = [
                        trail
                        for trail in trails
                        if owner in trail.owner_ids
                        and _operation_phase(trail) == phase
                        and (trail.trail_id, depth) not in scheduled
                    ]
                    for trail, route in _route_piece_trails(
                        pending,
                        current,
                        geometric_entries_only=True,
                    ):
                        key = (trail.trail_id, depth)
                        scheduled.add(key)
                        sequence.append(
                            (
                                trail,
                                depth,
                                phase,
                                route,
                                "defined",
                                owner,
                            )
                        )
                        current = route.end

    if strategy == CutDepthStrategy.GLOBAL_BY_DEPTH:
        sequence = [
            (*item, item[0].owner_ids[0])
            for item in sequence
        ]

    operations = []
    last_for_trail = {}
    phase_at_depth = {}
    previous_hybrid_operation = None
    for trail, depth, phase, route, routing_mode, executing_owner_id in sequence:
        dependencies = []
        if trail.trail_id in last_for_trail:
            dependencies.append(last_for_trail[trail.trail_id])
        is_final = abs(depth - depths[-1]) <= 1.0e-9
        if strategy not in {
            CutDepthStrategy.PIECE_BIDIRECTIONAL,
            CutDepthStrategy.PIECE_UNIDIRECTIONAL,
            CutDepthStrategy.HYBRID_STABILITY,
            CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL,
        } or routing_mode == "stability":
            for previous_phase in (CutPhase.INTERNAL, CutPhase.SHARED):
                if previous_phase == phase:
                    break
                dependencies.extend(
                    phase_at_depth.get((round(depth, 9), previous_phase), ())
                )
        if strategy in {
            CutDepthStrategy.PIECE_BIDIRECTIONAL,
            CutDepthStrategy.PIECE_UNIDIRECTIONAL,
            CutDepthStrategy.HYBRID_STABILITY,
            CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL,
        }:
            # The optimizer chooses the route first; this explicit chain then
            # prevents any later optimizer from crossing a depth boundary or
            # advancing a stability-critical final operation.
            if previous_hybrid_operation is not None:
                dependencies.append(previous_hybrid_operation)
        prepared = (
            trail.oriented(
                reverse=route.reverse,
                start_edge_index=route.start_edge_index,
            )
            if route is not None
            else trail
        )
        operation_id = "cut-%05d" % (len(operations) + 1)
        operation = CutOperation(
            operation_id,
            trail.trail_id,
            prepared.segment_ids,
            trail.owner_ids,
            depth,
            phase,
            tuple(dict.fromkeys(dependencies)),
            is_final,
            bool(route.reverse) if route is not None else False,
            int(route.start_edge_index) if route is not None else 0,
            routing_mode,
            executing_owner_id,
        )
        operations.append(operation)
        last_for_trail[trail.trail_id] = operation_id
        phase_at_depth.setdefault((round(depth, 9), phase), []).append(operation_id)
        if strategy in {
            CutDepthStrategy.PIECE_BIDIRECTIONAL,
            CutDepthStrategy.PIECE_UNIDIRECTIONAL,
            CutDepthStrategy.HYBRID_STABILITY,
            CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL,
        }:
            previous_hybrid_operation = operation_id
    if not coverage:
        segment_by_id = {
            segment.segment_id: segment for segment in segments
        }
        coverage = [
            SegmentDepthCoverage(
                segment_id,
                depth,
                (
                    segment_by_id[segment_id].owner_ids[0]
                    if segment_id in segment_by_id
                    else executing_owner_id
                ),
                trail.trail_id,
            )
            for trail, depth, _phase, _route, _mode, executing_owner_id in sequence
            for segment_id in trail.segment_ids
        ]
    result = tuple(operations), tuple(additional_trails), tuple(coverage)
    return result if segments else result[0]


def _tool_supports_vertical_plunge(tool_type: str) -> bool:
    return str(tool_type or "end_mill") in {
        "end_mill",
        "compression",
    }


def _release_geometry(tab, tool_diameter, tolerance, piece_centre=None):
    width = tab.width
    diameter = max(0.0, float(tool_diameter))
    sweep = max(0.0, width - diameter)
    if sweep <= tolerance:
        centre = tab.centre
        return centre, centre, 0.0
    start = tab.start
    end = tab.end
    if piece_centre is not None:
        # Finish the sweep at the endpoint farther from the released piece's
        # centre.  The final lateral effort is therefore directed away from
        # the loose part, and the cutter retracts at that endpoint.
        if start.distance_to(piece_centre) > end.distance_to(piece_centre):
            start, end = end, start
    direction = (end - start) * (1.0 / width)
    plunge = start + direction * (diameter * 0.5)
    end = plunge + direction * sweep
    return plunge, end, sweep


def _plan_release(
    tabs,
    graph,
    operations,
    trails,
    depths,
    tool_diameter,
    tool_type,
    tolerance,
    piece_metrics=(),
    start_xy=(0.0, 0.0),
):
    if not tabs:
        return ()
    if not _tool_supports_vertical_plunge(tool_type):
        raise GlobalCutPlanError(
            "a ferramenta %s não declara capacidade segura de mergulho vertical"
            % tool_type
        )
    remaining = set(graph.piece_ids)
    removed_tabs = set()
    release = []
    last_main = tuple(operation.operation_id for operation in operations)
    previous_release = None
    risk_by_owner = {
        metric.owner_id: (
            metric.aspect_ratio,
            metric.maximum_dimension,
            metric.perimeter,
        )
        for metric in piece_metrics
    }
    metrics_by_owner = {metric.owner_id: metric for metric in piece_metrics}
    current = (
        start_xy
        if isinstance(start_xy, Vec2)
        else Vec2.from_sequence(start_xy)
    )
    trail_by_id = {trail.trail_id: trail for trail in trails}
    if operations:
        last_operation = operations[-1]
        last_trail = trail_by_id.get(last_operation.trail_id)
        if last_trail is not None:
            prepared = last_trail.oriented(
                reverse=last_operation.reverse_trail,
                start_edge_index=last_operation.start_edge_index,
            )
            current = prepared.points[-1]

    def release_route(owner, position):
        piece_metric = metrics_by_owner.get(owner)
        remaining_tabs = [
            tab
            for tab in tabs
            if tab.releasable
            and owner in tab.owner_ids
            and tab.tab_id not in removed_tabs
        ]
        ordered = []
        route_position = position
        travel = 0.0
        while remaining_tabs:
            choices = []
            for tab in remaining_tabs:
                plunge, sweep_end, sweep = _release_geometry(
                    tab,
                    tool_diameter,
                    tolerance,
                    piece_metric.centre if piece_metric is not None else None,
                )
                choices.append(
                    (
                        route_position.distance_to(plunge),
                        tab.kind == TabRetentionKind.STOCK,
                        tab.tab_id,
                        tab,
                        plunge,
                        sweep_end,
                        sweep,
                    )
                )
            distance_to_tab, _stock_last, _tab_id, tab, plunge, sweep_end, sweep = min(
                choices,
                key=lambda item: (round(item[0], 9), item[1], item[2]),
            )
            travel += distance_to_tab
            ordered.append((tab, plunge, sweep_end, sweep))
            route_position = sweep_end
            remaining_tabs.remove(tab)
        return tuple(ordered), travel, route_position

    while remaining:
        candidates = [
            owner
            for owner in sorted(remaining)
            if graph.removal_preserves_other_pieces(owner, remaining, removed_tabs)
        ]
        if not candidates:
            raise GlobalCutPlanError(
                "não existe ordem de TabRelease que preserve a retenção das peças restantes"
            )
        routed_candidates = []
        for candidate_owner in candidates:
            route, travel, route_end = release_route(candidate_owner, current)
            if route:
                routed_candidates.append(
                    (
                        travel,
                        current.distance_to(route[0][1]),
                        risk_by_owner.get(candidate_owner, (0.0, 0.0, 0.0)),
                        candidate_owner,
                        route,
                        route_end,
                    )
                )
        if not routed_candidates:
            raise GlobalCutPlanError(
                "nenhuma peça liberável possui tab restante"
            )
        _travel, _entry, _risk, owner, incident, route_end = min(
            routed_candidates,
            key=lambda item: (
                round(item[0], 9),
                round(item[1], 9),
                item[2],
                item[3],
            ),
        )
        for index, (tab, plunge, sweep_end, sweep) in enumerate(incident):
            dependencies = (
                (previous_release,)
                if previous_release is not None
                else last_main
            )
            operation_id = "tab-release-%04d" % (len(release) + 1)
            release.append(
                TabReleaseOperation(
                    operation_id,
                    tab.tab_id,
                    owner,
                    plunge,
                    sweep_end,
                    sweep,
                    depths[-1],
                    dependencies,
                    index == len(incident) - 1,
                )
            )
            previous_release = operation_id
            removed_tabs.add(tab.tab_id)
        current = route_end
        remaining.remove(owner)
    return tuple(release)


def build_global_cut_plan(
    outer_contours: Sequence[CommonLineContour],
    *,
    internal_contours: Sequence[OwnedContour] = (),
    stock_boundary: Sequence[Vec2 | Sequence[float]] = (),
    stock_boundaries: Sequence[
        Sequence[Vec2 | Sequence[float]]
    ] = (),
    depths: Sequence[float],
    strategy: CutDepthStrategy | str = CutDepthStrategy.PER_PIECE,
    release_mode: TabReleaseMode | str = TabReleaseMode.KEEP_TABS,
    tabs_enabled: bool = False,
    tab_count: int = 0,
    tab_width: float = 0.0,
    tab_thickness: float = 0.0,
    manual_tab_positions: Iterable[Vec2 | Sequence[float] | dict] = (),
    best_fixation: bool = False,
    tool_diameter: float = 0.0,
    tool_type: str = "end_mill",
    tolerance: float = 0.02,
    retention_rules: RetentionRules | None = None,
    resolve_shared_edges: bool = True,
    start_xy: Vec2 | Sequence[float] = Vec2(0.0, 0.0),
    through_depth: float | None = None,
    hybrid_intermediate_mode: str = "per_piece_common_line",
    loose_waste_fixation: LooseWasteFixationMode | str = LooseWasteFixationMode.DISABLED,
    material_thickness: float | None = None,
    screw_pilot_diameter: float = 0.0,
    screw_pilot_depth: float = 0.0,
    screw_head_diameter: float = 10.0,
    screw_safety_margin: float = 3.0,
    screw_head_height: float = 3.0,
) -> GlobalCutPlan:
    """Build and validate a sheet-wide plan from cutter centre-lines."""

    strategy = _normalise_enum(strategy, CutDepthStrategy, "estratégia")
    release_mode = _normalise_enum(release_mode, TabReleaseMode, "Tab release")
    waste_fixation = _normalise_enum(
        loose_waste_fixation,
        LooseWasteFixationMode,
        "fixação de restos soltos",
    )
    clean_depths = tuple(sorted({abs(float(value)) for value in depths if abs(float(value)) > 1.0e-9}))
    if not clean_depths:
        raise GlobalCutPlanError("informe ao menos uma profundidade de corte")
    tolerance = abs(float(tolerance))
    if tolerance <= 0.0 or not math.isfinite(tolerance):
        raise GlobalCutPlanError("a tolerância geométrica deve ser positiva")
    rules = (
        retention_rules or RetentionRules(geometric_tolerance=tolerance)
    ).for_physical_tab_height(tab_thickness, material_thickness)

    outer_contours = tuple(outer_contours)
    clean_stock_boundaries = tuple(
        tuple(
            point if isinstance(point, Vec2) else Vec2.from_sequence(point)
            for point in (boundary or ())
        )
        for boundary in (stock_boundaries or ())
        if boundary
    )
    if not clean_stock_boundaries and stock_boundary:
        clean_stock_boundaries = (
            tuple(
                point if isinstance(point, Vec2) else Vec2.from_sequence(point)
                for point in stock_boundary
            ),
        )
    metrics = _piece_metrics(outer_contours)
    requested = max(0, int(tab_count))
    required_by_owner = {
        metric.owner_id: rules.required_tab_count(
            metric,
            requested,
            tool_diameter,
            tab_width,
        )
        for metric in metrics
    }
    manual_tab_positions = tuple(manual_tab_positions or ())
    isolated_manual_positions = _manual_tab_positions_by_contour(
        outer_contours,
        manual_tab_positions,
    )
    if release_mode == TabReleaseMode.AUTOMATIC_RELEASE and not tabs_enabled:
        raise GlobalCutPlanError(
            "automatic_release exige tabs ativas no corte principal"
        )

    try:
        if resolve_shared_edges:
            outer_plan = plan_common_line_cut(
                outer_contours,
                tolerance=tolerance,
                raise_on_error=True,
            )
            isolated_plans = (outer_plan,)
        else:
            isolated_plans = tuple(
                plan_common_line_cut(
                    (contour,),
                    tolerance=tolerance,
                    raise_on_error=True,
                )
                for contour in outer_contours
            )
            outer_plan = None
    except CommonLinePlanningError as error:
        raise GlobalCutPlanError(str(error))

    if tabs_enabled:
        collected_paths = []
        for plan_index, current_plan in enumerate(isolated_plans):
            current_manual_positions = (
                manual_tab_positions
                if resolve_shared_edges
                else isolated_manual_positions[plan_index]
            )
            current_paths = build_common_line_cut_paths(
                current_plan,
                # Manual placement is an exclusive mode. Never carry the
                # automatic minimum into the geometric splitter: a click must
                # create exactly its physical StockTab/SharedTab, while safety
                # remains the operator's explicit choice in this mode.
                tab_count=0,
                tab_length=tab_width,
                manual_tab_positions=current_manual_positions,
                best_fixation=bool(best_fixation),
                retention_rules=rules,
            )
            collected_paths.extend(current_paths)
        outer_paths = (
            tuple(collected_paths)
            if manual_tab_positions
            else _balanced_global_tab_paths(
                tuple(collected_paths),
                required_by_owner,
                float(tab_width),
                {contour.contour_id: contour.points for contour in outer_contours},
                rules,
                float(tool_diameter),
            )
        )
    else:
        outer_paths = tuple(
            path
            for current_plan in isolated_plans
            for path in build_common_line_cut_paths(current_plan)
        )

    segments = list(_paths_to_segments(outer_paths, PhysicalSegmentKind.EXTERNAL, tolerance))
    waste_regions = []
    screw_anchors = []
    outer_by_owner = {contour.contour_id: contour.points for contour in outer_contours}
    internal_by_owner = {}
    for contour in internal_contours:
        owner_ring = outer_by_owner.get(contour.owner_id)
        if owner_ring is None:
            raise GlobalCutPlanError(
                "contorno interno %s referencia peça inexistente %s"
                % (contour.contour_id, contour.owner_id)
            )
        if any(
            point_in_polygon(point, owner_ring, tolerance) != PointLocation.INSIDE
            for point in contour.points
        ):
            raise GlobalCutPlanError(
                "contorno interno %s não está estritamente dentro da peça %s"
                % (contour.contour_id, contour.owner_id)
            )
        for previous in internal_by_owner.get(contour.owner_id, ()):
            if _rings_touch_or_cross(previous.points, contour.points, tolerance):
                raise GlobalCutPlanError(
                    "contornos internos %s e %s se tocam, cruzam ou sobrepõem aresta"
                    % (previous.contour_id, contour.contour_id)
                )
        try:
            internal_plan = plan_common_line_cut(
                (CommonLineContour(contour.contour_id, contour.points),),
                tolerance=tolerance,
                raise_on_error=True,
            )
        except CommonLinePlanningError as error:
            raise GlobalCutPlanError(str(error))
        paths = tuple(
            CommonLineCutPath(
                (segment.start, segment.end),
                (contour.owner_id,),
                False,
                False,
            )
            for segment in internal_plan.perimeter_segments
        )
        waste_id = "waste-%s" % contour.contour_id
        fixation = "disabled"
        fallback_reason = ""
        use_waste_tabs = waste_fixation == LooseWasteFixationMode.TABS
        if waste_fixation == LooseWasteFixationMode.SCREWS:
            if material_thickness is None or float(material_thickness) <= 0.0:
                raise GlobalCutPlanError(
                    "fixação de restos por parafuso exige espessura do material"
                )
            if not _tool_supports_vertical_plunge(tool_type):
                raise GlobalCutPlanError(
                    "a ferramenta atual não admite os furos piloto dos parafusos"
                )
            required_clearance = (
                max(0.0, float(screw_head_diameter)) * 0.5
                + max(0.0, float(tool_diameter)) * 0.5
                + max(0.0, float(screw_safety_margin))
            )
            screw_point, _clearance = _safe_screw_point(
                contour.points,
                required_clearance,
                tuple(spec.points for spec in outer_contours)
                + tuple(spec.points for spec in internal_contours),
            )
            if screw_point is not None:
                requested_pilot_diameter = abs(float(screw_pilot_diameter))
                if (
                    requested_pilot_diameter > 1.0e-9
                    and requested_pilot_diameter
                    < abs(float(tool_diameter)) - tolerance
                ):
                    raise GlobalCutPlanError(
                        "o diâmetro piloto configurado é menor que a fresa atual"
                    )
                actual_hole_diameter = max(
                    abs(float(tool_diameter)),
                    requested_pilot_diameter,
                )
                pilot_depth = (
                    abs(float(screw_pilot_depth))
                    if abs(float(screw_pilot_depth)) > 1.0e-9
                    else abs(float(material_thickness)) + 2.0
                )
                screw_anchors.append(
                    ScrewAnchor(
                        "screw-%04d" % (len(screw_anchors) + 1),
                        waste_id,
                        screw_point,
                        actual_hole_diameter,
                        pilot_depth,
                        max(0.0, float(screw_head_diameter)) * 0.5
                        + max(0.0, float(screw_safety_margin)),
                        max(0.0, float(screw_head_height)),
                    )
                )
                fixation = "screw"
            else:
                use_waste_tabs = True
                fallback_reason = (
                    "sem posição com folga para cabeça, fresa e margem"
                )
        if use_waste_tabs:
            usable_lengths = sorted(
                (
                    path.points[0].distance_to(path.points[1])
                    for path in paths
                    if path.points[0].distance_to(path.points[1]) > tolerance
                ),
                reverse=True,
            )
            if len(usable_lengths) < rules.minimum_loose_waste_tabs:
                raise GlobalCutPlanError(
                    "o resto %s não possui duas bordas vetoriais distintas para tabs"
                    % waste_id
                )
            adaptive_tab_width = min(
                float(tab_width),
                max(
                    tolerance,
                    usable_lengths[rules.minimum_loose_waste_tabs - 1]
                    - 2.0 * tolerance,
                ),
            )
            waste_paths = build_common_line_cut_paths(
                internal_plan,
                tab_count=rules.minimum_loose_waste_tabs,
                tab_length=adaptive_tab_width,
                best_fixation=False,
                retention_rules=rules,
            )
            paths = tuple(
                CommonLineCutPath(
                    path.points,
                    (contour.owner_id,),
                    False,
                    path.tab,
                )
                for path in waste_paths
            )
            if sum(path.tab for path in paths) < rules.minimum_loose_waste_tabs:
                raise GlobalCutPlanError(
                    "o resto %s não comporta duas retenções vetoriais distintas"
                    % waste_id
                )
            fixation = "tabs"
        segments.extend(
            _paths_to_segments(
                paths,
                PhysicalSegmentKind.INTERNAL,
                tolerance,
                waste_id=waste_id if use_waste_tabs else None,
                tab_height_override=(
                    abs(float(material_thickness))
                    if use_waste_tabs and material_thickness is not None
                    else None
                ),
            )
        )
        waste_regions.append(
            WasteRegion(
                waste_id,
                contour.owner_id,
                tuple(contour.points),
                _polygon_area(contour.points),
                fixation,
                fallback_reason,
            )
        )
        internal_by_owner.setdefault(contour.owner_id, []).append(contour)

    if (
        clean_stock_boundaries
        and waste_fixation != LooseWasteFixationMode.DISABLED
    ):
        for current_stock_boundary in clean_stock_boundaries:
            components = _enclosed_waste_components(
                current_stock_boundary,
                outer_contours,
                rules,
                tolerance,
            )
            segments, detected_regions, detected_anchors = (
                _fix_enclosed_waste_components(
                    tuple(segments),
                    components,
                    outer_contours,
                    internal_contours,
                    waste_fixation,
                    rules,
                    tolerance,
                    tab_width,
                    material_thickness,
                    tool_diameter,
                    tool_type,
                    screw_pilot_diameter,
                    screw_pilot_depth,
                    screw_head_diameter,
                    screw_safety_margin,
                    screw_head_height,
                    len(waste_regions) + 1,
                    len(screw_anchors) + 1,
                )
            )
            waste_regions.extend(detected_regions)
            screw_anchors.extend(detected_anchors)
    if not segments:
        raise GlobalCutPlanError("nenhum segmento físico foi produzido")

    segments = list(
        _split_trails_for_safe_entry(
            tuple(segments),
            tolerance,
            tool_diameter,
        )
    )
    trails = _build_trails(tuple(segments), tolerance)
    tabs = _tabs_from_segments(tuple(segments), tab_thickness)
    reports = (
        _stability_reports(
            metrics,
            tabs,
            rules,
            requested,
            tool_diameter,
            tab_width,
            outer_contours,
        )
        if tabs_enabled and not manual_tab_positions
        else ()
    )
    operations, intermediate_trails, coverage = _schedule_operations(
        trails,
        clean_depths,
        strategy,
        metrics,
        start_xy,
        through_depth,
        RetentionGraph(tabs),
        tuple(segments),
        tolerance,
        hybrid_intermediate_mode,
    )
    all_trails = trails + intermediate_trails
    route_metrics = _route_metrics(
        operations,
        all_trails,
        tuple(segments),
        metrics,
        start_xy,
    )
    graph = RetentionGraph(tabs)
    releases = (
        _plan_release(
            tabs,
            graph,
            operations,
            all_trails,
            clean_depths,
            tool_diameter,
            tool_type,
            tolerance,
            metrics,
            start_xy,
        )
        if release_mode == TabReleaseMode.AUTOMATIC_RELEASE
        else ()
    )
    plan = GlobalCutPlan(
        strategy=strategy,
        release_mode=release_mode,
        depths=clean_depths,
        segments=tuple(segments),
        trails=all_trails,
        tabs=tabs,
        operations=operations,
        tab_release_operations=releases,
        piece_metrics=metrics,
        stability_reports=reports,
        waste_regions=tuple(waste_regions),
        screw_anchors=tuple(screw_anchors),
        segment_depth_coverage=coverage,
        route_metrics=route_metrics,
        hybrid_intermediate_mode=(
            "piece_unidirectional"
            if strategy == CutDepthStrategy.PIECE_UNIDIRECTIONAL
            else "piece_bidirectional"
            if strategy in {
                CutDepthStrategy.PIECE_BIDIRECTIONAL,
                CutDepthStrategy.HYBRID_PIECE_BIDIRECTIONAL,
            }
            else hybrid_intermediate_mode
        ),
    )
    return plan.validate()


__all__ = [
    "CutDepthStrategy",
    "CutOperation",
    "CutPhase",
    "CutRouteMetrics",
    "GlobalCutPlan",
    "GlobalCutPlanError",
    "GlobalCutTrail",
    "LooseWasteFixationMode",
    "OwnedContour",
    "PhysicalCutSegment",
    "PhysicalSegmentKind",
    "PieceMetrics",
    "RetentionGraph",
    "RetentionRules",
    "RetentionTab",
    "ScrewAnchor",
    "SegmentDepthCoverage",
    "StabilityReport",
    "TabReleaseMode",
    "TabReleaseOperation",
    "TabRetentionKind",
    "TrailRoute",
    "WasteRegion",
    "build_global_cut_plan",
    "optimize_fast_trails",
]
